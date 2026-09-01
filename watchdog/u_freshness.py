"""Hourly U-freshness reconcile rider (#797) — keep the footer `U` count TRUTHFUL.

INCIDENT (miva, 2026-08/09): the footer showed `U 1`, and when the owner asked
"čo máš v U?" the session replied "to nie je aktuálne" — the ticket had already
been answered/obsoleted but its `needs-answer`/`needs-decision` label (or a
question-map entry) was never cleared. Since #795 retired the daily question
re-ask, the footer `U N` is the owner's ONLY question surface, so a phantom `U`
directly LIES to him: his morning "čo máš v U?" over several sessions kept turning
up denied questions.

ROOT CAUSE (traced in the code): the footer `U N` is derived PURELY from labels +
the question map (`cli_quals._partition_workable` over `USER_WAITING_LABELS`,
rendered from the tickets-status cache `user_waiting` field by
`statusbar._user_waiting_sfx`). Label/map REMOVAL after a question is
answered/obsoleted is (a) session prose discipline, which sub-devs demonstrably
forget, plus (b) the ONE narrow mechanical clear, job 32 `reconcile_u_labels`,
which by its #515 design acts ONLY on questions captured at job 7's Discord-answer
point — a terminal-answered or simply-obsoleted question is out of its scope by
design (a broad watchdog sweep is a false-CLEAR risk it deliberately rejects).
NOTHING periodically forces a session to re-audit its own U members: the job-20
partition-audit nudge (`ops_wait_recheck`) audits I→W/U and W→I, but its U clause
covers only the mislabelled-I direction — stale-U REMOVAL had no trigger at all.

WHAT THIS DOES: a 5th rider on `goal_lane_sweep`'s EXISTING armed-candidate-pane
loop (ZERO new pane walk / capture), the faithful sibling of #547/#616/#733. For
each armed pane whose tickets-status cache shows `user_waiting > 0` — read via
`statusbar.obligation_partition(cwd)`, the SAME cache the footer renders, ZERO gh
calls — it delivers ONE compact `stuck-check: U-reconcile` keystroke telling the
SESSION to audit each U member (live owner question → keep; answered/obsolete →
drop the `needs-answer`/`needs-decision` label + clean the question-map entry WITH
evidence + `tickets-status --refresh`). A hard per-session floor ≥1h between
U-reconcile nudges (the owner's strop) is enforced by the shared `nudge_gate`.

#795 INVARIANT BY CONSTRUCTION: this rider imports NO notify / Discord path — its
only output is a keystroke into the session. The owner is never pinged; the
session fixes the state, and the footer `U` (his surface) becomes truthful.

DESIGN (#486 reuse, ZERO new delivery primitives): reuses `watchdog.send_verified`
(transcript-proof submit, with the #594 delivered-unconfirmed `out`),
`_pane_busy_waiting` (#714 — never submit into CC's "Waiting for N background
agents" transient), `_janitor_mark_watch`/`_janitor_clear_watch`, the shared
`stuck-check: ` own-payload prefix (already in `_JANITOR_OWN_PREFIXES` +
`_MACHINE_PROMPT_PREFIXES`), the per-sweep `handled` set, the compact latch (#741),
and the `_book_unverified_send` bounded-retry + orphan-reaper shapes. The verdict
logic is a PURE `_u_decision`; all I/O lives in `goal_u_freshness_recheck` behind
injectable seams (`u_fetch`, `refresh_fn`), and `dry_run` mutates nothing.

FAIL-SAFE BIAS (#539/#570 — never nudge on uncertainty): cache absent / no `ts` /
stale `ts` → spawn a refresh + SKIP (self-heals next sweep, #618 pattern);
`user_waiting` None on a fresh cache → skip; `user_waiting == 0` → clear the rec.
NO live `--waiting` subprocess fallback — a U nudge is never urgent enough to buy a
gh subprocess on the 120s sweep budget (#172/#365 class); the members + their
mechanical staleness tags live in the session's own `--waiting` command OUTPUT.
"""
import watchdog
from watchdog import ops_wait_recheck as _ops_wait_recheck
from watchdog import nudge_gate as _nudge_gate

CATEGORY = "u-freshness"

# How fresh the tickets-status cache must be for its `user_waiting` count to be
# trusted for a nudge decision. Mirrors airuleset.py's #618
# `_BACKLOG_STATUS_CACHE_MAX_AGE_S` / goal.py's GOAL_LANE_GIVEUP_CACHE_MAX_AGE_S
# (the SAME cache, the same 15-min tolerance). Older/unreadable → spawn a refresh
# and skip this sweep (the count self-heals within one refresh cycle).
U_STATUS_CACHE_MAX_AGE_S = 15 * 60

# the nudge is a compact TRIGGER (#714 lesson: a multi-KB wall collapses into a
# `[Pasted text]` placeholder the janitor cannot reclaim). Hard-capped.
NUDGE_MAX_CHARS = 700

# bounded retry (#714): a persistently-swallowing NON-busy pane backs off a full
# cadence after this many consecutive unverified submits, instead of typing every
# 60s sweep forever.
MAX_SEND_FAILS = 3

# orphan-reaper TTL for a per-sid rec whose session is gone (mirror of the sibling
# riders): the `visited_sids` gate is PRIMARY, this is the SECONDARY safety.
U_FRESHNESS_ORPHAN_TTL_S = 24 * 3600


def _default_u_fetch(cwd):
    """The default U source: `statusbar.obligation_partition(cwd)` — the SAME
    per-cwd tickets-status cache the footer renders (ZERO gh calls, the #367
    single-derivation lesson: the check is literally 'is the FOOTER claiming
    U>0', which is exactly what must be truthful). Returns `(user_waiting, ts)`:
    the label-based `user_waiting` count (int or None) and the cache write time
    (float or None). Lazy import so a partial checkout / test that never wires the
    rider never imports statusbar."""
    import statusbar
    _open, user_waiting, _ops, _gk, ts = statusbar.obligation_partition(cwd)
    return user_waiting, ts


def _default_refresh(cwd):
    """Kick a detached `tickets-status --refresh` for `cwd` (the #618 self-heal):
    a stale/absent cache is warmed so the NEXT sweep reads a fresh U count. Lazy
    import for the same reason as `_default_u_fetch`."""
    import statusbar
    statusbar._spawn_refresh(cwd)


# --- PURE DECIDER ----------------------------------------------------------
# rec (persisted per-sid state) + u_count (cache `user_waiting`, or None) + ts
# (cache write time, or None) -> (action, new_rec, reason):
#   "refresh" -- cache absent / no ts / stale ts -> spawn a refresh, NO nudge, NO
#                state change (self-heals next sweep, #618);
#   "skip"    -- fresh cache but `user_waiting` is None (a recorded failed refresh)
#                -> NO nudge, NO state change (undetermined, the safe direction);
#   "clear"   -- `user_waiting == 0` -> U drained, pop the sid's rec (episode end);
#   "wait"    -- U>0 but still inside the first_seen grace / reping window ->
#                persist (seed first_seen), NO nudge;
#   "nudge"   -- U>0 past the cadence -> the caller ATTEMPTS a verified send and
#                advances last_nudge only on a CONFIRMED submit.

def _u_decision(rec, u_count, ts, now, cadence, cache_max_age):
    """Pure verdict for ONE session's U-freshness audit. `rec` is the persisted
    per-sid dict (or None/malformed for a fresh session). `u_count` is the cache
    `user_waiting` (int) or None (undetermined). `ts` is the cache write time or
    None.

    Cache freshness is checked FIRST: a missing `ts`, or a `ts` older than
    `cache_max_age`, is UNTRUSTWORTHY → `refresh` (warm it, never nudge on a stale
    count). A fresh cache whose `user_waiting` is None is a recorded failed refresh
    → `skip` (undetermined, never nudge). `user_waiting == 0` → `clear` (the U
    drained — pop the rec). U>0 nudges only when `now - (last_nudge or first_seen)
    >= cadence`: `first_seen` gives the initial grace (never nag a session about a
    U member it JUST arrived at — a question set correctly moments ago sits in
    grace and stays silent), and `last_nudge` becomes the reping anchor afterwards.
    `last_nudge` is PRESERVED here (a "nudge" verdict is an INTENT; the caller sets
    last_nudge=now only on a transcript-confirmed submit, so a swallowed send
    retries next sweep). `first_seen` is seeded to `now` on first sight — a
    long-pre-existing U (present before this job existed) is first nudged one
    cadence after deploy, the safe cold-start."""
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return ("refresh", rec, "no-ts")
    if now - ts > cache_max_age:
        return ("refresh", rec, "stale-cache")
    if not isinstance(u_count, int) or isinstance(u_count, bool):
        return ("skip", rec, "u-undetermined")
    if u_count <= 0:
        return ("clear", None, "u-zero")
    first_seen = rec.get("first_seen") if isinstance(rec, dict) else None
    if not isinstance(first_seen, (int, float)) or isinstance(first_seen, bool):
        first_seen = now
    last_nudge = rec.get("last_nudge") if isinstance(rec, dict) else None
    if not isinstance(last_nudge, (int, float)) or isinstance(last_nudge, bool):
        last_nudge = None
    new_rec = {"first_seen": first_seen, "last_nudge": last_nudge,
               "u_count": u_count}
    anchor = last_nudge if last_nudge is not None else first_seen
    if now - anchor >= cadence:
        return ("nudge", new_rec, "due")
    return ("wait", new_rec, "grace")


# #714 — the compact U-reconcile TRIGGER text. The nudge carries the shared
# `stuck-check: ` prefix (own-payload recognition + machine-prompt exclusion), the
# footer U count, the commands the session runs itself (`--waiting` +
# `tickets-status --refresh`), and the #795 no-re-ask invariant. The members + the
# mechanical `no-question!`/`queued` tags live in the `--waiting` OUTPUT, never the
# keystroke (#527/#622 — the existing tag machinery is the mechanical signal, the
# session is the judge). Hard-capped at NUDGE_MAX_CHARS.
def _nudge_text(u_count):
    text = (
        "stuck-check: U-reconcile — pätička hlási U=%d. Spusti "
        "`core-quals/slice-quals --waiting` a nad KAŽDÝM U členom rozhodni: "
        "(a) živá otázka na ownera → ostáva; (b) zodpovedaná/vyriešená/neaktuálna "
        "→ zlož needs-answer/needs-decision + vyčisti question-map záznam S "
        "DÔKAZOM, potom `tickets-status --refresh`. Tagy no-question!/queued rieš "
        "podľa doktríny. Otázku ownerovi NEOPAKUJ (#795) — len sprav U pravdivé."
        % u_count)
    if len(text) <= NUDGE_MAX_CHARS:
        return text
    return text[:NUDGE_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"


# --- BOUNDED RETRY ---------------------------------------------------------

def _book_unverified_send(rec, new_rec, loc, u_count, now, cadence):
    """#714 bounded retry: book ONE undelivered send onto the persisted rec
    (`new_rec` IS `urecs[sid]`, so mutation persists). Under MAX_SEND_FAILS it
    increments the consecutive-failure counter and retries next sweep (last_nudge
    unadvanced → the SAME U is re-detected); at MAX_SEND_FAILS it BACKS OFF one
    full cadence (advance last_nudge, reset the counter) so a persistently-
    swallowing NON-busy pane is not typed into every 60s sweep forever. The counter
    crosses the JSON persistence boundary, so a corrupt/legacy non-int reads as 0
    and never raises. Returns the decision log line."""
    prior = rec.get("send_fails") if isinstance(rec, dict) else None
    fails = (prior if isinstance(prior, int) and not isinstance(prior, bool)
             else 0) + 1
    if fails >= MAX_SEND_FAILS:
        new_rec["last_nudge"] = now
        new_rec["send_fails"] = 0
        return ("u-freshness %s -> submit-unverified x%d — backing off one "
                "cadence (bounded retry #714, U=%d)" % (loc, fails, u_count))
    new_rec["send_fails"] = fails
    return ("u-freshness %s -> submit-unverified (attempt %d/%d, retry next "
            "sweep, U=%d)" % (loc, fails, MAX_SEND_FAILS, u_count))


# --- ORPHAN REAPER ---------------------------------------------------------

def _prune_u_freshness_orphans(urecs, visited_sids, now,
                               ttl_s=U_FRESHNESS_ORPHAN_TTL_S):
    """#531 — age/live-gated orphan prune for `state["u_freshness"]` (keyed on
    `sid = tpath.stem`). A rec is normally popped at episode end (U goes 0), but a
    session that DIES while U>0 would leak its rec forever. Reap ONLY when BOTH:
    (1) the sid was NOT a live candidate pane THIS sweep (`visited_sids`), AND
    (2) it is malformed OR its `lts` (write-time age anchor) is older than `ttl_s`.
    The visited gate is PRIMARY. A FUTURE `lts` (clock skew) is kept (the safe
    direction, #519). Never raises. Faithful mirror of the sibling reapers."""
    if not isinstance(urecs, dict):
        return
    for sid in [k for k, v in list(urecs.items())
                if k not in visited_sids
                and not (isinstance(v, dict)
                         and isinstance(v.get("lts"), (int, float))
                         and (now - v["lts"]) < ttl_s)]:
        urecs.pop(sid, None)


# --- ORCHESTRATOR ----------------------------------------------------------

def goal_u_freshness_recheck(now, run, urecs, sid, cwd, pid, tpath, loc,
                             dry_run, handled, u_fetch, state,
                             sleep_fn=None, refresh_fn=None, captured=None):
    """Audit ONE armed candidate pane's footer U count and, on cadence, deliver
    ONE verified U-reconcile nudge into that session. Called from
    `goal.goal_lane_sweep`'s existing armed-pane loop with the already-resolved
    pane context (ZERO new pane walk / capture). Mutates `urecs[sid]` (persisted by
    the shared `state`); returns a list of decision log lines (#486 — every verdict
    logged, never a silent skip). `dry_run` mutates no persistent state and sends
    nothing.

    `u_fetch(cwd)` is the injected U seam (kept out of run_once unit tests, exactly
    like `ops_wait_fetch`): returns `(user_waiting, ts)` — the label-based U count
    and the cache write time, both int/float or None. It is a LOCAL cache read
    (`statusbar.obligation_partition`), so unlike the gh-subprocess riders it needs
    no per-repo TTL cache — it costs one small file read. `refresh_fn(cwd)` warms a
    stale/absent cache (#618 self-heal); both default to the `statusbar`-backed
    module helpers.

    Keystroke coordination reuses the sibling machinery verbatim: the compact latch
    (#741, `compact.has_pending_request`), `send_verified` (transcript-proof
    submit), `_janitor_mark_watch`/`_janitor_clear_watch`, the per-sweep `handled`
    set (this rider runs LAST in the loop, after queue-arrival, so a pane an earlier
    keystroke job typed is deferred), `_pane_busy_waiting` (#714 busy-pane gate),
    and — the #797 addition — the SHARED `nudge_gate` (the owner's 1×/hour U strop
    AND the cross-category family-spacing floor). `captured` is the pane capture the
    caller already read (ZERO new capture); None skips the busy gate.

    #795 INVARIANT: no notify path is imported or called — the only output is a
    keystroke; the owner is never pinged."""
    logs = []
    fetch = u_fetch or _default_u_fetch
    refresh = refresh_fn or _default_refresh
    try:
        u_count, ts = fetch(cwd)
    except Exception as e:
        logs.append("u-freshness %s -> skip:fetch-error (%r) — undetermined, "
                    "no nudge" % (loc, e))
        return logs

    rec = urecs.get(sid)
    if not isinstance(rec, dict):
        rec = {}
    action, new_rec, reason = _u_decision(
        rec, u_count, ts, now, _nudge_gate._u_cadence(), U_STATUS_CACHE_MAX_AGE_S)

    if action == "refresh":
        if not dry_run:
            try:
                refresh(cwd)
            except Exception as e:
                logs.append("u-freshness %s -> refresh-spawn-error (%r)"
                            % (loc, e))
        logs.append("u-freshness %s -> skip:%s (spawned refresh, no nudge)"
                    % (loc, reason))
        return logs
    if action == "skip":
        logs.append("u-freshness %s -> skip:%s (state unchanged)" % (loc, reason))
        return logs
    if action == "clear":
        if not dry_run:
            urecs.pop(sid, None)
        logs.append("u-freshness %s -> clear (U==0)" % loc)
        return logs

    # action in ("wait", "nudge"): persist the seeded/refreshed rec (first_seen,
    # u_count, lts age-anchor). last_nudge is only advanced on a CONFIRMED send.
    if not dry_run:
        new_rec["lts"] = now
        urecs[sid] = new_rec

    if action == "wait":
        anchor = new_rec["last_nudge"] or new_rec["first_seen"]
        logs.append("u-freshness %s -> wait (U=%d, %s since anchor < cadence)"
                    % (loc, u_count, _ops_wait_recheck._fmt_age(now - anchor)))
        return logs

    # action == "nudge"
    # #741 WRITER-SIDE LATCH: a pending /compact for this session HOLDS the nudge —
    # never push work into the armed loop while a drained-boundary compact waits
    # for its quiet window. Defer WITHOUT a keystroke (last_nudge unadvanced,
    # `handled` unclaimed) so it retries a later sweep once the compact delivers.
    from watchdog import compact as _compact
    if _compact.has_pending_request(sid):
        logs.append("u-freshness %s -> hold:compact-pending (pending /compact; "
                    "no nudge until it delivers)" % loc)
        return logs
    if handled is not None and sid in handled:
        logs.append("u-freshness %s -> skip:already-handled (another sweep job "
                    "typed this pane; retry next sweep)" % loc)
        return logs
    # #714 BUSY-PANE GATE: NEVER type into a pane showing CC's "Waiting for N
    # background agents to finish" state — the submit is swallowed and parks
    # orphaned. Defer WITHOUT a keystroke; the transient state clears next sweep.
    if _ops_wait_recheck._pane_busy_waiting(captured):
        logs.append("u-freshness %s -> skip:busy-bg-agent (pane waiting on a "
                    "background agent — deferred, retry next sweep)" % loc)
        return logs
    # #797 SHARED CADENCE GATE: the owner's hard 1×/hour U strop AND the
    # cross-category family-spacing floor. A closed gate DEFERS (no keystroke,
    # last_nudge unadvanced, `handled` unclaimed) so it retries a later sweep —
    # never cancels. (u-freshness also has its own last_nudge cadence above; the
    # gate is the cross-rider authority + the owner's env-clampable strop.)
    if not _nudge_gate.gate_ok(state, sid, CATEGORY, now):
        logs.append("u-freshness %s -> hold:cadence-gate (shared 1x/hour U strop "
                    "or family gap; retry next sweep)" % loc)
        return logs
    if dry_run:
        logs.append("u-freshness %s -> WOULD-NUDGE (U=%d)" % (loc, u_count))
        return logs

    text = _nudge_text(u_count)
    # Mark janitor provenance BEFORE the send (mirrors the sibling jobs): a
    # residual stuck send stays reclaimable, cleared only on a delivered submit.
    watchdog._janitor_mark_watch(state, pid, now)
    # #594: a DELIVERED submit (confirmed OR box-bare-unconfirmed) advances the
    # cadence; only a GENUINE swallow / abort retries next sweep.
    send_out = {}
    ok = watchdog.send_verified(pid, text, run, tpath, sleep_fn=sleep_fn,
                                logs=logs, out=send_out)
    delivered = ok or bool(send_out.get("delivered_unconfirmed"))
    if not delivered:
        # A genuine swallow leaves last_nudge unadvanced -> retries next sweep;
        # bounded so a persistently-swallowing NON-busy pane backs off after
        # MAX_SEND_FAILS. send_verified already backed our text OUT of the box on a
        # genuine swallow, so nothing parks; sid NOT claimed, gate NOT marked.
        logs.append(_book_unverified_send(rec, new_rec, loc, u_count, now,
                                          _nudge_gate._u_cadence()))
        return logs
    watchdog._janitor_clear_watch(state, pid)
    new_rec["last_nudge"] = now
    new_rec["send_fails"] = 0
    urecs[sid] = new_rec
    _nudge_gate.mark_sent(state, sid, CATEGORY, now)   # #797 — start the strop clock
    if handled is not None:
        handled.add(sid)
    note = "" if ok else " (delivered-unconfirmed — submit raced confirmation)"
    logs.append("u-freshness nudge %s -> U=%d (tracked %s)%s"
                % (loc, u_count,
                   _ops_wait_recheck._fmt_age(now - new_rec["first_seen"]), note))
    return logs
