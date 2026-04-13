#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher)
# After a git push: cancel stale CI runs, then output MANDATORY monitoring instruction.

INPUT="${TOOL_INPUT:-}"

# Only act on git push commands
if ! echo "$INPUT" | grep -qE 'git\s+push'; then
    exit 0
fi

# Must be in a git repo with gh CLI and GitHub remote
git rev-parse --is-inside-work-tree &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0
gh repo view --json name &>/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
[ -z "$BRANCH" ] && exit 0

# Find active runs on this branch
IN_PROGRESS=$(gh run list --branch "$BRANCH" --status in_progress --json databaseId --jq '.[].databaseId' 2>/dev/null || echo "")
QUEUED=$(gh run list --branch "$BRANCH" --status queued --json databaseId --jq '.[].databaseId' 2>/dev/null || echo "")
ALL_RUNS=$(echo -e "${IN_PROGRESS}\n${QUEUED}" | grep -v '^$' | sort -rn | uniq)

[ -z "$ALL_RUNS" ] && exit 0

LATEST=$(echo "$ALL_RUNS" | head -1)
RUN_COUNT=$(echo "$ALL_RUNS" | wc -l)

# Cancel stale runs (keep only the latest)
if [ "$RUN_COUNT" -gt 1 ]; then
    STALE=$(echo "$ALL_RUNS" | tail -n +2)
    CANCELLED=0
    for RUN_ID in $STALE; do
        gh run cancel "$RUN_ID" 2>/dev/null && CANCELLED=$((CANCELLED + 1))
    done
    [ "$CANCELLED" -gt 0 ] && echo "CI: Cancelled ${CANCELLED} stale run(s) on ${BRANCH}."
fi

# MANDATORY CI MONITORING INSTRUCTION
# This output goes to Claude as PostToolUse feedback. It is not a suggestion.
cat <<MONITOR

⚠️ MANDATORY: You just pushed to ${BRANCH}. You MUST now:
1. Run a background command: sleep 300 && gh run view ${LATEST} --json status,conclusion,jobs --jq '{status,conclusion,jobs:[.jobs[]|{name,status,conclusion}]}'
2. Do NOT start any new task, brainstorm, or issue selection until CI reaches terminal state.
3. Do NOT send a completion report until CI is green.
4. If CI fails: gh run view ${LATEST} --log-failed — investigate and fix immediately.

Run #${LATEST} on ${BRANCH} — MONITOR THIS NOW.
MONITOR
