#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Blocks `git add` of sensitive files (TARGETS.md, .env*, credentials/secrets,
# *.pem/*.key/*.p12/...). Exit code 2 = block the tool call.
#
# Matching is done on the actual file TOKENS after `git add`, with proper
# extension/filename rules — NOT a substring grep of the raw command. The old
# version grep'd patterns like "*.pem" (a literal asterisk to grep, so it never
# matched) and ".env" (regex dot, so it blocked "environment.ts"). Both bugs are
# fixed here.
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

# Only check commands that look like git add.
echo "$INPUT" | grep -qE 'git\s+add' || exit 0

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

exit 0
