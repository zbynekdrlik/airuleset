#!/usr/bin/env bash
set -euo pipefail

# Hook: SessionStart (startup matcher)
# Automatically runs git fetch on session start to ensure branch refs are current.

# Only run if we're in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Fetch latest from origin (suppress output to avoid noise)
if git remote get-url origin &>/dev/null; then
    git fetch origin --quiet 2>/dev/null || true

    # Check if current branch is behind remote
    BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
    if [ -n "$BRANCH" ] && git rev-parse "origin/$BRANCH" &>/dev/null; then
        BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo "0")
        if [ "$BEHIND" -gt 0 ]; then
            echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH"
        fi
    fi
fi
