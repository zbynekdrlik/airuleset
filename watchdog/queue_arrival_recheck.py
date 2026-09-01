"""gk queue-ARRIVAL nudge (#733) — wake an armed FULL-authority `/goal`
supervisor that is parked on a long background waiter the moment a NEW hand-off
lands in the gk queue.

INCIDENT (odoo-erp gk box, 2026-08-26 evening): the gk autopilot session waited
on a release tail (a `run_in_background` shadow-CI waiter + a slovnormal
write-lock waiter). Its MAIN turn had ENDED, so the pane sat at an idle `❯` with
a live-shell footer badge. Meanwhile THREE new items arrived in the gk queue
(READY-FOR-REVIEW #5177 20:51, GATEKEEPER-ACTION #5310 21:05, READY-FOR-REVIEW
#3073 22:00) — and the session was blind to all three until the owner asked by
hand TWICE. Third recurrence of the class (miva 2026-08-15; #4233 Brevo
2026-08-16).

ROOT CAUSE (traced in `watchdog/__init__.py` run_once()'s 36-job docstring +
bodies): NO watchdog path gives an armed-but-WAITING session a fast,
arrival-triggered, in-session wake on a new gk hand-off.
  * Jobs 8/11 (bounce / gk-request backstop) nudge the repo's IDLE pane on the
    PRESENCE of a queue member, at a ~30-min cadence with a slow staged re-ping
    (24h/3d/7d for materially-unchanged state) — never a fast arrival trigger.
  * Job 11's stale-handoff alarm fires only after 6h+ untouched, and is a
    Discord ping, never an in-session keystroke.
  * Job 20's three riders (`goal_lane_occupancy_nudge` lane occupancy,
    `ops_wait_recheck` ~daily partition audit, `release_gap` ~6h release train)
    are the ONLY family that keystrokes into an armed `/goal` pane, but NONE
    reads the gk queue union for an arrival delta.
  * The "arm a standing queue-watcher when you arm a waiter" doctrine is
    prose-only (root cause 3) — nothing mechanical enforces it.

WHAT THIS DOES: a 4th rider on `goal_lane_sweep`'s EXISTING armed-candidate-pane
loop (ZERO new pane walk / capture), the faithful sibling of #547/#578 (ops-wait)
and #616 (release-gap). Per repo it snapshots the gk queue UNION
`ready-for-review ∪ needs-gatekeeper ∪ prio:bounce` (open issue numbers). The
signal is a SET DELTA, not presence or cadence: the FIRST observation seeds a
baseline (no nudge — we don't know what was already there); a LATER snapshot that
ADDS a member (`cur − base ≠ ∅`) delivers ONE verified `stuck-check:` nudge
naming the new arrival(s) — SUBJECT TO the per-sid NUDGE FLOOR (#780, see CADENCE
below): a delta inside the floor window is HELD (no keystroke) and its members
ACCUMULATE into the next post-floor nudge, so multiple waves within one window
fold into a single nudge naming all of them. The baseline is advanced to `cur`
(and last_nudge to `now`) only on a CONFIRMED delivery (a swallowed submit
re-detects the same arrival and retries, bounded to MAX_SEND_FAILS then backs
off). A member LEAVING / an unchanged snapshot silently advances the baseline — no
nudge. So it fires at most ONCE per floor window, naming every wave accumulated in
it — the fast wake the incident needed (the FIRST arrival after a seed fires at
once), rate-limited — while the persistent-unprocessed-queue case stays covered by
jobs 8/11.

FULL-authority gate (full-only, the SAME gate as `release_gap` (#616); the
INVERSE of #618's WIDENED lane gate): only a gk/full box PROCESSES this
cross-stream union; a reduced-authority stream HANDS OFF to gk and its own
returned `prio:bounce` is already job-8's concern. Cheap, before any fetch. An
unresolvable authority fails safe to skip (never a false nudge).

DESIGN (#486 reuse, ZERO new delivery/fetch/keystroke primitives): reuses
`watchdog.send_verified` (transcript-proof submit, with the #594
delivered-unconfirmed `out`), `_pane_busy_waiting` (#714 — never submit into CC's
"Waiting for N background agents" transient), `_janitor_mark_watch`/
`_janitor_clear_watch`, the shared `stuck-check: ` own-payload prefix (already in
`_JANITOR_OWN_PREFIXES` + `_MACHINE_PROMPT_PREFIXES`, so a swallowed nudge is
reclaimable AND never mistaken for a human answer), the per-sweep `handled` set
(at most ONE keystroke per pane per sweep across the keystroke jobs), a per-repo
TTL cache (the `_cached_member_fetch` shape) and the `_book_unverified_send`
bounded-retry + orphan-reaper shapes. The verdict logic is a PURE
`_queue_decision`; all I/O lives in `goal_queue_arrival_recheck` behind the same
injectable seams the sibling jobs use, and `dry_run` mutates nothing.

CADENCE (#780): arrival DETECTION is bounded by the FETCH cache TTL
(QUEUE_ARRIVAL_FETCH_TTL_S, ~5 min, env-tunable, floored at 60s), but the NUDGE
KEYSTROKE now also carries a per-sid min-interval FLOOR (QUEUE_ARRIVAL_NUDGE_FLOOR_S,
~30 min, env-tunable, floored at 5 min) — the sibling `_cadence` shape (ops_wait /
release_gap). Originally this rider had NO floor: during an active gk batch every
landing hand-off is a fresh set-delta, so "once per arrival wave" degenerated into
a re-fire nearly every TTL for hours (measured 8 nudges in 2h on gk). The floor
rate-limits DELIVERY while KEEPING the event-driven trigger: a delta inside the
floor window is HELD and its new members ACCUMULATE into the next post-floor nudge
(which names ALL of them), and the FIRST arrival after a seed (last_nudge unset)
still fires at once (the fast-wake the incident needed). Bounded to at most 3 gh
calls per repo per TTL (the ticket's proven 3-label union), never every sweep per
pane. Residual: a queue label FLAPPING within one floor window is folded into the
single post-floor nudge (an improvement over the pre-#780 one-nudge-per-flap).
"""
import os

import watchdog
from watchdog import ops_wait_recheck as _ops_wait_recheck
from watchdog import nudge_gate as _nudge_gate   # #797 shared cadence gate

# env AIRULESET_QUEUE_ARRIVAL_FETCH_TTL_S — how long a queue-union snapshot is
# CACHED per repo (`state["queue_arrival_cache"]`, keyed by cwd). ~5 min: the
# ticket's own proven watcher used a 300s loop, and it doubles as the arrival-
# detection latency. Floored so an env units error can't collapse it to a
# per-sweep gh call.
QUEUE_ARRIVAL_FETCH_TTL_S = 5 * 60
QUEUE_ARRIVAL_FETCH_TTL_MIN_S = 60
# a FAILED/unmeasurable fetch (None) is cached only briefly so a transient gh
# hiccup re-checks soon rather than suppressing arrival detection for a whole TTL.
QUEUE_ARRIVAL_FETCH_FAIL_TTL_S = 60
# orphan-reaper TTL for a per-sid rec whose session is gone (mirror of
# release_gap.RELEASE_GAP_ORPHAN_TTL_S): the `visited_sids` gate is PRIMARY (a
# live pane is never reaped), this is the SECONDARY safety for a budget-deferred
# pane.
QUEUE_ARRIVAL_ORPHAN_TTL_S = 24 * 3600
# the nudge is a compact TRIGGER (#714 lesson: a multi-KB wall collapses into a
# `[Pasted text]` placeholder the janitor cannot reclaim). Hard-capped.
NUDGE_MAX_CHARS = 700
# how many arrival numbers to name explicitly before summarizing "+K ďalších"
# (a huge wave must not blow the char cap or bury the signal).
MAX_NAMED_ARRIVALS = 12
# bounded retry (#714): a persistently-swallowing NON-busy pane backs off (accept
# the wave, advance the baseline) after this many consecutive unverified submits,
# instead of typing every 60s sweep forever.
MAX_SEND_FAILS = 3

# #780 — a per-sid MIN-INTERVAL floor between DELIVERED nudges (env
# AIRULESET_QUEUE_ARRIVAL_NUDGE_FLOOR_S). The sibling riders have one
# (ops_wait_recheck ~22h/6h, release_gap ~6h/2h); this rider originally had NONE
# — its rate was bounded only by the FETCH TTL (~5 min), so during an active gk
# batch EVERY landing hand-off was a fresh set-delta = a re-fire nearly every TTL
# window (measured 8 nudges in 2h on gk). The floor rate-limits the KEYSTROKE,
# NOT arrival DETECTION: a delta inside the floor window is HELD and its new
# members ACCUMULATE into the next post-floor nudge (which names ALL of them), so
# the nudge stays delta-triggered — just bounded. 30 min: cuts the batch-window
# storm to ~2/h while still surfacing every distinct wave within a floor. The
# floor applies only to the 2nd+ nudge — the FIRST arrival after a seed
# (last_nudge unset) still fires at once, preserving the #733 fast-wake.
QUEUE_ARRIVAL_NUDGE_FLOOR_S = 30 * 60
# floor for the env override (#504/#543 floor-clamp lesson the siblings follow): a
# sub-5-min value would collapse the floor back toward a per-fetch re-nudge, so a
# units error can never re-open the storm this fixes.
QUEUE_ARRIVAL_NUDGE_FLOOR_MIN_S = 5 * 60


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _fetch_ttl():
    """The cache TTL for a real queue-union read, floored so an env units error
    can't collapse it back to a per-sweep fetch."""
    return max(_env_int("AIRULESET_QUEUE_ARRIVAL_FETCH_TTL_S",
                        QUEUE_ARRIVAL_FETCH_TTL_S), QUEUE_ARRIVAL_FETCH_TTL_MIN_S)


def _nudge_floor():
    """#780 — the per-sid min interval between DELIVERED queue-arrival nudges, the
    env override floored at QUEUE_ARRIVAL_NUDGE_FLOOR_MIN_S so a units error can't
    collapse it back to a per-fetch re-nudge (the #504/#543 floor-clamp lesson the
    sibling riders' `_cadence` follow)."""
    return max(_env_int("AIRULESET_QUEUE_ARRIVAL_NUDGE_FLOOR_S",
                        QUEUE_ARRIVAL_NUDGE_FLOOR_S),
               QUEUE_ARRIVAL_NUDGE_FLOOR_MIN_S)


def _cached_queue(cwd, fetch, state, now, ttl=None, fail_ttl=None):
    """A per-cwd TTL cache over the queue-union `fetch` (a `list` of ints or
    None). Without it the fetch would spawn its 3-label gh union EVERY 60s sweep
    for EVERY armed pane on the 120s-budgeted sweep's critical path — this bounds
    it to at most one union per repo per TTL, shared across every armed pane.

    REUSES `ops_wait_recheck._cached_member_fetch` (#486 net-LOC-down — that
    helper is `cache_key`-parameterized precisely so ONE implementation serves
    every list-shaped fetch consumer), with this module's OWN cache namespace +
    ttl/fail_ttl. All its guarantees carry: `fetch is None` -> None with no cache
    write, a fetch exception / non-list return -> None, a malformed `ts` reads as
    expired (never raises), None cached only for `fail_ttl`."""
    return _ops_wait_recheck._cached_member_fetch(
        cwd, fetch, state, now, "queue_arrival_cache",
        _fetch_ttl() if ttl is None else ttl,
        QUEUE_ARRIVAL_FETCH_FAIL_TTL_S if fail_ttl is None else fail_ttl)


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state: {"base": [ints], "first_seen": ts, ...}) + cur
# (the current queue-union list, or None) -> (action, new_rec, reason, arrivals):
#   "skip"  -- cur is None / not a list (undetermined) -> NEVER a nudge, NEVER a
#              state change (safe direction);
#   "seed"  -- FIRST observation (no prior baseline) -> record base=cur, no nudge
#              (we don't know what was already parked before we started watching);
#   "track" -- baseline exists, no NEW member (unchanged, or a member resolved)
#              -> advance base=cur, no nudge;
#   "hold"  -- #780: baseline exists, `cur - base` is non-empty, BUT a delivered
#              nudge is still inside the per-sid FLOOR window (last_nudge set and
#              now - last_nudge < floor) -> keep base OLD (so the new members
#              ACCUMULATE into the next post-floor nudge) and DO NOT nudge;
#   "nudge" -- baseline exists, `cur - base` is non-empty, AND either no delivered
#              nudge yet (last_nudge unset -> the #733 fast-wake) or the floor has
#              elapsed -> the caller ATTEMPTS a verified send and advances base=cur
#              + last_nudge=now only on a CONFIRMED submit (so a swallow re-detects
#              the same arrival). `arrivals` is the sorted list of NEW numbers.

def _queue_decision(rec, cur, now, floor=0):
    """Pure verdict for ONE armed session's gk-queue snapshot. `rec` is the
    persisted per-sid dict (or None/malformed for a fresh session). `cur` is the
    fetched queue-union list, or None when UNDETERMINED (a gh error) — None fails
    safe to `skip`. `floor` (#780) is the per-sid min interval between DELIVERED
    nudges; the default 0 is the pre-#780 behavior (a delta always nudges), which
    keeps the legacy 3-arg callers/tests unchanged.

    The baseline (`rec["base"]`) is the set of queue members this session has
    already been told about. A NEW member (`cur - base`) is an arrival the parked
    session is blind to. It fires a `nudge` UNLESS a delivered nudge is still
    inside the floor window (`last_nudge` set and `now - last_nudge < floor`), in
    which case it is HELD (`hold`) — the decider keeps the OLD base for BOTH
    `nudge` and `hold`, so a swallowed send re-detects the arrival AND (the #780
    accumulation) members arriving during the floor keep growing `cur - base`, so
    the next post-floor nudge names ALL of them. `last_nudge` is preserved here
    (an INTENT); the orchestrator sets base=cur AND last_nudge=now only on a
    confirmed delivery. `first_seen` is preserved across the session's life (an
    observability anchor, not a cadence gate — arrivals are event-driven)."""
    if not isinstance(cur, list):
        return ("skip", rec, "undetermined", [])
    try:
        cur_set = {int(x) for x in cur}
    except (TypeError, ValueError):
        return ("skip", rec, "undetermined", [])
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)) or isinstance(first_seen, bool):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)) or isinstance(last_nudge, bool):
        last_nudge = None
    base = rec.get("base") if isinstance(rec, dict) else None
    if not isinstance(base, list):
        # First observation: seed the baseline, never nudge (we can't tell a
        # pre-existing member from a genuine arrival).
        return ("seed",
                {"base": sorted(cur_set), "first_seen": now, "last_nudge": None},
                "first-seen", [])
    base_set = set(base)
    arrivals = sorted(cur_set - base_set)
    if not arrivals:
        reason = "no-arrival" if cur_set == base_set else "resolved"
        return ("track",
                {"base": sorted(cur_set), "first_seen": first_seen,
                 "last_nudge": last_nudge},
                reason, [])
    # A genuine arrival. Keep base OLD so a swallowed send retries AND so members
    # arriving during a floor window ACCUMULATE; the caller promotes base=cur (and
    # last_nudge=now) on a confirmed delivery.
    new_rec = {"base": sorted(base_set), "first_seen": first_seen,
               "last_nudge": last_nudge}
    # #780 FLOOR: a delivered nudge still inside the floor window -> HOLD the
    # keystroke (the new members join the next post-floor nudge). The floor gates
    # only the 2nd+ nudge: last_nudge is None until the first delivered nudge, so
    # the first arrival after a seed still fires at once (the #733 fast-wake).
    if last_nudge is not None and (now - last_nudge) < floor:
        return ("hold", new_rec, "floor", arrivals)
    return ("nudge", new_rec, "arrival", arrivals)


def _fmt_arrivals(arrivals):
    """`#5177 #5310 #3073 (+2 ďalších)` — names up to MAX_NAMED_ARRIVALS, then
    summarizes the rest so a huge wave never blows the char cap."""
    named = arrivals[:MAX_NAMED_ARRIVALS]
    txt = " ".join("#%d" % n for n in named)
    extra = len(arrivals) - len(named)
    if extra > 0:
        txt += " (+%d ďalších)" % extra
    return txt


def _nudge_text(arrivals, cur_count):
    """The queue-arrival keystroke injected into the armed loop. Carries the
    shared `stuck-check: ` prefix (own-payload recognition + machine-prompt
    exclusion — see the module docstring). Names the NEW arrivals and points at
    the session's own gk backlog re-derivation, without hardcoding one repo's
    pipeline (generic over full-authority repos). Hard-capped at NUDGE_MAX_CHARS
    (a genuine over-cap only from a pathological wave -> truncate on a word
    boundary)."""
    text = (
        "stuck-check: gk queue arrival — do fronty hand-offov pribudli NOVÉ "
        "tickety %s (union ready-for-review ∪ needs-gatekeeper ∪ prio:bounce, "
        "spolu %d otvorených), kým si čakal na dlhý background waiter. Session "
        "čakajúca na waiter je slepá na nové hand-offy — re-deriv svoj gk "
        "backlog (core-quals --count / tvoj /goal stop-proof) a spracuj nové "
        "tickety: reviewni ready-for-review, konaj needs-gatekeeper, vezmi späť "
        "prio:bounce. Ak už na nich robíš, potvrď."
        % (_fmt_arrivals(arrivals), cur_count))
    if len(text) <= NUDGE_MAX_CHARS:
        return text
    return text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


# --- BOUNDED RETRY ---------------------------------------------------------

def _book_unverified_send(rec, new_rec, cur_sorted, loc, arrivals_n):
    """#714 bounded retry: book ONE undelivered send onto the persisted rec
    (`new_rec` IS `qrecs[sid]`, so mutation persists). Under MAX_SEND_FAILS it
    increments the consecutive-failure counter and retries next sweep (base
    unadvanced -> the SAME arrival is re-detected); at MAX_SEND_FAILS it BACKS
    OFF by ACCEPTING the wave (advance base=cur, reset the counter) so a
    persistently-swallowing NON-busy pane is not typed into every 60s sweep
    forever — the persistent-queue case stays jobs-8/11's. The counter crosses
    the JSON persistence boundary, so a corrupt/legacy non-int reads as 0 and
    never raises. Returns the decision log line."""
    prior = rec.get("send_fails") if isinstance(rec, dict) else None
    fails = (prior if isinstance(prior, int) and not isinstance(prior, bool)
             else 0) + 1
    if fails >= MAX_SEND_FAILS:
        new_rec["base"] = cur_sorted
        new_rec["send_fails"] = 0
        return ("queue-arrival %s -> submit-unverified x%d — backing off, "
                "accepting the %d-arrival wave (bounded retry #714)"
                % (loc, fails, arrivals_n))
    new_rec["send_fails"] = fails
    return ("queue-arrival %s -> submit-unverified (attempt %d/%d, retry next "
            "sweep, %d new)" % (loc, fails, MAX_SEND_FAILS, arrivals_n))


# --- ORPHAN REAPER ---------------------------------------------------------

def _prune_queue_arrival_orphans(qrecs, visited_sids, now,
                                 ttl_s=QUEUE_ARRIVAL_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["queue_arrival"]` (keyed on
    `sid = tpath.stem`). A rec normally lives for the session, so a session that
    DIES would leak its rec forever. Reap ONLY when BOTH: (1) the sid was NOT a
    live candidate pane THIS sweep (`visited_sids`), AND (2) it is malformed OR
    its `lts` (write-time age anchor) is older than `ttl_s`. The visited gate is
    PRIMARY (a live pane is never reaped regardless of `lts`). A FUTURE `lts`
    (clock skew) is kept (the safe direction, #519). Never raises. Faithful
    mirror of `release_gap._prune_release_gap_orphans`."""
    if not isinstance(qrecs, dict):
        return
    for sid in [k for k, v in list(qrecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        qrecs.pop(sid, None)


# --- ORCHESTRATOR ----------------------------------------------------------

def goal_queue_arrival_recheck(now, run, qrecs, sid, cwd, pid, tpath, loc,
                               dry_run, handled, queue_fetch, state,
                               sleep_fn=None, captured=None):
    """Audit ONE armed candidate pane's gk-queue snapshot and, on a NEW arrival,
    deliver ONE verified nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `qrecs[sid]` (persisted
    by the shared `state`); returns a list of decision log lines (#486 — every
    verdict logged, never a silent skip). `dry_run` mutates no persistent state
    and sends nothing.

    FULL-authority gate (full-only, the SAME gate as `release_gap` (#616); the
    INVERSE of #618's widened lane gate): only a gk/full box PROCESSES this
    cross-stream union. Cheap, BEFORE any fetch. An unresolvable authority fails
    safe to skip (never a false nudge into a reduced-authority stream box).

    `queue_fetch(cwd)` is the injected seam (network kept out of run_once unit
    tests, exactly like `ops_wait_fetch`): returns the queue-union member numbers
    (a `list` of ints) or None when unmeasurable — None fails safe to `skip`. It
    is read through `_cached_queue` (per-repo TTL cache) so the gh union fires at
    most once per repo per TTL, never every sweep per pane.

    `captured` (#714): the pane capture the caller already read for the lane
    nudge (ZERO new capture) — the BUSY-PANE GATE. When it shows CC's "Waiting
    for N background agents to finish" state (`_pane_busy_waiting`), the nudge is
    DEFERRED (no keystroke, base unadvanced, `handled` unclaimed) so it retries a
    later sweep. None (unwired / older caller) skips the gate.

    Before any keystroke the nudge branch consults the #741 compact latch
    (`compact.has_pending_request(sid)`, #780) — the FIRST defer-gate: a pending
    /compact HOLDS the nudge (`hold:compact-pending`, no keystroke, base/last_nudge
    unadvanced) so a drained-boundary compact delivers in a quiet pane before any
    new hand-off is pushed in. Keystroke coordination then reuses the sibling
    machinery verbatim: `send_verified` (transcript-proof submit; a swallowed Enter
    is NOT booked, its text restored), `_janitor_mark_watch`/`_janitor_clear_watch`,
    and the per-sweep `handled` set (at most ONE keystroke per pane per sweep —
    this job runs AFTER the lane nudge / ops-wait / release-gap riders in the loop,
    so a pane those already typed is deferred to next sweep, and a nudge WE send
    claims the sid)."""
    logs = []
    # FULL-authority gate (full-only, same gate as release_gap #616; the INVERSE
    # of #618's widened lane gate), cheap, before any fetch.
    try:
        import airuleset
        authority = airuleset.resolve_authority(cwd)
    except Exception as e:
        logs.append("queue-arrival %s -> skip:authority-unresolved (%r)"
                    % (loc, e))
        return logs
    if authority != "full":
        logs.append("queue-arrival %s -> skip:not-full-authority (%s)"
                    % (loc, authority))
        return logs
    # CACHED per-repo: the union fires at most once per repo per TTL. A
    # cache/fetch error reads as None -> skip.
    try:
        cur = _cached_queue(cwd, queue_fetch, state, now)
    except Exception as e:
        logs.append("queue-arrival %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    rec = qrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason, arrivals = _queue_decision(rec, cur, now,
                                                        _nudge_floor())

    if action == "skip":
        logs.append("queue-arrival %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action in ("seed", "track", "hold"):
        if not dry_run:
            new_rec["lts"] = now
            # carry any pending send_fails forward is unnecessary here — a
            # seed/track means the wave (if any) is resolved/baseline-known, and
            # a #780 `hold` follows a delivered nudge (send_fails already 0).
            qrecs[sid] = new_rec
        if action == "hold":
            # #780 FLOOR: a delivered nudge is still inside the floor window, so
            # the new arrivals are HELD (base kept OLD) and ACCUMULATE into the
            # next post-floor nudge — no keystroke this sweep.
            logs.append("queue-arrival %s -> hold:floor (%d new accumulating; "
                        "floor not elapsed, %d in union)"
                        % (loc, len(arrivals), len(cur)))
        else:
            logs.append("queue-arrival %s -> %s (%s — %d in union, baseline %s)"
                        % (loc, action, reason, len(cur),
                           "seeded" if action == "seed" else "advanced"))
        return logs

    # action == "nudge": persist the seeded/refreshed rec (base OLD, first_seen,
    # lts age-anchor). base is advanced to cur only on a CONFIRMED send below.
    cur_sorted = sorted({int(x) for x in cur})
    if not dry_run:
        new_rec["lts"] = now
        # Carry the consecutive-swallow counter forward (#733 review 🔵): this
        # persist runs BEFORE the handled/busy gates, so a deferral sweep between
        # two swallow sweeps must NOT silently reset it — else an alternating
        # busy/swallow pane never reaches MAX_SEND_FAILS. `_book_unverified_send`
        # reads the OLD rec, so this only preserves it across a deferral.
        prior_fails = rec.get("send_fails")
        if isinstance(prior_fails, int) and not isinstance(prior_fails, bool):
            new_rec["send_fails"] = prior_fails
        qrecs[sid] = new_rec

    # #780 WRITER-SIDE LATCH (#741): a pending /compact for this session HOLDS the
    # arrival nudge — never push a new hand-off into the armed loop while a
    # drained-boundary compact waits for its quiet window. Same shape as the
    # goal-family writers (goal.py:1792) and the busy-pane gate below: defer
    # WITHOUT a keystroke (base/last_nudge unadvanced, `handled` unclaimed) so it
    # retries a later sweep once the compact delivers. First delivery gate (the
    # strongest constraint). Lazy import — a defensive choice (a top-level import
    # is also fine, goal.py:173 does it), kept local to avoid any dependence on the
    # watchdog package-init ordering; fail-safe False on any error (writer proceeds
    # as pre-#741).
    from watchdog import compact as _compact
    if _compact.has_pending_request(sid):
        logs.append("queue-arrival %s -> hold:compact-pending (pending /compact; "
                    "no arrival nudge until it delivers, %d new)"
                    % (loc, len(arrivals)))
        return logs
    if handled is not None and sid in handled:
        logs.append("queue-arrival %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep, %d new)"
                    % (loc, len(arrivals)))
        return logs
    # #714 BUSY-PANE GATE: NEVER type into a pane showing CC's "Waiting for N
    # background agents to finish" state — the submit is swallowed and parks
    # orphaned. Defer WITHOUT a keystroke (no send_fails increment, base
    # unadvanced); the transient state clears and a later sweep delivers.
    if _ops_wait_recheck._pane_busy_waiting(captured):
        logs.append("queue-arrival %s -> skip:busy-bg-agent (pane waiting on a "
                    "background agent — deferred, retry next sweep, %d new)"
                    % (loc, len(arrivals)))
        return logs
    # #797 SHARED CADENCE GATE (family spacing): a DIFFERENT gated-family category
    # nudged this session within NUDGE_FAMILY_GAP_S -> DEFER (no keystroke, base &
    # last_nudge unadvanced so the arrival re-detects, `handled` unclaimed) so it
    # retries a later sweep. queue-arrival carries NO per-category floor (its own
    # #780 nudge floor governs), so the gate only spaces DISTINCT categories.
    if not _nudge_gate.gate_ok(state, sid, "queue-arrival", now):
        logs.append("queue-arrival %s -> hold:cadence-gate (shared family gap; "
                    "retry next sweep, %d new)" % (loc, len(arrivals)))
        return logs
    if dry_run:
        logs.append("queue-arrival %s -> WOULD-NUDGE (%d new: %s)"
                    % (loc, len(arrivals), _fmt_arrivals(arrivals)))
        return logs

    text = _nudge_text(arrivals, len(cur))
    # Mark janitor provenance BEFORE the send (mirrors the sibling jobs): a
    # residual stuck send stays reclaimable, cleared only on a delivered submit.
    watchdog._janitor_mark_watch(state, pid, now)
    # #594: a DELIVERED submit (confirmed OR box-bare-unconfirmed) advances the
    # baseline; only a GENUINE swallow / abort retries next sweep.
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out)
    delivered = ok or bool(send_out.get("delivered_unconfirmed"))
    if not delivered:
        # A genuine swallow leaves base unadvanced -> retries next sweep; bounded
        # so a persistently-swallowing NON-busy pane backs off after
        # MAX_SEND_FAILS (accept the wave). send_verified already backed our text
        # OUT of the box on a genuine swallow, so nothing parks; sid NOT claimed.
        logs.append(_book_unverified_send(rec, new_rec, cur_sorted, loc,
                                          len(arrivals)))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["base"] = cur_sorted
    new_rec["last_nudge"] = now   # #780 — start the floor window on a delivered nudge
    new_rec["send_fails"] = 0
    qrecs[sid] = new_rec
    _nudge_gate.mark_sent(state, sid, "queue-arrival", now)   # #797
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("queue-arrival nudge %s -> %d new (%s), baseline advanced to %d%s"
                % (loc, len(arrivals), _fmt_arrivals(arrivals), len(cur_sorted),
                   note))
    return logs
