#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1020 gate-family rework).
#
# Blocks a `git push` whose outgoing diff ADDS a test-skip / tautology pattern
# in a TEST file (modules/ci/test-strictness.md). All logic lives in
# gates/testskips.py: the BASE/DEST ref resolution (#847/#909/#1003) via
# gates.pushscope, the scan, and the `# airuleset:test-skip-ok <reason>` bypass
# (logged to audits/test-skip-bypasses.log via gates.audit).
# Exit code 2 = block. The reason is routed to STDERR only (the model-visible
# channel on a PreToolUse deny, #682) via the `1>&2` below, which replaces the
# original hook's `exec 1>&2`. A python3 malfunction (the gate cannot run) fails
# CLOSED with an honest "internal error" reason, never a silent pass.
# Dry-run: echo '{"tool_input":{"command":"git push origin main"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.testskips <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-test-skips internal error — the gate" >&2
    echo "  exited $RC instead of running the check. This is a HOOK MALFUNCTION," >&2
    echo "  not necessarily a real violation — fix the hook (or python3) first." >&2
    exit 2
fi
exit "$RC"
