#!/usr/bin/env bash
set -euo pipefail

# Hook: Stop
# WARNING-ONLY: tells Claude about CI status when it tries to stop.
# Does NOT block (exit 0) — blocking causes infinite loops because
# Claude can't run commands while stuck in a stop-block cycle.

# Must be in a git repo with gh CLI and GitHub remote
git rev-parse --is-inside-work-tree &>/dev/null || exit 0
command -v gh &>/dev/null || exit 0
gh repo view --json name &>/dev/null 2>&1 || exit 0

BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
[ -z "$BRANCH" ] && exit 0

# Get the latest run on this branch
LATEST=$(gh run list --branch "$BRANCH" --limit 1 --json databaseId,status,conclusion --jq '.[0] // empty' 2>/dev/null || echo "")
[ -z "$LATEST" ] && exit 0

STATUS=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
CONCLUSION=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('conclusion',''))" 2>/dev/null || echo "")
RUN_ID=$(echo "$LATEST" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('databaseId',''))" 2>/dev/null || echo "")

if [ "$STATUS" = "in_progress" ] || [ "$STATUS" = "queued" ]; then
    echo "WARNING: CI run #${RUN_ID} on ${BRANCH} is still ${STATUS}. You should monitor it before claiming done."
fi

if [ "$STATUS" = "completed" ] && [ "$CONCLUSION" = "failure" ]; then
    echo "WARNING: CI run #${RUN_ID} on ${BRANCH} FAILED. You should fix this before claiming done."
fi

exit 0
