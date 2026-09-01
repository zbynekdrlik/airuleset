"""watchdog/repo_health.py -- delivery-stall watch (job 24) + managed-repo
discovery/net-drift/stuck-main sweeps (jobs 27/28) (#433, module split
cluster E).

WHY THIS FILE EXISTS. Extracted VERBATIM (a MOVE, not a rewrite -- same
discipline as #404's `watchdog/usage.py` and #433's own `watchdog/burn_jobs.py`
/`watchdog/cards.py`) from `watchdog/__init__.py` as part of #433's
continuation of #404's per-service module split: `delivery_state`/
`_delivery_stalled`/`delivery_stall_watch` (job 24, detects a repo whose
work branch keeps moving while nothing merges to its base) plus
`discover_managed_repos`/`_repo_label`/`_sweep_due`/`_repo_sweep_batch`/
`net_drift_alarm`/`stuck_main_sweep` (jobs 27/28, box-wide repo sweeps
independent of whether any pane is open in a given repo) form one
self-contained "repo health" cluster, called from exactly ONE place,
`run_once()`'s own job dispatch, and never called BY any other watchdog job.

`_git_commit_ts` moved here too (it was left behind when cluster F
extracted the rest of the shared git-primitive discussion, #433 cluster F's
own design comment) -- its only caller, `delivery_state`, is part of THIS
cluster, so leaving it alone in `__init__.py` would have made it dead code
there. `_git_first_line`/`_git_base_ref` themselves stay in
`watchdog/cards.py` (cluster F, already extracted, already facade-exported)
-- this file imports them with a plain `from watchdog.cards import
_git_first_line, _git_base_ref`, an ordinary FORWARD import between two
leaf modules (`cards.py` depends on neither `repo_health.py` nor
`watchdog/__init__.py`, so there is no cycle). This is the same "which way
does the dependency arrow point" test cluster F's own design comment and
the corresponding `.claude/rules/internals-watchdog.md` playbook entry
establish: a NEW module needing an OLD `__init__.py`-resident symbol is the
circular-risk direction (clusters C/D); a module needing something already
hosted in ANOTHER already-extracted leaf module is always safe.

Re-exported from `watchdog/__init__.py` (`from watchdog.repo_health import
...`, placed after every symbol this cluster depends on is already defined
-- it depends on nothing in `__init__.py` itself, only stdlib `os`/`re` +
the two names imported from `watchdog.cards` above) so every existing
caller (`run_once()`'s jobs 24/27/28 dispatch, calling these as bare
module-global names; `airuleset.py`'s own `from watchdog import
discover_managed_repos`; and the test suite's `wd.<name>` attribute access
in `tests/test_delivery_stall.py`/`tests/test_managed_repo_sweeps.py`)
needs zero changes. Same facade pattern as `watchdog/usage.py` (#404
cluster A), `watchdog/burn_jobs.py` (#433 cluster B), and `watchdog/cards.py`
(#433 cluster F): thin re-export block, `X as X` syntax for ruff, own
module docstring, zero behavior change.
"""

import os
import re

import notify                    # #560: episode_gate() default; no cycle
                                 # (notify imports no watchdog module).
from watchdog.cards import _git_first_line, _git_base_ref

# --------------------------------------------------------------------------- #
# Job 24 — DELIVERY-STALL WATCH (#138, camera-box 2026-07-11 → 2026-07-28).
#
# Measured, from camera-box's own git + GitHub state:
#
#   * PR #704 (dev -> main) OPEN since 2026-07-11T20:57Z — 17 days.
#     `mergeable: MERGEABLE`, `mergeStateStatus: BLOCKED`. Eleven of twelve
#     checks green; the one red is `Full-path E2E (rig zero-loss gate)`.
#   * That gate's last SUCCESS on dev was 2026-07-13T04:53Z. Since then:
#     105 failures, 31 cancelled, 0 successes. The failure is a rig
#     precondition ("GATE FAILED: 1 node(s) DRIFTED or PTP-DEGRADED"), not a
#     defect in any diff, so no amount of code work could clear it.
#   * `origin/main` frozen at 2026-07-11; `origin/dev` 422 commits ahead.
#   * Issue closures/day: 21, 21, 12, 15, 6, 4 (07-10..07-15) then ZERO until
#     a single one on 07-27 — because closure there is merge-driven, so a
#     blocked merge makes closure structurally zero no matter how much lands.
#
# The loop meanwhile kept spending: on 07-27/28 alone, 33 dispatches across
# ~15 distinct tickets and 85 commits, for zero merges.
#
# WHY NOTHING NOTICED, AND WHY THAT IS THE ACTUAL DEFECT. Every signal this
# repo owns is merge-TRIGGERED: the per-ticket run-card fires AFTER a merge,
# `autopilot-progress/<repo>.json` is fed by that card, the statusline shows
# an open-issue count that only ever grows. A loop that cannot merge is
# therefore silent BY CONSTRUCTION — silence and health are the same
# observation — which is how 17 days passed unremarked.
#
# WHAT THIS JOB MEASURES, and why it is git and not turns. Two purely LOCAL
# facts per repo hosting a live pane:
#   SPEND    = the newest commit on the checked-out branch (local HEAD; always
#              current, needs no fetch).
#   DELIVERY = the newest commit on the base branch (`origin/HEAD`, falling
#              back to origin/main / origin/master), plus the count of commits
#              on HEAD not reachable from it.
# Fresh SPEND with a frozen DELIVERY and a real backlog is the stall.
#
# A re-poke / no-dispatch detector was the obvious alternative and the
# evidence rejects it: it would have been silent on BOTH halves of this
# incident. Through 07-16..07-26 there were no turns to count (0 Agent
# dispatches, 0-153 transcript lines/day, `/goal` unarmed since 07-13 — a
# deliberate live-event pause, not a stuck agent, and not this repo's bug).
# Through 07-27..07-28 the loop dispatched CORRECTLY across ~15 distinct
# tickets with real commits, so a dispatch-liveness detector would have read
# perfectly healthy while zero work shipped. Dispatch is not delivery.
#
# Three properties keep it quiet where it should be quiet:
#   * HEAD == base (this repo, which pushes straight to main) yields 0
#     undelivered — silent STRUCTURALLY, not by threshold.
#   * A parked repo with no fresh commits is silent: nothing is being spent,
#     so there is nothing to warn about.
#   * A candidate is CONFIRMED before it is announced. The injected probe
#     fetches the base ref and the verdict is RE-MEASURED after it, so a
#     remote-tracking ref that had merely gone stale locally can never on its
#     own raise an alarm. The probe's other half (naming the blocking PR and
#     its red check) is pure enrichment — losing it costs the detail, never
#     the alert.
#
# Detection only, exactly like job 21: it never types into a pane and never
# touches the repo's worktree, index or local branches (a fetch writes only
# remote-tracking refs). Deciding what to do about a blocked merge — fix the
# gate, park the PR, split the batch — is the user's call, and this repo is
# not the owner of any repo it watches.
# --------------------------------------------------------------------------- #

DELIVERY_STALL_S = 172800        # 48h with no delivery; env AIRULESET_DELIVERY_STALL_S
DELIVERY_WORK_FRESH_S = 86400    # 24h — the work branch must be actively moving
DELIVERY_MIN_UNDELIVERED = 3     # a stray commit or two is not a stalled batch
DELIVERY_REPING_S = 86400        # re-ping daily while it persists, not per sweep

# The stall window is bounded on BOTH ends. Past this, the base branch is not
# a delivery target that stopped receiving — it is a branch nobody delivers to
# at all, and the difference is invisible from the lower bound alone. Found
# live six minutes after job 24 shipped: `~/varos/eft5000`, a GitLab repo
# whose `origin/master` last moved on 2019-09-07 (2515 days) while real work
# merges into `develop-50` — which took a merge the same day the alert fired.
# `origin/HEAD` is unset there, so the fallback picks a branch abandoned in
# 2019 and correctly reports 3,248 commits "undelivered" to it, forever.
# The upper bound costs a genuine stall nothing: one that ever reached it has
# already pinged every single day for three months on the way.
DELIVERY_STALL_MAX_S = 90 * 86400


def _git_commit_ts(cwd, ref, git_run=None):
    out = _git_first_line(cwd, ["log", "-1", "--format=%ct", ref], git_run)
    try:
        return int(out)
    except (TypeError, ValueError):
        return None


def delivery_state(cwd, now, git_run=None):
    """SPEND vs DELIVERY for `cwd`'s repo (see the section comment).

    Returns a dict — `root`, `base`, `undelivered`, `work_age`,
    `delivery_age`, `base_ts` — or None when the answer is UNMEASURABLE (not
    a git repo, no resolvable base ref, `git` unavailable, a count that did
    not parse). None is never a stall: a repo this cannot read is a repo this
    says nothing about, the same "never block on don't-know" contract every
    gate in `watchdog/compact.py` uses."""
    if not cwd:
        return None
    root = _git_first_line(cwd, ["rev-parse", "--show-toplevel"], git_run)
    if not root:
        return None
    base = _git_base_ref(root, git_run)
    if not base:
        return None
    head_ts = _git_commit_ts(root, "HEAD", git_run)
    base_ts = _git_commit_ts(root, base, git_run)
    if head_ts is None or base_ts is None:
        return None
    raw = _git_first_line(root, ["rev-list", "--count", base + "..HEAD"],
                          git_run)
    try:
        undelivered = int(raw)
    except (TypeError, ValueError):
        return None
    return {"root": root, "base": base, "undelivered": undelivered,
            "base_ts": base_ts,
            "work_age": now - head_ts, "delivery_age": now - base_ts}


def _delivery_stalled(st, stall, work_fresh, min_undelivered):
    """The verdict, in one place so the pre-probe and post-probe reads can
    never drift apart."""
    return (st is not None
            and st["undelivered"] >= min_undelivered
            and st["work_age"] <= work_fresh
            and stall <= st["delivery_age"] <= DELIVERY_STALL_MAX_S)


def delivery_stall_watch(now, run, state, cwd_by_sid, send_fn=None,
                         dry_run=False, git_run=None, delivery_probe=None,
                         owner_by_sid=None, project_by_sid=None,
                         stall=None, work_fresh=None, min_undelivered=None,
                         reping=None, authority=None,
                         owner_by_cwd=None, owners_seen=None, account_owner=""):
    """Job 24 — see the section comment.

    Gated on `delivery_probe` (the "wired = on" convention of jobs 8/11/16):
    the probe carries the confirming fetch, and a verdict that was never
    confirmed must not reach the user's phone.

    One repo is examined once per sweep however many panes sit in it, the
    DETECTION is logged every sweep (issue #36's print-always convention) and
    the PING is deduped per repo per `reping` window, so a stall that lasts
    weeks produces a daily reminder rather than one forgotten alert or 1,440
    a day. State is dropped the moment the base advances, so a later stall in
    the same repo pings again on its own."""
    if delivery_probe is None:
        return []
    # #667 — the develop->main delivery-stall metric is a full-authority /
    # gatekeeper concern. On a reduced-authority (fork-no-merge / branch-merge)
    # sub-dev box the session owner cannot merge the integration branch to main
    # (the gatekeeper's release pipeline), and its own work reaching the
    # integration branch is a REVIEW-PENDING state, not a delivery stall — so
    # never ping the session owner from here; the full-authority gk/owner box
    # evaluates the same repos and pings whoever can actually release. A
    # missing `authority` (a direct caller / test) owns the whole metric — the
    # SAME "no scoping = own everything" convention make_owned_closed_filter
    # uses — so every existing caller stays byte-identical (#134: log, never
    # silence).
    if authority not in (None, "full"):
        return ["delivery-stall skip:reduced-authority (%s)" % authority]
    if stall is None:
        try:
            stall = int(os.environ.get("AIRULESET_DELIVERY_STALL_S",
                                       DELIVERY_STALL_S))
        except ValueError:
            stall = DELIVERY_STALL_S
    work_fresh = DELIVERY_WORK_FRESH_S if work_fresh is None else work_fresh
    if min_undelivered is None:
        min_undelivered = DELIVERY_MIN_UNDELIVERED
    reping = DELIVERY_REPING_S if reping is None else reping
    owner_by_sid = owner_by_sid or {}
    # #717: same multi-owner coin-flip guard as the repo-sweep jobs. The
    # session's own pane owner (owner_by_sid[sid], then owner_by_cwd[cwd]) is
    # the repo-derived answer; a miss on a multi-owner box SKIPs rather than
    # falling to `owner=None` -> resolve_owner()'s box-wide coin flip (#707).
    # An EMPTY/1-owner set is not ambiguous -> every pre-#717 caller unchanged.
    owner_by_cwd = owner_by_cwd or {}
    ambiguous = len(set(owners_seen or ())) > 1
    seen = dict(state.get("delivery_stall") or {})
    logs = []
    live = set()
    examined = set()

    for sid, cwd in sorted((cwd_by_sid or {}).items()):
        st = delivery_state(cwd, now, git_run=git_run)
        if st is None or st["root"] in examined:
            continue
        root = st["root"]
        examined.add(root)
        live.add(root)
        if not _delivery_stalled(st, stall, work_fresh, min_undelivered):
            seen.pop(root, None)
            continue

        # CONFIRM, then announce. The probe fetches the base ref; the verdict
        # is re-read afterwards so a locally-stale remote-tracking ref cannot
        # by itself produce a ping. Enrichment is best-effort by design.
        try:
            info = delivery_probe(root, st["base"])
        except Exception:
            info = None
        confirmed = delivery_state(cwd, now, git_run=git_run) or st
        if not _delivery_stalled(confirmed, stall, work_fresh, min_undelivered):
            seen.pop(root, None)
            logs.append("delivery-stall confirmed-clear %s" % root)
            continue
        st = confirmed

        label = os.path.basename(root)
        days = int(st["delivery_age"] // 86400)
        logs.append("delivery-stall %s undelivered=%d delivery_age=%ds base=%s"
                    % (label, st["undelivered"], int(st["delivery_age"]),
                       st["base"]))
        prev = seen.get(root) or {}
        pinged = prev.get("pinged_ts")
        if dry_run or send_fn is None or (
                pinged is not None and now - float(pinged) < reping):
            # `send_fn is None` must NOT mark this as pinged — nothing was
            # delivered, so a later sweep with a real notify path still owes
            # the user this alert (job 21's contract, reused verbatim).
            continue
        # #717: resolve the recipient; a multi-owner box whose pane owner we
        # cannot resolve SKIPs (logged, no dedup advance -> retries when the
        # box becomes deliverable) rather than coin-flipping via owner=None.
        # The `_repo_owner_from_panes(root, ...)` tail makes this derivation
        # UNIFORM with jobs 27/28 (#717 review F3): if the first-sorted sid's
        # own owner is unresolved but another live pane in the SAME repo has a
        # unique owner, that owner is still derivable (and >1 owner in the repo
        # is treated as not-derivable -> the box guard, never an arbitrary win).
        derived = (owner_by_sid.get(sid)
                   or (owner_by_cwd.get(cwd) if cwd else None)
                   or _repo_owner_from_panes(root, owner_by_cwd))
        deliver, owner = _alert_recipient(derived, ambiguous, account_owner)
        if not deliver:
            logs.append("delivery-stall skip owner-ambiguous %s "
                        "(multi-owner box)" % label)
            continue
        blocker = ""
        if isinstance(info, dict) and info.get("pr"):
            blocker = "\n> Blokuje to PR #%s%s." % (
                info["pr"],
                (" — neprejde kontrola `%s`" % info["check"])
                if info.get("check") else "")
        status = send_fn(
            "\U0001f4e6 **%s** — %d dní sa nič nedoručilo\n"
            "> Na pracovnej vetve čaká %d commitov hotovej práce, ale do "
            "vetvy `%s` sa už %d dní nič nezlúčilo, takže sa nezatvára ani "
            "jeden ticket.%s"
            % (label, days, st["undelivered"], st["base"].split("/")[-1],
               days, blocker),
            owner=owner,   # #717: pane-derived / single-owner, never a coin flip
            dedup_key="delivery-stall:%s:%d" % (label, int(now // reping)),
            dry_run=dry_run)
        logs.append("delivery-stall PING %s -> %s" % (label, status))
        seen[root] = {"pinged_ts": now, "base_ts": st["base_ts"]}

    if not dry_run:
        # drop repos with no live pane this sweep, so the state file cannot
        # grow without bound across a long-lived watchdog (job 21's shape).
        state["delivery_stall"] = {k: v for k, v in seen.items() if k in live}
    return logs


# --------------------------------------------------------------------------- #
# Jobs 27/28 (#137, 2026-07-28). Both close the SAME observation gap: camera-
# box's +101 net-open drift ran two weeks before the user noticed by feel, and
# the merge deadlock behind most of it (origin/main frozen 2026-07-11) ran 15
# days before job 24 (#138) existed to catch it. Job 24 needed a LIVE PANE in
# the repo to fire at all — these two sweep EVERY locally-cloned repo on the
# box on a bounded cadence, independent of whether any session happens to be
# open in it right now, so a repo nobody is actively looking at still gets
# checked. #138's own corrected lesson applies here from the start: the
# corpus is `$HOME`, never a guessed project directory, and any age-only
# window is bounded on BOTH ends (a repo abandoned in 2019 is not a "stopped
# receiving" repo, it never was a delivery target at all).
#
# Repo discovery is INJECTED (`repo_roots`), never done by the jobs
# themselves — same "wired = on" convention as `delivery_probe`/`fleet_fetch`
# and the same reason: a unit test controls its own repo set explicitly, and
# `cmd_watchdog` (airuleset.py) owns the real `find $HOME -maxdepth N -name
# .git` sweep, gated by cadence so it costs the network once an hour, not
# once a minute.
# --------------------------------------------------------------------------- #

MANAGED_SWEEP_INTERVAL_S = 3600      # run at most once an hour; env AIRULESET_MANAGED_SWEEP_S


def discover_managed_repos(home=None, max_depth=4, run=None):
    """Every `.git` directory under `home` (default `$HOME`), `max_depth`
    levels deep, EXCLUDING dependency/build noise -- the real corpus for any
    job that must sweep "every repo on this box", per #138's own correction
    (the intuitive `~/devel` guess silently missed a real repo). Returns a
    sorted list of repo ROOT paths (parent of `.git`), deduped. Best-effort:
    an unreadable subtree is skipped, never raised."""
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    skip_names = {"node_modules", ".cache", ".local", "venv", ".venv",
                  "__pycache__", ".npm", ".cargo", "target", "dist", "build"}
    roots = set()
    base_depth = str(home).rstrip("/").count("/")
    for dirpath, dirnames, _filenames in os.walk(home, topdown=True):
        depth = dirpath.rstrip("/").count("/") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in skip_names]
        if ".git" in dirnames:
            roots.add(dirpath)
            dirnames.remove(".git")   # never descend into .git itself
    return sorted(roots)


def _repo_label(root, git_run=None):
    """The repo's canonical NAME for logging/dedup -- from `origin`, never
    the directory basename (a checkout can be renamed locally; #134's own
    lesson, reused here)."""
    url = _git_first_line(root, ["remote", "get-url", "origin"], git_run)
    if url:
        m = re.search(r'[:/]([^/]+/[^/]+?)(\.git)?$', url)
        if m:
            return m.group(1)
    return os.path.basename(str(root).rstrip("/"))


def _repo_is_fork(root, git_run=None):
    """True when `root` is a fork CLONE whose own origin/main is NOT its real
    delivery target -- detected purely locally by a distinct `upstream` remote
    (the standard fork-clone convention: `origin` = your fork, `upstream` = the
    source you integrate into). A fork's own default branch freezing is the
    EXPECTED steady state -- integration goes UPSTREAM (for the sub-dev streams,
    the gatekeeper does it) -- so job 28's stuck-main heuristic must never
    measure it (#441: kvaskodev/odoo-erp pinged David daily). Purely local git,
    no network / no auth -- job 28's own invariant is preserved.

    Accepted residual: a NON-fork primary repo carrying a stray `upstream`
    remote (a leftover mirror-push target, a one-off `git remote add upstream`)
    is treated as a fork and dropped from stuck-main alerts -- there is no
    opt-IN override, only the `AIRULESET_STUCK_MAIN_SKIP` opt-out. A real
    `$HOME` corpus scan (all 41 repos) found the only `upstream`-carrying repo
    is a genuine fork, so this residual is theoretical on the fleet today."""
    up = _git_first_line(root, ["remote", "get-url", "upstream"], git_run)
    if not up:
        return False
    origin = _git_first_line(root, ["remote", "get-url", "origin"], git_run)
    # A distinct upstream (or an upstream with no configured origin at all) is
    # the fork shape; an upstream identical to origin is not.
    return up != origin


def _sweep_due(state, key, now, interval):
    """True once per `interval`, tracked in `state[key]`. Shared cadence gate
    for jobs 27/28 -- neither needs to run every 60s poll; both cost a
    network round trip (gh / git fetch) per repo."""
    last = state.get(key)
    elapsed = None
    if last is not None:
        try:
            elapsed = now - float(last)
        except (TypeError, ValueError):
            elapsed = None    # unusable stamp -- treat as "due", never crash
    if elapsed is not None and elapsed < interval:
        return False
    return True


# #172 fix (2): dev1 alone hosts 40+ repos -- sweeping ALL of them every hour,
# each costing a `git fetch` (job 28) or two `gh issue list` calls (job 27),
# is exactly what blew the 120s TimeoutStartSec budget in the first place.
# Bound the batch and rotate through the full repo list via a cursor kept in
# state, so coverage still reaches every repo over successive hourly sweeps
# instead of either "all of them, maybe killed" or "arbitrarily few forever".
#
# The real per-repo stale-data bound this buys is `interval *
# ceil(n_repos / batch)` -- NOT "one hour of drift data" (the #172 fix's own
# original commit message, docstring and playbook entry all overclaimed
# this; corrected in the #172 reopened pass). At the default batch of 3 on
# a 40-repo box that's `ceil(40/3) = 14` hourly sweeps, i.e. up to ~14h
# before a given repo's drift/stuck-main state is re-measured -- a real
# trade, not the number an operator would reason from if only told "an
# hour". Batching cuts the REPO COUNT examined per sweep ~13x (40/3), which
# is what actually prevents the livelock; it is not a time-based cut and it
# does NOT make a killed sweep impossible: jobs 27+28 alone still cost up
# to ~105s worst case per sweep at the default batch (job 27: up to 2
# `gh issue list` calls at `AIRULESET_ISSUE_FETCH_TIMEOUT`-ish 10s each per
# repo x 3 repos = ~60s; job 28: one `git fetch` at 15s per repo x 3 repos =
# ~45s) -- comfortably under the 120s `TimeoutStartSec` on its own, but not
# a hard guarantee once jobs 24/25's own (also network-bound) per-pane
# probes ahead of them in the same sweep are accounted for.
REPO_SWEEP_BATCH_MAX = 3          # env AIRULESET_REPO_SWEEP_BATCH

# #560: `DEDUP_MEMORY_MAX_AGE_S` (the 30d age-out of a per-repo `net_drift`/
# `stuck_main` dedup entry whose repo stopped appearing in `repo_roots()`)
# is REMOVED -- jobs 27/28 no longer keep a per-repo dedup memory at all;
# episode_gate() owns the store and its own age-reaping (`EPISODE_MAX_AGE_S`,
# `_prune_episodes` in notify).


def _repo_sweep_batch(repos, state, cursor_key, max_repos=None):
    """Round-robin slice of `repos` (already deduped) bounded to `max_repos`
    per sweep. `repos` MUST be the same stable order every call (the caller
    passes a sorted list) or the cursor drifts. Returns the batch AND does
    NOT mutate `repos` itself -- only `state[cursor_key]` advances.

    #172 (reopened) finding 2: `max_repos <= 0` (the obvious spelling for
    "disable batching" -- `AIRULESET_REPO_SWEEP_BATCH=0`, or any negative
    value) must NEVER silently sweep the FULL repo list -- that re-arms the
    exact pathological cost this cap exists to prevent, via the knob an
    operator is most likely to reach for. Clamp to the documented default
    instead of trusting the value verbatim.

    #172 (reopened) smaller item: the short-list fast path (batch size >=
    repo count, whether from a small `max_repos` or a transient short
    `repos` list) must NOT reset the cursor to 0. `discover_managed_repos`
    is explicitly best-effort -- a mount hiccup or a permissions blip that
    makes ONE sweep see only 2 repos instead of 40 must not rewind the
    whole rotation once the real count comes back, or the tail of the list
    is starved another full rotation for nothing."""
    if max_repos is None:
        try:
            max_repos = int(os.environ.get("AIRULESET_REPO_SWEEP_BATCH",
                                           REPO_SWEEP_BATCH_MAX))
        except ValueError:
            max_repos = REPO_SWEEP_BATCH_MAX
    if max_repos <= 0:
        max_repos = REPO_SWEEP_BATCH_MAX
    n = len(repos)
    if n == 0 or max_repos >= n:
        # Leave state[cursor_key] untouched -- see the finding-2/short-list
        # docstring note above. Nothing to rotate through when the whole
        # list fits in one batch anyway.
        return list(repos)
    try:
        start = int(state.get(cursor_key, 0) or 0) % n
    except (TypeError, ValueError):
        start = 0
    end = start + max_repos
    if end <= n:
        batch = repos[start:end]
    else:
        batch = repos[start:n] + repos[0:end - n]
    state[cursor_key] = end % n
    return batch


# --------------------------------------------------------------------------- #
# Job 27 — NET-ISSUE-DRIFT ALARM (#137). Per managed repo, the trailing-7-day
# opened-minus-closed count via `gh`. camera-box's own +101 over 21 days is
# ~+34/week at its worst window -- this would have pinged around 2026-07-14,
# instead of the user noticing by feel two weeks later. Gated on
# `issue_counts_fetch` (the "wired = on" convention): the real callable does
# the `gh issue list --search` round trip; a unit test injects a fake so the
# job itself never shells out.
# --------------------------------------------------------------------------- #

NET_DRIFT_WINDOW_S = 7 * 86400
NET_DRIFT_THRESHOLD = 10          # net > this pings; env AIRULESET_NET_DRIFT_THRESHOLD
# #560: the old per-`reping`-window re-ping cadence is retired -- episode_gate
# alerts ONCE at onset + ONE recovery (hysteresis via `notify.EPISODE_CLEAR_AFTER`),
# so this job no longer has (or needs) a `reping` knob.


def _repo_owner_from_panes(root, owner_by_cwd):
    """#717 -- the box's own answer to "who works THIS repo here": the UNIQUE
    owner of a live pane whose cwd is AT or UNDER `root`. Returns that owner,
    or None when NO pane sits in the repo OR more than one distinct owner does
    (both are "not derivable" -> the caller falls back to the box single/multi-
    owner guard). Path-segment aware (a sibling `/repos/cb-old` never matches
    `/repos/cb`); `os.path.normpath` collapses a trailing slash / `.`. Never
    raises. This is what lets a REPO-scoped alert deliver to the right owner
    even on a multi-owner box, instead of a first-owner-seen coin flip (#707)."""
    if not root or not owner_by_cwd:
        return None
    base = os.path.normpath(str(root))
    prefix = base + os.sep
    owners = set()
    for cwd, owner in owner_by_cwd.items():
        if not owner or not cwd:
            continue
        c = os.path.normpath(str(cwd))
        if c == base or c.startswith(prefix):
            owners.add(owner)
    return next(iter(owners)) if len(owners) == 1 else None


def _alert_recipient(derived, ambiguous, account_owner):
    """#717 / #707 doctrine -- the recipient for a box-level repo-health alert
    on a possibly multi-owner box. Returns `(deliver, owner)`:

    - a repo/session-DERIVED owner (a live pane's owner for THIS repo) is
      always unambiguous -> `(True, derived)`;
    - else on a `not ambiguous` box `account_owner` is a reliable answer ->
      `(True, account_owner or None)`. A ZERO-owner box (account_owner "")
      collapses to owner None -> the shared channel, byte-identical to the
      pre-#717 caller path (locked by `test_pre_717_callers_unchanged_*`). A
      genuine SINGLE-owner box now delivers to that owner's OWN thread with an
      @mention -- a deliberate IMPROVEMENT over the pre-#717 `resolve_owner()`
      path (which, in the plain systemd watchdog env, resolved "" -> the
      shared channel), NOT recipient parity (#717 review F2);
    - else the box is MULTI-owner with no derived owner -> `account_owner`
      would be the first-owner-seen COIN FLIP (#707), so DO NOT deliver:
      `(False, None)`, and the caller logs the skip (machine channel, never a
      wrong-owner @mention). This mirrors the `("" if ambiguous else
      account_owner)` guard `deliver_pending_done` already carries, made
      strict: a genuinely-ambiguous alert is skipped + logged, not routed to
      a guessed owner. (`reping_stale_questions` used to carry the SAME
      guard; #795 retired the whole daily re-ask, so it is now a no-op that
      carries no owner-resolution logic at all.)

    RESIDUAL (#717 review F1, inherent to the reused #707 proxy): `ambiguous`
    is derived from ONE sweep's live-pane scan (`len(set(owners_seen)) > 1`).
    On a genuinely multi-owner box that MOMENTARILY shows only one owner's
    panes (the others' sessions closed, or the pane loop cut short by the
    sweep-deadline budget so `owners_seen` is partial), `ambiguous` is False
    and a pane-less foreign repo's alert can still deliver to the only-seen
    owner. This is the SAME accepted proxy `deliver_pending_done` carries
    (`reping_stale_questions` used to carry it too; #795 retired that job
    outright); a TTL'd owners-seen high-water mark would close it but is
    deliberately out of scope here (it would diverge from the sibling guard
    the #707 doctrine reuses). Named, not silently accepted."""
    if derived:
        return True, derived
    if not ambiguous:
        return True, (account_owner or None)
    return False, None


def net_drift_alarm(now, state, send_fn=None, dry_run=False, repo_roots=None,
                    issue_counts_fetch=None, git_run=None, threshold=None,
                    window=NET_DRIFT_WINDOW_S,
                    interval=MANAGED_SWEEP_INTERVAL_S, persist=None,
                    max_repos=None, episode_gate=None,
                    owner_by_cwd=None, owners_seen=None, account_owner=""):
    """Job 27 -- see the section comment. `issue_counts_fetch(repo_label,
    window_s) -> (opened, closed) | None` -- None means unmeasurable (no gh
    auth, rate-limited, repo not on GitHub) and is never treated as a stall.

    #172: `persist` (the caller's save-state closure, same shape jobs 8/11
    already use) is invoked BEFORE any per-repo network call leaves this
    process -- the live incident: systemd's TimeoutStartSec=120 killed the
    run mid-sweep, the cadence marker had only ever been set in run_once's
    OWN memory, and the next 60s tick re-attempted the identical 40-repo
    sweep, was killed again, forever. The repo list is also BOUNDED per
    sweep (`_repo_sweep_batch`) so a box with many repos doesn't try to
    fetch all of them in one 120s-budgeted run in the first place -- see
    `REPO_SWEEP_BATCH_MAX`'s own comment for the real (not "one hour")
    stale-data bound this buys.

    #172 (reopened) finding 4: the cadence marker is now persisted BEFORE
    `repo_roots()` even runs (an `os.walk($HOME)`, not free) -- a kill
    inside the walk itself used to lose the marker exactly like a kill
    inside the per-repo loop did. The cursor advance is persisted again
    once the batch is drawn, still before the first `gh` call.

    #560: dedup is now the OPT-IN `notify.episode_gate()` primitive (#558),
    not a per-repo `state['net_drift']` `pinged_ts` memory re-paged per
    time-bucket. For each MEASURED repo the gate is consulted with a
    stable `condition_key` ("net-drift:<label>") + a boolean `healthy`
    (net <= threshold), and it decides open/hold/clearing/recover/quiet --
    so a persistent backlog alerts ONCE at onset + ONE recovery message,
    never a daily re-page and never a silent clear. The gate is consulted
    ONLY when there is a real measurement AND `not dry_run and send_fn is
    not None`: a dry-run must not mutate the real episode store (#516), and
    `send_fn is None` (nothing was delivered) must not advance the episode,
    or a later sweep with a real notify path would owe no onset (job 24's
    own contract). `episode_gate` is injectable (default `notify.
    episode_gate`) for tests, same "wired = injected" convention as
    `issue_counts_fetch`/`send_fn`. The `persist`/cadence/cursor
    crash-safety (#172 F4 above) is UNCHANGED; only the per-repo dedup
    memory moved to the gate's own atomic store."""
    if issue_counts_fetch is None:
        return []
    gate = episode_gate if episode_gate is not None else notify.episode_gate
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_NET_DRIFT_THRESHOLD",
                                           NET_DRIFT_THRESHOLD))
        except ValueError:
            threshold = NET_DRIFT_THRESHOLD
    persist = persist or (lambda: None)
    # #717: on a MULTI-owner box (>1 owner with a pane here) the account-wide
    # owner is a first-owner-seen coin flip; an alert with no repo-derived
    # owner must not ping a guessed person. An EMPTY/1-owner set is NOT
    # ambiguous, so every pre-#717 caller (owners_seen=None) is unchanged.
    ambiguous = len(set(owners_seen or ())) > 1
    owner_by_cwd = owner_by_cwd or {}
    logs = []
    if not _sweep_due(state, "net_drift_last_sweep", now, interval):
        return logs
    if not dry_run:
        # #172 F4: stamp + persist BEFORE repo_roots() (the os.walk) runs --
        # not just before the per-repo network loop.
        state["net_drift_last_sweep"] = now
        # #560: the pre-episode_gate per-repo dedup memory is retired; pop the
        # legacy key once so it can't sit as dead state on already-deployed
        # boxes (the gate has its own atomic store, notify-episodes.json).
        state.pop("net_drift", None)
        persist()
    repos = sorted(set(repo_roots() if callable(repo_roots) else (repo_roots or [])))
    if dry_run:
        # dry-run must not mutate persistent state -- peek the batch on a
        # throwaway copy of state so the real cursor never advances.
        batch = _repo_sweep_batch(repos, dict(state), "net_drift_cursor", max_repos)
    else:
        batch = _repo_sweep_batch(repos, state, "net_drift_cursor", max_repos)
        persist()      # cursor advance also survives a kill BEFORE a
                        # single per-repo `gh` call leaves this process
    for root in batch:
        label = _repo_label(root, git_run)
        try:
            counts = issue_counts_fetch(label, window)
        except Exception as exc:
            counts = None
            logs.append("net-drift fetch-error %s: %r" % (label, exc))
        if not counts:
            continue          # unmeasurable -- don't observe the episode
        opened, closed = counts
        net = opened - closed
        logs.append("net-drift %s opened=%d closed=%d net=%+d"
                    % (label, opened, closed, net))
        if dry_run or send_fn is None:
            # No real delivery path -> do NOT consult the gate (a dry-run must
            # not mutate the store #516; `send_fn is None` delivered nothing,
            # so a later real sweep still owes the onset). Measurement is
            # already logged above for diagnostics.
            continue
        # #717: resolve the recipient BEFORE the gate. A multi-owner box with
        # no pane in this repo has no unambiguous owner -> SKIP to the machine
        # channel (logged) WITHOUT consulting the gate, so the episode stays
        # fresh and fires an onset once the box becomes deliverable -- the
        # same "no delivery path -> don't observe" shape as the branch above.
        deliver, owner = _alert_recipient(
            _repo_owner_from_panes(root, owner_by_cwd), ambiguous, account_owner)
        if not deliver:
            logs.append("net-drift skip owner-ambiguous %s "
                        "(multi-owner box, no repo pane)" % label)
            continue
        healthy = net <= threshold
        decision = gate("net-drift:%s" % label, healthy, now=now)
        logs.append("net-drift GATE %s -> %s" % (label, decision))
        if decision == "open":
            status = send_fn(
                "\U0001f4c8 **%s** -- backlog rastie: +%d ticketov za posledny "
                "tyzden\n"
                "> Za poslednych 7 dni pribudlo %d novych a zavrelo sa len %d -- "
                "backlog rastie rychlejsie, ako sa stiha spracovavat."
                % (label, net, opened, closed),
                owner=owner,   # #717: repo-derived / single-owner, never a coin flip
                # send-layer key is FRESH per instant (int(now), the gk_request
                # / #535 / #543 pattern), never a coarser bucket than the gate's
                # own primary dedup -- else notify's TTL would swallow a genuine
                # re-open of a NEW episode.
                dedup_key="net-drift-open:%s:%d" % (label, int(now)),
                dry_run=dry_run)
            logs.append("net-drift PING %s -> %s" % (label, status))
        elif decision == "recover":
            status = send_fn(
                "✅ **%s** -- backlog uz nerastie\n"
                "> Za posledny tyzden pribudlo %d novych a zavrelo sa %d "
                "ticketov -- backlog sa vratil pod kontrolu (predtym nahlaseny "
                "rast je vyrieseny)."
                % (label, opened, closed),
                owner=owner,   # #717
                dedup_key="net-drift-recover:%s:%d" % (label, int(now)),
                dry_run=dry_run)
            logs.append("net-drift RECOVER %s -> %s" % (label, status))
    return logs


# --------------------------------------------------------------------------- #
# Job 28 — STUCK-MAIN SWEEP (#137). Per managed repo, purely local git (no
# `gh` call, no auth needed): the base branch (`origin/HEAD`, same resolver
# job 24 uses) has not moved in `age_threshold`, while the checked-out
# branch carries more than `ahead_threshold` commits not reachable from it.
# This is job 24's OWN measurement (`delivery_state`/`_delivery_stalled`),
# reused deliberately rather than reimplemented -- the difference is scope:
# job 24 only ever sees a repo with a LIVE PANE open in it right now; this
# sweeps every repo on the box on its own cadence, so a repo nobody has a
# session open in still gets checked. Bounded on both ends, per #138's own
# fix (`DELIVERY_STALL_MAX_S`) -- reused here too, so a repo that simply
# never merges anywhere (an abandoned fork) does not alarm forever.
# --------------------------------------------------------------------------- #

STUCK_MAIN_AGE_S = 5 * 86400          # env AIRULESET_STUCK_MAIN_AGE_S
STUCK_MAIN_AHEAD_MIN = 20             # env AIRULESET_STUCK_MAIN_AHEAD
# #560: no `reping` re-page cadence any more -- episode_gate (onset + one
# recovery via hysteresis) replaced the per-window re-ping.


def _stuck_main_skip_set():
    """`owner/repo` labels an operator explicitly opted OUT of the stuck-main
    sweep via `AIRULESET_STUCK_MAIN_SKIP` (comma/space separated) -- the
    per-repo opt-out (#441) for a fork/mirror clone whose delivery goes
    elsewhere but which lacks the `upstream`-remote convention `_repo_is_fork`
    auto-detects. Empty / unset -> empty set (never skips anything)."""
    raw = os.environ.get("AIRULESET_STUCK_MAIN_SKIP", "")
    return {tok for tok in re.split(r"[,\s]+", raw) if tok}


def stuck_main_sweep(now, state, send_fn=None, dry_run=False, repo_roots=None,
                     git_run=None, git_fetch=None, age_threshold=None,
                     ahead_threshold=None,
                     interval=MANAGED_SWEEP_INTERVAL_S, persist=None,
                     max_repos=None, episode_gate=None,
                     owner_by_cwd=None, owners_seen=None, account_owner=""):
    """Job 28 -- see the section comment. `git_fetch(root)` is called (best-
    effort before this pass -- see the #172 reopened note below) before
    reading refs, since no live session may have fetched this repo recently
    -- injected so a test never shells a real network fetch. `git_fetch=None`
    skips the fetch entirely (a test working with fixture repos that have
    no real remote).

    #172: `persist` (same shape jobs 8/11/27 already use) is invoked BEFORE
    any per-repo `git fetch` leaves this process, and the repo list is
    BOUNDED per sweep (`_repo_sweep_batch`) -- see net_drift_alarm's
    docstring for the full incident this fixes (a systemd
    TimeoutStartSec=120 kill mid-sweep, with the cadence marker never
    reaching disk, re-attempting the identical sweep forever) and
    `REPO_SWEEP_BATCH_MAX`'s own comment for the real (not "one hour")
    stale-data bound the batching buys.

    #172 (reopened) finding 5: a git_fetch FAILURE now SKIPS the repo for
    this sweep rather than falling through to `delivery_state()` on
    whatever refs are already on disk. A repo not fetched in days has a
    stale `origin/<base>` -- measuring on it inflates both `delivery_age`
    and `undelivered`, which is exactly the stuck-main signature this job
    pings on. A repo merely behind a slow link must never read as stuck.

    #172 (reopened) finding 4: the cadence marker is persisted BEFORE
    `repo_roots()` runs (see net_drift_alarm's matching note); the cursor
    advance is persisted again once the batch is drawn.

    #560: dedup is the OPT-IN `notify.episode_gate()` primitive (#558), not a
    per-repo `state['stuck_main']` `pinged_ts` memory re-paged per time-bucket
    -- see net_drift_alarm's matching #560 note. For each MEASURED repo
    (not fork-skipped, fetch OK, `delivery_state` succeeded) the gate is
    consulted with "stuck-main:<label>" + `healthy = not stalled` and decides
    open/hold/clearing/recover/quiet: a persistently stuck main alerts ONCE at
    onset + ONE recovery when it moves again, never a daily re-page. A
    fork-skip / fetch-error / unmeasurable repo does NOT consult the gate (we
    did not observe the condition, so the episode is left untouched -- an
    age-reap in the store handles a repo that stops being measured). Same
    dry-run / `send_fn is None` gate-suppression as net_drift_alarm. The
    `persist`/cadence/cursor crash-safety (#172 F4/F5 above) is UNCHANGED."""
    if age_threshold is None:
        try:
            age_threshold = int(os.environ.get("AIRULESET_STUCK_MAIN_AGE_S",
                                               STUCK_MAIN_AGE_S))
        except ValueError:
            age_threshold = STUCK_MAIN_AGE_S
    if ahead_threshold is None:
        try:
            ahead_threshold = int(os.environ.get("AIRULESET_STUCK_MAIN_AHEAD",
                                                  STUCK_MAIN_AHEAD_MIN))
        except ValueError:
            ahead_threshold = STUCK_MAIN_AHEAD_MIN
    persist = persist or (lambda: None)
    gate = episode_gate if episode_gate is not None else notify.episode_gate
    # #717: same multi-owner coin-flip guard as net_drift_alarm -- an EMPTY/
    # 1-owner set is not ambiguous, so every pre-#717 caller is unchanged.
    ambiguous = len(set(owners_seen or ())) > 1
    owner_by_cwd = owner_by_cwd or {}
    logs = []
    if not _sweep_due(state, "stuck_main_last_sweep", now, interval):
        return logs
    if not dry_run:
        # #172 F4: stamp + persist BEFORE repo_roots() (the os.walk) runs.
        state["stuck_main_last_sweep"] = now
        # #560: retire the pre-episode_gate per-repo dedup memory (moved to
        # the gate's own atomic store); pop the legacy key once.
        state.pop("stuck_main", None)
        persist()
    repos = sorted(set(repo_roots() if callable(repo_roots) else (repo_roots or [])))
    if dry_run:
        # dry-run must not mutate persistent state -- peek the batch on a
        # throwaway copy of state so the real cursor never advances.
        batch = _repo_sweep_batch(repos, dict(state), "stuck_main_cursor", max_repos)
    else:
        batch = _repo_sweep_batch(repos, state, "stuck_main_cursor", max_repos)
        persist()      # cursor advance also survives a kill BEFORE a
                        # single `git fetch` leaves this process
    skip_set = _stuck_main_skip_set()
    for root in batch:
        label = _repo_label(root, git_run)
        # #441: a fork's origin/main is deliberately frozen (delivery goes
        # upstream, not to its own main), so this base ref is never a stalled
        # merge -- skip it BEFORE the fetch. #560: a skip does NOT consult the
        # gate (we did not measure the condition -- the episode is left as-is,
        # age-reaped by the store if the repo stops being measured). Auto-
        # detected via a distinct `upstream` remote (purely local, no network)
        # or via the explicit opt-out list.
        if label in skip_set or _repo_is_fork(root, git_run):
            logs.append("stuck-main skip %s (fork/opt-out)" % label)
            continue
        if git_fetch is not None:
            try:
                git_fetch(root)
            except Exception as exc:
                logs.append("stuck-main git-fetch-error %s: %r" % (root, exc))
                continue        # #172 F5: refs may be STALE -- never
                                 # measure on them, skip this repo entirely
        st = delivery_state(root, now, git_run=git_run)
        if st is None:
            continue            # unmeasurable -- don't observe the episode
        stalled = (st["undelivered"] >= ahead_threshold
                   and age_threshold <= st["delivery_age"] <= DELIVERY_STALL_MAX_S)
        logs.append("stuck-main %s undelivered=%d delivery_age=%ds base=%s"
                    % (label, st["undelivered"], int(st["delivery_age"]),
                       st["base"]))
        if dry_run or send_fn is None:
            continue            # no real delivery path -> don't consult the
                                 # gate (#516 dry-run store guard; a `send_fn
                                 # is None` sweep delivered nothing, so a later
                                 # real sweep still owes the onset)
        # #717: resolve the recipient BEFORE the gate (see net_drift_alarm's
        # matching comment) -- a multi-owner box with no pane in this repo has
        # no unambiguous owner, so SKIP to the machine channel (logged) rather
        # than coin-flip the alert, and leave the episode fresh.
        deliver, owner = _alert_recipient(
            _repo_owner_from_panes(root, owner_by_cwd), ambiguous, account_owner)
        if not deliver:
            logs.append("stuck-main skip owner-ambiguous %s "
                        "(multi-owner box, no repo pane)" % label)
            continue
        healthy = not stalled
        decision = gate("stuck-main:%s" % label, healthy, now=now)
        logs.append("stuck-main GATE %s -> %s" % (label, decision))
        base = st["base"].split("/")[-1]
        if decision == "open":
            days = int(st["delivery_age"] // 86400)
            status = send_fn(
                "\U0001f512 **%s** -- vetva %s stoji %d dni, %d commitov caka na zluenie\n"
                "> Praca sa hromadi na pracovnej vetve, ale do %s sa uz %d dni nic "
                "nezluilo -- skontroluj, ci nie je zablokovany merge/PR."
                % (label, base, days, st["undelivered"], base, days),
                owner=owner,   # #717: repo-derived / single-owner, never a coin flip
                # FRESH per-instant key (#535/#543) -- never a bucket that
                # notify's TTL could use to swallow a new episode's re-open.
                dedup_key="stuck-main-open:%s:%d" % (label, int(now)),
                dry_run=dry_run)
            logs.append("stuck-main PING %s -> %s" % (label, status))
        elif decision == "recover":
            status = send_fn(
                "✅ **%s** -- vetva %s sa zase hybe\n"
                "> Do %s sa uz zlucuje -- predtym nahlaseny zablokovany merge "
                "je vyrieseny."
                % (label, base, base),
                owner=owner,   # #717
                dedup_key="stuck-main-recover:%s:%d" % (label, int(now)),
                dry_run=dry_run)
            logs.append("stuck-main RECOVER %s -> %s" % (label, status))
    # #560: no per-repo dedup-memory prune here any more -- episode_gate owns
    # its own store + `_prune_episodes` age-reaping.
    return logs
