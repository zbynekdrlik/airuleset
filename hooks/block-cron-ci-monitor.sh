#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (CronCreate)
# BLOCKS all CronCreate usage. /loop and cron are disabled via
# CLAUDE_CODE_DISABLE_CRON=1 but resumed sessions may not have it.
# This hook is the backup enforcement.

echo "BLOCKED: CronCreate and /loop are disabled. Use 'sleep N && gh run view <id>' for CI monitoring. See ci-monitoring.md." >&2
exit 2
