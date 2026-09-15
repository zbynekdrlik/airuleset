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
    `ops_wait_recheck` ~daily partition audit, `release_gap` ~1h release train)
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
naming the new arrival(s) — SUBJECT TO the shared per-pane-per-KIND 60-min floor
(#1023, `nudge_gate.gate_ok(state, sid, "queue-arrival", now)`): a delta inside
the floor window is HELD (no keystroke, `hold:floor`) and its members ACCUMULATE
into the next post-floor nudge, so multiple waves within one window fold into a
single nudge naming all of them. The baseline is advanced to `cur` only on a
CONFIRMED delivery (a swallowed submit re-detects the same arrival and retries,
bounded to MAX_SEND_FAILS then backs off). A member LEAVING / an unchanged
snapshot silently advances the baseline — no nudge. So it fires at most ONCE per
floor window, naming every wave accumulated in it — the fast wake the incident
needed (the FIRST arrival after a seed fires at once), rate-limited — while the
persistent-unprocessed-queue case stays covered by jobs 8/11.

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

CADENCE (#1023): arrival DETECTION is bounded by the FETCH cache TTL
(QUEUE_ARRIVAL_FETCH_TTL_S, ~5 min, env-tunable, floored at 60s), but the NUDGE
KEYSTROKE is bounded by the SHARED per-pane-per-KIND 60-min floor in `nudge_gate`
(the #1023 owner rule "raz za hodinu"), consulted via `gate_ok` in the delivery
branch. The pre-#1023 per-JOB 30-min floor (`QUEUE_ARRIVAL_NUDGE_FLOOR_S`) is
DELETED — it sat below the owner's 1 h rule and is subsumed by the ONE shared
floor, so the rule lives in exactly one place. The floor rate-limits DELIVERY
while KEEPING the event-driven trigger: a delta inside the floor window is HELD
(`hold:floor`, base kept OLD) and its new members ACCUMULATE into the next
post-floor nudge (which names ALL of them), and the FIRST arrival after a seed
(no prior confirmed delivery) still fires at once (the fast-wake the incident
needed). Bounded to at most 3 gh calls per repo per TTL (the ticket's proven
3-label union), never every sweep per pane. Residual: a queue label FLAPPING
within one floor window is folded into the single post-floor nudge (an
improvement over the pre-#780 one-nudge-per-flap).
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

# #1023 — the per-JOB 30-min nudge floor (`QUEUE_ARRIVAL_NUDGE_FLOOR_S`) is
# DELETED: it sat BELOW the owner's 1 h rule and is subsumed by the ONE
# per-pane-per-KIND 60-min floor in `nudge_gate` (consulted via
# `gate_ok(state, sid, "queue-arrival", now)` below). Delta ACCUMULATION is
# preserved without a per-job floor: `_queue_decision` returns "nudge" with `base`
# kept OLD, and when `gate_ok` holds the keystroke the orchestrator returns before
# advancing `base` — so members arriving inside the floor window keep growing
# `cur - base` and the next post-floor nudge names ALL of them, exactly as before.


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


def _cached_queue(cwd, fetch, state, now, cache_key="queue_arrival_cache",
                  ttl=None, fail_ttl=None):
    """A per-cwd TTL cache over the queue `fetch` (a `list` — ints for the review
    union, or rich `{id,kind,num,...}` dict records for the infra queue — or
    None). Without it the fetch would spawn its gh union EVERY 60s sweep for
    EVERY armed pane on the 120s-budgeted sweep's critical path — this bounds it
    to at most one union per repo per TTL, shared across every armed pane.

    `cache_key` (#1029) selects the cache NAMESPACE so the review union
    (`queue_arrival_cache`, byte-identical default) and the infra queue
    (`queue_arrival_infra_cache`) never share a snapshot — even were their cwds
    to coincide, they fetch different things.

    REUSES `ops_wait_recheck._cached_member_fetch` (#486 net-LOC-down — that
    helper is `cache_key`-parameterized AND element-type-agnostic precisely so
    ONE implementation serves every list-shaped fetch consumer, ints or dicts),
    with this module's ttl/fail_ttl. All its guarantees carry: `fetch is None`
    -> None with no cache write, a fetch exception / non-list return -> None, a
    malformed `ts` reads as expired (never raises), None cached only for
    `fail_ttl`."""
    return _ops_wait_recheck._cached_member_fetch(
        cwd, fetch, state, now, cache_key,
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
#   "nudge" -- baseline exists, `cur - base` is non-empty -> the caller ATTEMPTS a
#              verified send (subject to the shared per-kind floor `gate_ok`) and
#              advances base=cur only on a CONFIRMED submit (so a swallow — or a
#              floor hold — re-detects the same arrival). `arrivals` is the sorted
#              list of NEW numbers.

def _queue_decision(rec, cur, now, classify_fn=None):
    """Pure verdict for ONE armed session's gk-queue snapshot. `rec` is the
    persisted per-sid dict (or None/malformed for a fresh session). `cur` is the
    fetched queue-union list, or None when UNDETERMINED (a gh error) — None fails
    safe to `skip`.

    The baseline (`rec["base"]`) is the set of queue members this session has
    already been told about. A NEW member (`cur - base`) is an arrival the parked
    session is blind to → a `nudge` verdict. #1023: the per-JOB 30-min floor is
    gone; the decider keeps the OLD base for a `nudge`, and the ORCHESTRATOR's
    shared per-kind `gate_ok` (60 min) holds the keystroke when the floor has not
    elapsed — so a swallowed send re-detects the arrival AND members arriving
    inside the floor window keep growing `cur - base`, so the next post-floor
    nudge names ALL of them (accumulation preserved without a per-job floor). The
    orchestrator sets base=cur only on a confirmed delivery. `first_seen` is
    preserved across the session's life (an observability anchor, not a cadence
    gate — arrivals are event-driven)."""
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
        return ("seed",
                {"base": sorted(cur_set), "first_seen": now},
                "first-seen", [])
    base_set = set(base)
    arrivals = sorted(cur_set - base_set)
    if not arrivals:
        reason = "no-arrival" if cur_set == base_set else "resolved"
        return ("track",
                {"base": sorted(cur_set), "first_seen": first_seen},
                reason, [])
    # A genuine arrival. Keep base OLD so a swallowed send / a floor hold retries
    # AND so members arriving during a floor window ACCUMULATE; the caller
    # promotes base=cur on a confirmed delivery.
    new_rec = {"base": sorted(base_set), "first_seen": first_seen}
    # #993 item 4: DEPENDENCY AWARE. `classify_fn(number)` returns
    # "dispatchable" | "dep-wait" (the class-based infra branch was removed in
    # round 2b — infra serialisation is ROUTING, not a live-lane gate). A dep-wait
    # arrival is NOT dispatchable — nudge ONLY for dispatchable arrivals. When
    # EVERY arrival is non-dispatchable → HOLD (keep base OLD so they re-detect
    # once their deps close), reason `dep-wait`. None = legacy (every arrival
    # dispatchable). A nudge names only the dispatchable arrivals; base still
    # advances to `cur` on delivery (the session is woken and its own /goal loop
    # picks up the held dep members when they become workable).
    if classify_fn is not None:
        classes = {a: classify_fn(a) for a in arrivals}
        dispatchable = [a for a in arrivals if classes.get(a) == "dispatchable"]
        if not dispatchable:
            return ("hold", new_rec, "dep-wait", arrivals)
        arrivals = dispatchable
    return ("nudge", new_rec, "arrival", arrivals)


def _advanced_base(old_base, cur_sorted, arrivals):
    """#993 review 3: the base to advance to on a CONFIRMED nudge — `(old_base ∩
    cur) ∪ nudged_arrivals`. When the wave was ALL dispatchable (no classify
    filter) this equals `cur` (legacy behaviour, byte-identical). For a MIXED
    wave it EXCLUDES the held (non-dispatchable) members so they RE-DETECT next
    sweep once they become dispatchable, instead of being silently baked in."""
    cur_set = {int(x) for x in cur_sorted}
    keep = {int(x) for x in old_base} & cur_set
    return sorted(keep | {int(a) for a in arrivals})


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
        "prio:bounce. Poradie riešenia riadi priorita dohodnutá v tejto session "
        "(architektúra > architecture-rework > prio:bounce > backlog, #993), nie "
        "tento nudge. NEdispatchni dep-wait jednotku (otvorené Depends-on). "
        "Ak už na nich robíš, potvrď."
        % (_fmt_arrivals(arrivals), cur_count))
    if len(text) <= NUDGE_MAX_CHARS:
        return text
    return text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


# --- ROLE-AWARE (#1029) ----------------------------------------------------
# The gk box declares TWO windows (cli_fleet, #998): gk (role=review, parallel,
# ~/devel/odoo/odoo-erp) and gk-infra (role=infra, sequential,
# ~/devel/odoo/odoo-erp-infra). The FLOW review session routes infra-caused
# STOP:/GATEKEEPER-ACTION (INFRA) as comments on odoo-erp #6883 + infra tickets,
# but nothing woke the INFRA session — the rider was blind to it (the #998
# sequential skip + hardcoded review union). This rider is now ROLE-AWARE: the
# review path is byte-identical; the infra path fetches the INFRA queue, uses an
# infra nudge text, and bypasses the sequential skip (an arrival nudge is
# AWARENESS, not a refill — the exact thing the owner-present infra session was
# blind to). ONE keystroke primitive, ONE floor, ZERO new job/kind.

def _role_queue_config(role, queue_fetch, infra_queue_fetch, classify_builder):
    """Role-dependent `(fetch, cache_key, skip_sequential, classify_builder)`
    for the rider. review = today's union (byte-identical: queue_fetch,
    `queue_arrival_cache`, sequential-skip ON, the #993 dep classify); infra =
    the INFRA queue (infra_queue_fetch, `queue_arrival_infra_cache`,
    sequential-skip OFF — awareness not refill, and NO dep-wait classify). The
    infra-unwired short-circuit stays in the caller (it must `return`)."""
    if role == "infra":
        return infra_queue_fetch, "queue_arrival_infra_cache", False, None
    return queue_fetch, "queue_arrival_cache", True, classify_builder


def _resolve_role(cwd, resolve_role_fn):
    """The pane's role for the role-aware branch — "infra" when the injected
    `resolve_role_fn(cwd)` (cli_concurrency.resolve_role in production) returns
    "infra", else "review". resolve_role_fn None (unwired / legacy tests) →
    "review" (the "wired = on" seam convention, so every pre-#1029 caller keeps
    the byte-identical review path), and any resolver ERROR fails safe to
    "review" (never a spurious infra branch)."""
    if resolve_role_fn is None:
        return "review"
    try:
        r = resolve_role_fn(cwd)
    except Exception:  # noqa: BLE001 — any resolver fault => review (safe)
        return "review"
    return "infra" if r == "infra" else "review"


def _infra_ids_and_map(cur):
    """Normalise the infra fetch's rich records into `(ids, id_map)` for the
    shared set-delta body: `ids` is the list of int identifiers `_queue_decision`
    diffs (a ticket's number, a tagged comment's id), `id_map` maps each id to
    its record for the nudge text. Malformed records (non-dict / no int `id`)
    are dropped (never raises). `cur` None / not-a-list → `(None, {})` so the
    decider sees the undetermined signal."""
    if not isinstance(cur, list):
        return None, {}
    ids = []
    id_map = {}
    for r in cur:
        if not isinstance(r, dict):
            continue
        try:
            rid = int(r["id"])
        except (KeyError, TypeError, ValueError):
            continue
        ids.append(rid)
        id_map[rid] = r
    return ids, id_map


def _fmt_infra_arrivals(records):
    """Compact, human infra-arrival names for the nudge: tickets as `#N`, tagged
    STOP:/GATEKEEPER-ACTION (INFRA) comments as the ticket they sit on. Bounded
    by MAX_NAMED_ARRIVALS so a big wave never blows the char cap."""
    shown = records[:MAX_NAMED_ARRIVALS]
    tickets, comment_nums = [], []
    for r in shown:
        if not isinstance(r, dict):
            continue
        num = r.get("num", r.get("id"))
        if r.get("kind") == "comment":
            if num not in comment_nums:
                comment_nums.append(num)
        else:
            tickets.append(num)
    parts = []
    if tickets:
        parts.append("infra tickety " + " ".join("#%s" % n for n in tickets))
    if comment_nums:
        parts.append("nové STOP:/GATEKEEPER-ACTION (INFRA) komentáre na "
                     + " ".join("#%s" % n for n in comment_nums))
    extra = len(records) - len(shown)
    txt = "; ".join(parts) if parts else "nové infra položky"
    if extra > 0:
        txt += " (+%d ďalších)" % extra
    return txt


def _nudge_text_infra(records, cur_count):
    """The INFRA-role queue-arrival keystroke. Carries the shared `stuck-check: `
    prefix (own-payload recognition + machine-prompt exclusion — see the module
    docstring; NO new prefix registered). Names the NEW infra arrivals
    dynamically (`_fmt_infra_arrivals` — the SPECIFIC ticket/hub each arrival sits
    on) and points at the generic infra queue (`core-quals --role infra`). Box-
    agnostic (review 1 F3): NO hub number or window name is baked in, so a second
    box that declares a `role=infra` window inherits a correct nudge. Hard-capped
    at NUDGE_MAX_CHARS (truncate on a word boundary for a pathological wave)."""
    text = (
        "stuck-check: infra queue arrival — do infra fronty pribudlo: %s "
        "(spolu %d otvorených infra položiek), kým bola INFRA session slepá na "
        "hand-offy z FLOW session. Re-deriv svoj infra backlog "
        "(`core-quals --role infra`) a spracuj STOP:/GATEKEEPER-ACTION (INFRA) "
        "na infra hube a na infra tiketoch. Ak už na nich robíš, potvrď."
        % (_fmt_infra_arrivals(records), cur_count))
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
                               sleep_fn=None, captured=None,
                               batch_collect=None, classify_builder=None,
                               infra_queue_fetch=None, resolve_role_fn=None):
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
    (`compact.pending_compact_hold(sid, now)`, #780/#848 bounded) — the FIRST defer-gate: a pending
    /compact HOLDS the nudge (`hold:compact-pending`, no keystroke, base
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
    # #1029 — resolve the pane ROLE. review (default / unwired) = today's union,
    # byte-identical; infra = the INFRA queue (open infra tickets ∪ tagged
    # STOP:/GATEKEEPER-ACTION (INFRA) comments), an infra nudge text, the #998
    # sequential skip BYPASSED, and no dep-wait classify (an arrival nudge is
    # AWARENESS, not a refill/dispatch).
    role = _resolve_role(cwd, resolve_role_fn)
    _fetch, _cache_key, _skip_sequential, _classify_builder = _role_queue_config(
        role, queue_fetch, infra_queue_fetch, classify_builder)
    if role == "infra" and _fetch is None:
        logs.append("queue-arrival %s -> skip:infra-unwired (role=infra but "
                    "no infra_queue_fetch wired)" % loc)
        return logs

    # #998 — a SEQUENTIAL-mode REVIEW pane never gets a refill/queue-arrival
    # nudge: ONE unit at a time, no refill (subagents/consults are NOT gated).
    # The INFRA role is EXEMPT (#1029): the gk-infra window is ALWAYS sequential
    # and its arrival nudge is an AWARENESS wake, not a refill. Cheap, before any
    # fetch. Fail-safe: a resolver error is treated as non-sequential (today's
    # behaviour), and LOGGED (never a silent swallow).
    if _skip_sequential:
        try:
            import cli_concurrency
            _seq_mode = cli_concurrency.resolve_mode(cwd)
        except Exception as e:  # noqa: BLE001
            _seq_mode = None
            logs.append("queue-arrival %s -> concurrency-resolve-error (%r) — "
                        "treating as non-sequential" % (loc, e))
        if _seq_mode == "sequential":
            logs.append("queue-arrival %s -> skip:sequential-mode" % loc)
            return logs
    # CACHED per-repo, role-NAMESPACED: the fetch fires at most once per repo per
    # TTL. A cache/fetch error reads as None -> skip.
    try:
        cur = _cached_queue(cwd, _fetch, state, now, cache_key=_cache_key)
    except Exception as e:
        logs.append("queue-arrival %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    # #1029 — the decider + baseline diff INT ids. The review union already IS a
    # list of ints (`id_map` None); the infra fetch is rich records that
    # normalise to `(ids, id_map)` — `id_map` maps an arrival id back to its
    # record for the infra nudge text. `cur_ids` is what the whole shared body
    # below counts / advances (never `cur`).
    if role == "infra":
        cur_ids, id_map = _infra_ids_and_map(cur)
    else:
        cur_ids, id_map = cur, None

    rec = qrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    # #993 item 4: build the DEPENDENCY classify_fn for this cwd (per-issue
    # Depends-on read). `classify_builder(cwd)` is the injected seam (network
    # kept out of run_once unit tests, exactly like `queue_fetch`); None
    # (unwired / infra role / legacy tests) = every arrival dispatchable.
    classify_fn = _classify_builder(cwd) if _classify_builder is not None else None
    action, new_rec, reason, arrivals = _queue_decision(rec, cur_ids, now,
                                                        classify_fn=classify_fn)

    if action == "skip":
        logs.append("queue-arrival %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action in ("seed", "track", "hold"):
        if not dry_run:
            new_rec["lts"] = now
            # seed/track means the wave (if any) is resolved/baseline-known; the
            # only remaining `hold` is dep-wait (base kept OLD so they re-detect).
            qrecs[sid] = new_rec
        if action == "hold":  # #993 item 4: every arrival is dep-wait
            # HELD (base kept OLD so they re-detect once their deps close), no
            # keystroke this sweep.
            logs.append("queue-arrival %s -> hold:%s (%d new, all non-dispatchable; "
                        "keep waiting, %d in union)"
                        % (loc, reason, len(arrivals), len(cur_ids)))
        else:
            logs.append("queue-arrival %s -> %s (%s — %d in union, baseline %s)"
                        % (loc, action, reason, len(cur_ids),
                           "seeded" if action == "seed" else "advanced"))
        return logs

    # action == "nudge": persist the seeded/refreshed rec (base OLD, first_seen,
    # lts age-anchor). base is advanced to cur only on a CONFIRMED send below.
    cur_sorted = sorted({int(x) for x in cur_ids})
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
    # WITHOUT a keystroke (base unadvanced, `handled` unclaimed) so it
    # retries a later sweep once the compact delivers. First delivery gate (the
    # strongest constraint). Lazy import — a defensive choice (a top-level import
    # is also fine, goal.py:173 does it), kept local to avoid any dependence on the
    # watchdog package-init ordering; fail-safe False on any error (writer proceeds
    # as pre-#741).
    # #923 BATCH MODE: common delivery guards handled once by the caller.
    if batch_collect is None:
        if not watchdog.nudges_enabled("queue-arrival"):   # #1023 per-kind switch
            logs.append("queue-arrival %s -> skip:kind-off (queue-arrival, %d new)"
                        % (loc, len(arrivals)))
            return logs
        from watchdog import compact as _compact
        if _compact.pending_compact_hold(sid, now):   # #848 bounded
            logs.append("queue-arrival %s -> hold:compact-pending (pending /compact; "
                        "no arrival nudge until it delivers, %d new)"
                        % (loc, len(arrivals)))
            return logs
        if handled is not None and sid in handled:
            logs.append("queue-arrival %s -> skip:already-handled (another sweep "
                        "job typed this pane; retry next sweep, %d new)"
                        % (loc, len(arrivals)))
            return logs
        # #1023: idle-pane only — a busy "Waiting for N background agents" pane
        # ALWAYS defers (the #921 aged override that typed into a long-busy pane
        # is removed); send_verified's own bare-box/strip/queued-hint guards cover
        # a running turn / queued prompt / user draft. The arrival set persists
        # (base kept OLD) and delivers on the next idle tick.
        if _ops_wait_recheck._pane_busy_waiting(captured):
            logs.append("queue-arrival %s -> hold:busy (waiting on background "
                        "agents — deferred to next idle tick, %d new)"
                        % (loc, len(arrivals)))
            return logs
        if not _nudge_gate.gate_ok(state, sid, "queue-arrival", now):
            logs.append("queue-arrival %s -> %s; retry next sweep, "
                        "%d new" % (loc, _nudge_gate.floor_hold_reason(
                            state, sid, "queue-arrival", now), len(arrivals)))
            return logs
    if dry_run:
        logs.append("queue-arrival %s -> WOULD-NUDGE (%d new: %s)"
                    % (loc, len(arrivals), _fmt_arrivals(arrivals)))
        return logs

    # #1029 — role-aware nudge text: the review union names issue numbers; the
    # infra queue names its rich records (tickets / tagged-comment tickets).
    if role == "infra":
        text = _nudge_text_infra([id_map[a] for a in arrivals if a in id_map],
                                 len(cur_ids))
    else:
        text = _nudge_text(arrivals, len(cur_ids))
    # #923 BATCH COLLECT: contribute text, defer delivery+state to caller.
    if batch_collect is not None:
        def _on_deliver(_nr=new_rec, _q=qrecs, _s=sid, _n=now, _h=handled,
                        _st=state, _p=pid, _cs=cur_sorted, _ar=arrivals):
            watchdog._janitor_clear_watch(_st, _p)
            _nr["base"] = _advanced_base(_nr["base"], _cs, _ar)  # #993 review 3
            _nr["send_fails"] = 0
            _q[_s] = _nr
            if _h is not None:
                _h.add(_s)
        batch_collect.append(("queue-arrival", text, _on_deliver))
        logs.append("queue-arrival %s -> batch-collected (%d new)"
                    % (loc, len(arrivals)))
        return logs
    # Mark janitor provenance BEFORE the send (mirrors the sibling jobs): a
    # residual stuck/queued send stays reclaimable (the #372/#1022 janitor-undo
    # path), cleared ONLY on a transcript-CONFIRMED submit.
    watchdog._janitor_mark_watch(state, pid, now)
    # #1023: the BASELINE (arrival-set-done) advances ONLY on a transcript-
    # CONFIRMED submit (`ok`). `delivered-unconfirmed` is NON-TERMINAL for the
    # baseline (janitor watch LEFT SET, arrival re-nudges until confirmed) — BUT
    # 🟡4: the per-kind FLOOR IS stamped (the text reached the pane, so the
    # owner's "raz za hodinu" rule makes this a send), bounding the re-confirm to
    # the first idle tick AFTER the hour. Only a GENUINE swallow (text backed out,
    # nothing seen) skips the floor and backs off via _book_unverified_send below.
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out, nudge="queue-arrival",
                                state=state)  # #1022: record for the wedge
    if not ok:
        if send_out.get("delivered_unconfirmed"):
            # NON-terminal for the baseline — leave base untouched + janitor
            # watch SET for the undo path — but stamp the per-kind floor so the
            # re-confirm defers a full hour (owner's 1/hour rule, 🟡4).
            _nudge_gate.mark_sent(state, sid, "queue-arrival", now)   # #1023 🟡4
            logs.append("queue-arrival %s -> delivered-unconfirmed (no confirmed "
                        "nudge turn; baseline unchanged, floor stamped, undo via "
                        "janitor, re-check after floor, %d new)"
                        % (loc, len(arrivals)))
            return logs
        # A genuine swallow leaves base unadvanced -> retries next sweep; bounded
        # so a persistently-swallowing NON-busy pane backs off after
        # MAX_SEND_FAILS (accept the wave). send_verified already backed our text
        # OUT of the box on a genuine swallow, so nothing parks; sid NOT claimed.
        logs.append(_book_unverified_send(rec, new_rec, cur_sorted, loc,
                                          len(arrivals)))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["base"] = _advanced_base(new_rec["base"], cur_sorted, arrivals)  # #993 review 3
    new_rec["send_fails"] = 0
    qrecs[sid] = new_rec
    _nudge_gate.mark_sent(state, sid, "queue-arrival", now)   # #797
    if handled is not None:
        handled.add(sid)
    logs.append("queue-arrival nudge %s -> %d new (%s), baseline advanced to %d"
                % (loc, len(arrivals), _fmt_arrivals(arrivals), len(cur_sorted)))
    return logs
