#!/usr/bin/env bash
set -euo pipefail
# Stop hook stub: retired by #1137; kept for sessions started before 0.1.558
# (they still call this path; a missing file is exit 127 on every Stop). The
# playbook proof now lives in stop-check-prose-violations.sh. Never registered
# in settings/hooks.json.
exit 0
