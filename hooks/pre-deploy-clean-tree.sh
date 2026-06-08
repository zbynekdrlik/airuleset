#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash matcher)
# Blocks deploying a DIRTY git working tree to a remote target.
#
# Root cause it prevents: an uncommitted edit (even an accidental file-write
# revert) gets rsync'd/scp'd straight to production, diverging the live bytes
# from the committed HEAD with no review, no test, and no reflog trace.
# See modules/deploy/deploy-from-clean-tree.md.
#
# Fires when the command PUSHES local files to a remote (rsync/scp/sftp,
# incl. via sshpass) AND the current dir is a git work tree with tracked-file
# modifications. Pulls (remote -> local) are ignored.
#
# Bypass: AIRULESET_ALLOW_DIRTY_DEPLOY=1, or '# airuleset:deploy-dirty-ok' in
# the command (for genuine non-repo deploys only).
#
# Exit code 2 = block the tool call.

INPUT=$(cat 2>/dev/null || echo "")
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# Explicit bypasses
if echo "$CMD" | grep -q 'airuleset:deploy-dirty-ok'; then exit 0; fi
if [ "${AIRULESET_ALLOW_DIRTY_DEPLOY:-}" = "1" ]; then exit 0; fi

# Only relevant inside a git work tree
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# A token is a REMOTE target if it looks like [user@]host:path and is not a URL.
is_remote_token() {
    local t="$1"
    case "$t" in
        *://*) return 1 ;;                              # http://, ssh:// etc — not an scp dest
    esac
    echo "$t" | grep -qE '^([A-Za-z0-9_.-]+@)?[A-Za-z0-9_.-]+:'
}

# A token is a LOCAL path/name (no remote colon) — used to detect pull direction.
is_local_token() {
    local t="$1"
    case "$t" in
        -*) return 1 ;;                                 # a flag, ignore
    esac
    is_remote_token "$t" && return 1
    return 0
}

# Detect a PUSH-to-remote deploy. Split the command into segments on shell
# separators so chains like `rsync ... host:/p && ssh host restart` evaluate
# the rsync segment independently. Within a transfer segment, the remote
# DESTINATION must come after the source (push), not before it (pull).
DEPLOY_PUSH=0
SEGMENTS=$(echo "$CMD" | sed -E 's/(\&\&|\|\||;|\|)/\n/g')
while IFS= read -r seg; do
    # First meaningful word of the segment
    first=$(echo "$seg" | sed -E 's/^[[:space:]]+//' | awk '{print $1}')
    case "$first" in
        rsync|scp|sftp) ;;
        sshpass)
            echo "$seg" | grep -qE '\b(scp|rsync|sftp)\b' || continue ;;
        *) continue ;;
    esac

    # Walk tokens: find the last remote token and whether a local path follows it.
    last_remote_idx=-1
    local_after_remote=0
    idx=0
    for tok in $seg; do
        idx=$((idx + 1))
        if is_remote_token "$tok"; then
            last_remote_idx=$idx
            local_after_remote=0
        elif [ "$last_remote_idx" -ge 0 ] && is_local_token "$tok"; then
            local_after_remote=1
        fi
    done

    # Remote present AND it is the destination (no local path after it) => push.
    if [ "$last_remote_idx" -ge 0 ] && [ "$local_after_remote" -eq 0 ]; then
        DEPLOY_PUSH=1
        break
    fi
done <<< "$SEGMENTS"

[ "$DEPLOY_PUSH" -eq 1 ] || exit 0

# Tracked-file dirtiness only (ignore untracked build artifacts like target/).
DIRTY=$(git status --porcelain --untracked-files=no 2>/dev/null || echo "")
[ -z "$DIRTY" ] && exit 0

REPO=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || echo '?')")
HEAD_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo '?')

echo "" >&2
echo "🚫 BLOCKED: Refusing to deploy a DIRTY working tree to a remote target." >&2
echo "" >&2
echo "  Repo: $REPO   HEAD: $HEAD_SHA" >&2
echo "  Uncommitted tracked changes that would ship UNREVIEWED:" >&2
echo "$DIRTY" | head -20 | sed 's/^/    /' >&2
echo "" >&2
echo "  A deploy copies the WORKING TREE, not HEAD. These changes are not in" >&2
echo "  git — an accidental file-write revert ships straight to production." >&2
echo "" >&2
echo "  Fix: commit, revert, or stash these first, then deploy the COMMIT:" >&2
echo "    git status              # see what diverged" >&2
echo "    git diff                # confirm it is intentional" >&2
echo "    git add -A && git commit -m '...'   # or: git restore <file>" >&2
echo "  Then re-run the deploy and diff-verify the remote against HEAD." >&2
echo "" >&2
echo "  See modules/deploy/deploy-from-clean-tree.md." >&2
echo "  Genuine non-repo deploy? Bypass with AIRULESET_ALLOW_DIRTY_DEPLOY=1" >&2
echo "  or add '# airuleset:deploy-dirty-ok' to the command." >&2
echo "" >&2
exit 2
