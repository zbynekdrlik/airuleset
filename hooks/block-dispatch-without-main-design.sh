#!/usr/bin/env bash
set -euo pipefail
# PreToolUse stub: the dispatch-time design gate was retired by issue 1137 (B1);
# block-commit-without-design.sh keeps design-first. Kept for sessions started
# before the install (they still call this path; a missing file is exit 127 on
# every Agent call). Never registered in settings/hooks.json.
exit 0
