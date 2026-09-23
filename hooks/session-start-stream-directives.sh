#!/usr/bin/env bash
set -euo pipefail

# Hook: SessionStart — stream directives from the BASE ref (issue 1127).
#
# A stream session (david1..4, montalu*, miva1) reads its project CLAUDE.md and
# `.claude/**` straight off the WORKING TREE. Streams park on long-lived
# feature branches, so a directive the project later writes to
# `.claude/streams/<unix-user>.md` on its BASE branch (odoo-erp issue 8078)
# never reaches a session whose branch predates it — and the #314 fetch hook
# cannot see anything wrong, the feature branch is "up to date" with its own
# remote twin.
#
# This step reads `<base>:.claude/streams/<user>.md` with git plumbing (no
# checkout) and, when the working-tree copy is ABSENT or DIFFERS, prints the
# base copy. Plain stdout of a SessionStart hook is added to the model's
# context — the same channel session-start-fetch.sh's own lines use, so on the
# startup path (where both share one stdout) the output stays one valid
# contract. It also prints ONE WARNING line when the branch forked from the
# base more than 7 days ago AND the base has moved on since.
#
# Base ref: `upstream/<default>` when an `upstream` remote exists (<default> =
# the upstream/HEAD symref target, else origin/HEAD's branch name), else
# `origin/HEAD`'s target. Only the base branch is fetched (a remote-tracking
# ref update), skipped when the caller already fetched that remote.
#
# Wiring (settings/hooks.json):
#   * startup — invoked by session-start-fetch.sh's EXIT trap, AFTER its
#     `git fetch origin` (hooks of one event run in PARALLEL, so a sibling
#     registration would race that fetch). The trap sets
#     AIRULESET_STREAM_BASE_FETCHED=origin.
#   * compact — registered standalone, so a long-lived session picks up a
#     directive written since it booted.
#
# Contract: NEVER modifies the working tree, the index or HEAD, and NEVER
# blocks a session start — every failure prints nothing and exits 0. Output is
# composed first and printed only once complete.

STALE_SECONDS=$((7 * 86400))
MAX_CHARS=12000

# Drain the SessionStart payload when run standalone (compact); nothing here
# needs it. On the startup path the caller passes </dev/null.
if [ ! -t 0 ]; then
    cat >/dev/null 2>&1 || true
fi

_git_q() {
    GIT_TERMINAL_PROMPT=0 git "$@" 2>/dev/null
}

# Echo "<remote> <branch>" for a candidate base, or nothing.
_candidate() {
    local remote="$1" branch="$2"
    [ -n "$remote" ] && [ -n "$branch" ] || return 0
    if [ "$remote" != "${AIRULESET_STREAM_BASE_FETCHED:-}" ]; then
        _git_q fetch --quiet --no-tags "$remote" "$branch" >/dev/null || true
    fi
    if _git_q rev-parse --verify --quiet "refs/remotes/$remote/$branch" >/dev/null; then
        echo "$remote $branch"
    fi
}

_compose() {
    _git_q rev-parse --is-inside-work-tree >/dev/null || return 0
    local top user rel sym origin_default="" up_default="" pick="" base
    top=$(_git_q rev-parse --show-toplevel) || return 0
    user=$(id -un 2>/dev/null) || return 0
    case "$user" in "" | */* | .*) return 0 ;; esac
    rel=".claude/streams/$user.md"

    if sym=$(_git_q symbolic-ref --quiet --short refs/remotes/origin/HEAD); then
        origin_default=${sym#origin/}
    fi
    if _git_q remote get-url upstream >/dev/null; then
        if sym=$(_git_q symbolic-ref --quiet --short refs/remotes/upstream/HEAD); then
            up_default=${sym#upstream/}
        else
            up_default=$origin_default
        fi
        pick=$(_candidate upstream "$up_default") || pick=""
    fi
    if [ -z "$pick" ]; then
        pick=$(_candidate origin "$origin_default") || pick=""
    fi
    [ -n "$pick" ] || return 0
    base="${pick% *}/${pick#* }"

    # No stream file for this account on the base -> not a stream session.
    local base_blob
    base_blob=$(_git_q rev-parse --verify --quiet "refs/remotes/$base:$rel") || return 0
    [ "$(_git_q cat-file -t "$base_blob")" = "blob" ] || return 0

    local here
    if here=$(_git_q symbolic-ref --quiet --short HEAD); then
        here="branch '$here'"
    else
        here="detached HEAD at $(_git_q rev-parse --short HEAD || echo '?')"
    fi

    local out="" wt="$top/$rel" wt_blob=""
    if [ -f "$wt" ] && [ ! -L "$wt" ]; then
        wt_blob=$(cd "$top" && _git_q hash-object -- "$rel") || wt_blob=""
    fi
    if [ "$wt_blob" != "$base_blob" ]; then
        local state="is ABSENT from" content
        [ -e "$wt" ] && state="DIFFERS in"
        content=$(_git_q cat-file blob "$base_blob") || return 0
        if [ "${#content}" -gt "$MAX_CHARS" ]; then
            content="${content:0:$MAX_CHARS}
[... truncated — read the full file with: git show $base:$rel]"
        fi
        out="Stream directives from $base (your checkout is on $here): $rel $state your working tree, so the current version from $base is delivered here. These are this stream's binding project directives (source: git show $base:$rel):

$content"
    fi

    # Staleness: only when the base has commits the checkout lacks.
    local mb ct now age behind
    if mb=$(_git_q merge-base HEAD "refs/remotes/$base") &&
        ! _git_q merge-base --is-ancestor "refs/remotes/$base" HEAD; then
        ct=$(_git_q log -1 --format=%ct "$mb") || ct=""
        now=$(date +%s)
        if [ -n "$ct" ] && [ $((now - ct)) -gt "$STALE_SECONDS" ]; then
            age=$(((now - ct) / 86400))
            behind=$(_git_q rev-list --count "HEAD..refs/remotes/$base") || behind="?"
            local warn="WARNING: $here forked from $base $age days ago (merge-base $(_git_q rev-parse --short "$mb" || echo '?')) and $base has $behind newer commit(s) — project rules added there since (CLAUDE.md, .claude/**) are NOT in your working tree; merge $base into your branch to pick them up."
            if [ -n "$out" ]; then
                out="$out

$warn"
            else
                out="$warn"
            fi
        fi
    fi

    printf '%s' "$out"
}

OUT=$(_compose) || OUT=""
if [ -n "$OUT" ]; then
    printf '%s\n' "$OUT"
fi
exit 0
