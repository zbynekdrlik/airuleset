"""cli_quals.py — ticket-qualifying-set DERIVATION core (footer `I N` + `/goal` stop-proof).

Extracted from airuleset.py (#433 cluster I, File A of a 2-file split). The
LOWER layer of the slice/core-quals/authority cluster: this box's gh identity +
authority resolution + the slice/obligation/union qualifying-set derivation that
`cmd_tickets_status` (footer refresh), the run-card, `watchdog/goal.py` and the
hooks all call. The CLI subcommands + issue-row rendering live in the sibling
`cli_quals_cmd.py` (File B).

Self-contained LEAF: stdlib only at module level. Names that stay resident in
airuleset.py (shared gh plumbing `_gh_out`/`_gh_env`/`_repo_slug`/
`_comment_readiness_signal`, the shared registry `AUTHORITY_BY_USER`/
`AUTHORITY_PROFILES`, `_current_user`/`_gh_login`/`MAINTAINER_GH_LOGIN`, the
`_HANDOFF_COMMENT_CHECK_LIMIT` const) are reached lazily via a deferred
`import airuleset` inside the function bodies that need them, referenced as
`airuleset.X` — never a module-level `import airuleset` (that would crash CLI
mode, since airuleset.py runs as `__main__`; internals #1481). airuleset.py
re-exports every name here via its facade.
"""
import json
import os
import re
import time
from pathlib import Path

import working_time


class SliceUnresolved(Exception):
    """This box's own gh identity could not be resolved, so "my slice" is
    undefined. Raised by `_slice_quals` instead of falling back to a default
    qual set — every caller must handle it in ITS OWN established way
    (the CLI refuses; the footer and the run-card keep `None`, never a
    wrong number). #181 I-2."""


# The label a stream applies to a PERMANENT ops-channel ticket — a
# self-declared "this issue never auto-closes" channel (odoo-erp #1861:
# "[TRVALÝ OPS KANÁL — NEZATVÁRAŤ] erp-test-* teardown/recreate/refresh";
# #3037: a snapshot-retention alert log). It is never workable /autopilot
# backlog, no matter how long it stays open (#362).
OPS_CHANNEL_LABEL = "ops-channel"

# The qualifying-set EXCLUSION fragment shared by every open-issue search in
# this file that must never surface a manually-skipped OR a PERMANENT
# ops-channel ticket as workable backlog — `core-quals`/`slice-quals` (the
# `/goal` stop-proof), the footer's own counts (`cmd_tickets_status`), and
# the Discord run-card's `remaining` count all AND this onto their base
# query, so none of them can ever disagree about which population is
# "qualifying" (#362, same tier as the pre-existing `autopilot-skip`
# exclusion — before this fix, `core-quals --count` could NEVER reach `0`
# while a permanent ops-channel ticket sat open, and the /autopilot loop
# dispatched a full worker onto one that found "nothing to do").
#
# Deliberately NOT extended to the two POSITIVE `label:autopilot-skip`
# "skipped" bucket queries (`cmd_tickets_status`'s own `entry["skipped"]`) —
# that bucket answers a DIFFERENT question ("how many of the qualifying
# tickets are also explicitly skip-labelled"), not "the qualifying set"
# itself, and an ops-channel ticket without the skip label already never
# appears there. Deliberately NOT rendered as its own statusline bucket
# either (documented-invisible instead) — #313 is a direct, repeated user
# request to SIMPLIFY the footer ("counter chaos"), and a permanent,
# rarely-applied label is exactly the kind of population a new bucket would
# be noise for.
AUTOPILOT_SKIP_EXCL = "-label:autopilot-skip -label:%s" % OPS_CHANNEL_LABEL


def _core_search_excl():
    """The full-authority CORE slice's exclusion fragment: every REDUCED-
    authority sub-dev stream's own `stream:<user>` label, so the footer
    (`cmd_tickets_status`), the Discord card (`_notify_run_card`), and the
    `/goal` stop-proof (`cmd_core_quals`) all exclude EXACTLY the same
    sub-dev-owned tickets from a full-authority box's own count — single
    source of truth (#164 / #181 I4).

    Only entries whose profile is NOT `full` are excluded (#181 M-5): a
    hypothetical `full` entry in AUTHORITY_BY_USER is not a sub-dev stream
    at all, and excluding its label would silently remove a whole population
    from every full-authority count.

    #578 counting audit — `stream:core` is DELIBERATELY NOT excluded (and is
    the reason this filter keys on AUTHORITY_BY_USER membership, not on "carries
    ANY `stream:` label"): `core` is not a reduced-authority sub-dev stream, it
    is the full-authority box's OWN work marker (odoo-erp #4520-shape umbrella/
    tracking tickets), so a `stream:core` ticket STAYS in the core obligation
    set and IS counted in `I` — correct, because only this box acts on it. A
    `stream:core` ticket gated on another ticket/release belongs in `W` via an
    `ops-wait` label (the #578 pipeline-gated/umbrella doctrine), never dropped
    from the count by this exclusion. Conversely a FOREIGN stream's bare
    `needs-acceptance` (a reduced-authority `stream:<user>` in AUTHORITY_BY_USER,
    with NO `ready-for-review`/`needs-gatekeeper`) IS excluded here, so it never
    reaches the core obligation `seen` — the ONE mechanical guard against a
    foreign acceptance leaking into the full-authority partition, where
    `_partition_workable` (which cannot see box authority) would now route it to
    `U` (#622: a bare needs-acceptance is queued for owner approval → U
    unconditionally; the exclusion keeps a FOREIGN one out of this box's counts
    entirely, be that `U` or `I`).

    #561: each excluded stream is EXPANDED via `_stream_rename_equivalents()`
    — the SAME single alias primitive `_slice_quals()`/`_ticket_is_stream_
    labeled()` already consume — so a base-stream rename target excludes BOTH
    its old and its new `stream:` label. This was the THIRD consumer the #537
    staging missed: after the live montalu->montalu1 rename removed the old
    `montalu` key from AUTHORITY_BY_USER, this exclusion stopped covering the
    old `stream:montalu` label the odoo-erp tickets still carry (69 open,
    0 `stream:montalu1`), leaking ~50 tickets into gk core-quals/footer/goal.
    The set DEDUPES (`david` and `david1` are both keys pre-rename and each
    expands to the same pair — a naive nested comprehension would emit
    duplicate fragments), sorted for a deterministic query string."""
    import airuleset
    names = set()
    for u, profile in airuleset.AUTHORITY_BY_USER.items():
        if profile != "full":
            names.update(_stream_rename_equivalents(u))
    return " ".join("-label:stream:%s" % n for n in sorted(names))


def _gh_app_token_dir():
    """The GitHub App installation-token directory for THIS stream box
    (`~/.config/gh-app-tokens/`), resolved at CALL time so a relocated
    `$HOME`/override is honoured on every call, mirroring
    `watchdog.draft_rescue_dir()`'s own established shape.

    This is the EXACT path the real, deployed odoo-erp#3281 mechanism
    reads/writes on the subdev VPS — confirmed directly against the
    shipped scripts in zbynekdrlik/odoo-erp: `push-stream-tokens.sh`'s
    `REMOTE_DIR_NAME=".config/gh-app-tokens"` (created via `install -d
    -m 700` on the first successful token delivery) and
    `gh-app-token.sh`'s `TOKEN_DIR="${GH_APP_TOKEN_DIR:-$HOME/.config/
    gh-app-tokens}"` — never an invented convention.

    `GH_APP_TOKEN_DIR` overrides it for tests, mirroring that SAME shell
    script's own env var name so both sides of the mechanism agree."""
    override = os.environ.get("GH_APP_TOKEN_DIR")
    if override:
        return Path(override)
    return Path.home() / ".config" / "gh-app-tokens"


def _is_gh_app_token_box():
    """True when this box authenticates `gh` via a GitHub App INSTALLATION
    token (odoo-erp#3281's `gh-app-stream-tokens` mechanism — david2/
    david3/david4, odoo-erp#3282), detected from a LOCAL, STATIC fact —
    the App-token directory's presence — never a network call (#356).

    Why a network call cannot answer this question at all: an App
    installation token carries no user identity, so `gh api user` 403s
    ("Resource not accessible by integration") on EVERY call, structurally,
    not intermittently — there is no failure signature to distinguish
    "this is an App-token box" from "this box's gh is genuinely broken for
    an unrelated reason", which is exactly the ambiguity `SliceUnresolved`
    exists to refuse rather than guess at (#181 I-2). A local signal
    removes the ambiguity instead of trying to classify it.

    `.is_dir()`, never a bare `.exists()` — a stray FILE at this path must
    not be misread as "provisioned".

    Known, accepted residual (adversarial review of #356): a stray or
    stale App-token directory delivered to an OWN-account (PAT) box —
    e.g. a misdirected `push-stream-tokens.sh` delivery, or a leftover
    from an App-token-to-PAT migration — silently NARROWS that box's own
    slice from 3 quals (assignee ∪ author ∪ label) down to 1 (label
    alone), dropping any assigned/authored-but-unlabeled ticket from the
    stop-proof with no refusal (the existing empty-result validators check
    the LABEL dimension, never the missing assignee/author one). This is
    an operational-error trigger, not something this local, static check
    can distinguish from a genuine App-token box — a real App token proves
    nothing beyond "this directory exists" either."""
    try:
        return _gh_app_token_dir().is_dir()
    except OSError:
        return False


def _stream_rename_equivalents(name):
    """All stream names equivalent to `name` under the in-progress base-stream
    rename (#537): `name` itself FIRST, plus its rename alias in EITHER
    direction — old->new via `STREAM_RENAME_ALIASES`, or new->old via the
    reverse lookup. A name not involved in any rename returns just `[name]`.

    The ONE expansion primitive `_slice_quals()` and `_ticket_is_stream_
    labeled()` both consume, so the transition alias has a single definition.
    Reads `airuleset.STREAM_RENAME_ALIASES` (the facade re-export) so a test
    patch is honoured — never `cli_fleet` directly (the L-E rule).

    Invariant: the table must stay FLAT — no name is both a key AND a value
    (old names and new names disjoint). The old->new / new->old logic resolves
    exactly ONE alias edge per name; a CHAINED table (`{a: b, b: c}`) would
    resolve `b` only forward (`b->c`) and miss the reverse `a->b`, so a rename
    of a rename must be a new flat entry (`a->c`), never a chain."""
    import airuleset
    aliases = airuleset.STREAM_RENAME_ALIASES
    out = [name]
    if name in aliases:                       # old -> new
        out.append(aliases[name])
    else:
        for old, new in aliases.items():      # new -> old
            if new == name:
                out.append(old)
                break
    return out


def _slice_quals(user, cwd=None):
    """gh search quals for a reduced-authority stream's OWN ticket slice.
    Own-account streams (david/kvaskodev): assigned ∪ authored ∪ stream label.
    Shared-account boxes (gh login == the maintainer account): the stream
    LABEL alone — @me there matches the whole maintainer-authored backlog.
    App-token boxes (david2/david3/david4, #356): the stream LABEL alone,
    the SAME branch a shared-account box takes — an App installation token
    carries no user identity at all, so the assignee/author signal
    `assignee:@me`/`author:@me` would rely on is meaningless here, and the
    label is the only sound one. Detected via `_is_gh_app_token_box()`
    BEFORE `_gh_login()` is ever called, so this box never pays for (or
    depends on) a network call that is guaranteed to fail.

    #537: the `stream:<user>` label is EXPANDED via `_stream_rename_
    equivalents()` — a base-stream rename target (`montalu`/`montalu1`,
    `david`/`david1`, `simap`/`simap1`) carries BOTH its old and new label so
    old tickets keep matching during the transition, regardless of which name
    the box currently runs as. A non-renamed stream expands to itself, so its
    slice is byte-identical to before. The quals are UNIONED (one query per
    qual, `_slice_mine_and_handed`), so an extra alias label never narrows
    the result.

    Raises `SliceUnresolved` when the gh login cannot be resolved at all
    (#181 I-2) — an unresolvable identity cannot pick between those two
    branches, and guessing either one is a wrong answer on some box."""
    import airuleset
    labels = ["label:stream:" + n for n in _stream_rename_equivalents(user)]
    if _is_gh_app_token_box():
        return labels
    login = airuleset._gh_login(cwd)
    if login is None:
        raise SliceUnresolved(
            "gh api user failed — cannot tell whether this box authenticates "
            "as the maintainer account (slice = the stream LABEL alone) or as "
            "its own (assignee ∪ author ∪ label). Refusing to guess: the two "
            "branches disagree on every shared-account box.")
    if login == airuleset.MAINTAINER_GH_LOGIN:
        return labels
    return ["assignee:@me", "author:@me"] + labels


# An open, non-skip ticket carrying ANY of these labels is an obligation of the
# FULL-authority (core / gatekeeper) box even when it also carries a sub-dev
# `stream:<user>` label: only this box can perform the action they stand for.
#
# `needs-gatekeeper` = a stream→supervisor action request (cross-stream
# protocol rule 7 — by definition nobody else can do it).
#
# `ready-for-review` = a hand-off awaiting this box's review / merge / close
# (rule 4, and the fork-no-merge template's "CLOSED by the maintainer") —
# while it is open the full-authority loop HOLDS: review-watch, stay alive,
# re-check hourly, never end the loop ("neither side ever finishes while the
# other holds its ball") — so `core-quals --count` legitimately never
# reaching 0 while a hand-off sits open is CORRECT, and is NOT the
# never-stops failure the original ticket rejected.
#
# `prio:bounce` is DELIBERATELY NOT one of these labels (#307, 2026-08-07). It
# means the gatekeeper returned this ticket to the SUB-DEV with findings that
# need a fix — the SUB-DEV acts next, not this box, so a BARE open
# `prio:bounce` (no `ready-for-review`/`needs-gatekeeper` alongside it) is the
# sub-dev's own work: it does not block this box's obligation set, and
# letting the count reach 0 while the sub-dev fixes it is CORRECT, not a
# regression of the never-stops failure. Live evidence (odoo-erp,
# 2026-08-07): `core-quals --count` was inflated 63 -> 77 by 14 open
# `prio:bounce` tickets that belonged entirely to `stream:david` — and a
# full-authority `/goal` SELECTING from that inflated set could start
# IMPLEMENTING a sub-dev's bounce fix, violating the standing rule that the
# gatekeeper never patches a sub-dev's branch. A ticket carrying BOTH
# `prio:bounce` AND `ready-for-review` still counts — the hand-off is the
# live signal, matched by the `ready-for-review` qual above regardless of
# `prio:bounce`. The sub-dev's own `slice-quals` still includes its own
# `prio:bounce` tickets unaffected (they always also carry `stream:<user>`,
# which `_slice_quals()` already queries) — the two sides stay complementary.
MAINTAINER_ACTION_LABELS = ("needs-gatekeeper", "ready-for-review")


def _obligation_quals():
    """The per-qual search fragments whose UNION is a full-authority box's
    OBLIGATION set: the CORE slice, PLUS every open ticket only this box can
    action regardless of which stream owns it (#181 round 3, CRITICAL).

    `_core_search_excl()` is the FOOTER's *display* partition — "which
    population am I showing". Round 2 reused it as the `/goal` stop-proof's
    *obligation* partition — "which tickets must I finish before I may stop" —
    and those are not the same set. Measured on zbynekdrlik/odoo-erp
    2026-07-30: 83 open non-skip, 40 in the core partition, and 13 tickets
    outside it that only this box could move at the time (#2396 and #2377
    are `stream:montalu` + `needs-gatekeeper`, plus 11 open `prio:bounce`).
    The gatekeeper would close its 40, the proof would print 0, the loop
    would stop — leaving those tickets blocked on the very box that just
    stopped. That is #181 verbatim at a new address.

    #307 (2026-08-07) correction: `prio:bounce` is NOT one of
    MAINTAINER_ACTION_LABELS any more — see that tuple's own comment. All 11
    open `prio:bounce` tickets in the round-3 measurement above belonged
    entirely to `stream:david`, the sub-dev's own work; counting them
    inflated a real `core-quals --count` on the SAME repo from 63 to 77 a
    few days later, and let the obligation SELECTION path (`--list`) surface
    a ticket a full-authority `/goal` worker must never implement. The union
    below now excludes `prio:bounce` — only `needs-gatekeeper` and
    `ready-for-review` remain.

    This is NOT a revert to the whole-repo count the original ticket rejected
    (that was the never-stops failure): a stream ticket the sub-dev is
    actively working carries none of these labels and still does not block
    this box. Union in Python, one query per qual — gh's `--search` ANDs
    space-joined qualifiers ACROSS qualifier types and cannot OR them.

    Known residual, deliberate: a hand-off is detected by the
    `ready-for-review` LABEL (the same signal the footer's `gk` bucket uses,
    applied by the repo's own subdev-handoff-label workflow), not by the
    `READY-FOR-REVIEW:` comment that is its primary signal. The only
    single-query comment form is `"READY-FOR-REVIEW:" in:comments`, and
    GitHub tokenizes quoted phrases (the 2026-07-24 `in:title` false match),
    so it over-matches — and over-counting the obligation set is the
    never-stops failure again."""
    return [_core_search_excl()] + ["label:" + lb
                                    for lb in MAINTAINER_ACTION_LABELS]


def _repo_root(cwd=None, runner=None):
    """The git repo root for `cwd`, or "" when it cannot be resolved.

    ONE definition, so `cmd_tickets_status` (the footer), `cmd_slice_quals`
    and `cmd_core_quals` all resolve authority — and run gh — against the
    SAME root. #181 I-5: the CLI commands used a bare `resolve_authority()`,
    which reads `Path.cwd()/CLAUDE.md`, while the footer passes the repo
    root; a project marker `airuleset:authority=...` was therefore invisible
    to the CLI whenever the session cwd was a subdirectory, so the "ONE
    definition, resolved per box" claim held only when cwd was exactly the
    repo root.

    Carries #61's fallback: the session cwd may be the PARENT of the actual
    repo (montalu's ~/devel/odoo with the repo at ~/devel/odoo/odoo-erp) and
    `git rev-parse` only ever walks UPWARD. Exactly one `.git` subdirectory
    is descended into; 0 or >1 stays ambiguous — never guess."""
    import airuleset
    import subprocess

    cwd = cwd or os.getcwd()

    def _default_run(argv, cd):
        try:
            r = subprocess.run(argv, cwd=cd, capture_output=True, text=True,
                               timeout=20, env=airuleset._gh_env())
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    run = runner or _default_run
    root = run(["git", "rev-parse", "--show-toplevel"], cwd)
    if root:
        return root
    try:
        candidates = [p for p in Path(cwd).iterdir()
                      if p.is_dir() and (p / ".git").exists()]
    except OSError:
        candidates = []
    if len(candidates) == 1:
        return run(["git", "rev-parse", "--show-toplevel"], str(candidates[0]))
    return ""


def _ticket_is_stream_labeled(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...}
    dicts, or None/malformed) carries a stream:<user> label for any
    AUTHORITY_BY_USER stream — i.e. this ticket belongs to a sub-dev stream's
    slice, not the full-authority CORE slice (#164 defect 2: the D/T progress
    counter must not let a stream ticket's card inflate a core-scoped 'done'
    the core-scoped 'remaining' can't back).

    #537: recognition is EXPANDED via `_stream_rename_equivalents()` so a
    `stream:<name>` label is stream-owned when `<name>` is an
    AUTHORITY_BY_USER key OR a rename alias of one — this keeps historical
    `stream:montalu` tickets recognised even after the live-op END state
    removes the OLD base name from AUTHORITY_BY_USER (the new `montalu1` key
    expands back to `montalu`), so they never fall into the full-authority
    CORE slice."""
    import airuleset
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    recognized = {n for u in airuleset.AUTHORITY_BY_USER
                  for n in _stream_rename_equivalents(u)}
    return any(("stream:%s" % n) in names for n in recognized)


# The labels that mark a ticket as WAITING ON THE OWNER — the consolidated
# "on your court" family (#512, owner decision 2026-08-16 comment 5308903157):
# `needs-answer` (the ask-and-continue durable marker, pinged), `needs-decision`
# (a decision explicitly deferred TO the owner — a generic owner-decision label,
# #791: no sleep-window semantics), AND `needs-acceptance`
# (a hand-off the gatekeeper already processed — DONE, OPEN pending the owner's/
# client's acceptance, "done = client saw it", odoo-erp #3145). Such a ticket is
# genuine open work, but it is NOT this box's ACTIVE responsibility: the loop can
# do nothing with it until the OWNER acts (answers / decides / accepts), so it
# LEAVES the workable "I N" count and the /goal stop-proof's workable-0 proof —
# and surfaces SEPARATELY as `U N` + the stop-proof's user-waiting remainder, so
# nothing is hidden and the loop parks on it rather than claiming "backlog empty"
# past it (#468 for answer/decision; the user's directive 2026-08-14 "v I by
# nemali byť tie čo sú Q — nech je jasné kto za čo zodpovedá"; #512 folds
# `needs-acceptance` in so the owner sees "done, len sprocesovať odovzdanie" and
# "mám nezodpovedané otázky" as ONE "waiting on ME" number, not two letters).
#
# #512 supersedes #507's FOOTER placement of a BARE `needs-acceptance` ticket
# (was the stream's own workable `I N`) — it now lands in `U N`. #507's gk
# EXCLUSION mechanism (`GATEKEEPER_PROCESSED_LABELS`, the comment-fallback
# suppression in `_slice_mine_and_handed`) is UNTOUCHED, and #507's precedence
# invariant "a `needs-acceptance` ticket that is ALSO a re-hand-off/bounce stays
# gk/workable, never U" is preserved by the `needs-acceptance`-scoped override in
# `_row_is_user_waiting` (see NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS below).
#
# Deliberately DISTINCT from AUTOPILOT_SKIP_EXCL/ops-channel (which fully EXCLUDE
# a ticket from every consideration): a user-waiting ticket stays tracked,
# listed (`--waiting`) and counted (`U N`) — only PARTITIONED out of "mine to
# action right now". And distinct from `needs-design`/`question`/`blocked`
# (filing-time "needs input" labels that stay fully WORKABLE and get worked — the
# worker raises the question): these are applied ONLY AFTER a question has been
# raised (or, for needs-acceptance, after the work is DONE), so partitioning them
# never risks a question going unasked (skills/autopilot/SKILL.md's Step-1
# backlog-scope bullet, #468 reconciliation).
# #601: `needs-owner-action` — the OWNER must perform a physical/manual step
# (come to the rig, do a hardware action, be present). The owner is NEVER a
# third party, so an owner-blocked ticket belongs in `U` ("čo čaká na TEBA" —
# a question, an approval, OR a physical/manual step), NEVER in `W` (which is
# STRICTLY a third party / an external event MIMO ownera). Before #601 streams
# parked such a ticket as `ops-wait` → W (the only park label they knew), the
# doctrinal bug the owner ruled against (camera-box 2026-08-20). It is the
# LOWEST-precedence user-waiting label (see `_user_waiting_reason`): answer/
# decision/acceptance keep their #507/#526/#539 routing byte-exact, and `action`
# only decides routing/tag when it is the SOLE user-waiting label. It is cleared
# by the SUPERVISOR with evidence that the physical step was done (parallel to
# `ops-wait`), NOT by the Discord-answer auto-clear (watchdog job 32 keys on
# OWNER_DECISION_LABELS, which deliberately EXCLUDES it — an action is not a
# decision to re-ask as a daily "please answer").
USER_WAITING_LABELS = ("needs-answer", "needs-decision", "needs-acceptance",
                       "needs-owner-action")

# #512: the gk-processed / bounce labels that OVERRIDE a `needs-acceptance`
# ticket's routing to the `U N` user-waiting bucket. A `needs-acceptance` ticket
# is "waiting on the owner" (→ U) ONLY when it is NOT simultaneously:
#   - a genuine RE-hand-off (`ready-for-review`/`needs-gatekeeper`) → back in the
#     gatekeeper's court → stays `workable` so `_slice_mine_and_handed`'s
#     `handed`/gk logic counts it as `gk` (#507's "label wins" invariant), OR
#   - a returned bounce (`prio:bounce`) → reworkable by the stream → stays
#     `workable` with `handed=False` (the #313 bounce override) → the stream's I.
# A pure LABEL check, mirroring `_slice_mine_and_handed`'s own `label_handed`/
# `prio:bounce` checks, so the two derivations agree by construction. Scoped to
# `needs-acceptance` ONLY: `needs-answer`/`needs-decision` are not hand-off
# states, so their #468 routing (a handed + user-waiting row goes to U) is left
# byte-identical — this override applies solely to the one POST-hand-off label.
NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS = (
    "ready-for-review", "needs-gatekeeper", "prio:bounce")

# #507: labels that mark a hand-off the gatekeeper has ALREADY PROCESSED, so the
# ticket is no longer parked with the gatekeeper — it is back in the STREAM's
# court. `needs-acceptance` is the odoo-erp/montalu state applied when the
# gatekeeper reviews + merges a hand-off and moves it OUT of `ready-for-review`
# but leaves it OPEN pending client acceptance ("done = client saw it", odoo-erp
# #3145 — acceptance is the stream's own work, owner ruling 2026-08-15). The
# label-check below already gives such a ticket `handed=False` (it carries no
# `ready-for-review`/`needs-gatekeeper`), but the READY-FOR-REVIEW comment
# fallback (`_slice_mine_and_handed`, #313 pt 2) re-upgraded it forever off its
# stale, PERMANENT hand-off comment — the gatekeeper removes the LABEL when it
# processes a hand-off, but the COMMENT stays in history. So a ticket carrying
# any of these labels is EXCLUDED from the comment-fallback candidate walk: its
# stale hand-off comment can never re-flip it back to parked-with-gk.
#
# The common re-park case is still caught correctly: a genuine post-acceptance
# RE-hand-off normally carries a fresh `ready-for-review`/`needs-gatekeeper`
# LABEL (the repo's subdev-handoff-label workflow re-adds it server-side on the
# hand-off comment — live-observed on odoo-erp#3068), so `label_handed=True`
# and it is counted via the label-check BEFORE the fallback, never reaching this
# suppression.
#
# #507 review MAJOR (accepted, SAFE-direction residual — the claim is NOT
# absolute): the label is a DELIBERATELY unreliable hand-off signal (that is the
# whole reason the #313 comment fallback exists — a fork-no-merge label-add
# 403s, and the auto-labeller can itself be broken). So a re-hand-off that is
# COMMENT-ONLY (fresh READY-FOR-REVIEW comment, no fresh label added) AND leaves
# `needs-acceptance` in place is indistinguishable, by label alone, from a
# processed ticket with a stale comment — telling them apart needs a per-ticket
# timeline query (was the comment newer than the needs-acceptance labeling?),
# the exact cost this fix rejected (issue #507, ~1 extra gh call per candidate
# into the shared graphql bucket, #370). Such a ticket is UNDER-counted as gk
# (it carries no `ready-for-review`/`needs-gatekeeper` label). #622 changed WHERE
# it then lands: a bare `needs-acceptance` is queued for owner approval → `U`
# (leaves the workable `--count`), no longer the stream's own workable `I N` (the
# pre-#622 chained-I disposition). It is still SURFACED, not lost — it shows in
# `--waiting` (U) and the loop PARKS on it — but the safe-direction guarantee is
# narrower than #507/#508's "kept alive in the workable set": a bare
# needs-acceptance is NOT re-detected as gk by the READY-FOR-REVIEW comment
# fallback either (this label is in GATEKEEPER_PROCESSED_LABELS, so it is
# EXCLUDED from that candidate walk), so this comment-only re-hand-off self-heals
# ONLY when the repo auto-labeller re-adds `ready-for-review` (the #508 residual;
# a precise timeline-based fix is the tracked needs-user-decision follow-up).
#
# Streams without a needs-acceptance model simply never match — zero behaviour
# change there.
GATEKEEPER_PROCESSED_LABELS = ("needs-acceptance",)


def _row_is_user_waiting(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...}
    dicts, or None/malformed) marks the ticket as WAITING ON THE OWNER — i.e. it
    carries a USER_WAITING_LABELS label, WITH the #512 `needs-acceptance`-scoped
    gk/bounce override: a `needs-acceptance` ticket that ALSO carries a
    NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS label (a re-hand-off `ready-for-review`/
    `needs-gatekeeper`, or a returned `prio:bounce`) is NOT user-waiting — it
    stays `workable` so `_slice_mine_and_handed`'s `handed`/gk logic picks it up
    (#507's precedence: "label wins", preserved). `needs-answer`/`needs-decision`/
    `needs-owner-action` carry no such override (they are not hand-off states),
    so their routing is unchanged — `needs-owner-action` (#601) is
    user-waiting=True unconditionally (an owner-blocked physical step is always
    the owner's court; the acceptance-override applies to `needs-acceptance`
    only).

    A missing/unreadable `labels` value reads as NOT user-waiting (→ workable) —
    the SAFE side: never hide a ticket from THIS box's own responsibility because
    of a failed label read. The mirror of `_ticket_is_stream_labeled(None)` being
    False, and the OPPOSITE conservative direction from `_row_action`'s
    ownership check (there the harm is inviting foreign-code edits, so a failed
    read goes `action-only`; here the harm is hiding own work, so it stays
    workable) — both pick the non-harmful side of their own asymmetry."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for lb in USER_WAITING_LABELS:
        if lb not in names:
            continue
        if lb == "needs-acceptance" and any(
                ov in names for ov in NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS):
            continue   # re-hand-off / bounce overrides — stays workable (#507)
        return True
    return False


def _user_waiting_reason(labels):
    """The plain-word REASON a row is user-waiting — `answer` (needs-answer),
    `decision` (needs-decision), `acceptance` (needs-acceptance), or `action`
    (needs-owner-action, #601) — for the `--waiting` per-member reason tag
    (#512). Returns "" for a row that is not user-waiting (defensive; callers
    only pass the `user_waiting` bucket). A row carrying several user-waiting
    labels reports the FIRST by the fixed precedence answer > decision >
    acceptance > action, so the tag is deterministic.

    `action` is LAST on purpose (#601): answer/decision/acceptance have
    established routing semantics (#507/#526/#539) that MUST stay byte-exact, so
    `action` only decides the tag (and, via `_partition_workable`'s `else`
    branch, the routing) when needs-owner-action is the SOLE user-waiting label.
    A live owner QUESTION is the more time-sensitive of the two, so it is
    surfaced first when a ticket carries both."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for label, reason in (("needs-answer", "answer"),
                          ("needs-decision", "decision"),
                          ("needs-acceptance", "acceptance"),
                          ("needs-owner-action", "action")):
        if label in names:
            return reason
    return ""


# The label a stream/supervisor applies to an OPS-WAIT / evidence-gated ticket:
# open + technically "workable", but really BLOCKED on an external event or
# evidence (multi-day evidence collection, a foreign-session relaunch, an
# operational confirmation) with NO dispatchable code lane AND no automated
# completion signal (#510; concrete origin: #499-class ops tickets the #509
# lane-nudge surplus-floor comment already named as un-distinguishable). Advisory
# state the SUPERVISOR sets AND clears itself with evidence — never auto-applied.
#
# Mirrors the #468 U-bucket's SURFACE-ONLY treatment EXACTLY: like
# USER_WAITING_LABELS it LEAVES the workable `I N` / `core-quals --count` /
# lane-guard count (which runs those commands) and surfaces as its OWN `W N`
# footer bucket + `core-quals --ops-wait`; the loop PARKS on it (tracked, listed,
# never claimed backlog-empty past it, never blocks 🏁) — the only difference from
# the U-bucket being WHY it is parked (an external event/evidence vs the user's
# answer). UNLIKE the fully-EXCLUDED AUTOPILOT_SKIP_EXCL / ops-channel it stays
# tracked, listed and counted (in W N). A one-member tuple mirroring
# USER_WAITING_LABELS's shape, so a future second ops-wait synonym is a one-line add.
#
# 🏁/re-entry (resolved on #510, the user's 2026-08-16 decision "mirror the
# U-bucket exactly"): ops-wait LEAVES the count, so when it is the ONLY remaining
# backlog the 🏁 proof CAN fire and the loop disarms. Unlike the U-bucket (whose
# re-entry trigger is the user's routed Discord answer), ops-wait has no automatic
# re-entry — the SUPERVISOR clearing the label with evidence re-enters the ticket,
# a human action exactly parallel to the user answering a U-bucket question. The
# ticket is never lost (open + surfaced in W N / --ops-wait / the disarm report).
OPS_WAIT_LABELS = ("ops-wait",)


def _row_is_ops_wait(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...} dicts,
    or None/malformed) carries any OPS_WAIT_LABELS label. Same
    unreadable→workable SAFE-SIDE convention as `_row_is_user_waiting`: a
    missing/unreadable value reads as NOT ops-wait (→ workable), so a failed
    label read never hides a ticket from this box's own responsibility."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    return any(lb in names for lb in OPS_WAIT_LABELS)


def _ops_wait_reason(labels):
    """The plain-word REASON a row sits in the ops-wait (W) bucket, for the
    `--ops-wait` per-member tag (#526): `acceptance` when the row carries
    `needs-acceptance` AND is NOT simultaneously a re-hand-off/bounce
    (`NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS`) — routed to W by `_partition_workable`'s
    acceptance-scoped override; else `ops-wait` (a ticket parked on an external
    event/evidence via the `ops-wait` label). Lets a `--ops-wait` reader tell the
    two W populations apart.

    #539: `acceptance` now covers THREE sub-cases, all tagged identically (the
    doctrine names them in statusline-vocabulary.md #526 + skills/autopilot Step 1
    — a supervisor adds `ops-wait` WITH evidence in each): (1) the client
    acceptance thread was SENT and is waiting on the client to confirm; (2)
    FIX-CLASS — an owner-ruled no-thread close waiting on an EXTERNAL event (a
    foreign-repo fix), where no thread will ever be sent; (3) a deliberately
    DEFERRED thread, sent only after a future event (go-live). All three are
    "not the owner's court" (→ W, not U), which is why one tag serves them.

    A missing/malformed labels value reads as `ops-wait` (the safe generic
    tag). The gk-override exclusion mirrors `_row_is_user_waiting`'s own
    acceptance-scoping EXACTLY (#526 review 🔵): a contradictory
    `needs-acceptance`+`ready-for-review`/`needs-gatekeeper`/`prio:bounce`+
    `ops-wait` row (a re-hand-off/bounce that also carries ops-wait, reaching the
    ops_wait bucket via the plain `_row_is_ops_wait` branch, NOT the
    acceptance-override one) is tagged `ops-wait`, never mislabelled
    `acceptance`."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    if "needs-acceptance" in names and not any(
            ov in names for ov in NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS):
        return "acceptance"
    return "ops-wait"


def _partition_workable(rows, own_stream=None):
    """Split a `_union_open_issues`/`_slice_mine_and_handed` rows dict
    (`{number: {"number","title","createdAt","labels"}}`) THREE ways:
    `(workable, user_waiting, ops_wait)`. Both the user-waiting (#468) and the
    ops-wait (#510) buckets LEAVE `workable` — they are parked (on the user's
    answer / on an external event) and surface as the footer's `U N` / `W N`
    buckets and `--waiting` / `--ops-wait`, never in the workable count.

    ONE derivation, never independent queries: all three halves come from the
    SAME already-fetched rows, so the footer's `I N`/`U N`/`W N`, the /goal
    stop-proof's workable count, and the lane guard (which runs `core-quals`/
    `slice-quals --count`) cannot silently drift (#367/#468 lesson — the exact
    reason a search-exclusion + separate positive query was rejected). Extends
    the repo's own established client-side-partition pattern — no new mechanism.

    PRECEDENCE (#526, ROZHODNUTÉ v3): a row carrying BOTH a user-waiting AND an
    ops-wait label normally goes to `user_waiting` (a pending owner answer is the
    more actionable of the two) — EXCEPT a `needs-acceptance`-ONLY user-waiting
    row (reason == "acceptance": no needs-answer/needs-decision), which routes to
    `ops_wait` (W) instead. Once the stream has SENT the client acceptance thread
    and added `ops-wait`, the ticket is waiting on a THIRD PARTY, not a question
    for the owner — U is "čo sa ťa Claude pýta / čo máš schváliť", W is
    "odoslané, čaká tretia strana". needs-answer/needs-decision + ops-wait STAY
    in U (a pending owner answer beats a sent thread), so the override is
    acceptance-scoped. Both buckets leave `workable`, so the COUNT is identical
    either way; the precedence only decides which DISPLAY bucket (U vs W) the row
    lands in.

    #622 (owner directive 2026-08-22): a BARE `needs-acceptance` (no `ops-wait`,
    no gk-override) → U UNCONDITIONALLY. The code is merged and its only next step
    is an owner-approved client message, so it is never dispatchable-now code work
    (I = only that). This REVERSED #539's chained-I branch, which routed a bare
    needs-acceptance with no DELIVERED draft to `workable` (I) using "no delivered
    ping" as a proxy for "the stream's own chained work". #606 (2026-08-21) made
    that proxy wrong for the common case: with owner-questions delivered ONE AT A
    TIME, "no delivered ping" overwhelmingly means QUEUED-behind-others = waiting
    on the owner (→ U). The genuinely-chained case collapses into "queued in U"
    honestly (its dispatchable sibling work is its OWN ticket, still in I, so the
    loop never falsely disarms). The delivered-vs-queued distinction is now a
    DISPLAY tag only, computed on the on-demand `--waiting` path from
    `_acceptance_present_set` (delivered → `acceptance`, undelivered → `queued`) —
    it no longer routes, so this function is a PURE label partition again (no
    question-map read on the hot footer/count path).

    `needs-owner-action` (#601, the owner's own physical/manual step) routes to U
    via the `else` branch below WHEN it is the HIGHEST-precedence user-waiting
    label on the row — i.e. `_user_waiting_reason` reads `action` (no co-present
    needs-answer/needs-decision/needs-acceptance, all of which outrank it). In
    that normal case: (1) an owner-action + `ops-wait` row still lands in U
    (owner beats third-party framing — the owner is not a third party); and (2)
    it never enters the `ops_wait` bucket, so the #570 stale! W-freshness path can
    never touch it. (Since #622 a bare needs-acceptance is ALSO always U, so
    owner-action no longer differs from it on the I-vs-U axis — both are the
    owner's court.)

    Because `action` is the LOWEST precedence (deliberately, so needs-answer/
    needs-decision/needs-acceptance stay byte-exact per #507/#526), a
    PATHOLOGICAL row that ALSO carries a higher-precedence user-waiting label
    follows THAT label's routing, not action's: e.g. `needs-acceptance` +
    `needs-owner-action` + `ops-wait` reads reason `acceptance` and routes to W
    by the acceptance-scoped override (and the #507 gk-override a co-present
    `needs-acceptance` triggers applies too). Such a contradictory combo does not
    occur in practice — the byte-exact preservation of the co-present label's
    established semantics is the intended design, and a genuine owner-only-blocked
    ticket never carries a competing acceptance/answer label. The
    labelled-but-not-yet-announced defect is surfaced by the `no-action!` display
    flag (`_no_question_flagged` + `_print_issue_rows`), not by a routing gate.

    `own_stream` (#654): the box's OWN reduced-authority stream (its canonical
    AUTHORITY_BY_USER key, `_current_user()`), or None for a full-authority box.
    An ANSWER/DECISION/ACTION row owned by a FOREIGN stream (`_stream_owner_of`
    != own_stream) is routed to `workable` (action-only), NOT `user_waiting` —
    STREAM OWNERSHIP WINS for U routing (the ROZHODNUTÉ decision): a full-authority
    (gk) box never fields another stream's owner-question, its owning box does.
    Checked FIRST, so it beats the acceptance→W / U splits. A full box
    (own_stream=None) drops every such foreign row into I; a slice box keeps its
    OWN stream rows in its own U (owner == own_stream). SCOPED to answer/decision/
    action (the enumerated ROZHODNUTÉ reasons): needs-acceptance keeps its own
    #526/#622 routing (bare → U, sent-thread+ops-wait → W) — a foreign acceptance
    is search-excluded from the obligation set anyway, so it never reaches this
    branch on the gk box (the real leak path is answer/decision/action carrying a
    gk queue label, which have no gk-override). `stream:core`/bare/unreadable →
    `_stream_owner_of` == "" → not foreign → stays U (the box's own court)."""
    workable, user_waiting, ops_wait = {}, {}, {}
    for number, row in rows.items():
        labels = row.get("labels") if isinstance(row, dict) else None
        if _row_is_user_waiting(labels):
            reason = _user_waiting_reason(labels)
            # #654: a FOREIGN stream:<user> answer/decision/action row NEVER
            # enters THIS box's U — STREAM OWNERSHIP WINS (full contract + why
            # SCOPED away from needs-acceptance in the `own_stream` docstring
            # above). Checked FIRST, so it beats the acceptance→W / U splits.
            owner = _stream_owner_of(labels)
            if reason != "acceptance" and owner and owner != (own_stream or ""):
                workable[number] = row
            # #526: a needs-acceptance-ONLY user-waiting row (its acceptance
            # thread already sent, marked by the stream's `ops-wait`) is waiting
            # on the CLIENT, not the owner → route it to W. A pending owner
            # answer (needs-answer/needs-decision → reason != "acceptance")
            # keeps the row in U regardless of ops-wait. #622: every OTHER
            # user-waiting row — incl. a bare needs-acceptance queued for owner
            # approval, whether or not its draft was delivered — is the owner's
            # court → U.
            elif reason == "acceptance" and _row_is_ops_wait(labels):
                ops_wait[number] = row
            else:
                user_waiting[number] = row
        elif _row_is_ops_wait(labels):
            ops_wait[number] = row
        else:
            workable[number] = row
    return workable, user_waiting, ops_wait


def _acceptance_present_set(rows, cwd=None, home=None):
    """The set of BARE `needs-acceptance` issue numbers in `rows` whose
    acceptance DRAFT has been DELIVERED — a question-map ping references `#N`, the
    "presented for the owner's approval" signal. #622 REPURPOSED this from the
    #539 routing gate to a DISPLAY-only signal: a bare needs-acceptance is now
    always U (`_partition_workable` no longer takes an `acceptance_present`
    param), and this set only decides the `--waiting` reason TAG — a member IN the
    set is a live owner-approval question (tagged `acceptance`), a member NOT in it
    is QUEUED awaiting #606 one-at-a-time delivery (tagged `queued`).

    Deliberately the question map ALONE (`statusbar.question_map_ticket_refs`,
    local, no gh) — a presented draft ALWAYS fires a ❓ ping
    (`notify.record_question`), so the map is the authoritative "owner was
    actually asked" signal, and reading it costs no gh (#370). It runs ONLY on the
    on-demand `--waiting` display path now, never the hot footer/`/goal`-count
    path (which no longer computes this at all — #622). `cwd` (the caller's repo
    root) SCOPES the map read to THIS project — a MUST on a multi-repo box, where
    issue numbers collide across repos (#539 review MAJOR-1); every production call
    site passes its resolved root.

    Fail-safe: an UNREADABLE map (`question_map_ticket_refs` → None) returns ALL
    bare-needs-acceptance numbers — the conservative DISPLAY default (show them all
    as `acceptance`/delivered rather than falsely tagging a possibly-delivered
    draft `queued`). A readable-but-ABSENT map yields an empty ref set, so a bare
    needs-acceptance with no draft is correctly tagged `queued`. Only bare
    needs-acceptance rows are considered — a needs-answer/needs-decision row (→ U
    by label) and a needs-acceptance+ops-wait row (→ W) are never in the returned
    set, so the display tag never touches them."""
    import statusbar
    bare = set()
    for number, row in rows.items():
        labels = row.get("labels") if isinstance(row, dict) else None
        if (_row_is_user_waiting(labels)
                and _user_waiting_reason(labels) == "acceptance"
                and not _row_is_ops_wait(labels)):
            bare.add(number)
    try:
        refs = statusbar.question_map_ticket_refs(cwd, home)   # #539 MAJOR-1: cwd-scoped
    except Exception:
        refs = None
    if refs is None:
        return bare                              # unreadable map -> all stay U
    return {n for n in bare if n in refs}


def _partition_user_waiting(rows):
    """Back-compat 2-way split `(workable, waiting)` by USER_WAITING_LABELS,
    where `workable` still includes any ops-wait rows (the caller that needs the
    3-way split — footer/count/lane — uses `_partition_workable` directly). Kept
    as a thin delegate over `_partition_workable` so the user-waiting label logic
    has ONE definition (#468). `workable | ops_wait` is byte-for-byte the pre-#510
    behaviour for any caller that only cares about the user-waiting axis."""
    workable, waiting, ops_wait = _partition_workable(rows)
    return {**workable, **ops_wait}, waiting


# #539: the repo's OWN ask-flow markers — the shape a genuine owner-question
# comment takes (`❓ NEEDS YOU`/`❓ ASKED`, the `**Otázka …:**` block head, or a
# plain "otázka" mention). Deliberately NOT a bare `?`: a routine gatekeeper /
# review comment carries a `?` incidentally ("does this look right?"), so keying
# on `?` would make the `no-question!` tag toothless (never fire for the very
# fix-class acceptance tickets this exists to catch). Keying on the ask-flow
# vocabulary gives it teeth while staying SAFE — a real question comment always
# carries one of these (user-questions-slovak.md's hook-enforced template).
_ASK_MARKER_RE = re.compile(r"❓|ot[áa]z|needs you|asked", re.IGNORECASE)


def _comment_carries_question(body):
    """True if `body` (an issue comment body, or None/non-str) is a real
    OWNER-question comment — it carries one of the repo's ask-flow markers
    (`_ASK_MARKER_RE`). The durable-record corroboration for the #539
    no-question check: when a per-ticket ask-and-continue ping omitted `#N` from
    its block (the #512 map-dedup residual), the question map misses it, but the
    ticket's own question comment still proves the owner was asked — which is
    what keeps the `no-question!` tag from FALSELY accusing such a member.

    Marker-based, so the "nikdy falošný" guarantee is STRUCTURAL, not
    phrasing-level (#539 review MINOR-1): a false accusation is prevented by the
    map-unreadable / gh-failure fail-safes in the callers, NOT by this predicate
    recognizing every conceivable question phrasing. This repo's owner questions
    are the hook-enforced `**Otázka …:**` / `❓ NEEDS YOU`/`ASKED` template
    (`user-questions-slovak.md`), which always match; a bare English "can you
    decide X?" with no marker would not (accepted residual, safe direction — the
    tag is a display hint, and the caller's fail-safes are the real guarantee)."""
    if not isinstance(body, str):
        return False
    return bool(_ASK_MARKER_RE.search(body))


def _issue_question_comment_state(number, cwd=None):
    """The delivered-question COMMENT state of issue `number`, as the #539
    no-question fallback (used only for a `U` member the question map did not
    already cover). Returns:

      - True  -> a question-shaped comment (`_comment_carries_question`) exists,
      - False -> the gh fetch SUCCEEDED and found none,
      - None  -> the gh fetch FAILED / was unusable — the caller then does NOT
        flag this member (fail-safe: never a false accusation off a failed gh
        call, "nikdy falošný", #539).

    `airuleset._gh_out` returns "" on ANY failure OR empty result, but a
    successful `gh issue view <n> --json comments` always prints a JSON object
    (`{"comments": [...]}`, non-empty even with zero comments), so "" is
    unambiguously a FAILURE here → None. `gh issue view` is a graphql call
    (#370); it runs at most once per map-uncovered `U` member, only on the
    on-demand `--waiting` listing path (never the hot footer refresh)."""
    import airuleset
    raw = airuleset._gh_out("issue", "view", str(number), "--json", "comments",
                            cwd=cwd, timeout=15)
    if not raw:
        return None                              # gh failed -> fail-safe
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None                              # unparseable -> fail-safe
    comments = obj.get("comments") if isinstance(obj, dict) else None
    if not isinstance(comments, list):
        return None                              # unexpected shape -> fail-safe
    for c in comments:
        body = c.get("body") if isinstance(c, dict) else None
        if _comment_carries_question(body):
            return True
    return False


def _no_question_flagged(rows, cwd=None, home=None, comment_state_fn=None):
    """The set of `U`-member issue numbers to tag `no-question!`/`no-action!`
    (#539, mechanizing the #527 invariant "U > 0 ⟹ every U member carries a
    DELIVERED question/notice"). For a `needs-owner-action` member (#601) the
    "delivered question" IS a delivered ACTION notice — the SAME shape (a ❓ ping
    in the map referencing `#N`, or a ❓-marked comment naming the step), so this
    function needs NO action-specific branch; the display side (`_print_issue_
    rows`) renders the flag as `no-action!` for an `action`-reason member. For
    each member of `rows` (the `user_waiting` bucket):

      - covered if the question map references `#N`
        (`statusbar.question_map_ticket_refs`, the AUTHORITATIVE delivered-ping
        signal — cheap, local, no gh), OR
      - covered if a question-shaped comment exists on it (`comment_state_fn`,
        default `_issue_question_comment_state`, a bounded gh fallback run ONLY
        for a map-uncovered member — so a healthy stream pays ZERO gh calls and
        only the defect population incurs one).

    A member covered by NEITHER — and only when we are CONFIDENT of that — is
    flagged. Every uncertainty resolves toward NOT flagging (never a false
    accusation, "nikdy falošný"):
      - the map is UNREADABLE (corrupt) -> return an empty set, tag NOTHING (a
        delivered ping we cannot see must never be called absent);
      - a member's gh comment fetch FAILS (None) -> that member is not flagged.
    An ABSENT map (never pinged) reads as an empty ref set (readable), so its
    members ARE checked via the comment fallback — that is the montalu3 defect
    this catches, not a fail-safe case.

    #622: an `acceptance`-reason member is EXEMPT — a bare needs-acceptance is
    QUEUED for one-at-a-time (#606) owner-approval delivery, so a not-yet-delivered
    draft is a legitimate reason for non-delivery, not a forgotten question ("no-
    question! defekt by sa na queued člena nevzťahoval", owner). This REVISES the
    #527 invariant: U>0 ⟹ every U member is a delivered question/notice OR a queued
    acceptance awaiting #606 delivery. answer/decision/action still flag (they
    SHOULD carry a delivered question/notice); a DELIVERED acceptance is already
    ref-covered, so exempting the whole acceptance reason only spares the queued
    ones."""
    import statusbar
    try:
        refs = statusbar.question_map_ticket_refs(cwd, home)   # #539 MAJOR-1: cwd-scoped
    except Exception:
        refs = None
    if refs is None:
        return set()                             # unreadable map -> tag NOTHING
    check = comment_state_fn or _issue_question_comment_state
    flagged = set()
    for number, row in rows.items():
        if number in refs:
            continue                             # delivered ping references it
        labels = row.get("labels") if isinstance(row, dict) else None
        if _user_waiting_reason(labels) == "acceptance":
            continue                             # #622: queued acceptance is exempt
        try:
            state = check(number, cwd)
        except Exception:
            state = None
        if state is False:
            flagged.add(number)                  # ok fetch, no question -> flag
        # True (has a question) or None (fetch failed) -> never flag
    return flagged


# #570 — W (`ops-wait`) freshness: a parked W ticket the stream has not PUSHED
# on within OPS_WAIT_EVIDENCE_MAX_S is `stale!`-tagged in `--ops-wait`, so the
# footer/stop-proof reader and job 20's re-check nudge both see WHICH parked
# tickets have gone cold (the "tlač dopredu každý deň" doctrine, #570 bod 3).
# #607: the window is 24h of WORKING time — Saturday/Sunday (Europe/Bratislava)
# do NOT count toward it (`working_time.working_deadline_passed`), so a Friday
# reminder's deadline lands Monday afternoon, not Saturday.
OPS_WAIT_EVIDENCE_MAX_S = 24 * 3600
# Bound on the per-member `gh issue view` comment fetches per `--ops-wait`
# invocation (a real W set is a handful — montalu's worst incident was 13; a
# >25-member W is pathological). Overflow members are left UNTAGGED (the safe
# direction — never a false accusation). This keeps the watchdog's
# `_watchdog_ops_wait_fetch` subprocess (which runs `--ops-wait`) bounded so its
# 35s timeout has margin (~25 × <1s), alongside its own 30-min `_cached_ops_wait`
# cache; the session's own on-demand `--ops-wait` is uncapped in wall-clock time
# so it always gets full stale info. TRADE-OFF (#570 review 🔵): the stale
# computation is COUPLED into the SAME `--ops-wait` invocation the #547 W-nudge
# reads, so a pathological large-W + slow-gh cold-cache sweep can time the
# subprocess out → None → the W-clause of that day's partition nudge is dropped;
# it SELF-HEALS (the 60s `_cached_ops_wait` fail_ttl re-checks, and the I-clause
# still fires while I>0), so this is a bounded, fail-safe degradation, not a
# correctness bug. The cap + a modest 35s timeout keep it rare.
OPS_WAIT_STALE_MAX_FETCHES = 25

# #754 — the W-drain threshold. The goal state of the /goal loop is I0 ∧ U0 ∧
# W0: W is a DEBT bucket, not a terminal ticket state. When the parked-W set
# exceeds this size the CLI `--ops-wait` summary line flags `OVER-THRESHOLD` and
# the job-20 nudge escalates to an aggregate W-OVERFLOW clause (drain the bucket
# BEFORE new I work; if it cannot be consolidated, summarise to the owner ❓).
# CANONICAL value — the watchdog's local `WDRAIN_ESCALATE_N` is locked equal to
# this by tests/test_wdrain_lane_754.py so the CLI marker and the escalation fire
# at the SAME |W| (live incident: odoo-erp montalu3 grew to W 34 unchecked).
OPS_WAIT_WDRAIN_THRESHOLD = 8

# #818 — the #799 N=3 tacit-acceptance window in WORKING seconds (Sat/Sun in
# Europe/Bratislava excluded, via `working_time.working_deadline_passed`, the
# SAME weekend semantics `stale!` uses). A delivered+reminded client-acceptance W
# member that is inside this window is NOT `stale!`-flagged and NOT nudged for a
# second reminder (`tacit-wait`); once the window has ELAPSED it is a tacit-close
# CANDIDATE (`tacit-close?`). #799 forbids a second reminder in the window — the
# terminal action is a TACIT CLOSE — so on days 2–3 the mechanical layer must
# stop fighting the doctrine (a red `stale!` + a "remind DNES" nudge).
TACIT_WINDOW_WORKING_S = 3 * 24 * 3600

# #818 — the falsifiable "final reminder was SENT" signal: a dedicated,
# LINE-ANCHORED ticket marker `Acceptance-reminder: <msg-id>` on the
# final-reminder W-push comment (design Prístup 2, the `Acceptance-cited:`/
# `Acceptance-tacit:` family). Line-anchored (`^`, MULTILINE) so an INLINE or
# quoted (`> …`) mention of the phrase does NOT match — a client message never
# carries a TICKET marker at the start of a line. The trailing COLON is REQUIRED
# (review 🟡, both reviewers): it matches the `Acceptance-cited:`/`Acceptance-
# tacit:` family EXACTLY and rejects a line-starting PROSE mention
# (`Acceptance-reminder este neposlaná…`) or a hyphenated derivative
# (`Acceptance-reminder-draft: …`) that a bare `\b` would have falsely accepted
# — the dangerous false-OPEN direction. Matched case-insensitively; the `msg
# <id>` on the marker line doubles as the #753 citation, so `own_cited` and the
# window-opener are the same comment. RESIDUAL (accepted, bounded): the marker
# at line-start INSIDE a fenced code block still matches — but the fenced draft
# is the CLIENT message, which never carries a ticket marker, and `tacit-close?`
# is a session-verify CANDIDATE (the nudge mandates re-reading the msg-id to
# confirm the reminder was SENT before any close).
_FINAL_REMINDER_RX = re.compile(r"(?im)^[ \t]*Acceptance-reminder[ \t]*:")


def _comment_is_final_reminder(body):
    """True iff `body` carries the #818 line-anchored `Acceptance-reminder:`
    marker (the tacit-window opener). None/empty/non-str → False."""
    if not isinstance(body, str) or not body.strip():
        return False
    return bool(_FINAL_REMINDER_RX.search(body))


def _parse_iso_ts(s):
    """Epoch seconds for an ISO-8601 `createdAt` (gh renders `...Z`), or None on
    any unparsable/absent value (fail-safe — an unmeasurable timestamp is simply
    ignored, never guessed)."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


# #753 — a source citation the W-push doctrine mandates: a version, a release
# stage, an Odoo Discuss thread/msg id, or a `#N` ticket/PR reference. A
# content-free "still waiting" push carries none. Erring slightly LOOSE (any real
# anchor counts) is the #539 never-false-accuse direction — a too-tight test
# would flag a validly-pushed ticket as stale. The 3-part-semver arm's components
# are bounded `\d{1,3}` so a 4-digit-year date ("30.8.2026") no longer matches
# (`2.226.0` still does — the #698 live-probe lesson); RESIDUAL (accepted, the
# fail-safe-loose direction): a 2-digit-year date ("30.8.26") or an IP
# ("100.82.64.27") can still match — rare, and matching over-generously only
# UNDER-flags (never a false accusation).
_CITATION_RX = re.compile(
    r"#\d+"                                       # a ticket / PR reference
    r"|discuss\.channel_\d+|\bchannel[_ ]\d+"     # an Odoo Discuss thread
    r"|\bmsg\b[\s:#-]*\d{3,}|\bmessage\b[\s:#-]*\d{3,}"  # a message id
    r"|\bstage-\d+\b"                             # a release stage (#578/#588)
    r"|\bv\d+\.\d+(?:\.\d+)*\b"                   # a v-prefixed version
    r"|\b\d{1,3}\.\d{1,3}\.\d{1,3}\b",            # a 3-part semver (date-bounded)
    re.IGNORECASE)


def _comment_has_citation(body):
    """True iff `body` cites a source per the #753 W-push doctrine (version /
    Discuss thread or msg-id / `#N` ref). None/empty/non-str → False."""
    if not isinstance(body, str) or not body.strip():
        return False
    return bool(_CITATION_RX.search(body))


def _norm_ages(res):
    """Normalize `_issue_comment_ages` output (or an injected fake) to the
    `{own, any, own_cited, own_oldest, own_final_reminder}` dict (#753; #818
    added `own_final_reminder`). A legacy 2-tuple `(own, any)` — the #699/#607
    fakes — carries no body/citation info, so its `own` is treated as the CITED
    anchor (`own_cited = own`) and `own_final_reminder` is None (no tacit window
    — the fail-safe direction), which reproduces the PRE-#753 tuple semantics
    EXACTLY (own was the freshness anchor) — a legacy own counts as a valid push,
    never a false accusation. None/malformed → None (fail-safe, no flag)."""
    if res is None:
        return None
    if isinstance(res, dict):
        # #818: a dict from an older `_issue_comment_ages` (no `own_final_
        # reminder` key) reads as no marker → no tacit window (the fail-safe
        # direction), so tolerate it via `.setdefault` rather than requiring it.
        # NB this MUTATES the dict in place — deliberate: the only callers pass a
        # throwaway result or a per-sweep `ages_cache` dict whose sibling readers
        # (stale: own_cited/own_oldest/any; recheck: own) never read this key, so
        # the in-place default is safe + idempotent, never a copy-on-read
        # dependency (review 🔵 B).
        res.setdefault("own_final_reminder", None)
        return res
    if isinstance(res, (tuple, list)) and len(res) >= 2:
        return {"own": res[0], "any": res[1],
                "own_cited": res[0], "own_oldest": None,
                "own_final_reminder": None}
    return None


def _stream_self_login():
    """THIS box's own gh identity in the FORM a `gh issue view --json comments`
    comment `author.login` renders it (#463): the fixed App-bot login on an
    App-token box (NO network call — `gh api user` 403s there structurally), the
    real gh login on a PAT box, or None when unresolvable. None is not fatal:
    `_stale_ops_wait_flagged` degrades to the any-comment definition (the SAFE
    direction — it under-flags rather than false-accuse)."""
    import airuleset
    if _is_gh_app_token_box():
        return airuleset.STREAM_APP_BOT_LOGIN
    return airuleset._gh_login()


def _issue_comment_ages(number, self_login, now, cwd=None):
    """Evidence ages for issue `number`, the #570 freshness fallback (#753
    extends it citation-aware; #818 adds the tacit-window opener). Returns the
    DICT `{own, any, own_cited, own_oldest, own_final_reminder}`:
      - `own`      — createdAt of the newest comment authored by `self_login`;
      - `any`      — createdAt of the newest comment of ANY author;
      - `own_cited`— createdAt of the newest own comment that CITES a source
                     (`_comment_has_citation` — the #753 reset anchor);
      - `own_oldest`— createdAt of the OLDEST own comment (sustained-engagement
                     proxy — the montalu3 bare-push case);
      - `own_final_reminder` — createdAt of the newest own comment carrying the
                     #818 line-anchored `Acceptance-reminder:` marker (the #799
                     tacit-window opener); None when no reminder was recorded.
    Each is None when absent. Returns None (the WHOLE dict) when the gh fetch
    FAILED or was unusable → the caller does NOT flag (fail-safe, "nikdy
    falošný", #539). The DICT (over the pre-#753 2-tuple) is the extensible
    shape the #698 lesson prefers; `_norm_ages` keeps legacy 2-tuple fakes
    working, so the #699/#607 tests are untouched.

    `airuleset._gh_out` returns "" on ANY failure OR empty result, but a
    successful `gh issue view <n> --json comments` always prints a JSON object
    (`{"comments": [...]}`, non-empty even with zero comments), so "" is
    unambiguously a FAILURE here → None. The `--json comments` payload already
    carried `body` (the gh invocation is UNCHANGED) — #753 READS it to detect
    each own comment's citation, #818 additionally for the `Acceptance-reminder:`
    marker; `now` is unused for the read itself (passed for signature symmetry
    with the injectable seam the caller uses)."""
    import airuleset
    raw = airuleset._gh_out("issue", "view", str(number), "--json", "comments",
                            cwd=cwd, timeout=15)
    if not raw:
        return None                              # gh failed -> fail-safe
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None
    comments = obj.get("comments") if isinstance(obj, dict) else None
    if not isinstance(comments, list):
        return None
    own_ts = any_ts = own_cited = own_oldest = own_final_reminder = None
    for c in comments:
        if not isinstance(c, dict):
            continue
        ts = _parse_iso_ts(c.get("createdAt"))
        if ts is None:
            continue
        if any_ts is None or ts > any_ts:
            any_ts = ts
        author = c.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if self_login and login == self_login:
            body = c.get("body")
            if own_ts is None or ts > own_ts:
                own_ts = ts
            if own_oldest is None or ts < own_oldest:
                own_oldest = ts
            if _comment_has_citation(body) and (
                    own_cited is None or ts > own_cited):
                own_cited = ts
            # #818: the newest own comment carrying the line-anchored
            # `Acceptance-reminder:` marker is the tacit-window opener.
            if _comment_is_final_reminder(body) and (
                    own_final_reminder is None or ts > own_final_reminder):
                own_final_reminder = ts
    return {"own": own_ts, "any": any_ts,
            "own_cited": own_cited, "own_oldest": own_oldest,
            "own_final_reminder": own_final_reminder}


def _stale_ops_wait_flagged(rows, cwd=None, now=None, self_login=None, ages_fn=None):
    """The set of ops-wait (W) member numbers to tag `stale!` (#570) — a parked
    W ticket the stream has NOT pushed on within OPS_WAIT_EVIDENCE_MAX_S (24h of
    WORKING time; Sat/Sun in Europe/Bratislava excluded, #607 —
    `working_time.working_deadline_passed`).

    Evidence = the last CITED own push (#753). The daily push / third-party
    reminder / blocker re-verification the W doctrine mandates IS a ticket
    comment (#570 bod 3), but a content-free "čakáme" comment reset the 24h
    clock without the session ever reading the source (the montalu3 W-34
    degeneration) — so ONLY a comment that CITES a source (`_comment_has_
    citation`: a version / Discuss thread or msg-id / `#N` ref) counts as a
    reset. A member is flagged iff the gh fetch SUCCEEDS AND its freshness
    ANCHOR is older than 24h working time, where the anchor is, in order:
      - `own_cited` — the newest CITED own push (the mechanized "valid push"), else
      - `own_oldest` — the OLDEST own comment (the stream has engaged only with
        BARE pushes; if that engagement is itself >24h old the ticket is stale —
        the montalu3 sustained-bare-push case), else
      - `any` — the newest comment of ANY author (the pre-#753 untouched-park
        fallback: the stream never engaged and even the newest comment is old).

    Never a false accusation ("nikdy falošný", the #539 no-question! pattern):
    a gh failure / unusable read (ages_fn → None or `_norm_ages` → None), zero
    comments at all (every anchor None), or a member beyond
    OPS_WAIT_STALE_MAX_FETCHES is NOT flagged. RESIDUAL (documented, fail-safe):
    a freshly-parked ticket with a single BARE own comment <24h old (own_cited
    None, own_oldest recent) is NOT flagged — its park age is not in this fetch,
    so it under-flags rather than false-accuse (the watchdog's `w_seen`
    per-ticket park age could disambiguate, but that is not on this path). When
    `self_login` is unresolvable (None — a PAT-box gh hiccup), `_issue_comment_
    ages` resolves NO own comment (own/own_cited/own_oldest all None) so the check
    DEGRADES to the `any` fallback — the safe direction, a false negative, never
    a false positive.

    Deliberately keyed on the CITED own push, NOT "last comment at all": a
    third party's reply — or the stream's own bare "čakáme" — would otherwise
    reset the clock and HIDE the very staleness this exists to surface.

    #818 NB: this function does NOT know about the #799 tacit window — a
    delivered+reminded acceptance member here still flags `stale!`. The tacit
    EXEMPTION happens one layer UP, in `cli_quals_cmd._ops_wait_flag_sets`, which
    subtracts `_tacit_window_flagged(...)` from this set before rendering. The
    only production caller is that flag-sets function; the watchdog consumes the
    already-subtracted CLI `--ops-wait` reason column. A future DIRECT caller of
    the facade `airuleset._stale_ops_wait_flagged` would get an UN-subtracted set
    (and must apply the tacit exemption itself)."""
    now = time.time() if now is None else now
    if self_login is None and ages_fn is None:
        self_login = _stream_self_login()
    ages = ages_fn or (lambda n: _issue_comment_ages(n, self_login, now, cwd))
    flagged = set()
    for number in sorted(rows)[:OPS_WAIT_STALE_MAX_FETCHES]:
        try:
            res = ages(number)
        except Exception:
            res = None
        res = _norm_ages(res)
        if res is None:
            continue                             # gh failed / unusable -> no flag
        own_cited = res.get("own_cited")
        own_oldest = res.get("own_oldest")
        any_ts = res.get("any")
        # #753: reset only on a CITED own push; else the oldest own (bare)
        # engagement; else the newest any-author comment (untouched park).
        anchor = (own_cited if own_cited is not None
                  else own_oldest if own_oldest is not None else any_ts)
        # #607: the 24h window is WORKING days — Saturday/Sunday (Europe/
        # Bratislava) do not count, so a Friday-parked ticket is not falsely
        # flagged over the weekend (the shared `working_time` helper, used by
        # the gk-lane freshness push too). tz-error fails safe to the flat span.
        if anchor is not None and working_time.working_deadline_passed(
                anchor, now, OPS_WAIT_EVIDENCE_MAX_S):
            flagged.add(number)
        # zero comments (every anchor None) -> ambiguous -> never flag (safe)
    return flagged


def _tacit_window_flagged(rows, cwd=None, now=None, self_login=None,
                          ages_fn=None):
    """#818 — the (`tacit_wait`, `tacit_close`) member sets for a delivered +
    reminded client-acceptance W ticket inside / past its #799 N=3 tacit-
    acceptance window. A member is classified iff ALL hold (else it is left in
    NEITHER set — the fail-safe UNTAGGED direction, #539/#570 never-false-accuse):

      - its REASON is `acceptance` (`_ops_wait_reason(labels)`) — a client-
        thread-parked member. A pure `ops-wait` (release/event) member cannot
        tacitly land by client silence — it has the #698/#699 machinery instead;
      - the gh comment fetch SUCCEEDS and yields `own_final_reminder` (the newest
        own comment carrying the #818 `Acceptance-reminder:` marker — the
        window-opener); no marker → NEITHER set (stale! stands);
      - NEWEST-CITED GUARD: no own CITED push is strictly NEWER than the final
        reminder (`own_cited <= own_final_reminder`). A later cited push proves
        the session re-engaged (the client replied on Discuss, invisible here) →
        the member has left the tacit state machine → normal #570 handling.

    In-window (`working_deadline_passed(own_final_reminder, now,
    TACIT_WINDOW_WORKING_S)` FALSE) → `tacit_wait`; past-window (TRUE) →
    `tacit_close` (a tacit-close CANDIDATE the SESSION judges + closes with
    evidence — the watchdog NEVER auto-closes or auto-unlabels). Shares the
    caller's per-member comment fetch via `ages_fn` (ZERO new gh); a gh failure
    / unusable read leaves the member in NEITHER set. Reuses
    `working_time.working_deadline_passed` (weekend-aware, #607) so the CLI and
    the watchdog subprocess classify identically."""
    now = time.time() if now is None else now
    if self_login is None and ages_fn is None:
        self_login = _stream_self_login()
    ages = ages_fn or (lambda n: _issue_comment_ages(n, self_login, now, cwd))
    tacit_wait = set()
    tacit_close = set()
    for number in sorted(rows)[:OPS_WAIT_STALE_MAX_FETCHES]:
        row = rows[number] if isinstance(rows, dict) else None
        labels = row.get("labels") if isinstance(row, dict) else None
        if _ops_wait_reason(labels) != "acceptance":
            continue                             # client-acceptance-scoped only
        try:
            res = ages(number)
        except Exception:
            res = None
        res = _norm_ages(res)
        if res is None:
            continue                             # gh failed / unusable -> no flag
        ofr = res.get("own_final_reminder")
        if ofr is None:
            continue                             # no reminder recorded -> stale! stands
        own_cited = res.get("own_cited")
        if own_cited is not None and own_cited > ofr:
            continue                             # re-engaged after the reminder
        if working_time.working_deadline_passed(ofr, now,
                                                TACIT_WINDOW_WORKING_S):
            tacit_close.add(number)              # window elapsed -> tacit-close?
        else:
            tacit_wait.add(number)               # in window -> tacit-wait
    return tacit_wait, tacit_close


# #699 — RELEASE-parked W freshness: the TIGHT hourly cadence, distinct from the
# 24h `stale!` third-party-reminder window. Owner ruled (2026-08-25) that a
# release-parked ops-wait member must be deployed-state re-checked (#588) by the
# OWNING session at EVERY work cycle, min 1x/hour — never left to the daily job-20
# backstop — because releases land ~5x/day and a day-latency unpark means "cely
# den stojime". This tag SURFACES which release-parked members are OVERDUE for
# that re-check. It makes NO "landed" claim (that stays the #698 proof-only
# train-drained clause) — only "a re-check is overdue".
RELEASE_RECHECK_MAX_S = 3600
# The release-SHAPED title regex — kept BYTE-IDENTICAL to
# `watchdog.ops_wait_recheck._RELEASE_SHAPED_RX` (#698, live-probed) so the tag
# and the job-20 nudge classify the SAME titles (a drift-lock test asserts the two
# `.pattern`s equal). Duplicated, not imported, to keep cli_quals free of a
# watchdog import; the release-shaped token set never varies between the two.
_RELEASE_RECHECK_TITLE_RX = re.compile(
    r"(?i)(?:\breleas|\bvydan|\bnasaden|\bdeploy|\bstage-\d+\b|"
    r"\bv\d+\.\d+(?:\.\d+)*\b)")


def _release_recheck_flagged(rows, cwd=None, now=None, self_login=None,
                             ages_fn=None):
    """The set of RELEASE-parked ops-wait (W) member numbers to tag `recheck!`
    (#699) — a RELEASE-shaped member (TITLE names a release/version/stage,
    `_RELEASE_RECHECK_TITLE_RX`) the OWNING session has NOT re-checked within
    RELEASE_RECHECK_MAX_S (1h of WORKING time, #607 — weekend-excluded via
    `working_time.working_deadline_passed`). Freshness = the newest OWN comment
    age, the SAME `_issue_comment_ages` evidence `_stale_ops_wait_flagged` reads
    (share the `ages_fn` seam at the call site → one gh fetch per member for both
    tags).

    Never a false accusation (the #539/#570 fail-safe bias, STRICTER than
    `_stale_ops_wait_flagged` — NO any-comment fallback): a gh failure / unusable
    read (ages_fn → None), a non-release title, NO own comment at all (own_ts None
    — the session's re-check evidence is an OWN comment, so its absence is
    ambiguous, never proof of a missed re-check), or a member beyond
    OPS_WAIT_STALE_MAX_FETCHES is left UNTAGGED. The primary mechanism is the
    session DUTY (#699 doctrine) + the job-20 backstop, so the tag only ever
    UNDER-flags — barring a total tz/zoneinfo failure, where the shared
    `working_time` helper degrades to a flat weekend-inclusive span (the #570
    stale! baseline's own accepted fallback; benign here — a re-check nudge, not
    an accusation). Makes NO release-train / "landed" claim (that stays the #698
    proof-only clause) — only "re-check overdue"."""
    now = time.time() if now is None else now
    if self_login is None and ages_fn is None:
        self_login = _stream_self_login()
    ages = ages_fn or (lambda n: _issue_comment_ages(n, self_login, now, cwd))
    flagged = set()
    for number in sorted(rows)[:OPS_WAIT_STALE_MAX_FETCHES]:
        row = rows.get(number) if isinstance(rows, dict) else None
        title = row.get("title") if isinstance(row, dict) else None
        if not (isinstance(title, str)
                and _RELEASE_RECHECK_TITLE_RX.search(title)):
            continue                             # not release-shaped -> no flag
        try:
            res = ages(number)
        except Exception:
            res = None
        res = _norm_ages(res)                    # #753 dict / legacy 2-tuple
        if res is None:
            continue                             # gh failed / unusable -> no flag
        own_ts = res.get("own")
        if own_ts is None:
            continue                             # no own re-check evidence -> safe
        if working_time.working_deadline_passed(own_ts, now,
                                                RELEASE_RECHECK_MAX_S):
            flagged.add(number)
    return flagged


# #753 part 1a — the mechanical `unpark?` signal for a release-parked W member
# whose release has PROVABLY landed. `_release_train_drained` is the SAME
# proof-only predicate the #698 nudge uses; duplicated here — NOT imported — to
# keep cli_quals free of a watchdog import (the pattern `_RELEASE_RECHECK_TITLE_RX`
# already follows), drift-locked to
# `watchdog.ops_wait_recheck._release_train_drained` by test.
def _release_train_drained(rstate):
    """True IFF `rstate` proves a real, fully-drained 3-branch release train:
    `train` True (staging verified to exist), `ahead` == 0 (integration not ahead
    of prod), `in_flight` False (no open release PR / running deploy). Anything
    else — None/undetermined, a missing/False `train`, a live gap, a bool `ahead`
    — is False (the escalated "unpark" claim never rides an unproven state)."""
    if not isinstance(rstate, dict):
        return False
    ahead = rstate.get("ahead")
    return (rstate.get("train") is True
            and isinstance(ahead, int) and not isinstance(ahead, bool)
            and ahead == 0 and rstate.get("in_flight") is False)


# authorities whose ORIGIN is the canonical repo, so an origin release-train read
# is trustworthy. A fork-no-merge box's origin is the FORK, whose frozen branches
# could read "drained" forever — the #698 false-claim the guard exists for.
_UNPARK_AUTHORITIES = ("full", "branch-merge")


def _unpark_release_flagged(rows, authority=None, release_fetch=None):
    """The set of release-parked ops-wait (W) member numbers to tag `unpark?`
    (#753 part 1a) — a member whose blocker (a release) has PROVABLY landed, so
    the OWNING session should verify per #588 and clear `ops-wait`. Flagged iff:
      - the TITLE names a release/version/stage (`_RELEASE_RECHECK_TITLE_RX`) AND
        the member's reason is `ops-wait` (NOT `acceptance` — a client-blocked
        member with a release-ish title belongs to the (b) UNPARK-AUDIT branch), AND
      - `authority` ∈ {full, branch-merge} (origin == the canonical repo), AND
      - the repo's release train is PROVEN drained (`_release_train_drained` over
        the origin state `release_fetch()` returns).
    `release_fetch` is a 0-arg callable read AT MOST ONCE, and ONLY when a
    release-shaped member exists AND `authority` qualifies (so a repo with no
    release-parked member does ZERO extra work — the #570 budget is untouched, no
    per-member read). Fail-safe UNTAGGED (the #539/#570 never-false-accuse bias):
    a non-qualifying/absent authority, a fetch error/None, a not-drained/
    undetermined state, or no release-shaped member → the EMPTY set. Consistent
    between the session's on-demand `--ops-wait` call and the watchdog subprocess:
    both read the SAME origin state (never a credless PROD/Discuss read — the
    rejected option B), so the tag can never count inconsistently between them."""
    if authority not in _UNPARK_AUTHORITIES:
        return set()
    # Scope to `ops-wait`-reason members (parked on an external event) — EXCLUDE
    # an `acceptance`-reason member (client-blocked) that merely happens to carry
    # a release-shaped title (`nasadenie`/`deploy`/`v2.1`): its blocker is a CLIENT
    # reply, not a release, so it belongs to the (b) UNPARK-AUDIT branch, never a
    # release-landed `unpark?`. Labels are already in the rows (zero extra gh).
    shaped = {n for n, row in rows.items()
              if isinstance(row, dict)
              and isinstance(row.get("title"), str)
              and _RELEASE_RECHECK_TITLE_RX.search(row["title"])
              and _ops_wait_reason(row.get("labels")) == "ops-wait"}
    if not shaped:
        return set()
    try:
        rstate = release_fetch() if release_fetch is not None else None
    except Exception:
        rstate = None
    if not _release_train_drained(rstate):
        return set()
    return shaped


# #636: the gk hand-off labels that CONTRADICT an `ops-wait` park. Either means
# "parked with the gatekeeper for a gk ACTION" — the gatekeeper is a named fleet
# actor with a dedicated hand-off lane, NOT a third party (the exact parallel of
# #601's owner-is-not-a-third-party ruling), so a ticket blocked on a gk action
# belongs in gk N (stream) / the gk box's actionable I, NEVER in W. `ready-for-
# review` is the repo-workflow hand-off, `needs-gatekeeper` is airuleset's own
# gk-request lane (#191/#223 fold both into the same gk bucket).
_GK_HANDOFF_LABELS = ("needs-gatekeeper", "ready-for-review")

# #636 review 🟡: a `prio:bounce` OVERRIDES a co-present gk hand-off label back to
# "the STREAM's own court" (the #313 pt-2 override that `_slice_mine_and_handed`
# and NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS already honour — a bounced ticket is
# NOT counted in gk). So a bounced ticket is NEVER post-release limbo: it must be
# REWORKED in I, not re-handed-off. Excluding it keeps the gk-handoff! flag
# consistent with the handed/gk count and stops the nudge driving a premature
# re-hand-off (a bounce↔hand-off loop).
_GK_HANDOFF_BOUNCE_OVERRIDE = "prio:bounce"


def _gk_handoff_ops_wait_flagged(rows):
    """The set of ops-wait (W) member numbers to tag `gk-handoff!` (#636) — a
    parked W ticket that ALSO carries a `_GK_HANDOFF_LABELS` label. This is the
    contradictory "post-release limbo" shape: the footer partition routes a
    handed+parked row to the W bucket (`_partition_workable` +
    `cmd_tickets_status`), so the stale `ops-wait` HIDES the gk hand-off from
    `gk N` (stream) and the gk box's own actionable I — leaving the ticket stuck
    in a W nobody pushes forward (`gk` is not a third party, so the #570 daily-
    push doctrine has no valid action for it, odoo-erp #3108/#4600).

    PURE label check — no gh, no timeline, no heuristic (the whole reason #636
    chose this signal over the ticket's proposed timeline-verdict + release-
    evidence sweep). The FIX the tag prompts is to DROP the `ops-wait` label so
    the ticket surfaces in gk N (stream) / the gk box's I via its already-present
    `needs-gatekeeper` — the label change is the SUPERVISOR's with evidence,
    never auto-applied (the tag only surfaces).

    Requires BOTH `ops-wait` AND a gk label present, so the function is correct
    standalone (a genuine contradiction), not merely "the caller passed the W
    bucket". A co-present `prio:bounce` (#636 review 🟡) EXCLUDES the row — a
    bounced ticket is back in the stream's own court (the #313 override the
    handed/gk count already honours), never a gk hand-off. Never a false
    accusation ("nikdy falošný", the #539/#570 bias): a missing/malformed
    `labels` value on a row is simply not flagged."""
    flagged = set()
    for number, row in (rows or {}).items():
        labels = row.get("labels") if isinstance(row, dict) else None
        names = {(lb or {}).get("name") for lb in (labels or [])
                 if isinstance(lb, dict)}
        if ("ops-wait" in names
                and _GK_HANDOFF_BOUNCE_OVERRIDE not in names
                and any(lb in names for lb in _GK_HANDOFF_LABELS)):
            flagged.add(number)
    return flagged


def _authority_marker_path(cwd=None):
    """The absolute path of the CLAUDE.md that `_authority_marker_raw` reads for
    `cwd` — the SINGLE source of the marker-file LOCATION, so `authority
    --explain` can name the EXACT file a marker was (or was not) read from
    without re-deriving the formula and desyncing from what was actually read
    (#829). Mirrors `_authority_marker_raw`'s own read target verbatim."""
    return str((Path(cwd) if cwd else Path.cwd()) / "CLAUDE.md")


def _authority_marker_raw(cwd=None):
    """The RAW last `<!-- airuleset:authority=<tok> -->` HTML-comment token from
    the project CLAUDE.md (cwd-relative), whether or not it names a VALID profile,
    or None. The ONE file read that both resolution and the decision log share —
    surfacing the raw token lets `authority --explain` tell 'no marker' apart from
    'marker present but invalid' (a typo'd `branch_merge`), the exact misconfig
    class the decision log exists to diagnose (#821).

    Only the HTML-COMMENT form is read (exactly like `<!-- airuleset:merge=manual
    -->`) — a bare/prose mention of `airuleset:authority=…` is deliberately NOT
    read: an unanchored match could let a documentation sentence naming a profile
    silently ELEVATE a fork-no-merge stream (the UNSAFE direction). If several
    comment markers exist (a misconfig) the LAST one wins, so an operative marker
    placed after any example cannot be shadowed."""
    import re
    try:
        p = Path(_authority_marker_path(cwd))
        if p.is_file():
            hits = re.findall(r"<!--\s*airuleset:authority=([a-z-]+)\s*-->",
                              p.read_text(errors="ignore"))
            if hits:
                return hits[-1]
    except OSError:
        return None
    return None


def _authority_marker(cwd=None):
    """The project CLAUDE.md `<!-- airuleset:authority=<profile> -->` override for
    `cwd` — a VALID profile string or None. Lets a project raise/lower a stream's
    default authority (e.g. grant `full` to a montalu repo). The single place the
    marker is VALIDATED, so the CLI, the autopilot skill, and the issue-close guard
    hook agree; reads the raw token via `_authority_marker_raw` and keeps only a
    value in `AUTHORITY_PROFILES` (a bogus/typo'd token resolves to None → the
    per-user table stands, the fail-safe direction)."""
    import airuleset
    raw = _authority_marker_raw(cwd)
    return raw if raw in airuleset.AUTHORITY_PROFILES else None


def _authority_decision(cwd=None):
    """The authority resolution WITH its provenance, from ONE decision point:
    `(profile, source, raw_marker)`, where `source` is one of
    'project CLAUDE.md marker' | 'per-user map' | 'ci-runner (GitHub-hosted)' |
    'ci-runner (GitHub-hosted, container)' | 'full-authority account' |
    'default (unmapped)'.
    `resolve_authority` (the hot path) and `cmd_authority --explain` (the #486
    decision log) both derive from this single function, so the printed log can
    never desync from the resolved profile, and it distinguishes the reduced-stream
    map ROW and the explicit full-authority allow-list from the fail-safe default
    that decides a genuinely unmapped user.
    Resolution order (airuleset#827/#839): a valid marker wins; else the reduced-
    stream map row (`AUTHORITY_BY_USER`); else the GitHub-hosted CI runner
    (`_is_github_ci_runner` — airuleset's OWN CI, unspoofable, #839); else the
    explicit full-authority allow-list (`FULL_AUTHORITY_USERS`); else the fail-SAFE
    `fork-no-merge` default. The map is checked BEFORE the ci-runner branch and the
    full allow-list, so a reduced stream can never be elevated by a (bug) dual
    membership — restrictive wins. The pre-#827 default was the fail-OPEN `full`:
    an unmapped stream account (provisioned but forgotten in the map) silently got
    full merge/deploy/close authority; it now fails SAFE to the most restrictive
    profile, and the legitimate full accounts are enumerated in
    `FULL_AUTHORITY_USERS` (plus the un-spoofable CI-runner recognition) rather
    than relying on that open catch-all."""
    import airuleset
    raw = _authority_marker_raw(cwd)
    if raw in airuleset.AUTHORITY_PROFILES:
        return raw, "project CLAUDE.md marker", raw
    user = airuleset._current_user()
    if user in airuleset.AUTHORITY_BY_USER:
        return airuleset.AUTHORITY_BY_USER[user], "per-user map", raw
    # airuleset#839: the GitHub-HOSTED CI runner (unspoofable pw_name `runner`
    # OR uid 0 in a container job, AND GITHUB_ACTIONS=true AND
    # RUNNER_ENVIRONMENT=github-hosted) is a legitimate full-authority context
    # for THIS repo's OWN CI — it is in neither registry, so #827's fail-safe
    # would leave it `fork-no-merge` and break ~33 FULL-authority-gated tests.
    # Placed AFTER the map so a mapped stream can never be elevated
    # (defense-in-depth; no stream is ever `runner`/uid-0) and BEFORE the full
    # allow-list. `user` is the hardened `_current_user()` pw_name, so the whole
    # conjunction is un-spoofable. The source names the CONTAINER arm distinctly.
    ci_src = airuleset._github_ci_runner_source(user)
    if ci_src is not None:
        return "full", ci_src, raw
    if user in airuleset.FULL_AUTHORITY_USERS:
        return "full", "full-authority account", raw
    return "fork-no-merge", "default (unmapped)", raw


def resolve_authority(cwd=None) -> str:
    """The current stream's autopilot authority profile: a project CLAUDE.md
    `airuleset:authority=<profile>` marker (cwd-relative) OVERRIDES the per-user
    default map. This makes `airuleset.py authority` authoritative for both the
    autopilot skill and the `block-fork-no-merge-issue-close` hook (single source
    of truth) — cmd_authority's explain text has always PROMISED this override; it
    is now actually honored, not just documented. Derives the profile from the
    single `_authority_decision` so the CLI's `--explain` log can never name a
    source that disagrees with what actually resolved."""
    return _authority_decision(cwd)[0]


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    import airuleset
    if getattr(args, "maintainer_login", False):
        print(airuleset.MAINTAINER_GH_LOGIN)
        return
    if getattr(args, "self_login", False):
        # THIS box's own gh identity for the self-authored-close carve-out
        # (block-fork-no-merge-issue-close.sh, #463). An App installation token
        # 403s on `gh api user` structurally, so an App-token box's identity is
        # the fixed bot login every ticket it FILES carries, resolved WITHOUT a
        # network call (`gh api user` would only 403 anyway). Every other box
        # uses its real gh login. Prints nothing (empty) when the login cannot
        # be resolved -> the hook's fail-safe refuses the exemption (blocks),
        # never guesses.
        if _is_gh_app_token_box():
            print(airuleset.STREAM_APP_BOT_LOGIN)
            return
        login = airuleset._gh_login()
        if login:
            print(login)
        return
    if getattr(args, "app_bot_login", False):
        # #773: the shared stream App bot login (STREAM_APP_BOT_LOGIN), printed
        # UNCONDITIONALLY -- it is a static constant, not a per-box identity, so
        # no network call and no App-token-box detection is needed. The hook's
        # #773 fallback compares a ticket's AUTHOR against it: a ticket authored
        # by this bot was FILED by a stream (never maintainer-assigned, which is
        # authored by MAINTAINER_GH_LOGIN), so a reduced-authority stream may
        # self-close it even when --self-login could not resolve the box's own
        # identity.
        print(airuleset.STREAM_APP_BOT_LOGIN)
        return
    if getattr(args, "stream_label", False):
        # #533: THIS stream's ownership label `stream:<unix-user>` for the
        # acceptance-close carve-out in block-fork-no-merge-issue-close.sh.
        # Printed ONLY on a REDUCED-authority box (marker-aware via
        # resolve_authority, so a project marker is honored exactly like the
        # profile print below); a FULL-authority box prints NOTHING, so the
        # hook's fail-safe refuses the exemption. The label matches the one the
        # tickets carry (`_ticket_is_stream_labeled`) and the sub-dev slice uses
        # (`_slice_quals`) — the ownership signal that survives a shared gh
        # identity, unlike authorship (#463). No network call.
        if resolve_authority() != "full":
            # #564: emit ALL rename equivalents (newline-separated), routed
            # through the single `_stream_rename_equivalents()` alias primitive
            # (never a parallel table). A box whose base stream was renamed
            # (montalu -> montalu1) still owns tickets carrying the OLD
            # `stream:montalu` label during the transition, so the close-guard
            # hook must recognize ANY of them (`_has_own_stream_label` loops).
            # A non-renamed stream expands to just itself, so its output is
            # byte-identical to before.
            for n in _stream_rename_equivalents(airuleset._current_user()):
                print("stream:%s" % n)
        return
    # #486 / #821: compute the decision ONCE (ONE CLAUDE.md read, ONE resolution)
    # and use it for BOTH the plain profile line and the --explain log, so the
    # printed source can never disagree with the resolved profile (no shadow
    # re-derivation, no second file read that could differ mid-command).
    # #829: anchor the marker read at the REPO ROOT (`airuleset._repo_root() or
    # None`), IDENTICALLY to every in-process consumer — the run-card
    # (`airuleset.py` card_root), the footer/slice gates (`cli_quals_cmd.py`),
    # and the close-guard hook (which shells out to this plain `authority`) — the
    # established #181 I-5 / run-card precedent. The pre-#829 bare-cwd form read
    # `<cwd>/CLAUDE.md` with NO walk-up, so invoked from a SUBDIRECTORY of a
    # marker-carrying project it missed the repo-root marker (no marker in a
    # subdir), the map/allow-list won, and `--explain` mis-named the winning
    # source while the consumers honored the marker. `_repo_root() or None`
    # walks up to the git toplevel and falls back to None (→ read cwd,
    # marker=none when absent) outside any repo, exactly as the consumers do.
    root = airuleset._repo_root() or None
    profile, source, raw = _authority_decision(root)
    print(profile)
    if getattr(args, "explain", False):
        # An explicit decision LOG (not a silent `marker or map`). Diagnoses the
        # stale-mapping class (miva1 armed the wrong /goal template because
        # odoo-erp's PROSE was not the HTML-comment marker, so the map won) AND
        # its sibling (a typo'd `branch_merge` marker), by naming which source
        # decided, the raw marker (distinguishing 'none' from 'invalid'), and — for
        # a user in neither registry — the fail-SAFE `fork-no-merge` default vs a
        # reduced-stream map row vs the full-authority allow-list (airuleset#827).
        # The map= annotation is self-documenting: an unmapped user's line carries
        # the remedy ("add to AUTHORITY_BY_USER or FULL_AUTHORITY_USERS") so the
        # loud fail-safe degrade names its own fix. Lives ONLY in --explain
        # (opt-in), never on the hot resolve_authority() path the footer and
        # close-guard call every cycle. Resolved against the invoking cwd — the
        # same anchoring the plain `authority` output has always used.
        user = airuleset._current_user()
        if user in airuleset.AUTHORITY_BY_USER:
            map_val = airuleset.AUTHORITY_BY_USER[user]
        elif airuleset._is_github_ci_runner(user):
            # airuleset#839: same order as _authority_decision above; name the
            # CONTAINER arm distinctly so the map= annotation matches the source
            # line (which already distinguishes the container vs runner arm).
            ci_src = airuleset._github_ci_runner_source(user)
            map_val = ("GitHub-hosted CI runner (container) -> full"
                       if ci_src and "container" in ci_src
                       else "GitHub-hosted CI runner -> full")
        elif user in airuleset.FULL_AUTHORITY_USERS:
            map_val = "full-authority account -> full"
        else:
            map_val = ("unmapped -> fork-no-merge (fail-safe; add to "
                       "AUTHORITY_BY_USER or FULL_AUTHORITY_USERS)")
        if raw is None:
            # #829: no marker was read -> name NO path (there is nothing to name).
            mark = "none"
        else:
            # #829: a marker (valid OR invalid) WAS read -> name the ACTUAL
            # CLAUDE.md PATH it came from, so the diagnostic can be trusted about
            # WHERE the winning/invalid marker lives (the run-card/footer/consumer
            # divergence this ticket closes was exactly a marker read from a
            # DIFFERENT anchor than --explain claimed). The path is the repo-root
            # anchor resolved above, routed through the single _authority_marker_path.
            mpath = _authority_marker_path(root)
            base = raw if raw in airuleset.AUTHORITY_PROFILES else "invalid(%r)" % raw
            mark = f"{base} (read from {mpath})"
        print(f"resolved={profile} via {source} "
              f"(marker={mark}; user={user} map={map_val}); "
              f"an HTML-comment marker <!-- airuleset:authority=<profile> --> overrides the map.")


def _label_exists_on_repo(label, cwd=None):
    """True if `label` is a DEFINED label on the current repo (gh label list
    --search), False if confirmed absent, None if the query itself failed —
    an unreachable/erroring gh is NOT evidence the label is missing (#181
    C2, round 2)."""
    import airuleset
    raw = airuleset._gh_out("label", "list", "--search", label, "--json", "name",
                  "-L", "50", cwd=cwd)
    try:
        names = {(x or {}).get("name") for x in json.loads(raw)}
    except (ValueError, TypeError):
        return None
    return label in names


def _search_index_healthy(cwd=None):
    """Does gh's SEARCH path demonstrably work for this identity/repo?
    True = yes; False = demonstrably not (or unprovable); None = the repo
    genuinely has no open issues at all, so an empty slice is trivially
    correct.

    #181 I-1: round 2's cross-check ran `involves:@me` and only required the
    response to PARSE — but `[]` parses, and "search returns nothing
    everywhere" IS `[]`, i.e. the exact state the check claimed to detect was
    the state it accepted. The reviewer executed it: login zbynekdrlik, user
    montalu, label present, every query `[]` → rc 0, stdout `0`. A real
    cross-check must ASSERT NON-EMPTY on a query that cannot legitimately be
    empty.

    A SORT-ONLY search (`sort:created-desc`) is that query: it carries no
    filtering qualifier, so it matches every open issue in the repo. If it
    comes back empty the repo may genuinely have none — settled by the REST
    listing path (`gh issue list` with no `--search`, a different gh code
    path that does not touch the search index). REST sees issues but search
    sees none ⇒ the search index is not answering ⇒ refuse."""
    import airuleset
    probe = airuleset._gh_out("issue", "list", "--state", "open", "--search",
                    "sort:created-desc", "-L", "1", "--json", "number", cwd=cwd)
    try:
        rows = json.loads(probe)
    except (ValueError, TypeError):
        return False
    if not isinstance(rows, list):
        return False
    if rows:
        return True
    rest = airuleset._gh_out("issue", "list", "--state", "open", "-L", "1",
                   "--json", "number", cwd=cwd)
    try:
        rest_rows = json.loads(rest)
    except (ValueError, TypeError):
        return False
    if not isinstance(rest_rows, list):
        return False
    return None if not rest_rows else False


def _union_open_issues(quals, base, cwd=None, repo=None):
    """Run ONE `gh issue list --search` per qual and union the rows by issue
    number, returning `(rows_by_number, failed)`.

    Per-qual queries are not an optimisation choice: gh's `--search` ANDs
    space-joined qualifiers across qualifier types and cannot OR them, so a
    caller that needs a UNION (assignee ∪ author ∪ label; core ∪ the
    maintainer-action labels) must union client-side. `failed` is True if ANY
    query failed to parse — a gh error is never an empty result.

    `labels` is fetched alongside (#181 round 4): one extra field on queries
    already being made, and the thing that lets `_print_issue_rows` mark every
    row with what THIS box may DO with it. Without it the mandated backlog
    SELECTION source emitted no not-mine-to-implement discriminator at all, so
    the only thing between the FULL template's bounce-lane seed ("the OLDEST
    open prio:bounce ticket" — live on odoo-erp that is #2150, `stream:david`)
    and a gatekeeper writing code on a sub-dev's ticket was a prose clause the
    worker may never have loaded.

    `repo` (#382): when given, added as `-R <repo>` to each query so it
    targets that explicit repo regardless of `cwd`'s git remote — the
    Discord run-card fires from an arbitrary worker cwd (a worktree, a
    subdev checkout) and must target the `--repo` it was given, never
    "whatever repo `cwd` happens to resolve to". None (default) is
    unchanged for every existing caller (`cmd_tickets_status`,
    `cmd_core_quals`, `_print_issue_rows`), which all resolve the repo from
    `cwd`'s git remote instead."""
    import airuleset
    seen, failed = {}, False
    for qual in quals:
        search = (base + " " + qual).strip() if qual else base
        gh_args = ["issue", "list", "--state", "open", "--search", search]
        if repo:
            gh_args += ["-R", repo]
        gh_args += ["-L", "1000", "--json", "number,title,createdAt,labels"]
        raw = airuleset._gh_out(*gh_args, cwd=cwd, timeout=20)
        try:
            for x in json.loads(raw):
                seen[x["number"]] = x
        except (ValueError, TypeError, KeyError):
            failed = True
    return seen, failed


ROW_ACTION_ONLY = "action-only"
ROW_IMPLEMENT = "implement"


def _stream_owner_of(labels):
    """The REDUCED-authority stream that owns this ticket, or "" — read from a
    gh `--json labels` value (a list of {'name': ...} dicts, or None).

    Only non-`full` AUTHORITY_BY_USER entries count, the same filter
    `_core_search_excl()` applies (#181 M-5): a hypothetical `full` entry is
    not a sub-dev stream, and treating its label as ownership would wrongly
    mark its tickets untouchable.

    #561: recognition is EXPANDED via `_stream_rename_equivalents()` (the same
    single alias primitive `_core_search_excl`/`_slice_quals`/`_ticket_is_
    stream_labeled` use) so a legacy `stream:<old>` label still resolves to its
    owner after the rename removed the old key from AUTHORITY_BY_USER —
    otherwise a `stream:montalu`+`needs-gatekeeper` hand-off (still in the
    obligation union) would render `implement` in `core-quals --list` and
    invite the gatekeeper to write montalu's code. Returns the AUTHORITY_BY_
    USER KEY (the current canonical name, e.g. `montalu1`), never the matched
    alias — so `_row_action`'s `owner == own_stream` comparison keeps a box's
    own old-labeled ticket reading `implement`."""
    import airuleset
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for user, profile in sorted(airuleset.AUTHORITY_BY_USER.items()):
        if profile != "full" and any(("stream:%s" % n) in names
                                      for n in _stream_rename_equivalents(user)):
            return user
    return ""


def _own_handoff_label():
    """This box's own `handed-by:<user>` hand-off origin marker, or None
    when this box is not a registered sub-dev stream (#191 Part C). Guards
    `cmd_gk_request`'s origin-marker write against a full-authority box
    (dev1/gatekeeper) stamping a meaningless `handed-by:newlevel`/
    `handed-by:gatekeeper` label onto a gk-request filed for its own
    testing or on another stream's behalf.

    #191 adversarial review, CRITICAL C1: deliberately `handed-by:<user>`,
    NEVER `stream:<user>` — see `cmd_gk_request`'s own docstring for why
    reusing the ownership label would have broken the `/goal` stop-proof's
    termination condition."""
    import airuleset
    user = airuleset._current_user()
    return "handed-by:" + user if user in airuleset.AUTHORITY_BY_USER else None


# A candidate's ORIGIN-shaped labeled events, both the legacy ownership
# convention (`stream:<user>`, applied by a human during triage — the
# original, pre-#191 signal `_last_origin_owner` recovers) and the new
# hand-off marker (`handed-by:<user>`, #191 Part C) — either settles who a
# ticket belongs to for re-attribution purposes.
_ORIGIN_LABEL_RE = re.compile(r"^(?:stream|handed-by):(.+)$")


def _last_origin_owner(numbers, cwd=None):
    """For each issue in `numbers`, the stream that owns it per the
    TEMPORALLY-LAST origin-shaped (`stream:<user>` or `handed-by:<user>`)
    labeled event in its history — regardless of who applied it (a SHARED
    gh identity, e.g. montalu/marek/simap all authenticating as the
    maintainer, carries ZERO discrimination power between the streams that
    share it) and regardless of whether that label is STILL present (the
    timeline event survives a LATER relabel that removes it, unlike the
    label itself). #191 root cause 2: a shared-account stream's slice is
    `label:stream:<user>` alone, so a ticket relabelled away from it (the
    fix moved to shared code the stream cannot push to) silently vanishes —
    this recovers it from GitHub's own event history instead of guessing.

    #191 adversarial review, MAJOR M3: the original version asked "was MY
    label EVER applied", which let TWO streams that both once owned a
    ticket (A -> B -> unlabelled) BOTH reclaim it — the current-label
    bounding a caller may ALSO apply only helps when a current label
    survives, exactly the case this function exists to handle the absence
    of. Taking the LAST (not "ever") origin-shaped event settles a genuine
    competing claim correctly: whichever stream's label was applied most
    recently is the one this returns.

    ONE batched GraphQL call for the WHOLE candidate set, aliased per issue
    number -- never one REST call per candidate. A per-candidate REST loop
    shares a rate-limit bucket across every stream authenticating as the
    same PAT (#191 design review); batching collapses N candidates to O(1)
    calls regardless of how many there are. `timelineItems(last: 20, ...)`
    (#191 m2: NOT `first:`, which would read the OLDEST events on a churned
    ticket and truncate away the very origin/hand-off label this function
    exists to find). `owner`/`name` resolve via gh's own `-F owner=
    '{owner}' -F name='{repo}'` placeholder expansion from `cwd`'s git
    remote (the SAME shape `_watchdog_closed_fetch` already uses) -- no
    separate `gh repo view` call needed.

    Returns `{number: user}` for every candidate with at least one
    origin-shaped event found; a candidate with none is simply absent
    (never a guess). Returns `{}` on any failure or malformed response."""
    import airuleset
    if not numbers:
        return {}
    aliases = "\n".join(
        "i%d: issue(number: %d) { timelineItems(last: 20, "
        "itemTypes: [LABELED_EVENT]) { nodes { ... on LabeledEvent "
        "{ label { name } } } } }" % (int(n), int(n))
        for n in numbers)
    query = ("query($owner: String!, $name: String!) { repository(owner: "
             "$owner, name: $name) { %s } }" % aliases)
    raw = airuleset._gh_out("api", "graphql", "-f", "query=" + query,
                  "-F", "owner={owner}", "-F", "name={repo}",
                  cwd=cwd, timeout=20)
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or data.get("errors"):
        return {}
    repo = (data.get("data") or {}).get("repository")
    if not isinstance(repo, dict):
        return {}
    owners = {}
    for n in numbers:
        node = repo.get("i%d" % int(n))
        if not isinstance(node, dict):
            continue
        items = (node.get("timelineItems") or {}).get("nodes") or []
        if not isinstance(items, list):
            continue
        # `nodes` is chronological (oldest first) — walk it in order and
        # keep the LAST origin-shaped match, so a later relabel always
        # wins over an earlier one.
        last_owner = None
        for it in items:
            if not isinstance(it, dict):
                continue
            name = (it.get("label") or {}).get("name")
            m = _ORIGIN_LABEL_RE.match(name or "")
            if m:
                last_owner = m.group(1)
        if last_owner is not None:
            owners[n] = last_owner
    return owners


def _slice_mine_and_handed(quals, root, slug, extra=None):
    """`(rows, handed, failed)` for a reduced-authority stream's OWN ticket
    slice — the ONE shared derivation `cmd_tickets_status`'s footer AND
    `cmd_slice_quals`'s `/goal` stop-proof both consume (#391 consistency
    guard, mirroring the guard already established for the full-authority
    obligation set: never two independent derivations of "which of my
    tickets are still active" that could silently drift apart).

    `rows` is `_union_open_issues`'s own return shape (`{number: {"number",
    "title", "createdAt", "labels"}}`) — reused directly rather than a
    second, narrower fetch, so `--list`'s title/createdAt needs no extra gh
    call. `handed` maps a ticket number to whether it is already parked with
    the gatekeeper: a label check (`ready-for-review`/`needs-gatekeeper`,
    overridden by a `prio:bounce` label — #313 pt 2), PLUS — only when
    `extra` is None, i.e. the plain/unfiltered slice `cmd_tickets_status`
    always uses and `cmd_slice_quals` uses for its own `--count`/plain
    `--list` — the shared-account stream-owner recovery (`_last_origin_
    owner`) and the comment-based fallback (`_comment_readiness_signal`),
    moved here VERBATIM from `cmd_tickets_status`. `extra` (the bounce-lane
    seed's `--extra "label:prio:bounce"`) SKIPS that enrichment: the
    recovery step's own candidate query (`label:needs-gatekeeper,
    ready-for-review`, deliberately never filtered by `extra`) could recover
    a ticket that does not itself match `extra`, silently violating the
    filtered result's own contract — and a genuine `prio:bounce` ticket is
    already correctly un-handed via the label override alone, so the
    enrichment buys nothing there anyway.

    #391 adversarial review CRITICAL-1: the comment fallback (below) keeps
    the LAST comment signal, and a stream's own bounce nudge lane
    (skills/autopilot/SKILL.md: a BARE `prio:bounce` label + a sub-dev-
    authored ACK) applies the bounce with NO accompanying gatekeeper-shaped
    comment at all — so a ticket with a genuine, older READY-FOR-REVIEW
    comment and an INVISIBLE bounce (label only, no comment) would have its
    last-and-only comment signal read True, silently re-upgrading it to
    handed and discarding the label override just computed. `bounce_numbers`
    tracks every currently-`prio:bounce`-labeled row; the comment-fallback
    walk below may only upgrade one of THOSE numbers to handed when it also
    saw a recognised gatekeeper comment (a VISIBLE bounce) somewhere in the
    thread -- an invisible bounce fails toward "still unhandled", the safe
    (never-stop) direction for a `/goal` stop-proof. A non-bounce-labeled
    row is unaffected: the fallback's original "trust the last signal"
    behaviour is unchanged for it (this is the #313 broken-workflow case the
    fallback exists for, where no bounce is in play at all).

    `failed` is True on ANY gh query failure in the per-qual fetch — the
    caller must treat that as "cannot trust an unhandled count of 0", exactly
    like every other gh-search-derived zero in this file."""
    import airuleset
    base = AUTOPILOT_SKIP_EXCL + ((" " + extra) if extra else "")
    rows, failed = _union_open_issues(quals, base, cwd=root)
    handed = {}
    bounce_numbers = set()
    processed_numbers = set()
    for n_num, row in rows.items():
        labels = {(lb or {}).get("name") for lb in (row.get("labels") or [])}
        # #191 Part A ("different lane"): needs-gatekeeper is airuleset's OWN
        # hand-off lane (cmd_gk_request), not just the repo-workflow's
        # ready-for-review — either equally means "out of my hands, waiting
        # on someone else" (#223 folded both into the same gk bucket).
        label_handed = ("ready-for-review" in labels) or \
            ("needs-gatekeeper" in labels)
        # #313 pt 2 (F2/F3): `prio:bounce` is the gatekeeper's own "returned
        # to the sub-dev, not ready" verdict — it overrides a stale/lagged
        # hand-off LABEL so a bounced ticket reaches `unhandled` naturally;
        # the comment-fallback walk below is what still recognises a genuine
        # RE-hand-off after a bounce -- but (#391 CRITICAL-1) only when that
        # bounce is itself VISIBLE in the comment thread (see the docstring).
        if "prio:bounce" in labels:
            label_handed = False
            bounce_numbers.add(n_num)
        # #507: a ticket carrying a GATEKEEPER_PROCESSED_LABELS label
        # (`needs-acceptance`) is a hand-off the gatekeeper has ALREADY
        # processed — it is back in the STREAM's court, NOT parked with the
        # gatekeeper. It already reads `handed=False` here (it carries no
        # ready-for-review/needs-gatekeeper), and the comment-fallback walk
        # below EXCLUDES it (via `processed_numbers`) so its stale, permanent
        # READY-FOR-REVIEW comment can never re-flip it back to parked. See
        # GATEKEEPER_PROCESSED_LABELS for why the COMMON re-hand-off (fresh
        # label) is still caught by the label-check above, and the accepted
        # SAFE-direction residual for the comment-only-re-hand-off edge.
        if any(lb in labels for lb in GATEKEEPER_PROCESSED_LABELS):
            processed_numbers.add(n_num)
        handed[n_num] = label_handed

    if extra is not None:
        return rows, handed, failed

    # #191 Part B ("ownership relabel"): a SHARED-account stream's slice is
    # `label:stream:<user>` ALONE — once a handed-off ticket's stream:<user>
    # label is removed, it vanishes from `rows` entirely. Own-account streams
    # (assignee/author quals present) already see this for free via
    # author:@me. `_last_origin_owner` resolves the TEMPORALLY-LAST
    # origin-shaped labeled event, settling a competing claim correctly
    # (#191 adversarial review M3). Deliberately NEVER filtered by `extra`
    # (see the docstring above) — this branch only runs when extra is None
    # anyway.
    #
    # #391 adversarial review THEORETICAL-6 (accepted residual, no known
    # reproduction, edge-of-edge): rows recovered here make `rows` non-empty
    # unconditionally, so `cmd_slice_quals`'s C2 label-existence refusal
    # (`_refuse_unless_empty_is_trustworthy`) is skipped even if the
    # `stream:<user>` label itself was deleted from the repo — a shared-
    # account box could then print a trusted-looking `0` while genuinely
    # unlabeled, unhandled tickets sit orphaned (recovered-but-not-owned).
    # Requires repo-label deletion PLUS a prior recovered hand-off PLUS an
    # orphaned open ticket, simultaneously — not chased.
    if not failed and len(quals) == 1 and quals[0].startswith("label:stream:"):
        user = airuleset._current_user()
        raw = airuleset._gh_out("issue", "list", "--state", "open", "--search",
                      AUTOPILOT_SKIP_EXCL +
                      " label:needs-gatekeeper,ready-for-review",
                      "-L", "200", "--json", "number,labels,title,createdAt",
                      cwd=root, timeout=20)
        try:
            candidates = json.loads(raw)
        except (ValueError, TypeError):
            candidates = []
        by_num = {}
        if isinstance(candidates, list):
            for x in candidates:
                try:
                    n_num = x["number"]
                except (TypeError, KeyError):
                    continue
                if n_num in rows:
                    continue
                if _stream_owner_of(x.get("labels")):
                    continue  # currently owned by ANOTHER stream
                by_num[n_num] = x
        to_check = list(by_num)[:50]
        if to_check:
            owners = _last_origin_owner(to_check, cwd=root)
            for n_num, owner in owners.items():
                if owner == user:
                    rows[n_num] = by_num[n_num]
                    handed[n_num] = True

    # #313 pt 2: the label alone is not a reliable hand-off signal — the
    # PRIMARY signal is the READY-FOR-REVIEW comment (agents/autopilot-
    # worker.md), always postable regardless of write access. A candidate
    # the label missed is checked directly against its own comments, in
    # creation order, keeping the LAST signal (a stale pre-bounce comment is
    # correctly invalidated by a later gatekeeper finding/bounce, and a
    # genuine post-bounce re-submission overrides that again).
    #
    # #589 END CONDITION: the comment signal alone had NO way to expire a
    # DONE hand-off (gk reviewed + merged + released and removed the queue
    # labels but left no `**GATEKEEPER` finding comment — the live odoo-erp
    # #4502 shape → the stale READY-FOR-REVIEW comment read True forever, so
    # the ticket counted in gk permanently AND, since the label query said
    # it was NOT handed, in `I` too — the #391 `I = mine - gk` violation the
    # owner ruled unacceptable). Fix: read the issue TIMELINE (which carries
    # the label-removal / close EVENTS the `/comments` endpoint cannot see)
    # instead of `/comments`, and treat a gk-RESOLUTION event (queue-label
    # removal / close) AFTER the last hand-off comment as a NEGATIVE signal
    # via the shared last-signal-wins walk. Zero added gh calls — the
    # timeline REPLACES the comments call (one `gh api` per candidate).
    # `_timeline_handoff_signal` is the pure per-event classifier.
    #
    # ACCEPTED RESIDUAL (both #589 reviews): the timeline is fetched
    # oldest-first, ONE page of `per_page=100` events (the API ignores
    # `direction=desc` — verified live), so a resolution beyond event 100 on a
    # hyper-active ticket is missed and its stale hand-off keeps counting as
    # gk. This is never WORSE than the pre-#589 `/comments` behaviour (which
    # read only the oldest default page too) and fails toward the stream's own
    # court (a safe over-count of `I`, never a false gk stop); fetching the
    # LAST page needs the total count = an extra per-ticket call, the query
    # explosion #589's cost constraint forbids. See `_timeline_handoff_signal`
    # for the full residual list.
    #
    # #391 CRITICAL-1: for a row in `bounce_numbers`, an upgrade to handed
    # additionally requires `saw_gatekeeper_comment` -- a recognised
    # gatekeeper-authored COMMENT (not a resolution EVENT) seen SOMEWHERE in
    # the walk, proving the bounce is genuinely VISIBLE in the thread (a real
    # post-bounce re-hand-off) rather than a bare-label bounce with no comment
    # at all (which must never re-flip a stale pre-bounce hand-off comment
    # back to handed). `_timeline_handoff_signal`'s second return value is
    # True ONLY for a gatekeeper COMMENT, so this gate is byte-preserved.
    if slug and not failed:
        # #507: a `processed_numbers` ticket (needs-acceptance — a hand-off the
        # gatekeeper already processed) is EXCLUDED from the candidate walk
        # entirely: it must stay handed=False (own workable, back in the
        # stream's court), and its stale, permanent READY-FOR-REVIEW comment
        # must never re-flip it to parked-with-gk. Excluding it here (rather
        # than fetching its timeline and refusing the upgrade) also skips a
        # pointless `gh api .../timeline` call per such ticket. The COMMON
        # re-hand-off carries a fresh ready-for-review/needs-gatekeeper LABEL,
        # so it is already handed via the label-check and never reaches here;
        # the comment-only-re-hand-off edge is the accepted SAFE-direction
        # residual documented on GATEKEEPER_PROCESSED_LABELS.
        unhandled_candidates = sorted(
            (n_num for n_num in rows
             if not handed.get(n_num) and n_num not in processed_numbers),
            reverse=True)
        for n_num in unhandled_candidates[:airuleset._HANDOFF_COMMENT_CHECK_LIMIT]:
            raw = airuleset._gh_out("api",
                          "repos/%s/issues/%d/timeline?per_page=100" % (slug, n_num),
                          cwd=root, timeout=20)
            try:
                events = json.loads(raw)
            except (ValueError, TypeError):
                events = []
            if not isinstance(events, list):
                continue   # e.g. a bare int -- never a real answer
            verdict = False
            saw_gatekeeper_comment = False
            for ev in events:
                sig, is_gk_comment = airuleset._timeline_handoff_signal(ev)
                if is_gk_comment:
                    saw_gatekeeper_comment = True
                if sig is not None:
                    verdict = sig
            if verdict and (n_num not in bounce_numbers or
                            saw_gatekeeper_comment):
                handed[n_num] = True

    return rows, handed, failed


