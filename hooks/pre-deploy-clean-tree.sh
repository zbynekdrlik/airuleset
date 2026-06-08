#!/usr/bin/env bash
set -euo pipefail
set -f  # disable globbing — token classification must never expand globs against cwd

# Hook: PreToolUse (Bash matcher)
# Blocks deploying a DIRTY git working tree to a remote target.
#
# Root cause it prevents: an uncommitted edit (even an accidental file-write
# revert) gets rsync'd/scp'd straight to production, diverging the live bytes
# from the committed HEAD with no review, no test, and no reflog trace.
# See modules/deploy/deploy-from-clean-tree.md.
#
# DESIGN — conservative / fail-closed. Distinguishing a push (local->remote,
# dangerous) from a pull (remote->local, safe) by parsing arbitrary shell is
# fragile: trailing redirects (`2>&1`, `>log`), comments, flag values, and
# wrapper words all defeat positional heuristics, every one in the fail-OPEN
# direction. So this hook does NOT guess direction. If a transfer command
# (rsync/scp/sftp, incl. via sshpass, and rsync:// URLs) names ANY remote
# endpoint while the tree is dirty, it BLOCKS. A wrongly-blocked pull or
# remote-to-remote copy is one bypass token of friction; a missed dirty push
# is the production incident this exists to prevent.
#
# Allowed even when dirty: --dry-run / -n (transfers nothing).
#
# KNOWN GAPS (the rule covers more than the hook enforces):
#   - Streaming pushes (`tar c … | ssh host "tar x"`, `cat f | ssh host "cat >…"`)
#     and `docker build` of the working dir are NOT detected here.
#   - sftp batch/bare-host uploads (`sftp -b batch host`, `sftp host`) carry no
#     `host:path` token, so only sftp forms with an explicit `host:path` block.
#   The rule module is the agent-facing guidance for these; this hook enforces
#   the argv-detectable rsync/scp/sftp/sshpass remote-endpoint subset.
#
# Bypass: AIRULESET_ALLOW_DIRTY_DEPLOY=1, or '# airuleset:deploy-dirty-ok' in
# the command (for genuine non-repo / intentional-dirty transfers only).
#
# Exit code 2 = block the tool call.

INPUT=$(cat 2>/dev/null || echo "")

# Extract the command. Prefer jq; fall back to python3 so a MISSING jq does
# not silently disable the guard (fail-active, not fail-open). python3 is the
# airuleset runtime — always present.
if command -v jq >/dev/null 2>&1; then
    CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
else
    CMD=$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("tool_input", {}).get("command", "") or "")
except Exception:
    pass' 2>/dev/null || echo "")
fi
[ -z "$CMD" ] && exit 0

# Explicit bypasses
if echo "$CMD" | grep -q 'airuleset:deploy-dirty-ok'; then exit 0; fi
if [ "${AIRULESET_ALLOW_DIRTY_DEPLOY:-}" = "1" ]; then exit 0; fi

# Only relevant inside a git work tree
git rev-parse --is-inside-work-tree &>/dev/null || exit 0

# A token is a REMOTE endpoint if it looks like [user@]host:path or an rsync://
# daemon URL, and is not some other URL scheme. Only NON-flag tokens are tested
# by the caller, so a colon inside a flag value (e.g. --exclude=a:b) is ignored.
is_remote_token() {
    local t="$1"
    case "$t" in
        rsync://*) return 0 ;;                          # rsync daemon dest — valid remote
        *://*) return 1 ;;                              # http://, ssh://, ftp:// — not an scp/rsync endpoint
    esac
    echo "$t" | grep -qE '^([A-Za-z0-9_.-]+@)?[A-Za-z0-9_.-]+:'
}

# First real command word of a segment, skipping wrappers and env-assignments
# so `sudo rsync`, `time rsync`, `VAR=ssh rsync`, and `(rsync` are seen as rsync.
first_real_word() {
    local w
    for w in $1; do
        w="${w#"("}"                                    # strip a leading '(' e.g. (rsync
        case "$w" in
            ""|sudo|time|nice|ionice|env|command|exec|builtin|\\) continue ;;
            *=*) continue ;;                            # VAR=value env assignment
            *) printf '%s\n' "$w"; return 0 ;;
        esac
    done
    return 1
}

# Walk command segments (split on shell separators). A segment is a TRANSFER if
# its command word is rsync/scp/sftp, or sshpass invoking one of those.
DEPLOY_TO_REMOTE=0
SEGMENTS=$(echo "$CMD" | sed -E 's/(\&\&|\|\||;|\|)/\n/g')
while IFS= read -r seg; do
    cmd=$(first_real_word "$seg" || true)
    case "$cmd" in
        rsync|scp|sftp) ;;
        sshpass) echo "$seg" | grep -qE '\b(scp|rsync|sftp)\b' || continue ;;
        *) continue ;;
    esac

    # --dry-run / -n transfers nothing — safe even on a dirty tree.
    if echo "$seg" | grep -qE '(^|[[:space:]])(--dry-run|-n)([[:space:]]|$)'; then
        continue
    fi

    # Any non-flag remote endpoint in the segment => a transfer touching a
    # remote. Block on dirty (we do NOT try to prove it's a push).
    for tok in $seg; do
        case "$tok" in -*) continue ;; esac
        if is_remote_token "$tok"; then
            DEPLOY_TO_REMOTE=1
            break
        fi
    done
    [ "$DEPLOY_TO_REMOTE" -eq 1 ] && break
done <<< "$SEGMENTS"

[ "$DEPLOY_TO_REMOTE" -eq 1 ] || exit 0

# Tracked-file dirtiness only (ignore untracked build artifacts like target/).
DIRTY=$(git status --porcelain --untracked-files=no 2>/dev/null || echo "")
[ -z "$DIRTY" ] && exit 0

REPO=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || echo '?')")
HEAD_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo '?')

echo "" >&2
echo "🚫 BLOCKED: Refusing a remote transfer from a DIRTY working tree." >&2
echo "" >&2
echo "  Repo: $REPO   HEAD: $HEAD_SHA" >&2
echo "  Uncommitted tracked changes that would ship UNREVIEWED on a push:" >&2
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
echo "  This guard is conservative: it also blocks pulls / remote-to-remote" >&2
echo "  copies while dirty (the safe direction). For a genuine non-push, or a" >&2
echo "  non-repo transfer, bypass with AIRULESET_ALLOW_DIRTY_DEPLOY=1 or add" >&2
echo "  '# airuleset:deploy-dirty-ok' to the command." >&2
echo "  See modules/deploy/deploy-from-clean-tree.md." >&2
echo "" >&2
exit 2
