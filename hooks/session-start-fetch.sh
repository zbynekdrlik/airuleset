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

# #486 G1 — register a structured session heartbeat at startup
# (~/.claude/session-status/<sid>.json), so the reader sees a session as soon
# as it boots — before the first turn ends. Reads the SessionStart payload from
# stdin (nothing else in this hook consumes stdin). Placed BEFORE the git checks
# so it fires regardless of whether the cwd is a git repo. Best-effort +
# non-blocking; no consumer yet (G1).
_HB_INPUT=$(cat 2>/dev/null || echo "")
_HB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
printf '%s' "$_HB_INPUT" | PYTHONPATH="$_HB_DIR" \
    python3 -m watchdog.session_status --event session_start >/dev/null 2>&1 || true

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

# Never touch a repo with an in-progress merge/rebase/cherry-pick/revert/
# bisect — HEAD may be detached mid-operation, so this check runs BEFORE
# (and independently of) the branch lookup below. `sequencer` covers a
# multi-commit cherry-pick/revert run; REVERT_HEAD is CHERRY_PICK_HEAD's
# exact twin for `git revert` (#314 adversarial review F3).
GIT_DIR=$(git rev-parse --git-dir 2>/dev/null || echo "")
if [ -n "$GIT_DIR" ]; then
    for state in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG rebase-apply rebase-merge sequencer; do
        if [ -e "$GIT_DIR/$state" ]; then
            echo "WARNING: repository has an in-progress git operation (merge/rebase/cherry-pick/revert/bisect) — leaving it untouched"
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
# or not) must never be silently moved. An UNMEASURABLE status (the command
# itself failed, e.g. an unreadable .git/index) is treated the same as
# dirty — never guess "clean" when the check couldn't even run (#314
# adversarial review F4: `set -e` note — this assignment MUST be guarded
# with `||`, or a failing command substitution here would exit the whole
# script immediately instead of reaching this check at all).
STATUS_OUT=$(git status --porcelain 2>/dev/null) || STATUS_OUT="__UNMEASURABLE__"
if [ "$STATUS_OUT" = "__UNMEASURABLE__" ]; then
    echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH (could not determine working tree state — not fast-forwarding)"
    exit 0
fi
if [ -n "$STATUS_OUT" ]; then
    echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH (working tree dirty — not fast-forwarding)"
    exit 0
fi

# Only a GENUINE fast-forward is safe: HEAD must be an ancestor of
# origin/$BRANCH. Anything else is a real divergence — never touched.
if ! git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null; then
    echo "WARNING: Branch '$BRANCH' has diverged from origin/$BRANCH ($BEHIND commit(s) behind) — not fast-forwarding"
    exit 0
fi

# Never fast-forward when origin ADDS a path that already exists on disk —
# an ignored or untracked local file sharing that path would be silently
# CLOBBERED by `--ff-only` (git treats an ignored/untracked file as
# expendable relative to an incoming tracked file at the same path). The
# dirty-tree check above cannot see this at all: `git status --porcelain`
# never lists ignored files, and a plain untracked file only collides here
# when origin is about to introduce that exact path (#314 adversarial
# review F1 — a live, reproduced data-loss finding).
COLLIDE=""
while IFS= read -r p; do
    if [ -n "$p" ] && [ -e "$p" ]; then
        COLLIDE="$COLLIDE $p"
    fi
done < <(git diff --name-only --diff-filter=A HEAD "origin/$BRANCH" 2>/dev/null)
if [ -n "$COLLIDE" ]; then
    echo "WARNING: Branch '$BRANCH' is $BEHIND commit(s) behind origin/$BRANCH (origin adds file(s) that already exist locally:$COLLIDE — not fast-forwarding)"
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
