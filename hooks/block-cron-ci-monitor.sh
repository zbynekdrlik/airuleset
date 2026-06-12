#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (CronCreate)
# BLOCKS CronCreate for CI monitoring. /loop for CI POLLING is banned; /loop as the
# autopilot-fleet SUPERVISOR is sanctioned per ci-monitoring.md (workers monitor
# their own CI with 'sleep N && gh run view'). This hook is the backup enforcement.

echo "BLOCKED: CronCreate and /loop for CI monitoring are disabled. Use 'sleep N && gh run view <id>' for CI monitoring. /loop as autopilot-fleet supervisor is sanctioned per ci-monitoring.md. See ci-monitoring.md." >&2
exit 2
