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
(clusters A/B/E/F were zero-coupling). Twelve resident symbols stay in
`__init__.py`: the janitor-private tuning constants (`JANITOR_CLEAR_MAX_ITER`/
`JANITOR_CLEAR_BATCH_MAX`/`JANITOR_CLEAR_SETTLE_S`/`JANITOR_WATCH_MAX_AGE_S`),
the shared stash marker (`STASH_MARKER`), and the shared pane/stash primitives
(`capture_pane`, `_input_line_text`, `_input_box_head_text`, `pane_owner`,
`project_label`, `_draft_rescue_persist`, `_looks_like_own_stuck_content`).
`_input_line_text` returns the box TAIL (still read by `_janitor_clear_box`/
`_janitor_pop_stash`); `_input_box_head_text` returns the box HEAD and is what
`_janitor_recover` reads for own-content recognition (#506 -- a wrapped own
nudge's prefix is on the head, absent from the tail). This file's own
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


def _janitor_park_record(state, pid, now):
    """#488 — the WRITE side of the DURABLE, park-specific provenance record.
    Called by a stash-delivery caller when `deliver_with_stash` returned
    False (an abort that may have left a park in the single slot) — records
    `state['stash_parks'][pid] = now` in the SAME already-persisted watchdog
    `state` (`~/.claude/api-watchdog-state.json`, `save_state`), so the fact
    survives a restart/deploy AND, unlike `_janitor_watch_seen`'s 6h mark,
    does NOT expire: a genuinely-ours park can sit unresolved for far longer
    than 6h (the gk goal ran `(1d)`, the exact live gap this fixes). `now`
    is stored for observability only — the reclaim gate is age-UNBOUNDED,
    never a timeout band-aid. A no-op when `state` is `None`, mirroring
    `_janitor_mark_watch`."""
    if state is None:
        return
    state.setdefault("stash_parks", {})[pid] = now


def _janitor_park_seen(state, pid):
    """#488 — the READ side: True when a durable park record proves WE left
    a stash park outstanding for THIS pane, regardless of age (that is the
    whole point — a genuinely-ours park is reclaimable after any delay). A
    human's own parked draft is never given a record, so this is the signal
    that keeps the age-unbounded reclaim from ever acting on one. Type-
    checked like `_janitor_watch_seen` so a corrupt state entry never reads
    as provenance; no age bound (deliberately — see `_janitor_park_record`)."""
    ts = (state or {}).get("stash_parks", {}).get(pid)
    return isinstance(ts, (int, float)) and not isinstance(ts, bool)


def _janitor_clear_park(state, pid):
    """#488 — the RELEASE side. Cleared on (a) a verified-successful stash
    delivery (Claude Code owns the async auto-restore then; the janitor must
    not interfere — same shape as `_janitor_clear_watch` on success), (b) a
    successful janitor reclaim, and (c) the marker-gone backstop in
    `_janitor_recover` (a recorded park whose slot is no longer occupied has
    been resolved by CC / us / a human, so the record is stale). This is
    what safely bounds an age-unbounded record WITHOUT a timeout: it survives
    only while OUR park is still visibly present. A no-op when `state` is
    `None`, mirroring `_janitor_clear_watch`."""
    if state is None:
        return
    state.get("stash_parks", {}).pop(pid, None)


def _janitor_prune_parks(state, live_pids):
    """#488 review-1 MINOR — GC the age-unbounded `stash_parks` records so one
    can never orphan for a pane that no longer exists. The park record removed
    the 6h age bound (deliberately, for the stash case) — but the `janitor_watch`
    mark that bound guarded ALSO self-expires the stale-provenance an orphan
    would otherwise carry FOREVER (#372's own "a stale mark cannot license
    acting on unrelated LATER content forever" property). This restores that
    bound WITHOUT a timeout: drop every record whose pane-id is NOT in the
    CURRENT live tmux pane set — a pane absent from tmux can never be typed to,
    so its record can only ever be stale, and dropping it is strictly safe. Also
    bounds the dict's growth on a long-lived box.

    Runs each sweep alongside the marker-gone backstop (which only sees panes
    STILL in the candidate set) — this covers the panes that LEFT it. FAIL-SAFE
    by construction, in BOTH directions: an empty/None `live_pids` (a failed
    `tmux list-panes` read) prunes NOTHING; and a PARTIAL read (a live pane's id
    missing) can at worst drop a still-VALID record, which only ever makes the
    janitor MORE conservative — it loses `park_seen` provenance and falls back to
    the 6h generic mark, never destroying a draft nor licensing a wrong reclaim.
    So every prune error mode lands on the safe side (a missed legitimate
    reclaim), never the dangerous one. A no-op when `state` is None or holds no
    parks. Known, honestly-stated residual (heavily backstop-mitigated): tmux
    `%N` pane-ids reset on a SERVER restart, so a record written just before a
    restart can coincide with a reused id on a DIFFERENT session — but the
    marker-gone backstop clears it on the first unoccupied sighting of the reused
    pane. Even if it does not, the reachable actions never destroy a human's
    draft: occupied+bare → a non-destructive `pop` (restores the slot); occupied+
    own-content → a `clear-and-pop` that FIRST `_draft_rescue_persist`-snapshots
    the box AND requires `_looks_like_own_stuck_content` — a gate a human's
    ordinary draft never matches (`test_park_record_does_not_bypass_the_foreign_
    occupant_gate`)."""
    if state is None or not live_pids:
        return
    parks = state.get("stash_parks")
    if not parks:
        return
    live = set(live_pids)
    for pid in [p for p in parks if p not in live]:
        parks.pop(pid, None)


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
    # #506 -- recognize the own residue on the box HEAD row
    # (`_input_box_head_text`), NEVER the TAIL (`_input_line_text`). The tail is
    # the #193 `endswith` swallowed-tail contract (`_input_line_text` returns the
    # LAST wrapped row); but every real own nudge (289-720c: `lane-check: ` #490,
    # `stuck-check: ` #497, `bounce-backstop: `/`gk-request backstop: `, and the
    # /goal | /compact re-arm/compact residues) WRAPS at a live pane width, so
    # its leading prefix sits on the HEAD row and is ABSENT from the tail --
    # keying `_looks_like_own_stuck_content` on the tail made the clear /
    # clear-and-pop reclaim a DEAD branch for exactly the wrapped residue it
    # exists to reclaim (#506; the #501 head-read fix, one path over -- the
    # lane-guard submit path was repointed to the head, the janitor reclaim was
    # not). The provenance gate below is UNCHANGED and stays the ownership
    # decision; this only fixes WHICH ROW the content shape is read from. A bare
    # box reads `""` on BOTH head and tail, so the `itext == ""` pop branch is
    # byte-for-byte, and a foreign draft whose head carries no own prefix is
    # still left untouched (`_draft_rescue_persist` snapshots before any clear).
    itext = watchdog._input_box_head_text(captured)
    occupied = watchdog.STASH_MARKER in (captured or "")

    # #488 -- the DURABLE, age-unbounded park record. Written by
    # `deliver_with_stash` itself the instant it DEFINITIVELY parks a draft
    # (STASH_PARKED), never on a bare abort outcome, so it can only ever mark
    # an unambiguously-ours park (the slot was free before our own C-s). It
    # survives a restart/deploy and does NOT expire, so a genuinely-ours park
    # is reclaimable after any delay -- the exact gk gap (a goal ran `(1d)`,
    # past the 6h `_janitor_watch_seen` bound).
    park_seen = _janitor_park_seen(state, pid)
    if park_seen and not occupied:
        # Marker-gone backstop: the park was resolved (CC's own async
        # auto-restore / our pop-back / a human), so the record is stale ->
        # clear it. This is what bounds an age-unbounded record WITHOUT a
        # timeout: it survives ONLY while OUR park is still visibly present,
        # which is also why a human's LATER stash (recorded by nobody) can
        # never satisfy the gate purely by reusing this pane later. The clear
        # is JOURNALLED (#488-review-2 MINOR / the #486 "no silent state
        # mutation" direction) so a live-box operator can see the record being
        # reclaimed; on a plain dry-run sweep it mutates nothing and stays
        # silent.
        park_seen = False
        if not dry_run:
            _janitor_clear_park(state, pid)
            logs.append("janitor: stale park record cleared (%s) -> slot no "
                        "longer occupied" % loc)

    # PROVENANCE. The generic 6h delivery-attempt mark alone still gates the
    # destructive no-stash `clear` action (unchanged -- a conservative bound
    # for an irreversible box-clear). For a STASH reclaim (slot occupied) the
    # age-unbounded park record ALSO satisfies provenance -- it precisely
    # proves WE left this park, so a genuinely-ours draft is reclaimable
    # after any delay while a human's own stash (never recorded) is not.
    if not (_janitor_watch_seen(state, pid, now) or (occupied and park_seen)):
        return logs         # no watchdog job is known to have touched this
                            # pane recently -- never act on content alone
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
        _janitor_clear_park(state, pid)     # #488 -- our park is resolved
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
