#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1056 L1 (d) / #1057 item 3).
#
# Blocks a BLIND label flip from a REDUCED-authority sub-dev stream: a
# `gh issue edit <N> --remove-label prio:bounce` or `--add-label
# ready-for-review` (and the -l/--label add forms, heredoc/cd-aware) when
# gk-watch reports `bounce-unanswered` for that ticket AND no commit landed
# since the verdict -- the exact odoo-erp #5613/#6890 blind flip (16.-17.9.2026).
# All logic lives in gates/labeledit.py; it FAILS OPEN on any gh error / low
# budget / unresolvable slug (never a wrong block), degrades-to-allow on a
# full/unresolvable-authority box, logs every verdict to
# ~/.claude/labeledit-gate.log, and honours the bypass
# `# airuleset:labeledit-ok <reason>`.
# Exit code 2 = block; the reason is routed to STDERR (the model-visible channel
# on a PreToolUse deny, #682) via the `1>&2` below. A python3 malfunction fails
# CLOSED with an honest "internal error" reason, never a silent pass.
# Dry-run: AIRULESET_GK_WATCH_FIXTURE=/tmp/fx.json \
#   echo '{"tool_input":{"command":"gh issue edit 5 --remove-label prio:bounce"}}' \
#   | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.labeledit <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-blind-label-flip internal error —" >&2
    echo "  the gate exited $RC instead of running the check. This is a HOOK" >&2
    echo "  MALFUNCTION, not necessarily a real violation — fix the hook (or" >&2
    echo "  python3) first, or bypass with # airuleset:labeledit-ok <reason>." >&2
    exit 2
fi
exit "$RC"
