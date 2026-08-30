#!/usr/bin/env bash
set -euo pipefail

# Hook: PreToolUse (Bash)
# Blocks `gh issue close` (and the equivalent `gh api ... PATCH state=closed`) when
# THIS stream's autopilot authority is REDUCED (fork-no-merge OR branch-merge) —
# UNLESS the issue being closed is the stream's OWN (self-authored). Exit 2 = block;
# Claude sees stderr.
#
# Semantics (refined by the gatekeeper, 2026-07-11; widened to branch-merge #349,
# 2026-08-09):
#   - ASSIGNED / foreign-authored tickets: NEVER closed by a reduced-authority
#     stream itself BEFORE gatekeeper review — self-closing one removes the
#     READY-FOR-REVIEW hand-off event and bypasses review. The CLOSER depends on
#     the repo: on odoo-erp, per owner ruling #5378, once the gatekeeper posts its
#     review-verdict comment and drops the queue label the DELIVERING STREAM closes
#     the ticket itself with an evidence --comment (the #756 verdict-artifact
#     carve-out below permits exactly that); on a repo without that ruling the
#     gatekeeper maintainer closes it (fork-no-merge at cross-fork review/merge,
#     branch-merge only AFTER the full `/process-subdev` release pipeline).
#   - SELF-AUTHORED sub-findings (tickets the stream itself filed while working,
#     e.g. kvaskodev-authored kiosk sub-issues): closing them WITH evidence is the
#     stream's normal bookkeeping — ALLOWED, for BOTH profiles. The 2026-07-10
#     "drift" suspicion was falsified: those ~10 closes were David's own
#     sub-findings, review was NOT bypassed (the hand-off tickets stayed open).
#   The check is mechanical: issue author == the stream's authenticated gh login.
#   Undeterminable (gh error, no auth) → fail-SAFE: block, with the hand-off recipe.
#
# Scope (#349): every REDUCED-authority stream is gated (authority != `full`) — this
# used to exempt `branch-merge` on the assumption its PR "legitimately closes issues
# ... via a merged PR's `Closes #N`", which is FALSE: a branch-merge PR merges into
# the project's INTEGRATION branch, never the repository's actual DEFAULT branch, so
# GitHub's `Closes #N` auto-close never fires there either. A live incident
# (montalu3, 2026-08-09) self-closed three merged tickets with no hand-off at all as
# a direct result. Only `full` authority legitimately closes issues itself —
# resolved per-stream via `airuleset.py authority` (marker-aware).

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
[ -z "$CMD" ] && exit 0

# #540 (was #533 review M3): a `gh api` PATCH to the ISSUE resource is a
# close-shaped WRITE. `state=closed` may be VISIBLE in argv (bare OR value-quoted
# — the ordinary `-f state="closed"` / `state='closed'` shell shape, #540 review
# FINDING 1), OR HIDDEN in the request BODY — a `--input <file|->` body, or a
# `=@file`-valued field — where the literal never appears in argv at all (the
# escape #540 exists for). Treat all of these as a close. It never READS the body
# (a `--input -` stdin body is fundamentally invisible to a PreToolUse argv
# scan; the `--input` TOKEN is the fail-safe marker instead). A visible non-close
# write (a `-f state=open`/`state="open"` reopen, a non-state field with no
# --input/@file) stays a non-close, so a `issues/N` mention inside a field VALUE
# on a NON-issue endpoint is NOT over-blocked. The GET/read predicate (`gh api
# …/issues/N --jq '.state=="closed"'`, no PATCH method) is excluded by the method
# requirement. The method separator class `[[:space:]=]` catches the GLUED
# `--method=PATCH` (#533 review M3) as well as `-X PATCH`. This covers the argv
# shapes a well-meaning stream uses; genuinely out-of-band close routes (a
# GraphQL `closeIssue` mutation, obfuscation) are documented residuals on the
# ticket, NOT claimed complete here (#540 review FINDING 1/3). Called ONLY from
# `if`/`elif` conditions below, so `set -e` is ignored inside the body — the
# `grep && return 0` chain never aborts the hook on a no-match.
_is_patch_close_cmd() {
    printf '%s' "$1" | grep -qE 'gh[[:space:]]+api[^|]*issues/[0-9]+' || return 1
    printf '%s' "$1" | grep -qE '(-X|--method)[[:space:]=]*[Pp][Aa][Tt][Cc][Hh]' || return 1
    printf '%s' "$1" | grep -qE 'state=["'\'']?closed' && return 0             # visible (bare or value-quoted, #540 F1)
    printf '%s' "$1" | grep -qE '(^|[[:space:]])--input([[:space:]=]|$)' && return 0  # hidden body (file/-/=file)
    printf '%s' "$1" | grep -qE '(-F|--field)[[:space:]=]+[^[:space:]]*=@' && return 0  # -F state=@file
    return 1
}

# #756: the gatekeeper REVIEW-VERDICT artifact — the SAME signal odoo-erp's
# `subdev-self-close-guard.yml` (#3784) keys on to prove a gk review happened
# (never WHO closed): a case-INSENSITIVE H1-H3 heading that STARTS (after
# non-word decoration) with "gatekeeper" and carries a verdict word
# (review|verification|verdict), OR a line-start GATEKEEPER-CLOSE: marker (#3712).
# The reopen-guard's jq test is `(^|\n)#{1,3}[^\w#\n]*gatekeeper[^\n]*\b(review|
# verification|verdict)\b`; here a PER-LINE grep makes its (^|\n) line-anchor a `^`
# and its [^\n]* stay within one line, so `.*` never spans lines. Requiring an
# actual #{1,3} heading (never #{0,3}) preserves the reopen-guard's self-exemption
# immunity — a bare "gatekeeper review ..." prose line with no leading hash never
# self-satisfies — and #{1,3} (never #{1,6}) mirrors the H1-H3 restriction, so an
# H4+ heading does not match. `[^[:alnum:]_#]` == the reopen-guard's `[^\w#]`
# decoration class (\w = alnum + underscore), so a "## Ready for gatekeeper review"
# readiness line stops at the word char "Ready" and never matches. Called ONLY from
# an `if` condition, so `set -e` is suspended for its whole body — a `grep && return
# 0` chain never aborts the hook on a no-match. It matches the SAME time-window-blind
# artifact the reopen-guard checks; the reopen-guard (POST-close, precise window) is
# the authoritative second net. A DELIBERATELY forged verdict heading is an accepted
# residual (this hook is a CONFUSION guard against a well-meaning agent, not
# adversarial security — the whole system keys on the ARTIFACT, never on WHO closed,
# per odoo-erp#5378).
_has_gk_verdict_artifact() {
    printf '%s\n' "$1" | grep -iqE '^#{1,3}[^[:alnum:]_#]*gatekeeper.*\b(review|verification|verdict)\b' && return 0
    printf '%s\n' "$1" | grep -qE '^GATEKEEPER-CLOSE:' && return 0
    return 1
}

# #540 (was #533 review M3): the `gh issue close` boundary class now includes the
# quote chars ' and " (widened SYMMETRICALLY on the opening AND closing anchor per
# the #471 lesson) so a close opening an interpreter's quoted argument
# (`bash -c 'gh issue close N'`, `sh -c "…"`, `eval "…"`) is a boundary match —
# otherwise is_close stays 0 and the WHOLE guard is bypassed at the top. Once
# is_close=1, #533's HAS_INTERP (below) blanks ISSUE_NUM so a nested close can
# never ride a carve-out. N_CLOSE (the single-action top-level count, below)
# DELIBERATELY keeps the narrow class — it counts TOP-LEVEL closes only, and a
# quoted close is nested, not top-level (handled by HAS_INTERP): the two greps
# answer different questions. The pre-existing fail-safe on a NON-executing quoted
# mention of the phrase (e.g. inside a `--body "…"` comment) is unchanged in kind,
# only slightly wider (the phrase at the very start of a quoted string now matches
# too) — the worker simply rephrases, far cheaper than missing a real close.
_CLOSE_OPEN='(^|[;&|[:space:]('\''"])'
_CLOSE_END='([[:space:]]|$|['\''");&|()])'

# Is this an issue-CLOSE action? Match `gh issue close` at a command boundary
# (quote-aware, #540), plus the REST-API PATCH close forms (`_is_patch_close_cmd`).
is_close=0
if printf '%s' "$CMD" | grep -qE "${_CLOSE_OPEN}gh[[:space:]]+issue[[:space:]]+close${_CLOSE_END}"; then
    is_close=1
elif _is_patch_close_cmd "$CMD"; then
    is_close=1
fi
[ "$is_close" -eq 0 ] && exit 0

# ---------------------------------------------------------------------------
# #627 Discuss closing-note gate — authority-INDEPENDENT, odoo-erp-scoped.
# Runs BEFORE the authority resolution below, so it fires for EVERY closing
# hand: a sub-dev closing its own ticket AND the gatekeeper (full authority)
# closing a branch-merge ticket after the release pipeline. The obligation to
# leave the sub-dev's closing note as the LAST message in a bound Odoo Discuss
# thread FOLLOWS THE TICKET to its CURRENT owner / the closing hand — NEVER the
# author of the thread or the ticket (owner correction, airuleset #627,
# 2026-08-22: tickets move between streams by topic, so binding the obligation
# to the author would park it on a stream that no longer owns the topic). This
# gate never reads authorship — only the ticket's own text — so a ticket that
# MOVED between streams is handled natively (the `Discuss-thread:` binding is a
# durable comment, orthogonal to the mutable `stream:` label, so it survives
# the move — #627 STEP-0).
#
# Recognition = a line-anchored `Discuss-thread: <channel-id>` marker on the
# ticket OR (#695) a `discuss.channel_<N>` deep-URL token in the ticket text
# (the #657-mandated form — so a ticket whose stream forgot the manual mark
# still binds, the montalu5 hole); the close is blocked until a
# `Discuss-closed: <msg-id>` (note posted, last ticket) OR
# `Discuss-defer: <reason>` (deferred to the last ticket) disposition is ALSO
# present. The pure decision is in discuss_close_guard.py (network-free,
# unit-tested); this shell part only detects the issue + repo, fetches the
# ticket text, and pipes it to the module.
#
# EVERY `gh issue close <N>` in the command is checked, not just the first — a
# compound that batch-closes the sibling tickets of one thread (the likely
# N-tickets-one-thread flow) would otherwise smuggle a bound, note-less close
# past a head-1 check (#627 review MAJOR).
#
# FAIL-OPEN throughout — the gate's DEFAULT is ALLOW (most closes are not
# thread-bound), so any unverifiable state (no python3, no `gh issue close`
# number, a gh error, a non-odoo-erp repo) FALLS THROUGH to the authority logic
# below, never a false block (the sibling authority guard's default is BLOCK, so
# it keeps its OWN safe default — each fails toward its own default). Documented
# residuals (#319): a `gh api ... PATCH ... state=closed` REST close is not
# detected here (the dominant `gh issue close N` form — including a COMPOUND of
# several — IS covered); a MIXED-repo compound (a different -R per close) is
# resolved against ONE repo (the command's first -R, else the cwd remote), so a
# bound odoo-erp close smuggled behind a non-odoo-erp -R falls through — the
# natural SAME-repo sibling batch is fully covered; a marker QUOTED in prose
# without a real posted note is a false-PASS (falsifiability + review, the #516
# model). Bypass (rare, logged): `airuleset:discuss-close-ok` anywhere in the
# command (a deliberate, self-opt-in escape hatch — the token appears nowhere
# else, so a close carrying it is unambiguously opting out). Test seam:
# AIRULESET_DISCUSS_CLOSE_FIXTURE=<file> supplies the ticket JSON for EVERY
# target instead of a live `gh issue view`.
# ---------------------------------------------------------------------------
_DHERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
_DREPO="$(dirname "$_DHERE")"
_d_run_gate=1
if printf '%s' "$CMD" | grep -q 'airuleset:discuss-close-ok'; then
    _DLOG="/tmp/airuleset-discuss-close-bypass-${EUID:-$(id -u)}.log"
    { echo "$(date -Iseconds)  discuss-close bypass" >> "$_DLOG"; } 2>/dev/null || true
    _d_run_gate=0
fi
command -v python3 >/dev/null 2>&1 || _d_run_gate=0
[ -f "$_DREPO/discuss_close_guard.py" ] || _d_run_gate=0

if [ "$_d_run_gate" = "1" ]; then
    # EVERY `gh issue close <N>` number in the command (not head -1) — a compound
    # batch-close of one thread's sibling tickets must have EACH target checked.
    _D_NUMS=$(printf '%s' "$CMD" | grep -oE 'gh[[:space:]]+issue[[:space:]]+close[[:space:]]+"?#?[0-9]+' | grep -oE '[0-9]+' || echo "")
    # Repo for the command: the first -R/--repo — separated (`-R x`), =-joined
    # (`-R=x`) OR glued (`-Rx`, the `*` after the class) — else the cwd remote.
    _D_REPO_ARG=$(printf '%s' "$CMD" | grep -oE "(-R|--repo)[[:space:]=]*[\"']?[A-Za-z0-9._/-]+" | head -1 | sed -E "s/^(-R|--repo)[[:space:]=]*[\"']?//" || echo "")
    if [ -n "$_D_NUMS" ]; then
        # odoo-erp repo-scope (Odoo Discuss threads are an odoo-erp / client
        # thing): a non-odoo-erp close never engages the gate, killing the
        # cross-repo meta false-positive (e.g. this very airuleset ticket #627,
        # whose prose names these markers). Resolve the repo from -R, else the
        # cwd git remote; basename, strip trailing slash + .git, compare
        # case-insensitively (the #477 _is_camera_box_repo parse order).
        _D_REPONAME="$_D_REPO_ARG"
        if [ -z "$_D_REPONAME" ]; then
            _D_REPONAME=$(git remote get-url origin 2>/dev/null || echo "")
        fi
        _D_REPONAME="${_D_REPONAME%/}"
        _D_REPONAME="${_D_REPONAME%.git}"
        _D_REPONAME="${_D_REPONAME%/}"
        _D_REPONAME="${_D_REPONAME##*/}"
        _D_REPONAME="${_D_REPONAME##*:}"
        if [ "${_D_REPONAME,,}" = "odoo-erp" ]; then
            # Check EACH close target (numbers are pure digits — safe to word-split).
            # Block on the FIRST bound-no-disposition target found.
            _D_BLOCK_NUM=""
            for _D_NUM in $_D_NUMS; do
                _D_JSON=""
                if [ -n "${AIRULESET_DISCUSS_CLOSE_FIXTURE:-}" ] && [ -f "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" ]; then
                    _D_JSON=$(cat "${AIRULESET_DISCUSS_CLOSE_FIXTURE}" 2>/dev/null || echo "")
                elif [ -n "$_D_REPO_ARG" ]; then
                    _D_JSON=$(gh issue view "$_D_NUM" -R "$_D_REPO_ARG" --json body,comments 2>/dev/null || echo "")
                else
                    _D_JSON=$(gh issue view "$_D_NUM" --json body,comments 2>/dev/null || echo "")
                fi
                if [ -n "$_D_JSON" ]; then
                    _D_VERDICT=$(printf '%s' "$_D_JSON" | python3 "$_DREPO/discuss_close_guard.py" 2>/dev/null || echo "OK")
                    if [ "$_D_VERDICT" = "BLOCK" ]; then
                        _D_BLOCK_NUM="$_D_NUM"
                        break
                    fi
                fi
            done
            if [ -n "$_D_BLOCK_NUM" ]; then
                cat >&2 <<MSG

🚫 BLOCKED: this ticket has a bound Odoo Discuss thread (a Discuss-thread: line
OR a discuss.channel_<N> deep URL on the ticket — the URL alone binds, #695)
but carries no closing-note evidence — closing it now would leave
the client thread with our message (or their question) as the LAST message, then
silence (airuleset #627, owner directive 2026-08-22).

Whoever closes the ticket carries the obligation — it FOLLOWS THE TICKET to its
current owner, never the author. Before this ticket is closed, the sub-dev that
CURRENTLY owns the thread must post a closing note INTO that Odoo Discuss thread
("všetko vyriešené, tému uzatvárame"), so the LAST message in the thread is
always from the sub-dev — then record the evidence on THIS ticket. Add ONE of:

  • the closing note was posted (this is the LAST ticket bound to the thread):
      gh issue comment ${_D_BLOCK_NUM} --body "Discuss-closed: msg <message-id>  (thread <channel-id>)"

  • the thread STAYS OPEN because sibling tickets remain (the closing note goes
    at the LAST close, not here — name the still-open siblings):
      gh issue comment ${_D_BLOCK_NUM} --body "Discuss-defer: siblings #<A> #<B> still open — note goes at the last close"

Then re-run the close.

Both paths:
  • a sub-dev closing its own ticket: YOU post the note + record the line + close.
  • the gatekeeper closing a branch-merge ticket after the release pipeline: the
    OWNING stream posts the note + records Discuss-closed: at hand-off; the
    gatekeeper's close then finds the evidence. The gatekeeper does NOT post to
    the client thread — the stream that owns the thread does.

How to compose + post the closing note (body_is_html, owner on partner_ids,
the identity signature "<MarekAI|ZbynekAI> <N>", #641): skills/odoo-discuss-xmlrpc/handover-compose.md.

Bypass (rare, logged, ONLY a genuine non-client / meta ticket that merely names
these markers in prose): put  airuleset:discuss-close-ok  in the close command.
MSG
                exit 2
            fi
        fi
    fi
fi
# ---- end #627 Discuss gate; fall through to the authority logic below ----

# Resolve THIS stream's authority (marker-aware; the python reads the cwd project
# CLAUDE.md override, else the per-user map). The hook's cwd IS the session's cwd,
# i.e. the project dir — so the project's authority marker (if any) is honored.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(dirname "$SCRIPT_DIR")
AUTH=$(python3 "$REPO_DIR/airuleset.py" authority 2>/dev/null || echo "")

# Fail-SAFE: a close is being attempted but authority could not be resolved (the CLI
# errored / is missing). Do NOT silently allow it (that would re-enable the exact
# self-close drift on any infra breakage) — BLOCK with a clear diagnostic. This only
# bites when airuleset.py itself is broken, which already breaks the whole stream.
if [ -z "$AUTH" ]; then
    echo "BLOCKED (fail-safe): could not resolve autopilot authority — 'python3 $REPO_DIR/airuleset.py authority' produced no output (CLI missing/broken?)." >&2
    echo "  Refusing 'gh issue close' until authority can be verified. Fix airuleset.py, then retry — or hand off via a comment and let the maintainer close." >&2
    exit 2
fi
[ "$AUTH" = "full" ] && exit 0

# reduced-authority stream (fork-no-merge OR branch-merge): allow closing a
# SELF-AUTHORED issue (the stream's own sub-finding). Extract the issue number +
# optional -R/--repo from the `gh issue close` form; the `gh api PATCH` form is
# never exempted (use `gh issue close` for legit self-closes).
#
# #349 adversarial review, CRITICAL: every CURRENTLY REGISTERED branch-merge
# stream (marek, montalu, montalu2/3/4) authenticates as the SAME shared gh
# identity as the repo's MAINTAINER — on those boxes `ME` (below) resolves to
# the maintainer's own login, and so does the AUTHOR of virtually every
# assigned ticket (the maintainer files them). `ME == AUTHOR` is then ALWAYS
# true regardless of whether the ticket is a genuine self-filed sub-finding or
# the maintainer-assigned work this whole guard exists to protect — a verbatim
# replay of the montalu3 incident closes cleanly through this exemption. The
# discriminator is unusable once `ME` equals the maintainer's own identity, so
# the exemption is refused outright in that case (fail toward hand-off, never
# toward guessing — same direction as the fail-safe above): a shared-identity
# stream loses the self-close bookkeeping shortcut and hands off EVERY closed
# ticket, including its own sub-findings, which costs one extra comment and
# closes the actual regression. A genuinely separate-identity stream
# (fork-no-merge's david, whose gh login is never the maintainer's) is
# unaffected.
ISSUE_NUM=$(printf '%s' "$CMD" | grep -oE 'gh[[:space:]]+issue[[:space:]]+close[[:space:]]+"?#?([0-9]+)' | grep -oE '[0-9]+' | head -1 || echo "")
# REPO_ARG accepts a single- OR double-quoted value (`["']?`, #533 review m5) — an
# unquoted `-R owner/repo` is unchanged. A GLUED `-Rowner/repo` still yields empty
# (no separator); the #533 exemption below fails SAFE on that via the -R-present
# guard, and the author carve-out's own use is unchanged in direction (empty
# REPO_ARG -> author read against cwd repo, its pre-existing behaviour).
REPO_ARG=$(printf '%s' "$CMD" | grep -oE "(-R|--repo)[[:space:]=]+[\"']?[A-Za-z0-9._/-]+" | head -1 | sed -E "s/^(-R|--repo)[[:space:]=]+[\"']?//" || echo "")

# #533 review C1 (CRITICAL): the exemptions below `exit 0` on the FIRST close's
# issue number and then ALLOW THE WHOLE Bash command — so a compound like
# `gh issue close <own> --comment ok && gh issue close <foreign>` or
# `gh issue close <own> --comment ok && gh api -X PATCH .../issues/N -f state=closed`
# smuggled a SECOND, unguarded mutation (a foreign close, or the PATCH form the
# hook swears it never exempts) past the guard. Fail SAFE: an exemption may fire
# ONLY when the command is a SINGLE close action. If there is not EXACTLY one
# top-level `gh issue close`, OR a PATCH-close segment is also present, OR the
# command hides a nested interpreter (bash/sh -c, eval) that could carry another
# close, BLANK ISSUE_NUM — which skips BOTH carve-outs below (each gated on
# `[ -n "$ISSUE_NUM" ]`, so the author carve-out stays byte-untouched) and falls
# through to the BLOCK. A benign prefix like `cd dir && gh issue close N` still
# has exactly one close and no interpreter, so it is unaffected. (A pure PATCH
# close already has ISSUE_NUM empty — no `gh issue close` — so this only tightens
# the `gh issue close`-carrying forms.) The residual `bash -c '…'` / `--input`
# whole-hook detection gaps are pre-existing (is_close untouched) and filed
# separately; this guard closes the CHAINED smuggle, the #533-activated path.
CLOSE_HITS=$(printf '%s' "$CMD" | grep -oE '(^|[;&|[:space:](])gh[[:space:]]+issue[[:space:]]+close([[:space:]]|$)' 2>/dev/null || true)
N_CLOSE=$(printf '%s' "$CLOSE_HITS" | grep -c . 2>/dev/null || true)
# #540: uses the SAME `_is_patch_close_cmd` predicate as the front gate above —
# one definition, so the single-action guard and the detector can never drift on
# what a PATCH-close is (incl. the #540 hidden-body `--input`/`=@file` forms).
HAS_PATCH_CLOSE=0
if _is_patch_close_cmd "$CMD"; then
    HAS_PATCH_CLOSE=1
fi
HAS_INTERP=0
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:](])(bash|sh|dash|zsh)[[:space:]]+-c([[:space:]]|$)|(^|[;&|[:space:](])(eval|xargs)([[:space:]]|$)'; then
    HAS_INTERP=1
fi
# #756 review F6 (pre-existing #533/#540 residual, exposure widened): backtick command
# substitution is a nested-interpreter smuggle both HAS_INTERP and the N_CLOSE boundary
# class miss — `(` catches $(…) but a backtick glued to a word char is invisible, so
# `gh issue close X --comment y`gh issue close Z`` runs a second nested close no counter
# sees, which a carve-out `exit 0` would allow. Any backtick alongside a close blanks the
# exemption — fail toward hand-off (a legit close rarely carries a literal backtick).
if printf '%s' "$CMD" | grep -qF '`'; then
    HAS_INTERP=1
fi
if [ "${N_CLOSE:-0}" -ne 1 ] || [ "$HAS_PATCH_CLOSE" -eq 1 ] || [ "$HAS_INTERP" -eq 1 ]; then
    ISSUE_NUM=""   # not a single simple close — fail toward hand-off (no exemption)
fi

if [ -n "$ISSUE_NUM" ]; then
    # THIS box's own gh identity. NOT a raw `gh api user` — on a GitHub
    # App-token box (odoo-erp #3284: montalu/2/3/4, marek, david2/3/4) that
    # 403s structurally ("Resource not accessible by integration"), leaving ME
    # empty and the self-authored carve-out permanently unreachable even for a
    # genuinely App-authored sub-finding (#463). `authority --self-login`
    # returns the fixed stream bot login on such a box (no network call) and
    # the real `gh api user` login (via `_gh_login()`) on every other box — a
    # box whose gh resolves identically stays behaviourally the same, and a
    # git-credentials-only box (david's fork-no-merge) is a strict FAIL-SAFE
    # improvement: the old raw `gh api user` failed there (empty ME -> carve-out
    # broken) while `_gh_login()` resolves its real login; every divergence
    # still fails toward BLOCK, never a wrong-allow (#463 adversarial review).
    # The App identity (distinct from the maintainer) restores the
    # self-vs-assigned distinguishability the pre-App shared-PAT setup destroyed.
    ME=$(python3 "$REPO_DIR/airuleset.py" authority --self-login 2>/dev/null || echo "")
    MAINTAINER_LOGIN=$(python3 "$REPO_DIR/airuleset.py" authority --maintainer-login 2>/dev/null || echo "")
    if [ -n "$REPO_ARG" ]; then
        AUTHOR=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json author -q .author.login 2>/dev/null || echo "")
    else
        AUTHOR=$(gh issue view "$ISSUE_NUM" --json author -q .author.login 2>/dev/null || echo "")
    fi
    # MAINTAINER_LOGIN unresolvable -> cannot PROVE $ME is not the maintainer
    # -> refuse the exemption (same fail-SAFE direction as the rest of this
    # hook: undeterminable never means "allow").
    if [ -n "$ME" ] && [ -n "$AUTHOR" ] && [ "$ME" = "$AUTHOR" ] \
       && [ -n "$MAINTAINER_LOGIN" ] && [ "$ME" != "$MAINTAINER_LOGIN" ]; then
        exit 0   # self-authored sub-finding — the stream's own bookkeeping, allowed
    fi
fi

# #533: acceptance-close carve-out (ADDITIVE — the author carve-out above is
# BYTE-UNTOUCHED; the `gh api PATCH` form is NEVER exempted here, since ISSUE_NUM
# is empty for it). Authorship is a DEAD ownership signal on a shared-bot-identity
# box (every montalu-stream ticket is authored by the maintainer, so the author
# carve-out refuses even a genuine stream close). Ownership is instead the
# `stream:<user>` LABEL — the SAME signal cli_quals.py's `_ticket_is_stream_labeled`
# / `_slice_quals` already use, and it survives a shared gh identity. A
# REDUCED-authority stream may CLOSE its OWN stream-labeled `needs-acceptance`
# ticket WITH an evidence --comment. Allowed IFF ALL of:
#   (1) `authority --stream-label` is non-empty (this box's own stream label —
#       empty on a full-authority box, so the exemption never fires there);
#   (2) the ticket carries THAT label AND `needs-acceptance` AND NONE of the #512
#       re-hand-off/bounce override labels (ready-for-review / needs-gatekeeper /
#       prio:bounce — a re-hand-off/bounce is NOT an acceptance state);
#   (3) the command carries --comment/-c (close WITH a citation of the acceptance
#       evidence — a static presence check, deliberately no content grading).
# Every failure (empty stream label, unreadable labels, foreign/absent label,
# missing needs-acceptance, any override label present, missing --comment) falls
# through to the BLOCK below — fail toward hand-off, the SAME direction as the
# fail-safe above (#349/#463), never toward a wrong-allow. Why the #349 hole stays
# closed: `needs-acceptance` is applied EXCLUSIVELY by the gatekeeper AFTER the
# /process-subdev release pipeline, so a verbatim montalu3 replay (merged into the
# integration branch, no release yet) carries no such label and still blocks.
ACCEPTANCE_MISSING_COMMENT=0
if [ -n "$ISSUE_NUM" ]; then
    STREAM_LABEL=$(python3 "$REPO_DIR/airuleset.py" authority --stream-label 2>/dev/null || echo "")
    # #533 review m5: if the command carries a -R/--repo flag but REPO_ARG came
    # back EMPTY (a glued `-Rowner/repo`, or a form the parser cannot read), the
    # labels read below would run against the CWD repo instead of the named one —
    # a potential wrong-allow if the cwd repo happens to carry issue N with this
    # stream's acceptance labels. Fail SAFE: mark it unparseable and refuse the
    # exemption. A parseable -R gives a non-empty REPO_ARG, so a legit acceptance
    # close is unaffected.
    # Detect a -R/--repo FLAG token in ANY form — separated (`-R foo`, `-R=foo`),
    # end-of-string, OR GLUED (`-Rfoo`, which the REPO_ARG parser cannot read). If
    # such a flag is present but REPO_ARG is empty, the labels read would fall back
    # to the cwd repo → fail SAFE.
    REPO_UNPARSEABLE=0
    if printf '%s' "$CMD" | grep -qE '(^|[[:space:]])(-R|--repo)' \
       && [ -z "$REPO_ARG" ]; then
        REPO_UNPARSEABLE=1
    fi
    if [ -n "$STREAM_LABEL" ] && [ "$REPO_UNPARSEABLE" -eq 0 ]; then
        GH_RC=0
        if [ -n "$REPO_ARG" ]; then
            LABEL_NAMES=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json labels -q '.labels[].name' 2>/dev/null) || GH_RC=$?
        else
            LABEL_NAMES=$(gh issue view "$ISSUE_NUM" --json labels -q '.labels[].name' 2>/dev/null) || GH_RC=$?
        fi
        # A gh error reading labels is fail-SAFE: never exempt on an unverifiable
        # label set (mirror of the empty-AUTH / empty-AUTHOR fail direction).
        if [ "$GH_RC" -eq 0 ]; then
            # Whole-line fixed-string membership (label names carry `:`; never a
            # regex). Defined here (reachable, LABEL_NAMES in scope); only ever
            # called inside `if`/`&&`/`!` conditions, so `set -e` never aborts on
            # a grep no-match.
            _has_label() { printf '%s\n' "$LABEL_NAMES" | grep -qxF "$1"; }
            # #564: STREAM_LABEL may be MULTIPLE newline-separated equivalents.
            # A base-stream rename (montalu -> montalu1) means this box's own
            # tickets can still carry the OLD `stream:montalu` label during the
            # transition, so `authority --stream-label` emits every equivalent.
            # The acceptance carve-out matches if the ticket carries ANY of them.
            # Read line-by-line (a here-string runs in THIS shell, so `return`
            # works; `IFS= read -r` avoids any word-split / glob expansion — a
            # defensive choice, since `stream:<unix-user>` values never contain
            # whitespace or glob chars anyway). Only ever called inside `if`/`&&`
            # conditions, so `set -e` never aborts on a grep no-match (same reason
            # `_has_label` is safe).
            _has_own_stream_label() {
                local _lbl
                while IFS= read -r _lbl; do
                    [ -n "$_lbl" ] || continue
                    if _has_label "$_lbl"; then return 0; fi
                done <<< "$STREAM_LABEL"
                return 1
            }
            if _has_own_stream_label && _has_label "needs-acceptance" \
               && ! _has_label "ready-for-review" \
               && ! _has_label "needs-gatekeeper" \
               && ! _has_label "prio:bounce"; then
                # Conditions 1+2 hold; condition 3 (--comment/-c) is the last gate.
                if printf '%s' "$CMD" | grep -qE -- '--comment([[:space:]=]|$)' \
                   || printf '%s' "$CMD" | grep -qE -- '(^|[[:space:]])-c([[:space:]=]|$)'; then
                    exit 0   # acceptance close WITH evidence citation — allowed (#533)
                else
                    ACCEPTANCE_MISSING_COMMENT=1
                fi
            fi
        fi
    fi
fi

# #756: gk-verdict-artifact carve-out (ADDITIVE — the author carve-out AND the #533
# acceptance carve-out above are BYTE-UNTOUCHED; the `gh api PATCH` form is NEVER
# exempted here, since ISSUE_NUM is empty for it and the #533 single-action guard
# blanks it for a compound/PATCH/interpreter smuggle). Aligns airuleset with the
# odoo-erp owner ruling #5378: the gatekeeper reviews+merges, posts its verdict,
# DROPS the queue label, and HANDS THE TICKET BACK — the delivering STREAM closes it
# after review. A REDUCED-authority stream may CLOSE a foreign-authored ODOO-ERP
# ticket WITH an evidence --comment IFF ALL of:
#   (1) the repo is EXACTLY `zbynekdrlik/odoo-erp` (full owner/repo, not just the
#       `odoo-erp` basename — a fork/same-named repo has no reopen-guard net) — the
#       ONLY repo where #5378 applies AND where the reopen-guard
#       `subdev-self-close-guard.yml` (POST-close, precise time-window) provides the
#       SECOND net; a `-R` present-but-unparseable falls SAFE (block);
#   (2) the ticket carries a gk review-verdict ARTIFACT in its comments (the SAME
#       #3784 detection — never WHO closed);
#   (3) the ticket carries NONE of `ready-for-review`/`needs-gatekeeper` (gk still
#       owns it), `prio:bounce` (a returned bounce), or `needs-acceptance` (that
#       state is closed via the #533 acceptance carve-out above, which REQUIRES its
#       own --comment client-confirmation citation — excluding it here stops the
#       verdict path from bypassing that requirement);
#   (4) the command carries --comment/-c (an evidence citation).
# Every failure (non-odoo-erp, unparseable -R, unreadable ticket, no artifact, any
# override label, missing --comment) falls through to the BLOCK below — fail toward
# hand-off, the SAME direction as the author/acceptance carve-outs (#349/#463). Why
# the #349 hole stays closed: an unreviewed merged-into-integration ticket carries
# no verdict artifact (gk has not reviewed it) → condition 2 fails → still blocks.
VERDICT_MISSING_COMMENT=0
if [ -n "$ISSUE_NUM" ]; then
    # Repo for the close: -R, else the cwd git remote. Reduce to a FULL lowercased
    # `owner/repo` and require EXACTLY `zbynekdrlik/odoo-erp` — a basename-only match
    # (the #627 gate's parse) would engage the carve-out for `anyowner/odoo-erp` (a
    # fork or a same-named repo) whose safety net (the reopen-guard) does NOT exist,
    # so the carve-out would GRANT an exemption where #627 only fails OPEN. Handle the
    # three URL shapes: gh's `owner/repo` -R value, https://host/owner/repo[.git], and
    # scp `git@host:owner/repo[.git]` — strip scheme://, user@, and (scp) the host:
    # prefix, then keep the LAST two `/`-separated segments.
    VERDICT_REPOFULL="$REPO_ARG"
    if [ -z "$VERDICT_REPOFULL" ]; then
        VERDICT_REPOFULL=$(git remote get-url origin 2>/dev/null || echo "")
    fi
    VERDICT_REPOFULL="${VERDICT_REPOFULL%.git}"
    VERDICT_REPOFULL="${VERDICT_REPOFULL%/}"
    VERDICT_REPOFULL="${VERDICT_REPOFULL#*://}"   # drop scheme:// (https/ssh)
    VERDICT_REPOFULL="${VERDICT_REPOFULL##*@}"    # drop user@ (ssh)
    case "$VERDICT_REPOFULL" in *:*) VERDICT_REPOFULL="${VERDICT_REPOFULL##*:}" ;; esac  # scp host: prefix
    _vr_repo="${VERDICT_REPOFULL##*/}"
    _vr_rest="${VERDICT_REPOFULL%/*}"
    _vr_owner="${_vr_rest##*/}"
    VERDICT_REPOFULL="${_vr_owner}/${_vr_repo}"
    # #533 review m5 mirror: a -R flag present but REPO_ARG empty (a glued
    # `-Rowner/repo`) → the reads below would target the CWD repo → fail SAFE.
    VERDICT_REPO_UNPARSEABLE=0
    if printf '%s' "$CMD" | grep -qE '(^|[[:space:]])(-R|--repo)' \
       && [ -z "$REPO_ARG" ]; then
        VERDICT_REPO_UNPARSEABLE=1
    fi
    if [ "${VERDICT_REPOFULL,,}" = "zbynekdrlik/odoo-erp" ] && [ "$VERDICT_REPO_UNPARSEABLE" -eq 0 ]; then
        VGH_RC=0
        if [ -n "$REPO_ARG" ]; then
            VERDICT_JSON=$(gh issue view "$ISSUE_NUM" -R "$REPO_ARG" --json labels,comments 2>/dev/null) || VGH_RC=$?
        else
            VERDICT_JSON=$(gh issue view "$ISSUE_NUM" --json labels,comments 2>/dev/null) || VGH_RC=$?
        fi
        # A gh error / empty payload reading the ticket is fail-SAFE (never exempt
        # on an unverifiable ticket — the #349/#463 fail direction). Documented
        # residual (#756 review F8, low-confidence, fail-SAFE): `gh issue view --json
        # comments` returns only the first page (~100), so a verdict comment past #100
        # on a very long thread is invisible → false BLOCK (never a false allow). If it
        # ever bites, fetch comments via `gh api …/comments --paginate` like the
        # reopen-guard; the reopen-guard (POST-close) is the authoritative net anyway.
        if [ "$VGH_RC" -eq 0 ] && [ -n "$VERDICT_JSON" ]; then
            # `|| true`: fail-safe by CONSTRUCTION — a jq error (malformed payload)
            # must leave the vars empty (→ `_has_gk_verdict_artifact` fails → block),
            # never abort the whole hook mid-way under `set -e` (which would exit with
            # jq's status and no stderr) (#756 review F5).
            V_LABELS=$(printf '%s' "$VERDICT_JSON" | jq -r '.labels[].name' 2>/dev/null || true)
            V_COMMENTS=$(printf '%s' "$VERDICT_JSON" | jq -r '.comments[].body' 2>/dev/null || true)
            # Whole-line fixed-string membership (label names carry `:`; never a
            # regex). Only ever called inside `if`/`!` conditions, so `set -e`
            # never aborts on a grep no-match.
            _v_has_label() { printf '%s\n' "$V_LABELS" | grep -qxF "$1"; }
            if _has_gk_verdict_artifact "$V_COMMENTS" \
               && ! _v_has_label "ready-for-review" \
               && ! _v_has_label "needs-gatekeeper" \
               && ! _v_has_label "prio:bounce" \
               && ! _v_has_label "needs-acceptance"; then
                # Conditions 1+2+3 hold; condition 4 (--comment/-c) is the last gate.
                if printf '%s' "$CMD" | grep -qE -- '--comment([[:space:]=]|$)' \
                   || printf '%s' "$CMD" | grep -qE -- '(^|[[:space:]])-c([[:space:]=]|$)'; then
                    exit 0   # post-gk-review stream self-close WITH evidence — allowed (#756)
                else
                    VERDICT_MISSING_COMMENT=1
                fi
            fi
        fi
    fi
fi

if [ "$AUTH" = "branch-merge" ]; then
    echo "BLOCKED: branch-merge stream — you may close ONLY your OWN (self-authored) issues." >&2
    echo "" >&2
    echo "  This issue is assigned / foreign-authored (or its author could not be verified):" >&2
    echo "  merging into the project's INTEGRATION branch does NOT close it — that branch is" >&2
    echo "  not the repo's default branch, so GitHub's Closes #N auto-close never fires there." >&2
    echo "  Your authority ENDS at that merge; the gatekeeper closes the ticket only AFTER the" >&2
    echo "  full /process-subdev release pipeline (integration→staging→main + deploy + verify)." >&2
    echo "  Closing it yourself hides the hand-off and skips that review. (Self-authored" >&2
    echo "  sub-findings ARE closable — the hook verifies author == your gh login; if gh failed" >&2
    echo "  just now, fix auth and retry.)" >&2
    echo "" >&2
    echo "  odoo-erp #5378: once the gatekeeper posts its review-verdict comment AND drops the" >&2
    echo "  queue label, the DELIVERING STREAM closes this ticket itself with an evidence" >&2
    echo "  --comment (airuleset #756). Until that verdict lands, hand off and wait." >&2
    echo "" >&2
    echo "  HAND OFF instead, leaving the issue OPEN:" >&2
    echo "    - DONE (merged into integration): gh issue comment <N> --body \"READY-FOR-REVIEW: <PR/branch> — <local verify evidence>\"" >&2
    echo "                       (the repo's hand-off automation labels it ready-for-review; /process-subdev picks it up)" >&2
    echo "                       then fire the card:" >&2
    echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
    echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
    echo "" >&2
    echo "  See agents/autopilot-worker.md (branch-merge) + skills/process-subdev/SKILL.md." >&2
else
    echo "BLOCKED: fork-no-merge stream — you may close ONLY your OWN (self-authored) issues." >&2
    echo "" >&2
    echo "  This issue is assigned / foreign-authored (or its author could not be verified):" >&2
    echo "  the gatekeeper MAINTAINER closes it at cross-fork review/merge. Closing it yourself" >&2
    echo "  removes the READY-FOR-REVIEW hand-off event and bypasses the review this authority" >&2
    echo "  stream exists to enforce. (Self-authored sub-findings ARE closable — the hook" >&2
    echo "  verifies author == your gh login; if gh failed just now, fix auth and retry.)" >&2
    echo "" >&2
    echo "  odoo-erp #5378: once the gatekeeper posts its review-verdict comment AND drops the" >&2
    echo "  queue label, the DELIVERING STREAM closes this ticket itself with an evidence" >&2
    echo "  --comment (airuleset #756). Until that verdict lands, hand off and wait." >&2
    echo "" >&2
    echo "  HAND OFF instead, leaving the issue OPEN:" >&2
    echo "    - DONE ticket:     gh issue comment <N> --body \"READY-FOR-REVIEW: <branch> — <local verify evidence>\"" >&2
    echo "                       then fire the card:" >&2
    echo "                       airuleset.py notify --run-card --handoff --repo <owner/name> --issue <N> --goal \"…\" --achieved \"…\"" >&2
    echo "    - OBSOLETE ticket: gh issue comment <N> --body \"OBSOLETE: <evidence>\"   (do NOT close)" >&2
    echo "" >&2
    echo "  See agents/autopilot-worker.md (fork-no-merge) + pr-merge-policy.md (reduced-authority scope)." >&2
fi

# #533: ONLY condition 3 failed — ownership + acceptance state were both OK, the
# close was refused solely because it carried no evidence citation. Name the
# acceptance recipe (this hint fires for NO other block reason, so it never
# invites a workaround on a genuinely foreign/assigned ticket).
if [ "${ACCEPTANCE_MISSING_COMMENT:-0}" = "1" ]; then
    echo "" >&2
    echo "  #533 NOTE: this issue carries YOUR stream label + needs-acceptance and no" >&2
    echo "  re-hand-off/bounce label — a stream ACCEPTANCE close IS allowed, but ONLY WITH" >&2
    echo "  a citation of the acceptance evidence. Re-run adding a --comment:" >&2
    echo "    gh issue close $ISSUE_NUM --comment \"<acceptance evidence — client confirmed, ref …>\"" >&2
fi

# #756: ONLY condition 4 failed — this odoo-erp ticket carries a gatekeeper
# review-verdict artifact and no re-hand-off/bounce/acceptance label, so a
# post-review STREAM self-close IS allowed (odoo-erp#5378) but ONLY WITH an
# evidence citation. This hint fires for NO other block reason.
if [ "${VERDICT_MISSING_COMMENT:-0}" = "1" ]; then
    echo "" >&2
    echo "  #756 NOTE: this odoo-erp issue carries a gatekeeper review-verdict comment" >&2
    echo "  and no re-hand-off/bounce/acceptance label — the gatekeeper reviewed+merged" >&2
    echo "  and handed it back, so a stream self-close IS allowed (odoo-erp#5378), but ONLY" >&2
    echo "  WITH an evidence citation. Re-run adding a --comment:" >&2
    echo "    gh issue close $ISSUE_NUM --comment \"<merge + gatekeeper-verdict evidence, ref …>\"" >&2
fi
exit 2
