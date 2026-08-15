"""Long-running-turn watch + pane liveness / turn-elapsed readers (run_once job 21).

Extracted verbatim from ``watchdog/__init__.py`` as item G step 11 of the
definitive module split (issue #433). The job-21 LONG-TURN WATCH family plus the
pane-content readers it and the goal-rearm job (job 20) share:

* ``long_turn_watch`` (job 21, #84) + ``pane_turn_elapsed`` (reads CC's own
  spinner elapsed label), ``_human_duration`` / ``_human_age_desc`` (phone-
  readable durations) and the ``LONG_TURN_*`` thresholds.
* ``_reconcile_candidate_panes`` (job 20's own claude/node/bun pane enumeration)
  and ``_pane_has_bg_agent`` (the agent-strip / "Waiting for N background
  agents" reader, #36/#42) -- both consumed by ``watchdog/goal.py``'s
  dark-watch / lane-sweep at call time.
* ``_pane_compacting`` + ``COMPACTING_MARKER`` (the "/compact already in flight"
  guard, read by ``watchdog/goal.py``) and ``_QUEUED_COMPACT_RX`` (the queued-
  ``/compact`` regex whose only consumer is ``watchdog/pane_text.py``'s
  ``_pane_has_queued_compact``); both co-moved for block contiguity and reached
  by their external consumers through the re-export.

This is a back-reference module (``import watchdog``): every name it did NOT
co-move is reached through the package namespace at call time as
``watchdog.<name>`` -- ``_default_run`` (``watchdog/tmux_io.py``),
``_BG_AGENTS_WAIT_RX`` (still ``__init__``), ``_above_box_scan``
(``watchdog/pane_text.py``) and ``_pane_location`` (``watchdog/janitor.py``) --
so every ``patch.object(watchdog, "<name>", ...)`` seam still resolves. The
intra-module co-moved references stay BARE: the step-11 four-idiom C5 audit
proved none of the co-moved names is patched at the ``watchdog.`` path (only
``_pane_compacting`` and ``long_turn_watch`` are patched at all, and neither has
an intra-module caller here -- their callers live in ``watchdog/goal.py`` and
run_once, both outside this module).

Every name here (7 functions + 6 module-level constants) is re-exported into the
``watchdog`` namespace by the positional facade import in ``__init__.py``, so
``watchdog.<name>`` stays resolvable for run_once (job 21), ``watchdog/goal.py``,
``watchdog/pane_text.py`` and the tests. No constant here is a def-time default
of any function (``long_turn_watch``'s ``threshold`` defaults to ``None`` and
reads ``LONG_TURN_THRESHOLD_S`` in its body), so all constants move + re-export
cleanly with no ``from watchdog import`` needed.
"""

import os
import re

import watchdog


def _human_age_desc(age):
    """#339-review MAJOR -- a human-activity timestamp can legitimately land
    slightly AFTER `now` (mid-sweep drift, see the caller's own docstring),
    so the log line must read sensibly either way rather than printing a
    confusing negative number."""
    return "%ds old" % int(age) if age >= 0 else "%ds in the future" % int(-age)


# An agent-strip row for an OTHER (non-main) agent — `◯ <agent>` or its
# SELECTED form `❯ ◯ <agent>` (issue #36). Deliberately excludes the bare
# `● main` row: that renders even with zero subagents running, so it alone
# does not mean a worker is in flight.
_AGENT_STRIP_ROW_RX = re.compile(r"^(?:❯\s*)?◯\s+\S")


def _reconcile_candidate_panes(run):
    """[(pane_id, cwd, cmd)] for every tmux pane whose foreground command is
    claude/node/bun — the reference implementation's exact filter (some CC
    installs surface the wrapper's process name, e.g. 'node', as
    pane_current_command, not 'claude'). Deliberately its OWN enumeration,
    NOT `list_claude_panes` (which also resolves sudo-hosted stream panes
    and is relied on by every OTHER keystroke-sending job in this file) —
    widening THAT filter to node/bun would make an unrelated node/bun pane
    look like a live Claude session to every job, not just this one.

    Deduped by pane_id (same discipline as `list_claude_panes`): a GROUPED
    tmux session shares its underlying pane with every session name it is
    linked under, so `tmux list-panes -a` lists the SAME pane_id once per
    session name — confirmed live on dev1 (marek's grouped sessions each
    re-list every shared window). Without the dedup, a single live pane
    would be visited twice per sweep (harmless — the consuming job's own
    per-session dedup sees the first visit's claim — but wasteful and noisy
    in the logs). Job 20 (goal re-arm) is the only consumer since #132
    removed jobs 12/18/23."""
    run = run or watchdog._default_run
    out = run(["tmux", "list-panes", "-a", "-F",
               "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"])
    seen = set()
    res = []
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, cmd, cwd = parts
        if not pid or pid in seen:
            continue
        if cmd not in ("claude", "node", "bun"):
            continue
        seen.add(pid)
        res.append((pid, cwd, cmd))
    return res


def _pane_has_bg_agent(captured):
    """True if the captured pane shows a live BACKGROUND AGENT — a
    `◯ <agent>` agent-strip row (or its SELECTED form `❯ ◯ <agent>`, issue
    #36), or the ambient "Waiting for N background agents to finish" line
    CC prints while one runs. A restart (`/exit`) would KILL any in-flight
    worker. Added for job 12 (#42), which #132 removed; job 20 (goal re-arm)
    is the remaining consumer — it must not paste a `/goal` into a pane whose
    background worker is mid-flight."""
    if not captured:
        return False
    if watchdog._BG_AGENTS_WAIT_RX.search(captured):
        return True
    for ln in captured.splitlines():
        if _AGENT_STRIP_ROW_RX.match(ln.strip()):
            return True
    return False


# --------------------------------------------------------------------------- #
# Job 15 -- COMPACT OVERGROWN IDLE SESSIONS -- REMOVED (#102, 2026-07-27).
#
# `compact_stale_context` fired /compact purely off CONTEXT SIZE + IDLE
# DURATION, with no regard for what marker the session's last turn ended
# on -- exactly the mechanism the #102 correction identified as the live
# camera-box incident's likely cause (a `❓ NEEDS YOU` turn, blocked
# awaiting the user's answer, sat idle long enough to qualify and got
# compacted). The user's corrected agreement (#102): compaction fires
# ONLY at a completed-ticket boundary (job 14, `watchdog.compact.compact_
# sweep`) -- nothing else, no context-size heuristic of our own. Claude Code's
# own native auto-compact already handles a genuinely full context; this
# job never fed anything else (its state keys `compact_stale`/
# `compact_stale_attempts`, its constants COMPACT_CONTEXT_THRESHOLD/
# COMPACT_MIN_IDLE_S/COMPACT_STALE_*, and `_wait_for_compact_return` were
# ALL private to this job's own delivery and are removed with it -- none
# of it was reused elsewhere). See #102's evidence block for the full
# audit of what survived from jobs 15/17 and why.
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Job 17 -- HARD CONTEXT CEILING BACKSTOP -- REMOVED (#102, 2026-07-27).
#
# `compact_hard_ceiling` fired /compact purely off CONTEXT SIZE (a fixed
# ceiling), regardless of idle duration and even into a BUSY pane, with no
# regard for what marker the session's last turn ended on. Same #102
# correction as job 15: compaction fires ONLY at a completed-ticket
# boundary (job 14) -- no context-size heuristic of our own. This job's
# state key (`state['compact_ceiling']`), its own constants
# (COMPACT_HARD_CEILING/COMPACT_CEILING_QUEUED/COMPACT_CEILING_STUCK_CYCLES)
# were private to its own delivery and are removed with it. The SHARED
# helpers it also used (`_pane_compacting`/COMPACTING_MARKER,
# `_reconcile_candidate_panes`) are KEPT -- but AFTER the #402 compact
# collapse (below) neither is job 14's or job 21's own any more: job 14's
# real code now lives in `watchdog/compact.py`, which never calls either
# (it uses only `_pane_has_queued_compact`, which DOES survive as a
# genuine job-14 dependency via `watchdog._pane_has_queued_compact`); job
# 21 (`pane_turn_elapsed`/`long_turn_watch`) never called either to begin
# with. Both remaining callers are GOAL-owned -- now `watchdog/goal.py`'s
# own job-20 dark-watch/lane-sweep (formerly `_goal_*_nudge` family and
# `goal_rearm`, both collapsed away by #403) -- kept for THAT reason, not
# the one this comment originally gave. `_proc_fingerprint_alive`/
# `_pane_claude_proc_fingerprint`, the real `/compact` claim/lock system,
# and its later #402 always-False/no-op compatibility stubs
# (`compact_claim_active`/`compact_claim_set`) were ALL removed for good
# by #403, once goal's own delivery machinery stopped depending on them.
# See #102's evidence block for the original audit and #402's for the
# later one.
# --------------------------------------------------------------------------- #

COMPACTING_MARKER = "Compacting conversation"

# --------------------------------------------------------------------------- #
# #84 (2026-07-26 live gk incident) — reading the rows CC renders DIRECTLY
# ABOVE the input box. Two independent consumers share this ONE walk:
#
#   * the QUEUED-COMPACT guard (`_pane_has_queued_compact`) — a pane that
#     already has a `/compact` submitted-but-unexecuted must never be sent a
#     second one. CC drains its type-ahead queue only where a turn actually
#     ENDS, so during a long turn the queued commands just pile up and then
#     fire back-to-back: the first really compacts, the rest answer "Not
#     enough messages to compact". That is the duplicate-compact spam.
#
#   * the LONG-TURN detector (`pane_turn_elapsed`, job 21) — CC's spinner row
#     carries the turn's own elapsed time.
#
# The walk starts at the input box and moves UP through blank spacer rows,
# the box's own top separator, and the queued `❯ …` rows; the FIRST other
# content row it meets is the spinner (if the pane is running a turn at all)
# and also terminates the queue. That adjacency requirement is what keeps a
# conversation that merely QUOTES a panel (a session working on this very
# ticket) from being read as a live queue — real content below the quoted
# rows stops the walk before it ever reaches them. Everything BELOW the box
# (the agent strip, whose activity label is ARBITRARY model-generated text —
# the #36 scar — plus the statusline and mode hint) is peeled by
# `_above_input_box` and is never even looked at.
#
# Known, accepted limitation (the safe direction): a pane whose conversation
# ends with quoted `❯ /compact` rows IMMEDIATELY above the box would read as
# queued and skip one compaction. A missed compaction self-heals on the next
# sweep as the viewport scrolls; a missed GUARD is the duplicate this exists
# to kill.
# --------------------------------------------------------------------------- #

_QUEUED_COMPACT_RX = re.compile(r"^❯\s+/compact\s*$")

# CC's spinner: `· Germinating… (2h 40m 36s · ↓ 69.3k tokens)`,
# `✳ Baking… (4m 2s · esc to interrupt)`, `✳ Baking… (7s · …)`. Anchored on
# the ellipsis + parenthesised duration CC always renders, so a line of prose
# that merely mentions a duration is not a spinner.
#
# EVERY component is optional because CC DROPS the seconds once a turn is a
# few minutes old (`(2m · esc to interrupt)` — a shape that appears verbatim
# in this repo's own live-captured fixtures). A seconds-mandatory pattern
# therefore went blind exactly as a turn started getting long, which is the
# one case job 21 exists for. `\b` after each unit keeps `(3 messages)` from
# reading as 3 minutes, and an all-empty match (`(esc to interrupt)`, no
# duration claimed at all) is rejected by the caller rather than invented as
# 0s — a fake "just started" would reset the incident identity every sweep.
_TURN_ELAPSED_RX = re.compile(
    r"…\s*\(\s*(?:(\d+)\s*h\b)?\s*(?:(\d+)\s*m\b)?\s*(?:(\d+)\s*s\b)?")


def pane_turn_elapsed(captured):
    """How long the pane's CURRENT turn has been running, in seconds — read
    off CC's own spinner row — or None when no turn is running (or the row
    carries no elapsed time yet).

    Deliberately a PANE read, not a transcript read. The #84 forensic
    analysis showed CC logged the 2h40m incident as THREE internal turns,
    each continued by the armed `/goal` loop's Stop hook REJECTING the stop —
    and the input queue drained at NONE of those boundaries, only at the
    user's manual interrupt. A transcript-boundary-based duration would
    therefore have reported three short turns and missed the incident
    entirely. The pane's label runs from the last genuine EXTERNAL input,
    which is exactly the quantity that matters: how long the queue has been
    unable to drain."""
    _queued, spinner = watchdog._above_box_scan(captured)
    if not spinner:
        return None
    mm = _TURN_ELAPSED_RX.search(spinner)
    if not mm:
        return None
    h, mi, s = mm.group(1), mm.group(2), mm.group(3)
    if h is None and mi is None and s is None:
        return None                    # a spinner claiming no elapsed time
    return int(h or 0) * 3600 + int(mi or 0) * 60 + int(s or 0)


def _pane_compacting(captured):
    """True if the pane's OWN current text shows CC's "Compacting
    conversation" progress indicator -- a `/compact` already in flight, sent
    by job 14 or #65's synchronous delivery (jobs 15/17, once also senders,
    were REMOVED #102 -- the indicator does not care who triggered it, so
    this stays a useful defense-in-depth for any remaining sender).
    Resending `/compact` while this shows would just queue a redundant
    second compaction (#69)."""
    return COMPACTING_MARKER in (captured or "")

# --------------------------------------------------------------------------- #
# Job 21 — LONG-TURN WATCH (#84, 2026-07-26 live gatekeeper incident).
#
# The watchdog could only ever see CONTEXT SIZE. But a turn that simply RUNS
# for hours is a fault state of its own, independent of how big the context
# is: while it runs nothing compacts, no question is delivered, and every
# keystroke anyone sends just piles up in CC's type-ahead queue. The live
# incident: ONE turn at 2h40m, three `/compact` queued behind it, context at
# 398K, and nothing moved until the user manually interrupted.
#
# WHY THE PANE AND NOT THE TRANSCRIPT. The forensic read of that session
# (posted on #84) is what settles this. CC logged those 2h40m as THREE
# internal turns — 13:25→14:36, 14:36→15:30, 15:30→15:44 — each ended by the
# armed `/goal` loop's Stop hook REJECTING the stop and forcing continuation.
# The input queue drained at NONE of those boundaries; the two queued
# `/compact` commands sat there from 14:31 and 15:41 until the manual
# interrupt at 15:44. So "CC drains the queue at a turn boundary" is really
# "…only where the turn actually ENDS (the Stop is accepted)" — and a
# detector built on transcript turn boundaries would have seen three ordinary
# turns and missed this incident completely. The PANE's own elapsed label
# runs from the last genuine EXTERNAL input, which is precisely the quantity
# that matters here.
#
# (The same read DISPROVED the ticket's original hypothesis: no foreground
# subagent dispatch was involved. Every `Agent` call in the window returned
# async within ~100 ms and none was in flight during the long stretch — the
# turn was a long unbroken chain of correctly-bounded foreground `Bash` CI
# polls, extended over and over by CI restarting from concurrent merges.)
#
# Detection only — this job NEVER sends a keystroke. A long turn may be
# entirely legitimate (a genuine long CI wait), so the response is ONE
# deduped Discord ping per incident plus an unconditional log line every
# sweep, never an interrupt: deciding to break a running turn is the user's
# call, and interrupting a healthy one is the worse error.
# --------------------------------------------------------------------------- #

LONG_TURN_THRESHOLD_S = 1800          # 30 min; env AIRULESET_LONG_TURN_S
# Two sweeps of the SAME turn compute `start = now - elapsed` from a
# whole-second pane label read at slightly different moments, so the value
# jitters by a second or two. A generous tolerance keeps that jitter from
# reading as a new turn (which would re-ping); a genuinely new turn's start
# is minutes-to-hours away, never within this window.
LONG_TURN_SAME_TURN_TOLERANCE_S = 300


def _human_duration(seconds):
    """`9636` -> `2h 40m` — the phone-readable form for the ping."""
    h, rem = divmod(int(seconds), 3600)
    mins = rem // 60
    if h:
        return "%dh %dm" % (h, mins)
    return "%dm" % mins


def long_turn_watch(now, run, state, panes_by_sid, send_fn=None, dry_run=False,
                    threshold=None, project_by_sid=None, owner_by_sid=None):
    """Job 21 — see the section comment.

    Reuses the ONE per-sweep capture run_once already took (`panes_by_sid`,
    the same map jobs 7 and 14 consume), so a sweep costs no extra tmux
    round-trip for panes that are idle or short-running.

    Every DETECTION is logged unconditionally, every sweep (issue #36's
    print-always convention) — a long turn that persists must be visible in
    journalctl for as long as it lasts, not only on the sweep it crossed the
    threshold. The PING is deduped per (session, turn): the turn's identity
    is its START (`now - elapsed`, tolerant of read jitter per
    `LONG_TURN_SAME_TURN_TOLERANCE_S`), so a single incident pings once no
    matter how many sweeps it spans, while a NEW long turn later pings
    again. `dry_run` logs exactly as usual but never pings and never records
    state, mirroring every other job's dry-run contract."""
    if threshold is None:
        try:
            threshold = int(os.environ.get("AIRULESET_LONG_TURN_S",
                                           LONG_TURN_THRESHOLD_S))
        except ValueError:
            threshold = LONG_TURN_THRESHOLD_S
    project_by_sid = project_by_sid or {}
    owner_by_sid = owner_by_sid or {}
    seen = state.get("long_turn") or {}
    logs = []
    live = set()

    for sid, (pid, captured) in sorted((panes_by_sid or {}).items()):
        elapsed = pane_turn_elapsed(captured)
        if elapsed is None:
            continue                       # no turn running — nothing to say
        live.add(sid)
        start = now - elapsed
        prev = seen.get(sid) or {}
        same_turn = abs(start - float(prev.get("start") or 0)) \
            <= LONG_TURN_SAME_TURN_TOLERANCE_S
        entry = {"start": start,
                 "pinged": bool(same_turn and prev.get("pinged"))}
        if elapsed < threshold:
            if not dry_run:
                seen[sid] = entry
            continue

        label = project_by_sid.get(sid) or watchdog._pane_location(pid, run) or pid
        human = _human_duration(elapsed)
        logs.append("long-turn %s [%s] elapsed=%ds (%s)"
                    % (label, sid, elapsed, human))
        if dry_run or entry["pinged"] or send_fn is None:
            # `send_fn is None` (a caller with no notify path) must NOT mark
            # the turn as pinged — nothing was delivered, so a later sweep
            # with a real send_fn still owes the user this ping.
            continue
        status = send_fn(
            "\U0001f570 **%s** — jeden ťah beží už %s\n"
            "> Kým beží, nič sa nekompaktuje, otázky sa nedoručujú a "
            "napísané príkazy čakajú vo fronte. Ak to nie je zámer "
            "(dlhé čakanie na CI), treba sa pozrieť." % (label, human),
            owner=owner_by_sid.get(sid) or None,
            dedup_key="long-turn:%s:%d" % (sid, int(start)),
            dry_run=dry_run)
        logs.append("long-turn PING %s [%s] -> %s" % (label, sid, status))
        entry["pinged"] = True
        seen[sid] = entry

    if not dry_run:
        # drop sessions whose turn ended (or whose pane is gone) so the state
        # file cannot grow without bound across a long-lived watchdog.
        state["long_turn"] = {k: v for k, v in seen.items() if k in live}
    return logs
