"""#1203 — Job 54 MODEL RESTORE: undo a Claude Code model FALLBACK in a managed
pane, confirm it, and journal the fallback window whose work needs a re-review.

The incident (iemmixer on dev1, 28.9.2026): an Opus 5.5 safeguard stop switched
the session onto `claude-opus-4-8`, and it kept working for two days (78
commits, 4 PRs); nothing noticed. #1203 answers with three parts — the footer
shows the versioned model (`opus4.8 FALLBACK`), the PreToolUse gate
`block-model-fallback.sh` stops every tool call of that MAIN session, and this
job puts the session back:

  1. FALLBACK seen (`model_fallback.tail_state` + `verdict`, a bounded tail read
     of the pane's transcript) on a recognised Claude model → type
     `/model <MANAGED_MODEL>` (#1246: or the main model the pane's project
     declares, `model_lineup.main_models_for_transcript`; that project may also
     run MANAGED_MODEL) into the pane through verified delivery
     (`send_verified`, which confirms the `<command-name>/model` composite in the
     transcript). The pane must show a bare idle prompt and pass the recent-human
     veto, but NOT the #1110 transcript-liveness gate: the gate blocks every tool
     call, so a "live" turn there is only a /goal loop bouncing off the block, and
     waiting for it to go quiet would wait forever.
  2. `/model <managed>` recorded after the last reply, no reply yet → after
     RESUME_GRACE_S, type ONE short resume line (the session was stopped by the
     gate mid-work), this time only into a genuinely quiet pane (full gate set).
  3. The next reply runs on the managed model → CONFIRMED: ONE `RESTORED`
     journal line naming the fallback window, whose work needs a re-review.
     #1137: no ticket is filed. A reply still on the fallback model → back to
     step 1 (bounded).

The word "fallback" (and the re-review note) needs PROOF: a Claude Code
fallback marker in the tail or, once per episode, in a deeper backwards scan
(`model_fallback.find_marker`, DEEP_BYTES). A session that simply runs an
older model with no marker in reach is OFF-LINEUP: `/model` restores it (the
gate blocks it too) and the journal says so. The #1060 implementer window (its
session id, or a pane whose claude runs with `AIRULESET_ROLE=implementer`) is
never typed into. State is written into the sweep `state` as it changes.

Bounds (it is an always-on RECOVERY kind, `model-restore` in
RECOVERY_NUDGE_KINDS, so the per-kind floor and the total cap do not apply):
MAX_ATTEMPTS `/model` typings per fallback episode, RETRY_S apart; one resume
line per episode. A model the job does not recognise as Claude (`short_name`
== "", e.g. the #1060 implementer window's gateway alias) is never touched. A
dry-run types nothing.

State: `state["model_restore"] = {sid: {...}}`, pruned KEEP_S after the episode
ends or its pane is gone.
"""
import os
import time

NUDGE_KIND = "model-restore"
STATE_KEY = "model_restore"
MAX_ATTEMPTS = 3
RETRY_S = 180
RESUME_GRACE_S = 90
KEEP_S = 7 * 24 * 3600
MIN_BUDGET_S = 30
RESUME_TEXT = ("Model je späť na {mshort} po fallbacku (airuleset #1203) — "
               "pokračuj v práci.")


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _new_record(st, v, cwd, now, marker=None):
    marker = marker if isinstance(marker, dict) else None
    since = marker.get("timestamp") if marker else None
    return {"cwd": cwd, "proven": marker is not None,
            "from": (marker or {}).get("from") or v["managed"],
            "to": (marker or {}).get("to") or v["model"],
            "since": since if isinstance(since, str) and since else _iso(now),
            "since_exact": bool(since), "first_seen": now,
            "attempts": 0, "last_try": None, "cmd_at": None, "resumed_at": None,
            "restored_at": None, "gave_up": False, "touched": now}


def same_model(a, b):
    import model_fallback
    return model_fallback.same_model(a, b)


def restore_job(now, state, panes, *, managed, find_transcript, read_state,
                verdict, short_name, ready, deliver, outcome_kind, delivered, typed_not_delivered,
                dry_run=False, handled=None, find_marker=None,
                implementer_sid=None, pane_is_implementer=None, models_for=None):
    """One pass over the sweep's claude panes; returns journal lines.

    Injected deps (production wiring in `run_job`):
      find_transcript(cwd, exclude_sid) -> tpath | None (never the implementer's)
      read_state(tpath) -> model_fallback.tail_state dict | None (may raise OSError)
      verdict(st, managed) / short_name(model)   (model_fallback)
      ready(pid, cwd, live_ok) -> (ok, why, sid, tpath)   the idle-pane gates
      deliver(pid, tpath, text) -> SendOutcome
      find_marker(tpath, managed) -> marker | None   the deeper proof scan
      implementer_sid() -> str | None           the box's implementer session
      pane_is_implementer(pid) -> True/False/None (None = cannot tell)
      models_for(tpath) -> (target, allowed)    #1246 the pane project's main
          model (restore target) and every model it may run; default: `managed`
    """
    logs = []
    old = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    store = {k: dict(r) for k, r in old.items() if isinstance(r, dict)}
    if not dry_run:
        state[STATE_KEY] = store          # written through: a killed sweep keeps it
    impl_sid = implementer_sid() if implementer_sid is not None else None
    if models_for is None:
        def models_for(_tpath):
            return managed, (managed,)
    seen = set()
    for pid, cwd in panes:
        if not cwd:
            continue
        tpath = find_transcript(cwd, impl_sid)
        if not tpath:
            continue
        sid = os.path.basename(str(tpath))
        sid = sid[:-len(".jsonl")] if sid.endswith(".jsonl") else sid
        if sid in seen:
            continue
        skip = _implementer_skip(sid, pid, impl_sid, pane_is_implementer)
        if skip:           # never typed into -- and never takes the main pane's slot
            logs.append("model-restore: %s -> %s %s -- never touched" % (sid, pid, skip))
            continue
        seen.add(sid)
        try:
            st = read_state(tpath)
        except OSError as exc:
            logs.append("model-restore: %s transcript unreadable (%r) -- skip" % (sid, exc))
            continue
        target, allowed = models_for(tpath)
        v = verdict(st, target, allowed=allowed)
        rec = store.get(sid)
        if v is None:
            if rec is not None:
                logs += _healthy(now, sid, pid, cwd, st, rec, target, short_name,
                                 ready, deliver, outcome_kind, dry_run, handled,
                                 allowed=allowed)
            continue
        if not short_name(v["model"]):
            logs.append("model-restore: %s runs %r (not a recognised Claude model) "
                        "-- never touched" % (sid, v["model"]))
            continue
        if rec is None or rec.get("restored_at"):
            # a fallback after a CONFIRMED restore is a new episode (a new
            # re-review window), even when it lands on the same model again
            marker = st.get("marker") if isinstance(st, dict) else None
            if marker is None and find_marker is not None:
                try:
                    marker = find_marker(tpath, target)
                except OSError as exc:
                    logs.append("model-restore: %s deep marker scan failed (%r)" % (sid, exc))
            rec = store[sid] = _new_record(st, v, cwd, now, marker)
            if rec["proven"]:
                logs.append("model-restore: %s FALLBACK %s -> %s (since %s) [%s]"
                            % (sid, rec["from"], rec["to"], rec["since"], cwd))
            else:
                logs.append("model-restore: %s OFF-LINEUP runs %s, not %s (no fallback "
                            "marker in reach, first seen %s) -- restoring [%s]"
                            % (sid, rec["to"], target, rec["since"], cwd))
        rec["touched"] = now
        rec["cmd_at"] = None
        logs += _type_model(now, sid, pid, cwd, rec, target, ready, deliver,
                            outcome_kind, delivered, typed_not_delivered,
                            dry_run, handled)
    if not dry_run:
        state[STATE_KEY] = {k: r for k, r in store.items() if _keep(k, r, seen, now)}
    return logs


def _implementer_skip(sid, pid, impl_sid, pane_is_implementer):
    """Why this pane must never be typed into as the #1060 implementer, or ""."""
    if impl_sid is None:
        return ""                             # not an implementer box
    if sid == impl_sid:
        return "implementer session"
    if pane_is_implementer is None:
        return ""
    role = pane_is_implementer(pid)
    if role is None:
        return "pane role unknown on an implementer box"
    return "implementer pane" if role else ""


def _done(rec):
    return bool(rec.get("restored_at"))


def _keep(sid, rec, seen, now):
    """A live episode is kept while its pane exists; a finished (restored) one
    or an orphan only until KEEP_S after its last change."""
    if sid in seen and not _done(rec):
        return True
    touched = _num(rec.get("touched"))
    return touched is not None and 0 <= now - touched < KEEP_S


def _type_model(now, sid, pid, cwd, rec, managed, ready, deliver, outcome_kind,
                delivered, typed_not_delivered, dry_run, handled):
    if rec["attempts"] >= MAX_ATTEMPTS:
        if rec.get("gave_up"):
            return []
        rec["gave_up"] = True
        return ["model-restore: %s still on %s after %d `/model` attempts -- GAVE UP "
                "(the hook keeps blocking; type `/model %s` by hand)"
                % (sid, rec["to"], rec["attempts"], managed)]
    last = _num(rec.get("last_try"))
    if last is not None and 0 <= now - last < RETRY_S:
        return ["model-restore: %s hold:retry (%ds since attempt %d)"
                % (sid, now - last, rec["attempts"])]
    ok, why, _sid, tpath = ready(pid, cwd, True)
    if not ok:
        return ["model-restore: %s -> %s hold:%s" % (sid, pid, why)]
    text = "/model %s" % managed
    if dry_run:
        return ["model-restore: %s -> %s would type `%s` [dry-run]" % (sid, pid, text)]
    if handled is not None:
        handled.add(sid)
    try:
        outcome = deliver(pid, tpath, text)
    except Exception as exc:  # noqa: BLE001 -- keys may be in: count the attempt
        outcome = None
        err = " raised %r" % exc
    else:
        err = ""
    kind = outcome_kind(outcome)
    if kind in delivered or kind in typed_not_delivered:
        rec["attempts"] += 1
        rec["last_try"] = now
    return ["model-restore: %s -> %s typed `%s` -> %s%s (attempt %d/%d)"
            % (sid, pid, text, kind, err, rec["attempts"], MAX_ATTEMPTS)]


def _healthy(now, sid, pid, cwd, st, rec, managed, short_name, ready, deliver,
             outcome_kind, dry_run, handled, allowed=()):
    """The pane's verdict is no longer a fallback: either a `/model <managed>`
    waits for its first reply (resume it), or a reply on the managed model (or
    another `allowed` one, #1246) exists (confirmed → the RESTORED line)."""
    logs = []
    if _done(rec):
        return logs
    rec["touched"] = now
    if not rec.get("restored_at") and (st or {}).get("model_cmd"):
        if _num(rec.get("cmd_at")) is None:
            rec["cmd_at"] = now
            return ["model-restore: %s switched to `%s`, awaiting the first reply"
                    % (sid, st["model_cmd"])]
        if rec.get("resumed_at") or now - rec["cmd_at"] < RESUME_GRACE_S:
            return []
        ok, why, _sid, tpath = ready(pid, cwd, False)
        if not ok:
            return ["model-restore: %s resume hold:%s" % (sid, why)]
        text = RESUME_TEXT.format(mshort=short_name(managed) or managed)
        if dry_run:
            return ["model-restore: %s would type the resume line [dry-run]" % sid]
        if handled is not None:
            handled.add(sid)
        try:
            kind = outcome_kind(deliver(pid, tpath, text))
        except Exception as exc:  # noqa: BLE001 -- at-most-once: never retype
            kind = "error %r" % (exc,)
        if not kind.startswith("not-typed"):
            rec["resumed_at"] = now          # keys went in: the one resume slot is used
        return ["model-restore: %s -> %s resume line -> %s" % (sid, pid, kind)]
    if not (st and any(same_model(st.get("model"), m)
                       for m in (managed,) + tuple(allowed))):
        return logs                         # no reply on the managed model yet
    rec["restored_at"] = now
    logs.append("model-restore: %s RESTORED -- the next reply runs on %s "
                "(%s %s -> %s since %s%s)%s [%s]"
                % (sid, (st or {}).get("model") or managed,
                   "fallback" if rec.get("proven") else "off-lineup",
                   rec["from"], rec["to"], rec["since"],
                   "" if rec.get("since_exact") else ", first seen",
                   "; re-review the work done on the fallback in that window"
                   if rec.get("proven") else "", rec.get("cwd") or cwd))
    return logs


def run_job(now, state, panes, *, run, sleep_fn, projects_dir, dry_run=False,
            handled=None, budget_left=None):
    """The production wiring: the shared `watchdog.idle_pane` gates + delivery
    (resolved at call time so every `watchdog.<name>` seam stays effective),
    `model_fallback` for the verdict, `model_lineup.MANAGED_MODEL`. Held while
    the sweep has less than MIN_BUDGET_S left."""
    import model_fallback
    import model_lineup
    from watchdog import idle_pane
    left = budget_left() if budget_left is not None else None
    if left is not None and left < MIN_BUDGET_S:
        return ["model-restore: hold:budget (%.0fs left)" % left]
    jl = []
    ready_deps, deliver, _mark_sent = idle_pane.production_deps(
        now, state, NUDGE_KIND, run=run, sleep_fn=sleep_fn,
        budget_left=budget_left, journal=jl)

    def ready(pid, cwd, live_ok):
        deps = dict(ready_deps)
        if live_ok:
            deps["turn_live"] = lambda _tpath: False    # see the module docstring
        return idle_pane.pane_ready(pid, cwd, NUDGE_KIND, projects_dir=projects_dir,
                                    handled=handled, state=state, now=now, **deps)

    def find(cwd, exclude_sid=None):
        return newest_transcript(projects_dir, cwd, exclude_sid)

    def implementer_sid():
        try:
            with open(os.path.expanduser("~/.claude/airuleset-implementer-session"),
                      encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            return None

    logs = restore_job(
        now, state, panes, managed=model_lineup.MANAGED_MODEL, find_transcript=find,
        models_for=model_lineup.main_models_for_transcript,
        read_state=model_fallback.tail_state, verdict=model_fallback.verdict,
        short_name=model_fallback.short_name, ready=ready, deliver=deliver,
        outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
        typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED,
        dry_run=dry_run, handled=handled, find_marker=deep_marker,
        implementer_sid=implementer_sid,
        pane_is_implementer=lambda pid: pane_is_implementer(pid, run))
    return logs + jl


def deep_marker(tpath, managed):
    """The production `find_marker(tpath, managed)` seam: the deep scan stops
    at a later reply on `managed` (#1246: the model is `stop_model`, never the
    positional `max_bytes` -- passing it there raised TypeError on every sweep
    since #1203)."""
    import model_fallback
    return model_fallback.find_marker(tpath, stop_model=managed)


def newest_transcript(projects_dir, cwd, exclude_sid=None):
    """The pane's session transcript: the newest `*.jsonl` in its cwd's project
    dir (`find_active_transcript`), but never the #1060 implementer's own
    session, which shares the cwd with the main window -- then the next newest."""
    import watchdog
    tinfo = watchdog.find_active_transcript(projects_dir, cwd)
    tpath = tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo
    skip = (exclude_sid or "") + ".jsonl"
    if not tpath or not exclude_sid or os.path.basename(str(tpath)) != skip:
        return tpath
    d = os.path.dirname(str(tpath))
    try:
        rest = sorted((os.path.join(d, n) for n in os.listdir(d)
                       if n.endswith(".jsonl") and n != skip),
                      key=os.path.getmtime, reverse=True)
    except OSError:
        return None
    return rest[0] if rest else None


def pane_is_implementer(pid, run=None):
    """True when the pane's claude process runs with `AIRULESET_ROLE=implementer`
    (the #1060 `claude-impl` launcher), False when it does not, None when the
    process or its environment cannot be read."""
    import watchdog
    run = run or watchdog._default_run
    ppid = (run(["tmux", "display-message", "-p", "-t", pid, "#{pane_pid}"]) or "").strip()
    cpid = watchdog._pane_claude_pid(ppid) if ppid.isdigit() else None
    if not cpid:
        return None
    try:
        with open("/proc/%s/environ" % cpid, "rb") as fh:
            return b"AIRULESET_ROLE=implementer" in fh.read().split(b"\0")
    except OSError:
        return None
