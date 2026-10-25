#!/usr/bin/env bash
set -euo pipefail
# Retired by #1137, kept one release for sessions started before 0.1.556 (they
# still call this Stop hook path; a missing file is exit 127 on every Stop).
# Never registered in settings/hooks.json; delete in 0.1.557.
exit 0
