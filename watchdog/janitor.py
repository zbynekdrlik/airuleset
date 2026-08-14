"""#372 stuck-draft janitor — the watchdog's per-pane recovery mechanism for
its OWN wedged keystroke deliveries (a `/goal` re-arm, a `/compact` request, or
a stashed draft a delivering job parked and never popped back).

Extracted verbatim from `watchdog/__init__.py` by #433 cluster C (modular split
of the 9962-line monolith) — the seven functions the ticket names:
`_janitor_clear_box`, `_janitor_pop_stash`, `_janitor_watch_seen`,
`_janitor_mark_watch`, `_janitor_clear_watch`, `_janitor_recover`,
`_pane_location`. `watchdog/__init__.py` facade-re-exports all seven, so every
existing caller (`watchdog/goal.py`'s dark-watch, `watchdog/compact.py`,
`__init__.py`'s own bare `_pane_location` sites) reaches them unchanged.

This is the FIRST watchdog leaf with a back-dependency into `__init__.py`
(clusters A/B/E/F were zero-coupling). Eleven resident symbols stay in
`__init__.py`: the janitor-private tuning constants (`JANITOR_CLEAR_MAX_ITER`/
`JANITOR_CLEAR_BATCH_MAX`/`JANITOR_CLEAR_SETTLE_S`/`JANITOR_WATCH_MAX_AGE_S`),
the shared stash marker (`STASH_MARKER`), and the shared pane/stash primitives
(`capture_pane`, `_input_line_text`, `pane_owner`, `project_label`,
`_draft_rescue_persist`, `_looks_like_own_stuck_content`). This file's own
top-level `import watchdog` (never `from watchdog import <name>`) is how it
reaches them: the import binds the (possibly still-initializing) package object
at load time WITHOUT touching any attribute, and every `watchdog.<name>` read
happens later, at call time, when the package is fully initialized — so no
circular import is possible even though `__init__.py`'s facade re-export loads
this module mid-init. Exactly the established idiom `watchdog/goal.py` and
`watchdog/compact.py` already use for the same back-reference.

Monkeypatch seam note (for cluster D + test authors): a test that patches one of
the eleven resident symbols by attribute (`patch.object(watchdog,
"capture_pane")`, already live in `tests/test_goal_sweep.py`) still reaches
these functions — they read `watchdog.__dict__[name]` at call time, the exact
dict the patch writes. A test patching a janitor function itself sets the facade
attribute in `watchdog.__dict__`, which `__init__.py`'s bare-name callers
resolve — but the janitor's OWN internal calls between these seven functions
resolve in THIS module's globals, not via the facade (no test does this today)."""
import time

import watchdog


def _janitor_clear_box(pid, run, sleep_fn, log_fn):
    """#372 — clear the pane's CURRENT input-box content down to bare.

    ONE Escape first — a short slash command (`/compact`) can leave Claude
    Code's own command-palette MENU overlay open after typing (the THIRD
    #372 incident, forensically confirmed: the box held `/compact` with an
    open menu, which confused the ordinary verify read enough that neither
    tail-match nor the placeholder regex recognized the landed type) — a
    single Escape closes any such overlay before the clear loop begins.
    NEVER a second Escape (issue #35: a rapid double-Escape into a pane
    holding a draft PERMANENTLY DELETES it — the exact data loss this
    whole mechanism exists to prevent).

    Then an ITERATIVE, OBSERVED-STATE-DRIVEN backspace loop: each batch is
    sized to the length ACTUALLY SEEN this iteration (re-captured every
    time), never assumed from a remembered/source length — the #372 root
    cause was `len(payload)` (~3800) backspaces against a box that really
    held a ~26-char placeholder. Converges correctly regardless of whether
    Claude Code clears a collapsed placeholder atomically (bare after the
    first small batch) or character-by-character (several batches).
    Bounded by `JANITOR_CLEAR_MAX_ITER`; returns True only once a fresh
    capture confirms bare."""
    sleep_fn = sleep_fn or time.sleep
    run(["tmux", "send-keys", "-t", pid, "Escape"])
    for _ in range(watchdog.JANITOR_CLEAR_MAX_ITER):
        cap = watchdog.capture_pane(pid, run, lines=30)
        cur = watchdog._input_line_text(cap)
        if cur == "":
            return True
        if cur is None:
            # #372 adversarial-review MAJOR-1 — `_input_line_text` returns
            # `None` (never `""`) for a box that cannot currently be read
            # at all (a dialog opened mid-clear, a spinner, a transient
            # capture failure) — `cur == ""` above does not catch this, and
            # a naive `len(cur)` crashed with `TypeError`, which propagated
            # through this job's OWN top-level exception guard and aborted
            # the rest of the sweep for EVERY OTHER pane. An unreadable box
            # is the same "never guess" case every other keystroke helper
            # in this file already refuses on — abort this recovery
            # attempt cleanly; the janitor tries again next sweep.
            log_fn("janitor: box unreadable mid-clear")
            return False
        batch = min(len(cur), watchdog.JANITOR_CLEAR_BATCH_MAX)
        run(["tmux", "send-keys", "-t", pid] + ["BSpace"] * batch)
        sleep_fn(watchdog.JANITOR_CLEAR_SETTLE_S)
    log_fn("janitor: clear did not converge within %d iterations"
           % watchdog.JANITOR_CLEAR_MAX_ITER)
    return False


def _janitor_pop_stash(pid, run, log_fn):
    """#372 — the box is confirmed bare and the single stash slot is
    occupied: `C-s` always safely restores a parked draft onto a bare box
    (the identical operation Claude Code's own post-delivery auto-restore
    performs). Returns True only once a fresh capture shows non-empty
    content (the restored draft) — READ BACK, never asserted (the #134
    "a log line that claims a delivery it never checked" lesson)."""
    run(["tmux", "send-keys", "-t", pid, "C-s"])
    cap = watchdog.capture_pane(pid, run, lines=30)
    if watchdog._input_line_text(cap):
        return True
    log_fn("janitor: stash pop unverified")
    return False


def _janitor_watch_seen(state, pid, now, max_age_s=None):
    """#372 adversarial-review CRITICAL-1 — the janitor's own PROVENANCE
    gate: True only when SOME watchdog delivery job's own bookkeeping
    (`state['janitor_watch'][pid]`) proves it recently attempted a
    keystroke delivery to THIS EXACT pane. Content shape alone
    (`_looks_like_own_stuck_content`) is NOT proof of ownership — reviewed
    live: a human types their own `/goal` line via the documented manual
    autopilot-arming flow, or pastes a long block that Claude Code
    collapses into the IDENTICAL placeholder shape a machine delivery
    produces. Without this gate the janitor silently and unrecoverably
    destroyed both.

    Callers that attempt a delivery mark this (job 9/20's own call sites
    in `watchdog.goal.deliver_goal` -- the two GOAL_REARM-era functions
    that used to hold these call sites, `goal_rearm`/`_goal_template_
    drift`, were deleted wholesale by #403's collapse; job 14's own send
    point, `watchdog.compact.deliver_compact`) — never the janitor
    itself. Bounded by
    `max_age_s` (default `JANITOR_WATCH_MAX_AGE_S`) so a stale mark from
    long ago (the delivering job's own state got wedged, or this pane was
    genuinely never revisited) cannot license acting on unrelated LATER
    content forever; a future-dated mark (clock skew) is also refused,
    mirroring this file's own established `0 <=` clamp convention."""
    max_age_s = watchdog.JANITOR_WATCH_MAX_AGE_S if max_age_s is None else max_age_s
    ts = (state or {}).get("janitor_watch", {}).get(pid)
    return isinstance(ts, (int, float)) and not isinstance(ts, bool) \
        and 0 <= (now - ts) < max_age_s


def _janitor_mark_watch(state, pid, now):
    """#372 adversarial-review CRITICAL-1 — the WRITE side of
    `_janitor_watch_seen`'s provenance gate. Called by a delivering job
    right after IT attempts a keystroke send to `pid`, regardless of
    outcome (a failed attempt is exactly what the janitor needs to know
    about; a successful one is harmless to mark too, since the janitor's
    OWN content-shape check must ALSO recognize something stuck before it
    ever acts — this mark alone is never sufficient). A no-op when `state`
    is `None` (a caller/test that doesn't thread state through at all)."""
    if state is None:
        return
    state.setdefault("janitor_watch", {})[pid] = now


def _janitor_clear_watch(state, pid):
    """#372 round-2 adversarial-review MAJOR-1 — the release half of the
    provenance gate. An ORDINARY, VERIFIED-SUCCESSFUL delivery (the box
    ended up genuinely empty/submitted, confirmed by the primitive's own
    return value) proves nothing is left stuck for this pane FROM THIS
    ATTEMPT — call this right after such a success so the watch mark does
    not linger for the rest of `JANITOR_WATCH_MAX_AGE_S` (up to 6h) doing
    nothing except widening the window in which a LATER, completely
    unrelated pane content (a human's own typed `/goal` line, a pasted
    block) could satisfy the provenance gate purely by coincidence of
    timing. Never called for a FAILED/unverified delivery — that is
    exactly the case the mark must keep proving. A no-op when `state` is
    `None`, mirroring `_janitor_mark_watch`."""
    if state is None:
        return
    state.get("janitor_watch", {}).pop(pid, None)


def _janitor_recover(run, rec, pid, cwd, captured, loc, send_fn,
                          dry_run, sleep_fn, state=None, now=None):
    """#372 — the shared, generic per-pane janitor recovery driver, relocated
    beside `_janitor_watch_seen`/`_janitor_mark_watch`/`_janitor_clear_watch`
    by #403 (it used to be hosted inside goal_rearm, now deleted). Called
    from `watchdog/goal.py`'s own job-20 dark-watch loop.
    Mutates `rec` (the caller persists it); returns log lines.

    Runs at the START of every pane's sweep in this job, BEFORE any
    delivery attempt of its own (job 20's or a sibling job's) gets a
    chance to hit the SAME stuck state and abort again — this is what
    makes the "stash occupied" deadlock's exit REACHABLE (criterion 3):
    at most one sweep's delay, never forever.

    PROVENANCE FIRST (`_janitor_watch_seen`, adversarial-review CRITICAL-1
    fix): the janitor may act ONLY on a pane a watchdog delivery job's own
    bookkeeping proves it recently touched — content shape decides WHICH
    recovery action to take, never WHETHER to act at all.

    Two independently recoverable shapes, both provably safe once
    provenance holds:
      * the STASH SLOT is occupied (`STASH_MARKER` present) — the visible
        box is either already bare (an earlier undo cleared it but the
        pop was never verified) or holds our own recognizable stuck
        content (`_looks_like_own_stuck_content`) — clear it if needed,
        then pop the parked draft back.
      * NO stash is involved at all, but the visible box directly holds
        our own recognizable stuck content — job 14's no-draft `/compact`
        path (`send_continue`) never touches the stash slot, so this is
        the ONLY way that specific incident's stuck state is ever
        recovered. Just clear it to bare.
    A box holding anything else is left COMPLETELY untouched either way.

    Before any destructive clear, a draft-rescue snapshot of the box is
    persisted (`_draft_rescue_persist`) — the SAME established discipline
    `deliver_with_stash`/`_send_goal_verified` already follow before
    risking a box's content (adversarial-review CRITICAL-1 fix).

    Bounded: on a recovery FAILURE, ping the owner ONCE (deduped via
    `rec['janitor_pinged']`, cleared the moment a later sweep observes a
    clean pane again) and stop attempting the ping every sweep — never a
    silent, indefinite retry-forever (criterion 1c)."""
    logs = []
    now = now if now is not None else time.time()
    if not _janitor_watch_seen(state, pid, now):
        return logs         # no watchdog job is known to have touched this
                            # pane recently -- never act on content alone
    itext = watchdog._input_line_text(captured)
    occupied = watchdog.STASH_MARKER in (captured or "")
    if occupied:
        if itext == "":
            action = "pop"
        elif watchdog._looks_like_own_stuck_content(itext):
            action = "clear-and-pop"
        else:
            return logs                     # a genuine foreign occupant
    elif watchdog._looks_like_own_stuck_content(itext):
        action = "clear"
    else:
        return logs                         # nothing recognizable stuck

    if dry_run:
        logs.append("READY (janitor) %s -> would attempt %s recovery"
                    % (loc, action))
        return logs

    def _log(reason):
        logs.append(reason)

    if action != "pop":
        watchdog._draft_rescue_persist(pid, captured, logs=logs)

    if action == "pop":
        recovered = _janitor_pop_stash(pid, run, _log)
    elif action == "clear-and-pop":
        recovered = (_janitor_clear_box(pid, run, sleep_fn, _log)
                    and _janitor_pop_stash(pid, run, _log))
    else:                                    # "clear"
        recovered = _janitor_clear_box(pid, run, sleep_fn, _log)

    if recovered:
        rec["janitor_pinged"] = False
        logs.append("RECOVERED (janitor) %s -> stuck own delivery cleared"
                    % loc)
    else:
        if not rec.get("janitor_pinged"):
            rec["janitor_pinged"] = True
            if send_fn is not None:
                from notify import stream_redirect   # #212
                send_fn(
                    "\U0001f6a8 **%s** — watchdog má vlastný neodoslaný "
                    "text uviaznutý v pane (%s) a nedokázal ho automaticky "
                    "vyčistiť. Skontroluj a vybav prosím ručne."
                    % (watchdog.project_label(cwd), loc),
                    owner=stream_redirect(watchdog.pane_owner(pid, run)) or None,
                    dedup_key="janitor-stuck:%s:%s" % (cwd, loc),
                    dry_run=dry_run)
        logs.append("ESCALATED (janitor) %s -> recovery failed, pinged"
                    % loc)
    return logs


def _pane_location(pid, run):
    """Best-effort 'session:window.pane' for a pane — a job-10 wedge ping
    that named no window was a live complaint ('nevidim nikde ziaden
    neodoslany text', issue #35). Blank on any failure; never blocks a ping."""
    try:
        out = run(["tmux", "display-message", "-p", "-t", pid,
                   "#{session_name}:#{window_index}.#{pane_index}"])
        return (out or "").strip()
    except Exception:
        return ""
