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
#     stream itself — the gatekeeper maintainer closes them: for fork-no-merge at
#     cross-fork review/merge, for branch-merge only AFTER the full
#     `/process-subdev` release pipeline. Self-closing one removes the
#     READY-FOR-REVIEW hand-off event and bypasses review.
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

# Is this an issue-CLOSE action? Match `gh issue close` at a command boundary, plus
# the REST-API PATCH form `gh api .../issues/<N> ... state ... closed`. (A quoted
# mention of the phrase inside e.g. a commit message could false-positive; that is
# fail-SAFE — the worker simply rephrases — and far cheaper than missing a real close.)
is_close=0
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:](])gh[[:space:]]+issue[[:space:]]+close([[:space:]]|$)'; then
    is_close=1
# REST-API PATCH form: require BOTH the PATCH method AND `state=closed` (the field-set
# form) — so a READ predicate like `gh api .../issues/N --jq '.state=="closed"'`
# (which has neither `-X PATCH` nor a bare `state=closed`) is NOT mistaken for a write.
# The method separator class is `[[:space:]=]` so the GLUED `--method=PATCH` form is
# caught too (#533 review M3) — `-X PATCH` (space) still matches unchanged.
elif printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+api[^|]*issues/[0-9]+' \
     && printf '%s' "$CMD" | grep -qE '(-X|--method)[[:space:]=]*[Pp][Aa][Tt][Cc][Hh]' \
     && printf '%s' "$CMD" | grep -qE 'state=closed'; then
    is_close=1
fi
[ "$is_close" -eq 0 ] && exit 0

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
HAS_PATCH_CLOSE=0
if printf '%s' "$CMD" | grep -qE 'gh[[:space:]]+api[^|]*issues/[0-9]+' \
   && printf '%s' "$CMD" | grep -qE '(-X|--method)[[:space:]=]*[Pp][Aa][Tt][Cc][Hh]' \
   && printf '%s' "$CMD" | grep -qE 'state=closed'; then
    HAS_PATCH_CLOSE=1
fi
HAS_INTERP=0
if printf '%s' "$CMD" | grep -qE '(^|[;&|[:space:](])(bash|sh|dash|zsh)[[:space:]]+-c([[:space:]]|$)|(^|[;&|[:space:](])(eval|xargs)([[:space:]]|$)'; then
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
            if _has_label "$STREAM_LABEL" && _has_label "needs-acceptance" \
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
exit 2
