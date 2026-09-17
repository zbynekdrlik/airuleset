#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1020 gate-family rework,
# #1049 refresh-newer teeth).
#
# Gates a gk ACTION request (`airuleset.py gk-request`, a raw `gh` adding
# `needs-gatekeeper`, or a `GATEKEEPER-ACTION:` body) from a REDUCED-authority
# sub-dev stream. #516: it must carry a falsifiable `Self-service-checked:`
# line. #1049: a request that reads as a SELF-SERVICEABLE PROD READ (read-verb +
# PROD, not a live intervention, not a whitelisted gk-only surface) must ALSO
# cite `refresh <run-id|comment-id> at <ISO-UTC>` newer than the newest event
# timestamp, else it blocks (a copy older than the event is a reason to REFRESH,
# never to escalate). All logic lives in gates/selfservice.py; a review hand-off
# (ready-for-review / stream:<x> / READY-FOR-REVIEW:) is never gated (rule 8); a
# full/unresolvable-authority box is never gated (degrade-to-allow, #390). Every
# verdict is logged to ~/.claude/selfservice-gate.log. Bypass:
# `# airuleset:selfservice-ok <reason>`.
# Exit code 2 = block; the reason is routed to STDERR (the model-visible channel
# on a PreToolUse deny, #682) via the `1>&2` below. A python3 malfunction fails
# CLOSED with an honest "internal error" reason, never a silent pass.
# Dry-run: echo '{"tool_input":{"command":"gh issue edit 5 --add-label needs-gatekeeper"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.selfservice <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-gk-request-without-selfservice internal" >&2
    echo "  error — the gate exited $RC instead of running the check. This is a" >&2
    echo "  HOOK MALFUNCTION, not necessarily a real violation — fix the hook" >&2
    echo "  (or python3) first." >&2
    exit 2
fi
exit "$RC"
