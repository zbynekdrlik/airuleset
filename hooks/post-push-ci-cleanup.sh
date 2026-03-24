#!/usr/bin/env bash
set -euo pipefail

# Hook: PostToolUse (Bash matcher)
# After a git push, cancels stale CI runs on the same branch, keeping only the latest.

# TOOL_INPUT contains the command that was executed
INPUT="${TOOL_INPUT:-}"

# Only act on git push commands
if ! echo "$INPUT" | grep -qE 'git\s+push'; then
    exit 0
fi

# Must be in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Need gh CLI
if ! command -v gh &>/dev/null; then
    exit 0
fi

# Detect current branch
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
if [ -z "$BRANCH" ]; then
    exit 0
fi

# Check if we're in a GitHub repo
if ! gh repo view --json name &>/dev/null 2>&1; then
    exit 0
fi

# Find in-progress runs on this branch (sorted newest first by default)
IN_PROGRESS=$(gh run list --branch "$BRANCH" --status in_progress --json databaseId --jq '.[].databaseId' 2>/dev/null || echo "")
QUEUED=$(gh run list --branch "$BRANCH" --status queued --json databaseId --jq '.[].databaseId' 2>/dev/null || echo "")

# Combine and deduplicate
ALL_RUNS=$(echo -e "${IN_PROGRESS}\n${QUEUED}" | grep -v '^$' | sort -rn | uniq)

if [ -z "$ALL_RUNS" ]; then
    exit 0
fi

# Count runs
RUN_COUNT=$(echo "$ALL_RUNS" | wc -l)

if [ "$RUN_COUNT" -le 1 ]; then
    # Only one run — nothing to cancel
    LATEST=$(echo "$ALL_RUNS" | head -1)
    echo "CI: Run #${LATEST} is active on ${BRANCH}. Monitor it to completion."
    exit 0
fi

# Keep the newest (first line), cancel the rest
LATEST=$(echo "$ALL_RUNS" | head -1)
STALE=$(echo "$ALL_RUNS" | tail -n +2)
CANCELLED=0

for RUN_ID in $STALE; do
    if gh run cancel "$RUN_ID" 2>/dev/null; then
        CANCELLED=$((CANCELLED + 1))
    fi
done

if [ "$CANCELLED" -gt 0 ]; then
    echo "CI: Cancelled ${CANCELLED} stale run(s) on ${BRANCH}. Monitor run #${LATEST} (latest)."
else
    echo "CI: Run #${LATEST} is active on ${BRANCH}. Monitor it to completion."
fi
