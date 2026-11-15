"""#1332 — job 1's decision for an api-error pane whose box is NOT bare.

The obs incident (15.11.2026): the OAuth-rotation resume typed its pointer onto
the SECOND line of a box that held a line break, the read-back failed, and the
box (`❯` + an indented text row) read as "busy" to every reader for six hours.
Job 1 logged `skip busy-pane (api-error) … [busy txt='']` every sweep; the only
signal was a ping #1137 suppresses.

This leaf owns what job 1 does once `pane_at_idle_prompt` said "not bare":

* a READABLE box (`_classify_boundary` kind "input") is a draft. The resume
  paths call `reclaimed` AFTER their own grace / owner-stop / historical-401
  checks: our OWN stale machine text is reclaimed through the existing #1157
  proof (`send_outcome.presend_reclaim`: our exact pointer line whose file
  exists, a `stranded_own` record, a batch head), and a box that holds only line
  breaks (no character: no words to lose, and CC 2.1.317 refuses to stash it,
  measured) is cleared, once per episode. Nothing is typed in that sweep; the
  next sweep resumes into the bare box. A box that is not provably ours stays
  the owner's draft (the stash route / the credential-dead deferral, unchanged);
* a BUSY or unlocatable box is never typed into (#233). Its episode
  (`apierr-busypane:<key>`, anchored on its own wall-clock `first_seen`, #176 F2)
  pings once past 2x grace, as before, AND writes the `stuck!` footer cache
  (`recovery-stuck/status.json`, read by `statusbar.recovery_stuck_segment`) on
  every sweep past that threshold, so a recovery that cannot proceed is a STATE
  the owner sees, not only a suppressed ping. A reclaim that proved our text but
  could not clear it books the same episode.

Every shared primitive is read as `watchdog.<name>` at call time, so the
existing monkeypatch seams hold."""
import copy
import json
import logging
import os
import time
from pathlib import Path

import watchdog

_log = logging.getLogger(__name__)
STUCK_DIR = "recovery-stuck"
DRAFT, SKIP = "draft", "skip"          # `pane_not_bare` verdicts


def write_stuck_cache(state_path, project, reason, now):
    """The `stuck!` footer cache next to the watchdog state (`~/.claude/` in
    production). Best-effort: a failed write only delays the badge one sweep."""
    try:
        d = Path(state_path).parent / STUCK_DIR
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "status.json.tmp"
        tmp.write_text(json.dumps({"ts": int(now), "project": str(project or ""),
                                   "reason": str(reason or "")[:120]}),
                       encoding="utf-8")
        os.replace(tmp, d / "status.json")
    except OSError as e:     # airuleset:script-ok best-effort footer cache, logged
        _log.warning("recovery-stuck: footer cache not written (%s)", e)



def book_wedge(state, key, now, idle, grace, project, kind, snippet, logs, send_fn,
               owner, dry_run, state_path):
    """One sweep of a job-1 pane it cannot type into. Never types. The episode
    record (`apierr-busypane:<key>`, kept apart from job 4's `busypane:`) logs
    the classified kind + a snippet on every sweep (#176 item 3), pings ONCE
    strictly past 2x grace of its own wall clock (#176 item 2 / F2), and from
    that point paints the `stuck!` footer on every sweep. `reclaim-held` /
    `linebreak-held` are IDLE panes holding our own text / only line breaks we
    could not clear: their pings say exactly that."""
    bkey = "apierr-busypane:" + key
    b = state.get(bkey) or {"first_seen": int(now - idle), "pinged": False}
    b["last_seen"] = int(now)
    state[bkey] = b
    logs.append("skip busy-pane (api-error) %s [%s txt=%r]"
                % (project, kind, (snippet or "")[:40]))
    if (now - b["first_seen"]) <= 2 * grace:
        return
    if not dry_run:
        write_stuck_cache(state_path, project, kind, now)
    if not b["pinged"]:
        b["pinged"] = True
        logs.append("busy-pane-wedged (api-error) %s [%s] idle=%dm — ping only "
                    "(never type)" % (project, key, int(idle // 60)))
        why = {"reclaim-held": "v okne ostal náš vlastný text, ktorý sa nedá "
                               "bezpečne zmazať",
               "linebreak-held": "okno drží len prázdne riadky, ktoré sa nedajú "
                                 "bezpečne zmazať"}.get(
            kind, "pane vyzerá zaneprázdnená (%s)" % kind)
        send_fn("\U0001f6d1 **%s** — API chyba drží pane už %d min, no %s — nepíšem "
                "do nej klávesy, over ju prosím ručne." % (project, int(idle // 60), why),
                owner=owner, dedup_key="apierr-busypane:%s:%s" % (key, b["first_seen"]),
                dry_run=dry_run)


LINEBREAK_LOCK = "linebreak_clear"     # pid -> ts of the episode's clear attempt
LINEBREAK_LOCK_TTL_S = 1800


def _clear_line_breaks(pid, run, sleep_fn):
    """Backspace a box that holds only line breaks down to bare: one key per
    pass, re-read right before every key, stopped the moment a character
    appears (the owner started typing), the pane turns busy / unreadable or the
    `keys` primitive refuses (owner-stop latch). Returns the status."""
    from watchdog import send_outcome as _so
    for _ in range(watchdog.JANITOR_CLEAR_MAX_ITER):
        cap = watchdog.capture_pane(pid, run, lines=40)
        text = watchdog._input_line_text(cap)
        if text == "":
            return "bare"
        if text is None or text.strip():
            return "changed"
        if (_so._pane_busy(cap) or watchdog.STASH_MARKER in (cap or "")
                or watchdog._strip_selected(cap)):
            return "busy"
        if not watchdog.keys(pid, "BSpace", kind="janitor", run=run):
            return "refused"
        sleep_fn(watchdog.JANITOR_CLEAR_SETTLE_S)
    return "not-converged"


def clear_line_breaks_once(pid, run, sleep_fn, state, now):
    """The ONE line-break-only clear, run ONLY by the recovery resume paths
    (`reclaim`: job 1, credential-dead, job 6) — every other delivery holds on
    such a box (`send_outcome.LINE_BREAKS_HELD`): it is not our text, and a kind
    the owner switched off must type nothing. At most one attempt per pane per
    episode (its own lock,
    `LINEBREAK_LOCK`, so it never blocks the #1113 template/own-text clears),
    and the lock is released once the box reads bare — a success ends the
    episode, a later line break starts a new one. Returns the status."""
    locks = state.setdefault(LINEBREAK_LOCK, {})
    for k in [k for k, v in locks.items() if not isinstance(v, (int, float))
              or not 0 <= now - v < LINEBREAK_LOCK_TTL_S]:
        locks.pop(k, None)                  # pruned: an old or recycled pane id
    ts = locks.get(pid)
    if isinstance(ts, (int, float)) and 0 <= now - ts < LINEBREAK_LOCK_TTL_S:
        return "already tried this episode"
    locks[pid] = now
    status = _clear_line_breaks(pid, run, sleep_fn or time.sleep)
    if status in ("bare", "refused"):   # refused: no key went in, not an attempt
        locks.pop(pid, None)
    return status


def reclaim(pid, run, sleep_fn, state, now, dry_run, logs, label):
    """Reclaim our own stale text, or a line-break-only box, before a resume,
    through `send_outcome.presend_reclaim` (the one pre-send proof + clear).
    Returns None when the box is not provably ours (an owner draft, or a hold
    that came before the proof: the caller's draft path decides), "cleared"
    when the box is bare now (the next sweep resumes), or "held" when it is ours
    but the clear could not run or converge. A dry run decides on a copy of the
    state and sends no key."""
    from watchdog import owner_stop as _ostop
    from watchdog import send_outcome as _so
    cap = watchdog.capture_pane(pid, run, lines=40)
    text = watchdog._input_line_text(cap)
    if not text:
        return None
    if _ostop.pane_latch(pid):          # #1257: a stopped session — no key, no booking
        return "latched"
    if dry_run:   # the proof reads AND writes state: decide on a copy
        if text.strip() and _so._presend_own(pid, cap, copy.deepcopy(state),
                                             now) is None:
            return None
        logs.append("api-error reclaim %s: would clear %s -- dry-run"
                    % (label, "own stale text" if text.strip() else "line breaks"))
        return "held"
    if not text.strip():
        status = clear_line_breaks_once(pid, run, sleep_fn, state, now)
        logs.append("api-error reclaim %s: box held only line breaks (no words) -> %s"
                    % (label, status))
        return "cleared" if status == "bare" else "held"
    verdict = _so.presend_reclaim(pid, run, cap, sleep_fn or time.sleep, state, now,
                                  logs.append)
    if verdict == _so.HELD_DRAFT:
        return None       # the owner's draft: the caller's draft path decides
    if not verdict.startswith("pre-send"):     # held before the proof (strip, busy,
        if _so._presend_own(pid, cap, copy.deepcopy(state), now) is None:   # slot)
            return None
        verdict = "own stale text, " + verdict      # ours, but no clear can run now
    logs.append("api-error reclaim %s: %s" % (label, verdict))
    return "cleared" if verdict.startswith("pre-send: cleared") else "held"


def sesslimit_reclaim(sess, swallow_fails, pid, run, sleep_fn, state, now, dry_run,
                      logs, label):
    """Job 6 (session-limit resume): True when the box is not the owner's draft
    (our own leftover or only line breaks, never "your draft"). A hold counts
    toward job 6's bounded give-up (`swallow_fails` + `last_try`), so it can
    never stay parked in silence; a dry run counts nothing."""
    text = watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=40))
    rv = reclaim(pid, run, sleep_fn, state, now, dry_run, logs, label)
    if rv == "held" and not dry_run:
        sess["swallow_fails"], sess["last_try"] = swallow_fails + 1, now
    return rv is not None or not (text or "x").strip()


def reclaimed(pid, run, sleep_fn, state, key, now, idle, grace, project, logs,
              send_fn, owner, dry_run, state_path):
    """The resume paths' gate (job 1's draft delivery and the credential-dead
    continue), reached only after their own grace / latch / historical checks:
    True when the box held our own leftover or only line breaks (nothing is
    delivered this sweep; a clear that could not run books the wedge, so the
    owner sees `stuck!`), False when it is the owner's draft. A dry run books
    nothing (`run_once` saves state even then)."""
    verdict = reclaim(pid, run, sleep_fn, state, now, dry_run, logs, project or pid)
    if verdict == "held" and not dry_run:
        text = watchdog._input_line_text(watchdog.capture_pane(pid, run, lines=40))
        book_wedge(state, key, now, idle, grace, project or pid,
                   "linebreak-held" if not (text or "x").strip() else "reclaim-held",
                   text, logs, send_fn, owner, dry_run, state_path)
    return verdict is not None


def pane_not_bare(captured, pid, run, sleep_fn, state, key, now, idle, grace,
                  project, logs, send_fn, owner, dry_run, state_path):
    """Job 1's verdict for a pane `pane_at_idle_prompt` did not call bare:
    DRAFT (a readable box: the delivery path decides, `reclaimed` first) or
    SKIP (the pane cannot be typed into: the wedge is booked)."""
    kind, text = watchdog._classify_boundary(captured)
    if kind == "input" and text:
        return DRAFT
    book_wedge(state, key, now, idle, grace, project or pid, kind, text, logs, send_fn,
               owner, dry_run, state_path)
    return SKIP
