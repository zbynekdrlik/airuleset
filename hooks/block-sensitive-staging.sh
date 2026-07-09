#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
#
# Gate 1 (original): blocks `git add` of sensitive FILENAMES (TARGETS.md,
# .env*, credentials/secrets, *.pem/*.key/*.p12/...).
#
# Gate 2 (issue #4): blocks `git add` / `git commit` when the STAGED CONTENT
# itself contains an inlined secret VALUE, even inside an otherwise-allowed
# file (e.g. `.claude/skills/**`, `CLAUDE.md`) — a filename-only gate misses
# exactly this case, which is how the playbook-rollout secret leak happened
# (camera-box #212 OBS WS password, restreamer #271 FB App Secret: both
# leaked from INSIDE a committed skill file, not from a sensitive filename).
#
# Gate 2 scans only ADDED lines (git diff, not whole-file content) so
# pre-existing committed content is never re-flagged just because an
# unrelated part of the same file changed. It fires on BOTH `git add`
# (early feedback on the files named in the command) AND `git commit`
# (a comprehensive backstop over whatever is actually about to be
# committed — `--cached` diff, plus the unstaged diff too when `-a`/`-am`
# is used — regardless of how it got staged: `add`, `add -A`, `add -p`).
#
# Patterns: `sshpass -p '<literal>'`, `password|passphrase|secret|token|
# api[_-]?key` assigned a literal (quoted, 8+ char) value, 40+ char hex
# blobs, and 32+ char base64-ish blobs. A captured value that looks like a
# placeholder/env-ref ($VAR, <secret>, {{...}}, YOUR_*, *EXAMPLE*, repeated
# filler like xxxxxxxx) is NOT flagged.
#
# Bypass: `# airuleset:secret-ok <reason>` inline in the command (same
# convention as `airuleset:deploy-dirty-ok` / `airuleset:test-skip-ok`).
# Every bypass is logged to audits/secret-scan-bypasses.log.
#
# Exit code 2 = block the tool call.
#
# Reads the payload from STDIN (current CC contract; $TOOL_INPUT is the dead old
# env var — kept as a fallback). The $TOOL_INPUT-only version was a silent no-op.

PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
INPUT=$(printf '%s' "$PAYLOAD" | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception: pass' 2>/dev/null || echo "")
# Fall back to the raw payload ONLY when JSON parsing produced nothing AND the
# payload was not valid tool JSON — so an empty `command` never makes us scan the
# whole JSON blob (which would false-match on any nested string).
if [ -z "$INPUT" ]; then
    case "$PAYLOAD" in
        *'"tool_input"'*) INPUT="" ;;
        *) INPUT="$PAYLOAD" ;;
    esac
fi

# Only relevant for git add / git commit commands. Checked BEFORE the
# bypass marker below so an unrelated command is never touched at all.
IS_ADD=0
IS_COMMIT=0
echo "$INPUT" | grep -qE 'git\s+add' && IS_ADD=1
echo "$INPUT" | grep -qE 'git\s+commit' && IS_COMMIT=1
if [ "$IS_ADD" = 0 ] && [ "$IS_COMMIT" = 0 ]; then
    exit 0
fi

# Bypass marker short-circuits BOTH gates below, same shape as the other
# airuleset inline-marker bypasses. Requires a non-empty reason, AND the
# marker must be OUTSIDE any quoted string — a real bash `#` only starts a
# comment when it is not inside quotes, so quoted spans are stripped FIRST.
# Without this, the marker text merely being MENTIONED inside a commit
# message body (e.g. this very hook's own commit messages document the
# syntax) would match too, silently bypassing the scan for a commit that
# carries a real secret (this happened for real, twice, while authoring
# this hook — see the audits/secret-scan-bypasses.log entries it produced).
BYPASS_REASON=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
SQ=chr(39)
DQ=chr(34)
unquoted=re.sub(SQ+"[^"+SQ+"]*"+SQ, "", cmd)     # strip '"'"'...'"'"' spans
unquoted=re.sub(DQ+"[^"+DQ+"]*"+DQ, "", unquoted)  # strip "..." spans
m=None
for mm in re.finditer(r"#[ \t]*airuleset:secret-ok[ \t]+([^\n]+)", unquoted):
    m=mm
if m:
    print(m.group(1).rstrip())
' 2>/dev/null || echo "")

if [ -n "$BYPASS_REASON" ]; then
    AUDIT_LOG="$HOME/devel/airuleset/audits/secret-scan-bypasses.log"
    mkdir -p "$(dirname "$AUDIT_LOG")"
    PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || echo unknown)")
    echo "$(date -Iseconds)  project=$PROJECT  $BYPASS_REASON" >> "$AUDIT_LOG"
    exit 0
fi

# --- Gate 1: filename-based block (git add only) — unchanged from before ---
if [ "$IS_ADD" = 1 ]; then
    # Find the first sensitive token (empty if none). Pure double-quoted python (the
    # body is inside shell single quotes, so NO single quotes / no f-string escapes).
    BAD=$(printf '%s' "$INPUT" | python3 -c 'import re,sys
cmd=sys.stdin.read()
# Capture ONLY the git-add arguments — stop at the first command separator
# (&& || ; | newline). The old r"(.*)" with re.S over-captured the rest of a
# compound command (e.g. `git add x && git commit -m "...secret..."`), so the
# words "secret"/"credential" in a later commit message or piped command
# false-tripped the sensitive-filename check.
m=re.search(r"git\s+add\b([^\n;|&]*)", cmd)
args=m.group(1) if m else ""
Q=chr(34)+chr(39)
allow=(".env.example",".env.sample",".env.template",".env.dist")
bad=""
for t in args.split():
    t=t.strip(Q)
    if not t or t.startswith("-"): continue
    base=t.rsplit("/",1)[-1].lower()
    if base=="targets.md": bad=t
    elif base==".env" or (base.startswith(".env.") and not base.startswith(allow)): bad=t
    elif re.search(r"\.(pem|key|p12|p8|pfx|keystore|jks)$", base): bad=t
    elif "credential" in base or "secret" in base: bad=t
    if bad: break
print(bad)
' 2>/dev/null || echo "")

    if [ -n "$BAD" ]; then
        echo "BLOCKED: refusing to stage sensitive file '${BAD}'."
        echo "If you really need to stage it, do it manually outside Claude Code,"
        echo "or rename/relocate it out of the secret-file patterns."
        exit 2
    fi
fi

# --- Gate 2: staged CONTENT-value scan (git add and git commit) ------------
# Only meaningful inside a git work tree.
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

VIOLATIONS=$(python3 - "$INPUT" "$IS_ADD" "$IS_COMMIT" <<'PYEOF'
import re
import subprocess
import sys

CMD, IS_ADD, IS_COMMIT = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"

MAX_FILE_BYTES = 2_000_000  # skip absurdly large files (binaries, dumps)


def git(args):
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return ""


# --- secret pattern matching -------------------------------------------

KV_PAT = re.compile(
    r"""(?i)(password|passphrase|secret|token|api[_-]?key)"""
    r"""[ \t]*[=:][ \t]*(['"])[^'"$<{\n]{8,}\2"""
)
SSHPASS_PAT = re.compile(r"""sshpass\s+-p\s+(['"])([^'"\n]{4,})\1""")
# No \b anchor: '_' is a \w char, so a plain \b never anchors between "ghp_"
# and the token that follows it (real leaked tokens are routinely prefixed
# like that). The character class itself already bounds the match tightly
# (greedy {N,} stops at the first non-matching char — quote, space, '_',
# '/', punctuation), so an explicit boundary assertion is unnecessary and
# was actively wrong (it blocked matches right after '_').
HEX_PAT = re.compile(r"[0-9a-fA-F]{40,}")
# Deliberately excludes '/' (path segments are NOT secrets) and requires at
# least one digit (excludes plain-English/camelCase identifiers like
# "TestProseViolationsAutoMergeSignals") — both tuned against this repo's
# OWN doc/skill/test corpus (zero false positives at authoring time).
B64_PAT = re.compile(r"(?=[A-Za-z0-9+]*[0-9])[A-Za-z0-9+]{32,}={0,2}")

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
    m = SSHPASS_PAT.search(text)
    if m and not is_placeholder(m.group(2)):
        return "sshpass literal password"
    m = KV_PAT.search(text)
    if m:
        val_m = re.search(r"""(['"])([^'"$<{\n]{8,})\1\s*$""", m.group(0))
        val = val_m.group(2) if val_m else ""
        if not is_placeholder(val):
            return "literal " + m.group(1).lower() + " value"
    m = HEX_PAT.search(text)
    if m and not is_placeholder(m.group(0)):
        return "40+ char hex blob (possible key/token)"
    m = B64_PAT.search(text)
    if m and not is_placeholder(m.group(0)):
        return "32+ char high-entropy blob (possible secret)"
    return None


violations = []


def check_lines(path, lines):
    for line in lines:
        v = scan_line(line)
        if v:
            violations.append((path, v, line.strip()[:100]))
            return  # one hit per file is enough to block


def diff_added_lines(args):
    """Run `git diff <args>` and return {file: [added_line, ...]}."""
    out = git(["diff", "-U0"] + args)
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


def status_code(path):
    out = git(["status", "--porcelain", "--", path])
    line = out.splitlines()[0] if out.splitlines() else ""
    return line[:2] if len(line) >= 2 else "  "


try:
    if IS_ADD:
        m = re.search(r"git\s+add\b([^\n;|&]*)", CMD)
        argstr = m.group(1) if m else ""
        raw = [t.strip("'\"") for t in argstr.split()]
        flags = [t for t in raw if t.startswith("-")]
        paths = [t for t in raw if t and not t.startswith("-")]
        wildcard = ("-A" in flags or "--all" in flags
                    or "." in paths or "*" in paths or not paths)

        targets = []
        if wildcard:
            for line in git(["status", "--porcelain", "--untracked-files=all"]).splitlines():
                if len(line) < 4:
                    continue
                code, path = line[:2], line[3:]
                if "D" in code:
                    continue
                targets.append((path, code))
        else:
            for p in paths:
                targets.append((p, status_code(p)))

        for path, code in targets:
            if violations:
                break
            if code.strip() == "??":
                try:
                    import os
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        continue
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        check_lines(path, f.read().splitlines())
                except Exception:
                    pass
            else:
                check_lines(path, diff_added_lines(["--", path]).get(path, []))
                if not violations:
                    check_lines(path, diff_added_lines(["--cached", "--", path]).get(path, []))

    if IS_COMMIT and not violations:
        m = re.search(r"git\s+commit\b([^\n;|&]*)", CMD)
        argstr = m.group(1) if m else ""
        tokens = argstr.split()
        is_all = False
        for t in tokens:
            if t in ("-a", "--all"):
                is_all = True
            elif t.startswith("-") and not t.startswith("--") and "a" in t[1:]:
                is_all = True

        for path, lines in diff_added_lines(["--cached"]).items():
            check_lines(path, lines)
            if violations:
                break
        if is_all and not violations:
            for path, lines in diff_added_lines([]).items():
                check_lines(path, lines)
                if violations:
                    break
except Exception:
    violations = []

for path, reason, snippet in violations[:5]:
    print(path + ": " + reason + " — " + snippet)
PYEOF
)

if [ -n "$VIOLATIONS" ]; then
    echo ""
    echo "🚫 BLOCKED: staged content contains an inlined secret VALUE."
    echo ""
    echo "$VIOLATIONS" | sed 's/^/    /'
    echo ""
    echo "  Scrub the literal value (use an env var / placeholder instead)."
    echo "  If this is genuinely NOT a secret, bypass with:"
    echo "    # airuleset:secret-ok <reason>"
    echo "  appended to the command (logged to audits/secret-scan-bypasses.log)."
    echo "  See modules/quality/security-basics.md."
    echo ""
    exit 2
fi

exit 0
