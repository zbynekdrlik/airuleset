#!/usr/bin/env bash
set -euo pipefail
# Hook: Stop — enforces project-playbook-maintenance.md: a completion report
# (## ✅ Work Complete) MUST carry a "📔 Playbook:" line proving the post-ticket
# playbook-review ran. Blocks via {"decision":"block"} with a per-session cap.
command -v jq &>/dev/null || exit 0
INPUT=$(cat 2>/dev/null || echo "")
MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // empty' 2>/dev/null || echo "")
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")
[ -z "$MSG" ] && exit 0

# Only gate completion reports.
echo "$MSG" | grep -qE "^## ✅ Work Complete|^✅ Work Complete" || exit 0
# Pass if the playbook line is present.
echo "$MSG" | grep -qE "📔 Playbook:" && exit 0

RETRY_FILE="/tmp/airuleset-playbook-block-${SESSION_ID}"
RETRIES=$(cat "$RETRY_FILE" 2>/dev/null || echo 0)
MAX_RETRIES=3
if [ "$RETRIES" -lt "$MAX_RETRIES" ]; then
  echo "$((RETRIES+1))" > "$RETRY_FILE"
  REASON="Completion report is missing the '📔 Playbook:' line. Per project-playbook-maintenance.md, run the playbook-review skill before the report: capture any reusable procedure/gotcha to the project's .claude/skills/ (or CLAUDE.md router / memory per the routing rule), then add a 1-2 line '📔 Playbook: <what you learned/updated>' (or '📔 Playbook: nič nové' if genuinely nothing). This keeps project knowledge fresh + visible."
  jq -n --arg reason "$REASON" '{decision: "block", reason: $reason}'
  exit 0
fi
rm -f "$RETRY_FILE"
exit 0
