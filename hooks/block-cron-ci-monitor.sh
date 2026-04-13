#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (CronCreate)
# BLOCKS using CronCreate for CI monitoring.
# CI must use: sleep N && gh run view <id> (background command)
# CronCreate creates gaps (30min+ between checks) and breaks Discord notifications.

INPUT=$(cat 2>/dev/null || echo "")

# Check if the cron prompt mentions CI, gh run, monitoring, deploy
if echo "$INPUT" | grep -qiE 'gh run|ci.*status|monitor.*ci|ci.*monitor|deploy.*status|run.*view|workflow.*status'; then
    echo "BLOCKED: Do NOT use CronCreate or /loop for CI monitoring. Use a single background command instead: sleep 300 && gh run view <run-id> --json status,conclusion,jobs. See ci-monitoring.md." >&2
    exit 2
fi

exit 0
