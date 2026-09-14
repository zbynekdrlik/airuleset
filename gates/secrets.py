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

from gates import allow, command_of, emit_block, read_payload
from gates import audit
from gates.shellcmd import strip_quoted

MAX_FILE_BYTES = 2_000_000  # skip absurdly large files (binaries, dumps)

# --- secret pattern matching (verbatim from the #4 / #1003 classifier) ------

KV_PAT = re.compile(
    r"""(?i)(password|passphrase|secret|token|api[_-]?key)"""
    r"""[ \t]*[=:][ \t]*(['"])[^'"$<{\n]{8,}\2"""
)
SSHPASS_PAT = re.compile(r"""sshpass\s+-p\s+(['"])([^'"\n]{4,})\1""")
HEX_PAT = re.compile(r"[0-9a-fA-F]{40,}")
B64_PAT = re.compile(r"(?=[A-Za-z0-9+]*[0-9])[A-Za-z0-9+]{32,}={0,2}")
# #1003 -- high-confidence PREFIXED secret shapes. Checked FIRST in scan_line
# so a real token stays blocked even on a line that also carries a git object
# SHA (which HEX_PAT/B64_PAT below no longer flag).
SECRET_PREFIX_PAT = re.compile(
    r"gh[oprsu]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}"
    r"|sk-proj-[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
# #1003 -- a hex RUN of exactly a git object-id length (40 = SHA-1, 64 = SHA-256)
# is a commit/tree/blob SHA shape, not a credential -- but CONTEXT-AWARE (review
# F1): a same-length hex ASSIGNED to a secret key must still block.
_GIT_OBJECT_HEX_LENS = (40, 64)
_ALL_HEX_RE = re.compile(r"[0-9a-fA-F]+$")
_ASSIGN_KEY_RE = re.compile(r"([A-Za-z_][\w.-]*)['\"]?[ \t]*[:=][ \t]*['\"]?[ \t]*$")
_SHA_CTX_KEY_RE = re.compile(
    r"sha|commit|\boid\b|\btree\b|\bblob\b|parent|revision|checksum|digest|"
    r"\bhash\b|\brev\b|\bref\b",
    re.IGNORECASE)
_SECRET_CTX_KEY_RE = re.compile(
    r"pass|secret|token|\bapi\b|api[_-]?key|auth|cred|bearer|private|\bpat\b|"
    r"\bkey\b|access",
    re.IGNORECASE)


def _is_git_object_sha(text, start, run):
    """True iff ``run`` (a hex substring of ``text`` at offset ``start``) is a
    git object-id SHA safe to treat as inert: exactly a git object length, all
    hex, AND either BARE or under a SHA-context key that is not a credential key
    (review F1)."""
    if len(run) not in _GIT_OBJECT_HEX_LENS or not _ALL_HEX_RE.match(run):
        return False
    m = _ASSIGN_KEY_RE.search(text[:start])
    if not m:
        return True                       # bare / unassigned -> SHA shape
    key = m.group(1)
    if _SECRET_CTX_KEY_RE.search(key):
        return False                      # credential key -> never a SHA
    return bool(_SHA_CTX_KEY_RE.search(key))


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


def scan_line(text):
    """Return a short violation description for one line, or None."""
    m = SECRET_PREFIX_PAT.search(text)
    if m and not is_placeholder(m.group(0)):
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
    for m in HEX_PAT.finditer(text):
        run = m.group(0)
        if is_placeholder(run) or _is_git_object_sha(text, m.start(), run):
            continue
        return "40+ char hex blob (possible key/token)"
    for m in B64_PAT.finditer(text):
        run = m.group(0)
        if is_placeholder(run) or _is_git_object_sha(text, m.start(), run):
            continue
        return "32+ char high-entropy blob (possible secret)"
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
