#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop — force a stream to ACK a fresh gk BOUNCE before the turn ends
# (#1066 lane A). The refresh derivation (cli_bounce_unhandled) writes the
# UNHANDLED-bounce subset into the per-cwd tickets-status cache; this hook runs
# gates.bounce_unhandled (a CACHE read, NO gh on the Stop path) which exits 2
# when an unhandled bounce older than the 30-min grace has no `BOUNCE-ACK: #N`
# line in the turn's message. The mechanism the montalu1 incident needed
# (odoo-erp #6474 BOUNCE unhandled 30 h while five PRs merged) — "nemôžeš
# strácať bounced tickety" (owner 17.9.2026).
#
# Runner shape = the #1106 gates.spec_question shape: `python3 -P -m`, PYTHONPATH
# from the hook dir, FAIL-OPEN on any error. Per-session retry cap (like the
# sibling Stop hooks) so a persistent block can never wedge the session.

command -v jq &>/dev/null || exit 0

INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

RETRY_FILE="/tmp/airuleset-bounce-unhandled-block-${SID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3

if [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
    _HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
    _REPO_ROOT="$(dirname "$_HOOK_DIR")"
    _RC=0
    env PYTHONPATH="${_REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -P -m gates.bounce_unhandled <<<"$INPUT" 1>&2 || _RC=$?
    if [ "$_RC" -eq 2 ]; then
        echo "$((RETRIES+1))" > "$RETRY_FILE"
        exit 2
    fi
fi

# No block (or the retry cap was hit) → clear the counter and allow.
rm -f "$RETRY_FILE" 2>/dev/null || true
exit 0
