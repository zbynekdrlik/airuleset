"""Partition-audit re-check nudge (#547 W→I + #552 I→W/U) — the mechanical
counterpart of the prose-only `_partition_workable` labelling contract, in BOTH
directions.

INCIDENT-W (montalu5, 2026-08-18): a session parked 13 tickets into `W`/`ops-wait`
(waiting on Odoo discussion replies + a gk PROD release). The replies arrived and
the release shipped LONG ago, the armed `/goal` loop never looked, the session
stayed blind until the owner asked by hand. Root cause: the `W` re-entry contract
(`statusline-vocabulary.md` W bullet + skills/autopilot) is PROSE-ONLY — the
`/goal` evaluator reads only the transcript, `_watchdog_backlog_fetch` runs only
`--count` (never `--ops-wait`), and NO watchdog job reads the `--ops-wait` members.
So an armed loop parking on `W` has no trigger to ever re-check the external state.

INCIDENT-I (montalu3, 2026-08-18, #552): the OPPOSITE direction of the SAME
label-driven root cause. `_partition_workable` (cli_quals.py) is purely
label-driven — it derives the I/U/W split mechanically, but the LABELS
(`ops-wait` with evidence, `needs-answer`/`needs-decision`, a delivered
`needs-acceptance`) are set by session/supervisor JUDGMENT per the #526/#539
shapes, and NOTHING periodically forces a session to re-audit its `I` list against
those shapes. #547 mechanized only W→I (a parked W ticket whose event landed → the
label should come OFF); the I→W/U direction (an `I` ticket ALREADY meeting a
parking shape → the label should go ON) had no mechanical trigger at all, so 8
tickets rotated in `I` (fix-class waiting on airuleset#533, sent-thread,
deferred-thread) until the owner pushed back — the footer showed an inflated `I`
the owner could not trust. Both are the SAME class as #527's
`U`-without-a-delivered-question (which #539 mechanized with the `no-question!`
tag): a label-driven partition needs a mechanical re-audit trigger.

WHAT THIS DOES (both directions, ONE daily nudge — #552 combined over #547): on a
per-session cadence (~daily), for an armed `/goal` pane whose partition has
anything to audit (`I > 0` OR `--ops-wait` members parked), deliver ONE verified
keystroke that reminds the session to re-audit its WHOLE partition against the
#526/#539 shapes — the I→W/U clause (re-audit each `I` member: fix-class /
sent-thread / deferred-thread → `ops-wait`=W; delivered owner-question →
needs-answer/decision=U; chained-I + dispatchable stay I) AND, when W members are
parked, the W→I clause (re-check the external event named in the park comment,
then clear `ops-wait` WITH evidence or confirm the wait). The JUDGMENT stays in
the session (the watchdog cannot judge a #526/#539 shape); only the SCHEDULER is
mechanical, and the supervisor stays the ONLY one that sets/clears any label with
evidence — this job only SURFACES the audit back into the loop's attention
(exactly the U-bucket re-entry shape, cadence instead of a routed Discord answer).
Combining both directions into ONE ping (vs a separate I-nudge) is the #552 design
choice: ONE keystroke/day covering the whole partition audit, not two.

DESIGN (#486 reuse, ZERO new delivery/fetch primitives): this rides
`goal_lane_sweep`'s EXISTING armed-candidate-pane loop (which already resolves
pid/cwd/sid/tpath/loc + the `glance`, reads the structured `state["goal_mark"]`
armed gate, and coordinates keystrokes via the per-sweep `handled` set). It reuses
`watchdog.send_verified` (transcript-proof submit), the
`_janitor_mark_watch`/`_janitor_clear_watch` reclaim of a swallowed nudge, and the
shared `stuck-check: ` own-payload prefix (already in `_JANITOR_OWN_PREFIXES` +
`_MACHINE_PROMPT_PREFIXES`, so a swallowed nudge is reclaimable AND never mistaken
for a human answer). Both signals come from the SAME `_partition_workable`
derivation the footer/stop-proof use — the W members via `ops_wait_fetch` (=
`_watchdog_ops_wait_fetch`, the 1:1 sibling of `_watchdog_backlog_fetch`, read
through the per-repo `_cached_ops_wait` TTL cache), and the `I` COUNT via
`i_count` = the already-computed, already-cached `glance.backlog`
(`_cached_backlog_count`) the loop resolved for its own verdict — never a parallel
query and never a new per-sweep fetch (#367/#181; #547-review cache lesson). An
`i_count` of `None` (an awaiting-user / cheap-verdict pane whose backlog the glance
never consulted) is UNDETERMINED, so the I direction fails safe (no I nudge into a
❓-blocked pane), exactly like a `None` W fetch.

The verdict logic is a PURE `_recheck_decision(rec, i_count, w_members, now,
cadence)` with a THREE-valued spirit (nudge / wait / clear / skip-undetermined);
all I/O (fetch, send, state writes) lives in `goal_ops_wait_recheck` behind the
same injectable seams the sibling jobs use, and `dry_run` mutates nothing (#516).

PHASE 2 (source-aware probes) — EVALUATED (#550) & DEFERRED, not a pending TODO.
An event-driven variant — poll the Odoo Discuss thread / gk release named in a W
ticket's park comment and nudge the MOMENT the reply/release lands, instead of on
cadence — was evaluated in #550 and REJECTED as not cheaply-safe:
  - the external SOURCE (which thread / which release) lives ONLY in the free-form
    park COMMENT, not as a machine-readable field in `--ops-wait`'s structured
    output (reason is only acceptance|ops-wait; the title field is free-form
    prose), so a probe needs a per-ticket `gh issue view --comments` fetch on the
    SWEEP path (the on-demand `--waiting` comment machinery never runs there) + a
    fragile NL parse of prose;
  - the Odoo RO poll needs the stream's prod credential (the watchdog user lacks
    it), is fail2ban-sensitive, and is a network call on the 120s sweep critical
    path (#172/#365 class) — the SESSION already does this poll in-session when
    nudged, which is where that credential/access belongs;
  - the latency gap is already tunable via AIRULESET_OPS_WAIT_RECHECK_CADENCE_S
    (floor OPS_WAIT_RECHECK_MIN_S=6h), so a probe only helps below the hours range
    — not a real need for inherently multi-hour/multi-day waits.
REOPEN only when ALL hold: (T1) a machine-readable park convention exists (a
`waiting-on: odoo-thread:<id>` / `waiting-on: release:<tag>` field sessions
reliably emit); (T2) a measured need for sub-6h re-check latency; (T3) a safe,
budgeted RO-poll / push-signal seam OFF the sweep critical path. None hold today.
Full evidence: issue #550.
"""
import os

import watchdog

# env AIRULESET_OPS_WAIT_RECHECK_CADENCE_S — how long a W ticket sits parked (and
# how long between re-nudges) before this job re-surfaces it into the loop. ~22h
# so it fires a little more than daily (robust to sweep timing / never SKIPS a
# day), yet never a per-sweep re-nudge. Both the FIRST-nudge grace (measured from
# first_seen) and the reping cadence use this ONE value.
OPS_WAIT_RECHECK_CADENCE_S = 22 * 3600
# floor for the env override (#504/#543 floor-clamp lesson): a sub-6h value would
# nag an armed loop several times a day about a ticket it JUST parked — a units
# error must never turn the re-check into spam.
OPS_WAIT_RECHECK_MIN_S = 6 * 3600
# orphan-reaper TTL for a per-sid rec whose session is gone (mirrors
# GOAL_MARK_ORPHAN_TTL_S / the #519/#531 per-sid-leak reaper): the `visited_sids`
# gate is PRIMARY (a live pane is never reaped regardless of age), this is only
# the SECONDARY safety for a budget-deferred pane.
OPS_WAIT_RECHECK_ORPHAN_TTL_S = 24 * 3600
# env AIRULESET_OPS_WAIT_FETCH_TTL_S — how long a `--ops-wait` member-list read is
# CACHED per repo (`state["ops_wait_cache"]`, keyed by cwd, shared across every
# armed pane on that repo). This is what stops the fetch from firing every 60s
# sweep per pane: the sibling `_watchdog_backlog_fetch` is likewise cached (10 min,
# `_cached_backlog_count`). 30 min here (a bit longer — the NUDGE cadence is
# ~daily, so the member list never needs to be minute-fresh; a resolved W is
# re-detected within ≤1 TTL). Floored so a units error can't turn it into a
# per-sweep gh union again.
OPS_WAIT_FETCH_TTL_S = 30 * 60
OPS_WAIT_FETCH_TTL_MIN_S = 5 * 60
# a FAILED/unmeasurable fetch (None) is cached only briefly so a transient gh
# hiccup re-checks soon rather than suppressing detection for a whole TTL —
# mirrors BACKLOG_CHECK_FAILURE_TTL_S.
OPS_WAIT_FETCH_FAIL_TTL_S = 60


def _env_int(key, default_s):
    try:
        return int(os.environ.get(key, default_s))
    except (ValueError, TypeError):
        return default_s


def _cadence():
    """The effective cadence: the env override, floored at OPS_WAIT_RECHECK_MIN_S
    so a units-error / accidental sub-hour value can never turn the re-check into
    a per-sweep nag (#504/#543)."""
    return max(_env_int("AIRULESET_OPS_WAIT_RECHECK_CADENCE_S",
                        OPS_WAIT_RECHECK_CADENCE_S), OPS_WAIT_RECHECK_MIN_S)


def _fetch_ttl():
    """The cache TTL for a real member-list read, floored so an env units error
    can't collapse it back to a per-sweep fetch."""
    return max(_env_int("AIRULESET_OPS_WAIT_FETCH_TTL_S", OPS_WAIT_FETCH_TTL_S),
               OPS_WAIT_FETCH_TTL_MIN_S)


def _cached_ops_wait(cwd, ops_wait_fetch, state, now, ttl=None, fail_ttl=None):
    """The parked W member NUMBERS for the repo at `cwd`, CACHED per-cwd in
    `state["ops_wait_cache"]` for `ttl` (a real list) / `fail_ttl` (a None
    failure) — the faithful sibling of `_cached_backlog_count` (#365). This is
    the load-bearing fix (#547 review): without it the fetch would spawn a
    `--ops-wait` gh union subprocess EVERY 60s sweep for EVERY armed pane, on the
    120s-budgeted sweep's critical path — the exact per-sweep-gh class the backlog
    cache exists to prevent. Bounds it to at most one subprocess per repo per TTL,
    shared across every armed pane on that repo.

    `ops_wait_fetch is None` (not wired) -> None, no cache write (the "wired = on"
    convention). A fetch exception -> None. A `ts` crossing the JSON persistence
    boundary is type-checked (a malformed/legacy entry reads as EXPIRED, never
    raises). None (unmeasurable) is cached only for `fail_ttl` so a transient gh
    hiccup re-checks soon. Returns a `list[int]` or None — never a guessed []."""
    if ops_wait_fetch is None:
        return None
    ttl = _fetch_ttl() if ttl is None else ttl
    fail_ttl = OPS_WAIT_FETCH_FAIL_TTL_S if fail_ttl is None else fail_ttl
    cache = state.setdefault("ops_wait_cache", {})
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
        members = ops_wait_fetch(cwd)
    except Exception:
        members = None
    if not (members is None or isinstance(members, list)):
        members = None
    cache[cwd] = {"ts": now, "members": members}
    return members


def _fmt_age(seconds):
    """Human age of a parked W ticket for the nudge text — hours under 48h, days
    beyond (so a fresh-ish park reads honestly rather than rounding to `0d`). A
    negative age (a future `first_seen` under clock skew) clamps to 0 so the text
    never reads `~-1h` (#547 review 🔵)."""
    seconds = max(0, seconds)
    if seconds < 48 * 3600:
        return "~%dh" % int(seconds // 3600)
    return "~%dd" % int(seconds // 86400)


def _sig(members):
    """A stable signature of the parked W set (sorted numbers, comma-joined) —
    stored so a reader can see WHICH tickets a rec is tracking, and so a future
    set-change refinement has a hook. Numeric sort so the sig is order-stable."""
    return ",".join(str(n) for n in sorted(members))


def _members_line(members):
    """`#A #B #C` for the nudge text, oldest-number-first for stable reading."""
    return " ".join("#%d" % n for n in sorted(members))


def _partition_sig(i_count, w_members):
    """A stable signature of the whole partition-audit set — the I count and the
    sorted W numbers — stored for observability (a reader sees what a rec is
    tracking) and as a hook for a future set-change refinement. `i:?`/`w:?` name
    an UNDETERMINED (None) half honestly, never a misleading `0`/empty."""
    i_part = "i:%d" % i_count if isinstance(i_count, int) else "i:?"
    w_part = "w:%s" % _sig(w_members) if isinstance(w_members, list) else "w:?"
    return "%s|%s" % (i_part, w_part)


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state) + i_count (the cached I workable count, or None)
# + w_members (the fetched W numbers, or None) -> (action, new_rec, reason).
# The partition has "something to audit" when I>0 OR W is non-empty. action:
#   "skip"  -- nothing positive to audit AND at least one half UNDETERMINED
#              (fetch failed/refused, or i_count None on a ❓-blocked pane) ->
#              NEVER a nudge, NEVER a state change (the safe direction, #535
#              undetermined→silent; never clear a rec that might still be parked);
#   "clear" -- BOTH halves determined-empty (I==0 AND W==[]) -> partition drained,
#              pop the sid's rec (episode end);
#   "wait"  -- something to audit but still inside the grace / reping window ->
#              persist (seed first_seen/w_first_seen, refresh sig), no nudge;
#   "nudge" -- something to audit past the cadence -> the caller ATTEMPTS a
#              verified send and advances last_nudge only on a CONFIRMED submit.

def _recheck_decision(rec, i_count, w_members, now, cadence):
    """Pure verdict for ONE session's partition audit (#552 generalises #547's
    W-only decider to BOTH directions). `rec` is the persisted per-sid dict (or
    None/malformed for a fresh session). `i_count` is the cached `I` workable
    count (int), or None when UNDETERMINED (a ❓-blocked / cheap-verdict pane whose
    backlog the glance never consulted). `w_members` is the fetched `--ops-wait`
    numbers (list, possibly empty), or None when UNDETERMINED (a gh error/refusal).

    The partition has something to audit when I>0 OR W is non-empty. When NEITHER
    half is positive, the verdict splits on determinability: BOTH halves
    determined-empty (`i_count==0` AND `w_members==[]`) is a genuinely drained
    partition → `clear` (pop the rec); otherwise at least one half is UNDETERMINED
    → `skip` (leave the rec unchanged, no nudge) — never a false re-surface, and
    never a clear of a rec whose W might still be parked. This preserves #547's
    `skip:undetermined` exactly (W=None with I=0/undetermined still skips).

    A non-empty partition nudges only when `now - (last_nudge or first_seen) >=
    cadence`: `first_seen` gives the initial grace (never nag a session about a
    partition it JUST arrived at — after the owner's manual re-audit, a freshly
    seen pane sits in grace and stays silent, the montalu3 fixture), and
    `last_nudge` becomes the reping anchor afterwards. `last_nudge` is PRESERVED
    unchanged here (a "nudge" verdict is an INTENT; the caller sets last_nudge=now
    only after a transcript-confirmed submit, so a swallowed send retries next
    sweep rather than silently skipping a whole cadence). `first_seen` is seeded to
    `now` on first sight — so a long-pre-existing partition (present before this
    job existed) is first nudged one cadence after deploy, the safe cold-start.

    `w_first_seen` is a SECOND, W-specific anchor for the nudge text's truthful
    W-park age: seeded to `now` when W first becomes non-empty and DROPPED when W
    empties, so a long-running I>0 loop that parks a W ticket TODAY reads "~0h",
    never the partition's own (possibly days-old) `first_seen`."""
    i_pos = isinstance(i_count, int) and i_count > 0
    w_pos = isinstance(w_members, list) and bool(w_members)
    if not (i_pos or w_pos):
        i_empty = isinstance(i_count, int) and i_count <= 0
        w_empty = isinstance(w_members, list) and not w_members
        if i_empty and w_empty:
            return ("clear", None, "drained")
        return ("skip", rec, "undetermined")
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)):
        last_nudge = None
    if w_pos:
        w_first_seen = rec.get("w_first_seen") if isinstance(rec, dict) else None
        if not isinstance(w_first_seen, (int, float)):
            w_first_seen = now
    else:
        w_first_seen = None
    new_rec = {"first_seen": first_seen, "last_nudge": last_nudge,
               "w_first_seen": w_first_seen,
               "sig": _partition_sig(i_count, w_members)}
    anchor = last_nudge if last_nudge is not None else first_seen
    if now - anchor >= cadence:
        return ("nudge", new_rec, "due")
    return ("wait", new_rec, "grace")


# The I→W/U re-audit clause (#552): instructs the loop to re-audit its `I` list
# against the #526/#539 parking shapes. Fixed text (the I count itself is not
# named — the watchdog cannot judge WHICH `I` member qualifies; that is session
# judgment) — the word "re-audituj" is the stable token the wiring test keys on.
_I_CLAUSE = (
    "I→W/U: re-audituj každý `I` tiket proti #526/#539 tvarom — fix-class čakajúci "
    "na externú udalosť → `ops-wait` s dôkazom (W); odoslaný acceptance thread → "
    "`ops-wait` (W); deferred-thread na pomenovanú udalosť → `ops-wait` (W); "
    "doručená živá owner-otázka → needs-answer/needs-decision (U); chained-I a "
    "reálne dispatchovateľné ostávajú `I`.")

# The W→I re-check clause (#547, preserved as a subset of the combined nudge):
# names the parked W numbers + their truthful park age, instructs re-checking the
# external event the park comment records.
_W_CLAUSE = (
    "W→I: parknuté `ops-wait` tikety %s (parknuté %s) — over externý stav ktorý si "
    "pri parkovaní zapísal do komentára (odpoveď vo vlákne / vyšlý release): ak už "
    "dorazil, zlož `ops-wait` s dôkazom a vráť tiket do práce; ak sa stále čaká, "
    "potvrď to.")


def _nudge_text(i_count, w_members, now, w_first_seen):
    """The partition-audit keystroke injected into the armed loop. Carries the
    shared `stuck-check: ` prefix (own-payload recognition + machine-prompt
    exclusion — see the module docstring) and composes ONE ping from whichever
    direction(s) apply: the I→W/U re-audit clause when I>0, the W→I re-check clause
    (naming the parked W numbers + their truthful `w_first_seen` age) when W is
    non-empty. When ONLY W applies (I==0), the text degrades to #547's W-only
    nudge; when both apply, both clauses ride one keystroke. The label change is
    always the SUPERVISOR's with evidence, never an auto-unlabel by the watchdog."""
    i_pos = isinstance(i_count, int) and i_count > 0
    w_pos = isinstance(w_members, list) and bool(w_members)
    clauses = []
    if i_pos:
        clauses.append(_I_CLAUSE)
    if w_pos:
        age = (_fmt_age(now - w_first_seen)
               if isinstance(w_first_seen, (int, float)) else "?")
        clauses.append(_W_CLAUSE % (_members_line(w_members), age))
    return (
        "stuck-check: partition-audit — over či `I`/`W` labely tvojho `/goal` "
        "partition sedia s doktrínou #526/#539. %s Label mení supervisor s "
        "dôkazom, nikdy automaticky." % " ".join(clauses))


# --- ORPHAN REAPER ---------------------------------------------------------

def _prune_ops_wait_orphans(wrecs, visited_sids, now,
                            ttl_s=OPS_WAIT_RECHECK_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["ops_wait_recheck"]` (keyed
    on `sid = tpath.stem`). A rec is normally popped at episode end (W set goes
    empty), but a session that DIES while W is still parked would leak its rec
    forever. Reap ONLY when BOTH: (1) the sid was NOT a live candidate pane THIS
    sweep (`visited_sids` — session gone/superseded), AND (2) it is malformed OR
    its `lts` (write-time age anchor) is older than `ttl_s`. The visited gate is
    PRIMARY: a live pane (its loop body reached `sid = tpath.stem`) is never
    reaped regardless of `lts` staleness. A FUTURE `lts` (clock skew) is kept (the
    safe direction, #519). Never a per-episode pop here; never raises. Faithful
    mirror of `goal._prune_goal_mark_orphans` / `_prune_goal_lane_orphans`."""
    if not isinstance(wrecs, dict):
        return
    for sid in [k for k, v in list(wrecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        wrecs.pop(sid, None)


# --- ORCHESTRATOR ----------------------------------------------------------

def goal_ops_wait_recheck(now, run, wrecs, sid, cwd, pid, tpath, loc,
                          dry_run, handled, ops_wait_fetch, state,
                          sleep_fn=None, cadence=None, i_count=None):
    """Audit ONE armed candidate pane's partition (I→W/U + W→I) and, on cadence,
    deliver ONE verified re-audit nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `wrecs[sid]` (persisted
    by the shared `state`); returns a list of decision log lines (#486 — every
    verdict logged, never a silent skip). `dry_run` mutates no persistent state
    and sends nothing.

    `ops_wait_fetch(cwd)` is the injected W seam (network call kept out of run_once
    unit tests, exactly like `backlog_fetch`): returns the parked W numbers
    (`list[int]`), or None when unmeasurable — None fails safe to `skip`. It is
    read through `_cached_ops_wait` (per-repo TTL cache) so the gh subprocess
    fires at most once per repo per TTL, never every sweep per pane (#547 review).

    `i_count` is the `I` workable count — passed by the caller as the ALREADY
    resolved + cached `glance.backlog` (`_cached_backlog_count`), so this job adds
    ZERO new fetch (#552; #547-review cache lesson). `None` (an awaiting-user /
    cheap-verdict pane whose backlog the glance never consulted) is UNDETERMINED
    and fails the I direction safe — no I nudge into a ❓-blocked pane.

    Keystroke coordination reuses the sibling machinery verbatim: `send_verified`
    (transcript-proof submit; a swallowed Enter is NOT booked, its text restored),
    `_janitor_mark_watch`/`_janitor_clear_watch` (a residual stuck send stays
    reclaimable via the shared `stuck-check: ` prefix), and the per-sweep
    `handled` set (at most ONE keystroke per pane per sweep across the keystroke
    jobs — this job runs AFTER the lane nudge in the loop, so a pane the lane
    nudge already typed is deferred to next sweep, and a nudge WE send claims the
    sid so any keystroke job later in the SAME sweep skips it)."""
    logs = []
    cadence = cadence or _cadence()
    # CACHED per-repo (#547 review): the fetch fires at most once per repo per
    # OPS_WAIT_FETCH_TTL_S, NOT every sweep per pane — the sibling of
    # `_cached_backlog_count`. A cache/fetch error reads as None -> skip.
    try:
        members = _cached_ops_wait(cwd, ops_wait_fetch, state, now)
    except Exception as e:
        logs.append("ops-wait-recheck %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    rec = wrecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason = _recheck_decision(rec, i_count, members, now,
                                                cadence)

    if action == "skip":
        logs.append("ops-wait-recheck %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action == "clear":
        if not dry_run:
            wrecs.pop(sid, None)
        logs.append("ops-wait-recheck %s -> clear (partition drained — I==0 AND "
                    "W==[])" % loc)
        return logs

    # action in ("wait", "nudge"): persist the seeded/refreshed rec (first_seen,
    # w_first_seen, sig, lts age-anchor for the reaper). last_nudge is only
    # advanced on a CONFIRMED send below.
    if not dry_run:
        new_rec["lts"] = now
        wrecs[sid] = new_rec

    sig = new_rec["sig"]
    if action == "wait":
        anchor = new_rec["last_nudge"] or new_rec["first_seen"]
        logs.append("ops-wait-recheck %s -> wait (partition %s, %s since anchor "
                    "< cadence)" % (loc, sig, _fmt_age(now - anchor)))
        return logs

    # action == "nudge"
    if handled is not None and sid in handled:
        logs.append("ops-wait-recheck %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("ops-wait-recheck %s -> WOULD-NUDGE partition %s" % (loc, sig))
        return logs

    text = _nudge_text(i_count, members, now, new_rec["w_first_seen"])
    # Mark janitor provenance BEFORE the send (mirrors the lane nudge): a residual
    # stuck send stays reclaimable, cleared only on a confirmed submit.
    watchdog._janitor_mark_watch(state, pid, now)
    if not watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                  logs=logs):
        # Unverified submit — transient, retried next sweep. Do NOT advance
        # last_nudge (else a swallowed send silently skips a whole cadence), do
        # NOT claim the pane in `handled`.
        logs.append("ops-wait-recheck %s -> submit-unverified (partition %s, "
                    "retry next sweep)" % (loc, sig))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    wrecs[sid] = new_rec
    if handled is not None:
        handled.add(sid)
    logs.append("ops-wait-recheck nudge %s -> partition %s (tracked %s)"
                % (loc, sig, _fmt_age(now - new_rec["first_seen"])))
    return logs
