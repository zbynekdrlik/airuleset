#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1298).
#
# Refuses `gh issue edit <N> --add-label ops-wait` (comma lists too) unless a
# valid
#   Waiting-on: <third party> — <the concrete event> — <how it is checked>
# line is in a `gh issue comment` on that ticket in the same command or in a
# comment created on it in the last 30 minutes; `gh issue create --label
# ops-wait` is always refused (create, comment, then label). The party is
# never the owner (owner waits are U: needs-owner-action / needs-answer),
# never us / a session of ours, never the gatekeeper.
# All logic lives in gates/opswait.py; it FAILS OPEN on a gh read error or an
# unresolvable ticket (logged), logs every verdict to
# ~/.claude/opswait-gate.log, and honours `# airuleset:opswait-ok <reason>`.
# Exit 2 = block; the reason goes to STDERR (#682) via the `1>&2` below. A
# python3 malfunction fails CLOSED with an honest "internal error" reason.
# Dry-run: AIRULESET_OPSWAIT_COMMENTS_FIXTURE=/tmp/fx.json \
#   echo '{"tool_input":{"command":"gh issue edit 5 --add-label ops-wait"}}' \
#   | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
# Cheap pre-filter: no python start-up on the many Bash calls without ops-wait.
case "${PAYLOAD,,}" in
    *ops-wait*) ;;
    *) exit 0 ;;
esac
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.opswait <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-ops-wait-without-waiting-on internal" >&2
    echo "  error — the gate exited $RC instead of running the check. This is a" >&2
    echo "  HOOK MALFUNCTION, not necessarily a real violation — fix the hook (or" >&2
    echo "  python3) first, or bypass with # airuleset:opswait-ok <reason>." >&2
    exit 2
fi
exit "$RC"
