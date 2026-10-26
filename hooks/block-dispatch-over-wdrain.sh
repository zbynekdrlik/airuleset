#!/usr/bin/env bash
set -euo pipefail
# PreToolUse stub: the W-drain dispatch gate was retired by issue 1137 (B1);
# the W push stays in the watchdog job-20 nudge. Kept for sessions started
# before the install (they still call this path; a missing file is exit 127 on
# every Agent call). Never registered in settings/hooks.json.
exit 0
