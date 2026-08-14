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
from pathlib import Path


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
    from every full-authority count."""
    import airuleset
    return " ".join("-label:stream:%s" % u
                    for u, profile in sorted(airuleset.AUTHORITY_BY_USER.items())
                    if profile != "full")


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

    Raises `SliceUnresolved` when the gh login cannot be resolved at all
    (#181 I-2) — an unresolvable identity cannot pick between those two
    branches, and guessing either one is a wrong answer on some box."""
    import airuleset
    if _is_gh_app_token_box():
        return ["label:stream:" + user]
    login = airuleset._gh_login(cwd)
    if login is None:
        raise SliceUnresolved(
            "gh api user failed — cannot tell whether this box authenticates "
            "as the maintainer account (slice = the stream LABEL alone) or as "
            "its own (assignee ∪ author ∪ label). Refusing to guess: the two "
            "branches disagree on every shared-account box.")
    if login == airuleset.MAINTAINER_GH_LOGIN:
        return ["label:stream:" + user]
    return ["assignee:@me", "author:@me", "label:stream:" + user]


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
    the core-scoped 'remaining' can't back)."""
    import airuleset
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    return any(("stream:%s" % u) in names for u in airuleset.AUTHORITY_BY_USER)


# The labels that mark a ticket as WAITING ON THE USER's answer — a question
# asked and hanging: `needs-answer` (the ask-and-continue durable marker, pinged)
# and `needs-decision` (the sleep-window deferral, queued for after 06:00). Such
# a ticket is genuine open work, but it is NOT this box's ACTIVE responsibility:
# the loop can do nothing with it until the user answers (the question already
# pinged the phone), so it LEAVES the workable "I N" count and the /goal
# stop-proof's workable-0 proof — and surfaces SEPARATELY as `U N` + the
# stop-proof's user-waiting remainder, so nothing is hidden and the loop parks on
# it rather than claiming "backlog empty" past it (#468, the user's directive
# 2026-08-14: "v I by nemali byť tie čo sú Q — nech je jasné kto za čo zodpovedá").
#
# Deliberately DISTINCT from AUTOPILOT_SKIP_EXCL/ops-channel (which fully EXCLUDE
# a ticket from every consideration): a user-waiting ticket stays tracked,
# listed (`--waiting`) and counted (`U N`) — only PARTITIONED out of "mine to
# action right now". And distinct from `needs-design`/`question`/`blocked`
# (filing-time "needs input" labels that stay fully WORKABLE and get worked — the
# worker raises the question): these two are applied ONLY AFTER a question has
# already been raised, so partitioning them never risks a question going unasked
# (skills/autopilot/SKILL.md's Step-1 backlog-scope bullet, #468 reconciliation).
USER_WAITING_LABELS = ("needs-answer", "needs-decision")


def _row_is_user_waiting(labels):
    """True if `labels` (a gh --json labels value: a list of {'name': ...}
    dicts, or None/malformed) carries any USER_WAITING_LABELS label.

    A missing/unreadable `labels` value reads as NOT user-waiting (→ workable) —
    the SAFE side: never hide a ticket from THIS box's own responsibility because
    of a failed label read. The mirror of `_ticket_is_stream_labeled(None)` being
    False, and the OPPOSITE conservative direction from `_row_action`'s
    ownership check (there the harm is inviting foreign-code edits, so a failed
    read goes `action-only`; here the harm is hiding own work, so it stays
    workable) — both pick the non-harmful side of their own asymmetry."""
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    return any(lb in names for lb in USER_WAITING_LABELS)


def _partition_user_waiting(rows):
    """Split a `_union_open_issues`/`_slice_mine_and_handed` rows dict
    (`{number: {"number","title","createdAt","labels"}}`) into
    `(workable, waiting)` by USER_WAITING_LABELS on each row.

    ONE derivation, never two queries: both halves come from the SAME
    already-fetched set, so the footer's `I N`/`U N` and the /goal stop-proof's
    workable count / user-waiting list cannot silently drift (#367 lesson — the
    exact reason a search-exclusion + separate positive query was rejected).
    Extends the repo's own established client-side-partition pattern
    (`_slice_mine_and_handed` splits handed/unhandled from one fetch;
    `_row_action`/`_stream_owner_of` partition by ownership) — no new mechanism."""
    workable, waiting = {}, {}
    for number, row in rows.items():
        labels = row.get("labels") if isinstance(row, dict) else None
        (waiting if _row_is_user_waiting(labels) else workable)[number] = row
    return workable, waiting


def _authority_marker(cwd=None):
    """Read an `<!-- airuleset:authority=<profile> -->` override from the project
    CLAUDE.md (cwd-relative), or None. Lets a project raise/lower a stream's default
    authority (e.g. grant `full` to a montalu repo). The single place the marker is
    parsed, so the CLI, the autopilot skill, and the issue-close guard hook agree.

    The marker MUST be the HTML-COMMENT form (exactly like `<!-- airuleset:merge=manual
    -->`) — a bare/prose mention of `airuleset:authority=…` is deliberately NOT honored:
    an unanchored match could let a documentation sentence naming a profile silently
    ELEVATE a fork-no-merge stream to `full` and disable the issue-close guard (the
    UNSAFE direction). If several comment markers exist (a misconfig) the LAST one wins,
    so an operative marker placed after any example cannot be shadowed."""
    import airuleset
    import re
    try:
        p = (Path(cwd) if cwd else Path.cwd()) / "CLAUDE.md"
        if p.is_file():
            hits = re.findall(r"<!--\s*airuleset:authority=([a-z-]+)\s*-->",
                              p.read_text(errors="ignore"))
            for tok in reversed(hits):
                if tok in airuleset.AUTHORITY_PROFILES:
                    return tok
    except OSError:
        return None
    return None


def resolve_authority(cwd=None) -> str:
    """The current stream's autopilot authority profile: a project CLAUDE.md
    `airuleset:authority=<profile>` marker (cwd-relative) OVERRIDES the per-user
    default map. This makes `airuleset.py authority` authoritative for both the
    autopilot skill and the `block-fork-no-merge-issue-close` hook (single source
    of truth) — cmd_authority's explain text has always PROMISED this override; it
    is now actually honored, not just documented."""
    import airuleset
    return _authority_marker(cwd) or airuleset.AUTHORITY_BY_USER.get(airuleset._current_user(), "full")


def cmd_authority(args):
    """Print the current stream's autopilot authority profile (one word)."""
    import airuleset
    if getattr(args, "maintainer_login", False):
        print(airuleset.MAINTAINER_GH_LOGIN)
        return
    profile = resolve_authority()
    print(profile)
    if getattr(args, "explain", False):
        user = airuleset._current_user()
        print(f"user={user} (map: {airuleset.AUTHORITY_BY_USER.get(user, 'unmapped -> full')}); "
              f"a project CLAUDE.md marker airuleset:authority=<profile> overrides this.")


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
    mark its tickets untouchable."""
    import airuleset
    names = {(lb or {}).get("name") for lb in (labels or [])
             if isinstance(lb, dict)}
    for user, profile in sorted(airuleset.AUTHORITY_BY_USER.items()):
        if profile != "full" and ("stream:%s" % user) in names:
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
    # the label missed is checked directly against its own comments via
    # `_comment_readiness_signal`, in creation order, keeping the LAST
    # signal (a stale pre-bounce comment is correctly invalidated by a
    # later gatekeeper finding/bounce, and a genuine post-bounce
    # re-submission overrides that again).
    #
    # #391 CRITICAL-1: for a row in `bounce_numbers`, an upgrade to handed
    # additionally requires `saw_gatekeeper_comment` -- a recognised
    # gatekeeper-authored comment (`_comment_readiness_signal` returning
    # False) seen SOMEWHERE in the walk, proving the bounce is genuinely
    # VISIBLE in the thread (a real post-bounce re-hand-off) rather than a
    # bare-label bounce with no comment at all (which must never re-flip a
    # stale pre-bounce hand-off comment back to handed).
    if slug and not failed:
        unhandled_candidates = sorted(
            (n_num for n_num in rows if not handed.get(n_num)),
            reverse=True)
        for n_num in unhandled_candidates[:airuleset._HANDOFF_COMMENT_CHECK_LIMIT]:
            raw = airuleset._gh_out("api",
                          "repos/%s/issues/%d/comments" % (slug, n_num),
                          cwd=root, timeout=20)
            try:
                comments = json.loads(raw)
            except (ValueError, TypeError):
                comments = []
            if not isinstance(comments, list):
                continue   # e.g. a bare int -- never a real answer
            verdict = False
            saw_gatekeeper_comment = False
            for c in comments:
                body = c.get("body") if isinstance(c, dict) else None
                sig = airuleset._comment_readiness_signal(body)
                if sig is False:
                    saw_gatekeeper_comment = True
                if sig is not None:
                    verdict = sig
            if verdict and (n_num not in bounce_numbers or
                            saw_gatekeeper_comment):
                handed[n_num] = True

    return rows, handed, failed


