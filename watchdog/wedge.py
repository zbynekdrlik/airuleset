"""Queued-prompt-wedge detection (job 10, #20/#35) for the api-watchdog.

Extracted verbatim from ``watchdog/__init__.py`` as item G step 12 of the
definitive module split (issue #433). Two functions move here:

  ``_session_is_waiting``  -- job 10's ping-eligibility gate: True iff the
                              session's last assistant transcript entry ended
                              on a ``NEEDS YOU`` (❓) block.
  ``prompt_wedge_check``   -- job 10 itself: PING-FIRST detection of text stuck
                              in a pane's input box (a submitted-but-stuck
                              queued prompt or an abandoned draft), plus the
                              machine-nudge auto-submit path. This job SENDS
                              KEYSTROKES, so its extraction carries the
                              keystroke-safety review bar (single leading
                              Escape, never a double; #35/#36).

Both names are re-exported into the ``watchdog`` namespace by the positional
facade import in ``__init__.py``, so every ``watchdog.<name>`` seam keeps
resolving -- including job 10's own call inside ``run_once``'s fused per-pane
loop, which reaches ``prompt_wedge_check`` / ``_session_is_waiting`` as the
re-imported bare names in ``__init__``'s globals (identical patch semantics),
and every test that drives them via ``wd.prompt_wedge_check(...)``.

Direction is back-reference (convention C3): the two functions never call each
other (``_session_is_waiting`` is passed IN as ``prompt_wedge_check``'s
``waiting`` argument, never called by it), so this module has NO co-moved
cross-calls -- every free name they read lives OUTSIDE this module and is
reached call-time through the package namespace (``watchdog.<name>``), so any
``patch.object(watchdog, "<name>", ...)`` a test applies stays effective:

  * transcript readers (transcripts.py, re-exported):
    ``watchdog._iter_jsonl_tail`` / ``watchdog._entry_text``
  * pane/box helpers: ``watchdog._find_input_box`` (pane_classify.py),
    ``watchdog.pane_in_mode`` (tmux_io.py), ``watchdog._pane_location``
    (janitor.py), ``watchdog._cached_backlog_open`` (cross_stream.py),
    ``watchdog._own_nudge_submit_prefix`` (stash.py, #806 -- the registered
    own-nudge prefix recognizer, so a stranded ``lane-check:`` nudge is
    submitted here rather than pinged as a foreign draft)
  * still ``__init__``-resident: ``watchdog._is_dreply_machine_text`` and the
    six job-10 constants ``watchdog.MACHINE_NUDGE_PREFIX`` /
    ``watchdog.PWEDGE_MIN_IDLE_S`` / ``watchdog.PWEDGE_SWEEPS`` /
    ``watchdog.PWEDGE_SUBMIT_UNSTICK_AFTER`` /
    ``watchdog.PWEDGE_SUBMIT_GIVEUP_AFTER`` / ``watchdog.PWEDGE_PING_COOLDOWN_S``

No constant moves and neither function def-defaults one (every parameter
default is a literal), so there is no ``from watchdog import`` at module top --
the one banned import shape (``from watchdog import <function-below-its-
position>``) never arises.
"""

import watchdog


def _session_is_waiting(tpath, max_lines=50):
    """True iff the session's LAST assistant transcript entry's text
    contains 'NEEDS YOU' — i.e. it ended on a ❓ block, genuinely blocked on
    the user. Feeds job 10's `waiting` gate (issue #35): a parked draft only
    pings while the session is ACTUALLY waiting on it."""
    for entry in reversed(watchdog._iter_jsonl_tail(tpath, max_lines)):
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        return "NEEDS YOU" in (watchdog._entry_text(entry) or "")
    return False


def prompt_wedge_check(now, state, pid, captured, tmtime, owner, project,
                       send_fn, dry_run=False, run=None, waiting=True,
                       cwd=None, backlog_fetch=None):
    """Job 10 (#20, reworked #35) — queued-prompt-wedge detection, PING-FIRST.

    Text sitting in the input box (a submitted-but-stuck queued prompt, or an
    abandoned draft) blocks every keystroke delivery (job 7 draft protection)
    and can park a session for hours (gk+david 2026-07-20; the gk-master
    'nechať ako je' draft). Detection: the box text is BYTE-identical across
    >= PWEDGE_SWEEPS sweeps AND the transcript is >= PWEDGE_MIN_IDLE_S stale
    AND the pane shows no live-work signals.

    Three refinements from issue #35 on top of the original ping-first
    design:
      - MACHINE recognition now covers BOTH the static cross-stream prefixes
        AND job 7's own compose-reply text (`state['dreply_typed']`, set by
        `_record_dreply_typed`) — a swallowed delivery of OUR OWN text is
        auto-submitted (Escape+Enter — #36), never pinged as if it were a
        foreign draft.
      - `waiting` (default True — callers that can't cheaply determine it,
        e.g. a sudo-hosted foreign transcript, keep the old always-eligible
        behavior): a genuine USER draft pings NOTHING while the session is
        NOT waiting on the user (stash-around delivery, #35, means a parked
        draft no longer blocks anything urgently) — but state keeps
        tracking, so a later flip to `waiting=True` on the SAME stable draft
        still pings.
      - A per-pane cooldown (`PWEDGE_PING_COOLDOWN_S`) suppresses a re-ping
        for `pid` regardless of the draft's hash changing (a re-wrap).

    Deliberately NO auto-Enter on a genuine foreign draft (the ticket's
    decision — a machine must never submit a half-typed user draft).

    #160 defect 4 (`cwd`/`backlog_fetch`, both optional): the `not waiting`
    branch used to stay silent UNCONDITIONALLY — "tracked but nothing
    urgent" — even though a session stuck this way (unsent draft, nothing
    running, per the guard already above this branch) is worth a ping
    whenever the repo's own backlog genuinely has open, actionable work
    nobody else can reach while the draft blocks delivery. When
    `backlog_fetch` is wired and `_cached_backlog_open` confirms the
    backlog is non-empty, this falls through to the SAME ping path below
    instead of returning silently. `cwd`/`backlog_fetch` left at their
    default `None` (every pre-#160 caller/test) makes this a complete
    no-op — unchanged behavior."""
    key = "pwedge:" + pid
    box = watchdog._find_input_box(captured)
    if box is None:
        # We could not READ the input line — a running-turn spinner, an open
        # dialog, or no locatable boundary at all. That is NOT evidence that
        # there is no draft, and collapsing the two is what made this job
        # blind to every wrapped draft (#193): `_input_line_text` returned
        # None, the episode was forgotten on every sweep, and the two stable
        # sweeps its auto-Enter recovery needs could never accumulate — so
        # the one backstop for a stuck draft could not see the stuck draft.
        # An unreadable sweep neither ADVANCES an episode nor FORGETS it.
        return []
    head, tail, wrapped = box
    txt = tail if wrapped else head[1:].strip()
    attempts_key = "pwedge-submit-attempts:" + pid
    giveup_key = "pwedge-submit-giveup:" + pid
    if not txt:
        state.pop(key, None)          # provably BARE — there really is no draft
        state.pop(attempts_key, None)  # #255: the draft cleared -- forget any
                                       # escalation count so a LATER, unrelated
                                       # stuck draft starts counting from zero
        state.pop(giveup_key, None)    # ...and the give-up-ping dedup with it
        return []
    # A machine nudge's PREFIX lives on the box's HEAD row. A wrapped draft's
    # boundary row is its TAIL and can never carry it, so testing `txt` alone
    # mislabels our own stranded nudge a foreign draft and pings instead of
    # submitting it.
    head_txt = head[1:].strip()
    # #806 -- recognize EVERY registered own-nudge prefix, not only the
    # cross-stream `MACHINE_NUDGE_PREFIX` subset. `MACHINE_NUDGE_PREFIX` covers
    # bounce/gk-request but NOT `lane-check: ` (the empty-lane nudge) -- so a
    # genuinely-stranded lane-check nudge (a real swallow #814's delivered_
    # unconfirmed can't catch) fell through to the FOREIGN ping path and sat in
    # the composer forever while the watchdog only pinged (the mode-6 class:
    # detection ending in a suppressed ping instead of an action). The #501
    # `_own_nudge_submit_prefix` register (lane-check/bounce/gk-request) is the
    # single source of truth for "this HEAD is our own recognized machine nudge",
    # so a stranded own nudge is SUBMITTED in place here, never pinged. A FOREIGN
    # / human draft (no registered prefix -- e.g. a session's own `gk: ...` bounce
    # note) still takes the ping-only path below, untouched.
    machine = (head_txt.startswith(watchdog.MACHINE_NUDGE_PREFIX)
               or watchdog._own_nudge_submit_prefix(head_txt) is not None
               or watchdog._is_dreply_machine_text(state, pid, head_txt, txt))
    if not machine and ("esc to interrupt" in (captured or "")
                        or "Waiting for" in (captured or "")
                        or now - tmtime < watchdog.PWEDGE_MIN_IDLE_S):
        # a USER draft gets the conservative ping-first handling only once the
        # session is provably at rest; a MACHINE nudge is submit-anytime.
        state.pop(key, None)
        return []
    import hashlib
    h = hashlib.sha1(txt.encode("utf-8")).hexdigest()[:12]
    st = state.get(key)
    st = dict(st) if isinstance(st, dict) else {}
    if st.get("hash") != h:
        # NOTE: this fires both for a genuinely NEW draft AND for the SAME
        # still-stuck draft starting its next PWEDGE_SWEEPS-cycle right
        # after a machine-submit attempt popped `key` (#255) -- `attempts_key`
        # is deliberately NOT touched here; it carries its OWN hash below and
        # is the thing that tells those two cases apart.
        state[key] = {"hash": h, "n": 1, "pinged": False}
        return []
    st["n"] = int(st.get("n") or 1) + 1
    state[key] = st
    if st["n"] < watchdog.PWEDGE_SWEEPS or st.get("pinged"):
        return []
    if machine:
        unstick_note = ""
        if not dry_run and run and not watchdog.pane_in_mode(pid, run):
            # #255: attempts_key tracks its OWN (hash, count) pair,
            # independent of `key`'s own pop/reset cycle above -- it must
            # keep incrementing across repeated PWEDGE_SWEEPS-cycles of the
            # SAME stuck draft (same hash) and only reset to 1 when the
            # CONTENT genuinely changes (a different draft entirely).
            arec = state.get(attempts_key)
            arec = dict(arec) if isinstance(arec, dict) else {}
            attempts = (int(arec.get("n") or 0) + 1) if arec.get("hash") == h else 1
            state[attempts_key] = {"hash": h, "n": attempts}
            if attempts > watchdog.PWEDGE_SUBMIT_UNSTICK_AFTER:
                # #255: this SAME draft has already survived
                # PWEDGE_SUBMIT_UNSTICK_AFTER consecutive machine-submit
                # attempts (each of which already sent its OWN leading
                # Escape below and still failed) -- an ordinary swallowed-
                # submit race (issue #36's class) is ruled out by now. Live
                # incident (david@subdev, 2026-08-05): Claude Code was left
                # waiting for the ANSI bracketed-paste END marker (ESC[201~)
                # that nothing in this codebase ever sends -- `send-keys -l`
                # never emits real bracket framing (confirmed empirically:
                # tmux only wraps `paste-buffer -p`, never `send-keys -l`,
                # per `man tmux`) -- so every further Enter was swallowed as
                # literal text. A human's manual `send-keys -H 1b 5b 32 30
                # 31 7e` + Enter (NO leading Escape) recovered it instantly.
                #
                # CRITICAL (adversarial review): do NOT also send the
                # ordinary pre-Escape on THIS attempt -- it would put two
                # ESC bytes back to back on the wire (Escape, then this
                # sequence's own leading 0x1b), which is the exact rapid-
                # double-escape shape that PERMANENTLY DELETES a draft
                # (issue #35's hard rule) and might not even be recognized
                # as ESC[201~ at all (could parse as an Alt-modified CSI
                # instead). Send EXACTLY the sequence that was proven to
                # work, nothing more.
                run(["tmux", "send-keys", "-t", pid, "-H",
                     "1b", "5b", "32", "30", "31", "7e"])
                unstick_note = " (paste-end unstick)"
                if attempts > watchdog.PWEDGE_SUBMIT_GIVEUP_AFTER and not state.get(giveup_key):
                    # #255 (adversarial review MINOR finding): the paste-end
                    # unstick itself has now failed to actually clear this
                    # SAME draft PWEDGE_SUBMIT_GIVEUP_AFTER times over --
                    # keep retrying (it is still the best automatic
                    # recovery available), but tell a human ONCE rather
                    # than retrying silently forever.
                    state[giveup_key] = True
                    where = watchdog._pane_location(pid, run) if run else ""
                    loc = " (%s)" % where if where else ""
                    send_fn(
                        "⚠️ **%s**%s — automatické odoslanie "
                        "(vrátane paste-end unstick) opakovane "
                        "zlyháva na tom istom texte v okne "
                        "(pid %s); over to ručne — stlač tam "
                        "Enter alebo priamo v termináli skontroluj "
                        "stav." % (project, loc, pid),
                        owner=owner or None,
                        dedup_key="pwedge-submit-giveup:%s:%s" % (pid, h),
                        dry_run=dry_run)
                    unstick_note += " + give-up ping"
            else:
                # Escape first (issue #36) — a swallowed submit while the
                # agent-strip selector holds focus makes a bare Enter
                # navigate instead of submit; never a SECOND Escape (issue
                # #35: deletes a draft permanently).
                run(["tmux", "send-keys", "-t", pid, "Escape"])
            run(["tmux", "send-keys", "-t", pid, "Enter"])
        state.pop(key, None)     # still stuck → re-tracks and retries in 2 sweeps
        return ["machine-nudge submit %s (%s)%s" % (pid, project, unstick_note)]
    # #238-review-style finding 🔴F3 (this ticket's own review): resolved
    # ONCE, before EITHER branch below — the `not waiting` backlog-ping used
    # to send unconditionally, completely bypassing this per-pane cooldown
    # (a re-wrap resets `st["hash"]`/`st["pinged"]` above, so without this
    # check a fresh PWEDGE_SWEEPS-cycle after a mere terminal-width reflow
    # would re-ping every time — the EXACT "často mi chodí" spam issue #35
    # already fixed for the waiting branch, reintroduced one branch over).
    ping_key = "pwedge-ping:" + pid
    last_ping = state.get(ping_key)
    in_cooldown = last_ping and now - last_ping < watchdog.PWEDGE_PING_COOLDOWN_S
    if not waiting:
        # #160 defect 4 — before giving up silently, check whether the
        # repo's own backlog genuinely has open work waiting: "not waiting
        # on the user" no longer means "nothing depends on this clearing" —
        # a stash-around delivery (#35) already reaches the pane fine, but
        # nobody benefits from that while nothing is running and real
        # tickets sit unactioned. Unmeasurable/empty -> unchanged silent
        # "not waiting" behavior; genuinely non-empty -> fall through to the
        # SAME ping path below (own cooldown/dedup, tagged distinctly).
        if watchdog._cached_backlog_open(cwd, backlog_fetch, state, now) is not True:
            # tracked but nothing urgent — the session isn't blocked on the
            # user, and a stash-around delivery (#35) can still reach it. A
            # later poll where `waiting` flips True (same stable draft) pings.
            return ["pwedge-parked (not waiting) %s (%s)" % (pid, project)]
        if in_cooldown:
            st["pinged"] = True
            return ["pwedge-suppressed (cooldown, backlog) %s (%s)"
                    % (pid, project)]
        if dry_run:
            # #160-review-style finding 🟡F4 (this ticket's own review,
            # proven live) -- a --dry-run sweep run against REAL state (a
            # routine manual diagnostic on this repo) used to permanently
            # consume the one-shot cooldown with nothing ever actually
            # sent, since the state write happened unconditionally. Matches
            # this file's own established convention elsewhere (e.g. the
            # long-turn ping: `if dry_run ...: <skip, no state write>`
            # BEFORE ever calling send_fn) -- state is written only on a
            # genuine delivery.
            return ["prompt-wedge dry-run (backlog) %s (%s)" % (pid, project)]
        st["pinged"] = True
        state[ping_key] = now
        where = watchdog._pane_location(pid, run) if run else ""
        loc = " (%s)" % where if where else ""
        send_fn(
            "⚠️ **%s**%s — v okne visí NEODOSLANÝ text („%s…“), session stojí "
            "vyše 30 minút a v repozitári čaká otvorená práca, ktorú kým "
            "text blokuje, nikto neurobí. Stlač v tom okne Enter (text sa "
            "odošle) alebo ho zmaž."
            % (project, loc, head_txt[:60]),
            owner=owner or None,
            dedup_key="pwedge-backlog:%s:%s" % (pid, h), dry_run=dry_run)
        return ["prompt-wedge ping (backlog) %s (%s)" % (pid, project)]
    if in_cooldown:
        st["pinged"] = True
        return ["pwedge-suppressed (cooldown) %s (%s)" % (pid, project)]
    st["pinged"] = True
    state[ping_key] = now
    where = watchdog._pane_location(pid, run) if run else ""
    loc = " (%s)" % where if where else ""
    send_fn("⚠️ **%s**%s — v okne visí NEODOSLANÝ text („%s…“) a session stojí "
            "vyše 30 minút. Môže ísť aj o odložený (Ctrl+S stash) príkaz, "
            "ktorý sa po skončení bežiaceho ťahu vráti sám. Stlač v tom okne "
            "Enter (text sa odošle) alebo ho zmaž — dovtedy sa doň nedá nič "
            "doručiť."
            # the START of the draft, never its wrapped tail — a human
            # recognises their own text by how it begins (#193).
            % (project, loc, head_txt[:60]),
            owner=owner or None,
            dedup_key="pwedge:%s:%s" % (pid, h), dry_run=dry_run)
    return ["prompt-wedge ping %s (%s)" % (pid, project)]
