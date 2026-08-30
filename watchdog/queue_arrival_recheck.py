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
naming the new arrival(s), and the baseline is advanced to `cur` only on a
CONFIRMED delivery (a swallowed submit re-detects the same arrival and retries,
bounded to MAX_SEND_FAILS then backs off). A member LEAVING / an unchanged
snapshot silently advances the baseline — no nudge. So it fires ONCE per distinct
arrival wave — the fast wake the incident needed — while the persistent-
unprocessed-queue case stays covered by jobs 8/11.

FULL-authority gate (the #618/#616 MIRROR): only a gk/full box PROCESSES this
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

CADENCE: the FETCH is cached per repo for QUEUE_ARRIVAL_FETCH_TTL_S (~5 min,
env-tunable, floored) — the arrival-detection latency. Bounded to at most 3 gh
calls per repo per TTL (the ticket's proven 3-label union), never every sweep per
pane.
"""
import os

import watchdog

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


def _pane_busy_waiting(captured):
    """True iff the pane shows CC's "Waiting for N background agents to finish"
    spinner (#714): the supervisor turn has ENDED and is blocked waiting for a
    background worker before re-invocation, so a submitted Enter is swallowed and
    the nudge parks orphaned. Reuses `watchdog._BG_AGENTS_WAIT_RX` — the SAME
    signal `ops_wait_recheck._pane_busy_waiting` gates on, NARROWED to the Waiting
    line only (NOT the agent-strip `◯` worker rows an armed loop always carries).
    Fail-safe False on empty/None."""
    return bool(captured) and bool(watchdog._BG_AGENTS_WAIT_RX.search(captured))


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


def _cached_queue(cwd, fetch, state, now, ttl=None, fail_ttl=None):
    """A per-cwd TTL cache over the queue-union `fetch` — the faithful sibling of
    `ops_wait_recheck._cached_member_fetch` / `release_gap._cached_release_state`.
    Without it the fetch would spawn its 3-label gh union EVERY 60s sweep for
    EVERY armed pane on the 120s-budgeted sweep's critical path. Bounds it to at
    most one union per repo per TTL, shared across every armed pane there.

    `fetch is None` (not wired) -> None, no cache write (the "wired = on"
    convention). A fetch exception -> None. A `ts` crossing the JSON persistence
    boundary is type-checked (a malformed/legacy entry reads as EXPIRED, never
    raises). None (unmeasurable) is cached only for `fail_ttl` so a transient gh
    hiccup re-checks soon. Returns a `list` (of ints) or None — never a guessed
    []."""
    if fetch is None:
        return None
    ttl = _fetch_ttl() if ttl is None else ttl
    fail_ttl = QUEUE_ARRIVAL_FETCH_FAIL_TTL_S if fail_ttl is None else fail_ttl
    cache = state.setdefault("queue_arrival_cache", {})
    entry = cache.get(cwd)
    if isinstance(entry, dict):
        try:
            age = now - float(entry.get("ts", 0))
        except (TypeError, ValueError):
            age = None
        if age is not None:
            members = entry.get("members")
            entry_ttl = ttl if isinstance(members, list) else fail_ttl
            if age < entry_ttl:
                return members if isinstance(members, list) else None
    try:
        members = fetch(cwd)
    except Exception:
        members = None
    if not (members is None or isinstance(members, list)):
        members = None
    cache[cwd] = {"ts": now, "members": members}
    return members


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state: {"base": [ints], "first_seen": ts, ...}) + cur
# (the current queue-union list, or None) -> (action, new_rec, reason, arrivals):
#   "skip"  -- cur is None / not a list (undetermined) -> NEVER a nudge, NEVER a
#              state change (safe direction);
#   "seed"  -- FIRST observation (no prior baseline) -> record base=cur, no nudge
#              (we don't know what was already parked before we started watching);
#   "track" -- baseline exists, no NEW member (unchanged, or a member resolved)
#              -> advance base=cur, no nudge;
#   "nudge" -- baseline exists and `cur - base` is non-empty -> the caller
#              ATTEMPTS a verified send and advances base=cur only on a CONFIRMED
#              submit (so a swallow re-detects the same arrival). `arrivals` is
#              the sorted list of NEW numbers.

def _queue_decision(rec, cur, now):
    """Pure verdict for ONE armed session's gk-queue snapshot. `rec` is the
    persisted per-sid dict (or None/malformed for a fresh session). `cur` is the
    fetched queue-union list, or None when UNDETERMINED (a gh error) — None fails
    safe to `skip`.

    The baseline (`rec["base"]`) is the set of queue members this session has
    already been told about. A NEW member (`cur - base`) is an arrival the parked
    session is blind to -> `nudge`. The decider NEVER advances the baseline for a
    `nudge` (it keeps the OLD base in `new_rec`), so a swallowed send re-detects
    the same arrival next sweep; the orchestrator promotes base=cur only on a
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
    base = rec.get("base") if isinstance(rec, dict) else None
    if not isinstance(base, list):
        # First observation: seed the baseline, never nudge (we can't tell a
        # pre-existing member from a genuine arrival).
        return ("seed", {"base": sorted(cur_set), "first_seen": now},
                "first-seen", [])
    base_set = set(base)
    arrivals = sorted(cur_set - base_set)
    if not arrivals:
        reason = "no-arrival" if cur_set == base_set else "resolved"
        return ("track", {"base": sorted(cur_set), "first_seen": first_seen},
                reason, [])
    # A genuine arrival. Keep base OLD so a swallowed send retries; the caller
    # promotes base=cur on a confirmed delivery.
    return ("nudge", {"base": sorted(base_set), "first_seen": first_seen},
            "arrival", arrivals)


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

    FULL-authority gate (the #616 MIRROR): only a gk/full box PROCESSES this
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

    Keystroke coordination reuses the sibling machinery verbatim: `send_verified`
    (transcript-proof submit; a swallowed Enter is NOT booked, its text restored),
    `_janitor_mark_watch`/`_janitor_clear_watch`, and the per-sweep `handled` set
    (at most ONE keystroke per pane per sweep — this job runs AFTER the lane
    nudge / ops-wait / release-gap riders in the loop, so a pane those already
    typed is deferred to next sweep, and a nudge WE send claims the sid)."""
    logs = []
    # FULL-authority gate (#616 MIRROR), cheap, before any fetch.
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
    action, new_rec, reason, arrivals = _queue_decision(rec, cur, now)

    if action == "skip":
        logs.append("queue-arrival %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action in ("seed", "track"):
        if not dry_run:
            new_rec["lts"] = now
            # carry any pending send_fails forward is unnecessary here — a
            # seed/track means the wave (if any) is resolved/baseline-known.
            qrecs[sid] = new_rec
        logs.append("queue-arrival %s -> %s (%s — %d in union, baseline %s)"
                    % (loc, action, reason, len(cur),
                       "seeded" if action == "seed" else "advanced"))
        return logs

    # action == "nudge": persist the seeded/refreshed rec (base OLD, first_seen,
    # lts age-anchor). base is advanced to cur only on a CONFIRMED send below.
    cur_sorted = sorted({int(x) for x in cur})
    if not dry_run:
        new_rec["lts"] = now
        qrecs[sid] = new_rec

    if handled is not None and sid in handled:
        logs.append("queue-arrival %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep, %d new)"
                    % (loc, len(arrivals)))
        return logs
    # #714 BUSY-PANE GATE: NEVER type into a pane showing CC's "Waiting for N
    # background agents to finish" state — the submit is swallowed and parks
    # orphaned. Defer WITHOUT a keystroke (no send_fails increment, base
    # unadvanced); the transient state clears and a later sweep delivers.
    if _pane_busy_waiting(captured):
        logs.append("queue-arrival %s -> skip:busy-bg-agent (pane waiting on a "
                    "background agent — deferred, retry next sweep, %d new)"
                    % (loc, len(arrivals)))
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
    new_rec["send_fails"] = 0
    qrecs[sid] = new_rec
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("queue-arrival nudge %s -> %d new (%s), baseline advanced to %d%s"
                % (loc, len(arrivals), _fmt_arrivals(arrivals), len(cur_sorted),
                   note))
    return logs
