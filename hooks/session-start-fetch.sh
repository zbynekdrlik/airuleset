#!/usr/bin/env bash
set -euo pipefail

# Hook: SessionStart (startup matcher)
# Fetches origin at session start and, WHEN PROVABLY SAFE, fast-forwards the
# local branch to match — so a new session's CLAUDE.md / project files (read
# straight off the working tree at boot) never sit stale behind origin just
# because nobody happened to `git pull` on this particular checkout (#314).
# Fast-forward ONLY — never `reset --hard`, never `checkout -f`, never any
# history rewrite. Any unsafe state (dirty tree, an in-progress git
# operation, a genuinely diverged branch, detached HEAD) is left completely
# untouched and only reported via a WARNING line.

# Only run if we're in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
    exit 0
fi

# Only run if origin is configured
if ! git remote get-url origin &>/dev/null; then
    exit 0
fi

# Fetch latest from origin (suppress output to avoid noise)
git fetch origin --quiet 2>/dev/null || true

# Never touch a repo with an in-progress merge/rebase/cherry-pick/bisect —
# HEAD may be detached mid-operation, so this check runs BEFORE (and
# independently of) the branch lookup below.
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || echo "")
if [ -n "$GIT_DIR" ]; then
    for state in MERGE_HEAD CHERRY_PICK_HEAD BISECT_LOG rebase-apply rebase-merge; do
        if [ -e "$GIT_DIR/$state" ]; then
            echo "WARNING: repository has an in-progress git operation (merge/rebase/cherry-pick/bisect) — leaving it untouched"
            exit 0
        fi
    done
fi

# Check if current branch is behind remote
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
if [ -z "$BRANCH" ]; then
    # detached HEAD (not mid-operation, checked above) — nothing to compare/move
    exit 0
fi

if ! git rev-parse "origin/$BRANCH" &>/dev/null; then
    exit 0
fi

BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo "0")
if [ "$BEHIND" -le 0 ]; then
    exit 0
fi

# Never fast-forward a dirty tree — a checkout with uncommitted work (staged
# or not) must never be silently moved.
if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH (working tree dirty — not fast-forwarding)"
    exit 0
fi

# Only a GENUINE fast-forward is safe: HEAD must be an ancestor of
# origin/$BRANCH. Anything else is a real divergence — never touched.
if ! git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
    echo "WARNING: Branch '$BRANCH' has diverged from origin/$BRANCH ($BEHIND commit(s) behind) — not fast-forwarding"
    exit 0
fi

# `--ff-only` is itself a hard safety net: it can only ever move the ref
# forward along its own history and refuses (no-op on the tree) rather than
# doing anything destructive if this were somehow not a true fast-forward.
if git merge --ff-only "origin/$BRANCH" --quiet 2>/dev/null; then
    echo "Fast-forwarded '$BRANCH' to origin/$BRANCH ($BEHIND commit(s))"
else
    echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH (fast-forward attempt failed)"
fi
