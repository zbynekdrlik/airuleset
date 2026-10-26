"""gates.secrets -- the secret-staging gate (entry for block-sensitive-staging.sh).

Two gates, unchanged from the bash original (issue #4, #1003):
  * Gate 1 -- block ``git add`` of a sensitive FILENAME (TARGETS.md, .env*,
    *.pem/*.key/..., *credential*/*secret*).
  * Gate 2 -- block ``git add`` / ``git commit`` when the STAGED CONTENT (added
    diff lines) carries an inlined secret VALUE, even inside an allowed file.

The classifier (``scan_line`` + helpers) is offline/pure and unit-tested here at
the module level; the git-touching driver (``scan_staged_content``) is exercised
end-to-end by the existing hook tests via the thin bash adapter. Bypass:
``# airuleset:secret-ok <reason>`` inline (quote-aware, via gates.shellcmd), logged
through gates.audit. See block-sensitive-staging.sh's header for the full history.
"""
import os
import re
import subprocess
import sys

from gates import allow, codeonly, command_of, emit_block, read_payload
from gates import audit
from gates.shellcmd import strip_quoted

MAX_FILE_BYTES = 2_000_000  # skip absurdly large files (binaries, dumps)

# --- secret pattern matching -------------------------------------------------
# Issue 1137 B1 (owner 26.10.2026): a generic 32+ char / 40+ hex blob is NOT a
# secret (every sampled subdev block was a test class name, a receipt id, a
# tag sha or a data URI). What blocks: a KNOWN credential format, or a value
# assigned to a name that says password/passwd/passphrase/secret/token/api key.

_NAMES = (r"password|passphrase|passwd|secret|token|api[_-]?key"
          r"|(?:access|private|auth|client|signing)[_-]?key")
# `name = "value"`, `name: 'value'` and the JSON `"name": "value"` shape.
KV_PAT = re.compile(
    r"""(?i)(""" + _NAMES + r"""|(?<![A-Za-z])pat)["']?"""
    r"""[ \t]*[=:][ \t]*(['"])[^'"$<{\n]{8,}\2"""
)
# Shell / .env assignment: `ODOO_API_KEY=<value>`, `export TOKEN=<value>`.
BARE_ASSIGN_PAT = re.compile(
    r"""(?i)(?<![\w.-])[\w.-]*?(""" + _NAMES + r""")=([^\s'"`$<{}()\[\],;]{8,})""")
# A bare `key` / `auth` / `credentials` name holding a token-shaped value.
TOKEN_KEY_PAT = re.compile(
    r"""(?i)(?<![A-Za-z])(key|auth|bearer|credentials?)["']?[ \t]*[=:][ \t]*"""
    r"""["']([A-Za-z0-9_+/=-]{20,})["']""")
BEARER_PAT = re.compile(r"(?i)\bbearer[ \t]+([A-Za-z0-9._~+/=-]{20,})")
SSHPASS_PAT = re.compile(r"""sshpass\s+-p\s+(['"])([^'"\n]{4,})\1""")
# Known credential FORMATS. Checked first, so a real token blocks on any line.
SECRET_PREFIX_PAT = re.compile(
    r"gh[oprsu]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}"
    r"|sk-proj-[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|[rs]k_live_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|npm_[A-Za-z0-9]{36}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
)
# A Discord bot token: base64 user id . 6 chars . 27+ chars. Both outer parts
# carry a digit, so a dotted CamelCase identifier is not one.
DISCORD_PAT = re.compile(
    r"(?<![\w.-])([MN][A-Za-z0-9_-]{23,25})\.[A-Za-z0-9_-]{6}\.([A-Za-z0-9_-]{27,})")
_DIGIT_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[A-Za-z]")


PLACEHOLDER_RE = re.compile(
    r"^\$|^<|^\{\{|^YOUR_|EXAMPLE|CHANGEME|PLACEHOLDER|^TODO$|^FIXME$|^REDACTED$",
    re.I,
)


def is_placeholder(val):
    v = (val or "").strip()
    if not v:
        return True
    if PLACEHOLDER_RE.search(v):
        return True
    if re.fullmatch(r"(.)\1{3,}", v):  # xxxxxxxx / aaaaaaaa filler
        return True
    return False


def _token_like(val):
    return bool(_DIGIT_RE.search(val) and _LETTER_RE.search(val)
                and not is_placeholder(val))


def scan_line(text):
    """Return a short violation description for one line, or None."""
    m = SECRET_PREFIX_PAT.search(text)
    if m and not is_placeholder(m.group(0)):
        return "high-confidence secret token prefix"
    for m in DISCORD_PAT.finditer(text):
        if _DIGIT_RE.search(m.group(1)) and _DIGIT_RE.search(m.group(2)):
            return "high-confidence secret token prefix"
    m = SSHPASS_PAT.search(text)
    if m and not is_placeholder(m.group(2)):
        return "sshpass literal password"
    m = KV_PAT.search(text)
    if m:
        val_m = re.search(r"""(['"])([^'"$<{\n]{8,})\1\s*$""", m.group(0))
        val = val_m.group(2) if val_m else ""
        if not is_placeholder(val):
            return "literal " + m.group(1).lower() + " value"
    for pat in (BARE_ASSIGN_PAT, TOKEN_KEY_PAT, BEARER_PAT):
        for m in pat.finditer(text):
            if _token_like(m.group(m.lastindex)):
                return "literal %s value" % m.group(1).lower()
    return None


# Lockfile integrity hashes ("integrity": "sha512-...") are NOT secrets (#19).
LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "cargo.lock", "poetry.lock", "uv.lock",
    "pipfile.lock", "composer.lock", "gemfile.lock", "go.sum", "flake.lock",
    "packages.lock.json", "pubspec.lock",
}

# --- gate 1: sensitive FILENAME (git add only) ------------------------------

_ADD_ALLOW = (".env.example", ".env.sample", ".env.template", ".env.dist")


def filename_violation(cmd):
    """The first sensitive filename staged by a ``git add`` in ``cmd`` (empty
    string if none). Verbatim from Gate 1 of block-sensitive-staging.sh."""
    m = re.search(r"git\s+add\b([^\n;|&]*)", cmd)
    args = m.group(1) if m else ""
    q = chr(34) + chr(39)
    bad = ""
    for t in args.split():
        t = t.strip(q)
        if not t or t.startswith("-"):
            continue
        base = t.rsplit("/", 1)[-1].lower()
        if base == "targets.md":
            bad = t
        elif base == ".env" or (base.startswith(".env.") and not base.startswith(_ADD_ALLOW)):
            bad = t
        elif re.search(r"\.(pem|key|p12|p8|pfx|keystore|jks)$", base):
            bad = t
        elif "credential" in base or "secret" in base:
            bad = t
        if bad:
            break
    return bad


# --- gate 2: staged CONTENT-value scan (git add and git commit) -------------


def _git(args):
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


def _diff_added_lines(args):
    """Run ``git diff <args>`` and return {file: [added_line, ...]}."""
    out = _git(["diff", "-U0"] + args)
    result = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:]
            if path.startswith("b/"):
                path = path[2:]
            cur = path
            result.setdefault(cur, [])
        elif line.startswith("+") and not line.startswith("+++"):
            if cur is not None:
                result[cur].append(line[1:])
    return result


def _status_code(path):
    out = _git(["status", "--porcelain", "--", path])
    line = out.splitlines()[0] if out.splitlines() else ""
    return line[:2] if len(line) >= 2 else "  "


def scan_staged_content(cmd, is_add, is_commit):
    """The Gate 2 driver: return a list of (path, reason, snippet) violations
    for the staged content ``cmd`` is about to add/commit. Verbatim from
    block-sensitive-staging.sh's embedded scanner; any internal failure yields
    an empty list (fail toward not-blocking, as before)."""
    violations = []

    def check_lines(path, lines):
        if path.rsplit("/", 1)[-1].lower() in LOCKFILES:
            return
        for line in lines:
            v = scan_line(line)
            if v:
                violations.append((path, v, line.strip()[:100]))
                return  # one hit per file is enough to block

    try:
        if is_add:
            m = re.search(r"git\s+add\b([^\n;|&]*)", cmd)
            argstr = m.group(1) if m else ""
            raw = [t.strip("'\"") for t in argstr.split()]
            flags = [t for t in raw if t.startswith("-")]
            paths = [t for t in raw if t and not t.startswith("-")]
            wildcard = ("-A" in flags or "--all" in flags
                        or "." in paths or "*" in paths or not paths)

            targets = []
            if wildcard:
                for line in _git(["status", "--porcelain", "--untracked-files=all"]).splitlines():
                    if len(line) < 4:
                        continue
                    code, path = line[:2], line[3:]
                    if "D" in code:
                        continue
                    targets.append((path, code))
            else:
                for p in paths:
                    targets.append((p, _status_code(p)))

            for path, code in targets:
                if violations:
                    break
                if code.strip() == "??":
                    try:
                        if os.path.getsize(path) > MAX_FILE_BYTES:
                            continue
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            check_lines(path, f.read().splitlines())
                    except Exception:
                        continue
                else:
                    check_lines(path, _diff_added_lines(["--", path]).get(path, []))
                    if not violations:
                        check_lines(path, _diff_added_lines(["--cached", "--", path]).get(path, []))

        if is_commit and not violations:
            m = re.search(r"git\s+commit\b([^\n;|&]*)", cmd)
            argstr = m.group(1) if m else ""
            tokens = argstr.split()
            is_all = False
            for t in tokens:
                if t in ("-a", "--all"):
                    is_all = True
                elif t.startswith("-") and not t.startswith("--") and "a" in t[1:]:
                    is_all = True

            for path, lines in _diff_added_lines(["--cached"]).items():
                check_lines(path, lines)
                if violations:
                    break
            if is_all and not violations:
                for path, lines in _diff_added_lines([]).items():
                    check_lines(path, lines)
                    if violations:
                        break
    except Exception:
        return []

    return violations


# --- bypass ------------------------------------------------------------------

_BYPASS_RE = re.compile(r"#[ \t]*airuleset:secret-ok[ \t]+([^\n]+)")


def bypass_reason(cmd):
    """The ``# airuleset:secret-ok <reason>`` bypass reason from ``cmd`` (last
    occurrence), or "" -- searched on the QUOTE-STRIPPED command so a marker
    that merely appears inside a quoted commit-message body does not bypass the
    scan. Verbatim shape from block-sensitive-staging.sh's bypass parse."""
    unquoted = strip_quoted(cmd)
    m = None
    for mm in _BYPASS_RE.finditer(unquoted):
        m = mm
    return m.group(1).rstrip() if m else ""


# --- messages (verbatim) -----------------------------------------------------

def _gate1_msg(bad):
    return ("BLOCKED: refusing to stage sensitive file '%s'.\n"
            "If you really need to stage it, do it manually outside Claude Code,\n"
            "or rename/relocate it out of the secret-file patterns." % bad)


def _gate2_msg(violations):
    lines = [path + ": " + reason + " — " + snippet
             for path, reason, snippet in violations[:5]]
    indented = "\n".join("    " + ln for ln in "\n".join(lines).splitlines())
    return ("\n🚫 BLOCKED: staged content contains an inlined secret VALUE.\n\n"
            "%s\n\n"
            "  Scrub the literal value (use an env var / placeholder instead).\n"
            "  If this is genuinely NOT a secret, bypass with:\n"
            "    # airuleset:secret-ok <reason>\n"
            "  appended to the command (logged to audits/secret-scan-bypasses.log).\n"
            "  See modules/quality/security-basics.md.\n" % indented)


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if block := codeonly.violation(cmd):   # #1220: before, and immune to, any bypass
        emit_block(block)

    is_add = bool(re.search(r"git\s+add", cmd))
    is_commit = bool(re.search(r"git\s+commit", cmd))
    if not is_add and not is_commit:
        allow()

    reason = bypass_reason(cmd)
    if reason:
        audit.append_line(
            "secret-scan-bypasses.log",
            "%s  project=%s  %s" % (audit.iso_now(), audit.project_of(), reason))
        allow()

    if is_add:
        bad = filename_violation(cmd)
        if bad:
            emit_block(_gate1_msg(bad))

    # Gate 2 is only meaningful inside a git work tree.
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=8)
        inside = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        inside = False
    if not inside:
        allow()

    violations = scan_staged_content(cmd, is_add, is_commit)
    if violations:
        emit_block(_gate2_msg(violations))

    allow()


if __name__ == "__main__":
    sys.exit(main())
