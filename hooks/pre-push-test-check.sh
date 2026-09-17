#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1020 gate-family rework).
#
# Blocks a `git push` when: (1) feature code changed but no test files;
# (2) a bug-fix commit precedes any test commit in the PR (RED-before-GREEN);
# and WARNS on shallow test assertions. All logic lives in gates/pushtest.py;
# the BASE_REF resolution (#847/#909) is shared via gates.pushscope (WITHOUT the
# origin/<branch> override -- these gates are PR-scoped, #909 F1). Bypass:
# `[no-test: <reason>]` in the latest commit message, logged to
# audits/no-test-skips.log via gates.audit.
# Exit code 2 = block. The reason is routed to STDERR only (#682) via the `1>&2`
# below, which replaces the original hook's `exec 1>&2`. A python3 malfunction
# fails CLOSED with an honest "internal error" reason, never a silent pass.
# Dry-run: echo '{"tool_input":{"command":"git push origin dev"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.pushtest <<<"$PAYLOAD" 1>&2 || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): pre-push-test-check internal error — the gate" >&2
    echo "  exited $RC instead of running the check. This is a HOOK MALFUNCTION," >&2
    echo "  not necessarily a real violation — fix the hook (or python3) first." >&2
    exit 2
fi
exit "$RC"
