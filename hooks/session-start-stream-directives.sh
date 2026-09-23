#!/usr/bin/env bash
set -euo pipefail

# Hook: SessionStart — stream directives from the BASE ref (issue 1127).
#
# A stream session (david1..4, montalu*, miva1) reads its project CLAUDE.md and
# `.claude/**` straight off the WORKING TREE. Streams park on long-lived
# feature branches, so a directive the project later writes to
# `.claude/streams/<unix-user>.md` on its integration branch (odoo-erp issue
# 8078, written on `develop`) never reaches a session whose branch predates it
# — and the #314 fetch hook sees nothing wrong, the feature branch is "up to
# date" with its own remote twin.
#
# This step reads `<base>:.claude/streams/<user>.md` with git plumbing (no
# checkout) and prints it when the base's last change to that file is NOT in
# HEAD's history and the working-tree copy is absent or differs (a NEWER copy
# on the branch is never "corrected" with an older one). It also prints ONE
# WARNING line when the branch forked from the base more than 7 days ago AND
# the base has moved on. Plain stdout of a SessionStart hook is added to the
# model's context — the channel session-start-fetch.sh's own lines use, so the
# startup path (both share one stdout) keeps one contract. Claude Code shows
# hook output above ~10000 chars only as a file preview, so the delivered file
# is capped at MAX_BYTES (UTF-8 safe).
#
# Base ref: remote = `upstream` when that remote is configured (then NEVER the
# fork's origin — silent instead), else `origin`; branch = the first of
# develop, dev, <remote>/HEAD's target, main, master that exists locally, is a
# stream project, and still exists on the remote (odoo-erp's default is
# `main`, but its directives live on `develop`; a stale unpruned
# `origin/develop` in a dev/main repo is skipped).
#
# Cost: non-stream repos pay a handful of local ref lookups and NO fetch — the
# fetch happens only when the candidate, HEAD or origin's twin already carries
# `.claude/streams/`. It goes into a PRIVATE ref (refs/airuleset/stream-base/…,
# visible to `git push --mirror`/`log --all` like any ref) with no FETCH_HEAD,
# no auto-maintenance, no submodule recursion and no remote-tracking update,
# so it never races the session's own `git fetch`/`git pull`; it is
# time-bounded and ssh runs in BatchMode (unless the user configured their own
# ssh command). It is skipped when the caller already fetched that remote.
#
# Wiring (settings/hooks.json):
#   * startup — invoked by session-start-fetch.sh's EXIT trap, AFTER its
#     `git fetch origin` (hooks of one event run in PARALLEL, so a sibling
#     registration would race that fetch); the trap passes
#     AIRULESET_STREAM_BASE_FETCHED=origin.
#   * compact / resume / clear — registered standalone, so a long-lived,
#     resumed or cleared session picks up a directive written since boot.
#
# Contract: NEVER modifies the working tree, the index, HEAD, the user's
# remote-tracking refs or FETCH_HEAD, and NEVER blocks a session start — every
# failure prints nothing and exits 0. Output is composed first and printed only
# once complete.

STALE_SECONDS=$((7 * 86400))
MAX_BYTES=8000
FETCH_TIMEOUT=10
FETCH_HEAD_FRESH_SECONDS=300
PRIVATE_NS="refs/airuleset/stream-base"

# Drain the SessionStart payload when run standalone; nothing here needs it.
# On the startup path the caller passes </dev/null.
if [ ! -t 0 ]; then
    cat >/dev/null 2>&1 || true
fi

_git_q() {
    GIT_TERMINAL_PROMPT=0 git "$@" 2>/dev/null
}

_ref_exists() {
    _git_q rev-parse --verify --quiet "$1" >/dev/null
}

# The ref to read for <remote>/<branch>: the remote-tracking ref, or the
# private ref of an earlier fetch of this step when that one is newer (or the
# only one). Echoes nothing if neither exists.
_local_ref() {
    local tracking="refs/remotes/$1/$2" private="$PRIVATE_NS/$1/$2"
    if _ref_exists "$tracking"; then
        if _ref_exists "$private" &&
            _git_q merge-base --is-ancestor "$tracking" "$private"; then
            echo "$private"
        else
            echo "$tracking"
        fi
    elif _ref_exists "$private"; then
        echo "$private"
    fi
}

# A stream-directive project: the candidate base, HEAD, or origin's twin of
# the candidate carries `.claude/streams/` (all local, no network).
_is_stream_project() {
    local ref="$1" branch="$2"
    _git_q cat-file -e "$ref:.claude/streams" ||
        _git_q cat-file -e "HEAD:.claude/streams" ||
        _git_q cat-file -e "refs/remotes/origin/$branch:.claude/streams"
}

# True when the caller's just-completed full fetch of this remote no longer
# lists <branch> (deleted upstream, the local tracking ref is stale).
_gone_per_fetch_head() {
    local branch="$1" fh now mt
    fh=$(_git_q rev-parse --git-path FETCH_HEAD) || return 1
    [ -s "$fh" ] || return 1
    now=$(date +%s)
    mt=$(stat -c %Y "$fh" 2>/dev/null) || return 1
    [ $((now - mt)) -le "$FETCH_HEAD_FRESH_SECONDS" ] || return 1
    grep -q "branch '" "$fh" 2>/dev/null || return 1
    ! grep -qF "branch '$branch' of " "$fh" 2>/dev/null
}

# Fetch <remote>/<branch> into the private ref. Echoes "ok", "missing" (the
# branch no longer exists on the remote) or "failed" (network, auth, timeout).
_fetch_private() {
    local remote="$1" branch="$2" dst="$PRIVATE_NS/$1/$2"
    local ssh_env=() runner=() err rc=0
    if [ -z "${GIT_SSH_COMMAND:-}" ] && [ -z "${GIT_SSH:-}" ] &&
        ! _git_q config --get core.sshCommand >/dev/null; then
        ssh_env=(GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5")
    fi
    if command -v timeout >/dev/null 2>&1; then
        runner=(timeout "$FETCH_TIMEOUT")
    fi
    err=$(env "${ssh_env[@]}" GIT_TERMINAL_PROMPT=0 LC_ALL=C "${runner[@]}" git fetch \
        --quiet --no-tags --no-write-fetch-head --no-auto-maintenance \
        --no-recurse-submodules --refmap= \
        "$remote" "+refs/heads/$branch:$dst" </dev/null 2>&1 >/dev/null) || rc=$?
    if [ "$rc" -eq 0 ]; then
        echo ok
    elif printf '%s' "$err" | grep -q "couldn't find remote ref"; then
        echo missing
    else
        echo failed
    fi
}

# Echo "<branch> <ref>" for the integration branch of <remote>, or nothing.
_resolve_base() {
    local remote="$1" head="" name ref status
    if head=$(_git_q symbolic-ref --quiet --short "refs/remotes/$remote/HEAD"); then
        head=${head#"$remote"/}
    fi
    for name in develop dev "$head" main master; do
        [ -n "$name" ] || continue
        ref=$(_local_ref "$remote" "$name") || ref=""
        [ -n "$ref" ] || continue
        _is_stream_project "$ref" "$name" || continue
        if [ "$remote" = "${AIRULESET_STREAM_BASE_FETCHED:-}" ]; then
            _gone_per_fetch_head "$name" && continue
            echo "$name $ref"
            return 0
        fi
        status=$(_fetch_private "$remote" "$name") || status=failed
        case "$status" in
            ok) echo "$name $PRIVATE_NS/$remote/$name" ;;
            missing) continue ;;
            *) echo "$name $ref" ;;
        esac
        return 0
    done
}

# Blob content, capped at MAX_BYTES on a UTF-8 boundary.
_blob_text() {
    local blob="$1" base="$2" rel="$3" size
    size=$(_git_q cat-file -s "$blob") || return 1
    if [ "$size" -le "$MAX_BYTES" ]; then
        _git_q cat-file blob "$blob"
        return
    fi
    _git_q cat-file blob "$blob" | python3 -c '
import sys
data = sys.stdin.buffer.read()[: int(sys.argv[1])]
sys.stdout.buffer.write(data.decode("utf-8", "ignore").encode("utf-8"))
' "$MAX_BYTES" || return 1
    printf '\n[... truncated — read the full file with: git show %s:%s]' "$base" "$rel"
}

_compose() {
    _git_q rev-parse --is-inside-work-tree >/dev/null || return 0
    local top user rel remote pick branch ref
    top=$(_git_q rev-parse --show-toplevel) || return 0
    # Paths below (pathspecs, hash-object, FETCH_HEAD) are relative to the
    # repo root, whatever subdirectory the session started in.
    cd "$top" || return 0
    user=$(id -un 2>/dev/null) || return 0
    case "$user" in "" | */* | .*) return 0 ;; esac
    rel=".claude/streams/$user.md"

    if _git_q remote get-url upstream >/dev/null; then
        remote=upstream
    elif _git_q remote get-url origin >/dev/null; then
        remote=origin
    else
        return 0
    fi
    pick=$(_resolve_base "$remote") || pick=""
    [ -n "$pick" ] || return 0
    branch=${pick%% *}
    ref=${pick#* }
    local base="$remote/$branch"

    # No stream file for this account on the base -> not this stream.
    local base_blob
    base_blob=$(_git_q rev-parse --verify --quiet "$ref:$rel") || return 0
    [ "$(_git_q cat-file -t "$base_blob")" = "blob" ] || return 0

    local here
    if here=$(_git_q symbolic-ref --quiet --short HEAD); then
        here="branch '$here'"
    else
        here="detached HEAD at $(_git_q rev-parse --short HEAD || echo '?')"
    fi

    local out="" wt_blob="" last=""
    if [ -f "$rel" ]; then
        wt_blob=$(_git_q hash-object -- "$rel") || wt_blob=""
    fi
    last=$(_git_q log -1 --format=%H "$ref" -- "$rel") || last=""
    if [ "$wt_blob" != "$base_blob" ] &&
        { [ -z "$last" ] || ! _git_q merge-base --is-ancestor "$last" HEAD; }; then
        local state="is ABSENT from" content
        [ -e "$rel" ] && state="DIFFERS in"
        content=$(_blob_text "$base_blob" "$base" "$rel") || return 0
        out="Stream directives from $base (your checkout is on $here): $rel $state your working tree and $base has a newer version, delivered here. These are this stream's binding project directives (source: git show $base:$rel):

$content"
    fi

    # Staleness: only when the base has commits the checkout lacks.
    local mb ct now age behind short
    if mb=$(_git_q merge-base HEAD "$ref") &&
        ! _git_q merge-base --is-ancestor "$ref" HEAD; then
        ct=$(_git_q log -1 --format=%ct "$mb") || ct=""
        now=$(date +%s)
        if [ -n "$ct" ] && [ $((now - ct)) -gt "$STALE_SECONDS" ]; then
            age=$(((now - ct) / 86400))
            behind=$(_git_q rev-list --count "HEAD..$ref") || behind="?"
            short=$(_git_q rev-parse --short "$mb") || short="?"
            local warn="WARNING: $here forked from $base $age days ago (merge-base $short) and $base has $behind newer commit(s) — project rules added there since (CLAUDE.md, .claude/**) are NOT in your working tree; merge $base into your branch to pick them up."
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
