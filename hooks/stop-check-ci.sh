#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# BLOCKS Claude from stopping when CI is running/red or work is incomplete.
# Exit code 2 = block the stop, force Claude to keep working.

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

SHOULD_BLOCK=0
WARNINGS=""

# Check for in-progress CI runs on this branch
IN_PROGRESS=$(gh run list --branch "$BRANCH" --status in_progress --json databaseId --jq 'length' 2>/dev/null || echo "0")
QUEUED=$(gh run list --branch "$BRANCH" --status queued --json databaseId --jq 'length' 2>/dev/null || echo "0")

if [ "$IN_PROGRESS" -gt 0 ] || [ "$QUEUED" -gt 0 ]; then
    TOTAL=$((IN_PROGRESS + QUEUED))
    WARNINGS="${WARNINGS}\n🚫 CI STILL RUNNING: ${TOTAL} run(s) on ${BRANCH}. You MUST monitor until complete."
    SHOULD_BLOCK=1
fi

# Check latest run status
LATEST_CONCLUSION=$(gh run list --branch "$BRANCH" --limit 1 --json conclusion --jq '.[0].conclusion // "none"' 2>/dev/null || echo "unknown")

if [ "$LATEST_CONCLUSION" = "failure" ]; then
    WARNINGS="${WARNINGS}\n🚫 CI IS RED: Latest run on ${BRANCH} failed. Fix the failure before stopping."
    SHOULD_BLOCK=1
fi

# Check for uncommitted changes (warn, don't block)
DIRTY=$(git status --porcelain 2>/dev/null | head -5)
if [ -n "$DIRTY" ]; then
    WARNINGS="${WARNINGS}\n⚠️ UNCOMMITTED CHANGES: You have unstaged/uncommitted work."
fi

# Check for open PR not mergeable (warn, don't block)
OPEN_PR=$(gh pr list --head "$BRANCH" --state open --json number,mergeable --jq '.[0] // empty' 2>/dev/null || echo "")
if [ -n "$OPEN_PR" ]; then
    MERGEABLE=$(echo "$OPEN_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mergeable','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    if [ "$MERGEABLE" != "MERGEABLE" ]; then
        PR_NUM=$(echo "$OPEN_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('number','?'))" 2>/dev/null || echo "?")
        WARNINGS="${WARNINGS}\n⚠️ PR #${PR_NUM} IS NOT MERGEABLE: State=${MERGEABLE}."
    fi
fi

if [ -n "$WARNINGS" ]; then
    echo -e "\n🚨 STOP BLOCKED — INCOMPLETE WORK:${WARNINGS}"
    echo ""
    if [ "$SHOULD_BLOCK" -eq 1 ]; then
        echo "You cannot stop while CI is running or red. Monitor CI: gh run list --branch $BRANCH"
        exit 2
    fi
    echo "Unresolved issues found. Explain to the user what is blocking you."
fi

exit 0
