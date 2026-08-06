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
from datetime import datetime, timezone
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


def _iso(epoch):
    """An ISO-8601 UTC timestamp string in the exact shape CC writes, for a
    given epoch — lets a test place a marker a controlled distance from
    whatever `now` it drives the sweep with (#101's staleness gate)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


def marker_entry(kind, payload, ts=None):
    """The EXACT shape CC writes a /goal marker as — a TOP-LEVEL `user` entry
    whose `.message.content` is a plain STRING (verified live against this
    repo's own session transcript).

    `ts` defaults to the REAL current wall clock (never a fixed calendar
    date) — #101's staleness gate compares a marker's own embedded timestamp
    against real elapsed time, so a hardcoded date would silently drift into
    "stale" as the test suite ages past it. Tests exercising that gate itself
    pass an explicit `ts` (via `_iso`) anchored to their own synthetic `now`."""
    if ts is None:
        ts = _iso(time.time())
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


def assistant_entry(text, ts="2026-08-06T04:00:00.000Z"):
    """A plain real `assistant` transcript entry — #160 defect 1's
    goal-achieved backstop reads the session's LAST such entry
    (`transcript_last_assistant_text`) to decide whether a genuine
    `🏁 BACKLOG EMPTY:` claim is present."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": "msg_achieved", "content": text}}


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


class StashTmux(FakeTmux):
    """FakeTmux that MODELS the input box and Claude Code's single-slot prompt
    stash (Ctrl+S) instead of replaying a fixed capture list.

    A frozen capture makes every Ctrl+S look like a no-op, so a pane holding a
    draft LOOKED like it refused the delivery — while in production the draft
    is parked and the payload is delivered around it (issue 35)."""

    def __init__(self, captured, cwd=CWD, in_mode=False):
        super().__init__(captured, cwd=cwd, in_mode=in_mode)
        self.stash = None
        self.submitted = []
        self._box = ""
        for ln in captured.splitlines():
            if ln.strip().startswith("❯"):
                self._box = ln.strip()[1:].strip()
                break

    def _render(self):
        out = []
        for ln in self.captured.splitlines():
            if ln.strip().startswith("❯"):
                out.append("❯\xa0" + self._box if self._box else "❯\xa0")
            elif ln.strip().startswith("ctx ") and self.stash is not None:
                out.append(ln + "  " + wd.STASH_MARKER)
            else:
                out.append(ln)
        return "\n".join(out) + "\n"

    def _key(self, k):
        if k == "C-s":
            if self._box and self.stash is None:
                self.stash, self._box = self._box, ""
            elif not self._box and self.stash is not None:
                self._box, self.stash = self.stash, None
        elif k == "Enter" and self._box:
            self.submitted.append(self._box)
            self._box = ""

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "capture-pane" in j:
            self.sent.append(argv)
            return self._render()
        if argv[:2] == ["tmux", "send-keys"]:
            self.sent.append(argv)
            if "-l" in argv:
                self._box += argv[-1]
            else:
                for k in argv[4:]:
                    self._key(k)
            return ""
        return super().__call__(argv, timeout)


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

    def test_statusline_without_a_ctx_segment_still_reads(self):
        # LIVE 2026-07-26: a freshly launched session renders the managed
        # statusline WITHOUT the caveman `ctx …` block ("Fable 50% (5d)
        # otazky inde 1 … /rc"). Keying determinability on a `ctx ` prefix
        # made that whole pane unreadable — and worse, `_is_bottom_chrome`
        # does not classify that row as chrome, so the peel stopped ABOVE the
        # statusline and would have missed the indicator even when lit. The
        # footer is everything BELOW the input box, whatever it contains.
        fresh = ("● hello\n"
                 "────────────────────────\n"
                 "❯ \n"
                 "────────────────────────\n"
                 "  Fable 50% (5d)  otazky inde 1        ◎ /goal active (2m)\n"
                 "  ⏵⏵ auto mode on (shift+tab to cycle)\n")
        self.assertIs(wd.pane_goal_armed(fresh), True)
        self.assertIs(wd.pane_goal_armed(fresh.replace(
            "        ◎ /goal active (2m)", "")), False)

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
            handled=None, in_mode=False, dry_run=False, model_stash=False,
            backlog_fetch=None):
        # One `_go` call = ONE watchdog sweep. The transcript is written once
        # per test (appending it again would look like CC echoing a FRESH
        # `Goal set:` marker — a real signal this job keys on).
        if entries is not None or not getattr(self, "_wrote", False):
            self._write(entries or [marker_entry("set", PAYLOAD)])
            self._wrote = True
        tmux = (StashTmux(captured, in_mode=in_mode) if model_stash
                else FakeTmux(captured, cap_seq=cap_seq, in_mode=in_mode))
        logs = wd.goal_rearm(now or time.time(), tmux,
                             state if state is not None else {},
                             send_fn=self._send, dry_run=dry_run,
                             projects_dir=self.tmp.name, handled=handled,
                             sleep_fn=lambda s: None,
                             backlog_fetch=backlog_fetch)
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

    def test_completed_goal_is_not_a_failure(self):
        """A goal the evaluator legitimately ACHIEVED also leaves the marker
        saying `set` and the footer dark — CC writes NO `Goal cleared:` for a
        natural resolution (live-verified on an isolated session: `/goal` ->
        `✔ Goal achieved (3s · 1 turn)` -> indicator gone, transcript marker
        untouched). Re-arming that would restart a FINISHED autopilot run —
        two wasted full-context turns and a false "goal died" ping at the end
        of every successful run.

        The transcript cannot tell the two apart: montalu's own healthy loop
        wrote `stop_hook_summary` entries with `preventedContinuation: false`
        every ~19 minutes while it was working perfectly, so that field is NOT
        a resolution signal. The PANE is what distinguishes them."""
        pane = ("● Hotovo.\n"
                "✔ Goal achieved (3s · 1 turn · 56 tokens)\n" + FOOTER_DARK)
        tmux, logs = self._go(pane)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("achieved" in ln.lower() for ln in logs), logs)

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
        # never typed OVER a user's draft: the draft is PARKED first, the goal
        # is delivered into the box the park emptied, and Claude Code restores
        # the draft when that turn ends. This used to assert the goal was NOT
        # typed at all, which held only because a frozen capture made the park
        # invisible to the delivery's own verify — the opposite of what
        # `deliver_with_stash` does against a real pane (issue 35).
        tmux, _logs = self._go(PANE_DRAFT, model_stash=True)
        self.assertIn(GOAL_LINE, tmux.typed())
        self.assertEqual(tmux.submitted, [GOAL_LINE], tmux.sent)
        self.assertEqual(tmux.stash, "rozpisany draft",
                         "the draft must stay parked, never submitted or lost")

    def test_multiline_payload_is_never_typed(self):
        bad = PAYLOAD + "\nsecond line"
        tmux, logs = self._go(PANE_DARK, entries=[marker_entry("set", bad)])
        self.assertFalse(tmux.typed(), logs)


class TestScrollbackNeverDecides(GoalRearmBase):
    """LIVE 2026-07-26, the ticket's own acceptance test (`/exit` + relaunch
    on an isolated session): after a `claude -c` restart the pane's tmux
    SCROLLBACK still holds the DEAD session's `✔ Goal achieved` line — while
    the fresh process has no goal at all and the transcript marker still says
    `set`. Deciding off scrollback would refuse to heal exactly the case #76
    was filed for.

    Same lesson job 9 already learned the other way round (gk 2026-07-20: a
    stale scrollback `/goal` line armed into a fresh session): goal decisions
    read the VISIBLE VIEWPORT only. CC redraws its own screen, so the viewport
    is always the CURRENT session's content."""

    class ScrollbackTmux(FakeTmux):
        def __call__(self, argv, timeout=8):
            j = " ".join(argv)
            if "capture-pane" in j and "-S" in j:
                self.sent.append(argv)
                return ("● staré sedenie\n"
                        "✔ Goal achieved (3s · 1 turn · 56 tokens)\n"
                        "❯ /exit\n  ⎿  Bye!\n" + FOOTER_DARK)
            return super().__call__(argv, timeout)

    def test_stale_achieved_line_in_scrollback_still_heals(self):
        self._write([marker_entry("set", PAYLOAD)])
        self._wrote = True
        typed = CONV + FOOTER_DARK.replace("❯ \n", "❯ [Pasted text #1]\n")
        tmux = self.ScrollbackTmux(PANE_DARK,
                                   cap_seq=[PANE_DARK, typed, PANE_DARK])
        wd.goal_rearm(time.time(), tmux, {}, send_fn=self._send,
                      projects_dir=self.tmp.name, sleep_fn=lambda s: None)
        self.assertEqual(tmux.typed()[:1], [GOAL_LINE],
                         "a dead session's scrollback must not veto the heal")


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


class TestGoalRearmTransientRefusalNeverGivesUp(GoalRearmBase):
    """#101 live incident (dev2, 2026-07-27): two `deliver_with_stash`
    refusals in a row — both `stash-abort: no free prompt`, neither ever
    sent a single keystroke — permanently gave the backstop up for the whole
    2h streak window, even though the only real obstacle (a foreign draft
    sitting unsent) clears itself the moment it's submitted or cleared. A
    refusal that never touched the pane must be retried forever, not counted
    toward the cap; only a refusal that actually typed something and failed
    to verify is a real attempt."""

    def _stub(self, reason):
        def _fake(pid, text, run, captured=None, logs=None):
            if isinstance(logs, list):
                logs.append(reason)
            return False
        return m.patch.object(wd, "deliver_with_stash", side_effect=_fake)

    def test_pre_send_refusal_is_never_a_counted_attempt(self):
        state = {}
        now = time.time()
        with self._stub("stash-abort: no free prompt"):
            for i in range(wd.GOAL_REARM_MAX_ATTEMPTS + 3):
                t, logs = self._go(PANE_DRAFT, state=state, now=now + i * 5,
                                   cap_seq=[PANE_DRAFT])
                self.assertFalse(t.typed())
                self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs),
                                logs)
        self.assertEqual(self.pings, [],
                         "a transient pre-send refusal must never trip the "
                         "give-up ping, however many sweeps it recurs on")
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec.get("n", 0), 0,
                         "a pre-send refusal must not consume the attempt cap")

    def test_draft_clearing_still_delivers_after_repeated_refusals(self):
        state = {}
        now = time.time()
        with self._stub("stash-abort: no free prompt"):
            for i in range(wd.GOAL_REARM_MAX_ATTEMPTS + 1):
                self._go(PANE_DRAFT, state=state, now=now + i * 5,
                        cap_seq=[PANE_DRAFT])
        # the draft is gone now (submitted or cleared elsewhere) -> a real
        # bare-box delivery, unaffected by the earlier transient refusals
        t, logs = self._go(PANE_DARK, state=state, now=now + 1000,
                           cap_seq=self._typed_seq())
        self.assertTrue(t.typed(), logs)

    def test_post_send_refusal_still_counts_and_gives_up(self):
        state = {}
        now = time.time()
        with self._stub("stash-abort: type-verify-failed"):
            for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
                t, _ = self._go(PANE_DRAFT, state=state,
                                now=now + i * (wd.GOAL_REARM_CONFIRM_S + 30),
                                cap_seq=[PANE_DRAFT])
                self.assertFalse(t.typed(), "attempt %d" % i)
            late = (now
                   + wd.GOAL_REARM_MAX_ATTEMPTS * (wd.GOAL_REARM_CONFIRM_S + 30))
            t, logs = self._go(PANE_DRAFT, state=state, now=late,
                               cap_seq=[PANE_DRAFT])
        self.assertFalse(t.typed())
        self.assertTrue(any("GAVE UP" in ln.upper() for ln in logs), logs)
        self.assertEqual(len(self.pings), 1, self.pings)


class TestGoalRearmStaleMarkerIsNeverRevived(GoalRearmBase):
    """#101 live incident (dev2, 2026-07-27): the last `Goal set:` marker on
    record was 3 days old, from an already-closed autopilot run — and this
    job tried to type that dead payload into whatever the pane was now being
    used for. The natural-completion viewport check only protects a RECENT
    death (days of conversation scroll `✔ Goal achieved` out of view), so a
    marker this old — never recently confirmed armed — must never be
    revived at all."""

    def test_marker_older_than_the_bound_is_skipped(self):
        base = time.time()
        old_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        tmux, logs = self._go(PANE_DARK,
                              entries=[marker_entry("set", PAYLOAD, old_ts)],
                              now=base)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("skip stale-goal" in ln for ln in logs), logs)

    def test_marker_within_the_bound_is_revived_normally(self):
        base = time.time()
        fresh_ts = _iso(base - 60)
        tmux, logs = self._go(PANE_DARK,
                              entries=[marker_entry("set", PAYLOAD, fresh_ts)],
                              now=base, cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)

    def test_a_goal_confirmed_armed_recently_overrides_an_old_marker_ts(self):
        # the goal was legitimately alive off one old arm and only just went
        # dark THIS sweep — `last_armed` (this job's own most recent sighting
        # of the lit indicator), not the marker's raw age, is what decides
        # whether a fresh death is genuine.
        state = {}
        base = time.time()
        old_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, old_ts)],
                 state=state, now=base)
        tmux, logs = self._go(PANE_DARK, state=state, now=base + 60,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)


class TestLongPasteVerification(GoalRearmBase):
    """LIVE 2026-07-26, first automatic run of this job on dev1: it correctly
    detected a real victim (`parovanie_produktov` — transcript marker `set`,
    footer dark, last turn `✅`) and typed the 3152-char payload in… and then
    refused to submit it, twice, because **Claude Code COLLAPSES a long
    literal paste into `[Pasted text #N]`**. The tail-match every verified
    delivery in this file uses (`text.endswith(itext)`, `deliver_with_stash`
    step 5) can therefore NEVER match a long payload — it only ever worked
    because every previous caller sent short text.

    So the placeholder IS the success signal for a long type, and both
    verified-delivery paths must accept it — while a genuinely truncated
    partial type must still be refused (the #36 disaster this verification
    exists to prevent)."""

    PASTED = "[Pasted text #1]"

    def test_paste_placeholder_counts_as_typed(self):
        self.assertTrue(wd._typed_landed("/goal " + PAYLOAD, self.PASTED))

    def test_tail_match_still_counts_as_typed(self):
        text = "/goal short"
        self.assertTrue(wd._typed_landed(text, "short"))

    def test_partial_type_is_still_refused(self):
        self.assertFalse(wd._typed_landed("/goal " + PAYLOAD, "/goal STOP COND"))

    def test_empty_box_is_not_a_landed_type(self):
        self.assertFalse(wd._typed_landed("/goal x", ""))

    def test_long_rearm_is_submitted_not_abandoned(self):
        typed_pane = CONV + FOOTER_DARK.replace("❯ \n", "❯ " + self.PASTED + "\n")
        tmux, logs = self._go(PANE_DARK,
                              cap_seq=[PANE_DARK, typed_pane, PANE_DARK])
        self.assertEqual(tmux.typed()[0], GOAL_LINE)
        self.assertIn("Enter", tmux.keys(),
                      "a collapsed paste is a SUCCESSFUL type — submit it")
        self.assertTrue(any(ln.startswith("OK (goal-rearm)") for ln in logs),
                        logs)

    def test_slow_render_of_a_big_paste_is_waited_out(self):
        """LIVE 2026-07-26: a 2859-char payload really did land in the box —
        it was visible as `[Pasted text #1]` seconds later — but the
        verification capture taken IMMEDIATELY after `send-keys -l` still
        showed a bare box, so the delivery was declared failed and never
        submitted. CC needs a moment to ingest a big paste. Bounded poll, not
        a bigger blind timeout: it returns the instant the text is there."""
        bare = PANE_DARK
        typed = CONV + FOOTER_DARK.replace("❯ \n", "❯ " + self.PASTED + "\n")
        # first post-type capture is still bare (not rendered yet)
        tmux, logs = self._go(PANE_DARK,
                              cap_seq=[PANE_DARK, bare, bare, typed, bare])
        self.assertIn("Enter", tmux.keys(), logs)
        self.assertTrue(any(ln.startswith("OK (goal-rearm)") for ln in logs),
                        logs)

    def test_slow_clear_after_enter_is_not_a_swallowed_submit(self):
        """The mirror race, live 2026-07-26: the submit WORKED (the goal armed
        for real) but the capture taken immediately after `Enter` still showed
        `[Pasted text #1]`, so the delivery was read as a swallowed submit —
        logged FAIL and, worse, followed by a corrective `Escape`+`Enter` into
        a session whose turn had just STARTED. The box must be given the same
        bounded moment to clear that it gets to fill."""
        typed = CONV + FOOTER_DARK.replace("❯ \n", "❯ " + self.PASTED + "\n")
        tmux, logs = self._go(PANE_DARK,
                              cap_seq=[PANE_DARK, typed, typed, PANE_DARK])
        self.assertTrue(any(ln.startswith("OK (goal-rearm)") for ln in logs),
                        logs)
        self.assertNotIn("Escape", tmux.keys(),
                         "never Escape into a turn that just started")

    def test_a_type_that_never_appears_is_still_refused(self):
        tmux, logs = self._go(PANE_DARK,
                              cap_seq=[PANE_DARK] + [PANE_DARK] * 20)
        self.assertNotIn("Enter", tmux.keys(),
                         "a type that never renders must never be submitted")
        self.assertTrue(any(ln.startswith("FAIL") for ln in logs), logs)

    def test_stash_delivery_also_accepts_the_placeholder(self):
        # deliver_with_stash has the identical verify step, and job 20 routes
        # every draft-holding pane through it with the SAME long payload
        text = "/goal " + PAYLOAD
        with_draft = CONV + FOOTER_DARK.replace("❯ \n", "❯ draft\n")
        bare_stashed = (CONV + wd.STASH_MARKER + "\n" + FOOTER_DARK)
        pasted = (CONV + wd.STASH_MARKER + "\n"
                  + FOOTER_DARK.replace("❯ \n", "❯ " + self.PASTED + "\n"))
        # post-Ctrl+S (bare + slot lit) -> post-type (collapsed paste) -> post-Enter
        tmux = FakeTmux(bare_stashed, cap_seq=[bare_stashed, pasted, bare_stashed])
        ok = wd.deliver_with_stash("%1", text, tmux, captured=with_draft)
        self.assertTrue(ok, tmux.sent)
        self.assertIn("Enter", tmux.keys())


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
                             send_fn=self._send, projects_dir=self.tmp.name,
                             sleep_fn=lambda s: None)
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


# --------------------------------------------------------------------------- #
# #64 — the THIRD shape of job 20: the goal is armed and FIRING, but with a
# STALE version of the autopilot `/goal` template. The template is read once,
# at arm time, so a running loop keeps its old stop conditions forever.
#
# Every constant below is anchored in a measurement recorded on the ticket:
#   * fuzzy similarity CANNOT separate "same variant, older" from "different
#     variant" — measured on 62 commits of template history, the two ranges
#     overlap (tpl1-vs-tpl2, both CURRENT, = 0.7100; tpl0-current-vs-tpl0-from
#     -07-05 = 0.7279). So identity is an EXACT hash, never a threshold.
#   * the hash is taken over a NORMALIZED form (parenthetical citations,
#     backtick spans and punctuation dropped) because a real armed payload is
#     often the template minus a few editorial citations — live on this box,
#     restreamer's loop was 0.9808 from the current template and matched NO
#     historical version byte-exactly, yet is unambiguously that template.
#   * a session is only ever re-armed when it was OBSERVED matching a template
#     earlier (`tvar` recorded). A payload that never matched is a user's own
#     goal and is untouchable — proved on this box by the airuleset session's
#     own custom goal (0.2420 from its closest template).
# --------------------------------------------------------------------------- #

TPL_FULL = (
    '/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds: (A) '
    'BLOCKED ON MY ANSWER — the latest assistant message ends with a line '
    'starting `❓ NEEDS YOU:` (the camera-box wall, 2026-07-05). (B) BACKLOG '
    'DONE — every open issue not labeled autopilot-skip is closed via a merged '
    'PR, proven by `gh issue list --state open --search "-label:autopilot-skip"` '
    'showing none remain (this turn boundary is what lets the context compact '
    '— #58).')

# The SAME template after an editorial pass that only touched citations —
# what a real agent prints when it trims the parentheticals (the restreamer
# shape). Must be recognised as the SAME template.
TPL_FULL_TRIMMED = (
    '/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds: (A) '
    'BLOCKED ON MY ANSWER — the latest assistant message ends with a line '
    'starting `❓ NEEDS YOU:`. (B) BACKLOG DONE — every open issue not labeled '
    'autopilot-skip is closed via a merged PR, proven by `gh issue list '
    '--state open --search "-label:autopilot-skip"` showing none remain.')

# A genuinely NEWER version of the same variant — a real stop condition added.
TPL_FULL_V2 = TPL_FULL[:-1] + (
    '. Verify the deployed version from the live target before counting a '
    'ticket done.')

TPL_BRANCH = (
    '/goal STOP CONDITIONS — the loop is DONE the moment EITHER holds: (A) '
    'BLOCKED ON MY ANSWER — the latest assistant message ends with a line '
    'starting `❓ NEEDS YOU:`. (B) SLICE DONE — every open issue ASSIGNED TO ME '
    'is closed via my own PR merged into the INTEGRATION branch; my authority '
    'ENDS there — never promote to staging/main, never deploy.')

CUSTOM_GOAL = (
    '/goal Refactor the importer until `python3 -m pytest tests/importer` is '
    'green and the nightly run finishes under ten minutes, then stop.')


def write_templates(root, *lines):
    """A stand-in for the installed `skills/autopilot/SKILL.md` — the job must
    read the `/goal …` lines out of the SKILL prose, not a bespoke data file
    somebody has to remember to regenerate."""
    p = Path(root) / "SKILL.md"
    p.write_text(
        "## Step 2 — Start the engine\n\n"
        + "".join("**AUTHORITY: v%d**\n\n```\n%s\n```\n\n" % (i, ln)
                  for i, ln in enumerate(lines)),
        encoding="utf-8")
    return str(p)


def arm_entry(payload, ts="2026-07-26T12:54:10.000Z"):
    """The record CC writes for EVERY arm, idle or queued — live-captured on
    CC 2.1.220. A `/goal` typed into a BUSY pane drains from the type-ahead
    queue and writes NO `<local-command-stdout>Goal set:` marker at all (it
    becomes a `queue-operation` entry), so this is the only shape that sees
    both paths."""
    body = ('A session-scoped Stop hook is now active with condition: "%s". '
            'Briefly acknowledge the goal, then immediately start (or '
            'continue) working toward it — treat the condition itself as your '
            'directive.' % payload)
    return {"type": "user", "timestamp": ts, "message": {"content": body}}


class TestGoalTemplateIdentity(unittest.TestCase):
    """Identity is an EXACT hash of a NORMALIZED form — never a threshold."""

    def test_shipped_templates_are_mutually_distinct(self):
        tpls = [TPL_FULL, TPL_BRANCH]
        self.assertNotEqual(wd.goal_template_hash(tpls[0]),
                            wd.goal_template_hash(tpls[1]),
                            "two variants must never collapse onto one hash")

    def test_a_template_matches_itself(self):
        self.assertEqual(wd.goal_template_variant(TPL_FULL,
                                                  [TPL_BRANCH, TPL_FULL]), 1)

    def test_trimmed_citations_still_match_the_same_template(self):
        """The restreamer case: raw ratio 0.9808, byte-match against NO
        shipped version, yet unmistakably that template."""
        self.assertNotEqual(TPL_FULL, TPL_FULL_TRIMMED)
        self.assertEqual(wd.goal_template_variant(TPL_FULL_TRIMMED,
                                                  [TPL_FULL, TPL_BRANCH]), 0)

    def test_a_real_content_change_is_NOT_absorbed(self):
        """Normalisation must ignore citations WITHOUT ignoring substance —
        otherwise a genuine template change is invisible and nothing re-arms."""
        self.assertIsNone(wd.goal_template_variant(TPL_FULL_V2, [TPL_FULL]))

    def test_a_users_own_goal_matches_nothing(self):
        self.assertIsNone(wd.goal_template_variant(CUSTOM_GOAL,
                                                   [TPL_FULL, TPL_BRANCH]))

    def test_templates_are_read_out_of_the_skill_prose(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = write_templates(d.name, TPL_FULL, TPL_BRANCH)
        self.assertEqual(wd.load_goal_templates(p), [TPL_FULL, TPL_BRANCH])

    def test_missing_skill_file_is_not_fatal(self):
        self.assertEqual(wd.load_goal_templates("/nonexistent/SKILL.md"), [])


class TestQueuedArmIsSeen(unittest.TestCase):
    """A `/goal` delivered into a BUSY pane writes no `local-command-stdout`
    marker — without this shape the drift check reads a stale payload forever
    and re-arms the same session every sweep."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _scan(self, entries):
        p = write_transcript(entries, self.tmp.name)
        return wd.scan_goal_markers(p)[1]

    def test_arm_entry_is_a_set_marker_with_the_exact_payload(self):
        mark = self._scan([arm_entry(PAYLOAD)])
        self.assertEqual(mark["state"], "set")
        self.assertEqual(mark["payload"], PAYLOAD)

    def test_payload_containing_a_quote_survives(self):
        payload = TPL_FULL[len("/goal "):]
        self.assertIn('"', payload)
        self.assertEqual(self._scan([arm_entry(payload)])["payload"], payload)

    def test_prose_merely_quoting_the_sentence_is_not_state(self):
        entry = {"type": "user", "timestamp": "2026-07-26T14:00:00.000Z",
                 "message": {"content":
                             "Earlier the log said: A session-scoped Stop hook "
                             'is now active with condition: "%s".' % PAYLOAD}}
        self.assertIsNone(self._scan([entry]))

    def test_a_later_clear_still_wins_over_an_arm_entry(self):
        mark = self._scan([arm_entry(PAYLOAD),
                           marker_entry("cleared", PAYLOAD,
                                        "2026-07-26T13:10:00.000Z")])
        self.assertEqual(mark["state"], "cleared")


class GoalDriftBase(unittest.TestCase):
    def setUp(self):
        isolate_claims(self)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tpldir = TemporaryDirectory()
        self.addCleanup(self.tpldir.cleanup)
        self.pings = []
        self._wrote = False

    def _send(self, text, **kw):
        self.pings.append((text, kw))

    def _templates(self, *lines):
        return write_templates(self.tpldir.name, *lines)

    def _lit_seq(self, text):
        """cap_seq for a verified delivery into the bare box of an ARMED pane:
        the job's own sweep capture, the FRESH pre-delivery re-check, then
        post-type and post-Enter."""
        typed = CONV + FOOTER_LIT.replace("❯ \n", "❯ " + text[-40:] + "\n")
        return [PANE_LIT, PANE_LIT, typed, PANE_LIT]

    def _sweep(self, armed_payload=None, templates_path=None, state=None,
               now=None, cap_seq=(), pane=None, handled=None, dry_run=False,
               quiet=600):
        if armed_payload is not None:
            write_transcript([arm_entry(armed_payload,
                                        ts="2026-07-26T12:5%d:00.000Z"
                                           % (len(self.pings) % 10))],
                             self.tmp.name)
        # How long the transcript has been QUIET is what tells a paused loop
        # from one mid-turn; every test states it explicitly.
        p = Path(self.tmp.name) / wd.encode_project_dir(CWD) / (SID + ".jsonl")
        if p.exists():
            t = (now or time.time()) - quiet
            os.utime(p, (t, t))
        tmux = FakeTmux(pane or PANE_LIT, cap_seq=cap_seq)
        logs = wd.goal_rearm(now or time.time(), tmux,
                             state if state is not None else {},
                             send_fn=self._send, dry_run=dry_run,
                             projects_dir=self.tmp.name, handled=handled,
                             templates_path=templates_path,
                             sleep_fn=lambda s: None)
        return tmux, logs


class TestGoalTemplateDrift(GoalDriftBase):
    def test_up_to_date_loop_is_left_alone(self):
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        tmux, _logs = self._sweep(TPL_FULL[len("/goal "):], templates_path=tp)
        self.assertFalse(tmux.typed(), "a current template must never be retyped")

    def test_tracked_loop_is_rearmed_when_the_template_changes(self):
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        tp = self._templates(TPL_FULL_V2, TPL_BRANCH)          # push landed
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(tmux.typed(), [TPL_FULL_V2], logs)
        self.assertIn("Enter", tmux.keys())
        self.assertTrue(any("goal-drift" in ln for ln in logs), logs)

    def test_rearm_keeps_the_SAME_variant_the_loop_was_running(self):
        """Never re-resolve the authority profile — a branch-merge stream must
        not be handed the full-authority template because this box's own
        profile says so."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_BRANCH[len("/goal "):], templates_path=tp, state=state)
        branch_v2 = TPL_BRANCH[:-1] + ", and never touch another stream's tickets."
        tp = self._templates(TPL_FULL_V2, branch_v2)
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(branch_v2))
        self.assertEqual(tmux.typed(), [branch_v2], logs)

    def test_a_never_matched_goal_is_never_touched(self):
        state, seen = {}, []
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        seen += self._sweep(CUSTOM_GOAL[len("/goal "):], templates_path=tp,
                            state=state)[1]
        tp = self._templates(TPL_FULL_V2, TPL_BRANCH)
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        seen += logs
        self.assertFalse(tmux.typed(),
                         "a user's own goal must survive every template change")
        self.assertTrue(any("untracked" in ln for ln in seen), seen)

    def test_the_untracked_reason_is_logged_once_not_every_sweep(self):
        state, seen = {}, []
        tp = self._templates(TPL_FULL)
        seen += self._sweep(CUSTOM_GOAL[len("/goal "):], templates_path=tp,
                            state=state)[1]
        for _ in range(4):
            seen += self._sweep(templates_path=tp, state=state)[1]
        self.assertEqual(len([ln for ln in seen if "untracked" in ln]), 1,
                         "a custom goal must not spam the journal every minute")

    def test_a_trimmed_arm_is_tracked_too(self):
        """The restreamer shape must be covered — it is the one this ticket
        was actually filed about."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL_TRIMMED[len("/goal "):], templates_path=tp,
                    state=state)
        tp = self._templates(TPL_FULL_V2, TPL_BRANCH)
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(tmux.typed(), [TPL_FULL_V2], logs)

    def test_a_successful_rearm_settles(self):
        state = {}
        tp = self._templates(TPL_FULL)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        tp = self._templates(TPL_FULL_V2)
        self._sweep(templates_path=tp, state=state,
                    cap_seq=self._lit_seq(TPL_FULL_V2))
        # CC echoes the new arm; the next sweep must find nothing left to do
        tmux, _logs = self._sweep(TPL_FULL_V2[len("/goal "):],
                                  templates_path=tp, state=state,
                                  cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(), "the re-arm must not repeat itself")

    def test_not_wired_means_no_drift_behaviour_at_all(self):
        state = {}
        self._sweep(TPL_FULL[len("/goal "):], state=state)
        tmux, logs = self._sweep(state=state, cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(), logs)
        self.assertFalse(any("goal-drift" in ln for ln in logs), logs)

    def test_dry_run_never_types(self):
        state = {}
        tp = self._templates(TPL_FULL)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        tp = self._templates(TPL_FULL_V2)
        tmux, logs = self._sweep(templates_path=tp, state=state, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln and "goal-drift" in ln for ln in logs),
                        logs)


class TestGoalDriftRefusals(GoalDriftBase):
    def _drifted(self):
        """A tracked session whose template has since changed."""
        state = {}
        tp = self._templates(TPL_FULL)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        return state, self._templates(TPL_FULL_V2)

    def test_busy_pane_is_skipped(self):
        state, tp = self._drifted()
        tmux, _logs = self._sweep(templates_path=tp, state=state, pane=PANE_BUSY)
        self.assertFalse(tmux.typed(),
                         "a 3KB goal is never typed into a running turn")

    def test_pane_compacted_this_sweep_is_skipped(self):
        state, tp = self._drifted()
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 handled={SID}, cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(), logs)

    def test_outstanding_compact_claim_is_skipped(self):
        state, tp = self._drifted()
        with m.patch.object(wd, "compact_claim_active", return_value=True):
            tmux, logs = self._sweep(templates_path=tp, state=state,
                                     cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(), logs)

    def test_a_loop_mid_turn_is_never_typed_into(self):
        """Live-observed while building this: the pane read as a free prompt
        for one moment, the delivery typed 3 KB into it, the loop fired its
        next turn before the Enter landed, and the whole payload was left
        sitting UNSUBMITTED in the box — where every other job then sees it
        as a foreign draft. A pane's momentary look is not enough; the
        transcript's own quiet window is what distinguishes a paused loop
        from one mid-turn."""
        state, tp = self._drifted()
        tmux, logs = self._sweep(templates_path=tp, state=state, quiet=5,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("quiet" in ln for ln in logs), logs)

    def test_a_pane_that_went_busy_since_the_sweep_capture_is_left_alone(self):
        """The sweep's capture is several tmux round-trips old by the time
        delivery is reached — re-verify against a FRESH one first."""
        state, tp = self._drifted()
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=[PANE_LIT, PANE_BUSY])
        self.assertFalse(tmux.typed(), logs)

    def test_dark_footer_is_the_OTHER_shape_not_this_one(self):
        """A dead goal is #76's re-arm (transcript bytes); drift only ever acts
        on a loop that is provably still armed."""
        state, tp = self._drifted()
        tmux, logs = self._sweep(templates_path=tp, state=state, pane=PANE_DARK,
                                 cap_seq=[PANE_DARK, PANE_DARK, PANE_DARK])
        self.assertFalse(any("goal-drift" in ln for ln in logs), logs)


class TestGoalDriftBounded(GoalDriftBase):
    def _unconfirmed_sweeps(self, n):
        """`n` sweeps of a drifted session, each far enough apart that the
        previous delivery's confirmation window has expired without CC ever
        recording the new arm — i.e. deliveries that genuinely did not take."""
        state, now = {}, time.time()
        tp = self._templates(TPL_FULL)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state,
                    now=now)
        tp = self._templates(TPL_FULL_V2)
        sent, logs = 0, []
        for i in range(n):
            tmux, ln = self._sweep(templates_path=tp, state=state,
                                   now=now + (i + 1) * (wd.GOAL_REARM_CONFIRM_S
                                                        + 30),
                                   cap_seq=self._lit_seq(TPL_FULL_V2))
            sent += len(tmux.typed())
            logs += ln
        return state, sent, logs

    def test_gives_up_and_pings_once(self):
        _state, _sent, logs = self._unconfirmed_sweeps(
            wd.GOAL_DRIFT_MAX_ATTEMPTS + 3)
        self.assertEqual(len(self.pings), 1,
                         "exactly one Discord ping on give-up")
        self.assertIn("goal", self.pings[0][0].lower())
        self.assertTrue(any("GAVE UP (goal-drift)" in ln for ln in logs), logs)

    def test_attempts_are_capped(self):
        _state, sent, _logs = self._unconfirmed_sweeps(
            wd.GOAL_DRIFT_MAX_ATTEMPTS + 3)
        self.assertEqual(sent, wd.GOAL_DRIFT_MAX_ATTEMPTS)

    def test_a_delivery_awaits_confirmation_before_trying_again(self):
        """Live-observed: the re-arm landed byte-identically, but CC had not
        yet written its arm record when the next sweep ran, so the job still
        read the OLD payload and delivered a SECOND time. A delivery in
        flight is not a failed one — it is the #72/#82 typed-vs-verified
        distinction, applied here."""
        state = {}
        tp = self._templates(TPL_FULL)
        now = time.time()
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state,
                    now=now)
        tp = self._templates(TPL_FULL_V2)
        first, _ = self._sweep(templates_path=tp, state=state, now=now + 10,
                               cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(first.typed(), [TPL_FULL_V2])
        again, logs = self._sweep(templates_path=tp, state=state, now=now + 40,
                                  cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(again.typed(),
                         "a delivery still awaiting CC's arm record must not "
                         "be repeated")

    def test_an_unconfirmed_delivery_is_retried_once_the_window_passes(self):
        state = {}
        tp = self._templates(TPL_FULL)
        now = time.time()
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state,
                    now=now)
        tp = self._templates(TPL_FULL_V2)
        self._sweep(templates_path=tp, state=state, now=now + 10,
                    cap_seq=self._lit_seq(TPL_FULL_V2))
        later = now + 10 + wd.GOAL_REARM_CONFIRM_S + 5
        tmux, logs = self._sweep(templates_path=tp, state=state, now=later,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(tmux.typed(), [TPL_FULL_V2], logs)

    def test_a_NEW_template_change_earns_a_fresh_budget(self):
        state = {}
        tp = self._templates(TPL_FULL)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        tp = self._templates(TPL_FULL_V2)
        for _ in range(wd.GOAL_DRIFT_MAX_ATTEMPTS + 2):
            self._sweep(templates_path=tp, state=state,
                        cap_seq=self._lit_seq(TPL_FULL_V2))
        v3 = TPL_FULL_V2 + " Also stop on an unfixable CI failure."
        tp = self._templates(v3)
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(v3))
        self.assertEqual(tmux.typed(), [v3], logs)


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

    def test_template_path_reaches_the_job(self):
        calls = self._cycle(goal_rearm_enabled=True,
                            goal_templates_path="/tmp/SKILL.md")
        self.assertEqual(calls[0].get("templates_path"), "/tmp/SKILL.md")

    def test_template_path_defaults_to_unwired(self):
        calls = self._cycle(goal_rearm_enabled=True)
        self.assertIsNone(calls[0].get("templates_path"))


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

    def test_cmd_watchdog_points_at_the_INSTALLED_skill(self):
        """Managed boxes (marek/david/montalu) have no airuleset checkout —
        the templates must come from the skill `install` actually deployed."""
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
        p = str(seen.get("goal_templates_path"))
        self.assertTrue(p.endswith("skills/autopilot/SKILL.md"), p)
        self.assertNotIn("devel/airuleset", p)

    def test_cmd_watchdog_wires_the_backlog_fetch(self):
        # #160 defects 1/4 -- without this, both the goal-achieved backstop
        # and the widened wedge ping are permanently disabled on every real
        # box, even though this exact test file exercises them directly.
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
        self.assertTrue(callable(seen.get("backlog_fetch")), seen.keys())
        self.assertIs(seen.get("backlog_fetch"), airuleset._watchdog_backlog_fetch)


class TestGoalAchievedBacklogVerification(GoalRearmBase):
    """#160 defect 1 — a `✔ Goal achieved` pane is only trusted unconditionally
    when the session's own last real turn made NO genuine backlog-empty
    CLAIM at all. A genuine claim gets verified against the real repo
    backlog before being trusted."""

    ACHIEVED_PANE = ("● Hotovo.\n"
                     "✔ Goal achieved (3s · 1 turn · 56 tokens)\n" + FOOTER_DARK)

    def test_no_genuine_claim_skips_without_ever_calling_backlog_fetch(self):
        # the existing behavior (test_completed_goal_is_not_a_failure)
        # unchanged -- and backlog_fetch, even if wired, must never be
        # spent on a goal whose stop condition isn't backlog-shaped.
        calls = []
        tmux, logs = self._go(
            self.ACHIEVED_PANE,
            backlog_fetch=lambda cwd: calls.append(cwd) or 5)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("achieved" in ln.lower() for ln in logs), logs)
        self.assertEqual(calls, [], "no genuine claim -> gh must never run")

    def test_genuine_claim_but_backlog_non_empty_rearms(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("Some work happened.\n"
                                  "🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        # cap_seq[0] is what goal_rearm's OWN initial capture-pane call
        # returns (consumed before `_send_goal_verified`'s own captures) —
        # it must be the ACHIEVED pane, not the plain `_typed_seq()` default,
        # or the achieved-check never sees the right content at all.
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            cap_seq=[self.ACHIEVED_PANE] + self._typed_seq()[1:],
            backlog_fetch=lambda cwd: 3)
        self.assertTrue(tmux.typed(), logs)
        self.assertEqual(tmux.typed()[0], GOAL_LINE)
        self.assertTrue(any("FALSE-ACHIEVED" in ln for ln in logs), logs)

    def test_genuine_claim_and_backlog_verified_empty_skips(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            backlog_fetch=lambda cwd: 0)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("verified empty" in ln for ln in logs), logs)

    def test_genuine_claim_but_unmeasurable_backlog_fails_open(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]

        def boom(cwd):
            raise OSError("no gh")

        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries, backlog_fetch=boom)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("unverifiable" in ln for ln in logs), logs)

    def test_unwired_backlog_fetch_keeps_the_old_behavior(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        tmux, logs = self._go(self.ACHIEVED_PANE, entries=entries)
        self.assertFalse(tmux.typed(), logs)

    def test_a_mere_mention_is_not_a_claim(self):
        # a fenced/backticked mention (the worked-example shape) must not
        # be read as a genuine claim.
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("The proof line looks like\n"
                                  "`🏁 BACKLOG EMPTY: 0 open`\n"
                                  "when genuine.\n✅ DONE: hotovo")]
        calls = []
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            backlog_fetch=lambda cwd: calls.append(cwd) or 5)
        self.assertFalse(tmux.typed(), logs)
        self.assertEqual(calls, [])

    def test_repeated_sweeps_within_ttl_reuse_the_cache(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        calls = []

        def fetch(cwd):
            calls.append(cwd)
            return 0
        state = {}
        now = time.time()
        self._go(self.ACHIEVED_PANE, entries=entries, state=state, now=now,
                 backlog_fetch=fetch)
        self._go(self.ACHIEVED_PANE, state=state, now=now + 60,
                 backlog_fetch=fetch)
        self.assertEqual(len(calls), 1, calls)

    def test_stale_cached_true_never_double_rearms_after_backlog_empties(self):
        # #160-review-style finding 🔴F1 (this ticket's own review, proven
        # live against this exact harness) -- once a cached `True` verdict
        # is ACTED ON (a re-arm), it must be dropped so the NEXT
        # achieved-with-claim sweep for this cwd reads FRESH data.
        # Otherwise: a loop closes the remaining ticket(s), claims empty
        # again within the SAME 10-minute cache window, and job 20 reads
        # the STALE cached `True` again -> a SECOND spurious re-arm ->
        # the 2-attempt cap is exhausted by re-arms that were never
        # wrong -> the give-up ping fires at a loop that finished
        # correctly every time.
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("Some work happened.\n"
                                  "🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        open_flag = [True]
        calls = []

        def fetch(cwd):
            calls.append(cwd)
            return 3 if open_flag[0] else 0

        state = {}
        now = time.time()
        _tmux1, logs1 = self._go(
            self.ACHIEVED_PANE, entries=entries, state=state, now=now,
            cap_seq=[self.ACHIEVED_PANE] + self._typed_seq()[1:],
            backlog_fetch=fetch)
        self.assertTrue(any("FALSE-ACHIEVED" in ln for ln in logs1), logs1)
        self.assertEqual(len(calls), 1, calls)
        # the re-arm closed the remaining ticket(s) -- backlog genuinely
        # empty now, WITHIN the same 10-minute cache TTL window.
        open_flag[0] = False
        _tmux2, logs2 = self._go(
            self.ACHIEVED_PANE, state=state, now=now + 60,
            cap_seq=[self.ACHIEVED_PANE] + self._typed_seq()[1:],
            backlog_fetch=fetch)
        self.assertFalse(any("FALSE-ACHIEVED" in ln for ln in logs2), logs2)
        self.assertTrue(any("verified empty" in ln for ln in logs2), logs2)
        self.assertEqual(len(calls), 2,
                         "the cache entry a re-arm ACTED ON must be dropped "
                         "so the next sweep reads FRESH data, not the "
                         "stale True this very re-arm invalidated: %r"
                         % calls)


class TestCachedBacklogOpen(unittest.TestCase):
    """`_cached_backlog_open` — the shared per-cwd cache both job 10 and
    job 20 read. Covers #160-review-style findings 🔵F5 (a failed/refused
    fetch gets a MUCH shorter negative TTL than a genuine answer), 🔵F9 (a
    malformed persisted `ts` degrades rather than raising), and the
    `stale_after` prune this ticket's own review's 🔵F6 finding added to
    `run_once`'s cleanup pass."""

    def test_genuine_answer_is_cached_for_the_full_ttl(self):
        calls = []
        state = {}
        now = time.time()
        wd._cached_backlog_open("/x", lambda cwd: calls.append(cwd) or 3,
                                state, now)
        wd._cached_backlog_open("/x", lambda cwd: calls.append(cwd) or 3,
                                state, now + wd.BACKLOG_CHECK_INTERVAL_S - 1)
        self.assertEqual(len(calls), 1, calls)

    def test_a_failed_fetch_expires_much_sooner_than_a_genuine_answer(self):
        # #160-review-style finding 🔵F5 (this ticket's own review) -- the
        # expensive-and-useless case (a transient `gh` hiccup) must not be
        # rate-limited IN for the SAME long window a real answer gets.
        calls = []

        def boom(cwd):
            calls.append(cwd)
            raise OSError("no gh")

        state = {}
        now = time.time()
        r1 = wd._cached_backlog_open("/x", boom, state, now)
        self.assertIsNone(r1)
        # well past the SHORT failure TTL, but still well inside the long
        # genuine-answer TTL -- a retry must happen.
        r2 = wd._cached_backlog_open(
            "/x", boom, state, now + wd.BACKLOG_CHECK_FAILURE_TTL_S + 1)
        self.assertIsNone(r2)
        self.assertEqual(len(calls), 2, calls)

    def test_a_malformed_persisted_ts_degrades_to_expired_not_a_crash(self):
        # #160-review-style finding 🔵F9 (this ticket's own review) -- `ts`
        # crosses a JSON persistence boundary; a corrupt/legacy value must
        # never raise, and must read as EXPIRED (never "cannot tell, keep
        # forever").
        calls = []
        state = {"backlog_cache": {"/x": {"ts": "not-a-number", "open": True}}}
        result = wd._cached_backlog_open(
            "/x", lambda cwd: calls.append(cwd) or 5, state, time.time())
        self.assertEqual(result, True)
        self.assertEqual(len(calls), 1, calls)

    def test_unwired_fetch_never_writes_the_cache(self):
        state = {}
        result = wd._cached_backlog_open("/x", None, state, time.time())
        self.assertIsNone(result)
        self.assertNotIn("backlog_cache", state)


class TestBacklogCachePrune(unittest.TestCase):
    """#160-review-style finding 🔵F6 (this ticket's own review) --
    `state['backlog_cache']` is a NAMED store the flat-key cleanup pass in
    `run_once` never touches (by design); nothing else pruned it either, so
    it grew by one entry per new repo ever monitored, forever."""

    def _sweep_cleanup_only(self, initial_state, now):
        # drive run_once's own REAL cleanup pass (not a reimplemented copy
        # of it) by round-tripping state through a real temp file, with
        # every other job a no-op (an empty `list-panes` answer, nothing
        # wired) -- save_state() is unconditional even under dry_run, per
        # this file's own established contract.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        state_path = str(Path(d.name) / "state.json")
        wd.save_state(state_path, initial_state)
        wd.run_once(now=now, dry_run=True, run=lambda argv, timeout=8: "",
                    state_path=state_path)
        return wd.load_state(state_path)

    def test_a_stale_entry_is_pruned(self):
        now = 100 * wd.BACKLOG_CHECK_INTERVAL_S
        state = {"backlog_cache": {"/x/old": {"ts": 0.0, "open": True}}}
        result = self._sweep_cleanup_only(state, now)
        self.assertNotIn("/x/old", result.get("backlog_cache", {}))

    def test_a_fresh_entry_survives(self):
        now = 100 * wd.BACKLOG_CHECK_INTERVAL_S
        state = {"backlog_cache": {"/x/fresh": {"ts": now - 30, "open": False}}}
        result = self._sweep_cleanup_only(state, now)
        self.assertIn("/x/fresh", result.get("backlog_cache", {}))

    def test_a_malformed_entry_is_pruned_rather_than_raising(self):
        now = 100 * wd.BACKLOG_CHECK_INTERVAL_S
        state = {"backlog_cache": {"/x/bad": {"ts": "garbage"}}}
        result = self._sweep_cleanup_only(state, now)   # must not raise
        self.assertNotIn("/x/bad", result.get("backlog_cache", {}))


class TestGoalAchievedClassifierImportFailure(GoalRearmBase):
    """#160-review-style finding 🔵F7 (this ticket's own review) -- an
    unguarded `import backlog_marker_gate` inside the per-pane loop would
    take down job 20 for EVERY pane in the sweep on any failure (a
    mid-deploy window where one file has landed and its sibling hasn't is
    real -- this repo's api-watchdog timer runs the working tree live).
    A failure here must degrade to "no claim" for the ONE affected pane,
    loudly, never escape and kill the whole sweep."""

    ACHIEVED_PANE = ("● Hotovo.\n"
                     "✔ Goal achieved (3s · 1 turn · 56 tokens)\n" + FOOTER_DARK)

    def test_classifier_failure_degrades_to_no_claim_for_this_pane_only(self):
        entries = [marker_entry("set", PAYLOAD),
                  assistant_entry("🏁 BACKLOG EMPTY: 0 open, main green\n"
                                  "✅ DONE: hotovo")]
        with m.patch.dict("sys.modules", {"backlog_marker_gate": None}):
            tmux, logs = self._go(
                self.ACHIEVED_PANE, entries=entries,
                backlog_fetch=lambda cwd: 5)
        # never crashed the whole job (goal_rearm returns log lines, not an
        # exception) and never trusted/typed anything on the degraded read.
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("classifier error" in ln or "achieved" in ln.lower()
                            for ln in logs), logs)


class TestGoalDarkSilentDeadEndPings(GoalRearmBase):
    """#160 defect 3 — `GOAL_REARM_MAX_DARK_S` used to be a PERMANENT,
    silent dead end. It now pings ONCE (never re-arms — a payload this
    stale is not safe to type into an unknown pane)."""

    def test_stale_goal_pings_once(self):
        state = {}
        now = time.time()
        self._write([marker_entry("set", PAYLOAD,
                                  ts=_iso(now - wd.GOAL_REARM_MAX_DARK_S - 1))])
        self._wrote = True
        # last_armed/mts both old -> stale-goal branch
        self._go(PANE_DARK, state=state, now=now)
        self._go(PANE_DARK, state=state, now=now + 60)
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(self.pings[0][0].startswith("⚠️"), self.pings)

    def test_never_retypes_the_payload(self):
        state = {}
        now = time.time()
        self._write([marker_entry("set", PAYLOAD,
                                  ts=_iso(now - wd.GOAL_REARM_MAX_DARK_S - 1))])
        self._wrote = True
        tmux, _logs = self._go(PANE_DARK, state=state, now=now)
        self.assertFalse(tmux.typed())

    def test_a_later_dark_episode_after_a_real_revival_pings_again(self):
        # #238-review-style finding 🔴F2 (this ticket's own review) — the
        # ORIGINAL version of this test only asserted `len(self.pings)==2`,
        # never that the two pings carry genuinely DISTINCT dedup keys —
        # the exact same gap `TestGoalGiveUpPingPerEpisode` below already
        # guards against for the give-up ping. Without a distinct key per
        # episode the notify layer's own dedup would silently swallow the
        # SECOND ping in production even though it landed here.
        state = {}
        now = time.time()
        self._write([marker_entry("set", PAYLOAD,
                                  ts=_iso(now - wd.GOAL_REARM_MAX_DARK_S - 1))])
        self._wrote = True
        self._go(PANE_DARK, state=state, now=now)
        self.assertEqual(len(self.pings), 1, self.pings)
        # the goal comes back ARMED for real (a human re-armed it by hand)
        self._go(PANE_LIT, state=state, now=now + 60)
        # ... then dies again, dark for another MAX_DARK_S window
        later = now + 60 + wd.GOAL_REARM_MAX_DARK_S + 1
        self._go(PANE_DARK, state=state, now=later)
        self.assertEqual(len(self.pings), 2, self.pings)
        dark_keys = [kw.get("dedup_key") for _text, kw in self.pings
                    if "dedup_key" in kw
                    and str(kw["dedup_key"]).startswith("goaldark:")]
        self.assertEqual(len(dark_keys), 2, self.pings)
        self.assertNotEqual(dark_keys[0], dark_keys[1],
                            "each dark episode must get its OWN dedup key, "
                            "or the notify layer silently drops the repeat")

    def test_dry_run_never_consumes_the_one_shot_ping(self):
        # #238-review-style finding 🟡F4 (this ticket's own review) — the
        # flag/save used to happen BEFORE the send_fn/dry_run check, so a
        # `--dry-run` sweep (a normal manual diagnostic against REAL state,
        # per this repo's own playbook) would permanently mark the episode
        # as pinged with nothing ever actually sent. A dry-run sweep must
        # be a complete no-op on the persisted flag; the NEXT real sweep
        # still delivers the genuine ping.
        state = {}
        now = time.time()
        self._write([marker_entry("set", PAYLOAD,
                                  ts=_iso(now - wd.GOAL_REARM_MAX_DARK_S - 1))])
        self._wrote = True
        self._go(PANE_DARK, state=state, now=now, dry_run=True)
        self.assertEqual(self.pings, [])
        self._go(PANE_DARK, state=state, now=now + 60)
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(self.pings[0][0].startswith("⚠️"), self.pings)


class TestGoalRearmSweepDeadline(unittest.TestCase):
    """#160-review-style finding 🟡F2 (this ticket's own review, measured
    live) — goal_rearm now makes a blocking network call per distinct repo
    it re-verifies (`_watchdog_backlog_fetch`), and previously had NO
    wall-clock self-bound of its own (unlike jobs 8/9, #255's own
    `tail_deadline`). The per-pane loop must never START a new pane's work
    once the shared sweep budget is exhausted -- mirrors jobs 8/9's own
    tested pattern exactly."""

    def _run_two_panes(self):
        def run(argv, timeout=8):
            j = " ".join(argv)
            if "list-panes" in j:
                return "%1\tclaude\t/x/repo-a\n%2\tclaude\t/x/repo-b"
            if "capture-pane" in j:
                return PANE_DARK
            if "display-message" in j:
                if "pane_in_mode" in j:
                    return "0"
                return "sess:0.0"
            return ""
        return run

    def test_never_starts_a_new_pane_past_the_deadline(self):
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        for i, cwd in enumerate(["/x/repo-a", "/x/repo-b"]):
            write_transcript([marker_entry("set", PAYLOAD)], proj.name,
                             cwd=cwd, sid="sid-%d" % i)
        logs = wd.goal_rearm(time.time(), self._run_two_panes(), {},
                             send_fn=lambda *a, **k: None,
                             projects_dir=proj.name,
                             time_fn=lambda: 100.0, sweep_deadline=0.0)
        self.assertTrue(any("goal-rearm-budget-exceeded" in ln for ln in logs),
                        logs)
        self.assertTrue(any("0/2 panes handled" in ln for ln in logs), logs)

    def test_a_deferred_pane_is_untouched_state_wise(self):
        # deferring must lose NOTHING -- the pane is simply retried next
        # sweep, exactly like an untouched pane always would be.
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        for i, cwd in enumerate(["/x/repo-a", "/x/repo-b"]):
            write_transcript([marker_entry("set", PAYLOAD)], proj.name,
                             cwd=cwd, sid="sid-%d" % i)
        state = {}
        wd.goal_rearm(time.time(), self._run_two_panes(), state,
                     send_fn=lambda *a, **k: None, projects_dir=proj.name,
                     time_fn=lambda: 100.0, sweep_deadline=0.0)
        self.assertEqual(state.get("goal_rearm", {}), {})

    def test_no_deadline_given_is_unbounded_unchanged_behavior(self):
        # default None -> the pre-#160-review behavior: no budget message
        # at all, every pane is visited.
        proj = TemporaryDirectory()
        self.addCleanup(proj.cleanup)
        for i, cwd in enumerate(["/x/repo-a", "/x/repo-b"]):
            write_transcript([marker_entry("set", PAYLOAD)], proj.name,
                             cwd=cwd, sid="sid-%d" % i)
        logs = wd.goal_rearm(time.time(), self._run_two_panes(), {},
                             send_fn=lambda *a, **k: None,
                             projects_dir=proj.name)
        self.assertFalse(any("budget-exceeded" in ln for ln in logs), logs)


class TestGoalGiveUpPingPerEpisode(GoalRearmBase):
    """#160 defect 3 — the give-up ping's dedup key must be genuinely
    DISTINCT per streak-reset episode, or the notify layer's dedup silently
    swallows every give-up after the first (the live gk incident: 3 real
    GAVE UP events, only 1 marker on disk)."""

    def _attempt(self, state, now):
        return self._go(PANE_DARK, state=state, now=now,
                        cap_seq=self._typed_seq())

    def test_dedup_key_differs_across_streak_episodes(self):
        # mirrors TestGoalRearmBounded.test_gives_up_and_pings_once_after_
        # the_attempt_cap's own structure: MAX_ATTEMPTS successful deliveries,
        # THEN a separate confirming sweep is what actually reaches n >=
        # max_attempts and fires GAVE UP (the delivery attempts themselves
        # only ever bring n UP TO the cap, never past it in the same call).
        state = {}
        now = time.time()
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
            self._attempt(state, now + i * (wd.GOAL_REARM_CONFIRM_S + 30))
        confirm1 = now + wd.GOAL_REARM_MAX_ATTEMPTS * (wd.GOAL_REARM_CONFIRM_S + 30)
        self._go(PANE_DARK, state=state, now=confirm1)   # GAVE UP #1
        late = now + wd.GOAL_REARM_STREAK_S + 10
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
            self._attempt(state, late + i * (wd.GOAL_REARM_CONFIRM_S + 30))
        confirm2 = late + wd.GOAL_REARM_MAX_ATTEMPTS * (wd.GOAL_REARM_CONFIRM_S + 30)
        self._go(PANE_DARK, state=state, now=confirm2)   # GAVE UP #2
        gave_up = [kw.get("dedup_key") for _text, kw in self.pings
                  if "dedup_key" in kw and str(kw["dedup_key"]).startswith("goalrearm:")]
        self.assertEqual(len(gave_up), 2, self.pings)
        self.assertNotEqual(gave_up[0], gave_up[1],
                            "each streak episode's GAVE UP must get its OWN "
                            "dedup key, or the notify layer silently drops "
                            "every repeat after the first")


if __name__ == "__main__":
    unittest.main()
