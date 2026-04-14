#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks custom bash monitor scripts that bypass Claude's Bash tool
# (e.g. /tmp/main-monitor.sh, while true loops writing to files).
# These detach from the session, don't trigger idle notifications,
# and break the CI monitoring flow. Only sleep+gh run view as a
# Bash background command is acceptable.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Pattern 1: writing a monitor script to disk
if echo "$CMD" | grep -qiE 'cat\s*>\s*(/tmp/|\$HOME/|~/|/var/).*(monitor|watch|poll|ci-check|ci_check)'; then
    echo "BLOCKED: Do not write custom monitor scripts. Use 'sleep N && gh run view <run-id>' with run_in_background:true via the Bash tool. See ci-monitoring.md." >&2
    exit 2
fi

# Pattern 2: bash script with while/until loop + sleep + gh (custom CI monitor)
if echo "$CMD" | grep -qE 'bash\s+/tmp/.*monitor|nohup.*(monitor|ci-watch)|while\s+true.*sleep.*gh\s+run'; then
    echo "BLOCKED: Custom monitor loops detach from Claude's session and don't trigger notifications. Use 'sleep N && gh run view <run-id>' as a single background Bash call. See ci-monitoring.md." >&2
    exit 2
fi

exit 0
