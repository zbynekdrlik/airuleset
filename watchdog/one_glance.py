"""#486 G3+G6 -- the one-glance STRUCTURED predicate + the authoritative armed
signal.

Answers the human's single-glance supervision question -- "is this supervisor
session healthily working its backlog, or is it stuck?" -- from STRUCTURED
STATE ONLY, never from a rendered tmux pane. The whole point of #486: a person
reads the MEANING ("goal armed + 0 workers + backlog waiting + nothing moving =
stuck") in one glance, while the old guard buried that predicate under a
closed-form regex over the footer PIXELS that goes silently blind on every
render change (stash prefix, `(1d)` age granularity -- the gk diagnostic).

The inputs are all composed from EXISTING canonical readers (no new parser, so
nothing can drift -- exactly what #486 forbids):

  * armed  <- ``resolve_goal_armed`` (G6): a strict PRECEDENCE chain, dark_watch's
    tail-proof ``state["goal_mark"]`` marker FIRST, the G1 heartbeat's own
    ``goal_armed`` only as the fallback (both the render footer AND the heartbeat
    go blind past the 4 MB transcript tail, so a day-old arm needs the marker).
  * idle / marker          <- G1 ``session_status.read_status`` (the heartbeat
    JSON: file mtime as the idle signal, its terminal ``marker``).
  * live worker count      <- G2 ``transcripts.count_live_workers`` (subagent
    transcript mtimes + the #484 wedged-worker guard).
  * open backlog count     <- ``cross_stream._cached_backlog_count`` (the same
    cached ``gh`` count the lane path already reads).

This module is the PURE core (``one_glance_verdict`` / ``resolve_goal_armed``)
plus a resolver (``evaluate``) that takes the three readers as INJECTED callables
-- so it imports nothing from the ``watchdog`` package (no import cycle) and a
reviewer can see EXACTLY which structured readers are composed.

G6 SCOPE: this predicate's ``goal_armed`` is now the AUTHORITATIVE armed action
gate for ``goal_lane_sweep`` (it replaced the render ``pane_goal_armed`` read).
The G5 parallel-run mismatch machinery (which compared the two paths to earn the
evidence for this retirement) is deleted -- the render path no longer gates the
lane action, so there is nothing left to compare against.
"""
from collections import namedtuple

# The verdict a single glance produces, plus the resolved facts behind it (so a
# caller never has to re-parse the formatted line). `live_workers` / `backlog`
# are `None` when the verdict was resolvable from the armed signal + heartbeat
# ALONE (the cheap short-circuit in `evaluate` never consults those readers for
# them). `src` names WHERE the armed verdict came from (goal_mark / heartbeat /
# heartbeat-tail / unknown) -- the observability signal that replaced the render
# footer annotation.
OneGlance = namedtuple(
    "OneGlance",
    "verdict live_workers backlog idle_over_threshold marker heartbeat_state "
    "goal_armed idle_s src line",
)

# Every verdict, smallest concern -> most actionable. The ONLY actionable one is
# `stuck`; the rest are honest "healthy / not-a-candidate / can't-tell" states.
VERDICTS = (
    "no-heartbeat",   # armed unknown AND heartbeat absent/corrupt
    "armed-unknown",  # armed unknown but a heartbeat exists (goal_armed None)
    "not-armed",      # armed False -> no /goal here
    "awaiting-user",  # ❓ marker -> blocked on a question, never "stuck"
    "working",        # armed + >=1 live worker -> lanes occupied
    "no-backlog",     # armed + 0 workers + no/unmeasurable open backlog
    "warming",        # armed + 0 workers + backlog, but recently active (debounce)
    "stuck",          # armed + 0 workers + backlog + idle>N + not awaiting-user
)

# The verdicts that mean "the structured state says a /goal IS armed here" (a
# genuine lane candidate). Used to decide which lines are worth journalling.
_STRUCTURED_ARMED = ("awaiting-user", "working", "no-backlog", "warming", "stuck")


def resolve_goal_armed(goal_mark_entry, hb_goal_armed):
    """#486 G6 -- the authoritative armed signal: a strict PRECEDENCE chain over
    STRUCTURED state. Returns ``(armed, src)`` where ``armed`` is
    ``True``/``False``/``None``.

    ``goal_mark_entry`` is ``state["goal_mark"][sid]`` -- the incremental
    ``Goal set:``/``Goal cleared:`` marker ``goal_dark_watch`` maintains and
    PERSISTS across sweeps AND watchdog restarts. Once the arm's ``Goal set:``
    line was seen (in the delta at arm time) the "set" state survives past the
    4 MB tail the render footer AND the heartbeat's own single-shot
    ``goal_armed`` scan BOTH go blind on (the live gk incident: a day-old arm).
    So a goal_mark verdict is strictly fresher/truer and the heartbeat NEVER
    vetoes it -- an ``hb_goal_armed`` False on a long-armed loop is the 4 MB lie
    (live-confirmed as the G5 ``structured-blind`` class). The heartbeat is only
    the fallback when goal_mark has no verdict (no marker seen yet).

    ``None`` (both unknown) is fail-CLOSED at the gate: the lane nudge fires only
    on a POSITIVE armed, never a guess -- a spurious keystroke into a session
    that turns out not-armed is the one direction the guard must avoid.
    """
    mark = goal_mark_entry.get("mark") if isinstance(goal_mark_entry, dict) else None
    mstate = mark.get("state") if isinstance(mark, dict) else None
    if mstate == "set":
        return True, "goal_mark"
    if mstate == "cleared":
        return False, "goal_mark"
    if hb_goal_armed is True:
        return True, "heartbeat"
    if hb_goal_armed is False:
        return False, "heartbeat-tail"
    return None, "unknown"


def heartbeat_only_verdict(heartbeat_state, goal_armed, marker):
    """The verdict when it is resolvable from the RESOLVED armed signal + the
    marker ALONE -- before the worker count or the backlog is ever consulted --
    or ``None`` when a /goal IS armed and the session is not awaiting the user,
    the only case that genuinely needs the (more expensive) worker + backlog
    readers.

    ``goal_armed`` here is ``resolve_goal_armed``'s result, NOT the raw
    heartbeat: an armed-True/False verdict is DEFINITE regardless of whether a
    heartbeat file exists (goal_mark can arm a heartbeatless session), so it
    takes precedence over the ``absent``/``corrupt`` heartbeat-state label,
    which only decides how to NAME an ``armed is None`` (can't-tell) verdict.
    Single source of truth shared by ``one_glance_verdict`` (continues past
    ``None``) and ``evaluate`` (uses ``None`` to decide whether to pay for those
    two readers)."""
    if goal_armed is True:
        if marker == "needs_you":
            return "awaiting-user"     # ❓-blocked -> never "stuck"
        return None                    # armed + not awaiting -> needs readers
    if goal_armed is False:
        return "not-armed"
    # goal_armed is None -> can't tell; keep the honest heartbeat-state label.
    if heartbeat_state in ("absent", "corrupt"):
        return "no-heartbeat"
    return "armed-unknown"


def one_glance_verdict(*, heartbeat_state, goal_armed, marker,
                       idle_over_threshold, live_workers, backlog):
    """Classify a supervisor session from RESOLVED structured facts. Pure --
    no I/O, no pane text, deterministic. Returns one of ``VERDICTS``.

    The ordering encodes the human's own glance: first rule out "can't tell"
    (armed unknown), then "not a lane candidate" (no goal armed), then
    "legitimately not stuck" (blocked on a question / lanes occupied / nothing
    to do / just-recently active), and only what survives all of those is
    ``stuck``. Every "not stuck" branch is a POSITIVE reason, never silence.
    """
    cheap = heartbeat_only_verdict(heartbeat_state, goal_armed, marker)
    if cheap is not None:
        return cheap
    # goal_armed is True and not awaiting-user -- a genuine lane candidate.
    if not isinstance(live_workers, int):
        # DEFENSIVE symmetry with the backlog guard below (count_live_workers
        # contractually returns an int today): an UNMEASURABLE worker count can
        # never confirm workers==0, so it must never assert `stuck` -- the safe
        # direction (a spurious `warming` at worst, never a false nudge).
        return "warming"
    if live_workers > 0:
        return "working"
    if not isinstance(backlog, int) or backlog <= 0:
        # None = unmeasurable backlog -> never guessed as work-to-do (the
        # fail-safe direction: an unmeasurable backlog must not read as stuck).
        return "no-backlog"
    if not idle_over_threshold:
        return "warming"
    return "stuck"


def _armed_word(v):
    return {True: "yes", False: "no", None: "?"}.get(v, "?")


def _idle_word(idle_s):
    if not isinstance(idle_s, (int, float)):
        return "n/a"
    return "%ds" % int(idle_s)


def _num_word(v):
    # `n/a` for a reader that was never consulted (cheap verdict) or genuinely
    # unmeasurable -- honest, never a misleading "0"/"None".
    return str(v) if isinstance(v, int) else "n/a"


def format_line(loc, g):
    """The single ``one-glance <loc> -> <VERDICT> (...)`` decision line, in the
    same greppable shape as the existing ``lane-occupancy <loc> -> ...`` lines.
    Carries every structured number AND ``src`` -- which structured signal the
    armed verdict came from (``goal_mark`` = the tail-proof marker; ``heartbeat``
    = the fallback; ``heartbeat-tail`` = the possibly-4MB-blind not-armed;
    ``unknown`` = neither could tell). This is the observability that replaced
    the render footer's own read: a decision line for EVERY candidate pane, so
    the deliberately-SILENT render skip this redesign removed can never recur.
    """
    return ("one-glance %s -> %s (hb=%s armed=%s src=%s workers=%s backlog=%s "
            "idle=%s marker=%s)" % (
                loc, g.verdict, g.heartbeat_state, _armed_word(g.goal_armed),
                g.src, _num_word(g.live_workers), _num_word(g.backlog),
                _idle_word(g.idle_s), g.marker))


def is_informative(g):
    """Whether this one-glance line carries SIGNAL worth journalling, or is pure
    per-sweep noise. Emit for every genuine lane candidate (structured-armed,
    which includes ``stuck``) and for every CAN'T-TELL (``no-heartbeat`` /
    ``armed-unknown`` -- either could hide a genuinely-armed session, the exact
    class this redesign must never go blind on); stay SILENT only for a plain
    ``not-armed`` pane (a definite not-a-candidate -- the per-sweep noise the
    pre-G6 render path also silenced)."""
    if g.verdict in _STRUCTURED_ARMED:
        return True
    return g.verdict != "not-armed"


def evaluate(now, sid, cwd, projects_dir, state, backlog_fetch, goal_mark_entry,
             loc, *, read_status, count_live_workers, cached_backlog_count,
             idle_threshold_s, freshness_s, on_warn=None):
    """Resolve the STRUCTURED inputs and return ``(OneGlance, line)``.

    ``read_status`` / ``count_live_workers`` / ``cached_backlog_count`` are
    INJECTED (the caller passes ``watchdog.read_status`` etc.) so this function
    composes the canonical G1/G2/backlog readers and reads NO pane text. Each
    is contractually non-raising, so ``evaluate`` never raises either.

    ``goal_mark_entry`` is ``state["goal_mark"].get(sid)`` -- dark_watch's
    tail-proof marker for this session, which runs BEFORE the lane sweep in the
    same run_once and shares ``state``. ``resolve_goal_armed`` composes it with
    the heartbeat's own ``goal_armed`` into the authoritative armed signal.

    COST: the armed signal + heartbeat are resolved FIRST, and the two EXPENSIVE
    readers (``count_live_workers`` = a disk stat pass; ``cached_backlog_count``
    = a ``gh`` subprocess on a cache miss) are consulted ONLY when a /goal is
    armed and the session is not awaiting the user -- NOT for the cheap verdicts
    (``no-heartbeat`` / ``armed-unknown`` / ``not-armed`` / ``awaiting-user``).
    A plain non-armed pane therefore costs ONE heartbeat file read per sweep,
    never a per-sweep ``gh`` fetch.

    ``idle_threshold_s`` is passed straight to ``read_status`` as its
    ``stale_after_s``, so the heartbeat's own ``fresh``/``stale`` verdict IS the
    idle>threshold signal. The caller supplies both windows (the lane path's own
    ``GOAL_LANE_IDLE_S`` / ``GOAL_LANE_LIVE_WINDOW_S``).
    """
    hb = read_status(sid=sid, now=now, stale_after_s=idle_threshold_s,
                     on_warn=on_warn)
    armed, src = resolve_goal_armed(goal_mark_entry, hb.goal_armed)
    idle_over = hb.state == "stale"   # stale_after_s == idle_threshold_s
    cheap = heartbeat_only_verdict(hb.state, armed, hb.marker)
    if cheap is not None:
        verdict, workers, backlog = cheap, None, None
    else:
        workers, _evidence = count_live_workers(projects_dir, cwd, sid, now,
                                                freshness_s, on_warn=on_warn)
        backlog = cached_backlog_count(cwd, backlog_fetch, state, now)
        verdict = one_glance_verdict(
            heartbeat_state=hb.state, goal_armed=armed, marker=hb.marker,
            idle_over_threshold=idle_over, live_workers=workers, backlog=backlog)
    g = OneGlance(verdict, workers, backlog, idle_over, hb.marker, hb.state,
                  armed, hb.age_s, src, "")
    g = g._replace(line=format_line(loc, g))
    return g, g.line


# --- #571: the lane-occupancy working-no-tasks + low-mem-surface deciders ------
#
# Both are PURE (facts in / verdict out), so the capped orchestrator
# `goal.goal_lane_occupancy_nudge` consumes them through thin module-level
# helpers and never grows, and every branch is mutation-lockable in isolation.

LaneWorkingNoTasks = namedtuple("LaneWorkingNoTasks", "defer streak log")


def lane_working_no_tasks_decision(*, marker, render_waiters, structured_live,
                                   backlog, defer_streak, max_defers):
    """#571 — the lane-occupancy ``working-no-tasks`` branch decider.

    The branch fires only on a ``⏳`` marker with 0 RENDER task badges
    (``render_waiters <= 0``). Pre-#571 it then DEFERRED unconditionally, reading
    the FLAPPING render badge as truth — so a worker mid-long-tool-call (whose
    strip badge is render-invisible but whose subagent transcript is disk-live)
    read as "0 live tasks" and SUPPRESSED the fill nudge (the gk 16-issues /
    2-lanes regression). ``structured_live`` is the STRUCTURED read
    (``transcripts.lane_has_live_evidence`` over ``count_live_workers`` evidence —
    any non-stale lane, the #565 evidence predicate, NEVER the wedged-excluding
    count).

    Verdict (`LaneWorkingNoTasks(defer, streak, log)`):

      * NOT applicable (``marker != "⏳"`` or ``render_waiters > 0``): the branch
        does not fire → ``defer=False``, streak RESET (0), ``log=None`` (silent —
        the saturation logic below logs its own decision).
      * ``structured_live`` True: lanes ARE live (render-invisible) → NOT a
        working-no-tasks state → PROCEED (``defer=False``), streak RESET,
        ``log=None`` (the fill/saturation logic logs).
      * ``structured_live`` False (genuinely 0 non-stale lanes): a ``⏳`` claiming
        work with nothing running → BOUNDED defer. ``defer=True`` while
        ``streak < max_defers`` OR ``backlog <= 0`` (nothing to nudge for). At
        ``streak >= max_defers`` WITH ``backlog > 0`` → stop deferring
        (``defer=False``) so the pane reaches the empty-lane nudge path (its own
        cooldown / GOAL_LANE_MAX_NUDGES give-up bound the keystrokes) — never an
        unbounded identical skip loop (the #566 livelock class). Both defer and
        the stop-deferring case journal a greppable ``log``.

    ``streak`` is the caller's NEW persisted defer streak. (#619: the #611
    ``escalated`` field is retired -- the 15-min idle floor it bypassed is gone,
    so the stop-deferring case simply reaches the nudge like any ``defer=False``.)
    """
    if marker != "⏳" or render_waiters > 0:
        return LaneWorkingNoTasks(False, 0, None)  # branch does not fire
    if structured_live:
        return LaneWorkingNoTasks(False, 0, None)  # lanes live -> proceed
    streak = defer_streak + 1
    if backlog > 0 and streak >= max_defers:
        return LaneWorkingNoTasks(
            False, streak,
            "working-no-tasks ESCALATE (%d defers, backlog>0, 0 structured live "
            "lanes -- proceeding to the gated nudge path)" % streak)
    return LaneWorkingNoTasks(
        True, streak,
        "skip:working-no-tasks (⏳ marker, 0 structured live lanes, defer %d/%d)"
        % (streak, max_defers))


LaneLowMemSurface = namedtuple("LaneLowMemSurface", "surface streak surfaced")


def lane_low_mem_surface_decision(*, backlog, min_backlog,
                                  streak, max_streak, already_surfaced):
    """#571 — the persistent-low-mem CAPACITY-CEILING surface decider.

    Called ONLY inside the low-mem skip branch (a low-mem skip is firing THIS
    sweep — so there is no not-low-mem case here; the mem-recovered / box-filled
    episode RESET is a SEPARATE ``_lane_lowmem_reset``). The OOM protection (the
    ``skip:low-mem`` itself and its memory threshold) is UNCHANGED — this decider
    takes no threshold value and only decides whether to ALSO emit the ONE
    owner-facing CAPACITY-CAPPED signal
    (a persistent RAM ceiling is an OWNER DECISION: upgrade the box vs accept a
    lower saturation).

    Verdict (`LaneLowMemSurface(surface, streak, surfaced)` — ``streak`` and
    ``surfaced`` are the caller's NEW persisted episode state):

      * ``already_surfaced``: keep counting, ``surface=False`` (deduped — the
        signal fires EXACTLY once per episode).
      * ``streak+1 >= max_streak`` AND ``backlog >= min_backlog`` AND not yet
        surfaced → ``surface=True`` (a PERSISTENT ceiling, not a transient dip /
        thin backlog), ``surfaced=True``.
      * otherwise (accumulating, or a thin backlog): ``surface=False``.
    """
    streak = streak + 1
    if already_surfaced:
        return LaneLowMemSurface(False, streak, True)
    if streak >= max_streak and backlog >= min_backlog:
        return LaneLowMemSurface(True, streak, True)
    return LaneLowMemSurface(False, streak, False)


# --- #662: the persistent-STUCK -> owner-ALERT decider -------------------------
#
# SILENCE B of the montalu6 9,5h outage: one_glance's `stuck` verdict (the ONLY
# actionable one) was consumed solely by a journal line + the lane KEYSTROKE
# nudge -- which cannot revive a dead / login-dialog-covered session. Nothing
# routed a PERSISTENT structural stuck to an OWNER alert. This PURE decider
# (facts in / verdict out, mutation-lockable in isolation like its two siblings
# above) is the wire: the thin orchestrator `goal._lane_stuck_owner_alert`
# consumes it and records ONE `stuckalert:` signal per episode. #688 (owner
# ruling 2026-08-25) then added `stuckalert:` to SUPPRESSED_ALERT_PREFIXES —
# the structural `stuck` verdict is a heuristic that fires on many
# non-human-needed states, so the send() drops the Discord PING and keeps only
# the machine-channel signal (journal + `suppressed` delivery-log line).

StuckOwnerAlert = namedtuple("StuckOwnerAlert", "alert streak alerted")


def stuck_owner_alert_decision(*, verdict, streak, max_streak, already_alerted):
    """#662 -- decide whether a structurally-confirmed STUCK supervisor session
    has been stuck long enough (the bounded keystroke lane-nudge recovery
    provably failed) to warrant ONE per-episode alert record. (#688: that record
    is now MACHINE-CHANNEL only -- `stuckalert:` was owner-ruled spam and added
    to SUPPRESSED_ALERT_PREFIXES, so send() drops the Discord PING and keeps the
    journal + `suppressed` delivery-log line; this decider is unchanged.)

    Fires ONLY on the actionable ``stuck`` verdict; ANY other verdict RESETS
    the episode (streak 0, alerted False) -- a session that recovered
    (working / awaiting-user / no-backlog / warming / not-armed) is not stuck,
    so a FUTURE stuck episode alarms afresh. Deduped per episode via
    ``already_alerted`` so the alert fires EXACTLY once even though ``stuck``
    re-derives every sweep. Returns ``StuckOwnerAlert(alert, streak, alerted)``
    -- ``streak``/``alerted`` are the caller's NEW persisted episode state.

    The threshold is a STREAK (consecutive stuck sweeps) rather than a single
    reading so a one-off measurement never alarms; combined with one_glance's
    own idle-over-threshold floor (``GOAL_LANE_IDLE_S``), the owner learns of a
    coverage outage minutes after it starts instead of the 9,5h montalu6 never.
    """
    if verdict != "stuck":
        return StuckOwnerAlert(False, 0, False)      # recovered -> reset episode
    streak = streak + 1
    if already_alerted:
        return StuckOwnerAlert(False, streak, True)  # already alerted this episode
    if streak >= max_streak:
        return StuckOwnerAlert(True, streak, True)
    return StuckOwnerAlert(False, streak, False)


LaneGiveupCause = namedtuple("LaneGiveupCause", "cause detail")


def lane_giveup_cause_decision(*, workable, user_waiting, ops_wait, gk,
                               age_s, max_age_s):
    """#693 -- classify WHY the lanes stayed empty at the lane-nudge give-up,
    from the tickets-status partition (the SAME counts the footer renders).
    PURE (facts in / verdict out, mutation-lockable like the deciders above);
    the thin orchestrator `goal._lane_giveup_cause` resolves the facts.

    Causes (owner ruling, #693 ROZHODNUTÉ -- (a)/(b) are NORMAL states, never
    an alarm; (c) is an airuleset-bug signal that stays machine-channel too):

      * ``backlog-exhausted`` -- workable == 0 and no parked buckets: the
        session simply ran out of dispatchable work.
      * ``parked``            -- workable == 0 but U/W/gk > 0: everything
        open is waiting on the owner / a third party / the gatekeeper.
      * ``stall``             -- workable > 0 yet the lanes stayed empty: the
        one genuinely-suspect class (a coverage gap on THIS box).
      * ``unknown``           -- the partition is unreadable (workable None)
        or the cache age is outside the trusted ``[0, max_age_s)`` window
        (`age_s` None, stale at/over the bound, or NEGATIVE -- a future ts
        from clock skew / a corrupt entry; mirrors the #618 reader's own
        ``0 <= age < max`` bound for the SAME cache): classified HONESTLY
        as can't-tell, never guessed toward any other class.

    `detail` always names the raw counts + cache age ("-" for an absent
    bucket -- e.g. `gk` on a full-authority entry), so the journal verdict is
    self-describing. A None parked bucket counts 0 toward the parked SUM but
    renders as "-", keeping the sum honest for the entries the cache writer
    produces (open + user_waiting/ops_wait are written together; gk only on
    reduced authority)."""
    def _w(v):
        return "-" if v is None else str(v)

    detail = "workable=%s U=%s W=%s gk=%s age=%s" % (
        _w(workable), _w(user_waiting), _w(ops_wait), _w(gk),
        ("%dm" % (age_s // 60)) if isinstance(age_s, (int, float)) else "-")
    if not isinstance(workable, int) or not isinstance(age_s, (int, float)) \
            or not (0 <= age_s < max_age_s):
        return LaneGiveupCause("unknown", detail)
    if workable > 0:
        return LaneGiveupCause("stall", detail)
    parked = sum(v for v in (user_waiting, ops_wait, gk) if isinstance(v, int))
    if parked > 0:
        return LaneGiveupCause("parked", detail)
    return LaneGiveupCause("backlog-exhausted", detail)
