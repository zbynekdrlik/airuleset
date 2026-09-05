#!/usr/bin/env bash
# nudge-module-context-cost.sh — PreToolUse(Write|Edit) on modules/**
# Prints current always-on bytes + ceiling + headroom. Exit 0 ALWAYS (#859 A4).
# NON-BLOCKING: informational only, never prevents a write.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RATCHET="$REPO_DIR/tests/context_ratchet.json"

# Only fire on modules/ paths — read .tool_input.file_path (the real
# PreToolUse payload shape, cf. block-main-implementation.sh:472).
INPUT="$(cat)"
FILE_PATH="$(echo "$INPUT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || true)"

case "$FILE_PATH" in
  */modules/*) ;;
  *) exit 0 ;;
esac

# Read ratchet ceiling
if [ ! -f "$RATCHET" ]; then
  exit 0
fi

CEILING="$(python3 -c "import json;print(json.load(open('$RATCHET'))['ceilings'].get('modules_resolved_bytes',0))" 2>/dev/null || echo 0)"
if [ "$CEILING" -eq 0 ] 2>/dev/null; then
  exit 0
fi

# Compute current resolved bytes via context-baseline --json
CURRENT="$(python3 "$REPO_DIR/airuleset.py" context-baseline --json 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin).get('global',{}).get('resolved_bytes',0))" 2>/dev/null || echo 0)"
if [ "$CURRENT" -eq 0 ] 2>/dev/null; then
  exit 0
fi

HEADROOM=$((CEILING - CURRENT))

echo "Context-cost: ${CURRENT} B / ${CEILING} B ceiling (headroom: ${HEADROOM} B) — editing $(basename "$FILE_PATH")" >&2
exit 0
