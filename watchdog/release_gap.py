"""Release-gap nudge (#616) — surface a stalled release train into an armed
FULL-authority `/goal` loop.

INCIDENT (owner, recurring): the gatekeeper's `/goal` loop keeps merging sub-dev
work into the integration branch (`develop`), but nothing ever nudges it to START
the release train (develop -> staging -> main + deploy + verify), so merged work
sits UNRELEASED for days ("práca zmergovaná, ale nikdy nevydaná"). Root cause is
structural, traced in `watchdog/__init__.py` run_once():

  * Job 20 (`goal.goal_lane_sweep` + its riders `goal_lane_occupancy_nudge`,
    `ops_wait_recheck.goal_ops_wait_recheck`) is the ONLY family that keystrokes
    into an armed `/goal` pane — but its signals are lane occupancy (fill worker
    lanes) and the W/I partition audit, NEITHER of which reads develop-vs-main.
  * Jobs 24/28 (`delivery_stall_watch` / `stuck_main_sweep`) DO measure "fresh
    work over a frozen base", but structurally cannot serve this: they are
    DETECTION-ONLY (a deduped Discord ping, never a keystroke — "what to do about
    a blocked merge is the user's call"), they measure the CHECKED-OUT branch vs
    its base (the gk odoo-erp checkout is a detached HEAD, not develop), they
    dedup to ONE ping per repo per day (so a days-old stall surfaces at most once
    and is easily lost in the feed), and they never check "a release is in
    flight". So the owner gets, at best, a phone ping — never an in-session nudge
    to run the release.

WHAT THIS DOES: on a per-session cadence (~1h, env-tunable — #812, owner "release
train bez prestojov"; was 6h until a live gk stall proved 6h outlasts how often
the release-in-flight signal flaps, so the anchor never aged to cadence), for an
armed `/goal` pane on a FULL-authority box whose integration branch is ahead of
prod AND no release is in flight, deliver ONE verified keystroke reminding the
session to run its release pipeline. The FULL-authority gate is the INVERSE of #618: #618
narrowed the lane-occupancy nudge's SKIP to `authority is None` (widening THAT
nudge to reduced-authority stream boxes too), whereas a release train is run ONLY
by the gatekeeper, so THIS nudge fires ONLY where `resolve_authority(cwd) ==
"full"`. Since airuleset#827 `resolve_authority` fails SAFE to `fork-no-merge`
for an unmapped user (only the explicit full accounts resolve "full"), so the
authority gate now excludes an unmapped box too; the primary gk-narrowing is
still the release-train SHAPE, not the authority word alone: the fetch
requires BOTH an integration branch ahead of prod AND a `staging` branch to
exist, so a full-authority box that is not a 3-branch release repo is never
nudged. A release IN FLIGHT (an open develop->staging / staging->main PR, or a
running deploy/release workflow) suppresses the nudge and RESETS the stall anchor
— the train is already moving.

DESIGN (#486 reuse, ZERO new delivery/fetch/keystroke primitives): this rides
`goal_lane_sweep`'s EXISTING armed-candidate-pane loop (which resolves
pid/cwd/sid/tpath/loc, the structured `state["goal_mark"]` armed gate, and the
per-sweep `handled` set) — a faithful sibling of `ops_wait_recheck` (#547/#578).
It runs AFTER the lane nudge and the ops-wait recheck, respects `handled` (at most
ONE keystroke per pane per sweep), and reuses `watchdog.send_verified` (with the
#594 delivered-unconfirmed `out`), `_janitor_mark_watch`/`_janitor_clear_watch`,
and the shared `stuck-check: ` own-payload prefix (already in
`_JANITOR_OWN_PREFIXES` + `_MACHINE_PROMPT_PREFIXES`, so a swallowed nudge is
reclaimable AND never mistaken for a human answer). NOT a separately-numbered job:
it is a third rider on job 20's armed-pane loop, exactly like the ops-wait and
i-members riders — a separate numbered job would duplicate the pane walk (against
#486).

STATE (`{"ahead": int, "in_flight": bool, "train": bool}` or None — the
`train` key, #698, feeds only the sibling ops-wait release-landed escalation;
THIS decider ignores it) comes from ONE injected
`release_state_fetch(cwd)` seam (network kept out of run_once unit tests, exactly
like `backlog_fetch`/`ops_wait_fetch`), read through `_cached_release_state` (a
per-repo TTL cache SHARED with that #698 consumer) so the gh subprocess fires
at most once per repo per TTL,
never every sweep per pane. None (undetermined — a gh/ssh error, or a repo with
no integration branch) fails SAFE to `skip`: never a false nudge.

The verdict logic is a PURE `_release_decision(rec, rstate, now, cadence,
min_ahead)`; all I/O (authority read, fetch, send, state writes) lives in
`goal_release_gap_recheck` behind the same injectable seams the sibling jobs use,
and `dry_run` mutates nothing (#516).

CADENCE / no give-up: a stalled gap is first nudged one cadence after it appears,
then re-nudged every cadence while it persists — never permanently silent (the
#134 anti-silence invariant), mirroring `ops_wait_recheck` which likewise has no
give-up owner-ping. A one-shot owner escalation after N ignored nudges was
EVALUATED and DEFERRED for the first cut: the PRIMARY mechanism is the in-session
nudge (a single effective nudge starts the train -> in_flight -> skip); a stalled
train that ignores repeated nudges is a deeper problem, re-surfaced every cadence
by the journalled decision line. REOPEN if a live loop is observed ignoring the
nudge for days.
"""
import os

import watchdog
from watchdog import nudge_gate as _nudge_gate   # #797 shared cadence gate

# env AIRULESET_RELEASE_GAP_CADENCE_S — how long a stalled release gap sits (and
# how long between re-nudges) before this job re-surfaces it. 1h (#812, owner
# "release train bez prestojov" — hourly checks, not more often): 6h was longer
# than how often the release-in-flight signal FLAPS (each flap resets the stall
# anchor via the `inflight` action), so the wait age never reached the cadence —
# LIVE gk forensics 2026-09-01: 1103 decisions on a real 210->254-commit gap,
# ZERO nudges all day (longest continuous wait window ~3h34m < 6h). An hourly
# cadence fires in the STALL GAPS between flaps (4 such >1h windows that day).
RELEASE_GAP_CADENCE_S = 1 * 3600
# floor for the env override (#504/#543 floor-clamp lesson): the owner's intended
# hourly cadence must be REACHABLE, so the floor is 1h (a 2h floor would have
# clamped a 1h override back to 2h); it still clamps a units-error sub-hour value
# so the nudge can never become a per-sweep nag.
RELEASE_GAP_MIN_S = 1 * 3600
# minimum integration-ahead-of-prod commit count before the gap is "real" (env
# AIRULESET_RELEASE_GAP_MIN_AHEAD, floored at 1 — a units error must never make a
# 0-commit "gap" nudge). Default 1: any unreleased integration commit qualifies.
RELEASE_GAP_MIN_AHEAD = 1
# env AIRULESET_RELEASE_STATE_FETCH_TTL_S — how long a `{ahead,in_flight,train}` read is
# CACHED per repo (`state["release_state_cache"]`, keyed by cwd, shared across
# every armed pane on that repo). 30 min — half the #812 1h cadence (was ~8% of
# the old 6h): a stale in_flight sample can delay a nudge by <=1 TTL, so a stall
# window must exceed ~1.5h to guarantee a nudge — comfortably under the
# multi-hour stalls this watches, so the release state never needs minute-fresh;
# a resolved release is re-detected within <=1 TTL. Floored so a units error
# can't turn it into a per-sweep gh call.
RELEASE_STATE_FETCH_TTL_S = 30 * 60
RELEASE_STATE_FETCH_TTL_MIN_S = 5 * 60
# a FAILED/unmeasurable fetch (None) is cached only briefly so a transient gh
# hiccup re-checks soon rather than suppressing detection for a whole TTL.
RELEASE_STATE_FETCH_FAIL_TTL_S = 60
# orphan-reaper TTL for a per-sid rec whose session is gone (mirror of
# ops_wait_recheck.OPS_WAIT_RECHECK_ORPHAN_TTL_S): the `visited_sids` gate is
# PRIMARY (a live pane is never reaped), this is the SECONDARY safety for a
# budget-deferred pane.
RELEASE_GAP_ORPHAN_TTL_S = 24 * 3600

# #749 — bounded retry, mirroring ops_wait_recheck.MAX_SEND_FAILS (#714). A pane
# whose submit is persistently swallowed (verify fails every sweep — e.g. it sat
# in CC's "Waiting for N background agents" state, or was otherwise un-typeable)
# would otherwise be re-typed every ~60s sweep forever, since `last_nudge` only
# advances on a CONFIRMED send: type -> head/tail verify fails -> undo -> retype,
# endlessly (the owner's "dokolecka promptuje"). After MAX_SEND_FAILS consecutive
# undelivered sends the nudge backs off one full cadence (advance `last_nudge`),
# bounding the storm to <= MAX_SEND_FAILS keystrokes per cadence — i.e. per HOUR
# since the #812 1h cadence (was per-6h); still bounded, and a bg-agent swallow
# is caught earlier by the `_BG_AGENTS_WAIT_RX` busy-pane gate before it counts.
MAX_SEND_FAILS = 3


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _cadence():
    """The effective cadence: the env override, floored at RELEASE_GAP_MIN_S so a
    units-error / accidental sub-hour value can never turn the nudge into a
    per-sweep nag (#504/#543)."""
    return max(_env_int("AIRULESET_RELEASE_GAP_CADENCE_S", RELEASE_GAP_CADENCE_S),
               RELEASE_GAP_MIN_S)


def _min_ahead():
    """The effective minimum integration-ahead count, floored at 1."""
    return max(_env_int("AIRULESET_RELEASE_GAP_MIN_AHEAD", RELEASE_GAP_MIN_AHEAD), 1)


def _fetch_ttl():
    """The cache TTL for a real release-state read, floored so an env units error
    can't collapse it back to a per-sweep fetch."""
    return max(_env_int("AIRULESET_RELEASE_STATE_FETCH_TTL_S",
                        RELEASE_STATE_FETCH_TTL_S), RELEASE_STATE_FETCH_TTL_MIN_S)


def _integration_branch():
    """The integration branch name (env AIRULESET_RELEASE_INTEGRATION_BRANCH,
    default `develop`), read at CALL time (#545 — never an import-time constant)."""
    return os.environ.get("AIRULESET_RELEASE_INTEGRATION_BRANCH", "develop")


def _prod_branch():
    """The production branch name (env AIRULESET_RELEASE_PROD_BRANCH, default
    `main`), read at CALL time."""
    return os.environ.get("AIRULESET_RELEASE_PROD_BRANCH", "main")


def _fmt_age(seconds):
    """Human age of a stalled gap for the decision log — MINUTES under 2h (#812
    review: with the 1h cadence a sub-hour wait rendered "~0h", hiding exactly
    the window-length signal a flap-vs-cadence audit needs), hours under 48h,
    days beyond. A negative age (a future anchor under clock skew) clamps to 0."""
    seconds = max(0, seconds)
    if seconds < 2 * 3600:
        return "~%dm" % int(seconds // 60)
    if seconds < 48 * 3600:
        return "~%dh" % int(seconds // 3600)
    return "~%dd" % int(seconds // 86400)


def _sig(rstate):
    """A stable signature of the release state — the ahead count and in-flight
    flag — stored for observability. `?` names an undetermined half honestly."""
    if not isinstance(rstate, dict):
        return "?"
    a = rstate.get("ahead")
    f = rstate.get("in_flight")
    return "ahead:%s|inflight:%s" % (a if isinstance(a, int) else "?",
                                     f if isinstance(f, bool) else "?")


def _cached_release_state(cwd, fetch, state, now, ttl=None, fail_ttl=None):
    """A per-cwd TTL cache over the release-state `fetch` — the faithful sibling
    of `ops_wait_recheck._cached_member_fetch` (#547), for a DICT value instead
    of a list. Without it the fetch would spawn a gh subprocess EVERY 60s sweep
    for EVERY armed pane on the 120s-budgeted sweep's critical path. Bounds it to
    at most one subprocess per repo per TTL, shared across every armed pane there.

    `fetch is None` (not wired) -> None, no cache write (the "wired = on"
    convention). A fetch exception -> None. A `ts` crossing the JSON persistence
    boundary is type-checked (a malformed/legacy entry reads as EXPIRED, never
    raises). None (unmeasurable) is cached only for `fail_ttl` so a transient gh
    hiccup re-checks soon. Returns a `dict` or None — never a guessed {}."""
    if fetch is None:
        return None
    ttl = _fetch_ttl() if ttl is None else ttl
    fail_ttl = RELEASE_STATE_FETCH_FAIL_TTL_S if fail_ttl is None else fail_ttl
    cache = state.setdefault("release_state_cache", {})
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        try:
            age = now - float(entry.get("ts", 0))
        except (TypeError, ValueError):
            age = None
        if age is not None:
            rstate = entry.get("rstate")
            entry_ttl = ttl if isinstance(rstate, dict) else fail_ttl
            if age < entry_ttl:
                return rstate if isinstance(rstate, dict) else None
    try:
        rstate = fetch(cwd)
    except Exception:
        rstate = None
    if not (rstate is None or isinstance(rstate, dict)):
        rstate = None
    cache[cwd] = {"ts": now, "rstate": rstate}
    return rstate


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state) + rstate ({"ahead": int, "in_flight": bool,
# "train": bool} or None; `train` (#698) is IGNORED here) -> (action, new_rec, reason). action:
#   "skip"     -- undetermined (rstate None, or a non-int ahead / non-bool
#                 in_flight) -> NEVER a nudge, NEVER a state change (safe direction);
#   "clear"    -- no gap (ahead < min_ahead) -> drained, pop the sid's rec;
#   "inflight" -- gap AND a release is in flight -> reset the stall anchor, no
#                 nudge (the train is already moving);
#   "wait"     -- gap, no release in flight, still inside the grace/reping window
#                 -> persist (seed first_seen, refresh sig), no nudge;
#   "nudge"    -- gap, no release in flight, past the cadence -> the caller
#                 ATTEMPTS a verified send and advances last_nudge on a CONFIRMED
#                 submit, OR (#749) after MAX_SEND_FAILS consecutive UNDELIVERED
#                 attempts (the bounded-retry back-off — one cadence, so a
#                 persistently-swallowing pane is not re-typed every sweep).

def _release_decision(rec, rstate, now, cadence, min_ahead):
    """Pure verdict for ONE armed session's release-gap state. `rec` is the
    persisted per-sid dict (or None/malformed for a fresh session). `rstate` is
    the fetched `{"ahead": int, "in_flight": bool, "train": bool}` (the #698
    `train` key is ignored by this decider), or None when UNDETERMINED (a
    gh/ssh error, or a repo with no integration branch) — None fails safe to
    `skip`.

    The gap is "real" when `ahead >= min_ahead`. Below that (incl. ahead 0) is a
    drained/absent gap -> `clear` (pop the rec). A real gap with `in_flight` True
    is `inflight` (reset first_seen=now, drop last_nudge -> once the release ends
    and if a gap persists, a fresh cadence grace applies before nudging). A real
    gap with `in_flight` False is a STALLED train: it nudges only when `now -
    (last_nudge or first_seen) >= cadence` — `first_seen` gives the initial grace
    (never nudge a gap that JUST appeared) and becomes the reping anchor via
    `last_nudge` afterwards. `last_nudge` is PRESERVED unchanged here (a "nudge"
    verdict is an INTENT; the caller sets last_nudge=now after a
    transcript-confirmed submit — so a swallowed send retries next sweep rather
    than skipping a whole cadence — OR, per #749, after MAX_SEND_FAILS consecutive
    UNDELIVERED sends (the bounded-retry back-off, so a persistently-swallowing
    pane is not re-typed every sweep forever). `first_seen` is seeded to `now` on first
    sight — so a long-pre-existing gap is first nudged one cadence after deploy,
    the safe cold-start."""
    if not isinstance(rstate, dict):
        return ("skip", rec, "undetermined")
    ahead = rstate.get("ahead")
    in_flight = rstate.get("in_flight")
    # `bool` is an `int` subclass — exclude it so a stray True never reads as a
    # 1-commit gap (review F10, unreachable from this fetch but closed anyway).
    if (not isinstance(ahead, int) or isinstance(ahead, bool)
            or not isinstance(in_flight, bool)):
        return ("skip", rec, "undetermined")
    if ahead < min_ahead:
        return ("clear", None, "no-gap")
    if in_flight:
        # The train is already moving -> reset the stall anchor, never nudge.
        return ("inflight", {"first_seen": now, "last_nudge": None,
                             "sig": _sig(rstate)}, "release-in-flight")
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)):
        last_nudge = None
    new_rec = {"first_seen": first_seen, "last_nudge": last_nudge,
               "sig": _sig(rstate)}
    anchor = last_nudge if last_nudge is not None else first_seen
    if now - anchor >= cadence:
        return ("nudge", new_rec, "due")
    return ("wait", new_rec, "grace")


def _nudge_text(ahead, integration, prod):
    """The release-gap keystroke injected into the armed loop. Carries the shared
    `stuck-check: ` prefix (own-payload recognition + machine-prompt exclusion —
    see the module docstring). Names the branches and the gap, and points at the
    project's own release pipeline (e.g. `/process-subdev`) WITHOUT hardcoding it
    as the only option — the job is generic over full-authority repos."""
    return (
        "stuck-check: release-gap — integračná vetva `%s` je %d commitov PRED "
        "`%s` (produkcia) a ŽIADNY release nie je in flight (žiaden otvorený "
        "`%s`→staging / staging→`%s` PR ani bežiaci deploy). Nenechaj zmergovanú "
        "prácu sedieť nevydanú: spusti release pipeline projektu (podľa doktríny "
        "projektu — napr. `/process-subdev`: integrácia→staging→main + deploy + "
        "verify), otvor/pokračuj release PR a dotiahni vlak do `%s`. Ak zámerne "
        "ešte batchuješ ďalšie tickety, potvrď to; inak vydaj TERAZ."
        % (integration, ahead, prod, integration, prod, prod))


# --- ORPHAN REAPER ---------------------------------------------------------

def _prune_release_gap_orphans(rrecs, visited_sids, now,
                               ttl_s=RELEASE_GAP_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["release_gap"]` (keyed on
    `sid = tpath.stem`). A rec is normally popped at episode end (no gap), but a
    session that DIES while a gap is tracked would leak its rec forever. Reap
    ONLY when BOTH: (1) the sid was NOT a live candidate pane THIS sweep
    (`visited_sids`), AND (2) it is malformed OR its `lts` (write-time age
    anchor) is older than `ttl_s`. The visited gate is PRIMARY (a live pane is
    never reaped regardless of `lts`). A FUTURE `lts` (clock skew) is kept (the
    safe direction, #519). Never raises. Faithful mirror of
    `ops_wait_recheck._prune_ops_wait_orphans`."""
    if not isinstance(rrecs, dict):
        return
    for sid in [k for k, v in list(rrecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        rrecs.pop(sid, None)


# --- ORCHESTRATOR ----------------------------------------------------------

def _book_unverified_send(rec, new_rec, loc, ahead, now):
    """#749 bounded retry (faithful mirror of `ops_wait_recheck._book_unverified_
    send`, #714): book ONE undelivered release-gap send. The consecutive-failure
    counter is READ from the OLD persisted `rec` (last sweep's value) and WRITTEN
    to `new_rec` (which `_release_decision` rebuilds fresh each sweep from
    first_seen/last_nudge/sig, so the counter must be carried across explicitly);
    `new_rec` IS `rrecs[sid]`, so the write persists. Under MAX_SEND_FAILS it
    increments and retries next sweep; at MAX_SEND_FAILS it BACKS OFF one full
    cadence (advance `last_nudge`, reset the counter) so a persistently-swallowing
    pane is not re-typed every ~60s sweep forever. The counter crosses the JSON
    persistence boundary, so a corrupt/legacy non-int reads as 0 and never raises.
    Returns the decision log line."""
    prior = rec.get("send_fails") if isinstance(rec, dict) else None
    fails = (prior if isinstance(prior, int) and not isinstance(prior, bool)
             else 0) + 1
    if fails >= MAX_SEND_FAILS:
        new_rec["last_nudge"] = now
        new_rec["send_fails"] = 0
        return ("release-gap %s -> submit-unverified x%d — backing off one "
                "cadence (bounded retry #749, ahead=%d)" % (loc, fails, ahead))
    new_rec["send_fails"] = fails
    return ("release-gap %s -> submit-unverified (attempt %d/%d, retry next "
            "sweep, ahead=%d)" % (loc, fails, MAX_SEND_FAILS, ahead))


def goal_release_gap_recheck(now, run, rrecs, sid, cwd, pid, tpath, loc,
                             dry_run, handled, release_state_fetch, state,
                             sleep_fn=None, cadence=None, min_ahead=None,
                             captured=None):
    """Audit ONE armed candidate pane's release-gap and, on cadence, deliver ONE
    verified nudge into that session. Called from `goal.goal_lane_sweep`'s
    existing armed-pane loop with the already-resolved pane context (ZERO new
    pane walk / capture). Mutates `rrecs[sid]` (persisted by the shared `state`);
    returns a list of decision log lines (#486 — every verdict logged, never a
    silent skip). `dry_run` mutates no persistent state and sends nothing.

    FULL-authority gate (the #618 MIRROR): a release train is run ONLY by the
    gatekeeper, so this proceeds only where `airuleset.resolve_authority(cwd) ==
    "full"`. Cheap, BEFORE any fetch. An unresolvable authority fails safe to
    skip (never a false nudge into a reduced-authority stream box).

    `release_state_fetch(cwd)` is the injected seam (network kept out of run_once
    unit tests, exactly like `ops_wait_fetch`): returns `{"ahead": int,
    "in_flight": bool, "train": bool}` (the #698 key is ignored here) or None
    when unmeasurable — None fails safe to `skip`. It
    is read through `_cached_release_state` (per-repo TTL cache) so the gh
    subprocess fires at most once per repo per TTL, never every sweep per pane.

    Keystroke coordination reuses the sibling machinery verbatim: `send_verified`
    (transcript-proof submit; a swallowed Enter is NOT booked delivered, its text
    restored — and #749 bounds the RETRY: after MAX_SEND_FAILS consecutive
    undelivered sends the nudge backs off one full cadence),
    `_janitor_mark_watch`/`_janitor_clear_watch`, and the per-sweep
    `handled` set (at most ONE keystroke per pane per sweep across the keystroke
    jobs — this job runs AFTER the lane nudge and the ops-wait recheck in the
    loop, so a pane those already typed is deferred to next sweep, and a nudge WE
    send claims the sid).

    `captured` (#749/#714): the pane capture the caller already read for the
    lane nudge (ZERO new capture) — the BUSY-PANE GATE. When it shows CC's
    "Waiting for N background agents to finish" state (`watchdog._BG_AGENTS_WAIT_
    RX`), the nudge is DEFERRED (no keystroke, `last_nudge` unadvanced, `handled`
    unclaimed) so it retries a later sweep: a submit into that transient mid-turn
    state is swallowed and parks the text orphaned in the input box. None
    (unwired / older caller) skips the gate — the send's own bare/collapsed
    checks still apply. Mirrors the sibling `ops_wait_recheck` in the SAME loop."""
    logs = []
    cadence = cadence or _cadence()
    min_ahead = _min_ahead() if min_ahead is None else min_ahead
    # FULL-authority gate (#618 MIRROR), cheap, before any fetch.
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception as e:
        logs.append("release-gap %s -> skip:authority-unresolved (%r)"
                    % (loc, e))
        return logs
    if authority != "full":
        logs.append("release-gap %s -> skip:not-full-authority (%s)"
                    % (loc, authority))
        return logs
    # CACHED per-repo: the fetch fires at most once per repo per TTL, never every
    # sweep per pane. A cache/fetch error reads as None -> skip.
    try:
        rstate = _cached_release_state(cwd, release_state_fetch, state, now)
    except Exception as e:
        logs.append("release-gap %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    rec = rrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason = _release_decision(rec, rstate, now, cadence,
                                                min_ahead)

    if action == "skip":
        logs.append("release-gap %s -> skip:%s (state unchanged)" % (loc, reason))
        return logs
    if action == "clear":
        if not dry_run:
            rrecs.pop(sid, None)
        logs.append("release-gap %s -> clear (%s not ahead of %s — drained)"
                    % (loc, _integration_branch(), _prod_branch()))
        return logs

    # action in ("wait", "inflight", "nudge"): persist the seeded/refreshed rec
    # (first_seen, sig, lts age-anchor for the reaper). last_nudge is only
    # advanced on a CONFIRMED send below.
    if not dry_run:
        new_rec["lts"] = now
        rrecs[sid] = new_rec

    ahead = rstate.get("ahead")
    sig = new_rec["sig"]
    if action == "inflight":
        # Surface the CUMULATIVE age the gap had been tracked BEFORE this
        # in-flight flap reset the anchor (#812 review, finding 2): a flapping
        # in-flight signal that resets `first_seen=now` faster than the cadence
        # starves the nudge forever, and the pre-reset age is the one signal that
        # turns such a starvation into a single journal grep instead of a day of
        # forensics. `rec` is the OLD persisted rec (pre-reset).
        prior = rec.get("first_seen") if isinstance(rec, dict) else None
        was = (" tracked %s pre-reset," % _fmt_age(now - prior)
               if isinstance(prior, (int, float)) else "")
        logs.append("release-gap %s -> skip:release-in-flight (ahead=%d,%s "
                    "anchor reset)" % (loc, ahead, was))
        return logs
    if action == "wait":
        anchor = new_rec["last_nudge"] or new_rec["first_seen"]
        logs.append("release-gap %s -> wait (ahead=%d, %s since anchor < "
                    "cadence)" % (loc, ahead, _fmt_age(now - anchor)))
        return logs

    # action == "nudge"
    # #780 WRITER-SIDE LATCH (#741): a pending /compact for this session HOLDS the
    # release-gap nudge — never push work into the armed loop while a
    # drained-boundary compact waits for its quiet window. Same shape as the
    # goal-family writers (goal.py:1792) and the busy-pane gate below: defer
    # WITHOUT a keystroke (last_nudge unadvanced, `handled` unclaimed) so it
    # retries a later sweep once the compact delivers. Lazy import — a defensive
    # choice (a top-level import is also fine, goal.py:173 does it), kept local to
    # avoid any dependence on the watchdog package-init ordering; fail-safe False on
    # any error (a blank sid / unreadable store -> writer proceeds as pre-#741).
    from watchdog import compact as _compact
    if _compact.pending_compact_hold(sid, now):   # #848 bounded
        logs.append("release-gap %s -> hold:compact-pending (pending /compact; "
                    "no nudge until it delivers)" % loc)
        return logs
    if handled is not None and sid in handled:
        logs.append("release-gap %s -> skip:already-handled (another sweep job "
                    "typed this pane; retry next sweep)" % loc)
        return logs
    # #749/#714 BUSY-PANE GATE: NEVER type into a pane showing CC's "Waiting for
    # N background agents to finish" state — the submit is swallowed and the text
    # parks ORPHANED in the input box (the head/tail verify then fails every
    # sweep). Defer WITHOUT a keystroke; the transient Waiting state clears between
    # turns and a later sweep delivers into the genuinely-idle `❯`. last_nudge
    # stays unadvanced, the pane is NOT claimed in `handled`, and `send_fails` is
    # NEITHER incremented NOR carried (this sweep booked no failure) — the fresh
    # `new_rec` simply omits it, so an alternating busy↔swallow pane restarts the
    # streak at 1, the accepted #714 residual (exact sibling parity). Inlined
    # rather than importing `ops_wait_recheck._pane_busy_waiting` (a rider→rider
    # private reach); both wrappers delegate to the SAME single-sourced signal
    # `watchdog._BG_AGENTS_WAIT_RX`, so a signal change still lands in one place.
    if captured and watchdog._BG_AGENTS_WAIT_RX.search(captured):
        logs.append("release-gap %s -> skip:busy-bg-agent (pane waiting on a "
                    "background agent — deferred, retry next sweep)" % loc)
        return logs
    # #797 SHARED CADENCE GATE (family spacing): a DIFFERENT gated-family category
    # nudged this session within NUDGE_FAMILY_GAP_S -> DEFER (no keystroke,
    # last_nudge unadvanced, `handled` unclaimed) so it retries a later sweep.
    # release-gap carries NO per-category floor (its own ~1h cadence governs), so
    # the gate is a pure family-spacing no-op except when a sibling fired recently.
    if not _nudge_gate.gate_ok(state, sid, "release-gap", now):
        logs.append("release-gap %s -> hold:cadence-gate (shared family gap; "
                    "retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("release-gap %s -> WOULD-NUDGE (ahead=%d, no release in "
                    "flight)" % (loc, ahead))
        return logs

    text = _nudge_text(ahead, _integration_branch(), _prod_branch())
    # Mark janitor provenance BEFORE the send (mirrors the sibling jobs): a
    # residual stuck send stays reclaimable, cleared only on a delivered submit.
    watchdog._janitor_mark_watch(state, pid, now)
    # #594: a DELIVERED submit (confirmed OR box-bare-unconfirmed) advances the
    # dedup; only a GENUINE swallow / abort retries next sweep.
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out)
    delivered = ok or bool(send_out.get("delivered_unconfirmed"))
    if not delivered:
        # #749 BOUNDED RETRY: a persistently-swallowing pane must not be re-typed
        # every sweep forever (`last_nudge` never advances on a failed send). Book
        # the failure; at MAX_SEND_FAILS back off one full cadence. `new_rec` IS
        # `rrecs[sid]` already (the persist step above ran — dry_run returned at
        # WOULD-NUDGE, never reaches here), and `_book_unverified_send` mutates it
        # in place, so the count/back-off persists with no re-assign (sibling
        # parity: ops_wait_recheck's not-delivered path re-assigns nothing either).
        logs.append(_book_unverified_send(rec, new_rec, loc, ahead, now))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    new_rec["send_fails"] = 0   # #749: a delivered send clears the failure streak
    rrecs[sid] = new_rec
    _nudge_gate.mark_sent(state, sid, "release-gap", now)   # #797
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("release-gap nudge %s -> ahead=%d %s (tracked %s)%s"
                % (loc, ahead, sig, _fmt_age(now - new_rec["first_seen"]), note))
    return logs
