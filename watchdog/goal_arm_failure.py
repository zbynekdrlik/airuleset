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
    as what it is: `drop:nudge-off`, decided BEFORE any pane read, nothing typed,
    never a failed verify with a misleading `ARM-CONFIRM-FAIL box=empty`. It is
    TERMINAL and books ONE origin attempt (`_record_delivered_attempt`), so a
    watchdog re-arm recorder stays bounded by its own 24 h attempt cap and then
    falls through to its keystroke-free ping -- the same bound the old
    three-sweep verify-failed drop gave, without typing or a misleading ping;
  * the SELF-CALLBACK failure record + notice -- when a `self-callback` arm is
    dropped at the strict attempt cap, the failure is kept in the watchdog state
    (`airuleset.py status` shows `goal: arm failed (<reason> xN) — paste the
    /goal line /autopilot printed, or re-run /autopilot` until the session arms,
    a new request is pending, or `SHOW_S` passes) and ONE pointer line is typed
    into the pane.

A watchdog re-arm origin is NOT covered by the notice (its failures are bounded
by its own attempt caps and pings). The notice never types a `/goal`, and it
starts with the watchdog's own `stuck-check: ` prefix, so every reader of human
prompts (`_MACHINE_PROMPT_PREFIXES`: the U-count / answered-question prune, the
recent-human gates; `stream_migrate._own_keystroke`: the #1133 answer detector;
the janitor's own-content proof) reads it as machine text, never an answer.

Keystroke safety of the notice. It is typed ONLY into a pane at rest, checked
right before the keystroke with the SAME primitives the arm path uses: not in
copy-mode, no open dialog (`goal._recovery_pane_ready`), a CLEAN idle `input`
boundary with an EMPTY box, no "Waiting for N background agents" render, and no
live turn per the #1110 transcript-liveness gate. `send_verified` then
re-checks the box bare, refuses a spinner render, and submits once. The ONE
gate it does not take is the 30-min recent-human window: the owner typed
`/autopilot` minutes ago, so that window would veto every notice -- the #752
ruling that makes the owner's own arm immune to it (the notice is that arm's
failure report). A refused gate types nothing; the status row still carries it.
The notice is typed ONCE per session and failure window (a later re-arm that
also caps refreshes the status row only), and it addresses the owner and tells
the session to take no action, so it cannot start a re-arm loop. It rides the
always-on `goal-arm` kind of the arm it reports on (the design's "one pointer
line in the pane"), bounded by that once-per-window rule instead of a nudge cap.
"""

import time

import watchdog

STATE_KEY = "goal_arm_failed"
SHOW_S = 24 * 3600                  # the status row stops naming a day-old failure
NOTICE_NUDGE = "goal-arm"           # always-on recovery kind, same as the arm itself
NOTICE = ("stuck-check: goal arm failed {attempts}x. Owner: paste /goal above or "
          "re-run /autopilot. Claude: no action")   # <= 100 cells (#1157)


def nudge_off(sid, cwd, text, nudge, logs, out, log_fn, record_fn=None):
    """The honest disposition of a delivery the per-kind switch withholds: the
    SAME `nudges OFF: suppressed <kind>` journal line the `keys` primitive
    writes, a `DROP nudge-off(<kind>)` goal-sync line, ONE booked origin attempt
    (`record_fn`, None on a dry run) and the terminal `drop:nudge-off`. Zero
    keystrokes, zero pane reads."""
    watchdog._suppress_nudge(nudge, text, logs)
    log_fn("DROP nudge-off(%s) sid=%s cwd=%s (nothing typed)" % (nudge, sid, cwd))
    if record_fn is not None:
        record_fn()
    if out is not None:
        out["detail"] = "nudge kind %s is OFF, nothing typed" % nudge
    return "drop:nudge-off"


def _ts(v):
    """A record's epoch, or None for a missing/corrupt value."""
    try:
        return float(v.get("ts")) if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def record(state, sid, cwd, reason, attempts, now, notice_ts=None):
    """Keep (refresh) the failure for the status row, carrying `notice_ts` (when
    the pane notice was typed this window); prune records past `SHOW_S` (or
    corrupt) so the map stays bounded. A no-op without a state dict."""
    if not isinstance(state, dict) or not sid:
        return
    recs = state.get(STATE_KEY)
    if not isinstance(recs, dict):
        recs = state[STATE_KEY] = {}
    for k in [k for k, v in recs.items()
              if _ts(v) is None or now - _ts(v) > SHOW_S]:
        recs.pop(k, None)
    recs[sid] = {"ts": now, "cwd": cwd, "reason": reason or "?",
                 "attempts": int(attempts), "notice_ts": notice_ts}


def clear(state, sid):
    """A later arm of `sid` landed: the failure is history."""
    if isinstance(state, dict) and isinstance(state.get(STATE_KEY), dict):
        state[STATE_KEY].pop(sid, None)


def failure(sid, now, state=None):
    """The recorded failure of `sid`, or None when there is none, it is older
    than `SHOW_S`, or the session armed AFTER it (a `Goal set` marker newer than
    the record in the persisted `goal_mark` -- the owner pasted the line, or a
    later arm landed). `state` defaults to the PERSISTED watchdog state (the
    status CLI runs outside the sweep); a missing/corrupt store reads as none."""
    if not sid:
        return None
    if state is None:
        from watchdog.decide import load_state
        state = load_state(watchdog.STATE_PATH)
    recs = state.get(STATE_KEY) if isinstance(state, dict) else None
    rec = recs.get(sid) if isinstance(recs, dict) else None
    ts = _ts(rec)
    if ts is None or not 0 <= now - ts <= SHOW_S:
        return None
    gm = (state.get("goal_mark") or {}).get(sid) if isinstance(
        state.get("goal_mark"), dict) else None
    mark = gm.get("mark") if isinstance(gm, dict) else None
    if isinstance(mark, dict) and mark.get("state") == "set" \
            and isinstance(mark.get("ts"), (int, float)) and mark["ts"] > ts:
        return None
    return rec


def status_row(rec):
    """The `status` `goal:` row for a failed owner arm."""
    return ("goal: arm failed (%s x%s) — paste the /goal line /autopilot printed, "
            "or re-run /autopilot" % (rec.get("reason", "?"), rec.get("attempts", "?")))


def _pane_at_rest(sid, cwd, run, projects_dir, now):
    """`(pid, tpath, "")` when the notice may be typed now, else
    `(None, None, <gate>)`. See the module docstring for the gate set."""
    from watchdog import goal as _goal
    from watchdog import goal_turn_liveness as _live
    from watchdog import ops_wait_recheck as _owr
    pid, cap, _loc = _goal._recovery_pane_ready(sid, cwd, run, projects_dir,
                                                now, human_gate=False)
    if pid is None:
        return None, None, cap                       # no-pane / in-mode / dialog
    kind, _draft = watchdog._classify_boundary(cap)
    if kind != "input" or watchdog._input_line_text(cap) != "":
        return None, None, "box not an empty idle input"
    if _owr._pane_busy_waiting(cap):
        return None, None, "busy-waiting"
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    if not tinfo:
        return None, None, "no-transcript"
    if _live.turn_live(_live.transcript_age_s(tinfo[0], now)):
        return None, None, "live-turn"
    return pid, tinfo[0], ""


def on_self_arm_capped(sid, cwd, reason, attempts, run, projects_dir, state,
                       now, sleep_fn):
    """A `self-callback` arm was dropped at the strict attempt cap: record it for
    the status row, then type ONE pointer line into a pane at rest (never a
    `/goal`), once per session and failure window. Returns journal lines; every
    outcome is named (#486)."""
    head = "ARM-FAILED (goal-sweep) %s sid=%s -> " % (watchdog.project_label(cwd),
                                                      sid)
    prev = failure(sid, now, state if state is not None else {}) or {}
    record(state, sid, cwd, reason, attempts, now, notice_ts=prev.get("notice_ts"))
    if prev.get("notice_ts") is not None:
        return [head + "notice already typed this window; status row refreshed"]
    pdir = projects_dir or watchdog.PROJECTS_DIR
    pid, tpath, gate = _pane_at_rest(sid, cwd, run, pdir, now)
    if pid is None:
        return [head + "notice not typed (%s); status row set" % gate]
    slog = []
    res = watchdog.send_verified(
        pid, NOTICE.format(attempts=attempts), run=run, tpath=tpath,
        sleep_fn=sleep_fn or time.sleep, logs=slog, nudge=NOTICE_NUDGE,
        state=state, now=now)
    kind = getattr(res, "kind", res)
    if kind != "not-typed":                          # a key reached the pane
        record(state, sid, cwd, reason, attempts, now, notice_ts=now)
    return [head + "pane notice %s; status row set" % kind] + slog
