"""#1203 — Job 54 MODEL RESTORE: undo a Claude Code model FALLBACK in a managed
pane, confirm it, and file a re-review ticket for the work done on the fallback.

The incident (iemmixer on dev1, 28.9.2026): an Opus 5.5 safeguard stop switched
the session onto `claude-opus-4-8`, and it kept working for two days (78
commits, 4 PRs); nothing noticed. #1203 answers with three parts — the footer
shows the versioned model (`opus4.8 FALLBACK`), the PreToolUse gate
`block-model-fallback.sh` stops every tool call of that MAIN session, and this
job puts the session back:

  1. FALLBACK seen (`model_fallback.tail_state` + `verdict`, a bounded tail read
     of the pane's transcript) on a recognised Claude model → type
     `/model <MANAGED_MODEL>` into the pane through verified delivery
     (`send_verified`, which confirms the `<command-name>/model` composite in the
     transcript). The pane must show a bare idle prompt and pass the recent-human
     veto, but NOT the #1110 transcript-liveness gate: the gate blocks every tool
     call, so a "live" turn there is only a /goal loop bouncing off the block, and
     waiting for it to go quiet would wait forever.
  2. `/model <managed>` recorded after the last reply, no reply yet → after
     RESUME_GRACE_S, type ONE short resume line (the session was stopped by the
     gate mid-work), this time only into a genuinely quiet pane (full gate set).
  3. The next reply runs on the managed model → CONFIRMED: journal it, and file
     ONE re-review ticket in the pane's own project repo (its native `gh`, run in
     the pane's cwd) naming the fallback window and the commits made in it.
     A reply still on the fallback model → back to step 1 (bounded).

Bounds (it is an always-on RECOVERY kind, `model-restore` in
RECOVERY_NUDGE_KINDS, so the per-kind floor and the total cap do not apply):
MAX_ATTEMPTS `/model` typings per fallback episode, RETRY_S apart; one resume
line per episode; FILE_MAX_TRIES ticket attempts. A model the job does not
recognise as Claude (`short_name` == "", e.g. the #1060 implementer window's
gateway alias) is never touched. A dry-run types and files nothing.

State: `state["model_restore"] = {sid: {...}}`, pruned KEEP_S after the episode
ends or its pane is gone.
"""
import os
import subprocess
import time

NUDGE_KIND = "model-restore"
STATE_KEY = "model_restore"
MAX_ATTEMPTS = 3
RETRY_S = 180
RESUME_GRACE_S = 90
FILE_MAX_TRIES = 3
KEEP_S = 7 * 24 * 3600
MIN_BUDGET_S = 30
MAX_COMMITS_LISTED = 30
RESUME_TEXT = ("Model je späť na {mshort} po fallbacku (airuleset #1203) — "
               "pokračuj v práci.")


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _episode(st, v):
    """The episode key: the fallback marker's uuid when it is in the tail, else
    the fallback model itself (a session already on it when first seen)."""
    marker = st.get("marker") if isinstance(st, dict) else None
    if isinstance(marker, dict) and marker.get("uuid"):
        return "marker:%s" % marker["uuid"]
    return "model:%s" % v["model"]


def _new_record(st, v, cwd, now):
    marker = st.get("marker") if isinstance(st.get("marker"), dict) else None
    since = marker.get("timestamp") if marker else None
    return {"episode": _episode(st, v), "cwd": cwd,
            "from": (marker or {}).get("from") or v["managed"],
            "to": (marker or {}).get("to") or v["model"],
            "since": since if isinstance(since, str) and since else _iso(now),
            "since_exact": bool(since), "first_seen": now,
            "attempts": 0, "last_try": None, "cmd_at": None, "resumed_at": None,
            "restored_at": None, "ticket": None, "file_tries": 0,
            "gave_up": False, "touched": now}


def restore_job(now, state, panes, *, managed, find_transcript, read_state,
                verdict, short_name, ready, deliver, file_ticket,
                outcome_kind, delivered, typed_not_delivered,
                dry_run=False, handled=None):
    """One pass over the sweep's claude panes; returns journal lines.

    Injected deps (production wiring in `run_job`):
      find_transcript(cwd) -> tpath | None
      read_state(tpath) -> model_fallback.tail_state dict | None (may raise OSError)
      verdict(st, managed) / short_name(model)   (model_fallback)
      ready(pid, cwd, live_ok) -> (ok, why, sid, tpath)   the idle-pane gates
      deliver(pid, tpath, text) -> SendOutcome
      file_ticket(rec, managed, now) -> (ok, ref, err)
    """
    logs = []
    store = state.get(STATE_KEY) if isinstance(state.get(STATE_KEY), dict) else {}
    store = {k: dict(r) for k, r in store.items() if isinstance(r, dict)}
    seen = set()
    for pid, cwd in panes:
        if not cwd:
            continue
        tpath = find_transcript(cwd)
        if not tpath:
            continue
        sid = os.path.basename(str(tpath))
        sid = sid[:-len(".jsonl")] if sid.endswith(".jsonl") else sid
        if sid in seen:
            continue
        seen.add(sid)
        try:
            st = read_state(tpath)
        except OSError as exc:
            logs.append("model-restore: %s transcript unreadable (%r) -- skip" % (sid, exc))
            continue
        v = verdict(st, managed)
        rec = store.get(sid)
        if v is None:
            if rec is not None:
                logs += _healthy(now, sid, pid, cwd, st, rec, managed, short_name,
                                 ready, deliver, file_ticket, outcome_kind,
                                 delivered, dry_run, handled)
            continue
        if not short_name(v["model"]):
            logs.append("model-restore: %s runs %r (not a recognised Claude model) "
                        "-- never touched" % (sid, v["model"]))
            continue
        if rec is None or rec.get("restored_at"):
            # a fallback after a CONFIRMED restore is a new episode (a new
            # re-review ticket), even when it lands on the same model again
            rec = store[sid] = _new_record(st, v, cwd, now)
            logs.append("model-restore: %s FALLBACK %s -> %s (since %s) [%s]" % (
                sid, rec["from"], rec["to"], rec["since"], cwd))
        rec["touched"] = now
        rec["cmd_at"] = None
        logs += _type_model(now, sid, pid, cwd, rec, managed, ready, deliver,
                            outcome_kind, delivered, typed_not_delivered,
                            dry_run, handled)
    if not dry_run:
        state[STATE_KEY] = {k: r for k, r in store.items() if _keep(k, r, seen, now)}
    return logs


def _done(rec):
    return bool(rec.get("restored_at")) and (
        bool(rec.get("ticket")) or rec.get("file_tries", 0) >= FILE_MAX_TRIES)


def _keep(sid, rec, seen, now):
    """A live episode is kept while its pane exists; a finished one (restored +
    ticket settled) or an orphan only until KEEP_S after its last change."""
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
             file_ticket, outcome_kind, delivered, dry_run, handled):
    """The pane's verdict is no longer a fallback: either a `/model <managed>`
    waits for its first reply (resume it), or a reply on the managed model
    exists (confirmed → re-review ticket)."""
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
        rec["resumed_at"] = now
        return ["model-restore: %s -> %s resume line -> %s" % (sid, pid, kind)]
    if not rec.get("restored_at"):
        rec["restored_at"] = now
        logs.append("model-restore: %s RESTORED -- the next reply runs on %s "
                    "(fallback %s -> %s since %s)"
                    % (sid, (st or {}).get("model") or managed, rec["from"],
                       rec["to"], rec["since"]))
    if rec.get("ticket") or rec.get("file_tries", 0) >= FILE_MAX_TRIES or dry_run:
        return logs
    rec["file_tries"] = rec.get("file_tries", 0) + 1
    try:
        ok, ref, err = file_ticket(rec, managed, now)
    except Exception as exc:  # noqa: BLE001 -- retried next sweep, bounded
        ok, ref, err = False, None, repr(exc)
    if ok:
        rec["ticket"] = ref or "filed"
        logs.append("model-restore: %s re-review ticket filed: %s" % (sid, ref))
    else:
        logs.append("model-restore: %s re-review ticket NOT filed (try %d/%d): %s"
                    % (sid, rec["file_tries"], FILE_MAX_TRIES, err))
    return logs


def commit_range(cwd, since, until, sub_run=None):
    """`[(sha, subject)]` of the commits on this checkout's local branches in the
    fallback window, oldest first (`git log --branches --since --until`)."""
    sub_run = sub_run or subprocess.run
    r = sub_run(["git", "log", "--branches", "--reverse", "--since=%s" % since,
                 "--until=%s" % until, "--format=%h\t%s"],
                cwd=cwd, capture_output=True, text=True, timeout=30)
    if getattr(r, "returncode", 1) != 0:
        return None
    out = []
    for line in (getattr(r, "stdout", "") or "").splitlines():
        sha, _, subject = line.partition("\t")
        if sha:
            out.append((sha, subject))
    return out


def compose_ticket(rec, managed, now, commits):
    """`(title, body)` of the re-review ticket."""
    until = _iso(rec.get("restored_at") or now)
    title = "Re-review work done on fallback model %s (%s .. %s)" % (
        rec["to"], rec["since"], until)
    lines = [
        "Auto-filed by the airuleset watchdog (kind `model-restore`, "
        "zbynekdrlik/airuleset#1203).", "",
        "Claude Code switched this session off the managed model on its own "
        "(a safeguard fallback), and work continued on the fallback model:",
        "- from `%s` to `%s`" % (rec["from"], rec["to"]),
        "- fallback window (UTC): %s .. %s%s" % (
            rec["since"], until,
            "" if rec.get("since_exact") else
            " (start = first seen by the watchdog; the fallback marker was "
            "outside the bounded transcript tail, so it began earlier)"),
        "- restored to `%s`; checkout: `%s`" % (managed, rec.get("cwd")), ""]
    if commits is None:
        lines.append("Commit range: could not be read (`git log` failed in the "
                     "checkout) -- list it by hand for the window above.")
    elif not commits:
        lines.append("Commits on local branches in the window: none.")
    else:
        lines.append("Commits on local branches in the window: %d, range `%s^..%s`"
                     % (len(commits), commits[0][0], commits[-1][0]))
        for sha, subject in commits[:MAX_COMMITS_LISTED]:
            lines.append("- `%s` %s" % (sha, subject))
        if len(commits) > MAX_COMMITS_LISTED:
            lines.append("- ... %d more" % (len(commits) - MAX_COMMITS_LISTED))
    lines += ["", "Re-review every change above on the managed model: the "
              "owner's rule is that work must not continue on a fallback model."]
    return title, "\n".join(lines)


def file_ticket(rec, managed, now, sub_run=None):
    """File the re-review ticket with the pane's own `gh` in its cwd (the
    project repo). Returns `(ok, ref, err)`."""
    sub_run = sub_run or subprocess.run
    cwd = rec.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        return False, None, "checkout %r is gone" % cwd
    commits = commit_range(cwd, rec["since"], _iso(rec.get("restored_at") or now),
                           sub_run=sub_run)
    title, body = compose_ticket(rec, managed, now, commits)
    r = sub_run(["gh", "issue", "create", "--title", title, "--body", body],
                cwd=cwd, capture_output=True, text=True, timeout=60)
    if getattr(r, "returncode", 1) != 0:
        return False, None, "gh issue create failed: %s" % (
            (getattr(r, "stderr", "") or "").strip()[:200])
    return True, (getattr(r, "stdout", "") or "").strip() or None, ""


def run_job(now, state, panes, *, run, sleep_fn, projects_dir, dry_run=False,
            handled=None, budget_left=None, sub_run=None):
    """The production wiring: the shared `watchdog.idle_pane` gates + delivery
    (resolved at call time so every `watchdog.<name>` seam stays effective),
    `model_fallback` for the verdict, `model_lineup.MANAGED_MODEL`. Held while
    the sweep has less than MIN_BUDGET_S left."""
    import model_fallback
    import model_lineup
    import watchdog
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

    def find(cwd):
        tinfo = watchdog.find_active_transcript(projects_dir, cwd)
        return tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo

    logs = restore_job(
        now, state, panes, managed=model_lineup.MANAGED_MODEL, find_transcript=find,
        read_state=model_fallback.tail_state, verdict=model_fallback.verdict,
        short_name=model_fallback.short_name, ready=ready, deliver=deliver,
        file_ticket=lambda rec, managed, t: file_ticket(rec, managed, t, sub_run=sub_run),
        outcome_kind=idle_pane.outcome_kind, delivered=idle_pane.DELIVERED,
        typed_not_delivered=idle_pane.TYPED_NOT_DELIVERED,
        dry_run=dry_run, handled=handled)
    return logs + jl
