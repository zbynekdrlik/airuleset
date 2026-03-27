#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# When Claude tries to stop, check if CI is green and work is complete.
# Outputs a warning message that Claude sees in its context if work appears incomplete.
# Does NOT block (exit 0) — but the warning forces Claude to address it.

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Need gh CLI
if ! command -v gh &>/dev/null; then
    exit 0
fi

# Must be a GitHub repo
if ! gh repo view --json name &>/dev/null 2>&1; then
    exit 0
fi

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
if [ -z "$BRANCH" ]; then
    exit 0
fi

# Check for uncommitted changes
DIRTY=$(git status --porcelain 2>/dev/null | head -5)

# Check for in-progress CI runs on this branch
IN_PROGRESS=$(gh run list --branch "$BRANCH" --status in_progress --json databaseId --jq 'length' 2>/dev/null || echo "0")
QUEUED=$(gh run list --branch "$BRANCH" --status queued --json databaseId --jq 'length' 2>/dev/null || echo "0")

# Check latest run status
LATEST_CONCLUSION=$(gh run list --branch "$BRANCH" --limit 1 --json conclusion --jq '.[0].conclusion // "none"' 2>/dev/null || echo "unknown")

# Check for open PR
OPEN_PR=$(gh pr list --head "$BRANCH" --state open --json number,mergeable --jq '.[0] // empty' 2>/dev/null || echo "")

WARNINGS=""

if [ -n "$DIRTY" ]; then
    WARNINGS="${WARNINGS}\n⚠️ UNCOMMITTED CHANGES: You have unstaged/uncommitted work. Did you forget to commit and push?"
fi

if [ "$IN_PROGRESS" -gt 0 ] || [ "$QUEUED" -gt 0 ]; then
    TOTAL=$((IN_PROGRESS + QUEUED))
    WARNINGS="${WARNINGS}\n⚠️ CI STILL RUNNING: ${TOTAL} run(s) in progress/queued on ${BRANCH}. You should monitor until complete."
fi

if [ "$LATEST_CONCLUSION" = "failure" ]; then
    WARNINGS="${WARNINGS}\n⚠️ CI IS RED: Latest run on ${BRANCH} failed. You have unfinished work — fix the failure before stopping."
fi

if [ -n "$OPEN_PR" ]; then
    MERGEABLE=$(echo "$OPEN_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mergeable','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    if [ "$MERGEABLE" != "MERGEABLE" ]; then
        PR_NUM=$(echo "$OPEN_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('number','?'))" 2>/dev/null || echo "?")
        WARNINGS="${WARNINGS}\n⚠️ PR #${PR_NUM} IS NOT MERGEABLE: State=${MERGEABLE}. Fix before stopping."
    fi
fi

if [ -n "$WARNINGS" ]; then
    echo -e "\n🚨 STOP CHECK — INCOMPLETE WORK DETECTED:${WARNINGS}"
    echo ""
    echo "If you cannot continue, explain to the user SPECIFICALLY what is blocking you and ask for guidance. Do not silently stop with unfinished work."
fi

exit 0
