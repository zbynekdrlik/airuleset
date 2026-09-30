"""The ONE readiness gate set + delivery wiring for an idle-pane machine nudge
that a sweep job types into ONE known pane (#1163 Job 52 watch triggers, #1176
Job 53 checkout-lag notice).

#1163's lesson: a new idle-pane keystroke job must adopt the WHOLE
sibling-rider gate set, and both reviews' 🔴 was the one gate it missed. So the
set lives here, once: the transcript (a session exists), the sweep's
typed-this-sweep set, copy-mode, the idle render, the "Waiting for N
background agents" line (`ops_wait_recheck._pane_busy_waiting` — a pane
waiting on its own worker renders a bare `❯`), the recent-human veto, the
#1110 transcript liveness and the `nudge_gate` per-kind floor + total cap.
The delivery wiring (`production_deps`) is the sibling-rider contract: janitor
watch mark/clear around `send_verified`, the outcome threaded back, and a
skipped confirm wait when the sweep budget is short.
"""
import os
import time

# `SendOutcome.kind` values meaning an Enter went into the pane (at-most-once:
# never type the same message again) / keys reached the pane without a submit
# (floor the retry).
DELIVERED = ("submitted", "delivered-unconfirmed", "unconfirmed", "error")
TYPED_NOT_DELIVERED = ("typed-undone", "typed-stranded", "swallowed")


def outcome_kind(outcome):
    """The `SendOutcome.kind` of a delivery return (a bool from a fake or an
    older primitive maps to submitted / not-typed; a raise is `error`)."""
    return (getattr(outcome, "kind", None)
            or ("error" if outcome is None else
                "submitted" if outcome else "not-typed"))


def pane_ready(pid, cwd, kind, *, projects_dir, find_transcript, capture,
               in_mode, at_idle, busy_waiting, turn_live, recent_human,
               gate_ok, handled, state, now):
    """`(ready, why, sid, tpath)` for ONE pane: every gate a sibling idle-pane
    rider applies, cheapest first."""
    tinfo = find_transcript(projects_dir, cwd)
    tpath = tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo
    if not tpath:
        return False, "no-transcript", None, None
    sid = os.path.basename(str(tpath))
    sid = sid[:-len(".jsonl")] if sid.endswith(".jsonl") else sid
    if handled is not None and sid in handled:
        return False, "handled-this-sweep", sid, tpath
    if in_mode(pid):
        return False, "in-mode", sid, tpath
    captured = capture(pid)
    if not at_idle(captured):
        return False, "busy-pane", sid, tpath
    if busy_waiting(captured):
        return False, "busy-waiting", sid, tpath   # waiting on its own worker
    if recent_human(sid, cwd, tpath, pid):
        return False, "recent-human", sid, tpath
    if turn_live(tpath):
        return False, "live-turn", sid, tpath      # #1110 transcript liveness
    if not gate_ok(state, sid, kind, now):
        return False, "floor", sid, tpath
    return True, "", sid, tpath


def production_deps(now, state, kind, *, run, sleep_fn, budget_left, journal):
    """`(ready_deps, deliver, mark_sent)` — the real primitives, resolved at
    call time through the `watchdog` package (the back-reference convention,
    so every `watchdog.<name>` test seam stays effective). `ready_deps` are
    the `pane_ready` keyword gates; `deliver(pid, tpath, text)` returns the
    `SendOutcome`; `journal` collects `send_verified`'s own lines."""
    import watchdog
    from watchdog import goal as _goal
    from watchdog import goal_turn_liveness as _live
    from watchdog import nudge_gate
    from watchdog import ops_wait_recheck as _owr
    from watchdog import queue_arrival_recheck as _qa

    def deliver(pid, tpath, text):
        watchdog._janitor_mark_watch(state, pid, now)
        left = budget_left() if budget_left is not None else None
        outcome = watchdog.send_verified(
            pid, text, run, tpath, sleep_fn=sleep_fn, logs=journal, out={},
            nudge=kind, state=state, now=now,
            skip_confirm=(left is not None
                          and left < _qa.QUEUE_ARRIVAL_CONFIRM_MIN_BUDGET_S))
        if outcome or getattr(outcome, "kind", "") == "not-typed":
            watchdog._janitor_clear_watch(state, pid)    # nothing left to watch
        return outcome

    def recent_human(sid, cwd, tpath, pid):
        return _goal._recovery_recent_human(sid, cwd, tpath, now, pid=pid, run=run)

    ready_deps = {
        "find_transcript": watchdog.find_active_transcript,
        "capture": lambda pid: watchdog.capture_pane(pid, run),
        "in_mode": lambda pid: watchdog.pane_in_mode(pid, run),
        "at_idle": watchdog.pane_at_idle_prompt,
        "busy_waiting": _owr._pane_busy_waiting,
        # the WALL clock, never the sweep-start `now`: these jobs run late in
        # the sweep, so a transcript written after `now` would read as not live
        "turn_live": lambda tpath: _live.turn_live(
            _live.transcript_age_s(tpath, max(now, time.time()))),
        "recent_human": recent_human,
        "gate_ok": nudge_gate.gate_ok,
    }
    return ready_deps, deliver, nudge_gate.mark_sent
