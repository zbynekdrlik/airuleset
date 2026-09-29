#!/usr/bin/env bash
# Sourced by hooks/block-fork-no-merge-issue-close.sh — the body of its
# #627/#891/#1185 Discuss/acceptance close gate (split out at #1185 to keep the
# hook under ~1000 lines; not executed on its own). Inherits the hook's
# `set -euo pipefail`, $CMD, $_DREPO, the segmenter's D_* signals and
# _repo_owner_repo_of; `exit 2` here exits the hook (a PreToolUse block).
# The design + fail-direction notes stay in the hook's section header.

# #1185: verify an owner `issuecomment-<id>` citation ONLINE (the guard prints
# `OWNER-CHECK <login> <repo|>:<id>...`). Prints nothing when ONE cited comment is
# the owner's; else the per-ref reasons (a fetch failure or another author).
_d_owner_check() {
    local _login _ref _repo _id _got _rc _why=""
    set -- $1; shift; _login="$1"; shift
    for _ref in "$@"; do
        _repo="${_ref%%:*}"; _id="${_ref##*:}"; [ -n "$_repo" ] || _repo="$_D_REPOFULL"
        _rc=0; _got=$(timeout 10 gh api "repos/$_repo/issues/comments/$_id" --jq .user.login 2>&1) || _rc=$?
        [ "$_rc" = 0 ] && [ "$_got" = "$_login" ] && return 0
        if [ "$_rc" != 0 ]; then _why+=" issuecomment-$_id could not be fetched (rc $_rc: ${_got:0:120});"
        else _why+=" issuecomment-$_id is authored by ${_got:-?}, not $_login;"; fi
    done
    printf '%s' "$_why"
}

# #1185: did the ticket EVER carry `needs-acceptance` (a removed label is no
# escape)? Prints one labeled-event count per page; rc != 0 on a failed read.
_d_label_history() {
    timeout 10 gh api "repos/$_D_REPOFULL/issues/$1/events" --paginate \
        --jq '[.[]|select(.event=="labeled" and .label.name=="needs-acceptance")]|length' 2>&1
}

# #837: EVERY clean top-level `gh issue close <N>` number (D_NUMS) + the first
# close segment's -R (D_REPO_ARG, GLUED-tolerant — `-Rx` reads `x`), both from the
# segmenter, which reads each close segment's own tokens quote/backslash-aware (a
# `gh issue close N` mentioned inside a comment value is not a real close; a
# `-R x/y` inside a quoted argument is never the repo). A compound batch-close of
# one thread's sibling tickets has EACH target in D_NUMS.
_D_NUMS="$D_NUMS"
_D_REPO_ARG="$D_REPO_ARG"
if [ -n "$_D_NUMS" ]; then
    # odoo-erp repo-scope (Odoo Discuss threads are an odoo-erp / client
    # thing): a non-odoo-erp close never engages the gate, killing the
    # cross-repo meta false-positive (e.g. this very airuleset ticket #627,
    # whose prose names these markers). Resolve the repo from _D_REPO_ARG
    # (this gate's OWN glued-tolerant -R extraction above), else the cwd git
    # remote, via the shared #760 _repo_owner_repo_of helper, then take the
    # BASENAME and compare case-insensitively.
    _D_REPOFULL=$(_repo_owner_repo_of "$_D_REPO_ARG")
    _D_REPONAME="${_D_REPOFULL##*/}"
    if [ "${_D_REPONAME,,}" = "odoo-erp" ]; then
        # Check EACH close target (numbers are pure digits — safe to word-split).
        # Block on the FIRST bound-no-disposition target found.
        _D_BLOCK_NUM=""
        _D_BLOCK_KIND=""
        for _D_NUM in $_D_NUMS; do
            # #1185: a `--reason "not planned"` close is never acceptance-checked.
            case " $D_NOTPLANNED " in *" $_D_NUM "*) continue ;; esac
            _D_JSON=""
            if [ -n "${AIRULESET_DISCUSS_CLOSE_FIXTURE:-}" ] && [ -f "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" ]; then
                _D_JSON=$(cat "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" 2>/dev/null || echo "")
            elif [ -n "$_D_REPO_ARG" ]; then
                _D_JSON=$(gh issue view "$_D_NUM" -R "$_D_REPO_ARG" --json body,comments,labels 2>/dev/null || echo "")
            else
                _D_JSON=$(gh issue view "$_D_NUM" --json body,comments,labels 2>/dev/null || echo "")
            fi
            if [ -n "$_D_JSON" ]; then
                _D_VERDICT=$(printf '%s' "$_D_JSON" | python3 "$_DREPO/discuss_close_guard.py" --report-unbound 2>/dev/null || echo "OK")
                if [ "$_D_VERDICT" = "UNBOUND" ]; then
                    # #1185: not bound by a thread / the current label → read the label
                    # history; a ticket that ever carried needs-acceptance is bound.
                    _D_RC=0; _D_HIST=$(_d_label_history "$_D_NUM") || _D_RC=$?
                    if [ "$_D_RC" != 0 ] || ! [[ "$_D_HIST" =~ ^[0-9[:space:]]+$ ]]; then
                        _D_HIST_WHY="rc $_D_RC: ${_D_HIST:0:120}"
                        _D_BLOCK_NUM="$_D_NUM"; _D_BLOCK_KIND="HISTORY"; break
                    fi
                    _D_SUM=0; for _c in $_D_HIST; do _D_SUM=$((_D_SUM + _c)); done
                    _D_VERDICT="OK"
                    if [ "$_D_SUM" -gt 0 ]; then
                        _D_VERDICT=$(printf '%s' "$_D_JSON" | python3 "$_DREPO/discuss_close_guard.py" --bound 2>/dev/null || echo "OK")
                    fi
                fi
                # #1185: BLOCK-CITED = citations without evidence; OWNER-CHECK =
                # only an owner issuecomment- cited → verify it online first.
                case "$_D_VERDICT" in
                    BLOCK|BLOCK-CITED) _D_BLOCK_NUM="$_D_NUM"; _D_BLOCK_KIND="$_D_VERDICT"; break ;;
                    "OWNER-CHECK "*)
                        _D_OWNER_WHY=$(_d_owner_check "$_D_VERDICT")
                        if [ -n "$_D_OWNER_WHY" ]; then
                            _D_BLOCK_NUM="$_D_NUM"; _D_BLOCK_KIND="OWNER"; break
                        fi ;;
                esac
            fi
        done
        if [ "$_D_BLOCK_KIND" = "HISTORY" ]; then
            cat >&2 <<MSG

🚫 BLOCKED (airuleset #1185): the needs-acceptance label history of #${_D_BLOCK_NUM}
could not be read (${_D_HIST_WHY}). A ticket that EVER carried needs-acceptance is
acceptance-bound (removing the label is no escape), so an unreadable history
cannot be allowed. Retry once GitHub reads recover, or use the logged bypass
airuleset:discuss-close-ok  in the close command.
MSG
            exit 2
        fi
        if [ "$_D_BLOCK_KIND" = "OWNER" ]; then
            cat >&2 <<MSG

🚫 BLOCKED (airuleset #1185): ticket ${_D_BLOCK_NUM} cites an owner ruling that
could not be verified —${_D_OWNER_WHY}
An Acceptance-cited: issuecomment-<id> counts only when that GitHub comment is
the owner's own ROZHODNUTÉ (checked online at close). Cite the owner's comment,
or another evidence form (msg <id> / meeting <recording id>). A GitHub read
failure: retry, or the logged bypass  airuleset:discuss-close-ok  in the close.
MSG
            exit 2
        fi
        if [ "$_D_BLOCK_KIND" = "BLOCK-CITED" ]; then
            cat >&2 <<MSG

🚫 BLOCKED: this ticket's Acceptance-cited: line carries no msg <id> (e.g. it
cites only a stage, "task in Hotovo") — and a Hotovo/Hotové a STREAM account
set is never acceptance evidence (airuleset #1185): the stream cannot be its
own acceptance; a message or stage move a STREAM account authored is not
acceptance either.

Cite the Odoo message that IS the acceptance, on the Acceptance-cited line:
  • a client message or reaction, or the owner's/client's own stage move (its
    chatter tracking message) — name its author:
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-cited: msg <message-id> task <task-id>"
  • the owner accepted on the client's behalf (an owner ROZHODNUTÉ comment):
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-cited: owner ROZHODNUTÉ issuecomment-<id>"
  • the client confirmed in a RECORDED meeting (name the recording):
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-cited: meeting <recording id> [mm:ss] <who>"
    (the spelling "nahrávka <recording id>" counts the same; the id is mandatory)
  • the client confirmed on Discord (the message's own URL, all digits):
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-cited: https://discord.com/channels/<guild>/<channel>/<message>"
  • the odoo-erp#8507 auto-close (montalu): the full line from
    skills/odoo-client-messaging/client-board-stages.md rule 6, ending
    "msg <auto-close note id> task <task-id>".
A confirmation said only inside a webterm/Claude session is not evidence: ask
the client to confirm in a durable channel (a 👍/message on the Odoo task, or
Discord) and cite that; a stream-bot comment or a payment event is never
acceptance. No such message yet → the task is not accepted: leave the ticket
open (move the task back to the verification stage if a stream set Hotovo).

Then re-run the close.
MSG
            exit 2
        fi
        if [ -n "$_D_BLOCK_NUM" ]; then
            cat >&2 <<MSG

🚫 BLOCKED: this ticket is bound to client acceptance
(a Discuss-thread:/Acceptance-thread: line, a discuss.channel_<N> deep URL —
the URL alone binds, #695 — or, #1185, the needs-acceptance label) but it
carries no closing-note or acceptance evidence — closing it now would leave
the client thread with our message (or their question) as the LAST message,
then silence (airuleset #627/#891).

Whoever closes the ticket carries the obligation — it FOLLOWS THE TICKET to
its current owner, never the author. Before this ticket is closed, post a
closing note via the project's own client channel mechanism, then record the
evidence on THIS ticket. Add ONE of:

  • the client's acceptance is on record and the closing note was posted (this
    is the LAST ticket bound to the thread) — cite the ACCEPTANCE message:
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-cited: msg <message-id>"
    (legacy: Discuss-closed: msg <message-id> also accepted); the owner
    accepted on the client's behalf → "Acceptance-cited: owner ROZHODNUTÉ issuecomment-<id>"

  • the thread STAYS OPEN because sibling tickets remain (the closing note goes
    at the LAST close, not here — name the still-open siblings):
      gh issue comment ${_D_BLOCK_NUM} --body "Acceptance-defer: siblings #<A> #<B> still open"
    (legacy: Discuss-defer: also accepted)

Then re-run the close.

Both paths:
  • a sub-dev closing its own ticket: YOU post the note + record the line + close.
  • the gatekeeper closing a branch-merge ticket after the release pipeline: the
    OWNING stream posts the note + records Discuss-closed: at hand-off; the
    gatekeeper's close then finds the evidence. The gatekeeper does NOT post to
    the client thread — the stream that owns the thread does.

How to compose + post the closing note (identity signature, owner approval,
per project channel — odoo-erp: task chatter per .claude/rules/odoo-task-sync.md):
skills/odoo-client-messaging/handover-compose.md.

Bypass (rare, logged, ONLY a genuine non-client / meta ticket that merely names
these markers in prose): put  airuleset:discuss-close-ok  in the close command.
MSG
            exit 2
        fi
    fi
fi
