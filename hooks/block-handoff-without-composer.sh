#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse(Bash) -- #843 ("Sub-dev mandatory hand-off composer").
#
# GATE: a READY-FOR-REVIEW hand-off comment post is allowed ONLY when:
#   (a) the command invokes airuleset.py handoff (the composer itself), OR
#   (b) a fresh receipt exists whose sha256 matches the body, OR
#   (c) the box has full authority (gk box never gated).
#
# Fail-safe: authority resolution failure -> allow.
# Bypass: # airuleset:handoff-ok <reason>

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Allow the CLI composer itself — anchored on the SUBCOMMAND position
# (airuleset.py handoff or airuleset handoff as the invoked program),
# not a loose substring pair that body content can satisfy.
case "$CMD" in
    *"airuleset.py handoff"*|*"airuleset handoff"*) exit 0 ;;
esac
case "$CMD" in *"airuleset:handoff-ok"*) exit 0 ;; esac

# Gate only on comment-posting commands (gh issue comment / gh api comments).
case "$CMD" in
    *"gh issue comment"*) ;;
    *"gh api"*"comments"*) ;;
    *) exit 0 ;;
esac

# Pre-filter: check if the body carries READY-FOR-REVIEW — either inline
# (--body "...READY-FOR-REVIEW...") OR in a body file (-F / --body-file).
# The mandated flow uses -F body.md, so ONLY checking the command text
# misses the canonical posting shape (#843 review RED-1).
_HAS_MARKER=0
case "$CMD" in
    *"READY-FOR-REVIEW"*|*"Ready for gatekeeper cross-fork review"*)
        _HAS_MARKER=1 ;;
esac
if [ "$_HAS_MARKER" -eq 0 ]; then
    # Check if a -F / --body-file argument points to a file with the marker.
    _BODY_FILE=""
    for _tok in $CMD; do
        case "$_tok" in
            -F|--body-file) _BODY_FILE="NEXT" ;;
            *)
                if [ "$_BODY_FILE" = "NEXT" ]; then
                    _BODY_FILE="$_tok"
                    break
                fi ;;
        esac
    done
    if [ -n "$_BODY_FILE" ] && [ "$_BODY_FILE" != "NEXT" ] && [ -r "$_BODY_FILE" ]; then
        if grep -q "READY-FOR-REVIEW\|Ready for gatekeeper cross-fork review" "$_BODY_FILE" 2>/dev/null; then
            _HAS_MARKER=1
        fi
    fi
fi
[ "$_HAS_MARKER" -eq 0 ] && exit 0

LOG="$HOME/.claude/handoff-gate.log"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || HOOK_DIR=""
REPO_ROOT_DIR=""
[ -n "$HOOK_DIR" ] && REPO_ROOT_DIR="$(dirname "$HOOK_DIR")"

RC=0
OUT=$(python3 - "$CMD" "$(pwd)" "$REPO_ROOT_DIR" <<'PYEOF' 2>/dev/null
import hashlib, json, os, re, shlex, sys, time

cmd, cwd, repo_root = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    if repo_root:
        sys.path.insert(0, repo_root)
    import airuleset
    if airuleset.resolve_authority(cwd=cwd) == "full":
        print("ALLOW full-authority box"); sys.exit(0)
except Exception:
    print("ALLOW authority-resolution-failed"); sys.exit(0)

body = ""
try:
    tokens = shlex.split(cmd)
except ValueError:
    tokens = cmd.split()

for i, tok in enumerate(tokens):
    if tok in ("--body-file", "-F") and i + 1 < len(tokens):
        path = tokens[i + 1]
        if path.startswith("@"):
            path = path[1:]
        try:
            with open(path) as f:
                body = f.read()
        except OSError:
            pass
        break

if not body:
    for i, tok in enumerate(tokens):
        if tok == "--body" and i + 1 < len(tokens):
            body = tokens[i + 1]
            break

if not body:
    print("ALLOW body-unresolvable"); sys.exit(0)

rfr = re.compile(r'^\s*([#*_-]+\s*)?READY-FOR-REVIEW', re.MULTILINE)
cfr = re.compile(r'Ready for gatekeeper cross-fork review[.!]?\s*$', re.MULTILINE)
if not rfr.search(body) and not cfr.search(body):
    print("ALLOW no-RFR-in-body"); sys.exit(0)

body_hash = hashlib.sha256(body.encode()).hexdigest()
gate_dir = os.path.join(os.path.expanduser("~"), ".claude/handoff-gate")
now = time.time()

found = False
if os.path.isdir(gate_dir):
    for fn in os.listdir(gate_dir):
        if not fn.endswith(".json"):
            continue
        fp = os.path.join(gate_dir, fn)
        try:
            with open(fp) as f:
                r = json.loads(f.read())
            if r.get("sha256") == body_hash and now - r.get("ts", 0) <= 600:
                found = True; break
        except (OSError, ValueError, TypeError):
            continue

if found:
    print("ALLOW receipt-match"); sys.exit(0)

print("BLOCK READY-FOR-REVIEW comment must be composed via "
      "'python3 airuleset.py handoff' (no matching fresh receipt)")
sys.exit(2)
PYEOF
) || RC=$?

LINE1=$(printf '%s
' "$OUT" | head -n1)
case "$LINE1" in
    "ALLOW"*|"")
        { printf '%s ALLOW %s
' "$(date -u +%FT%TZ)" "$LINE1"; } >> "$LOG" 2>/dev/null || true
        exit 0 ;;
    "BLOCK"*)
        { printf '%s BLOCK %s
' "$(date -u +%FT%TZ)" "$LINE1"; } >> "$LOG" 2>/dev/null || true
        printf '%s
' "$LINE1" >&2
        exit 2 ;;
esac
exit 0
