#!/usr/bin/env bash
# airuleset #1224 — PreToolUse(Bash): a session never reads its OWN transcript
# with a content reader. A 598 MB transcript loaded by `python3 -` + `grep -o`
# froze dev1 for 19 min; the question block it was after is printed by
# `airuleset.py question-block`. All logic lives in gates/owntranscript.py.
#
# Cheap by construction: python starts only when the command names this
# session's `<id>.jsonl` or `.claude/projects` at all (a bash substring
# pre-filter), so the other Bash calls pay a jq read and nothing else. Fail-OPEN on an unreadable payload: this is a
# resource guard, not a security boundary.
# Dry-run: echo '{"transcript_path":"/x/<sid>.jsonl","tool_input":{"command":"grep a /x/<sid>.jsonl"}}' | bash <this hook>
set -euo pipefail

INPUT=$(cat)
TP=$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)
SID=${TP##*/}
SID=${SID%.jsonl}
if [ -z "$CMD" ]; then exit 0; fi
# never the bare id: it is also in the scratchpad / task / tool-results paths
case "$CMD" in
    *"$SID.jsonl"*|*.claude/projects*) ;;
    *) exit 0 ;;
esac

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
RC=0
printf '%s' "$INPUT" | env PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -P -m gates.owntranscript || RC=$?
exit "$RC"
