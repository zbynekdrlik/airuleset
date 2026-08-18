"""W/ops-wait re-check nudge (#547) — the mechanical counterpart of the prose-only
`W`-bucket re-entry contract.

INCIDENT (montalu5, 2026-08-18): a session parked 13 tickets into `W`/`ops-wait`
(waiting on Odoo discussion replies + a gk PROD release). The replies arrived and
the release shipped LONG ago, the armed `/goal` loop never looked, the session
stayed blind until the owner asked by hand. Root cause: the `W` re-entry contract
(`statusline-vocabulary.md` W bullet + skills/autopilot) is PROSE-ONLY — the
`/goal` evaluator reads only the transcript, `_watchdog_backlog_fetch` runs only
`--count` (never `--ops-wait`), and NO watchdog job reads the `--ops-wait` members.
So an armed loop parking on `W` has no trigger to ever re-check the external state.
This is the SAME class as #527's `U`-without-a-delivered-question, which #539
mechanized with the `no-question!` tag; `W` needs its mechanical counterpart.

WHAT THIS DOES (source-agnostic core — the ticket's phase 1): on a per-session
cadence (~daily), for an armed `/goal` pane whose repo has `--ops-wait` members
parked past a grace window, deliver ONE verified keystroke into that session:
"re-check the external state and either clear ops-wait WITH evidence or confirm
still waiting". The supervisor stays the ONLY one who clears the label with
evidence — this job only SURFACES the parked ticket back into the loop's
attention (exactly the U-bucket re-entry shape, cadence instead of a routed
Discord answer).

DESIGN (#486 reuse, ZERO new delivery primitives): this rides `goal_lane_sweep`'s
EXISTING armed-candidate-pane loop (which already resolves pid/cwd/sid/tpath/loc,
reads the structured `state["goal_mark"]` armed gate, and coordinates keystrokes
via the per-sweep `handled` set). It reuses `watchdog.send_verified`
(transcript-proof submit), the `_janitor_mark_watch`/`_janitor_clear_watch`
reclaim of a swallowed nudge, and the shared `stuck-check: ` own-payload prefix
(already in `_JANITOR_OWN_PREFIXES` + `_MACHINE_PROMPT_PREFIXES`, so a swallowed
nudge is reclaimable AND never mistaken for a human answer). The `--ops-wait`
members come from the SAME `_partition_workable` derivation the footer/stop-proof
use (via `ops_wait_fetch`, an injected seam = `_watchdog_ops_wait_fetch` in
airuleset.py, the 1:1 sibling of `_watchdog_backlog_fetch`) — never a parallel
query (#367/#181).

The verdict logic is a PURE `_recheck_decision(rec, members, now, cadence)` with a
THREE-valued spirit (nudge / wait / clear / skip-undetermined); all I/O
(fetch, send, state writes) lives in `goal_ops_wait_recheck` behind the same
injectable seams the sibling jobs use, and `dry_run` mutates nothing (#516).

PHASE 2 (source-aware probes) — EVALUATED (#550) & DEFERRED, not a pending TODO.
An event-driven variant — poll the Odoo Discuss thread / gk release named in a W
ticket's park comment and nudge the MOMENT the reply/release lands, instead of on
cadence — was evaluated in #550 and REJECTED as not cheaply-safe:
  - the external SOURCE (which thread / which release) lives ONLY in the free-form
    park COMMENT, never in `--ops-wait`'s structured output (reason is only
    acceptance|ops-wait), so a probe needs a NEW per-ticket `gh issue view
    --comments` fetch + a fragile NL parse of prose;
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


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state) + members (the fetched W numbers, or None) ->
# (action, new_rec, reason). action:
#   "skip"  -- members undetermined (fetch failed/refused) -> NEVER a nudge,
#              NEVER a state change (the safe direction, #535 undetermined→silent);
#   "clear" -- no W members parked -> pop the sid's rec (episode end);
#   "wait"  -- W parked but still inside the grace / reping window -> persist
#              (seed first_seen, refresh sig), no nudge;
#   "nudge" -- W parked past the cadence -> the caller ATTEMPTS a verified send
#              and advances last_nudge only on a CONFIRMED submit.

def _recheck_decision(rec, members, now, cadence):
    """Pure verdict for ONE session's W re-check. `rec` is the persisted per-sid
    dict (or None/malformed for a fresh session). `members` is the fetched
    `--ops-wait` numbers, or None when the fetch was UNDETERMINABLE (a gh
    error/refusal) — undetermined always fails safe to `skip` (no nudge, no state
    write), never a false re-surface of a ticket that may already be cleared.

    A non-empty set nudges only when `now - (last_nudge or first_seen) >=
    cadence`: `first_seen` gives the initial grace (never nag a session about a
    ticket it JUST parked — it knows), and `last_nudge` becomes the reping anchor
    afterwards. `last_nudge` is PRESERVED unchanged here (a "nudge" verdict is an
    INTENT; the caller sets last_nudge=now only after a transcript-confirmed
    submit, so a swallowed send retries next sweep rather than silently skipping a
    whole cadence). `first_seen` is seeded to `now` on first sight — so a
    long-pre-existing park (parked before this job existed) is first nudged one
    cadence after deploy, the safe cold-start (never a false nudge, always far
    better than the incident's "never")."""
    if members is None:
        return ("skip", rec, "undetermined")
    if not members:
        return ("clear", None, "no-w")
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)):
        last_nudge = None
    new_rec = {"first_seen": first_seen, "last_nudge": last_nudge,
               "sig": _sig(members)}
    anchor = last_nudge if last_nudge is not None else first_seen
    if now - anchor >= cadence:
        return ("nudge", new_rec, "due")
    return ("wait", new_rec, "grace")


def _nudge_text(members, now, first_seen):
    """The keystroke injected into the armed loop. Carries the shared
    `stuck-check: ` prefix (own-payload recognition + machine-prompt exclusion —
    see the module docstring), names the parked W numbers and how long they have
    been tracked, and instructs the loop to re-check the SPECIFIC external state
    its own park comment records (a thread reply / a gk release) and to either
    clear `ops-wait` WITH evidence or confirm the wait — never an auto-unlabel by
    the watchdog."""
    age = _fmt_age(now - first_seen)
    return (
        "stuck-check: W tikety %s sú parknuté na externú udalosť (ops-wait) už %s "
        "a nič medzitým tento stav neprekontrolovalo. Over externý stav ktorý si "
        "pri parkovaní zapísal do komentára tiketu (odpoveď vo vlákne / vyšlý "
        "release): ak už dorazil, zlož `ops-wait` s dôkazom a vráť tiket do práce; "
        "ak sa stále čaká, potvrď to. Label skladá supervisor s dôkazom, nikdy "
        "automaticky." % (_members_line(members), age))


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
                          sleep_fn=None, cadence=None):
    """Re-check ONE armed candidate pane's parked W tickets and, on cadence,
    deliver a verified re-check nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `wrecs[sid]` (persisted
    by the shared `state`); returns a list of decision log lines (#486 — every
    verdict logged, never a silent skip). `dry_run` mutates no persistent state
    and sends nothing.

    `ops_wait_fetch(cwd)` is the injected seam (network call kept out of run_once
    unit tests, exactly like `backlog_fetch`): returns the parked W numbers
    (`list[int]`), or None when unmeasurable — None fails safe to `skip`. It is
    read through `_cached_ops_wait` (per-repo TTL cache) so the gh subprocess
    fires at most once per repo per TTL, never every sweep per pane (#547 review).

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
    action, new_rec, reason = _recheck_decision(rec, members, now, cadence)

    if action == "skip":
        logs.append("ops-wait-recheck %s -> skip:%s (state unchanged)"
                    % (loc, reason))
        return logs
    if action == "clear":
        if not dry_run:
            wrecs.pop(sid, None)
        logs.append("ops-wait-recheck %s -> clear (no W members parked)" % loc)
        return logs

    # action in ("wait", "nudge"): persist the seeded/refreshed rec (first_seen,
    # sig, lts age-anchor for the reaper). last_nudge is only advanced on a
    # CONFIRMED send below.
    if not dry_run:
        new_rec["lts"] = now
        wrecs[sid] = new_rec

    sig = new_rec["sig"]
    if action == "wait":
        anchor = new_rec["last_nudge"] or new_rec["first_seen"]
        logs.append("ops-wait-recheck %s -> wait (W %s, %s since anchor < cadence)"
                    % (loc, sig, _fmt_age(now - anchor)))
        return logs

    # action == "nudge"
    if handled is not None and sid in handled:
        logs.append("ops-wait-recheck %s -> skip:already-handled (another sweep "
                    "job typed this pane; retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("ops-wait-recheck %s -> WOULD-NUDGE W %s" % (loc, sig))
        return logs

    text = _nudge_text(members, now, new_rec["first_seen"])
    # Mark janitor provenance BEFORE the send (mirrors the lane nudge): a residual
    # stuck send stays reclaimable, cleared only on a confirmed submit.
    watchdog._janitor_mark_watch(state, pid, now)
    if not watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                  logs=logs):
        # Unverified submit — transient, retried next sweep. Do NOT advance
        # last_nudge (else a swallowed send silently skips a whole cadence), do
        # NOT claim the pane in `handled`.
        logs.append("ops-wait-recheck %s -> submit-unverified (W %s, retry next "
                    "sweep)" % (loc, sig))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    wrecs[sid] = new_rec
    if handled is not None:
        handled.add(sid)
    logs.append("ops-wait-recheck nudge %s -> W %s (parked %s)"
                % (loc, sig, _fmt_age(now - new_rec["first_seen"])))
    return logs
