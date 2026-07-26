"""Watchdog job 20 — GOAL RE-ARM BACKSTOP (#76).

An armed `/goal` dies SILENTLY, in the SAME process, with NO restart and NO
`Goal cleared:` marker written (montalu@subdev, 2026-07-26: one continuous
transcript since 2026-06-15, the loop died twice in one day, correlated with
`/compact`). Consequence: every transcript-based goal detector (the
`block-main-implementation.sh` goal-armed path, #54; any future re-arm
mechanism) still reads "armed" while CC runs no loop at all, and nothing ever
alerts — the user finds the stream parked hours later.

So detection needs TWO sources, exactly as the ticket demands:
  * the TRANSCRIPT marker — the truth about INTENT (`<local-command-stdout>Goal
    set: …`, the full untruncated payload; verified live: the marker body is
    byte-identical to the `/goal ` line that armed it);
  * the PANE FOOTER `◎ /goal` indicator — the truth about REALITY.
Marker says `set` + footer dark = this exact failure → re-arm with the EXACT
transcript bytes (never the viewport, which hard-wraps long goals), then
verify the indicator lit again.

Guards locked here: a `Goal cleared:` newer than the last `Goal set:` is a
DELIBERATE user shutdown and must never be re-armed; a nested `tool_result`
mentioning the marker (a session grepping ANOTHER session's transcript) is
never the current session's state; re-arming is BOUNDED (a goal that keeps
resolving itself must not spin a turn per sweep forever) and pings once on
give-up; and the delivery never collides with a `/compact` already in flight
for the same pane (#69/#78 shared gates).
"""

import json
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd

CWD = "/home/x/devel/demo"
SID = "sess"

PAYLOAD = ("STOP CONDITIONS — the loop is DONE the moment EITHER holds: (A) "
           "BLOCKED ON MY ANSWER — the latest assistant message ends with "
           "`❓ NEEDS YOU:`. (B) BACKLOG DONE — every open issue is closed and "
           "`python3 -m pytest tests/` is green on main. Dispatch ONE worker at "
           "a time and never close a ticket without live proof.")
GOAL_LINE = "/goal " + PAYLOAD

FOOTER_DARK = ("──────────────────────────────────\n"
               "❯ \n"
               "──────────────────────────────────\n"
               "  ctx ███░  5h 25%  wk 88%  Issues 2/24  caveman:lite\n"
               "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
               "  ● main\n")
FOOTER_LIT = FOOTER_DARK.replace("caveman:lite",
                                 "caveman:lite            ◎ /goal active (29m)")

CONV = "● Ticket #12 hotový, pokračujem ďalej.\n"

PANE_DARK = CONV + FOOTER_DARK
PANE_LIT = CONV + FOOTER_LIT
PANE_BUSY = CONV + FOOTER_DARK.replace("❯ \n", "✳ Baking… (2m · esc to interrupt)\n")
PANE_DRAFT = CONV + FOOTER_DARK.replace("❯ \n", "❯ rozpisany draft\n")


def marker_entry(kind, payload, ts="2026-07-26T12:54:10.000Z"):
    """The EXACT shape CC writes a /goal marker as — a TOP-LEVEL `user` entry
    whose `.message.content` is a plain STRING (verified live against this
    repo's own session transcript)."""
    body = "<local-command-stdout>Goal %s: %s</local-command-stdout>" % (
        kind, payload)
    return {"type": "user", "timestamp": ts, "message": {"content": body}}


def nested_marker_entry(payload):
    """The SAME text nested inside a `tool_result` — a session that grepped
    ANOTHER session's transcript. Structurally never top-level state."""
    return {"type": "user", "timestamp": "2026-07-26T13:00:00.000Z",
            "message": {"content": [
                {"type": "tool_result",
                 "content": [{"type": "text",
                              "text": "<local-command-stdout>Goal set: %s"
                                      "</local-command-stdout>" % payload}]}]}}


def summary_entry(payload):
    """A compaction SUMMARY narrating the goal in prose — mentions the words,
    is not a marker."""
    return {"type": "user", "timestamp": "2026-07-26T14:00:00.000Z",
            "message": {"content":
                        "This session is being continued from a previous "
                        "conversation...\nThe loop's Goal set: %s was armed "
                        "earlier." % payload}}


def write_transcript(entries, root=None, cwd=CWD, sid=SID):
    d = Path(root) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    with open(p, "a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return p


class FakeTmux:
    def __init__(self, captured, cap_seq=(), cwd=CWD, in_mode=False):
        self.captured = captured
        self.cap_seq = list(cap_seq)
        self.cwd = cwd
        self.in_mode = in_mode
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        self.sent.append(argv)
        if "list-panes" in j:
            # `_reconcile_candidate_panes` (jobs 12/17/20) asks for THREE
            # fields and rejects any other count; `list_claude_panes` asks
            # for four (it also resolves sudo-hosted panes). Answer whichever
            # this caller actually asked for.
            row = "%1\tclaude\t" + self.cwd
            return row + "\t4242" if "pane_pid" in j else row
        if "capture-pane" in j:
            if self.cap_seq:
                return self.cap_seq.pop(0)
            return self.captured
        if "display-message" in j:
            if "pane_in_mode" in j:
                return "1" if self.in_mode else "0"
            if "pane_pid" in j:
                return "4242"
            return "sess:0.0"
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]

    def keys(self):
        return [a[-1] for a in self.sent
                if "send-keys" in " ".join(a) and "-l" not in a]


def isolate_claims(testcase):
    """#78 — never touch the real ~/.claude/compact-claims.json (the live
    systemd watchdog runs this working tree every 60s on this box)."""
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    p = Path(d.name) / "claims.json"
    patcher = m.patch.object(wd, "compact_claims_path", return_value=p)
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return p


class TestGoalMarkerScan(unittest.TestCase):
    """Source 1 — the transcript marker (truth about INTENT)."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_set_marker_yields_the_exact_full_payload(self):
        p = write_transcript([marker_entry("set", PAYLOAD)], self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertIsNotNone(mark)
        self.assertEqual(mark["state"], "set")
        self.assertEqual(mark["payload"], PAYLOAD)

    def test_cleared_after_set_is_a_deliberate_shutdown(self):
        p = write_transcript([marker_entry("set", PAYLOAD),
                              marker_entry("cleared", PAYLOAD,
                                           "2026-07-26T13:10:00.000Z")],
                             self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertEqual(mark["state"], "cleared")

    def test_set_after_cleared_is_armed_again(self):
        p = write_transcript([marker_entry("cleared", PAYLOAD),
                              marker_entry("set", PAYLOAD,
                                           "2026-07-26T13:10:00.000Z")],
                             self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertEqual(mark["state"], "set")

    def test_nested_tool_result_marker_is_not_state(self):
        p = write_transcript([nested_marker_entry(PAYLOAD)], self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertIsNone(mark)

    def test_compaction_summary_mentioning_the_marker_is_not_state(self):
        p = write_transcript([summary_entry(PAYLOAD)], self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertIsNone(mark)

    def test_no_marker_at_all(self):
        p = write_transcript([{"type": "assistant",
                               "message": {"content": "hello"}}], self.tmp.name)
        _off, mark = wd.scan_goal_markers(p)
        self.assertIsNone(mark)

    def test_incremental_scan_reads_only_appended_bytes(self):
        p = write_transcript([marker_entry("set", PAYLOAD)], self.tmp.name)
        off1, mark1 = wd.scan_goal_markers(p)
        self.assertEqual(off1, p.stat().st_size)
        self.assertEqual(mark1["state"], "set")
        write_transcript([{"type": "assistant", "message": {"content": "x"}}],
                         self.tmp.name)
        off2, mark2 = wd.scan_goal_markers(p, off=off1)
        self.assertEqual(off2, p.stat().st_size)
        self.assertIsNone(mark2, "no NEW marker in the appended bytes")

    def test_truncated_file_resets_the_offset(self):
        p = write_transcript([marker_entry("set", PAYLOAD)], self.tmp.name)
        off, mark = wd.scan_goal_markers(p, off=p.stat().st_size + 10_000)
        self.assertEqual(off, p.stat().st_size)
        self.assertEqual(mark["state"], "set")

    def test_partial_trailing_line_is_not_consumed(self):
        p = write_transcript([marker_entry("set", PAYLOAD)], self.tmp.name)
        with open(p, "a") as f:
            f.write('{"type": "assistant", "message": {"conte')  # mid-write
        off, _mark = wd.scan_goal_markers(p)
        self.assertLess(off, p.stat().st_size,
                        "offset must stop after the last COMPLETE line")


class TestPaneGoalIndicator(unittest.TestCase):
    """Source 2 — the pane footer (truth about REALITY)."""

    def test_lit_indicator_reads_armed(self):
        self.assertIs(wd.pane_goal_armed(PANE_LIT), True)

    def test_dark_footer_reads_not_armed(self):
        self.assertIs(wd.pane_goal_armed(PANE_DARK), False)

    def test_indicator_in_conversation_text_is_never_armed(self):
        # a pane whose SCROLLBACK quotes the indicator (this very ticket's
        # discussion) must not read as armed — only the footer chrome counts
        pane = ("● Ticket #76: footer bez `◎ /goal` = slucka je mrtva.\n"
                + FOOTER_DARK)
        self.assertIs(wd.pane_goal_armed(pane), False)

    def test_no_statusline_is_undeterminable(self):
        self.assertIsNone(wd.pane_goal_armed("● hello\n❯ \n"))

    def test_empty_capture_is_undeterminable(self):
        self.assertIsNone(wd.pane_goal_armed(""))


class GoalRearmBase(unittest.TestCase):
    def setUp(self):
        isolate_claims(self)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pings = []

    def _send(self, text, **kw):
        self.pings.append((text, kw))

    def _write(self, entries):
        return write_transcript(entries, self.tmp.name)

    def _go(self, captured, entries=None, state=None, now=None, cap_seq=(),
            handled=None, in_mode=False, dry_run=False):
        # One `_go` call = ONE watchdog sweep. The transcript is written once
        # per test (appending it again would look like CC echoing a FRESH
        # `Goal set:` marker — a real signal this job keys on).
        if entries is not None or not getattr(self, "_wrote", False):
            self._write(entries or [marker_entry("set", PAYLOAD)])
            self._wrote = True
        tmux = FakeTmux(captured, cap_seq=cap_seq, in_mode=in_mode)
        logs = wd.goal_rearm(now or time.time(), tmux,
                             state if state is not None else {},
                             send_fn=self._send, dry_run=dry_run,
                             projects_dir=self.tmp.name, handled=handled)
        return tmux, logs

    def _typed_seq(self, text=GOAL_LINE):
        """cap_seq for a SUCCESSFUL verified delivery into a bare box: the
        job's own first capture, then post-type (our text at the boundary),
        then post-Enter (box empty again)."""
        typed_pane = CONV + FOOTER_DARK.replace("❯ \n", "❯ " + text[-40:] + "\n")
        return [PANE_DARK, typed_pane, PANE_DARK]


class TestGoalRearmDetectsAndArms(GoalRearmBase):
    def test_marker_set_plus_dark_footer_rearms_with_transcript_bytes(self):
        tmux, logs = self._go(PANE_DARK, cap_seq=self._typed_seq())
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertEqual(typed[0], GOAL_LINE)
        self.assertIn("Enter", tmux.keys())
        self.assertTrue(any("goal-rearm" in ln for ln in logs), logs)

    def test_log_distinguishes_rearm_from_job9_autoarm(self):
        _tmux, logs = self._go(PANE_DARK, cap_seq=self._typed_seq())
        self.assertTrue(any("goal-rearm" in ln for ln in logs), logs)
        self.assertFalse(any("goal-autoarm" in ln for ln in logs), logs)

    def test_payload_never_comes_from_the_pane(self):
        # the pane shows a TRUNCATED, hard-wrapped goal fragment; the armed
        # text must still be the transcript's exact bytes (the #36 lesson)
        pane = ("● /goal STOP CONDITIONS — the loop is DONE the moment EIT\n"
                + FOOTER_DARK)
        seq = [pane] + self._typed_seq()[1:]
        tmux, _logs = self._go(pane, cap_seq=seq)
        self.assertEqual(tmux.typed()[0], GOAL_LINE)

    def test_dry_run_never_types(self):
        tmux, logs = self._go(PANE_DARK, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln for ln in logs), logs)


class TestGoalRearmRefusals(GoalRearmBase):
    def test_cleared_marker_is_left_alone(self):
        tmux, _logs = self._go(PANE_DARK,
                               entries=[marker_entry("set", PAYLOAD),
                                        marker_entry("cleared", PAYLOAD,
                                                     "2026-07-26T13:10:00.000Z")])
        self.assertFalse(tmux.typed(),
                         "a deliberately cleared goal must never be re-armed")

    def test_no_marker_no_action(self):
        tmux, _logs = self._go(PANE_DARK,
                               entries=[{"type": "assistant",
                                         "message": {"content": "hi"}}])
        self.assertFalse(tmux.typed())

    def test_lit_indicator_no_action(self):
        tmux, _logs = self._go(PANE_LIT)
        self.assertFalse(tmux.typed())

    def test_undeterminable_footer_no_action(self):
        tmux, _logs = self._go("● hello\n❯ \n")
        self.assertFalse(tmux.typed())

    def test_busy_pane_is_skipped(self):
        tmux, _logs = self._go(PANE_BUSY)
        self.assertFalse(tmux.typed(),
                         "a long goal is never typed into a busy pane")

    def test_copy_mode_pane_is_skipped(self):
        tmux, _logs = self._go(PANE_DARK, in_mode=True)
        self.assertFalse(tmux.typed())

    def test_foreign_draft_goes_through_stash_delivery(self):
        # never typed OVER a user's draft — deliver_with_stash or nothing
        tmux, _logs = self._go(PANE_DRAFT)
        self.assertNotIn(GOAL_LINE, tmux.typed())

    def test_multiline_payload_is_never_typed(self):
        bad = PAYLOAD + "\nsecond line"
        tmux, logs = self._go(PANE_DARK, entries=[marker_entry("set", bad)])
        self.assertFalse(tmux.typed(), logs)


class TestGoalRearmCompactCoordination(GoalRearmBase):
    def test_sid_compacted_this_sweep_is_skipped(self):
        tmux, _logs = self._go(PANE_DARK, handled={SID})
        self.assertFalse(tmux.typed(),
                         "never type a long goal into a pane just compacted")

    def test_outstanding_compact_claim_blocks_the_rearm(self):
        wd.compact_claim_set(SID, CWD, proc={"pid": "1", "starttime": "1"})
        with m.patch.object(wd, "compact_claim_active", return_value=True):
            tmux, _logs = self._go(PANE_DARK)
        self.assertFalse(tmux.typed())

    def test_compacting_pane_is_skipped(self):
        pane = CONV + "✳ Compacting conversation…\n" + FOOTER_DARK
        tmux, _logs = self._go(pane)
        self.assertFalse(tmux.typed())


class TestGoalRearmBounded(GoalRearmBase):
    """The re-arm must never become a loop: a goal that keeps resolving itself
    would otherwise burn one full-context turn per sweep forever."""

    def _attempt(self, state, now):
        return self._go(PANE_DARK, state=state, now=now,
                        cap_seq=self._typed_seq())

    def test_confirmed_rearm_is_recorded_and_stops(self):
        state = {}
        now = time.time()
        t1, _ = self._attempt(state, now)
        self.assertTrue(t1.typed())
        # indicator lit again on the next sweep -> confirmed, nothing typed
        t2, logs = self._go(PANE_LIT, state=state, now=now + 90)
        self.assertFalse(t2.typed())
        self.assertTrue(any("CONFIRM" in ln.upper() for ln in logs), logs)

    def test_grace_before_a_second_attempt(self):
        state = {}
        now = time.time()
        t1, _ = self._attempt(state, now)
        self.assertTrue(t1.typed())
        t2, _ = self._go(PANE_DARK, state=state, now=now + 5)
        self.assertFalse(t2.typed(), "no retry inside the confirm grace")

    def test_gives_up_and_pings_once_after_the_attempt_cap(self):
        state = {}
        now = time.time()
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
            t, _ = self._attempt(state, now + i * (wd.GOAL_REARM_CONFIRM_S + 30))
            self.assertTrue(t.typed(), "attempt %d must be delivered" % i)
        late = now + wd.GOAL_REARM_MAX_ATTEMPTS * (wd.GOAL_REARM_CONFIRM_S + 30)
        t, logs = self._go(PANE_DARK, state=state, now=late)
        self.assertFalse(t.typed(), "capped — never a loop")
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(any("GAVE UP" in ln.upper() for ln in logs), logs)
        # and it stays quiet afterwards (one ping per streak, not per sweep)
        t2, _ = self._go(PANE_DARK, state=state, now=late + 120)
        self.assertFalse(t2.typed())
        self.assertEqual(len(self.pings), 1, self.pings)

    def test_streak_resets_after_the_window(self):
        state = {}
        now = time.time()
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
            self._attempt(state, now + i * (wd.GOAL_REARM_CONFIRM_S + 30))
        late = now + wd.GOAL_REARM_STREAK_S + 10
        t, _ = self._go(PANE_DARK, state=state, now=late,
                        cap_seq=self._typed_seq())
        self.assertTrue(t.typed(),
                        "a session that dies again hours later must be healed")


class TestGoalLoopStallNudge(GoalRearmBase):
    """The SECOND shape the same job must cover — and the one the 2026-07-26
    forensics actually points at.

    Reading both boxes' transcripts (montalu: 4 compactions that day;
    gatekeeper: 8) shows the goal did not simply "get disarmed". Every SURVIVAL
    had a post-compaction STIMULUS arrive — a background subagent's
    task-notification, or the human typing — and every DEATH had none: a
    `/compact` landing at a `## ✅ Work Complete` boundary with NO worker in
    flight, after which the loop never fired again until a human intervened
    (montalu 05:15:55 via job 14's idle poll, 14:38:57 via #65's synchronous
    path — different senders, identical outcome).

    And nothing covers it: job 4, the only "idle when it should be working"
    nudge, is hard-gated on the last marker being `⏳ WORKING`, while a
    completed ticket inside an armed loop correctly ends `✅ DONE`
    (message-status-marker.md, 2026-07-25). So "goal ARMED, last turn ✅, then
    silence" had no watchdog coverage at all.

    Refusals locked here matter as much as the nudge: a `⏳` turn belongs to
    job 4, a `❓` turn is the loop's OWN legitimate stop condition (never
    nudge past an unanswered question), and a running background worker means
    the stimulus is still coming."""

    IDLE = 40 * 60

    def _sweep(self, marker="✅ DONE: ticket #12 hotový", idle=None,
               captured=PANE_LIT, state=None, now=None, extra=None):
        now = now or time.time()
        entries = [marker_entry("set", PAYLOAD),
                   {"type": "assistant", "timestamp": "2026-07-26T15:00:00.000Z",
                    "message": {"content": "Hotovo.\n\n" + marker}}]
        p = self._write(entries + (extra or []))
        self._wrote = True
        mt = now - (self.IDLE if idle is None else idle)
        os.utime(p, (mt, mt))
        tmux = FakeTmux(captured)
        logs = wd.goal_rearm(now, tmux, state if state is not None else {},
                             send_fn=self._send,
                             projects_dir=self.tmp.name)
        return tmux, logs

    def test_armed_but_silent_after_a_done_turn_gets_nudged(self):
        tmux, logs = self._sweep()
        self.assertIn(wd.GOAL_STALL_TEXT, tmux.typed(), tmux.sent)
        self.assertTrue(any("goal-stall" in ln for ln in logs), logs)

    def test_working_marker_belongs_to_job4(self):
        tmux, _ = self._sweep(marker="⏳ WORKING: worker beží")
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_question_marker_is_never_nudged_past(self):
        tmux, _ = self._sweep(marker="❓ NEEDS YOU: schváliš to?")
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed(),
                         "an unanswered question IS the loop's stop condition")

    def test_fresh_transcript_is_not_a_stall(self):
        tmux, _ = self._sweep(idle=60)
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_background_worker_means_the_stimulus_is_still_coming(self):
        pane = PANE_LIT + "  ◯ autopilot-worker  Polling CI    2m · ↓ 10k\n"
        tmux, _ = self._sweep(captured=pane)
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_busy_pane_is_never_nudged(self):
        busy = CONV + FOOTER_LIT.replace("❯ \n", "✳ Baking… (esc to interrupt)\n")
        tmux, _ = self._sweep(captured=busy)
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_draft_is_never_typed_over(self):
        draft = CONV + FOOTER_LIT.replace("❯ \n", "❯ rozpisany draft\n")
        tmux, _ = self._sweep(captured=draft)
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_unarmed_session_is_not_this_branch(self):
        # a DARK footer is the re-arm branch's business, not the stall nudge's
        tmux, _ = self._sweep(captured=PANE_DARK)
        self.assertNotIn(wd.GOAL_STALL_TEXT, tmux.typed())

    def test_nudge_is_spaced_not_every_sweep(self):
        state, now = {}, time.time()
        t1, _ = self._sweep(state=state, now=now)
        self.assertIn(wd.GOAL_STALL_TEXT, t1.typed())
        t2, _ = self._sweep(state=state, now=now + 90)
        self.assertNotIn(wd.GOAL_STALL_TEXT, t2.typed())

    def test_bounded_then_one_ping(self):
        state, now = {}, time.time()
        for i in range(wd.GOAL_STALL_MAX_NUDGES):
            t, _ = self._sweep(state=state,
                               now=now + i * (wd.GOAL_STALL_INTERVAL_S + 30))
            self.assertIn(wd.GOAL_STALL_TEXT, t.typed(), "nudge %d" % i)
        late = now + wd.GOAL_STALL_MAX_NUDGES * (wd.GOAL_STALL_INTERVAL_S + 30)
        t, logs = self._sweep(state=state, now=late)
        self.assertNotIn(wd.GOAL_STALL_TEXT, t.typed())
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(any("GAVE UP" in ln.upper() for ln in logs), logs)
        t2, _ = self._sweep(state=state, now=late + wd.GOAL_STALL_INTERVAL_S + 30)
        self.assertEqual(len(self.pings), 1, "one ping per stall, not per sweep")

    def test_real_progress_resets_the_counter(self):
        state, now = {}, time.time()
        for i in range(wd.GOAL_STALL_MAX_NUDGES):
            self._sweep(state=state,
                        now=now + i * (wd.GOAL_STALL_INTERVAL_S + 30))
        moved = now + wd.GOAL_STALL_MAX_NUDGES * (wd.GOAL_STALL_INTERVAL_S + 30)
        self._sweep(state=state, now=moved, idle=30)     # the loop advanced
        t, _ = self._sweep(state=state, now=moved + wd.GOAL_STALL_INTERVAL_S + 60)
        self.assertIn(wd.GOAL_STALL_TEXT, t.typed(),
                      "a session that really advanced must be helped again")


class TestRunOnceWiring(unittest.TestCase):
    """Same 'wired = on' convention as jobs 13/14/16/18/19 — an existing
    caller that passes nothing sees NO behavior change."""

    def _cycle(self, **kw):
        td = TemporaryDirectory()
        self.addCleanup(td.cleanup)
        calls = []

        def fake(now, run, state, **kwargs):
            calls.append(kwargs)
            return ["goal-rearm called"]

        with m.patch.object(wd, "goal_rearm", side_effect=fake):
            wd.run_once(now=time.time(), dry_run=True,
                        run=lambda *a, **k: "",
                        send_fn=lambda *a, **k: None,
                        projects_dir=str(Path(td.name) / "projects"),
                        state_path=str(Path(td.name) / "state.json"), **kw)
        return calls

    def test_not_wired_means_never_called(self):
        self.assertEqual(self._cycle(), [])

    def test_wired_runs_the_job(self):
        self.assertEqual(len(self._cycle(goal_rearm_enabled=True)), 1)

    def test_docstring_documents_job_20(self):
        self.assertIn("(20)", wd.run_once.__doc__)


class TestCmdWatchdogEnablesJob20(unittest.TestCase):
    """The production caller must actually turn it on — a job wired only in
    `run_once`'s signature never runs on any box."""

    class _Args:
        dry_run = False
        verbose = False

    def test_cmd_watchdog_passes_the_gate(self):
        import contextlib
        import io
        import airuleset
        seen = {}

        def fake(*a, **kw):
            seen.update(kw)
            return []

        with m.patch.object(wd, "run_once", side_effect=fake):
            with contextlib.redirect_stdout(io.StringIO()):
                airuleset.cmd_watchdog(self._Args())
        self.assertTrue(seen.get("goal_rearm_enabled"), seen.keys())


if __name__ == "__main__":
    unittest.main()
