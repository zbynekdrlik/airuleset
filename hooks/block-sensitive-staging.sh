#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher) -- THIN ADAPTER (#1020 gate-family rework).
#
# All logic lives in gates/secrets.py (run as `python3 -m gates.secrets`):
#   * Gate 1 -- blocks `git add` of a sensitive FILENAME (TARGETS.md, .env*,
#     *.pem/*.key/*.p12/..., *credential*/*secret*).
#   * Gate 2 (#4) -- blocks `git add`/`git commit` when the STAGED CONTENT
#     carries an inlined secret VALUE, even inside an otherwise-allowed file.
#   * Bypass -- `# airuleset:secret-ok <reason>` inline (quote-aware), logged
#     to audits/secret-scan-bypasses.log via gates.audit.
# Exit code 2 = block the tool call; the reason prints to BOTH stdout and stderr.
#
# Reads the JSON payload from STDIN (current CC hook contract), falling back to
# the dead $TOOL_INPUT env var, and hands it to the module on stdin -- so the
# module runs its git subprocesses in this hook's inherited cwd (the session
# cwd), identical to the previous embedded-Python hook.
#
# Fail-CLOSED wrapper (#1020 part 2 item 2): this is a security-adjacent gate, so
# an INTERNAL python error (the gate cannot run -- import failure, a bug) must
# NOT let the tool proceed. gates.secrets prints its own block reason to BOTH
# stdout and stderr (emit_block), so there is NO `1>&2` here (unlike the push
# adapters); a non-0/2 exit becomes an honest HOOK MALFUNCTION exit 2.
# Dry-run: echo '{"tool_input":{"command":"git add .env"}}' | bash <this hook>

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
REPO_ROOT="$(dirname "$HOOK_DIR")"
PAYLOAD=$(cat 2>/dev/null || echo "")
[ -z "$PAYLOAD" ] && PAYLOAD="${TOOL_INPUT:-}"
RC=0
env PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m gates.secrets <<<"$PAYLOAD" || RC=$?
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
    echo "🚫 BLOCKED (fail-closed): block-sensitive-staging internal error — the gate" >&2
    echo "  exited $RC instead of running the check. This is a HOOK MALFUNCTION," >&2
    echo "  not necessarily a real violation — fix the hook (or python3) first." >&2
    exit 2
fi
exit "$RC"
