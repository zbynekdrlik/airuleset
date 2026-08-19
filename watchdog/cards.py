"""watchdog/cards.py -- per-ticket completion-card reconciliation (job 25,
#134) plus the shared git-primitive trio it (and job 24) run on
(#433, module split cluster F).

WHY THIS FILE EXISTS. Extracted VERBATIM (a MOVE, not a rewrite -- same
discipline as #404's `watchdog/usage.py` and #433's own `watchdog/burn_jobs.py`)
from `watchdog/__init__.py` as part of #433's continuation of #404's
per-service module split: `card_reconcile` (job 25) plus its own
`merged_closes`/`_normalize_closed`/`_commits_in_window` helpers detect a
ticket that closed with no delivered Discord run-card, purely from local
git -- a self-contained cluster called from exactly ONE place, `run_once()`'s
own job dispatch, and never called BY any other watchdog job.

THE SHARED GIT-HELPER TRIO (`_default_git_run`/`_git_first_line`/
`_git_base_ref`) moved here TOO, even though this file's own module
docstring only needs to explain job 25: at the time this file was
extracted, all three were genuinely shared with job 24's `delivery_state`/
`_repo_label` (then still resident in `watchdog/__init__.py`, cluster E,
not yet extracted) -- `_default_git_run`'s own docstring says so explicitly
("job 24's `_git_first_line`/`delivery_state`, job 25's `merged_closes`").
That was NOT the circular-import hazard a coupled cluster like C/D would
have: the dependency direction was the SAFE one -- this file needs nothing
back from `watchdog/__init__.py` (only stdlib `re` + a locally-imported
`subprocess` + locally-imported `notify` package functions, unchanged from
before the move). Cluster E has SINCE been extracted too, into
`watchdog/repo_health.py` -- `_git_commit_ts`, `delivery_state`, and
`_repo_label` no longer live in `watchdog/__init__.py` at all;
`repo_health.py` imports `_git_first_line`/`_git_base_ref` directly from
this module (`from watchdog.cards import _git_first_line, _git_base_ref`),
a plain leaf-to-leaf forward import, exactly the shape this docstring
predicted would be safe when it was written.

MONKEYPATCH SEAM NOTE (#433 cluster-F review MINOR-2, updated after cluster
E's own extraction): before cluster F's own move, `watchdog._default_git_run
= spy` reached every caller of the trio, since they all resolved it against
the SAME module's globals. After cluster F it resolves against
`watchdog.cards.__dict__` instead -- a package-level patch of
`watchdog._default_git_run` no longer reaches `_git_first_line` (here) or
any caller now resident in `watchdog.repo_health` (`_git_commit_ts`/
`delivery_state`/`_repo_label`). No existing test does this (verified: no
test patches these names at all, in either module -- every test that needs
control injects `git_run=` instead, which is unaffected) -- cluster E's own
extraction did not add such a test either, so this remains a latent
seam-narrowing rather than a live break.

Re-exported from `watchdog/__init__.py` (`from watchdog.cards import ...`,
placed after every symbol this cluster depends on is already defined -- it
depends on none) so every existing caller (`run_once()`'s job 25 dispatch,
calling `card_reconcile` as a bare module-global name, and the test suite's
`wd.card_reconcile`/`watchdog.card_reconcile` attribute access) needs zero
changes. Same facade pattern as `watchdog/usage.py` (#404 cluster A) and
`watchdog/burn_jobs.py` (#433 cluster B): thin re-export block, `X as X`
syntax for ruff, own module docstring, zero behavior change.
"""

import re


def _default_git_run(argv, timeout=10):
    """The default `git_run` callable for every `git -C <cwd> ...` helper in
    this module (job 24's `_git_first_line`/`delivery_state`, job 25's
    `merged_closes`) -- a thin `subprocess.run` wrapper returning stdout on
    success, `None` on any failure (non-zero exit, timeout, missing `git`).
    Genuinely shared: NOT compact-owned, even though it used to sit inside
    the (now-deleted, #402) compact block purely by physical proximity to
    `compact_boundary_substantial`'s own `_git_commit_count_since` helper,
    which WAS compact-only and is gone with it."""
    import subprocess
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except Exception:
        return None


def _git_first_line(cwd, argv, git_run=None):
    """One `git -C <cwd> …` call, stripped. None on any failure OR empty
    output — never a partial guess."""
    out = (git_run or _default_git_run)(["git", "-C", str(cwd)] + list(argv))
    if out is None:
        return None
    return out.strip() or None


def _git_base_ref(cwd, git_run=None):
    """The repo's DELIVERY ref — the branch a merge lands on. `origin/HEAD`
    is authoritative when the checkout has it; the explicit fallbacks cover a
    clone that never resolved it. None when neither exists (then the repo is
    unmeasurable and job 24 stays silent about it)."""
    ref = _git_first_line(cwd, ["symbolic-ref", "--quiet",
                                "refs/remotes/origin/HEAD"], git_run)
    if ref and ref.startswith("refs/remotes/"):
        return ref[len("refs/remotes/"):]
    for cand in ("origin/main", "origin/master"):
        if _git_first_line(cwd, ["rev-parse", "--verify", "--quiet",
                                 cand + "^{commit}"], git_run):
            return cand
    return None


def _owned_identity(root, closed):
    """The default owned-set filter for a caller that wires no owner scoping (a
    direct call / test): own EVERYTHING — the pre-#534 behaviour a full-authority
    box also keeps. Returns a copy so the caller may mutate it freely."""
    return dict(closed)


def make_owned_closed_filter(current_user_fn=None, authority_fn=None,
                             owner_fn=None):
    """#534 — the per-sweep OWNER-SCOPING filter run_once passes to BOTH job 24
    (`card_reconcile`) and job 25 (`report_reconcile`).

    Both jobs derive their owed set from `merged_closes(root, base, ...)`, which
    reads the SHARED base branch (origin/HEAD). On a multi-stream repo (odoo-erp)
    that branch carries every stream's + the gatekeeper's merges, so an unscoped
    owed set nudges/pings a REDUCED-authority stream box for FOREIGN tickets
    (montalu3 got report-owed nudges for stream:core / stream:miva1 it does not
    own). This scopes the owed set to what THIS box actually owes:

      * FULL-authority (gatekeeper) box -> `closed` unchanged: it owns the whole
        merged set, #534 is exclusively a reduced-authority defect, and re-scoping
        the gatekeeper risks newly SUPPRESSING a legitimate nudge for a ticket it
        did close.
      * REDUCED-authority stream box -> only candidates whose TEMPORALLY-LAST
        origin-shaped label (`stream:<user>` / `handed-by:<user>`) names THIS
        box's own stream (alias-expanded via `_stream_rename_equivalents` so a
        renamed box still matches its own legacy `stream:<old>` label during the
        transition, #564), via `_last_origin_owner` — the SAME ownership oracle
        `_slice_mine_and_handed` / `cmd_gk_request` use (never a parallel
        derivation, #181). ONE batched GraphQL timeline read for the whole
        candidate set, so it survives a relabel and works on a shared-gh-account
        stream (montalu authenticates as the maintainer, so @me is meaningless —
        the label event is the only sound signal).
      * UNDETERMINABLE owner (gh failure / unresolved identity) -> NOT owned.
        Fail toward NOT nudging: a false nudge is the reported bug, a missed one
        is re-evaluated next sweep (nothing is latched here).

    The per-root ownership lookup is memoized so the two jobs (same registry
    entry, same `closed` for a given root) share ONE GraphQL call. The memo lives
    exactly one sweep (run_once builds a fresh filter per sweep), so a gh-failure
    sweep re-queries next sweep rather than latching a wrong answer.

    The seams (`current_user_fn` / `authority_fn` / `owner_fn`) default to the
    real gh-backed resolvers and are injected only by tests, so the jobs stay
    gh-free by default and the whole mechanism is testable without a network."""
    if current_user_fn is None:
        def current_user_fn():
            import airuleset
            return airuleset._current_user()
    if authority_fn is None:
        def authority_fn(root):
            from cli_quals import resolve_authority
            return resolve_authority(cwd=root)
    if owner_fn is None:
        def owner_fn(root, numbers):
            from cli_quals import _last_origin_owner
            return _last_origin_owner(numbers, cwd=root)

    memo = {}                              # root -> {issue_num: owning_user}
    auth = {}                              # root -> authority (once per root/sweep)

    def owned(root, closed):
        # Returns the owned subset (a dict) to ACT on, or None to SKIP this root
        # this sweep WITHOUT disturbing its dedup state. UNDETERMINABLE ownership
        # (gh failure / unresolved identity / a reduced box that owns none of the
        # candidates) must return None, never {} — the caller pops the permanent
        # nudged/pinged dedup on an empty result, so returning {} on a transient
        # gh hiccup would RE-nudge an already-delivered ticket the next sweep
        # (#534 review MINOR-2). Only a genuinely-empty `closed` returns {} so
        # the caller still cleans up when nothing merged. `_last_origin_owner`
        # cannot tell a gh failure from a genuine no-owner (both {}), so a
        # reduced box owning nothing skips too — a lingering empty dedup entry is
        # the harmless, spam-free trade for never re-nudging on a flake.
        if not closed:
            return dict(closed)            # nothing merged -> caller pops (clean)
        if root not in auth:
            auth[root] = authority_fn(root)
        if auth[root] == "full":
            return dict(closed)            # full-authority owns the whole set
        me = current_user_fn()
        if not me:
            return None                    # can't identify box -> skip, keep dedup
        # #564: match this box's OWN stream under the in-progress base-stream
        # rename too — a renamed box (montalu1) still owns merged tickets whose
        # temporally-last origin label is the LEGACY stream:montalu, and
        # `_last_origin_owner` returns that bare label username ("montalu"). Route
        # the comparison through the SAME single `_stream_rename_equivalents`
        # primitive item 2 uses (never a parallel rename table); a non-renamed
        # stream expands to just itself, so the match is byte-identical to the
        # prior `== me`.
        import airuleset
        own_names = set(airuleset._stream_rename_equivalents(me))
        if root not in memo:
            try:
                memo[root] = owner_fn(root, sorted(closed))
            except Exception:
                memo[root] = None          # gh failure -> undeterminable this sweep
        owners = memo[root]
        if owners is None:
            return None                    # skip, keep dedup
        scoped = {n: ts for n, ts in closed.items() if owners.get(n) in own_names}
        return scoped or None              # own-nothing/flake -> skip, keep dedup

    return owned


# --------------------------------------------------------------------------- #
# Job 25 — CARD RECONCILIATION (#134, marek's stream 2026-07-23 → 2026-07-28).
#
# Measured: the `claude-marek` thread received ZERO per-ticket completion
# cards for five days while tvdole closed 6 issues / merged 5 PRs and
# parovanie-produktov closed 97 / merged 80. The last dedup marker for that
# repo is `#192` at 2026-07-23 19:05; issues #193–#295 produced none, and
# tvdole has never produced one at any point. Nothing was broken — the
# mechanism is healthy and zbynek-owned repos card through today. The card is
# simply an ACTION WITH NO ARTIFACT ANYONE CHECKS, so workers drifted out of
# the habit and nothing pushed back.
#
# WHY THIS EXISTS ALONGSIDE THE SubagentStop GATE. The gate
# (`hooks/subagent-stop-check-run-card.sh`) is the in-band half and it is the
# one that restores a REAL card, with the worker's own plain-Slovak goal and
# achieved text. But it is structurally blind to two of the ways this fails:
# a worker that DIES mid-run never reaches SubagentStop at all, and a
# delivery that fails AFTER the worker returned happens when no hook is
# looking. Both collapse to one observable — an issue closed by a merge with
# no delivered card — which is what this job measures.
#
# WHAT IT READS, AND WHY NOT `gh`. Purely LOCAL git, per repo hosting a live
# pane: the merge commits that landed on the base branch inside the window,
# and the `Closes/Fixes/Resolves #N` they carry. That is the same fact a `gh`
# query would return, for free, without spending an API call per repo per
# sweep against a 5,000/h budget and without needing auth in every checkout.
#
# RELATION TO JOB 24 (#138), checked before adding rather than after. They do
# not overlap and in the common case are mutually exclusive BY CONSTRUCTION:
# job 24 fires when the base branch is FROZEN (merges stopped); job 25 fires
# when the base branch MOVED (merges happened) and the reports did not. They
# share no state key and answer different questions.
#
# Detection only, exactly like jobs 21 and 24: it never types into a pane and
# never touches a worktree, index or local branch. Deciding what to do about
# an unreported ticket is the user's call.
# --------------------------------------------------------------------------- #

CARD_WINDOW_S = 172800            # merges older than 48h are history, not a gap
CARD_GRACE_S = 1200               # a ticket younger than this hasn't had time
                                   # to be carded yet (#224) — the worker's own
                                   # post-merge sequence (deploy, verify, THEN
                                   # the card) takes minutes by design, and the
                                   # original code had no minimum age at all,
                                   # so it beat the worker's own card by
                                   # seconds on every clean run
CARD_MAX_LISTED = 8               # the phone gets numbers, not a wall

# --- #525 REPORT reconciliation knobs (see report_reconcile below) --- #
REPORT_GRACE_S = 600              # ~10 min: a supervisor's post-merge sequence
                                   # (deploy → verify → the report itself) takes
                                   # minutes; only past this is a report OWED
REPORT_MAX_SWALLOWS = 3           # consecutive-swallow give-up before escalating
                                   # the owner (#442-F2 anti-silence)
REPORT_REPROBE_S = 600           # #511: after a swallow, re-probe the pane on
                                   # this bounded interval (never every sweep,
                                   # never a permanent stop) until it lands
REPORT_MAX_LISTED = 8             # phone/pane gets numbers, not a wall
REPORT_TAIL_LINES = 400          # a fresh (un-compacted) report sits near the
                                   # tail — the compact-sync.log covers a report
                                   # already compacted OUT of this window

_CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
                        re.I)


def merged_closes(root, base, since_ts, git_run=None):
    """`{issue_num: commit_epoch_ts}` for every issue closed by a commit on
    `base` since `since_ts`.

    GitHub closes an issue from a `Closes #N` in a commit reachable from the
    default branch, so this is the delivery event itself, read locally — and
    the commit's OWN timestamp is carried forward so `card_reconcile` can
    gate a fresh merge with a per-TICKET grace period (#224), not just the
    window's edge. A record separator (`%x1e`) marks the start of each
    commit's block and a field separator (`%x1f`) splits its timestamp from
    the message, so a `Closes #N` mentioned in either the subject or the
    body is still found — the whole message stays in the same record. When
    the same issue is mentioned in more than one commit within the window
    (rare), the MOST RECENT commit's timestamp wins — the newest mention is
    the honest "when did this actually settle" answer."""
    text = (git_run or _default_git_run)(
        ["git", "-C", str(root), "log", base,
         "--since=@%d" % int(since_ts), "--format=%x1e%ct%x1f%s%n%b"])
    result = {}
    for rec in (text or "").split("\x1e"):
        ts_str, sep, rest = rec.partition("\x1f")
        if not sep:
            continue
        try:
            ts = float(ts_str)
        except (TypeError, ValueError):
            continue
        for m in _CLOSES_RE.finditer(rest):
            n = int(m.group(1))
            result[n] = max(ts, result.get(n, ts))
    return result


def _normalize_closed(value):
    """Normalize what a `closed_fetch` callback returns into the same
    `{issue_num: ts_or_None}` shape `merged_closes` produces.

    Accepts a `{n: ts}` dict, a list of `(n, ts)` pairs, or a bare list of
    ints (the pre-#224 contract some callers — and this job's own existing
    tests — still use). A bare int gets `ts=None`, and `card_reconcile`
    treats an unknown timestamp as ALREADY past grace: not knowing when a
    ticket closed must never suppress a report the pre-grace code would
    have sent instantly — "report now" is the safe default, never "wait
    forever for a timestamp that will never arrive"."""
    if isinstance(value, dict):
        return {int(k): (None if v is None else float(v))
                for k, v in value.items()}
    out = {}
    for item in value or ():
        if isinstance(item, (tuple, list)) and len(item) == 2:
            n, ts = item
            out[int(n)] = None if ts is None else float(ts)
        else:
            out[int(item)] = None
    return out


def _commits_in_window(root, base, since_ts, git_run=None):
    out = _git_first_line(root, ["rev-list", "--count",
                                 "--since=@%d" % int(since_ts), base], git_run)
    try:
        return int(out)
    except (TypeError, ValueError):
        return 0


def card_reconcile(now, run, state, cwd_by_sid, send_fn=None, dry_run=False,
                   git_run=None, card_probe=None, marker_ok=None,
                   owner_by_sid=None, window=None, grace=None,
                   closed_fetch=None, reopen_fetch=None, owned_closed=None):
    """Job 25 — see the section comment.

    Gated on `card_probe` (the "wired = on" convention of jobs 8/11/16/24):
    the probe carries the confirming fetch, so a base ref that had merely
    gone stale locally cannot on its own claim a ticket went unreported.

    Two independent fixes over the original #134 shape, both from live
    false positives measured on this box (#224):

      * GRACE PERIOD, per ticket. The original code had no minimum age at
        all — only `window`, a MAXIMUM age — so it beat the worker's own
        delivered card by seconds on every clean run (merge closed 3
        tickets at 11:46:21, this job pinged "unreported" at 11:46:24,
        the worker's cards were `sent` at 11:49:06-11:49:14). A ticket is
        only "pingable" once `now - <its own closing commit's timestamp>
        >= grace` — read from `merged_closes`'s per-ticket dict, never
        derived from the window's edge, so the grace genuinely applies
        per ticket rather than per repo (two tickets in the same repo, one
        old and one fresh, are judged independently in the same sweep).
      * DEDUP, per ticket, forever — not per repo per calendar day. A
        ticket whose card can never arrive (the worker died, or the issue
        closed with no PR at all) used to be re-announced once a day for
        as long as it sat inside the 48h window, and any NEWLY stuck
        ticket restarted that clock for every OTHER ticket in the same
        repo (834 consecutive sweeps of the same six camera-box tickets,
        ~14h). `state["card_unreported"][root]["pinged"]` now remembers
        every issue number this job has ever pinged for that repo — a
        ticket earns exactly one nag, and a genuinely NEW unreported
        ticket still pings immediately, even in the sweep that just
        deduped an old one. Entries are pruned once their ticket ages out
        of `closed` (the 48h window), so the memory stays bounded.

    One repo is examined once per sweep however many panes sit in it, and
    DETECTION is logged every sweep regardless of grace/dedup (issue #36's
    print-always convention — the log is a local journal line, never the
    thing that reaches the user's phone)."""
    if card_probe is None:
        return []
    window = CARD_WINDOW_S if window is None else window
    grace = CARD_GRACE_S if grace is None else grace
    if marker_ok is None:
        try:
            from notify import marker_delivered as marker_ok
        except ImportError:
            return []
    owner_by_sid = owner_by_sid or {}
    owned_closed = owned_closed or _owned_identity   # #534 owner scoping
    seen = dict(state.get("card_unreported") or {})
    logs, live, examined = [], set(), set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
        if not root or root in examined:
            continue
        examined.add(root)
        base = _git_base_ref(root, git_run)
        if not base:
            continue                      # unmeasurable — never a finding
        live.add(root)

        # The repo NAME comes from `origin`, never the directory basename:
        # the checkout is `parovanie_produktov` while every marker is keyed
        # `parovanie-produktov`, so a directory-derived key matches nothing
        # and would report every ticket as unreported. Resolved HERE, ahead
        # of `closed` — the #182 reopen-clear step below needs it and must
        # run independent of whether anything closed in THIS sweep's window
        # (adversarial-review finding: it used to sit AFTER `if not closed:
        # continue`, so a repo where nothing closed this sweep — the ticket's
        # OWN original close having aged out of the window, with no sibling
        # ticket closing to keep `closed` non-empty — never reached it at
        # all, and the exact bug #182 exists to fix kept reproducing).
        try:
            from notify import repo_name_for
            name = repo_name_for(root)
        except ImportError:
            name = ""

        # #182: a REOPENED ticket's existing run-card marker refers to a
        # PRIOR close and must not keep deduping the card the NEXT close
        # earns. `reopen_fetch` (wired = on, like `closed_fetch`) answers
        # "of the issue numbers that already have a marker for this repo,
        # which are OPEN again right now" — a marker for one of those is
        # cleared so the next close claims fresh. Gated separately from the
        # rest of this job (never on `card_probe`/`closed` at all) so a
        # caller that never wires it — every pre-#182 test — sees NO
        # behavior change at all. An existing marker predates THIS sweep's
        # window entirely, which is exactly why this cannot depend on
        # `closed` being non-empty.
        if name and reopen_fetch is not None:
            try:
                from notify import card_marker_numbers, forget_marker
            except ImportError:
                card_marker_numbers = forget_marker = None
            if card_marker_numbers is not None:
                candidates = card_marker_numbers(name)
                if candidates:
                    try:
                        reopened = reopen_fetch(root, candidates)
                    except Exception as e:
                        logs.append("card-reconcile reopen-fetch-failed %s: %r"
                                    % (root, e))
                        reopened = None
                    for n in sorted(reopened or ()):
                        forget_marker("%s#%d" % (name, n))
                        logs.append("card-reconcile reopen-cleared %s#%d"
                                    % (name, n))

        # CONFIRM, then announce (job 24's contract, reused verbatim): the
        # probe fetches the base ref, and the measurement happens after it.
        try:
            card_probe(root, base)
        except Exception as e:
            logs.append("card-reconcile probe-failed %s: %r" % (root, e))
        closed = merged_closes(root, base, now - window, git_run)
        # `verified_source` tracks whether `closed` ALREADY passed GitHub's
        # own CLOSED_EVENT->closer check (#230's `_watchdog_closed_fetch`),
        # as opposed to being a bare local commit-message-keyword match
        # (`merged_closes`) that has not yet been verified against anything.
        verified_source = False
        if not closed and closed_fetch is not None:
            # A repo that never writes `Closes #N` trailers is invisible to
            # the local read — its issues close from the PR body, which is a
            # GitHub-side fact. tvdole is exactly that repo, and it is one of
            # the two in #134's own evidence, so the local-only design would
            # have been blind to half the incident. The fallback is bounded
            # to precisely that signature — fresh merges on the base branch,
            # yet zero trailers — so a repo that answers locally never costs
            # an API call, and a parked repo never costs one either.
            if _commits_in_window(root, base, now - window, git_run) > 0:
                try:
                    closed = _normalize_closed(closed_fetch(root, now - window))
                    verified_source = True
                except Exception as e:
                    logs.append("card-reconcile closed-fetch-failed %s: %r"
                                % (root, e))
                    closed = {}
        # #534: scope to tickets THIS box owns a card for — a reduced-authority
        # stream box reads the SHARED base branch, which carries every stream's
        # merges, so an unscoped set pings the owner about FOREIGN tickets.
        # Full-authority box -> unchanged. None = UNDETERMINABLE ownership (gh
        # flake / unresolved identity): skip WITHOUT popping the pinged dedup, or
        # gh recovering next sweep would re-ping an already-pinged own ticket
        # (#534 review MINOR-2).
        scoped = owned_closed(root, closed)
        if scoped is None:
            continue
        closed = scoped
        if not closed:
            seen.pop(root, None)
            continue

        if not name:
            continue

        # Two ways a ticket can already have been reported: its OWN card, or
        # a catch-up DIGEST that accounted for it (#141). The digest writes
        # its own namespace, and only on a delivered POST — so a digest that
        # never reached Discord suppresses nothing here.
        #
        # Resolved SEPARATELY from `repo_name_for`, whose ImportError path
        # skips the repo entirely: the timer runs the working tree every
        # 60s, so a mid-push checkout can genuinely see a watchdog newer
        # than notify. Sharing that import would turn a transient
        # missing-symbol window into a silent, unlogged death of the whole
        # backstop — the exact shape this job exists to catch.
        try:
            from notify import backfill_marker_key
        except ImportError:
            backfill_marker_key = None
            logs.append("card-reconcile backfill-namespace-unavailable %s"
                        % name)
        missing = [n for n in sorted(closed)
                   if not marker_ok("%s#%d" % (name, n))
                   and not (backfill_marker_key is not None
                            and marker_ok(backfill_marker_key(name, n)))]
        if not missing:
            seen.pop(root, None)
            continue

        # Detection is logged unconditionally, on `missing` alone (never
        # gated on grace or on per-ticket dedup) — this is a local journal
        # line, not the phone ping, so it stays true to issue #36's
        # print-always convention regardless of what happens next.
        logs.append("card-unreported %s n=%d issues=%s"
                    % (name, len(missing),
                       ",".join(str(n) for n in missing[:CARD_MAX_LISTED])))

        prev = seen.get(root) or {}
        # Per-ticket "already pinged, ever" memory. Pruned to tickets still
        # inside `closed` — once a ticket ages out of the 48h window it can
        # never be "missing" again, so its entry is dead weight.
        pinged = {k: v for k, v in (prev.get("pinged") or {}).items()
                  if int(k) in closed}
        # #474: per-ticket "STABLY verify-rejected, ever" memory — parallel
        # to `pinged`, pruned the same way. A candidate that a SUCCESSFUL
        # `closed_fetch` verify rejected (a local `Closes #N` trailer that
        # GitHub's own CLOSED_EVENT->closer check does NOT confirm — the
        # odoo-erp non-default-branch shape) is a STABLE disagreement. Before
        # this memory it was re-verified with a fresh `gh` GraphQL call AND
        # its `verify-rejected` line re-logged on EVERY 60s sweep, forever
        # (gk: the same 9 odoo-erp tickets every sweep). Remembering it here
        # excludes it from `pingable` below, so neither the re-verify nor the
        # re-log happens again — the rejection is logged once, on the sweep
        # it first happens.
        rejected = {k: v for k, v in (prev.get("rejected") or {}).items()
                    if int(k) in closed}
        # A ticket is still inside its own grace window when its closing
        # commit's timestamp is known AND recent. An UNKNOWN timestamp
        # (the `closed_fetch` bare-int-list fallback) is never treated as
        # "inside grace" — not knowing when a ticket closed must never
        # suppress a report the pre-#224 code would have sent instantly.
        pingable = [n for n in missing
                    if not (closed.get(n) is not None
                            and now - closed[n] < grace)
                    and str(n) not in pinged
                    and str(n) not in rejected]

        # #232: `merged_closes` is a bare commit-message-keyword match — it
        # is NOT proof GitHub actually closed the ticket from that commit
        # (odoo-erp: 22 local `Fixes #N` matches on a non-default branch,
        # none of which ever triggered a GitHub auto-close). Only a
        # candidate that already came from the VERIFIED `closed_fetch`
        # fallback (`verified_source`) skips this — asking again would cost
        # a second `gh` call for an answer already known. Every OTHER
        # candidate that survived grace + dedup (i.e. would actually nag
        # this sweep) is checked against the SAME CLOSED_EVENT->closer
        # GraphQL query #230 built, and only a confirmed merged-PR closer
        # is kept. A `closed_fetch` failure (raises, times out, or itself
        # degrades to `None`) drops every currently-pingable candidate for
        # THIS SWEEP ONLY — nothing here is added to `pinged`, so a genuine
        # finding is retried next sweep rather than silently swallowed
        # forever (the direction the pre-existing fallback-failure branch
        # above already takes).
        if pingable and not verified_source and closed_fetch is not None:
            verify_ok = True
            try:
                raw = closed_fetch(root, now - window)
            except Exception as e:
                logs.append("card-reconcile verify-failed %s: %r" % (root, e))
                raw = None
                verify_ok = False
            # #474: `closed_fetch`'s OWN documented degrade sentinel is None
            # (`_watchdog_closed_fetch` returns None on any gh failure —
            # "degrade to the local-only answer rather than to silence"). A
            # None here is an UNMEASURABLE verify, NOT a stable rejection —
            # treat it exactly like a raised exception below: drop the ping
            # this sweep but persist NOTHING, so a genuine finding is retried
            # once the verifier recovers. Only a genuine MEASURED answer
            # (a dict/list, even empty) makes a "not in confirmed" stable.
            if raw is None:
                verify_ok = False
                # #474-review MINOR-1: a None here is the verifier saying "I
                # could not reach GitHub", NOT "GitHub confirmed these were
                # not merge-closed". Log it as verify-UNMEASURABLE, never
                # verify-REJECTED — a sustained graphql-bucket outage would
                # otherwise re-log a MISLEADING "rejected" line every sweep
                # for each unverified candidate (the ping decision is still
                # correct: dropped this sweep, retried next). The exception
                # branch above already logs its own `verify-failed` line, so
                # only the graceful-None path needs this.
                logs.append("card-reconcile verify-unmeasurable %s issues=%s"
                            % (name, ",".join(str(n)
                                              for n in pingable[:CARD_MAX_LISTED])))
            confirmed = _normalize_closed(raw)
            newly_rejected = [n for n in pingable if n not in confirmed]
            # verify-REJECTED only for a SUCCESSFUL verify's genuine
            # rejections (verify_ok) — the transient-failure paths (None /
            # exception) have their own honest lines above and must never
            # claim GitHub rejected a ticket it was never actually asked.
            if newly_rejected and verify_ok:
                logs.append("card-reconcile verify-rejected %s issues=%s"
                            % (name, ",".join(str(n)
                                              for n in newly_rejected[:CARD_MAX_LISTED])))
            # #474: only a SUCCESSFUL verify's rejections are STABLE — persist
            # them into `rejected` so a later sweep excludes them from
            # `pingable` above and never re-verifies or re-logs. A TRANSIENT
            # verify failure (`confirmed = {}` makes EVERYTHING look rejected)
            # must remember NOTHING, so a genuine finding is retried next
            # sweep once the verifier recovers — the same direction the
            # fallback-failure branch above already takes, and what keeps a
            # ticket rejected only by a boom-verifier still pingable later.
            if verify_ok:
                for n in newly_rejected:
                    rejected[str(n)] = now
            pingable = [n for n in pingable if n in confirmed]

        if dry_run or send_fn is None or not pingable:
            # `send_fn is None` must NOT mark anything pinged — nothing was
            # delivered, so a later sweep still owes the user the alert
            # (jobs 21/24's contract, reused verbatim). A `pingable` empty
            # only because everything is still inside grace, or already
            # pinged, is equally a no-op this sweep.
            seen[root] = {"pinged": pinged, "rejected": rejected}
            continue

        shown = ", ".join("#%d" % n for n in pingable[:CARD_MAX_LISTED])
        more = ("" if len(pingable) <= CARD_MAX_LISTED
                else " a ďalších %d" % (len(pingable) - CARD_MAX_LISTED))
        # #369 review M1 (TRIGGERED): a per-repo "finished tickets never
        # reported" nag is exactly the ticket-work-scoped traffic #369's own
        # design comment (item 7) says belongs on the PROJECT thread, not
        # the shared owner pile — the same one-liner every other wired call
        # site already uses.
        from notify import stream_qualified
        status = send_fn(
            "\U0001f4ee **%s** — %d hotových ticketov bez hlásenia\n"
            "> Tieto tickety sa dokončili a zavreli, ale na telefón o nich "
            "neprišla žiadna správa: %s%s.\n"
            "> Práca je hotová — chýba len hlásenie o nej."
            % (name, len(pingable), shown, more),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="card-unreported:%s:%s"
                      % (name, "-".join(str(n) for n in pingable)),
            dry_run=dry_run, project=stream_qualified(name))
        logs.append("card-unreported PING %s -> %s" % (name, status))
        for n in pingable:
            pinged[str(n)] = now
        seen[root] = {"pinged": pinged, "rejected": rejected}

    if not dry_run:
        state["card_unreported"] = {k: v for k, v in seen.items() if k in live}
    return logs


# --------------------------------------------------------------------------- #
# Job 25 — REPORT RECONCILIATION (#525, the report-emission side of #523).
#
# A saturated `/process-subdev` (or `/autopilot`) SUPERVISOR consumes a worker
# return (task-notification) and continues `⏳ WORKING` WITHOUT writing the
# per-ticket `## ✅ Work Complete` report. The only compact request that fires
# for that boundary is `origin=subagent-stop`, which legitimately LAPSES on the
# `⏳` veto (`_compact_self_reported_complete` is `self-callback`-ONLY, #425
# by-design — #523 root cause). So the ticket gets neither a report NOR its
# per-ticket compact (the #411 `--self` path is a CONSEQUENCE of the report).
#
# Job 25 already computes "closed/merged tickets of this box" for missing CARDS
# (`card_reconcile` above). This is the SECOND check over the SAME closed set:
# a closed ticket whose closing SUPERVISOR session has NO subsequent report
# boundary since the close → after grace → ONE deduplicated in-band nudge into
# the supervisor pane (transcript-verified `send_verified`), with a
# consecutive-swallow give-up that escalates to the owner so a permanently
# swallowed delivery is never silent (#442-F2 / #502 / #509 / #511).
#
# It changes NOTHING about the compact veto (#425/#523 stay): the supervisor
# writing the report re-arms the per-ticket compact through the existing #411
# `--self` path all on its own.
# --------------------------------------------------------------------------- #

# The SAME canonical heading `hooks/stop-check-prose-violations.sh` and
# `watchdog/compact.py::_COMPACT_COMPLETION_HEADING_RX` anchor on — imported
# lazily at call time (below) rather than copied, so a genuine report is read
# by the identical rule that enforces it, never a parallel drifting spelling.


def _iso_epoch(s):
    """A CC transcript / compact-sync ISO-8601 timestamp -> epoch float, or
    None on anything unparseable. Handles both the transcript `…Z` form and
    the compact-sync `…+00:00` form. A tz-NAIVE value (no offset) is read as
    UTC, never local time — `datetime.timestamp()` on a naive datetime would
    otherwise shift the epoch by the box's local offset (a 2h error on a
    UTC+2 box), silently mis-ordering a boundary vs a close ts. Real sources
    are always tz-aware, so this is defensive, not a live path."""
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def report_boundary_after(rows, since_ts):
    """PURE (facts-in / verdict-out, #504): True iff any REAL assistant turn in
    `rows` dated `>= since_ts` carries the canonical `## ✅ Work Complete`
    heading. `rows` are already-parsed transcript jsonl dicts — the caller
    decides how many to read. An `isApiErrorMessage` turn and a turn with no
    parseable timestamp are skipped; a tool-only turn has empty text so the
    heading regex never matches it. Never reads the transcript itself, so a
    reviewer can attack the decision directly."""
    from watchdog.compact import _COMPACT_COMPLETION_HEADING_RX as _RX
    from watchdog.transcripts import _entry_text
    for e in rows or ():
        if not isinstance(e, dict) or e.get("type") != "assistant":
            continue
        if e.get("isApiErrorMessage") is True:
            continue
        ts = _iso_epoch(e.get("timestamp"))
        if ts is None or ts < since_ts:
            continue
        if _RX.search(_entry_text(e) or ""):
            return True
    return False


def _self_callback_records(log_lines):
    """`{sid: [epoch_ts, …]}` for every compact-sync.log line carrying
    `origin=self-callback` (a SEND / NOT-DELIVERED / SKIP-expired line — the
    only lines that name the origin). A `self-callback` origin arises ONLY
    from a well-formed `## ✅ Work Complete` report (the #411 Stop-hook /
    `compact-request --self` path), so such a line dated after a ticket's
    close is compaction-SURVIVING proof the report was written even after the
    heading itself scrolled out of the transcript. Cheap + bounded: the log is
    capped at `COMPACT_SYNC_LOG_LINES_MAX` lines."""
    out = {}
    for ln in log_lines or ():
        ts_str, _, payload = ln.partition(" ")
        if "origin=self-callback" not in payload:
            continue
        m = re.search(r"\bsid=(\S+)", payload)
        if not m:
            continue
        ep = _iso_epoch(ts_str)
        if ep is None:
            continue
        out.setdefault(m.group(1), []).append(ep)
    return out


def _autopilot_mutex_held(root):
    """True iff a LIVE session holds the #8 integration mutex for `root` right
    now — the supervisor is mid-integration (merge → gates → push), so a
    per-ticket report is not yet legitimately owed (#456: the lock guards only
    that critical section). Best-effort; any failure -> False (a lock-read
    error must never permanently suppress the check — the grace already covers
    a normal integration cycle, and the lock file existing+readable is the
    common case whenever it is genuinely held)."""
    try:
        from cli_autopilot_lock import (_autopilot_lock_path,
                                        _autopilot_lock_read, _pid_alive)
        holder = _autopilot_lock_read(_autopilot_lock_path(root))
        pid = holder.get("pid") if isinstance(holder, dict) else None
        return bool(pid) and _pid_alive(int(pid))
    except Exception:
        return False


def _report_escalate_ping(send_fn, root, owed, shown, owner, dry_run):
    """Fire ONE owner Discord ping when the in-band report nudge is
    PERMANENTLY swallowed (#442-F2 anti-silence — a wedged pane must never
    silently strand the owed report). Returns the send status, or None when
    there is no `send_fn`. Best-effort repo-name / project resolution: an
    unresolvable name degrades to the root path, never a raise."""
    if send_fn is None:
        return None
    name = ""
    try:
        from notify import repo_name_for
        name = repo_name_for(root) or ""
    except Exception:
        name = ""
    try:
        from notify import stream_qualified
        project = stream_qualified(name) if name else None
    except Exception:
        project = None
    return send_fn(
        "\U0001f4ee **%s** — supervisor nenapísal report a in-band "
        "pripomienku sa nedá doručiť\n"
        "> Tieto tickety sa dokončili a zavreli, ale supervisor pre ne "
        "nenapísal `## ✅ Work Complete` a nedá sa mu to napísať do panela: "
        "%s.\n> Skontroluj ten panel." % (name or root, shown),
        owner=owner or None,
        dedup_key="report-owed:%s:%s" % (name or root,
                                         "-".join(str(n) for n in owed)),
        dry_run=dry_run, project=project)


def report_reconcile(now, run, state, cwd_by_sid, panes_by_sid,
                     send_fn=None, dry_run=False, git_run=None,
                     owner_by_sid=None, projects_dir=None, sleep_fn=None,
                     window=None, grace=None, max_swallows=None, reprobe=None,
                     mutex_held=None, recent_human=None,
                     transcript_fn=None, rows_fn=None,
                     verified_send=None, compact_log_path=None,
                     owned_closed=None):
    """The #525 report-owed reconciliation — see the section comment.

    Iterates ONLY the SUPERVISOR (main-checkout) sessions in `cwd_by_sid` (a
    worker worktree owes no report). For each repo it re-derives the closed set
    the same way `card_reconcile` does — `merged_closes`, LOCAL git, which
    directly names what THIS supervisor merged (the right signal for "who owes
    the report", and free of a gh call). A ticket is OWED when it closed past
    `grace`, is not already DELIVERED (the `nudged` dedup, forever), and neither
    signal shows a report boundary since its close: a `## ✅ Work Complete`
    heading in the supervisor transcript (fresh, un-compacted report) OR a
    `origin=self-callback` compact-sync.log record for the sid (a report that
    was written+compacted out of the transcript). Delivery is an in-band nudge
    into the supervisor pane via `send_verified`, throttled by `reprobe`.

    #511 anti-silence: a PERSISTENTLY swallowed nudge is a DELIVERY-mechanics
    failure (a wedged pane that may clear). It escalates to the owner ONCE (the
    per-ticket `pinged` one-shot suppresses re-pinging) but then FALLS THROUGH
    to the bounded `reprobe` re-probe — the ticket is NEVER permanently stopped,
    so the moment the pane un-wedges the nudge lands (→ `nudged`, done). A
    landed nudge (verified) clears the swallow streak so a genuinely-new storm
    re-escalates. Only `nudged` (delivered) is a permanent per-ticket latch.

    #516: `nudged`/`pinged` are ONE-SHOT latches (a handled ticket is never
    re-evaluated), so the persisted `state["report_owed"]` write is gated on
    `not dry_run` — a diagnostic `--once --dry-run` mutates only a local copy
    and can never suppress the genuine nudge on a later real timer sweep."""
    if not cwd_by_sid:
        return []
    window = CARD_WINDOW_S if window is None else window
    grace = REPORT_GRACE_S if grace is None else grace
    max_swallows = REPORT_MAX_SWALLOWS if max_swallows is None else max_swallows
    reprobe = REPORT_REPROBE_S if reprobe is None else reprobe
    owner_by_sid = owner_by_sid or {}
    panes_by_sid = panes_by_sid or {}
    owned_closed = owned_closed or _owned_identity   # #534 owner scoping

    if mutex_held is None:
        mutex_held = _autopilot_mutex_held
    if recent_human is None:
        def recent_human(cwd, sid):
            try:
                from watchdog.compact import _compact_recent_human_activity
                return bool(_compact_recent_human_activity(
                    cwd, sid, now, projects_dir=projects_dir))
            except Exception:
                return False
    if transcript_fn is None:
        def transcript_fn(pd, sid, cwd):
            try:
                import watchdog
                return watchdog._transcript_for_session(pd, sid, cwd)
            except Exception:
                return None
    if rows_fn is None:
        def rows_fn(tpath):
            if not tpath:
                return []
            try:
                from watchdog.transcripts import _iter_jsonl_tail
                return _iter_jsonl_tail(str(tpath), REPORT_TAIL_LINES)
            except Exception:
                return []
    if verified_send is None:
        def verified_send(pane_id, text, tpath):
            try:
                import watchdog
                return bool(watchdog.send_verified(
                    pane_id, text, run, tpath, sleep_fn=sleep_fn))
            except Exception:
                return False

    # compact-sync.log self-callback records — read ONCE per sweep.
    records = {}
    try:
        if compact_log_path is None:
            from watchdog.compact import compact_sync_log_path
            compact_log_path = compact_sync_log_path()
        if compact_log_path:
            from pathlib import Path
            lines = Path(compact_log_path).read_text(
                encoding="utf-8").splitlines()
            records = _self_callback_records(lines)
    except OSError:
        records = {}                       # no log yet -> transcript alone

    owed_state = dict(state.get("report_owed") or {})
    logs, live, examined = [], set(), set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
        if not root or root in examined:
            continue
        if "/.claude/worktrees/" in root:
            continue                       # a worker worktree owes no report
        examined.add(root)
        base = _git_base_ref(root, git_run)
        if not base:
            continue                       # unmeasurable — never a finding
        live.add(root)
        closed = merged_closes(root, base, now - window, git_run)
        # #534: scope to the box's own slice. None = UNDETERMINABLE ownership (gh
        # flake / unresolved identity): skip WITHOUT popping the nudged dedup, or
        # gh recovering next sweep would re-nudge an already-nudged own ticket
        # (review MINOR-2). A real empty subset still returns {} (caller cleans up).
        scoped = owned_closed(root, closed)
        if scoped is None:
            continue
        closed = scoped
        if not closed:
            owed_state.pop(root, None)
            continue

        try:
            prev = owed_state.get(root) or {}
            nudged = {k: v for k, v in (prev.get("nudged") or {}).items()
                      if int(k) in closed}
            pinged = {k: v for k, v in (prev.get("pinged") or {}).items()
                      if int(k) in closed}
            swallows = int(prev.get("swallows") or 0)
            last_try = float(prev.get("last_try") or 0)

            def _save(sw=None, lt=None):
                owed_state[root] = {
                    "nudged": nudged, "pinged": pinged,
                    "swallows": swallows if sw is None else sw,
                    "last_try": last_try if lt is None else lt}

            pane = panes_by_sid.get(sid)
            pane_id = pane[0] if pane else None
            tpath = transcript_fn(projects_dir, sid, cwd)
            rows = rows_fn(tpath) if tpath else []
            sc_ts = records.get(sid) or ()

            owed = []
            for n in sorted(closed):
                if str(n) in nudged:
                    continue               # already DELIVERED — permanent dedup
                ts_close = closed[n]
                if ts_close is not None and now - ts_close < grace:
                    continue               # still inside its own grace
                if any(t >= ts_close for t in sc_ts) or \
                        report_boundary_after(rows, ts_close):
                    continue               # report present (fresh or compacted)
                owed.append(n)

            # detection is logged unconditionally (issue #36 print-always).
            if owed:
                logs.append("report-owed %s sid=%s issues=%s"
                            % (root, sid,
                               ",".join(str(n) for n in owed[:REPORT_MAX_LISTED])))

            if not owed:
                _save(sw=0)                 # nothing owed -> streak clears
                continue

            # vetoes suppress DELIVERY this sweep, never mark a ticket handled.
            if mutex_held(root):
                logs.append("report-owed SKIP mutex-held %s" % root)
                _save()
                continue
            if recent_human(cwd, sid):
                logs.append("report-owed SKIP recent-human %s" % root)
                _save()
                continue

            # #511: after a swallow, RE-PROBE on a bounded interval (never a
            # permanent stop). The FIRST attempt (swallows==0) is immediate.
            if swallows > 0 and (now - last_try) < reprobe:
                logs.append("report-owed backoff %s (streak=%d)"
                            % (root, swallows))
                _save()
                continue

            if dry_run or pane_id is None:
                _save()
                continue

            shown = ", ".join("#%d" % n for n in owed[:REPORT_MAX_LISTED])
            text = ("report-owed: %s — napíš per-ticket ## ✅ Work Complete "
                    "report + spusti compact-request --self" % shown)
            ok = verified_send(pane_id, text, tpath)
            last_try = now
            if ok:
                for n in owed:
                    nudged[str(n)] = now
                swallows = 0
                logs.append("report-owed NUDGE %s -> ok issues=%s"
                            % (root, shown))
            else:
                swallows += 1
                to_ping = [n for n in owed if str(n) not in pinged]
                if swallows >= max_swallows and to_ping:
                    shown_ping = ", ".join("#%d" % n
                                           for n in to_ping[:REPORT_MAX_LISTED])
                    status = _report_escalate_ping(
                        send_fn, root, to_ping, shown_ping,
                        owner_by_sid.get(sid), dry_run)
                    if status is not None:
                        logs.append("report-owed ESCALATE %s -> %s issues=%s"
                                    % (root, status, shown_ping))
                    # ONE-SHOT owner ping; the pane nudge KEEPS re-probing (#511)
                    for n in to_ping:
                        pinged[str(n)] = now
                else:
                    logs.append("report-owed swallowed %s (streak=%d) issues=%s"
                                % (root, swallows, shown))
            _save()
        except Exception as e:
            # one bad root must never crash the sweep or lose card_reconcile's
            # own logs (this runs concatenated after it in the job-25 lambda).
            logs.append("report-reconcile error %s: %r" % (root, e))
            continue

    if not dry_run:
        state["report_owed"] = {k: v for k, v in owed_state.items()
                                if k in live}
    return logs
