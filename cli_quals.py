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
import subprocess
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

    Known residual (adversarial review of #356, MITIGATED by #918): a
    stray or stale App-token directory delivered to an OWN-account (PAT)
    box — e.g. a misdirected ``push-stream-tokens.sh`` delivery, or a
    leftover from an App-token-to-PAT migration — silently NARROWS that
    box's own slice from 3 quals (assignee ∪ author ∪ label) down to 1
    (label alone). #918 mitigated the IDENTITY side: ``_stream_self_
    login()`` now validates via ``_gh_login()`` and returns the real PAT
    login when the active auth is a PAT, so own-comment matching and
    bounce-round derivation are correct even with a stray directory.
    The slice-narrowing residual (this function still returns True →
    ``_slice_quals`` takes the label-only branch) remains accepted."""
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
#
# #1053 (owner directive 2026-09-16): `gk-processing` is added. The gatekeeper's
# pickup step swaps `ready-for-review` → `gk-processing` (so the sub-dev's `gk`
# bucket does NOT fall to 0 while gk is actually working — the david1 report),
# and while gk is processing the ticket is the gatekeeper box's OWN `I`
# obligation exactly like a fresh hand-off. The POST-deploy return state
# `verify-on-copy` is deliberately NOT here: it is the SUB-DEV's own action
# (verify on a fresh PROD copy), not the maintainer's — see SUBDEV_ACTION_LABELS
# and GATEKEEPER_PROCESSED_LABELS. Kept in lock-step with
# `airuleset._HANDOFF_QUEUE_LABELS` (the #589 resolution-signal set), asserted by
# test_gk_comment_end_condition_589.
MAINTAINER_ACTION_LABELS = ("needs-gatekeeper", "ready-for-review",
                            "gk-processing")

# #1053: the SUB-DEV's own action-only labels — a state where only the reduced-
# authority stream box can act, so (like a MAINTAINER_ACTION_LABELS hand-off in
# the #943 ops-wait override) the label keeps the row in the sub-dev's workable
# `I` even if a stale `ops-wait` is co-present, never hidden in `W`.
# `verify-on-copy` = "verify the deployed change on your own fresh PROD copy".
# Deliberately SEPARATE from MAINTAINER_ACTION_LABELS: it must NOT enter the gk
# box's obligation UNION query (`_obligation_quals`) — the gatekeeper never
# actions a verify-on-copy ticket, its owning stream does.
SUBDEV_ACTION_LABELS = ("verify-on-copy",)

# #1056 L2 (i0): a returned bounce is the STREAM's own rework, never "waiting on
# a third party". `_partition_workable` pulls a `prio:bounce` row OUT of the
# ops-wait/W bucket into `workable`, extending the #507
# NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS precedence (which already keeps such a row
# out of U) into the W branch. A pure LABEL override (the safe over-count
# direction) — the precise gk-verdict-vs-RFR classification lives in Job 8's
# `_bounce_nudge_message` and gk-watch, keeping this partition network-free.
# The literal mirrors `_GK_HANDOFF_BOUNCE_OVERRIDE` (defined later, next to
# `_count_bounce`); kept as its own name here so the override reads at the
# partition site without a forward reference.
_PARTITION_BOUNCE_LABEL = "prio:bounce"


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
#
# #1053: `verify-on-copy` (the post-deploy hand-back state) is the SECOND
# gatekeeper-PROCESSED label — gk has reviewed + merged + deployed and RETURNED
# the ticket to the sub-dev to verify on its own fresh PROD copy, so it is back
# in the STREAM's court (`handed=False`, routes to the sub-dev's `I`). Excluding
# it from the comment/timeline candidate walk means a stale READY-FOR-REVIEW
# hand-off comment can never re-flip a verify-on-copy ticket back to parked-with-
# gk, exactly the guarantee `needs-acceptance` gets.
GATEKEEPER_PROCESSED_LABELS = ("needs-acceptance", "verify-on-copy")


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

# #1067 (a): the labels that DEFINE W (ops-wait) membership — a row is in the W
# bucket iff it carries `ops-wait` (external event) OR `needs-acceptance` (client
# thread sent, routed to W by `_partition_workable`'s acceptance-scoped override).
# The batched comment prefetch (`_ops_wait_prefetch_comments`) ANDs this label OR
# onto each member-defining qual, so ONE paginated `gh api graphql` search
# (`search(query: "<qual> label:ops-wait,needs-acceptance" …)`, #1067 slice 1b)
# fetches every W member's newest comments in O(pages) calls instead of one
# `gh issue view` per member. GraphQL search `label:a,b` ORs the labels
# (live-verified 2026-09-23 on odoo-erp: issueCount matched the union).
OPS_WAIT_PREFETCH_LABELS = OPS_WAIT_LABELS + ("needs-acceptance",)
# gh issue list --limit ceiling for the prefetch. A member beyond it, or a
# member missing from a truncated list, falls back to today's per-issue
# `gh issue view` — correct, just slower.
OPS_WAIT_PREFETCH_LIMIT = 500
# #1067 — gh's `issue list --json comments` truncates the nested comments
# connection at 100 per issue (GraphQL default page), while `gh issue view
# --json comments` paginates FULLY (live-verified 2026-09-23: airuleset #870
# list=100 vs view=136). A prefetch row at/over this cap is therefore possibly
# MISSING its newest comments — which would give a wrong `own`/`own_cited`
# freshness anchor and a FALSE `stale!` ("nikdy falošný", #539/#570). So such a
# row is EXCLUDED from the prefetch map and falls back to the fully-paginated
# per-issue read. W tickets with >100 comments are rare, so the extra per-issue
# reads are few; correctness is never traded for the batch.
OPS_WAIT_PREFETCH_COMMENT_CAP = 100
# #1067 slice 1b — the GraphQL `search` page size. `search(type: ISSUE,
# first: N)` carries N issues per request, each with its newest
# `comments(last: OPS_WAIT_PREFETCH_COMMENT_CAP)`. Small pages keep a single
# request light on a busy repo (the slice-1 `gh issue list` HTTP-502/504'd on
# odoo-erp trying to pull up to 100 issues × 100 comments at once). The pager
# stops at `hasNextPage == false` OR after `ceil(OPS_WAIT_PREFETCH_LIMIT /
# page-size)` pages, whichever comes first.
OPS_WAIT_PREFETCH_PAGE_SIZE = 10


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
    `ops-wait` row is tagged `ops-wait`, never mislabelled `acceptance`.
    (#943: a `needs-gatekeeper`/`ready-for-review` + `ops-wait` row now routes
    to `workable` (I) via the MAINTAINER_ACTION_LABELS override in
    `_partition_workable`, so this reason function is reached only for rows
    in the `ops_wait` bucket — which no longer includes the contradictory
    gk+ops-wait shape.)"""
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

    #943 (owner escalation 2026-09-08, odoo-erp #6294 APK): a NON-user-waiting
    row carrying BOTH ops-wait AND a MAINTAINER_ACTION_LABELS label
    (`needs-gatekeeper` / `ready-for-review`) routes to `workable` (action-only
    I), NOT `ops_wait` (W). Only the full-authority box can action a hand-off,
    so the hand-off label keeps the row visible in I — the #589/#636
    over-count-safe direction. The `_gk_handoff_ops_wait_flagged` function (#636)
    already DETECTED this contradictory shape and tagged it `gk_handoff!` in the
    nudge text, but the partition itself routed it to W, making it invisible to
    the gatekeeper's I count for 2 days. The override is unconditional (not
    authority-gated) because this function is a pure label partition (#622),
    and on a reduced-authority box these rows are structurally absent.

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
            # #943: a MAINTAINER_ACTION_LABELS label (needs-gatekeeper /
            # ready-for-review) OVERRIDES ops-wait → workable (action-only I).
            # Only the full-authority box can action a hand-off, so the hand-off
            # label must keep the row visible in I, never hidden in W (the
            # #589/#636 over-count-safe direction — the _gk_handoff_ops_wait_
            # flagged function already DETECTS this shape, but the partition
            # itself routed it to W for 2 days, odoo-erp #6294 APK). The
            # override is unconditional (not authority-gated) because the
            # function is a pure label partition (#622); on a reduced-authority
            # box these rows are structurally absent from the obligation set
            # anyway (_slice_mine_and_handed search-excludes foreign
            # needs-gatekeeper rows). Additive partition-extension pattern
            # (#601/#622).
            names = {(lb or {}).get("name") for lb in (labels or [])
                     if isinstance(lb, dict)}
            # #1053: a SUBDEV_ACTION_LABELS label (`verify-on-copy`) overrides
            # ops-wait the SAME way — only the owning stream can perform the
            # post-deploy verify, so it stays action-only I, never hidden in W.
            # #1056 L2 (i0): a `prio:bounce` label ALSO overrides ops-wait →
            # workable. A returned bounce is the stream's own rework (the #313
            # bounce override / #507 precedence extended into the W branch), so
            # it is never "waiting on a third party" — the montalu1 read where
            # three bounces sat in W (`prio:bounce`+`needs-acceptance`+
            # `ops-wait`) as a false third-party wait. Label-only (the safe
            # over-count direction); the precise verdict-vs-RFR classification
            # is Job 8's / gk-watch's job, keeping this partition network-free.
            if (_PARTITION_BOUNCE_LABEL in names
                    or any(ml in names for ml in
                           (MAINTAINER_ACTION_LABELS + SUBDEV_ACTION_LABELS))):
                workable[number] = row
            else:
                ops_wait[number] = row
        else:
            workable[number] = row
    return workable, user_waiting, ops_wait


def _split_merged_unreleased(workable, ops_wait, merged_numbers):
    """#1083 — the MERGED-UNRELEASED extension of `_partition_workable`: pull the
    open tickets whose fix PR is already merged into develop/staging but NOT yet
    in main (`merged_numbers`, derived from GIT by `cli_release_state`) OUT of the
    workable `I` and third-party `W` buckets into a distinct `M` bucket. There is
    nothing for the box to ACT on such a ticket until the release cut, so it must
    leave `I` (and, being merged, `gk`/handed logic too — those rows never reach
    this function as workable once they are in `M`).

    Runs on the SAME already-partitioned buckets `_partition_workable` produced —
    ONE derivation, never a second query (#367) — and is kept a SEPARATE additive
    function (the #601/#622 additive-partition-extension pattern) so
    `_partition_workable`'s widely behaviour-locked 3-tuple contract is unchanged.

    PRECEDENCE (design item 2): a merged ticket LEAVES into `M` regardless of its
    lifecycle label — UNLESS it carries a `U`-class label (`needs-answer` /
    `needs-decision` / `needs-acceptance`: the owner's court beats "waiting for
    the cut", so it keeps its existing U/W bucket) or `prio:bounce` (gk returned
    it for rework: it stays in `I`). A U-class row is never even in `workable`
    (it is in `user_waiting`); the check still guards `ops_wait`, where a
    `needs-acceptance`+`ops-wait` row (→ W by #526) must STAY in W, not be pulled
    into `M`. Returns `(workable, ops_wait, merged)` — all three fresh dicts;
    `user_waiting` is never touched (owner-court rows are out of scope here)."""
    merged_set = {int(n) for n in (merged_numbers or [])}
    if not merged_set:
        return dict(workable), dict(ops_wait), {}
    merged, new_workable, new_ops_wait = {}, {}, {}
    for bucket, keep in ((workable, new_workable), (ops_wait, new_ops_wait)):
        for number, row in bucket.items():
            labels = row.get("labels") if isinstance(row, dict) else None
            names = {(lb or {}).get("name") for lb in (labels or [])
                     if isinstance(lb, dict)}
            if (int(number) in merged_set
                    and not _row_is_user_waiting(labels)
                    and _PARTITION_BOUNCE_LABEL not in names):
                merged[number] = row
            else:
                keep[number] = row
    return new_workable, new_ops_wait, merged


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


# #1025 — the owner-QUESTION subset of USER_WAITING_LABELS (a `❓ ASKED`/
# `❓ NEEDS YOU` turn adds one of these). `needs-acceptance` is EXCLUDED — it is a
# queued client-message approval, not a ❓ owner question. The gate's PURPOSE
# is about these three; but the MEMBERSHIP question the gh fallback answers is "is
# #N in the box's U SET", and U (the footer's `user_waiting`, cached in
# `user_waiting_numbers`) is the FULL `USER_WAITING_LABELS` — needs-acceptance
# included. The fallback therefore searches `USER_WAITING_LABELS` so it agrees
# with the cache set (else a just-added needs-acceptance ticket named in a ❓ turn,
# on a stale U==0 cache, would gh-search-MISS → false `not_in_u` → false block —
# #1025 review 🟡2). Over-approximates scope (a needs-acceptance+ops-wait W member
# also matches) → biased toward `in_u`/allow, the safe direction.
QUESTION_U_LABELS = USER_WAITING_LABELS

# How fresh the tickets-status cache must be for its `user_waiting_numbers` to be
# trusted as a fast-allow (mirrors u_freshness's own cache-age gate, #797).
_QUESTION_U_CACHE_FRESH_S = 120


def _default_u_runner(argv, cwd):
    """Run ONE gh command in `cwd`, returning stdout on success or None on any
    failure/timeout — the fail-open signal `question_ticket_in_u` needs. Unlike
    `airuleset._gh_out` (which returns "" for BOTH a gh error AND an empty
    result, conflating fail-open with a genuine empty set), None here means
    "could not measure" → the gate fails OPEN, "" / "[]" means "measured empty"
    → a legitimate not-in-U."""
    import airuleset
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                           timeout=20, env=airuleset._gh_env())
        return r.stdout if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — any failure is "unmeasurable", never a block
        return None


def question_ticket_in_u(numbers, cwd, *, home=None, now=None, runner=None,
                         cache_max_age_s=_QUESTION_U_CACHE_FRESH_S):
    """#1025/#1026 — classify a ❓ turn's named same-repo `#N` refs against this
    box's `U` (owner-court) surface AND the `infra` label. Returns:

      "infra"       — (#1026) at least one named ticket carries the `infra`
                      label: an infra-caused release block is NEVER an owner
                      question — the FLOW session must route it to the infra
                      lane (open/update the infra ticket + tag
                      `GATEKEEPER-ACTION (INFRA)` on the hub), not ask the owner.
                      Checked FIRST (highest priority).
      "in_u"        — at least one named ticket is in the box's U set (the owner
                      CAN click it): the `❓ ASKED`/`❓ NEEDS YOU` is honest.
      "not_in_u"    — the U set is determinable and NONE of `numbers` is in it
                      (the needs-answer/decision/owner-action label never landed,
                      or the ticket is closed): the owner sees U without it.
      "unmeasurable"— the U set could not be determined (no fresh cache AND a gh
                      failure): FAIL-OPEN, never block a question on a hiccup.

    COST (#1025/#1026): the tickets-status cache (footer-refreshed, authority-
    correct for this box) is consulted FIRST (zero gh); only when it does not
    already confirm membership does it fall back to a SINGLE `gh issue list
    --search` — the "one gh call at most" ceiling. #1026 folds the `infra`
    detection INTO that same single call (`label:infra` added to the U-label
    search, `--json number,labels` read) so no second gh call is added. The full
    `--waiting` derivation (several gh searches) is deliberately NOT run from a
    Stop hook. The label search over-approximates scope (label-based, no #654
    stream-ownership exclusion), so it is biased toward `in_u`/allow: a U block
    fires only when the label is provably NOT on any named ticket.

    #1026 cache-path note: the fresh-cache fast-allow returns "in_u" without
    seeing labels, so IN ISOLATION a ticket BOTH in the cached U set AND
    `infra`-labelled would read "in_u" (allow), not "infra". This hole is MOOT
    for the gate's only caller (`gates.questionscope.decide`): it invokes this
    function ONLY when the box's U count is 0, i.e. the cached U set is empty/
    None, so `nums & cached` never triggers the fast-path — the gh fallback
    always runs and the infra label is always detected. It stays documented for
    any future caller with a non-empty U; even there it is safe (such a ticket IS
    visible to the owner — no #1025 blindness — and the zero-gh TEXT-shape
    trigger is the strong, U-independent guard)."""
    import statusbar
    import time as _time
    nums = set()
    for n in (numbers or []):
        try:
            nums.add(int(str(n).strip().lstrip("#")))
        except (TypeError, ValueError):
            continue
    if not nums:
        return "in_u"                      # nothing checkable — never block
    if now is None:
        now = _time.time()
    # Fast-allow: a FRESH cache that already lists a named ticket in U (zero gh).
    try:
        cached, ts = statusbar.user_waiting_numbers(cwd, home=home)
    except Exception:  # noqa: BLE001 — a cache read failure just skips fast-allow
        cached, ts = None, None
    if (cached is not None and ts is not None
            and 0 <= (now - ts) <= cache_max_age_s and (nums & cached)):
        return "in_u"
    # Fallback: ONE gh search for this repo's open owner-court tickets, with
    # `label:infra` folded in (#1026) so the infra detection rides the SAME
    # single call. Also covers the just-added-label lag a fresh cache can miss
    # (a live label check). None → unmeasurable (fail-open).
    run = runner or _default_u_runner
    label_q = "label:infra," + ",".join(QUESTION_U_LABELS)
    out = run(["gh", "issue", "list", "--state", "open", "--search", label_q,
               "--json", "number,labels", "-L", "200"], cwd)
    if out is None:
        return "unmeasurable"
    try:
        data = json.loads(out or "[]")
        by_num = {}
        for x in data:
            if isinstance(x, dict) and "number" in x:
                by_num[int(x["number"])] = {
                    (lb or {}).get("name") for lb in (x.get("labels") or [])}
    except (ValueError, TypeError, KeyError):
        return "unmeasurable"
    # #1026: an `infra`-labelled named ticket wins — infra lane, not owner court.
    if any("infra" in by_num.get(n, set()) for n in nums):
        return "infra"
    # #1025: a named ticket PRESENT in this label-search result matched a
    # user-waiting label (it is not infra, and the search is `label:infra,<U>`),
    # so presence ⇒ in U. A label-less runner stub (older tests) also lands here.
    return "in_u" if any(n in by_num for n in nums) else "not_in_u"


# #948: hard cap on question-map supplement gh calls per refresh — the map is
# TYPICALLY 0-3 entries, but a pathological accumulation must not burn the
# shared #370 GraphQL budget. 10 is generous enough for any realistic box.
_QMAP_SUPPLEMENT_CAP = 10


def _question_map_u_supplement(rows, root, runner):
    """#948: a DICT ``{number: row_dict}`` of OPEN, user-waiting tickets
    referenced in the question map but ABSENT from the slice search ``rows``.

    On a shared-gh-identity/app-token box the slice is ``label:stream:<user>``
    only. A ticket authored via the shared token but lacking that label is
    invisible to the search. Meanwhile the question-map entry referencing ``#N``
    excludes the ping from the ticketless count (dedup). The ticket falls through
    BOTH paths -> footer ``U 0`` while a real question is pending.

    Returns a DICT ``{int: {"labels": [...], "title": str, "createdAt": str}}``
    so the caller can BOTH add ``len(result)`` to the cache count AND merge the
    rows into the ``--waiting`` listing — keeping the ONE-derivation invariant
    (#367/#391, #948 review MAJOR-2). An empty dict on any error (fail-safe:
    never inflate U off an unreadable map or failed gh).

    Checks ``state == "OPEN"`` for every fetched ticket (#948 review MAJOR-1):
    a CLOSED ticket with a stale ``needs-answer`` label must never inflate U.
    Capped at ``_QMAP_SUPPLEMENT_CAP`` gh calls per refresh (#948 review
    MINOR-3) to protect the #370 GraphQL budget.

    ``runner(argv, cd)`` is the caller's ``_out`` (a subprocess wrapper); ``rows``
    is the ``_union_open_issues`` / ``_slice_mine_and_handed`` result dict."""
    import statusbar as _sb
    try:
        refs = _sb.question_map_ticket_refs(root)
    except Exception:
        return {}
    if not refs:
        return {}
    result = {}
    checked = 0
    for qn in refs:
        if qn in rows:
            continue
        if checked >= _QMAP_SUPPLEMENT_CAP:
            break
        checked += 1
        raw = runner(["gh", "issue", "view", str(qn),
                      "--json", "labels,state,title,createdAt"], root)
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        # #948 review MAJOR-1: only OPEN tickets count.
        state = obj.get("state", "")
        if isinstance(state, str) and state.upper() != "OPEN":
            continue
        qlabels = obj.get("labels")
        if _row_is_user_waiting(qlabels):
            result[qn] = {
                "labels": qlabels,
                "title": obj.get("title", ""),
                "createdAt": obj.get("createdAt", ""),
            }
    return result


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
# direction — never a false accusation). This keeps the watchdog's detached
# quals refresher (`--snapshot-json`, #1067 1d, which runs this same tagging)
# bounded so its 180s child timeout has margin (~25 × <1s), on a 5-min snapshot
# TTL; the session's own on-demand `--ops-wait` is uncapped in wall-clock time
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

# #881 — the convergence-clock parking marker: `Ops-wait-target: <event> by
# <YYYY-MM-DD>`. Line-anchored (^), colon-required, mirroring #818's
# `Acceptance-reminder:` family. A date-less marker is NOT valid (an event
# without a date is the indefinite parking #881 attacks). The required
# ` by YYYY-MM-DD` tail is the falsifiable convergence clock — when the
# self-declared date passes, the member tags `converge!`. A hyphenated
# derivative (`Ops-wait-target-draft:`) does NOT match because the regex
# requires `[ \t]*:` immediately after the name (no `-`). Mid-line prose
# mentions are excluded by `^`. Matched case-insensitively.
_OPS_WAIT_TARGET_RX = re.compile(
    r"(?im)^[ \t]*Ops-wait-target[ \t]*:[ \t]*(?P<event>\S[^\n]*?)"
    r"[ \t]+by[ \t]+(?P<date>\d{4}-\d{2}-\d{2})[ \t.]*$"
)

# #881 — the age ceiling: a W member older than this many calendar days with
# no valid FUTURE-dated target tags `converge!`. 14 days halves the observed
# multi-week rot horizon (montalu3, 4 weeks) while sitting an order of
# magnitude past every existing cadence (24h push, 3d tacit), so it catches
# only genuine rot, never legitimate week-scale evidence collection. A valid
# future target SUPPRESSES the ceiling.
OPS_WAIT_CONVERGE_AGE_D = 14


def _comment_is_final_reminder(body):
    """True iff `body` carries the #818 line-anchored `Acceptance-reminder:`
    marker (the tacit-window opener). None/empty/non-str → False."""
    if not isinstance(body, str) or not body.strip():
        return False
    return bool(_FINAL_REMINDER_RX.search(body))


def _comment_ops_wait_target(body):
    """#881: extract the `Ops-wait-target: <event> by <YYYY-MM-DD>` date from
    `body`, or None if no valid marker is present. Returns the date string
    (YYYY-MM-DD) only — the event text is for human reading, not machine use.
    A format-valid but calendar-invalid date (2026-99-99) returns None — a
    typo'd date degrades to "no valid marker" (→ `no-target!`), matching the
    design's "a date-less marker is NOT valid" stance.
    None/empty/non-str → None."""
    if not isinstance(body, str) or not body.strip():
        return None
    m = _OPS_WAIT_TARGET_RX.search(body)
    if not m:
        return None
    date_str = m.group("date")
    # Validate the date is calendar-valid, not just format-valid.
    from datetime import datetime
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None                                  # 2026-99-99 → no marker
    return date_str


def _comment_ops_wait_target_full(body):
    """#944: extract BOTH the event text AND the date from the
    `Ops-wait-target: <event> by <YYYY-MM-DD>` marker. Returns
    `(event, date)` or `(None, None)` if no valid marker is present.
    The event text is the part between the colon and ` by <date>` —
    it tells us WHAT the ticket is waiting on (deploy/release/client reply).
    None/empty/non-str → (None, None)."""
    if not isinstance(body, str) or not body.strip():
        return None, None
    m = _OPS_WAIT_TARGET_RX.search(body)
    if not m:
        return None, None
    date_str = m.group("date")
    from datetime import datetime
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None, None
    event = m.group("event").strip() if m.group("event") else None
    return event, date_str


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
        res.setdefault("own_target", None)           # #881
        res.setdefault("own_target_event", None)     # #944
        return res
    if isinstance(res, (tuple, list)) and len(res) >= 2:
        return {"own": res[0], "any": res[1],
                "own_cited": res[0], "own_oldest": None,
                "own_final_reminder": None,
                "own_target": None,
                "own_target_event": None}
    return None


def _is_own_login(login, self_login):
    """App-aware identity match for own-comment detection (#904).

    On App-token streams, ``_stream_self_login()`` returns the ``app/``-prefixed
    form (``STREAM_APP_BOT_LOGIN = "app/odoo-erp-stream-tokens"``), but
    GitHub's ``gh issue view --json comments`` renders the comment
    ``author.login`` as the bare slug (``"odoo-erp-stream-tokens"``).

    This helper accepts both directions of the ``app/`` prefix mismatch
    (and the exact-match case) while rejecting any truly different login.
    """
    if not login or not self_login:
        return False
    if login == self_login:
        return True
    # #904: strip "app/" and compare bare slugs. If we reach here, the
    # exact match already failed, so at least one side differs — when
    # both sides are bare-identical the early return above fires first.
    bare_login = login[4:] if login.startswith("app/") else login
    bare_self = self_login[4:] if self_login.startswith("app/") else self_login
    return bare_login == bare_self


def _stream_self_login():
    """THIS box's own gh identity for own-comment matching (#463, #904).

    Returns the ``app/``-prefixed form on App-token boxes
    (``STREAM_APP_BOT_LOGIN``; NO network call — ``gh api user`` 403s
    structurally), the real gh login on a PAT box, or None when
    unresolvable. None is not fatal: ``_stale_ops_wait_flagged``
    degrades to the any-comment definition (the SAFE direction — it
    under-flags rather than false-accuse).

    Note (#904): GitHub renders ISSUE ``author.login`` as the ``app/``
    form, but COMMENT ``author.login`` as the bare slug. The
    ``_is_own_login`` helper normalizes both directions.

    #918: a stray App-token directory on a PAT box
    (``_is_gh_app_token_box()`` true but the active auth is a PAT)
    made this function return ``STREAM_APP_BOT_LOGIN`` instead of the
    real PAT login — every own-comment comparison then failed (wrong
    identity), producing invisible own comments in
    ``_issue_comment_ages()`` (``_bounce_round`` no longer uses
    self_login — it counts prio:bounce events since #942). Fixed by
    validating the
    App-token detection: if ``_gh_login()`` succeeds (returns a real
    login), the box is NOT operating as an App-token box (a genuine
    App token makes ``gh api user`` 403 → ``_gh_login()`` = None).

    Issue 1129: on a genuine App-token box the identity is the App that
    MINTED the token in use (``_gh_app_token_slug()``, the ``.app``
    sidecar next to it) — a second-App stream (``odoo-erp-stream-tokens-2``)
    comments as that slug — falling back to ``STREAM_APP_BOT_LOGIN`` when
    no valid record exists."""
    import airuleset
    if _is_gh_app_token_box():
        # Validate: a genuine App-token box has no user identity
        # (_gh_login() returns None because gh api user 403s).
        # If _gh_login() succeeds, the dir is stray and the real
        # PAT login is the correct identity (#918).
        real_login = airuleset._gh_login()
        if real_login is not None:
            return real_login
        # Issue 1129: the App that actually minted this box's token.
        slug = _gh_app_token_slug()
        if slug:
            return "app/" + slug
        return airuleset.STREAM_APP_BOT_LOGIN
    return airuleset._gh_login()


# Issue 1129: the SAME identifier rule odoo-erp `push-stream-tokens.sh`
# (`safe_name`) and `mint-installation-token.sh` apply to an App slug.
_APP_SLUG_RX = re.compile(r"[A-Za-z0-9._-]+")


def _gh_app_token_slug():
    """The GitHub App slug recorded next to the token THIS box's `gh` uses,
    or None when there is no usable record (issue 1129).

    The token in use is `~/.config/gh-app-tokens/primary` (what
    `gh-app-token` with no argument reads) — a symlink the minting path
    (odoo-erp `scripts/gh-app/push-stream-tokens.sh`) points at the
    per-repo token file `<owner>__<name>`, next to which it already writes
    the `.expires` sidecar. The slug record is the SIBLING `.app` sidecar of
    the resolved token file (`realpath(primary) + ".app"`; a regular-file
    `primary` therefore reads `primary.app`). Streams minted by a second App
    (streams.conf `app=odoo-erp-stream-tokens-2`) author their comments as
    that App's slug, so the constant `STREAM_APP_BOT_LOGIN` misreads every
    own comment as foreign.

    Local, static read — no network (the #356 rule for App-token boxes).
    Returns None on a missing/unreadable sidecar or content that is not a
    single identifier, so the caller keeps today's constant (never guesses a
    wider identity — accepting any `odoo-erp-stream-tokens*` slug would let
    another stream's comment refresh this stream's W freshness)."""
    try:
        primary = _gh_app_token_dir() / "primary"
        sidecar = Path(os.path.realpath(primary) + ".app")
        content = sidecar.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    if not _APP_SLUG_RX.fullmatch(content):
        return None
    return content


def _ages_from_comments(comments, self_login):
    """The PURE parse half of `_issue_comment_ages` (#1067 (a)): turn a gh
    `comments` list into the `{own, any, own_cited, own_oldest,
    own_final_reminder, own_target, own_target_event}` dict, or None when
    `comments` is not a usable list. The element shape is identical whether it
    came from `gh issue view --json comments` (per-issue) or the per-row
    `comments` of `gh issue list --json number,comments` (the batched prefetch),
    so both feed the SAME parse → byte-identical ages. This parse never depends
    on the clock, so it takes no `now` (its caller `_issue_comment_ages` keeps a
    `now` param only for the injectable-seam signature symmetry)."""
    if not isinstance(comments, list):
        return None
    own_ts = any_ts = own_cited = own_oldest = own_final_reminder = None
    own_target = None                                # #881: newest valid target date
    own_target_event = None                          # #944: event text of the winning target
    own_target_ts = 0                                # comment ts of the winning target
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
        if self_login and _is_own_login(login, self_login):
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
            # #881: the newest own comment (by ts) carrying a valid
            # `Ops-wait-target: <event> by <date>` marker. Newest
            # comment-ts wins (revising a target = posting a newer
            # comment), NOT the date inside the marker.
            tgt_event, tgt_date = _comment_ops_wait_target_full(body)
            if tgt_date is not None:
                if own_target is None or ts > own_target_ts:
                    own_target = tgt_date
                    own_target_event = tgt_event
                    own_target_ts = ts
    return {"own": own_ts, "any": any_ts,
            "own_cited": own_cited, "own_oldest": own_oldest,
            "own_final_reminder": own_final_reminder,
            "own_target": own_target,
            "own_target_event": own_target_event}


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
    # #1067 (a): the pure parse is factored into `_ages_from_comments` so the
    # SAME logic runs on a batched-prefetch `comments` list (identical shape) and
    # on this per-issue read — through the shared `ages_fn` seam.
    return _ages_from_comments(comments, self_login)


# #1067 slice 1b — the paginated GraphQL search that carries only the newest
# comments per W member. `first: %d` = OPS_WAIT_PREFETCH_PAGE_SIZE issues per
# page; `comments(last: %d)` = the newest OPS_WAIT_PREFETCH_COMMENT_CAP comments
# (what every freshness anchor needs) plus `totalCount` (drives the >cap
# fallback). `$q` is the search string, `$cursor` the page cursor (null → page 1).
_OPS_WAIT_PREFETCH_GQL = (
    "query($q: String!, $cursor: String) {"
    " search(query: $q, type: ISSUE, first: %d, after: $cursor) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { ... on Issue { number"
    " comments(last: %d) { totalCount nodes {"
    " author { login } createdAt body } } } } } }"
) % (OPS_WAIT_PREFETCH_PAGE_SIZE, OPS_WAIT_PREFETCH_COMMENT_CAP)


def _ops_wait_prefetch_comments(member_quals, root, limit=None):
    """#1067 slice 1b: ONE paginated `gh api graphql` search per member-defining
    qual, returning `{number: comments}` — the newest comments of every W
    member. Replaces the per-member `gh issue view --json comments` loop behind
    `--ops-wait` / the footer's stale-W count (one gh call per W member — 102 s
    for 74 members on montalu1, #1067).

    WHY GraphQL search, not `gh issue list` (slice 1): `gh issue list --search
    "<qual> label:ops-wait,needs-acceptance" --json number,comments` cannot page
    by cursor and cannot bound the nested comments connection, so it pulls up to
    the full 100 comments of up to `--limit` issues in ONE request — which
    HTTP-502s (`--limit 500`) / 504s (`--limit 100`) on a busy stream repo
    (odoo-erp), leaving the slice-1 prefetch returning 0 rows there. The search
    query is bounded on BOTH axes: `first: OPS_WAIT_PREFETCH_PAGE_SIZE` issues
    per page (paged by `pageInfo.endCursor` until `hasNextPage` is false, capped
    at `ceil(OPS_WAIT_PREFETCH_LIMIT / page-size)` pages), and `comments(last:
    OPS_WAIT_PREFETCH_COMMENT_CAP)` newest comments per issue. Live-measured
    2026-09-23: 74 montalu W members in 8 pages, ≈ 12 s total (vs the old 502/504
    and the 115–120 s `slice-quals --ops-wait`).

    Each page carries its own 20 s timeout; a page that FAILS (gh error / parse
    error / GraphQL `errors`) stops that qual's paging but KEEPS the pages
    already read (their members stay in the map), and every unfetched member
    falls back to the per-issue read.

    The GraphQL comment node carries the SAME THREE keys `_ages_from_comments`
    consumes (`author: {login}`, `createdAt`, `body`) — `gh issue view --json
    comments` also carries extra keys (id/url/…) this query omits, but the parse
    reads only those three, so the ages are IDENTICAL either way — EXCEPT
    `comments(last: 100)` carries only the newest
    100, so a row whose `totalCount > OPS_WAIT_PREFETCH_COMMENT_CAP` is EXCLUDED
    from the map and falls back to the fully-paginated per-issue read (its OLD
    anchors could otherwise be missing — never a false `stale!`, #539/#570).
    This is slice 1b's cap rule; it replaces slice 1's `len(comments) < 100`
    check (a full `comments(last: 100)` list is always exactly 100 at/over the
    cap, so length alone can no longer distinguish a truncated list from a
    naturally-100-comment one — `totalCount` can).

    `member_quals` are the SAME quals that produced the W members (ONE
    derivation, #367 — no parallel membership query): `cmd_slice_quals`'s
    `_slice_quals(user)` (usually ONE qual on a shared-account stream box) or
    `cmd_core_quals`'s `_obligation_quals()`. A qual whose gh read fails / is
    unparsable on its FIRST page contributes nothing (its members fall back);
    an empty `member_quals`, or an unresolvable repo slug, returns `{}`
    (everything falls back — byte-identical to today's failure mode). Over-fetch
    (a qual that also matches non-W tickets) is harmless: only members present in
    the `ops_wait` set are ever consumed."""
    import airuleset
    from gates import ghread
    out = {}
    if not member_quals:
        return out
    # The search string needs the repo slug embedded (`repo:<slug>`). Resolve it
    # with the FLEET fork-aware, LOCAL-git-only resolver (`ghread.canonical_slug`,
    # the ONE slug reader the open-issue snapshot / cross-stream / release-state
    # readers share, #1094) — NEVER the origin-only `_repo_slug`: on a fork clone
    # (david1-4 — origin = the fork, issues disabled) `_repo_slug` returns the
    # FORK slug, whose `search(query: "repo:<fork> …")` silently returns nothing
    # (prefetch inert on exactly the busiest streams) or, worse, a colliding fork
    # issue's comments for a canonical member. `canonical_slug` resolves the base
    # repo the way `gh` does, with zero network (survives quota exhaustion, and
    # removes the extra `gh repo view` round-trip). An empty / unresolvable slug
    # can't build a valid search → everything falls back per-issue.
    slug = (ghread.canonical_slug(root) or "").strip()
    if not slug:
        return out
    label_q = "label:" + ",".join(OPS_WAIT_PREFETCH_LABELS)
    lim = limit if limit else OPS_WAIT_PREFETCH_LIMIT
    max_pages = max(1, -(-lim // OPS_WAIT_PREFETCH_PAGE_SIZE))  # ceil(lim/size)
    base = "repo:%s is:issue is:open" % slug
    for qual in member_quals:
        parts = [base]
        if qual:
            parts.append(qual)
        parts.append(label_q)
        search = " ".join(parts)
        cursor = None
        for _page in range(max_pages):
            # `-f` (raw string) for every String-typed variable — `$q`/`$cursor`
            # are `String`, so avoid `-F`'s number/bool/null/@file coercion.
            gh_args = ["api", "graphql", "-f",
                       "query=" + _OPS_WAIT_PREFETCH_GQL, "-f", "q=" + search]
            if cursor:
                gh_args += ["-f", "cursor=" + cursor]
            raw = airuleset._gh_out(*gh_args, cwd=root, timeout=20)
            if not raw:
                break                             # page failure -> keep earlier
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                break
            if not isinstance(data, dict) or data.get("errors"):
                break                             # GraphQL error -> keep earlier
            search_res = (data.get("data") or {}).get("search")
            if not isinstance(search_res, dict):
                break
            for node in (search_res.get("nodes") or []):
                if not isinstance(node, dict):
                    continue
                num = node.get("number")
                cconn = node.get("comments")
                if not isinstance(num, int) or not isinstance(cconn, dict):
                    continue
                nodes = cconn.get("nodes")
                total = cconn.get("totalCount")
                # A row with MORE than the cap comments carries only its newest
                # 100 -> possibly missing an OLD anchor -> exclude, fall back to
                # the fully-paginated per-issue read (never a false `stale!`).
                if (isinstance(nodes, list) and isinstance(total, int)
                        and total <= OPS_WAIT_PREFETCH_COMMENT_CAP):
                    out.setdefault(num, nodes)
            page_info = search_res.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                break                             # no cursor -> can't page on
    return out


def ops_wait_ages_fn(ops_wait, root, member_quals):
    """#1067 (a): return an `ages_fn(n)` closure backed by the batched comment
    prefetch, memoized per call. A member present in the prefetch is parsed from
    it (`_ages_from_comments`); one absent from it (truncated list / label just
    changed / `member_quals` None) falls back to the per-issue `_issue_comment_
    ages`. The ONE seam shared by the `--ops-wait` reason column
    (`cli_quals_cmd._ops_wait_flag_sets`) and the footer's stale-W count
    (`_compute_net_stale_w` in `cmd_tickets_status`), so BOTH pay O(quals) gh
    calls, not O(members) — with results byte-identical to the per-member path.

    Routed through `airuleset.*` (not bare names) so a test mocking
    `airuleset._issue_comment_ages` / `_stream_self_login` / `_gh_out` intercepts,
    exactly as the pre-#1067 inline `_ages` closure did.

    The prefetch runs ONLY when there are actually W members to serve
    (`ops_wait` non-empty) — an empty W set makes ZERO gh calls (the common
    idle-box footer case), never firing a per-qual `gh issue list` for a fetch
    nothing will consume."""
    import airuleset
    import cli_parallel
    self_login = airuleset._stream_self_login()
    prefetch = (airuleset._ops_wait_prefetch_comments(member_quals, root)
                if (member_quals and ops_wait) else {})
    cache = {}
    # #1067 slice 1c (b): the >100-comment fallback members (absent from the
    # batched 1b prefetch) are read in PARALLEL, bounded to the SAME sorted cap
    # the flag-set consumers (`_stale_ops_wait_flagged` etc.) query, so a large
    # W set's per-issue `gh issue view` fallbacks overlap instead of running one
    # at a time. Identical results to the lazy path — `_ages` still reads `cache`
    # first; a per-call failure is omitted by `run_parallel` and re-derived
    # lazily by `_ages` (the same fail-safe direction as before).
    if ops_wait:
        fallback = [n for n in sorted(ops_wait)[:OPS_WAIT_STALE_MAX_FETCHES]
                    if n not in prefetch]
        if fallback:
            cache.update(cli_parallel.run_parallel(
                fallback,
                lambda n: airuleset._issue_comment_ages(
                    n, self_login, None, cwd=root)))

    def _ages(n):
        if n not in cache:
            if n in prefetch:
                cache[n] = airuleset._ages_from_comments(prefetch[n], self_login)
            else:
                cache[n] = airuleset._issue_comment_ages(
                    n, self_login, None, cwd=root)
        return cache[n]

    return _ages


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


def _compute_net_stale_w(ops_wait, cwd=None, ages_fn=None, now=None,
                         member_quals=None):
    """#989: compute the net stale W count — ``_stale_ops_wait_flagged`` minus
    the ``_tacit_window_flagged`` exemption.

    #1067 (a): when ``ages_fn`` is not supplied and ``member_quals`` is given,
    build the shared batched-prefetch ``ages_fn`` (``ops_wait_ages_fn``) so the
    footer's hot stale-W refresh pays O(quals) gh calls, not O(members) — the
    SAME seam ``_ops_wait_flag_sets`` uses. An explicit ``ages_fn`` still wins
    (test fakes / callers that already share a fetch).

    ``_tacit_window_flagged`` returns a 2-tuple ``(tacit_wait, tacit_close)``
    that must be unpacked and unioned before subtraction.  The #986 review
    commit (55864130) inlined this subtraction in ``cmd_tickets_status`` but
    forgot the unpacking, producing ``set - tuple`` → ``TypeError``.  This
    helper encapsulates the correct pattern (matching ``cli_quals_cmd.py``
    lines 528-531) so both call sites share the tested derivation.

    Returns the count (``int``) of net-stale members.  Raises on any
    upstream failure (the caller's ``try/except`` is the fail-open wrapper)."""
    if ages_fn is None and member_quals:
        ages_fn = ops_wait_ages_fn(ops_wait, cwd, member_quals)
    stale = _stale_ops_wait_flagged(ops_wait, cwd=cwd, ages_fn=ages_fn,
                                    now=now)
    tacit_wait, tacit_close = _tacit_window_flagged(ops_wait, cwd=cwd,
                                                    ages_fn=ages_fn, now=now)
    net_stale = stale - (tacit_wait | tacit_close)
    return len(net_stale)


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
# #1053: `gk-processing` (gk's live-work state) is the third gk hand-off label,
# so a W-parked row also carrying it is the same `gk-handoff!` contradiction.
_GK_HANDOFF_LABELS = ("needs-gatekeeper", "ready-for-review", "gk-processing")

# #636 review 🟡: a `prio:bounce` OVERRIDES a co-present gk hand-off label back to
# "the STREAM's own court" (the #313 pt-2 override that `_slice_mine_and_handed`
# and NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS already honour — a bounced ticket is
# NOT counted in gk). So a bounced ticket is NEVER post-release limbo: it must be
# REWORKED in I, not re-handed-off. Excluding it keeps the gk-handoff! flag
# consistent with the handed/gk count and stops the nudge driving a premature
# re-hand-off (a bounce↔hand-off loop).
_GK_HANDOFF_BOUNCE_OVERRIDE = "prio:bounce"


def _count_bounce(rows):
    """Count of rows carrying the `prio:bounce` label (#1056 L1 (b)) — the
    primitive behind the footer's `· bounce K`. `rows` is a
    `{number: {"labels": [...]}}` dict; the count is over EXACTLY the rows
    passed. The footer feeds it the FULL role-filtered partition (workable ∪
    user-waiting ∪ ops-wait) via `count_bounce_all` (#1056 L2 (i0)), so a
    returned bounce is counted regardless of which parking bucket it sits in.
    A bounce row that also carries a stale hand-off label is counted in `gk`
    (excluded from the displayed `I N`) yet still counted here — a returned
    bounce is urgent regardless of a stale label. A missing/unreadable/malformed
    labels value counts as no-bounce (the safe direction — never crash the
    footer refresh, #1056 review R2)."""
    n = 0
    for row in (rows or {}).values():
        labels = row.get("labels") if isinstance(row, dict) else None
        names = {(lb or {}).get("name") for lb in (labels or [])
                 if isinstance(lb, dict)}
        if _GK_HANDOFF_BOUNCE_OVERRIDE in names:
            n += 1
    return n


def count_bounce_all(workable, waiting, ops_wait):
    """Count EVERY open `prio:bounce` across the FULL footer partition — the
    union of `workable`, `waiting` (U) and `ops_wait` (W) (#1056 L2 (i0)).

    `_partition_workable` already pulls a `prio:bounce`+ops-wait row into
    `workable`, so the common returned-bounce lands in the I bucket; this union
    ALSO catches the rarer bounce parked on a genuine OWNER answer
    (`needs-answer`/`needs-decision` + `prio:bounce`), which deliberately STAYS
    in U (you cannot rework without the answer) yet is still an open, urgent
    returned bounce the footer's `· bounce K` must count. "Count EVERY open
    prio:bounce in the slice regardless of W/U parking" — the montalu1 read the
    supervisor cited (footer `bounce 1` while five bounces were open).

    Pure, network-free — a bucket-merge over already-fetched rows (the #367
    one-derivation invariant: the SAME partitioned rows the footer's I/U/W
    counts come from). None/empty buckets are treated as {}."""
    merged = {}
    merged.update(ops_wait or {})
    merged.update(waiting or {})
    merged.update(workable or {})     # workable last: a row can appear once
    return _count_bounce(merged)


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


def _converge_flagged(rows, cwd=None, now=None, self_login=None, ages_fn=None):
    """#881: the set of ops-wait (W) member numbers to tag `converge!` — a
    parked ticket that must issue a CONVERGENCE VERDICT (close / unpark /
    re-lane / re-target-with-citation / owner-digest) instead of another
    freshness push.

    Two prongs (OR):
    1. TARGET-MISS: the newest valid own `Ops-wait-target:` marker's date
       has PASSED (< today UTC — the safe direction: fires up to ~2h late
       for Europe/Bratislava, never early).
    2. AGE CEILING: no valid FUTURE-dated target exists AND createdAt age
       > OPS_WAIT_CONVERGE_AGE_D (14 days). A valid future target
       SUPPRESSES the ceiling — the session's declared expectation is
       respected.

    Fail-safe (#539/#570 bias): gh error / missing data → UNTAGGED."""
    from datetime import datetime, timezone
    if now is None:
        now = time.time()
    today_str = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    flagged = set()
    for number, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        res = ages_fn(number) if ages_fn else None
        res = _norm_ages(res)
        if res is None:
            continue                                 # gh error -> fail-safe
        own_target = res.get("own_target")
        # Prong 1: target-miss — the declared date has passed.
        if own_target is not None and own_target < today_str:
            flagged.add(number)
            continue
        # Prong 2: age ceiling — no valid future target + old ticket.
        if own_target is not None and own_target >= today_str:
            continue                                 # future target suppresses
        # own_target is None — no marker at all.
        created_ts = _parse_iso_ts(row.get("createdAt"))
        if created_ts is None:
            continue                                 # unparseable -> fail-safe
        age_days = (now - created_ts) / 86400
        if age_days > OPS_WAIT_CONVERGE_AGE_D:
            flagged.add(number)
    return flagged


def _no_target_flagged(rows, cwd=None, now=None, self_login=None, ages_fn=None):
    """#881: the set of ops-wait (W) member numbers to tag `no-target!` — a
    parked ticket with NO valid `Ops-wait-target:` marker on any own comment.

    Fires IMMEDIATELY on park (no grace period): parking requires a target
    marker; its absence is the defect the tag surfaces. The composition
    layer (`_ops_wait_flag_sets`) subtracts `verdict_in_flight | converge`
    from this set — a converge! member already demands a verdict (which
    includes setting a target), and a tacit/unpark/gk-handoff member has
    a verdict in flight.

    Fail-safe: gh error / comment fetch failure → UNTAGGED."""
    flagged = set()
    for number, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        res = ages_fn(number) if ages_fn else None
        res = _norm_ages(res)
        if res is None:
            continue                                 # gh error -> fail-safe
        own_target = res.get("own_target")
        if own_target is None:
            flagged.add(number)
    return flagged


def _deploy_target_flagged(rows, cwd=None, now=None, self_login=None,
                           ages_fn=None):
    """#944: the set of ops-wait (W) member numbers to tag `deploy-target!` —
    a parked ticket whose newest valid `Ops-wait-target:` event text names a
    deploy/release. PURE regex on the event text (from `_issue_comment_ages`'s
    `own_target_event`), NO external reads.

    Fail-safe: gh error / no target / no event text / non-deploy event →
    UNTAGGED (never a false deploy-target claim)."""
    from watchdog.release_watch import is_deploy_target
    flagged = set()
    for number, row in (rows or {}).items():
        if not isinstance(row, dict):
            continue
        res = ages_fn(number) if ages_fn else None
        res = _norm_ages(res)
        if res is None:
            continue
        event = res.get("own_target_event")
        if is_deploy_target(event):
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


def _more_restrictive(a, b):
    """The MORE RESTRICTIVE (lower-authority) of two profiles on the lattice
    full > branch-merge > fork-no-merge. `AUTHORITY_PROFILES` is ordered
    strongest -> weakest, so a HIGHER index is more restrictive; the tuple
    ordering IS the lattice (no separate rank table). Equal profiles return `a`
    (== b). #828: this is how the marker CAPS the base (`more-restrictive-of`)."""
    import airuleset
    order = airuleset.AUTHORITY_PROFILES
    return a if order.index(a) >= order.index(b) else b


def _authority_base(user):
    """The marker-FREE base authority for `user`: `(profile, source)` from the
    reduced-stream map -> ci-runner recognition -> full allow-list -> fail-safe
    `fork-no-merge` default. This is the base the #828 marker can only LOWER,
    never raise, so BOTH `_authority_decision` and `cmd_authority --explain`
    derive the base from this ONE function (no parallel re-derivation that could
    desync — the #486/#821 SSOT discipline). `source` is one of 'per-user map' |
    'ci-runner (GitHub-hosted)' | 'ci-runner (GitHub-hosted, container)' |
    'full-authority account' | 'default (unmapped)'. Resolution order
    (airuleset#827/#839): the reduced-stream map row (`AUTHORITY_BY_USER`) is
    checked FIRST so a reduced stream can never be elevated by a (bug) dual
    membership; then the GitHub-hosted CI runner (`_github_ci_runner_source` —
    airuleset's OWN CI, unspoofable, #839; the source names the CONTAINER arm
    distinctly); then the explicit full-authority allow-list
    (`FULL_AUTHORITY_USERS`); else the fail-SAFE `fork-no-merge` default (the
    pre-#827 fail-OPEN `full` catch-all is gone — every legitimate full account
    is enumerated in the registries)."""
    import airuleset
    if user in airuleset.AUTHORITY_BY_USER:
        return airuleset.AUTHORITY_BY_USER[user], "per-user map"
    ci_src = airuleset._github_ci_runner_source(user)
    if ci_src is not None:
        return "full", ci_src
    if user in airuleset.FULL_AUTHORITY_USERS:
        return "full", "full-authority account"
    return "fork-no-merge", "default (unmapped)"


def _authority_decision(cwd=None):
    """The authority resolution WITH its provenance, from ONE decision point:
    `(profile, source, raw_marker)`, where `source` is one of 'marker-lowered' |
    'per-user map' | 'ci-runner (GitHub-hosted)' |
    'ci-runner (GitHub-hosted, container)' | 'full-authority account' |
    'default (unmapped)'.
    `resolve_authority` (the hot path) and `cmd_authority --explain` (the #486
    decision log) both derive from this single function, so the printed log can
    never desync from the resolved profile, and it distinguishes the reduced-stream
    map ROW and the explicit full-authority allow-list from the fail-safe default
    that decides a genuinely unmapped user.
    Resolution (airuleset#828, owner decision A — the marker is a CAP, not an
    override): compute the marker-FREE base (`_authority_base`), then take the
    MORE RESTRICTIVE of `(base, marker)`. A valid `<!-- airuleset:authority=... -->`
    marker may only LOWER the base authority, NEVER raise it — a marker that
    would raise is IGNORED (the base source stands), closing the self-elevation
    vector: a reduced stream editing its OWN (stream-editable) CLAUDE.md can no
    longer grant itself `full`. `full` is granted EXCLUSIVELY via the per-user
    map / full allow-list / ci-runner recognition, never a marker. When the
    marker did lower, the source is 'marker-lowered'; when it equals or would
    raise the base (or is absent/invalid), the base source stands (`raw` is still
    surfaced so `cmd_authority --explain` can report an ignored raise)."""
    import airuleset
    raw = _authority_marker_raw(cwd)
    marker = raw if raw in airuleset.AUTHORITY_PROFILES else None
    user = airuleset._current_user()
    base, base_source = _authority_base(user)
    # #828: cap the base with the marker (more-restrictive-of). Only a marker
    # STRICTLY below the base applies (and reports 'marker-lowered'); an equal or
    # raising marker leaves the base and its source untouched.
    if marker is not None and marker != base \
            and _more_restrictive(base, marker) == marker:
        return marker, "marker-lowered", raw
    return base, base_source, raw


def resolve_authority(cwd=None) -> str:
    """The current stream's autopilot authority profile: the marker-free base
    (per-user map / full allow-list / ci-runner / fail-safe default) CAPPED by a
    project CLAUDE.md `airuleset:authority=<profile>` marker (cwd-relative) — the
    marker may only LOWER the base authority, never raise it (airuleset#828,
    owner decision A). This makes `airuleset.py authority` authoritative for both
    the autopilot skill and the `block-fork-no-merge-issue-close` hook (single
    source of truth). Derives the profile from the single `_authority_decision`
    so the CLI's `--explain` log can never name a source that disagrees with what
    actually resolved."""
    return _authority_decision(cwd)[0]


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    import airuleset
    if getattr(args, "maintainer_login", False):
        print(airuleset.MAINTAINER_GH_LOGIN)
        return
    if getattr(args, "self_login", False):
        # THIS box's own gh identity for the self-authored-close carve-out
        # (block-fork-no-merge-issue-close.sh, #463). Delegates to
        # _stream_self_login() which validates App-token-box detection
        # against the real gh auth (#918 — a stray App-token dir on a
        # PAT box no longer returns the wrong identity). Prints nothing
        # (empty) when the login cannot be resolved -> the hook's
        # fail-safe refuses the exemption (blocks), never guesses.
        login = _stream_self_login()
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
        # #829: anchor at the REPO ROOT too — the close-guard hook shells out to
        # BOTH `authority` and `authority --stream-label` from the SAME session
        # cwd, so this arm must honor a repo-root marker from a subdirectory
        # identically to the profile print below, or the two disagree and the
        # own-stream acceptance-close carve-out mis-fires.
        if resolve_authority(cwd=airuleset._repo_root() or None) != "full":
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
        # close-guard call every cycle. Resolved against the REPO ROOT (the
        # `root` computed above) — the same anchoring the plain `authority`
        # output now uses (#829), identical to every in-process consumer.
        user = airuleset._current_user()
        # #486/#828: derive BOTH the base profile AND the map= classification from
        # the SINGLE `_authority_base` (the same function `_authority_decision`
        # used), so the printed map= can never desync from the resolved source —
        # no parallel registry re-derivation. The CONTAINER arm is named distinctly
        # (airuleset#839) exactly as its source string is.
        base_profile, base_source = _authority_base(user)
        if base_source == "per-user map":
            map_val = base_profile
        elif base_source == "ci-runner (GitHub-hosted, container)":
            map_val = "GitHub-hosted CI runner (container) -> full"
        elif base_source == "ci-runner (GitHub-hosted)":
            map_val = "GitHub-hosted CI runner -> full"
        elif base_source == "full-authority account":
            map_val = "full-authority account -> full"
        else:  # "default (unmapped)"
            map_val = ("unmapped -> fork-no-merge (fail-safe; add to "
                       "AUTHORITY_BY_USER or FULL_AUTHORITY_USERS)")
        # #828: annotate what the marker actually DID — lowered / ignored-as-a-
        # raise / redundant / absent / invalid.
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
            if raw not in airuleset.AUTHORITY_PROFILES:
                state = "invalid(%r)" % raw
            elif source == "marker-lowered":
                state = "%s lowered (was %s)" % (raw, base_profile)
            elif raw != base_profile:
                # a valid marker that would RAISE was IGNORED (the #828 cap: `full`
                # comes only from the registries, never a stream-editable marker).
                state = "%s ignored (would raise %s)" % (raw, base_profile)
            else:
                state = "%s (== base %s)" % (raw, base_profile)
            mark = f"{state} (read from {mpath})"
        print(f"resolved={profile} via {source} "
              f"(marker={mark}; user={user} map={map_val}); "
              f"a marker <!-- airuleset:authority=<profile> --> can only LOWER "
              f"authority (more-restrictive-of), never raise it (#828).")


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


def _union_open_issues(quals, base, cwd=None, repo=None,
                       fields=("number", "title", "createdAt", "labels")):
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
    `cwd`'s git remote instead. `fields` (#1128) is the row shape; the
    default is unchanged, `stream-wait` adds `updatedAt` to fingerprint."""
    import airuleset
    seen, failed = {}, False

    # #1087 (b): fetch ONE ETag-cached REST snapshot of the repo's open issues
    # and filter each qual CLIENT-SIDE, so N per-qual GraphQL searches collapse
    # to ONE snapshot read (a 304 re-poll is budget-free). A qual whose
    # `--search` semantics the client-side matcher can't represent (free text,
    # in:title, ...), an unresolvable @me login, or a snapshot that couldn't be
    # read ALL fall back to the ORIGINAL per-qual GraphQL search below — so the
    # counts are never worse than the pre-#1087 behaviour (fail-safe: a snapshot
    # error over-reads via GraphQL exactly as before, never a silent under-count
    # that would false a /goal stop-proof).
    #
    # AIRULESET_QUALS_NO_SNAPSHOT=1 is an operability + test kill-switch (same
    # idiom as AIRULESET_DRAFT_RESCUE_DIR / AIRULESET_TEST_IGNORE_DISABLE): it
    # forces the pre-#1087 per-qual GraphQL path, so a box on which the snapshot
    # ever misbehaves reverts cleanly, and the ~40 hermetic quals tests that mock
    # only `_gh_out` keep exercising the GraphQL contract they were written for.
    snapshot = None
    if os.environ.get("AIRULESET_QUALS_NO_SNAPSHOT") != "1":
        try:
            from gates import ghread
            slug = repo or ghread.canonical_slug(cwd)
            if slug:
                snapshot, snap_err = ghread.list_open_issues_cached(
                    slug, cwd=cwd, timeout=20)
                if snap_err:
                    snapshot = None
        except Exception:
            snapshot = None

    me_login, me_resolved = None, False
    for qual in quals:
        search = (base + " " + qual).strip() if qual else base
        rows = None
        if snapshot is not None:
            if ("@me" in search) and not me_resolved:
                try:
                    me_login = airuleset._gh_login(cwd)
                except Exception:
                    me_login = None
                me_resolved = True
            if ghread.search_client_side_ok(search, me_login):
                rows = [r for r in snapshot
                        if ghread.issue_matches_search(r, search, me_login)]
        if rows is not None:
            # Reduce to the SAME `fields` row shape the GraphQL path returns,
            # so every consumer is byte-identical regardless of source.
            for r in rows:
                seen[r["number"]] = {k: r.get(k) for k in fields}
            continue
        gh_args = ["issue", "list", "--state", "open", "--search", search]
        if repo:
            gh_args += ["-R", repo]
        gh_args += ["-L", "1000", "--json", ",".join(fields)]
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


# --------------------------------------------------------------------------- #
# #1009 — the merged+released END STATE. After a stream PR merges into develop,
# the odoo-erp Sub-dev Handoff Gate re-runs from `main` on the `issues` event
# and STRIPS `ready-for-review`; GitHub does NOT auto-close (merge went to
# develop, not the default branch), so the ticket stays OPEN with no hand-off
# label and `_slice_mine_and_handed`'s label/timeline logic returns it to the
# stream's workable `I` — although the stream's authority ended at the develop
# merge (release + close are the gk's). These two helpers detect the DONE state
# at the cause: the ticket's stream PR is MERGED and its merge commit is an
# ancestor of `origin/main` — the SAME `git merge-base --is-ancestor <merge>
# origin/main` predicate goal_registry's /goal release proof uses. The result
# folds into `handed` as the distinct truthy state `"released"` (never a new
# label — the ticket forbids it, and depending on a foreign workflow's labels is
# what caused this bug). Fail-safe: any gh/git error, missing slug/stream, or an
# unfetched merge commit → NOT released → the ticket simply stays in `I` (the
# never-falsely-done direction).
# --------------------------------------------------------------------------- #

def _commit_is_released(oid, root):
    """True IFF commit `oid` is an ancestor of `origin/main` in the repo at
    `root` — the /goal release proof's own predicate. Fail-safe False (a git
    error, an unfetched/unknown commit, or a missing origin/main → 'not
    released')."""
    if not oid or not root:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor",
             str(oid), "origin/main"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return r.returncode == 0


def _released_stream_numbers(candidates, root, slug, stream):
    """The subset of `candidates` (ticket numbers) whose stream PR is MERGED and
    RELEASED. A batched `gh pr list --state merged --search head:<stream>/` per
    stream-name equivalent (NOT one gh call per candidate — `_slice_mine_and_
    handed` runs on the footer's hot refresh path) that returns every recent
    merged stream-branch PR + its merge commit, then a LOCAL `git merge-base
    --is-ancestor` per matched candidate. A PR is matched to a candidate by its
    head branch prefix `<eq>/<N>-` (re-checked in Python, since a `head:` search
    may over-match).

    `stream` is EXPANDED via `_stream_rename_equivalents()` (#537) — the SAME
    alias primitive every other stream-identity consumer uses (`_slice_quals`,
    `_ticket_is_stream_labeled`) — because the in-progress base-stream rename
    means a ticket's immutable PR branch may carry EITHER the old or the new
    stream name (montalu↔montalu1, david↔david1, simap↔simap1 — exactly the
    incident streams). A non-renamed stream expands to just `[stream]`, so its
    cost is unchanged (ONE gh call). Fail-safe EMPTY set (gh/git error, missing
    slug/stream, or no candidate → nothing marked released → every candidate
    stays in `I`)."""
    import airuleset
    want = {n for n in (candidates or [])}
    if not slug or not stream or not want:
        return set()
    equivs = _stream_rename_equivalents(stream)
    prs = []
    for eq in equivs:
        raw = airuleset._gh_out(
            "pr", "list", "--state", "merged",
            "--search", "head:%s/" % eq,
            "--json", "number,mergeCommit,headRefName", "-L", "100",
            cwd=root, timeout=20)
        try:
            part = json.loads(raw)
        except (ValueError, TypeError):
            part = []
        if isinstance(part, list):
            prs.extend(part)
    num_res = [re.compile(re.escape(eq + "/") + r"(\d+)(?:-|$)") for eq in equivs]
    released = set()
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        head = str(pr.get("headRefName") or "")
        n_num = None
        for rx in num_res:
            mm = rx.match(head)
            if mm:
                n_num = int(mm.group(1))
                break
        if n_num is None or n_num not in want or n_num in released:
            continue
        mc = pr.get("mergeCommit")
        oid = mc.get("oid") if isinstance(mc, dict) else None
        if oid and _commit_is_released(oid, root):
            released.add(n_num)
    return released


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
        # #1053: `gk-processing` (gk applied it at pickup, replacing
        # `ready-for-review`) is ALSO a "parked with the gatekeeper" state — the
        # count must NOT drop to 0 while gk is working. `verify-on-copy` is
        # deliberately absent: it is the post-deploy RETURN to the sub-dev's own
        # `I` (handled by GATEKEEPER_PROCESSED_LABELS, below).
        label_handed = ("ready-for-review" in labels) or \
            ("needs-gatekeeper" in labels) or ("gk-processing" in labels)
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
        # #1053: derive the candidate query from MAINTAINER_ACTION_LABELS so
        # `gk-processing` is included and the two never desync — a gk-processing
        # ticket that lost its `stream:<user>` label (shared-account relabel)
        # must still be recovered, else it vanishes from I/gk/U/W entirely.
        raw = airuleset._gh_out("issue", "list", "--state", "open", "--search",
                      AUTOPILOT_SKIP_EXCL +
                      " label:" + ",".join(MAINTAINER_ACTION_LABELS),
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
        walk = unhandled_candidates[:airuleset._HANDOFF_COMMENT_CHECK_LIMIT]
        # #1009 released detection + #589 timeline verdict, MUTATING `handed`.
        # #1067 slice 1c (b): the per-issue timeline reads run through a bounded
        # thread pool (extracted to `_handed_from_timelines`) instead of one
        # sequential `gh api …/timeline` at a time.
        _handed_from_timelines(walk, bounce_numbers, handed, root, slug)

    return rows, handed, failed


def _timeline_verdict(n_num, root, slug):
    """Fetch issue ``n_num``'s timeline and classify it as ``(verdict,
    saw_gatekeeper_comment)``, or ``None`` when the response is not a usable
    event list (parse error / non-list — never a real answer, so no upgrade).
    A pure per-issue unit so ``_handed_from_timelines`` can run it in parallel
    (#1067 slice 1c (b)); the verdict logic is byte-preserved from the old
    inline walk (last-signal-wins over `_timeline_handoff_signal`)."""
    import airuleset
    raw = airuleset._gh_out(
        "api", "repos/%s/issues/%d/timeline?per_page=100" % (slug, n_num),
        cwd=root, timeout=20)
    try:
        events = json.loads(raw)
    except (ValueError, TypeError):
        events = []
    if not isinstance(events, list):
        return None   # e.g. a bare int -- never a real answer
    verdict = False
    saw_gatekeeper_comment = False
    for ev in events:
        sig, is_gk_comment = airuleset._timeline_handoff_signal(ev)
        if is_gk_comment:
            saw_gatekeeper_comment = True
        if sig is not None:
            verdict = sig
    return (verdict, saw_gatekeeper_comment)


def _handed_from_timelines(walk, bounce_numbers, handed, root, slug):
    """#589/#1009 hand-off resolution for the ``walk`` candidate set, MUTATING
    ``handed`` in place.

    #1009: FIRST detect the merged+released DONE state for the candidate set
    (excluding a bounce — gk returned it for rework, the stream's own court, so
    hand-off states stay untouched) via ONE batched `gh pr list`, folding a
    released ticket into `handed` as the distinct truthy state "released" (it
    leaves `I` and counts in `gk`; it also SAVES its timeline fetch).

    #589/#1067 slice 1c (b): each remaining candidate's timeline is then read for
    a gk-resolution verdict through a bounded `run_parallel` (max_workers=6)
    instead of one sequential `gh api …/timeline` at a time. The verdicts are
    folded back in NUMBER order, so the result is identical to — and independent
    of thread scheduling from — the old sequential walk (every fold only SETS
    `handed`, never reads a prior value). A per-issue read that raises / parses
    unusably is isolated by `run_parallel` (no upgrade for that number), the same
    fail-safe direction as the old `continue`.

    #391 CRITICAL-1: for a `bounce_numbers` row, an upgrade to handed additionally
    requires a recognised gatekeeper COMMENT (`saw_gatekeeper_comment`) — byte-
    preserved here."""
    import airuleset
    import cli_parallel
    try:
        stream = airuleset._current_user()
    except Exception:
        stream = ""
    released = _released_stream_numbers(
        [n for n in walk if n not in bounce_numbers], root, slug, stream)
    to_walk = []
    for n_num in walk:
        if n_num in released:
            handed[n_num] = "released"
        else:
            to_walk.append(n_num)
    verdicts = cli_parallel.run_parallel(
        to_walk, lambda n: _timeline_verdict(n, root, slug))
    for n_num in sorted(to_walk):        # deterministic number-order fold
        res = verdicts.get(n_num)
        if res is None:
            continue                     # unusable read -> no upgrade
        verdict, saw_gatekeeper_comment = res
        if verdict and (n_num not in bounce_numbers or
                        saw_gatekeeper_comment):
            handed[n_num] = True


# ---------------------------------------------------------------------------
# Bounce round derivation (#843) — one function feeds CLI + slice-quals
# ---------------------------------------------------------------------------

def _bounce_label_events(events_raw):
    """Extract prio:bounce label-add event timestamps from a JSON array
    returned by the GitHub issue events REST API.

    Returns a list of ``datetime`` objects (UTC).  Empty list on any parse
    error — fail-safe (#942).  Sibling of ``_count_bounce_label_events``
    (which is now ``len()`` of this); the timestamp is needed by
    ``audit_bounce_rule_updates.py`` for rolling-window trend analysis
    (#957)."""
    from datetime import datetime
    if not events_raw:
        return []
    try:
        events = json.loads(events_raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(events, list):
        return []
    timestamps = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("event") == "labeled":
            lbl = ev.get("label")
            if isinstance(lbl, dict) and lbl.get("name") == "prio:bounce":
                raw_ts = ev.get("created_at", "")
                try:
                    # GitHub returns ISO 8601 with Z suffix.
                    ts = datetime.fromisoformat(
                        raw_ts.replace("Z", "+00:00"))
                    timestamps.append(ts)
                except (ValueError, TypeError, AttributeError):
                    # Unparseable timestamp — still count the event (len()
                    # preserves the count) but it cannot participate in
                    # window/treadmill analysis.  None signals "counted
                    # but undated" (#957 C2 fix — epoch caused false
                    # treadmill! when two unparseable timestamps were
                    # identical).
                    timestamps.append(None)
    return timestamps


def _count_bounce_label_events(events_raw):
    """Count prio:bounce label-add events in a JSON array from the
    GitHub issue events REST API.  Returns 0 on any parse error (#942).

    Delegates to ``_bounce_label_events`` — one source of truth (#957)."""
    return len(_bounce_label_events(events_raw))


def _has_bounce_label(labels_raw):
    """Check whether the prio:bounce label is currently present on an issue,
    from the JSON output of ``gh issue view --json labels``.
    Returns False on any parse error (#942)."""
    if not labels_raw:
        return False
    try:
        obj = json.loads(labels_raw)
    except (ValueError, TypeError):
        return False  # unparseable -> fail-safe
    if not isinstance(obj, dict):
        return False
    labels = obj.get("labels")
    if not isinstance(labels, list):
        return False
    for lb in labels:
        if isinstance(lb, dict):
            if lb.get("name") == "prio:bounce":
                return True
        elif isinstance(lb, str) and lb == "prio:bounce":
            return True
    return False


def _bounce_round(number, self_login, cwd=None, runner=None, repo=None):
    """Derive the bounce round for issue `number`.

    round = 1 + count(prio:bounce label-add events in the issue timeline).
    Floored to 2 while the ticket carries the `prio:bounce` label.

    Uses the issue events API (not RFR comment count) so gate-FAIL
    iterations that leave superseded READY-FOR-REVIEW comments do not
    inflate the round (#942).

    Fail-safe: gh/API error -> 1 (never a false Fable requirement; an
    over-count on a re-sync round escalates the review tier = SAFE
    direction).

    `runner` injectable for tests (returns fake ``_gh_out`` results).
    `repo` passes ``owner/name`` to build the events API path and
    ``-R owner/name`` for the label query."""
    import airuleset

    run = runner or airuleset._gh_out

    # 1. Count prio:bounce label-add events from the issue timeline (#942).
    # When repo is given, build the explicit path; otherwise use gh's
    # {owner}/{repo} template variables resolved from the cwd's git remote
    # (F1 review finding: repo=None must not silently skip the events call).
    events_path = ("repos/%s/issues/%s/events" % (repo, number)
                   if repo
                   else "repos/{owner}/{repo}/issues/%s/events" % number)
    events_raw = run("api", events_path,
                     "--paginate", cwd=cwd, timeout=30)
    bounce_adds = _count_bounce_label_events(events_raw)

    rnd = bounce_adds + 1

    # 2. Fetch current labels for the floor-to-2 safety check.
    r_args = ["-R", repo] if repo else []
    labels_raw = run("issue", "view", str(number), *r_args, "--json",
                     "labels", cwd=cwd, timeout=15)
    if _has_bounce_label(labels_raw) and rnd < 2:
        rnd = 2

    return rnd


