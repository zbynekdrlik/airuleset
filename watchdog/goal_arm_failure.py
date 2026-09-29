"""watchdog/goal_arm_failure.py -- #1181: a `/goal` arm the owner asked for must
never fail in silence.

Incident (dev1 iemmixer + fohmixer 29.9.2026, the controller since 22.9.): the
owner's `/autopilot` callback (`goal-arm --self`, origin `self-callback`) was
refused by the staged machine-nudge switch on every non-declared pane: nothing
was typed, `deliver_goal` reported `skip:verify-failed`, and after the strict
attempt cap the request was dropped. The owner learned it only by looking at a
dark footer. The root fix lives in `goal._declared_window_nudge` (the owner's
own arm rides the always-on `goal-arm` kind); this leaf owns the two things
that keep ANY remaining failure visible:

  * `nudge_off` -- a delivery the owner's per-kind switch withholds is reported
    as what it is (`skip:nudge-off`, nothing typed), never as a failed verify
    with a misleading `ARM-CONFIRM-FAIL box=empty` diagnostic;
  * the SELF-CALLBACK failure record + notice -- when a `self-callback` arm is
    dropped at the strict attempt cap, ONE pointer line lands in the pane
    (`send_verified`, the one verified keystroke primitive) and the failure is
    kept in the watchdog state, so `airuleset.py status` shows
    `goal: arm failed (<reason> xN) — paste the line above or re-run /autopilot`
    until the pane arms, a new request is pending, or `SHOW_S` passes.

A watchdog re-arm origin is NOT covered by the notice: it is the watchdog's own
guess, bounded by its own caps and pings. The notice never types a `/goal`.

Keystroke safety of the pointer line: it follows the `self-callback` arm's own
ruling (#752, owner 2026-08-30 -- the owner having just typed `/autopilot` is
never a reason to defer), so it is NOT client-active vetoed; `send_verified`
still types only into a provably BARE box (a draft is rescued and the send
aborted), refuses a busy/spinner pane, and submits once (one corrective
Escape+Enter only while our own text is provably still in the box).
"""

import time

import watchdog

STATE_KEY = "goal_arm_failed"
SHOW_S = 24 * 3600                  # the status row stops naming a day-old failure
NOTICE_NUDGE = "goal-arm"           # always-on recovery kind, same as the arm itself
NOTICE = ("goal-arm failed ({reason} x{attempts}): paste the /goal line above "
          "or re-run /autopilot -- watchdog notice, no reply needed")


def nudge_off(sid, cwd, text, nudge, logs, out, log_fn):
    """The honest disposition of a delivery the per-kind switch withholds: the
    SAME `nudges OFF: suppressed <kind>` journal line the `keys` primitive
    writes, a `SKIP nudge-off(<kind>)` goal-sync line, and `skip:nudge-off`.
    Zero keystrokes, zero pane reads."""
    watchdog._suppress_nudge(nudge, text, logs)
    log_fn("SKIP nudge-off(%s) sid=%s cwd=%s (nothing typed)" % (nudge, sid, cwd))
    if out is not None:
        out["detail"] = "nudge kind %s is OFF, nothing typed" % nudge
    return "skip:nudge-off"


def record(state, sid, cwd, reason, attempts, now):
    """Keep the failure for the status row; prune records past `SHOW_S` so the
    map stays bounded. A no-op without a state dict."""
    if not isinstance(state, dict) or not sid:
        return
    recs = state.get(STATE_KEY)
    if not isinstance(recs, dict):
        recs = state[STATE_KEY] = {}
    for k in [k for k, v in recs.items()
              if not isinstance(v, dict) or now - float(v.get("ts") or 0) > SHOW_S]:
        recs.pop(k, None)
    recs[sid] = {"ts": now, "cwd": cwd, "reason": reason or "?",
                 "attempts": int(attempts)}


def clear(state, sid):
    """A later arm of `sid` landed: the failure is history."""
    if isinstance(state, dict) and isinstance(state.get(STATE_KEY), dict):
        state[STATE_KEY].pop(sid, None)


def failure(sid, now, state=None):
    """The recorded failure of `sid` if it is younger than `SHOW_S`, else None.
    `state` defaults to the PERSISTED watchdog state (the status CLI runs
    outside the sweep); a missing/corrupt store reads as no failure."""
    if not sid:
        return None
    if state is None:
        from watchdog.decide import load_state
        state = load_state(watchdog.STATE_PATH)
    recs = state.get(STATE_KEY) if isinstance(state, dict) else None
    rec = recs.get(sid) if isinstance(recs, dict) else None
    if not isinstance(rec, dict):
        return None
    try:
        age = now - float(rec.get("ts"))
    except (TypeError, ValueError):
        return None
    return rec if 0 <= age <= SHOW_S else None


def status_row(rec):
    """The `status` `goal:` row for a failed owner arm."""
    return ("goal: arm failed (%s x%s) — paste the line above or re-run "
            "/autopilot" % (rec.get("reason", "?"), rec.get("attempts", "?")))


def on_self_arm_capped(sid, cwd, reason, attempts, run, projects_dir, state,
                       now, sleep_fn):
    """A `self-callback` arm was dropped at the strict attempt cap: record it,
    then type ONE pointer line into the pane (never a `/goal`). Returns journal
    lines; every outcome is named (#486)."""
    from watchdog import compact
    record(state, sid, cwd, reason, attempts, now)
    loc = watchdog.project_label(cwd)
    pid = compact._find_pane_for_session(sid, cwd, run=run,
                                         projects_dir=projects_dir)
    tinfo = watchdog.find_active_transcript(
        projects_dir or watchdog.PROJECTS_DIR, cwd)
    if not pid or not tinfo:
        return ["ARM-FAILED (goal-sweep) %s sid=%s -> notice not typed (no "
                "pane/transcript); status row set" % (loc, sid)]
    slog = []
    res = watchdog.send_verified(
        pid, NOTICE.format(reason=reason or "?", attempts=attempts), run=run,
        tpath=tinfo[0], sleep_fn=sleep_fn or time.sleep, logs=slog,
        nudge=NOTICE_NUDGE, state=state, now=now)
    return ["ARM-FAILED (goal-sweep) %s sid=%s -> pane notice %s; status row "
            "set" % (loc, sid, getattr(res, "kind", res))] + slog
