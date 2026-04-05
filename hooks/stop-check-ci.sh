#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# BLOCKS Claude from stopping when CI is actively running or the latest run failed.
# Exit code 2 + stderr message = block the stop.

# Must be in a git repo with gh CLI and GitHub remote
git rev-parse --is-inside-work-tree &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0
gh repo view --json name &>/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
[ -z "$BRANCH" ] && exit 0

# Get the latest run on this branch — this is the one that matters
LATEST=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId,status,conclusion --jq '.[0] // empty' 2>/dev/null || echo "")
[ -z "$LATEST" ] && exit 0

STATUS=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
CONCLUSION=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion',''))" 2>/dev/null || echo "")
RUN_ID=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('databaseId',''))" 2>/dev/null || echo "")

# Block if the LATEST run is still going
if [ "$STATUS" = "in_progress" ] || [ "$STATUS" = "queued" ]; then
    echo "STOP BLOCKED: Latest CI run #${RUN_ID} on ${BRANCH} is ${STATUS}. Monitor it: gh run view ${RUN_ID}" >&2
    exit 2
fi

# Block if the LATEST run failed
if [ "$STATUS" = "completed" ] && [ "$CONCLUSION" = "failure" ]; then
    echo "STOP BLOCKED: Latest CI run #${RUN_ID} on ${BRANCH} FAILED. Fix the failure: gh run view ${RUN_ID} --log-failed" >&2
    exit 2
fi

# Check for uncommitted changes (warn only, don't block)
DIRTY=$(git status --porcelain 2>/dev/null | head -3)
if [ -n "$DIRTY" ]; then
    echo "Warning: uncommitted changes on ${BRANCH}"
fi

exit 0
