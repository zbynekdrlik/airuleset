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
        # #322 — `_type_literal` sends a long payload as SEVERAL consecutive
        # `-l` calls (chunked) rather than one — join a consecutive RUN of
        # them into a single logical "typed" entry (nothing else, no
        # capture-pane/Enter/Escape, ever interrupts one delivery's own
        # chunk burst) so every existing `tmux.typed()[0] == GOAL_LINE`-
        # style assertion keeps working unchanged for both the short
        # single-burst path and the long chunked one.
        out = []
        buf = []
        for a in self.sent:
            if "-l" in a:
                buf.append(a[-1])
            elif buf:
                out.append("".join(buf))
                buf = []
        if buf:
            out.append("".join(buf))
        return out

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
            backlog_fetch=None, progress_dir=None):
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
                             backlog_fetch=backlog_fetch,
                             progress_dir=progress_dir)
        return tmux, logs

    def _typed_seq(self, text=GOAL_LINE):
        """cap_seq for a SUCCESSFUL verified delivery into a bare box: the
        job's own first capture, #271's own re-capture-immediately-before-
        typing (still bare), then post-type (our text at the boundary), then
        post-Enter (box empty again)."""
        typed_pane = CONV + FOOTER_DARK.replace("❯ \n", "❯ " + text[-40:] + "\n")
        return [PANE_DARK, PANE_DARK, typed_pane, PANE_DARK]


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


class TestGoalRearmGiveUpHasAReachableExit(GoalRearmBase):
    """#322 requirement 2: once GAVE UP fires, `skip gave-up` used to repeat
    FOREVER — nothing short of the blind `GOAL_REARM_STREAK_S` (2h) full-
    state reset ever cleared it, and that reset is tied to elapsed time
    alone, never to the pane actually recovering. Live on dev1: a give-up
    state from 15:28:00 was still `skip gave-up` at 15:41:32 with the pane
    genuinely idle the whole time (`❯` bare, no `◎ /goal`), matching the
    user's own repeated report — "štvrtýkrát" — that the loop sits silent
    until manual intervention."""

    def _give_up(self, state, now):
        """Reach the give-up state via `GOAL_REARM_MAX_ATTEMPTS` REAL,
        keystroke-consuming deliveries that type+submit successfully but
        never get confirmed lit — the SAME mechanics `TestGoalRearmBounded`
        already exercises, reused here so this class tests the RESET, never
        re-derives how give-up itself is reached."""
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS):
            self._go(PANE_DARK, state=state,
                     now=now + i * (wd.GOAL_REARM_CONFIRM_S + 30),
                     cap_seq=self._typed_seq())
        late = now + wd.GOAL_REARM_MAX_ATTEMPTS * (wd.GOAL_REARM_CONFIRM_S + 30)
        _t, logs = self._go(PANE_DARK, state=state, now=late)
        self.assertTrue(any("GAVE UP" in ln.upper() for ln in logs), logs)
        return late

    def test_reset_never_fires_before_the_reset_window_elapses(self):
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        # a minute later — pane already idle again, but well short of
        # GOAL_REARM_GIVEUP_RESET_S
        t, logs = self._go(PANE_DARK, state=state, now=gave_up_at + 60)
        self.assertFalse(t.typed(), logs)
        self.assertTrue(any("skip gave-up" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 1, self.pings)

    def test_reset_never_fires_while_the_pane_stays_busy(self):
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        late = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        # PANE_DRAFT, not PANE_BUSY: a footer-undeterminable pane (no `❯`
        # boundary at all) short-circuits job 20 BEFORE it ever reaches the
        # give-up/reset logic (`armed is None: continue`) — that is a
        # DIFFERENT, already-correct "never guess" refusal, not this gate.
        # A pane holding a draft is determinable (armed=False) but NOT idle
        # at a bare prompt — the exact precondition the reset must refuse.
        t, logs = self._go(PANE_DRAFT, state=state, now=late)
        self.assertFalse(t.typed(), logs)
        self.assertTrue(any("skip gave-up" in ln for ln in logs), logs)

    def test_reset_fires_once_idle_and_the_reset_window_has_elapsed(self):
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        late = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        t, logs = self._go(PANE_DARK, state=state, now=late,
                           cap_seq=self._typed_seq())
        self.assertTrue(any("RESET" in ln.upper() for ln in logs), logs)
        self.assertTrue(t.typed(), "the reset must retry delivery this "
                        "SAME sweep, not merely clear the flag: %r" % logs)
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertFalse(rec.get("pinged"), rec)

    def test_a_genuinely_still_broken_pane_gives_up_and_pings_again(self):
        # #322's own third requirement: a delivery that REALLY keeps failing
        # on a free pane must still cap out and ping — reset is not a
        # licence to retry forever without limit.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        self.assertEqual(len(self.pings), 1, self.pings)
        late = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        self._give_up(state, late)
        self.assertEqual(len(self.pings), 2, self.pings)

    def test_reset_is_bounded_per_streak(self):
        # #322 REOPENED (adversarial-review MAJOR-1): an unbounded reset on
        # a pane that is idle-but-permanently-broken (idleness is its
        # STEADY STATE, so "idle + elapsed" alone degenerates into a bare
        # 15-minute timer) retries every GOAL_REARM_GIVEUP_RESET_S forever
        # until the blind 2h streak reset — measured live-replay: 10 pings
        # / 152 multi-KB /goal submissions into a live pane over 3h. The
        # bound converts that into "one extra try, then the SAME permanent
        # skip", which this test proves directly: after
        # GOAL_REARM_GIVEUP_MAX_RESETS resets are already spent, a THIRD
        # give-up — still idle, still well past the reset window — must
        # NOT reset again.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)          # give-up #1
        late1 = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        self._give_up(state, late1)                      # reset, then give-up #2
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec.get("giveup_resets"), wd.GOAL_REARM_GIVEUP_MAX_RESETS,
                         rec)
        self.assertEqual(len(self.pings), 2, self.pings)
        late2 = (late1 + wd.GOAL_REARM_MAX_ATTEMPTS
                * (wd.GOAL_REARM_CONFIRM_S + 30)
                + wd.GOAL_REARM_GIVEUP_RESET_S + 30)
        t, logs = self._go(PANE_DARK, state=state, now=late2)
        self.assertFalse(t.typed(), "the reset budget is spent -- no more "
                         "retries this streak: %r" % logs)
        self.assertTrue(any("skip gave-up" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 2,
                         "a third GAVE UP must not fire once the reset "
                         "budget is exhausted -- the pane stays quiet "
                         "until the natural streak reset: %r" % self.pings)

    def test_dedup_key_is_stable_across_a_reset_within_the_same_streak(self):
        # #322 REOPENED — the FIRST cut of this fix re-stamped `first` on
        # every reset, and `first` is a component of the GAVE UP ping's own
        # dedup_key (#160 defect 3's own deliberate design: `h` alone is
        # STABLE across a reset, so folding in `first` is what makes each
        # EPISODE distinct from a repeat). Re-stamping it therefore minted
        # a genuinely NEW dedup key on every retry, so the notify layer's
        # OWN dedup could never recognise a repeat give-up as a repeat.
        # This directly proves the fix: a SECOND give-up within the SAME
        # streak (after exactly one reset) must carry the IDENTICAL
        # dedup_key as the first, so `notify.send`'s own dedup absorbs it.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        late = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        self._give_up(state, late)
        self.assertEqual(len(self.pings), 2, self.pings)
        key1 = self.pings[0][1].get("dedup_key")
        key2 = self.pings[1][1].get("dedup_key")
        self.assertIsNotNone(key1)
        self.assertEqual(key1, key2,
                         "a repeat give-up in the SAME streak must share "
                         "the first give-up's dedup key, or the notify "
                         "layer's own dedup can never absorb it: %r vs %r"
                         % (key1, key2))

    def test_dry_run_never_mutates_or_consumes_the_reset_budget(self):
        # #322 REOPENED (adversarial-review MINOR-1) — mirrors the #186
        # `dark_pinged` lesson: a `--dry-run` diagnostic sweep (this repo's
        # own normal manual troubleshooting command) must never spend the
        # one-way-door reset budget or clear the give-up suppression with
        # nothing actually sent.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        late = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        t, logs = self._go(PANE_DARK, state=state, now=late, dry_run=True)
        self.assertFalse(t.typed(), logs)
        self.assertTrue(any("RESET" in ln.upper() for ln in logs), logs)
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertTrue(rec.get("pinged"), "a dry-run sweep must not clear "
                        "the real give-up suppression: %r" % rec)
        self.assertEqual(rec.get("n"), wd.GOAL_REARM_MAX_ATTEMPTS, rec)
        self.assertEqual(rec.get("giveup_resets", 0), 0,
                         "a dry-run sweep must not consume a reset: %r" % rec)
        # a REAL sweep right after must still see the full, unspent budget
        t2, logs2 = self._go(PANE_DARK, state=state, now=late + 1,
                             cap_seq=self._typed_seq())
        self.assertTrue(t2.typed(), logs2)

    def test_a_corrupt_gaveup_at_never_crashes_and_never_resets(self):
        # #322 REOPENED (adversarial-review T1) — a hand-edited/corrupted
        # `~/.claude/api-watchdog-state.json` (this repo's state file has
        # been manually pruned before, #232) could otherwise raise a
        # TypeError on `now - gaveup_at` and kill job 20 for every pane,
        # every sweep. The fail-safe direction is to never reset (never
        # guess), never crash.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        state["goal_rearm"][SID]["gaveup_at"] = "not-a-number"
        # Well past GOAL_REARM_GIVEUP_RESET_S (so the corrupted value is
        # what's actually under test), but comfortably short of
        # GOAL_REARM_STREAK_S (2h) from the streak's own `first` timestamp
        # (~`now`) -- crossing that would fire the UNRELATED natural
        # streak-reset block instead and mask this test's own intent.
        late = gave_up_at + 3 * wd.GOAL_REARM_GIVEUP_RESET_S
        self.assertLess(late - now, wd.GOAL_REARM_STREAK_S,
                        "test timing must stay inside the streak window")
        t, logs = self._go(PANE_DARK, state=state, now=late)
        self.assertFalse(t.typed(), logs)
        self.assertTrue(any("skip gave-up" in ln for ln in logs), logs)

    def test_a_new_streak_gets_a_fresh_reset_budget(self):
        # the streak-reset block (payload hash changed, or GOAL_REARM_STREAK_S
        # elapsed) must ALSO clear giveup_resets/gaveup_at -- otherwise a
        # spent budget from an OLD streak would silently disable the reset
        # for the rest of this session's life, even for a genuinely NEW
        # problem.
        state = {}
        now = time.time()
        gave_up_at = self._give_up(state, now)
        late1 = gave_up_at + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        self._give_up(state, late1)               # spends the one reset
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec.get("giveup_resets"), wd.GOAL_REARM_GIVEUP_MAX_RESETS)
        # a genuinely NEW streak, far past GOAL_REARM_STREAK_S later
        much_later = late1 + wd.GOAL_REARM_STREAK_S + 3600
        gave_up_at2 = self._give_up(state, much_later)
        rec2 = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec2.get("giveup_resets"), 0,
                         "a fresh streak must start with a fresh reset "
                         "budget: %r" % rec2)
        late2 = gave_up_at2 + wd.GOAL_REARM_GIVEUP_RESET_S + 30
        t, logs = self._go(PANE_DARK, state=state, now=late2,
                           cap_seq=self._typed_seq())
        self.assertTrue(t.typed(), "the NEW streak's own reset must still "
                        "be reachable: %r" % logs)

    def test_a_corrupt_giveup_resets_never_crashes_and_is_treated_as_spent(self):
        # #322 REOPENED (2nd adversarial-review MINOR-1) -- `.get(..., 0)`
        # only supplies the default when the key is ABSENT; a PRESENT but
        # corrupt `giveup_resets` (a hand-edited/pruned state file, the same
        # class T1 guarded `gaveup_at` against) reaches the `<` comparison
        # uncaught -- symmetric fail-safe: never crash, never guess, treat
        # an unreadable budget as already spent (never resets).
        state = {}
        now = time.time()
        self._give_up(state, now)
        state["goal_rearm"][SID]["giveup_resets"] = "not-a-number"
        late = now + 3 * wd.GOAL_REARM_GIVEUP_RESET_S
        # keep the run well inside the streak window, same discipline as
        # the sibling corrupt-gaveup_at test above.
        self.assertLess(late - now, wd.GOAL_REARM_STREAK_S)
        t, logs = self._go(PANE_DARK, state=state, now=late)
        self.assertFalse(t.typed(), logs)
        self.assertTrue(any("skip gave-up" in ln for ln in logs), logs)


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

    def test_slot_occupied_is_bounded_then_counts_as_a_real_failure(self):
        # #322 REOPENED (2nd adversarial-review MAJOR-1) -- a slot our OWN
        # collapsed-paste early return stranded (parked draft, zero further
        # keystrokes -- deliver_with_stash cannot safely recover an unproven
        # collapsed buffer) refuses every LATER sweep with this SAME
        # "stash-abort: slot occupied" reason, which the generic #101
        # carve-out above treats as transient FOREVER -- unlike a genuinely
        # foreign occupant, a slot WE stranded never self-resolves, so `n`
        # would never advance and GAVE UP would never fire. Past
        # GOAL_REARM_SLOT_STUCK_MAX CONSECUTIVE occurrences it must stop
        # being transient.
        state = {}
        now = time.time()
        with self._stub("stash-abort: slot occupied"):
            for i in range(wd.GOAL_REARM_SLOT_STUCK_MAX - 1):
                t, logs = self._go(PANE_DRAFT, state=state, now=now + i * 5,
                                   cap_seq=[PANE_DRAFT])
                self.assertFalse(t.typed(), "attempt %d" % i)
                self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs),
                                logs)
            rec = state.get("goal_rearm", {}).get(SID, {})
            self.assertEqual(rec.get("n", 0), 0,
                             "still under the bound -- must not count yet: %r"
                             % rec)
            # the Nth (bound-exceeding) occurrence stops being transient
            t, logs = self._go(
                PANE_DRAFT, state=state,
                now=now + (wd.GOAL_REARM_SLOT_STUCK_MAX - 1) * 5,
                cap_seq=[PANE_DRAFT])
            self.assertFalse(t.typed())
            self.assertFalse(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
            self.assertTrue(any("FAIL" in ln for ln in logs), logs)
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec.get("n", 0), 1,
                         "the bound-exceeding occurrence must count as a "
                         "real failure: %r" % rec)
        self.assertEqual(rec.get("slot_stuck_n", 0), 0,
                         "the consecutive counter resets once it fires: %r"
                         % rec)

    def test_slot_occupied_eventually_reaches_gave_up(self):
        # A permanently-stranded slot must still surface GAVE UP -- the
        # user gets told, instead of the reachable give-up-reset mechanism
        # (this round's own feature) being silently unreachable forever.
        state = {}
        now = time.time()
        with self._stub("stash-abort: slot occupied"):
            for i in range(50):
                t, logs = self._go(PANE_DRAFT, state=state, now=now + i * 5,
                                   cap_seq=[PANE_DRAFT])
                self.assertFalse(t.typed(), "attempt %d" % i)
                if any("GAVE UP" in ln.upper() for ln in logs):
                    break
            else:
                self.fail("never reached GAVE UP within 50 sweeps")
        self.assertEqual(len(self.pings), 1, self.pings)


class TestGoalRearmPlainBranchTransientRefusal(GoalRearmBase):
    """#322 live incident (dev1, 2026-08-08, session `2d02a127-…`, pane
    `zbynek-4:2.0`): the PLAIN (non-draft) delivery branch passes the OUTER,
    stale top-of-sweep `captured` into `_send_goal_verified`, which takes its
    OWN fresh, LIVE re-capture right before typing (#176-F3's own "the
    sweep's capture is stale by delivery time" pattern). When the pane races
    from idle (the outer capture) to busy (the inner fresh one) in that gap —
    a long foreground Bash tool call straddling the sweep — the primitive
    correctly refuses to type anything, but used to log NOTHING, so the #101
    carve-out never recognised it and the caller counted a zero-keystroke
    refusal as a real attempt. Two such races on dev1's own live session
    (`n=2, pinged=True`) permanently exhausted the cap while the pane was
    idle again a minute later."""

    def test_race_to_busy_between_the_two_captures_is_never_counted(self):
        state = {}
        now = time.time()
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS + 3):
            t, logs = self._go(PANE_DARK, state=state, now=now + i * 5,
                               cap_seq=[PANE_DARK, PANE_BUSY])
            self.assertFalse(t.typed(), "zero keystrokes on sweep %d: %r"
                             % (i, t.sent))
            self.assertTrue(any("SKIP-TRANSIENT" in ln for ln in logs), logs)
        self.assertEqual(self.pings, [],
                         "a raced-busy pre-send refusal must never trip the "
                         "give-up ping, however many sweeps it recurs on")
        rec = state.get("goal_rearm", {}).get(SID, {})
        self.assertEqual(rec.get("n", 0), 0,
                         "a zero-keystroke raced refusal must not consume "
                         "the attempt cap")

    def test_pane_settling_idle_still_delivers_after_repeated_races(self):
        state = {}
        now = time.time()
        for i in range(wd.GOAL_REARM_MAX_ATTEMPTS + 1):
            self._go(PANE_DARK, state=state, now=now + i * 5,
                    cap_seq=[PANE_DARK, PANE_BUSY])
        # the pane genuinely settled -- a real bare-box delivery, unaffected
        # by the earlier transient races
        t, logs = self._go(PANE_DARK, state=state, now=now + 1000,
                           cap_seq=self._typed_seq())
        self.assertTrue(t.typed(), logs)


class TestGoalRearmStaleMarkerIsNeverRevived(GoalRearmBase):
    """#101 live incident (dev2, 2026-07-27): the last `Goal set:` marker on
    record was 3 days old, from an already-closed autopilot run — and this
    job tried to type that dead payload into whatever the pane was now being
    used for.

    #173 refined this (dev1, 2026-08-06 — a 7h watchdog livelock, #172,
    silently stranded every session that happened to go dark during it): a
    marker this old is skipped forever ONLY when the transcript itself
    proves the darkness was a DELIBERATE `/goal clear` (#170's own
    invariant, untouched). When no `Goal cleared:` exists anywhere after
    the last `Goal set:`, the darkness is presumed a technical OUTAGE (a
    crash, a binary update, an API error, or the watchdog itself being
    unable to sweep for hours) rather than the user's own hand, and
    re-arming now continues normally past the cap — see
    `_goal_dark_died_by_outage` and `TestGoalDarkDiedByOutage` below."""

    def test_marker_with_no_clear_ever_rearms_past_the_cap(self):
        # #173 — the PRIMARY scenario this ticket exists for: nothing in
        # the transcript ever says `Goal cleared:`, so a marker this old is
        # now an outage-eligible re-arm, not a permanent dead end.
        base = time.time()
        old_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        tmux, logs = self._go(PANE_DARK,
                              entries=[marker_entry("set", PAYLOAD, old_ts)],
                              now=base, cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertTrue(any("stale-but-outage" in ln for ln in logs), logs)

    def test_dark_pinged_is_reset_by_the_outage_branch_itself(self):
        # Adversarial-review finding F2 (this ticket's own review,
        # 2026-08-06): the outage branch's own `dark_pinged` reset
        # (mirroring the ARMED branch's reset above it) had ZERO test
        # coverage — a mutant deleting it passed the WHOLE suite, because
        # every existing test that had `dark_pinged==True` going in
        # either kept the transcript unreadable (never reaching the
        # reset at all) or routed the reset through the ARMED branch's
        # OWN sibling code instead (`TestGoalDarkSilentDeadEndPings`'s
        # "later dark episode" test always revives via `PANE_LIT`). This
        # isolates the outage branch's reset specifically: establish,
        # ping while unreadable, then confirm outage (readable again,
        # still only `set`, STILL `PANE_DARK` -- never `PANE_LIT`/armed)
        # and read the flag directly.
        state = {}
        now = time.time()
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD)],
                 state=state, now=now)
        p = (Path(self.tmp.name) / wd.encode_project_dir(CWD)
             / (SID + ".jsonl"))
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        later = now + wd.GOAL_REARM_MAX_DARK_S + 60
        self._go(PANE_DARK, state=state, now=later)
        self.assertTrue(state["goal_rearm"][SID]["dark_pinged"])
        os.chmod(p, 0o600)
        even_later = later + 60
        tmux, logs = self._go(PANE_DARK, state=state, now=even_later,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertFalse(state["goal_rearm"][SID]["dark_pinged"],
                         "the outage branch must reset dark_pinged itself "
                         "-- this session was never observed ARMED "
                         "(PANE_LIT), so the sibling reset in the armed "
                         "branch never ran")

    def test_marker_with_an_explicit_clear_is_still_never_revived(self):
        # #173 — a genuine `Goal cleared:` after the last `Goal set:` keeps
        # #170's invariant intact: never re-armed, however long it has been
        # dark. This scenario reaches the EARLIER `if rec.get("mark") !=
        # "set": continue` gate (job 20's own incremental scan already
        # sees the newest marker as "cleared") -- it proves the overall
        # OBSERVABLE outcome end-to-end, never the fresh
        # `_goal_dark_died_by_outage` re-derivation itself (that function's
        # own "set-then-cleared" case is unit-tested directly in
        # `TestGoalDarkDiedByOutage`).
        base = time.time()
        old_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        cleared_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 1800)
        tmux, _logs = self._go(
            PANE_DARK,
            entries=[marker_entry("set", PAYLOAD, old_ts),
                     marker_entry("cleared", PAYLOAD, cleared_ts)],
            now=base)
        self.assertFalse(tmux.typed())

    def test_just_under_the_cap_is_the_normal_revival_path(self):
        # #173 edge case — the dark threshold is a strict `>`; comfortably
        # under it must behave exactly as before this ticket (no outage
        # log line at all — the whole dark branch is never entered).
        base = time.time()
        under_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S + 60)
        tmux, logs = self._go(
            PANE_DARK, entries=[marker_entry("set", PAYLOAD, under_ts)],
            now=base, cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertFalse(any("stale-but-outage" in ln for ln in logs), logs)
        self.assertFalse(any("skip stale-goal" in ln for ln in logs), logs)

    def test_just_over_the_cap_triggers_the_outage_branch(self):
        base = time.time()
        over_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 60)
        tmux, logs = self._go(
            PANE_DARK, entries=[marker_entry("set", PAYLOAD, over_ts)],
            now=base, cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertTrue(any("stale-but-outage" in ln for ln in logs), logs)

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

    def test_active_transcript_overrides_a_stale_last_armed(self):
        # #321 shape A2 -- `last_armed`/`mts` measures WHEN this job last
        # CONFIRMED an arm, never WHETHER the session is alive right now. A
        # continuously busy loop (live, dev1: transcript mtime in seconds,
        # `run 12/19` in the footer -- never "dark" by any real measure) can
        # run for hours without a fresh footer confirmation (the SAME
        # truncated-◎-glyph class `_goal_recover_untracked`'s own
        # `GOAL_ARMED_ACTIVITY_GRACE_S` already guards for a DIFFERENT
        # branch) -- treating that as "dark" would type a stale payload
        # into a pane whose real state is "still working". A genuine REAL
        # transcript turn (`type: user`/`assistant`, declared timestamp
        # recent) within the dark cap must be trusted OVER a stale
        # last_armed, and the session falls through to the SAME re-arm
        # machinery every non-dark session already uses -- never a new,
        # separate code path.
        state = {}
        base = time.time()
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD)],
                 state=state, now=base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        self._write([assistant_entry("still working", _iso(base))])
        tmux, logs = self._go(PANE_DARK, state=state, now=base,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertFalse(any("stale-but-outage" in ln for ln in logs), logs)
        self.assertFalse(any("skip stale-goal" in ln for ln in logs), logs)

    def test_stale_activity_still_reaches_the_dark_cap_normally(self):
        # the control: when the transcript's OWN newest real turn is ALSO
        # past the dark cap (no recent activity at all), the activity
        # signal must not manufacture a false "still alive" verdict --
        # this is the ordinary #173 outage path, unaffected by #321.
        state = {}
        base = time.time()
        old_ts = _iso(base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, old_ts)],
                 state=state, now=base - wd.GOAL_REARM_MAX_DARK_S - 3600)
        tmux, logs = self._go(PANE_DARK, state=state, now=base,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertTrue(any("stale-but-outage" in ln for ln in logs), logs)

    def test_activity_is_never_read_when_nowhere_near_the_dark_cap(self):
        # Adversarial-review finding MINOR-1 (this ticket's own review):
        # `_last_real_turn_ts` (a 2 MB tail read + a json.loads per line)
        # used to run UNCONDITIONALLY for every mark=="set" pane, every
        # 60s sweep, even when the session is nowhere near the dark cap --
        # its value is only ever consumed inside the dark `if`. The
        # #320-review "cheap gates before an expensive read" lesson
        # applies here too: gate the activity read behind the SAME cheap
        # age comparison that already exists, so a healthy session pays
        # nothing extra.
        state = {}
        base = time.time()
        fresh_ts = _iso(base - 60)
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, fresh_ts)],
                 state=state, now=base - 60)
        calls = []
        real_fn = wd._last_real_turn_ts

        def counting(tpath, tail_bytes=2_000_000):
            calls.append(tpath)
            return real_fn(tpath, tail_bytes=tail_bytes)

        with m.patch.object(wd, "_last_real_turn_ts", counting):
            self._go(PANE_DARK, state=state, now=base + 60,
                     cap_seq=self._typed_seq())
        self.assertEqual(calls, [],
                         "the activity read must be gated behind the "
                         "cheap age check, never paid for a session that "
                         "is not even candidate-dark")


class TestGoalClearedStaleWhenReallyRearmed(GoalRearmBase):
    """#320 — dev1 live incident, sid `2d02a127-...`: `rec['last_armed']`
    (job 20's own DIRECT footer-lit observation, independent of the
    transcript) can postdate `rec['mts']` (the newest transcript marker's
    own timestamp, a 'cleared' one) when a real busy-pane arm never
    produces ANY transcript marker at all — confirmed live on dev1's real
    state/transcript: three `goal-autoarm OK` sends after the clear, zero
    resulting markers of either shape anywhere in the whole file. The state
    machine used to have no way to notice this contradiction and skipped
    the session forever at `if rec.get("mark") != "set": continue`."""

    def test_last_armed_after_the_clear_is_treated_as_a_rearm(self):
        state = {}
        now0 = time.time()
        set_ts = _iso(now0)
        arm1_ts = now0 + 60
        clear_ts = now0 + 120
        arm2_ts = now0 + 180
        # Step 1: a genuine early arm -- last_armed=arm1_ts, mark="set".
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, set_ts)],
                state=state, now=arm1_ts)
        # Step 2: a genuine clear AFTER that arm -- mark flips to "cleared",
        # last_armed stays arm1_ts (< clear_ts) -- #170 intact so far.
        self._go(PANE_DARK, entries=[marker_entry("cleared", PAYLOAD,
                                                   _iso(clear_ts))],
                state=state, now=clear_ts + 10)
        self.assertEqual(state["goal_rearm"][SID]["mark"], "cleared")
        # Step 3: the footer shows armed AGAIN, AFTER the clear (a real
        # busy-pane arm producing NO new transcript marker at all --
        # dev1's own live shape) -- last_armed becomes arm2_ts > clear_ts.
        self._go(PANE_LIT, state=state, now=arm2_ts)
        self.assertEqual(state["goal_rearm"][SID]["mark"], "cleared",
                         "still cleared per the transcript's own view")
        self.assertGreater(state["goal_rearm"][SID]["last_armed"],
                           state["goal_rearm"][SID]["mts"])
        # Step 4: footer goes dark again, well within the dark cap -- the
        # contradiction (last_armed > mts while mark == "cleared") must now
        # be treated as a re-arm, not a permanent #170 skip.
        tmux, logs = self._go(PANE_DARK, state=state, now=arm2_ts + 30,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertEqual(state["goal_rearm"][SID]["mark"], "set")
        self.assertTrue(any("stale-cleared-but-rearmed" in ln for ln in logs),
                        logs)

    def test_a_clean_clear_with_no_later_arm_is_still_never_revived(self):
        # #170's own control: last_armed predates the clear (the arm was
        # BEFORE the clear, the well-behaved case) -> unchanged behaviour.
        state = {}
        now0 = time.time()
        set_ts = _iso(now0)
        clear_ts = now0 + 120
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, set_ts)],
                state=state, now=now0 + 60)
        tmux, _logs = self._go(PANE_DARK,
                               entries=[marker_entry("cleared", PAYLOAD,
                                                     _iso(clear_ts))],
                               state=state, now=clear_ts + 10)
        self.assertFalse(tmux.typed())
        self.assertEqual(state["goal_rearm"][SID]["mark"], "cleared")

    def test_dev1_incident_replayed_stale_and_dark_now_rearms_past_the_cap(self):
        # The REAL live shape (2d02a127, verified 2026-08-08, #320) -- AND
        # its own aftermath (#321, live: 15h+ of "skip stale-goal
        # (goal-rearm) zbynek-4:2.0 (54886 s since last confirmed armed)"
        # repeating in the journal every 60s WITHOUT interruption, despite
        # `mark` having already correctly flipped to "set" via #320).
        #
        # This test used to assert the OPPOSITE outcome
        # (`test_dev1_incident_replayed_stale_and_dark_still_flips_and_pings`,
        # `assertFalse(tmux.typed())` + "skip stale-goal") -- that was the
        # exact #321 bug encoded as expected behaviour: `_goal_cleared_stale`
        # flips `mark` to "set" this sweep, but `_goal_dark_died_by_outage`'s
        # OWN independent fresh re-read finds the SAME newest 'cleared'
        # marker and disbelieves it, permanently re-skipping the session
        # #320 had just revived (never a one-shot ping -- the JOURNAL LINE
        # itself repeats every sweep forever, only the Discord ping is
        # one-shot). #321 fixes it by passing `last_armed` through so
        # `_goal_dark_died_by_outage` applies the IDENTICAL staleness rule
        # `_goal_cleared_stale` already used, instead of a second one that
        # can contradict it -- the session now falls through into the SAME
        # outage/re-arm machinery every other tracked session already uses,
        # exactly as #173's own decided semantics require ("re-arm continues
        # normally past the cap", never a permanent stale-goal skip).
        state = {}
        now0 = time.time()
        set_ts = _iso(now0 - wd.GOAL_REARM_MAX_DARK_S - 7200)
        clear_ts = now0 - wd.GOAL_REARM_MAX_DARK_S - 3600
        arm2_ts = now0 - wd.GOAL_REARM_MAX_DARK_S - 1800
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD, set_ts)],
                state=state, now=now0 - wd.GOAL_REARM_MAX_DARK_S - 7100)
        self._go(PANE_DARK, entries=[marker_entry("cleared", PAYLOAD,
                                                   _iso(clear_ts))],
                state=state, now=clear_ts + 10)
        self._go(PANE_LIT, state=state, now=arm2_ts)
        tmux, logs = self._go(PANE_DARK, state=state, now=now0,
                              cap_seq=self._typed_seq())
        self.assertTrue(tmux.typed(), logs)
        self.assertEqual(state["goal_rearm"][SID]["mark"], "set")
        self.assertFalse(state["goal_rearm"][SID].get("dark_pinged"),
                         "the outage branch never pings -- only the "
                         "permanent-skip branch does")
        self.assertTrue(any("stale-cleared-but-rearmed" in ln for ln in logs),
                        logs)
        self.assertTrue(any("stale-but-outage" in ln for ln in logs), logs)
        self.assertFalse(any("skip stale-goal" in ln for ln in logs), logs)
        self.assertEqual(len(self.pings), 0, self.pings)


class TestGoalDarkDiedByOutage(unittest.TestCase):
    """Direct unit coverage of `_goal_dark_died_by_outage` (#173) — the
    reused #266 transcript mechanism `goal_rearm`'s dark-cap decision now
    re-derives INDEPENDENTLY of job 20's own incremental `rec['mark']`
    bookkeeping, right before the risky "type a potentially stale payload"
    decision.

    Fail-safe direction is the OPPOSITE of `_goal_was_cleared_by_user`'s:
    job 9 arms a FRESH session, so failing open (an unreadable transcript
    keeps arming) is the safe direction there. This function revives a
    goal that has been genuinely dark for `GOAL_REARM_MAX_DARK_S` (>=6h)
    into a pane whose current use is unknown, so an unreadable, absent, or
    markerless transcript must stay CONSERVATIVE here: False — never
    presume an outage without proof."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, *entries):
        return write_transcript(list(entries), self.tmp.name)

    def test_missing_transcript_is_not_an_outage(self):
        missing = Path(self.tmp.name) / "nope.jsonl"
        self.assertFalse(wd._goal_dark_died_by_outage(missing))

    def test_unreadable_transcript_is_not_an_outage(self):
        p = self._write(marker_entry("set", PAYLOAD))
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        self.assertFalse(wd._goal_dark_died_by_outage(p))

    def test_markerless_transcript_is_not_an_outage(self):
        p = self._write({"type": "assistant", "message": {"content": "hi"}})
        self.assertFalse(wd._goal_dark_died_by_outage(p))

    def test_only_a_set_marker_no_clear_ever_is_an_outage(self):
        p = self._write(marker_entry("set", PAYLOAD))
        self.assertTrue(wd._goal_dark_died_by_outage(p))

    def test_set_then_cleared_is_not_an_outage(self):
        p = self._write(marker_entry("set", PAYLOAD),
                        marker_entry("cleared", PAYLOAD,
                                     "2026-07-26T13:10:00.000Z"))
        self.assertFalse(wd._goal_dark_died_by_outage(p))

    def test_set_cleared_set_only_the_last_pair_matters(self):
        # a full set/clear/set cycle -- the goal was cleared, then armed
        # again; the NEWEST marker is `set`, so this is an outage-eligible
        # episode, not a governed clear.
        p = self._write(marker_entry("set", PAYLOAD,
                                     "2026-07-26T10:00:00.000Z"),
                        marker_entry("cleared", PAYLOAD,
                                     "2026-07-26T11:00:00.000Z"),
                        marker_entry("set", PAYLOAD,
                                     "2026-07-26T12:00:00.000Z"))
        self.assertTrue(wd._goal_dark_died_by_outage(p))

    def test_set_cleared_set_cleared_only_the_last_pair_matters(self):
        p = self._write(marker_entry("set", PAYLOAD,
                                     "2026-07-26T10:00:00.000Z"),
                        marker_entry("cleared", PAYLOAD,
                                     "2026-07-26T11:00:00.000Z"),
                        marker_entry("set", PAYLOAD,
                                     "2026-07-26T12:00:00.000Z"),
                        marker_entry("cleared", PAYLOAD,
                                     "2026-07-26T13:00:00.000Z"))
        self.assertFalse(wd._goal_dark_died_by_outage(p))

    def test_a_set_marker_buried_past_the_old_tail_window_is_still_an_outage(self):
        # Adversarial-review finding F1 (this ticket's own review,
        # 2026-08-06, live-reproduced against the tail-bootstrap form):
        # a long-lived, busy loop keeps WRITING for hours before it dies
        # (exactly the #172 stranded population #173 exists to revive),
        # so its own `Goal set:` marker can end up sitting well past
        # `GOAL_MARK_TAIL_BYTES` (4 MB) before EOF by the time this check
        # runs. A bootstrap-from-tail read would find NOTHING in that
        # window and wrongly conclude "not an outage" -- the read must
        # cover the WHOLE file (`off=0`), never just the tail.
        p = self._write(marker_entry("set", PAYLOAD))
        with open(p, "a") as f:
            filler = json.dumps({"type": "assistant",
                                 "message": {"content": "x" * 900}}) + "\n"
            while f.tell() < wd.GOAL_MARK_TAIL_BYTES + 200_000:
                f.write(filler)
        self.assertGreater(p.stat().st_size, wd.GOAL_MARK_TAIL_BYTES,
                           "fixture must genuinely exceed the tail window")
        self.assertTrue(wd._goal_dark_died_by_outage(p))

    def test_last_armed_is_ignored_by_default_unchanged_behaviour(self):
        # every existing caller (this whole test class) calls with a single
        # positional arg -- the new param must default to a no-op so none of
        # them need to change.
        p = self._write(marker_entry("cleared", PAYLOAD))
        self.assertFalse(wd._goal_dark_died_by_outage(p))

    def test_a_stale_cleared_marker_is_not_a_real_clear_when_last_armed_postdates_it(self):
        # #321 shape A1 -- the caller's OWN #320 determination
        # (`_goal_cleared_stale`: a genuine arm happened AFTER this exact
        # marker, even though CC wrote no marker for it) must not be
        # independently disbelieved by this function's own fresh re-read of
        # the SAME marker -- reusing the IDENTICAL staleness rule, not a
        # second contradicting one.
        cleared_ts = time.time() - 100
        p = self._write(marker_entry("cleared", PAYLOAD, _iso(cleared_ts)))
        last_armed = cleared_ts + 50
        self.assertTrue(wd._goal_dark_died_by_outage(p, last_armed))

    def test_a_genuine_clear_after_last_armed_is_still_not_an_outage(self):
        # #170's own control, re-derived with `last_armed` now in play: the
        # arm predates the clear (the well-behaved, deliberate case) -> this
        # function must still refuse to treat it as an outage.
        cleared_ts = time.time()
        p = self._write(marker_entry("cleared", PAYLOAD, _iso(cleared_ts)))
        last_armed = cleared_ts - 50
        self.assertFalse(wd._goal_dark_died_by_outage(p, last_armed))

    def test_last_armed_never_overrides_a_genuine_set_marker_result(self):
        # a "set" marker is already an outage on its own merits (no clear at
        # all) -- passing last_armed must not change that verdict either way.
        p = self._write(marker_entry("set", PAYLOAD))
        self.assertTrue(wd._goal_dark_died_by_outage(p, time.time()))

    def test_last_armed_without_a_marker_ts_is_never_guessed(self):
        # an unparsed/missing marker timestamp is unmeasurable -- never
        # treated as "stale" just because SOME last_armed value was passed.
        p = self._write({"type": "user",
                         "timestamp": "not-a-real-timestamp",
                         "message": {"content":
                                     "<local-command-stdout>Goal cleared: %s"
                                     "</local-command-stdout>" % PAYLOAD}})
        self.assertFalse(wd._goal_dark_died_by_outage(p, time.time()))


class TestGoalRecoverUntracked(GoalRearmBase):
    """#312 -- `goal_rearm`'s `rec.get("mark")` used to collapse TWO
    structurally different states into one silent-forever `continue`:
    `mark == "cleared"` (an explicit clear THIS job itself scanned, the
    #170 class -- MUST stay untouchable) versus `mark is None` (this job
    never tracked this session's goal history at all -- e.g. a
    hand-adapted goal armed before this sid was first swept, or any other
    gap in an earlier sweep's incremental tail-bootstrap). Only the second
    is a genuine coverage gap. `_goal_recover_untracked` attempts a ONE-
    SHOT full-file rescan for it, gated on live `autopilot-progress`
    evidence (never an unconditional rescan -- deploy-day safety: measured
    live on dev1, 14 of 21 currently-tracked sessions have no resolved
    `mark` at all, several backing multi-hundred-MB transcripts) and on
    the transcript having gone genuinely quiet (never while it was written
    to moments ago -- a truncated `◎ /goal` footer glyph, live-confirmed
    on a wide custom statusline, must not be misread as a dead goal).

    NOT the #64 fleet-template-hash mechanism the ticket's own filed
    hypothesis named -- `_goal_template_drift`'s `tvar` gates only the
    SEPARATE stale-template re-sync sub-feature, reachable exclusively
    from the ARMED branch, confirmed by code trace to play no part in
    this dark-goal path at all."""

    def _buried_marker_transcript(self, kind="set", ts=None):
        p = self._write([marker_entry(kind, PAYLOAD, ts)])
        with open(p, "a") as f:
            filler = json.dumps({"type": "assistant",
                                 "message": {"content": "x" * 900}}) + "\n"
            while f.tell() < wd.GOAL_MARK_TAIL_BYTES + 200_000:
                f.write(filler)
        self.assertGreater(p.stat().st_size, wd.GOAL_MARK_TAIL_BYTES,
                           "fixture must genuinely exceed the tail window")
        self._wrote = True
        return p

    def _future_now(self):
        # far enough past the fixture's own (real, "just written") mtime
        # to clear GOAL_ARMED_ACTIVITY_GRACE_S -- avoids mocking os.utime.
        return time.time() + wd.GOAL_ARMED_ACTIVITY_GRACE_S + 3600

    def _live_progress_dir(self, ts):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "demo.json").write_text(
            json.dumps({"done": 3, "remaining": 2, "ts": ts}))
        return d.name

    def test_marker_buried_past_tail_is_recovered_with_live_progress(self):
        self._buried_marker_transcript()
        future = self._future_now()
        pd = self._live_progress_dir(future)
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, logs = self._go(PANE_DARK, cap_seq=self._typed_seq(),
                                  now=future, progress_dir=pd)
        self.assertIn(GOAL_LINE, tmux.typed(), logs)
        self.assertTrue(any("RECOVERED" in ln for ln in logs), logs)

    def test_no_progress_file_never_recovers(self):
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, now=future,
                                   progress_dir=empty_dir.name)
        self.assertFalse(tmux.typed())

    def test_no_progress_logs_once_then_stays_quiet(self):
        # #321 shape B (montalu2@subdev %0, odoo-erp): this bail-out used to
        # be TOTALLY SILENT every sweep, forever, for any session whose
        # /goal is not a batch /autopilot loop with a live progress
        # heartbeat (an ordinary, manually-armed /goal has none by design)
        # -- live evidence: zero journal lines containing "goal" across 30
        # real minutes for a session with a dark footer and two goal_rearm
        # recs that never got a `mark`. Logged ONCE per session (mirrors
        # the `goalarm_cleared`/`goalarm_noresolve` log-once-then-quiet
        # shape already used elsewhere in this file) so a human can SEE why
        # nothing is happening, without a line every 60s forever -- the
        # recovery decision itself is UNCHANGED (still never acts without
        # live batch-progress evidence).
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux1, logs1 = self._go(PANE_DARK, state=state, now=future,
                                    progress_dir=empty_dir.name)
            tmux2, logs2 = self._go(PANE_DARK, state=state, now=future + 60,
                                    progress_dir=empty_dir.name)
        self.assertFalse(tmux1.typed())
        self.assertFalse(tmux2.typed())
        self.assertTrue(any("skip untracked-no-progress" in ln
                            for ln in logs1), logs1)
        self.assertFalse(any("skip untracked-no-progress" in ln
                             for ln in logs2), logs2)

    def test_no_progress_marker_clears_once_progress_becomes_live(self):
        self._buried_marker_transcript()
        future = self._future_now()
        state = {}
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            self.assertTrue(
                state.get("goalrearm_noprogress", {}).get(SID))
            pd = self._live_progress_dir(future)
            tmux, logs = self._go(PANE_DARK, state=state, now=future + 60,
                                  cap_seq=self._typed_seq(), progress_dir=pd)
        self.assertNotIn(SID, state.get("goalrearm_noprogress", {}))
        self.assertTrue(any("RECOVERED" in ln for ln in logs), logs)
        self.assertTrue(tmux.typed(), logs)

    def test_no_state_seam_never_logs_and_never_crashes(self):
        # `state` is optional -- a caller that never wires it must behave
        # exactly as before this ticket: silent, no crash.
        self._buried_marker_transcript()
        p = (Path(self.tmp.name) / wd.encode_project_dir(CWD)
             / (SID + ".jsonl"))
        rec = {}
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        logs = wd._goal_recover_untracked(
            self._future_now(), rec, SID, CWD, p, time.time(), "demo",
            progress_dir=empty_dir.name)
        self.assertEqual(logs, [])

    def test_stale_progress_file_never_recovers(self):
        self._buried_marker_transcript()
        future = self._future_now()
        pd = self._live_progress_dir(future - 7 * 3600)   # past the 6h window
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, now=future, progress_dir=pd)
        self.assertFalse(tmux.typed())

    def test_zero_remaining_never_recovers(self):
        self._buried_marker_transcript()
        future = self._future_now()
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "demo.json").write_text(
            json.dumps({"done": 5, "remaining": 0, "ts": future}))
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, now=future, progress_dir=d.name)
        self.assertFalse(tmux.typed())

    def test_unresolvable_repo_never_recovers(self):
        self._buried_marker_transcript()
        future = self._future_now()
        pd = self._live_progress_dir(future)
        with m.patch("notify.repo_name_for", return_value=""):
            tmux, _logs = self._go(PANE_DARK, now=future, progress_dir=pd)
        self.assertFalse(tmux.typed())

    def test_recent_transcript_activity_defers_recovery(self):
        # #312-hardening -- a footer read of "not armed" can be a
        # TRUNCATED render (a wide custom statusline cutting off the ◎
        # glyph) rather than a genuinely dead goal; a transcript written
        # to moments ago is independent evidence the session may still be
        # alive -- recovery must defer to a later sweep, never act now.
        self._buried_marker_transcript()
        now = time.time()   # the fixture's OWN real, just-written mtime
        pd = self._live_progress_dir(now)
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, logs = self._go(PANE_DARK, now=now, progress_dir=pd)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("maybe-truncated" in ln for ln in logs), logs)

    def test_the_full_scan_never_repeats(self):
        self._buried_marker_transcript()
        future = self._future_now()
        pd = self._live_progress_dir(future)
        calls = []
        real_scan = wd.scan_goal_markers

        def counting_scan(path, off=None, tail_bytes=wd.GOAL_MARK_TAIL_BYTES):
            if off == 0:
                calls.append(path)
            return real_scan(path, off=off, tail_bytes=tail_bytes)

        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"), \
             m.patch.object(wd, "scan_goal_markers", counting_scan):
            self._go(PANE_DARK, state=state, now=future,
                     cap_seq=self._typed_seq(), progress_dir=pd)
            self._go(PANE_DARK, state=state, now=future + 60,
                     cap_seq=self._typed_seq(), progress_dir=pd)
        self.assertEqual(len(calls), 1, calls)

    def test_a_buried_but_since_cleared_goal_stays_untouched(self):
        # the #170 guard survives the recovery scan -- a session whose
        # LATEST buried marker is a deliberate clear is never re-armed,
        # even once live progress evidence makes it eligible for the scan.
        #
        # Adversarial-review finding F4 (this ticket's own review, verified
        # by mutation): the ORIGINAL fixture wrote the "cleared" marker
        # AFTER the filler, i.e. right at EOF -- squarely INSIDE the
        # ordinary tail-bootstrap window, so the REGULAR per-sweep scan
        # (not this recovery path at all) already resolves `rec['mark'] ==
        # "cleared"` on the very first sweep, and `_goal_recover_untracked`
        # is never even called (instrumented proof: 0 calls). The test
        # passed for the wrong reason -- mutating away the recovery path's
        # own `mark.get("state") != "set"` guard left it green. Both
        # markers must be BURIED past the tail window for this test to
        # actually exercise the recovery scan's own #170 guard.
        p = self._write([marker_entry("set", PAYLOAD,
                                      _iso(time.time() - 7200))])
        with open(p, "a") as f:
            f.write(json.dumps(marker_entry("cleared", PAYLOAD)) + "\n")
            filler = json.dumps({"type": "assistant",
                                 "message": {"content": "x" * 900}}) + "\n"
            while f.tell() < wd.GOAL_MARK_TAIL_BYTES + 200_000:
                f.write(filler)
        self.assertGreater(p.stat().st_size, wd.GOAL_MARK_TAIL_BYTES,
                           "fixture must genuinely exceed the tail window")
        self._wrote = True
        future = self._future_now()
        pd = self._live_progress_dir(future)
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, now=future, progress_dir=pd)
        self.assertFalse(tmux.typed(),
                         "a deliberately cleared goal must never be "
                         "re-armed even after a full-file recovery scan")


class TestGoalRecoverUntrackedAlternativeEvidence(GoalRearmBase):
    """#324 — montalu2@subdev's own live incident: footer dark,
    `rec['mark']` never tracked, no live autopilot-progress heartbeat for
    a manually-armed (non-`/autopilot`-batch) `/goal` loop — a shape for
    which `_live_autopilot_progress` can NEVER become true, so recovery
    used to have no reachable exit at all. Two fixes: (1) a `goalarm`
    record (job 9's own per-pane dedup, proving a real arm was attempted
    at some point) is a second, independent, cheap evidence source that
    unlocks the SAME one-shot scan; (2) when NEITHER evidence source ever
    resolves, this no longer waits silently forever — it escalates with
    ONE deduped Discord ping past `GOAL_REARM_UNTRACKED_PING_S`."""

    def _buried_marker_transcript(self, kind="set", ts=None):
        p = self._write([marker_entry(kind, PAYLOAD, ts)])
        with open(p, "a") as f:
            filler = json.dumps({"type": "assistant",
                                 "message": {"content": "x" * 900}}) + "\n"
            while f.tell() < wd.GOAL_MARK_TAIL_BYTES + 200_000:
                f.write(filler)
        self.assertGreater(p.stat().st_size, wd.GOAL_MARK_TAIL_BYTES,
                           "fixture must genuinely exceed the tail window")
        self._wrote = True
        return p

    def _future_now(self):
        return time.time() + wd.GOAL_ARMED_ACTIVITY_GRACE_S + 3600

    def _typed_seq(self, text=GOAL_LINE):
        typed_pane = CONV + FOOTER_DARK.replace(
            "❯ \n", "❯ " + text[-40:] + "\n")
        return [PANE_DARK, PANE_DARK, typed_pane, PANE_DARK]

    def test_goalarm_record_is_alternative_evidence_for_recovery(self):
        # no live autopilot-progress file at all (progress_dir defaults to
        # the real, almost-certainly-empty ~/.claude/autopilot-progress for
        # a repo named "demo") — but `state['goalarm']` DOES carry an entry
        # for this exact pane (`%1`, the pid FakeTmux reports), proving
        # job 9 genuinely attempted a real arm against it at some point.
        # That alone must be enough to unlock the recovery scan.
        self._buried_marker_transcript()
        future = self._future_now()
        state = {"goalarm": {"%1": future - 3600}}
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, logs = self._go(PANE_DARK, state=state, now=future,
                                  cap_seq=self._typed_seq())
        self.assertIn(GOAL_LINE, tmux.typed(), logs)
        self.assertTrue(any("RECOVERED" in ln for ln in logs), logs)
        self.assertTrue(any("goalarm record" in ln for ln in logs), logs)

    def test_no_goalarm_record_and_no_progress_never_recovers(self):
        # neither evidence source present — the scan must never be paid
        # for, and nothing gets typed.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, state=state, now=future,
                                   progress_dir=empty_dir.name)
        self.assertFalse(tmux.typed())

    def test_untracked_with_no_evidence_pings_after_the_grace_window(self):
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            tmux2, logs2 = self._go(
                PANE_DARK, state=state,
                now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                progress_dir=empty_dir.name)
        self.assertFalse(tmux2.typed(), logs2)
        self.assertTrue(any("ESCALATED" in ln for ln in logs2), logs2)
        self.assertEqual(len(self.pings), 1, self.pings)

    def test_untracked_ping_fires_only_once(self):
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            self._go(PANE_DARK, state=state,
                    now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                    progress_dir=empty_dir.name)
            self._go(PANE_DARK, state=state,
                    now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 600,
                    progress_dir=empty_dir.name)
        self.assertEqual(len(self.pings), 1, self.pings)

    def test_untracked_state_clears_once_evidence_appears(self):
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            self._go(PANE_DARK, state=state,
                    now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                    progress_dir=empty_dir.name)
            self.assertTrue(
                state.get("goalrearm_untracked_pinged", {}).get(SID))
            pd = self._live_progress_dir(
                future + wd.GOAL_REARM_UNTRACKED_PING_S + 120)
            tmux, logs = self._go(
                PANE_DARK, state=state,
                now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 120,
                cap_seq=self._typed_seq(), progress_dir=pd)
        self.assertNotIn(SID, state.get("goalrearm_noprogress", {}))
        self.assertNotIn(SID, state.get("goalrearm_untracked_pinged", {}))
        self.assertTrue(any("RECOVERED" in ln for ln in logs), logs)
        self.assertTrue(tmux.typed(), logs)

    def test_a_stale_goalarm_record_is_never_alternative_evidence(self):
        # #324-review MAJOR-4 -- a `state['goalarm']` entry has no
        # freshness bound of its own (the store is never pruned and its
        # key, a tmux pane id, can be reused across a server restart) —
        # bound it by the SAME `GOAL_REARM_PROGRESS_WINDOW_S` window
        # `_live_autopilot_progress` already uses, or a genuinely stale
        # entry left over from an unrelated, long-gone session wrongly
        # unlocks recovery and types a payload into today's pane.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {"goalarm": {"%1": future - wd.GOAL_REARM_PROGRESS_WINDOW_S
                             - 3600}}
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, state=state, now=future,
                                   progress_dir=empty_dir.name)
        self.assertFalse(tmux.typed())

    def test_a_future_dated_goalarm_record_is_never_alternative_evidence(self):
        # #324-review MAJOR-4's own `0 <=` lower clamp — a clock-skewed
        # FUTURE timestamp must never look "already fresh" either.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {"goalarm": {"%1": future + 3600}}
        with m.patch("notify.repo_name_for", return_value="demo"):
            tmux, _logs = self._go(PANE_DARK, state=state, now=future,
                                   progress_dir=empty_dir.name)
        self.assertFalse(tmux.typed())

    def test_a_legacy_bool_first_seen_does_not_ping_on_the_first_sweep(self):
        # #324-review CRITICAL-2 — a pre-#324 record stored a bare `True`
        # for `goalrearm_noprogress[sid]` (never a timestamp). Since bool
        # is a subclass of int in Python, `now - True` == `now - 1`,
        # which clears the 30-min bound by decades and would ping on the
        # very FIRST sweep after this fix deploys, quoting a nonsense
        # "vyše decades of minutes" age — reproduced live against dev1's
        # own real persisted state before this fix. A legacy/garbage
        # value must be treated as unset and re-stamped to `now`, exactly
        # like a genuinely first sighting.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {"goalrearm_noprogress": {SID: True}}
        with m.patch("notify.repo_name_for", return_value="demo"):
            _tmux, logs = self._go(PANE_DARK, state=state, now=future,
                                   progress_dir=empty_dir.name)
        self.assertFalse(self.pings, self.pings)
        self.assertFalse(any("ESCALATED" in ln for ln in logs), logs)
        self.assertIsInstance(
            state.get("goalrearm_noprogress", {}).get(SID), float,
            "a legacy bool must be re-stamped to a real timestamp, not "
            "trusted verbatim")

    def test_a_future_dated_first_seen_never_escalates(self):
        # #324-review MINOR-5 — the same re-stamp guard, the other
        # direction: a future-dated `first_seen` (clock skew, a restored
        # snapshot) must never silently disable the escalation forever
        # by making `now - first_seen` permanently negative.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {"goalrearm_noprogress": {SID: future + 3600}}
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            _tmux2, logs2 = self._go(
                PANE_DARK, state=state,
                now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                progress_dir=empty_dir.name)
        self.assertTrue(any("ESCALATED" in ln for ln in logs2), logs2)
        self.assertEqual(len(self.pings), 1, self.pings)

    def test_two_separate_dark_episodes_get_distinct_dedup_keys(self):
        # #324-review MAJOR-3 — `dedup_key` used to be `sid` alone, which
        # is STABLE across a revival: a session whose bookkeeping clears
        # (evidence flickered briefly true, or the state was otherwise
        # reset), then goes dark again with no evidence a SECOND time,
        # would produce the IDENTICAL dedup key for the genuinely new
        # episode — silently suppressed at the notify layer for
        # `notify._DEDUP_TTL_S` (14 days). Folding in `first_seen` (each
        # episode's own anchor) makes every episode's key distinct.
        #
        # This drives the escalation branch directly TWICE with the
        # bookkeeping cleared in between (never letting `has_progress`
        # go true, which would permanently resolve `rec['mark']` and
        # route the session out of `_goal_recover_untracked` for good —
        # a genuinely unrelated code path, not what this test is about).
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            # Episode 1: dark, no evidence, escalates.
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name)
            self._go(PANE_DARK, state=state,
                    now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                    progress_dir=empty_dir.name)
            self.assertEqual(len(self.pings), 1, self.pings)
            # The bookkeeping clears (whatever real cause resolved it —
            # this test isolates the DEDUP-KEY claim, not the exact
            # clearing trigger).
            state.get("goalrearm_noprogress", {}).pop(SID, None)
            state.get("goalrearm_untracked_pinged", {}).pop(SID, None)
            # Episode 2: genuinely NEW dark stretch, no evidence again.
            ep2_start = future + wd.GOAL_REARM_UNTRACKED_PING_S + 200
            self._go(PANE_DARK, state=state, now=ep2_start,
                     progress_dir=empty_dir.name)
            self._go(PANE_DARK, state=state,
                    now=ep2_start + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                    progress_dir=empty_dir.name)
        self.assertEqual(len(self.pings), 2, self.pings)
        key1 = self.pings[0][1].get("dedup_key")
        key2 = self.pings[1][1].get("dedup_key")
        self.assertIsNotNone(key1)
        self.assertIsNotNone(key2)
        self.assertNotEqual(key1, key2,
                            "each dark episode must get its own dedup key")

    def test_dry_run_never_sends_a_real_ping(self):
        # #324-review CRITICAL-1 -- a `--dry-run` sweep (this repo's own
        # documented manual diagnostic, routinely run against REAL
        # persisted state) must NEVER mark `pinged` — only report READY.
        # Assert on the FLAG itself, not just on `self.pings`: the
        # original bug marked `pinged[sid] = True` BEFORE checking
        # `dry_run`, which permanently consumed the one-shot escalation
        # even though nothing was ever actually sent — `self.pings`
        # alone stayed empty either way and could not catch it.
        self._buried_marker_transcript()
        future = self._future_now()
        empty_dir = TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        state = {}
        with m.patch("notify.repo_name_for", return_value="demo"):
            self._go(PANE_DARK, state=state, now=future,
                     progress_dir=empty_dir.name, dry_run=True)
            _tmux2, logs2 = self._go(
                PANE_DARK, state=state,
                now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 60,
                progress_dir=empty_dir.name, dry_run=True)
        self.assertFalse(self.pings, self.pings)
        self.assertTrue(any("READY" in ln for ln in logs2), logs2)
        self.assertFalse(
            state.get("goalrearm_untracked_pinged", {}).get(SID),
            "a dry-run sweep must never mark the one-shot ping consumed")
        # A REAL sweep afterward, still past the grace window, must
        # still escalate for real — proving the dry-run runs above never
        # permanently disabled it.
        with m.patch("notify.repo_name_for", return_value="demo"):
            _tmux3, logs3 = self._go(
                PANE_DARK, state=state,
                now=future + wd.GOAL_REARM_UNTRACKED_PING_S + 120,
                progress_dir=empty_dir.name)
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(any("ESCALATED" in ln for ln in logs3), logs3)

    def _live_progress_dir(self, ts):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "demo.json").write_text(
            json.dumps({"done": 3, "remaining": 2, "ts": ts}))
        return d.name


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
                              cap_seq=[PANE_DARK, PANE_DARK, typed_pane, PANE_DARK])
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
                              cap_seq=[PANE_DARK, PANE_DARK, typed, typed, PANE_DARK])
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


class _SendVerifiedSwallowFake:
    """A STATEFUL bare-box pane for `_send_goal_verified` (never a scripted
    cap_seq — the poll loops inside `_await_typed` need an unbounded number
    of captures). Models the #36 agent-strip class of bug: Enter neither
    submits nor clears the box until `swallow_enters` is exhausted.

    ALSO models CC's single-slot prompt stash (`C-s` parks/pops, mirroring
    `FakePane.key` in tests/test_stash_unconditional.py) — needed to give
    `parked=False` (`_send_goal_verified` never parks anything; there is no
    foreign draft to protect on this bare-box-only primitive) a mutation-
    provable regression lock: a mutant hardcoding `parked=True` at that
    call site would fire a real `C-s`, which this fake can now observe."""

    def __init__(self, swallow_enters=0):
        self.box = ""
        self.stash = None
        self.swallow_enters = swallow_enters
        self.sent = []

    def _render(self):
        if self.box:
            return CONV + FOOTER_DARK.replace("❯ \n", "❯ " + self.box + "\n")
        return CONV + FOOTER_DARK

    def __call__(self, argv, timeout=8):
        self.sent.append(argv)
        j = " ".join(argv)
        if "capture-pane" in j:
            return self._render()
        if "display-message" in j:
            return "sess:0.0"
        if argv[:2] == ["tmux", "send-keys"]:
            if "-l" in argv:
                self.box += argv[-1]
            else:
                for k in argv[4:]:
                    if k == "Enter":
                        if self.swallow_enters > 0:
                            self.swallow_enters -= 1
                        elif self.box:
                            self.box = ""
                    elif k == "BSpace":
                        self.box = self.box[:-1]
                    elif k == "C-s":
                        if self.box and self.stash is None:
                            self.stash, self.box = self.box, ""
                        elif not self.box and self.stash is not None:
                            self.box, self.stash = self.stash, None
        return ""

    def keys(self):
        return [a[-1] for a in self.sent
                if "send-keys" in " ".join(a) and "-l" not in a]


class SendGoalVerifiedSwallowedSubmitLeavesBoxRecoverable(unittest.TestCase):
    """#306 sibling gap: `_send_goal_verified` (job 20's own no-draft
    primitive — its docstring calls it "the same protocol `deliver_with_stash`
    uses for its own type/submit steps, minus the stash") had the identical
    zero-recovery gap on its swallowed-submit path — on
    `if _await_typed(pid, text, run, sleep_fn, want=False): return False`
    it simply returned, leaving our typed `/goal …` text glued in the box.
    There is no draft to protect here (the box was verified bare before
    typing), so the fix only needs the backspace half of
    `deliver_with_stash`'s recovery."""

    def test_permanently_swallowed_submit_backspaces_our_own_text(self):
        text = "/goal " + PAYLOAD
        # 2 Enters get sent by `_send_goal_verified` on a fully-swallowed
        # submit (the original + one corrective retry) -- swallow both.
        tmux = _SendVerifiedSwallowFake(swallow_enters=2)
        ok = wd._send_goal_verified("%1", text, tmux, sleep_fn=lambda s: None)
        self.assertFalse(ok, tmux.sent)
        self.assertEqual(tmux.box, "",
                         "our own text must be backspaced out, never left "
                         "sitting in the box: %r" % tmux.sent)
        # adversarial-review MINOR-1: this primitive never parks anything
        # (there is no foreign draft to protect on a bare-box-only entry
        # gate) -- `_undo_and_release_slot` must be called with
        # `parked=False`, so it must NEVER fire a `C-s`. A mutant hard-
        # coding `parked=True` at that call site is caught here, not by
        # `tmux.box`/`tmux.stash` alone (a stray `C-s` against an empty
        # box+empty slot is a state no-op in this fake, so only the
        # KEYSTROKE itself is provable evidence of the wrong call).
        self.assertNotIn("C-s", tmux.keys(),
                         "this primitive must never touch the stash slot: %r"
                         % tmux.sent)
        self.assertIsNone(tmux.stash, tmux.sent)

    def test_never_a_rapid_double_escape_during_the_recovery(self):
        text = "/goal " + PAYLOAD
        tmux = _SendVerifiedSwallowFake(swallow_enters=2)
        wd._send_goal_verified("%1", text, tmux, sleep_fn=lambda s: None)
        keys = tmux.keys()
        for a, b in zip(keys, keys[1:]):
            self.assertFalse(a == "Escape" and b == "Escape",
                             "a rapid double-Escape permanently deletes a "
                             "draft: %r" % tmux.sent)


class TestSendGoalVerifiedPreSendRefusalsAreLogged(unittest.TestCase):
    """#322 live incident (dev1, 2026-08-08): both PRE-SEND `return False`
    branches of `_send_goal_verified` (never a `send-keys -l` call) used to
    return with an EMPTY `logs` list, so the caller's own #101 carve-out
    (`if not ok and dlogs and dlogs[-1] in _GOAL_REARM_TRANSIENT_REASONS`)
    could never recognise a zero-keystroke refusal from THIS primitive —
    only from `deliver_with_stash`'s own `stash-abort: *` reasons. Two such
    silent refusals (a race from idle, at the caller's stale top-of-sweep
    capture, to busy — the SAME pane, a single blocking foreground `pytest`
    call straddling both sweeps) permanently exhausted the give-up cap."""

    def test_raced_fresh_capture_going_busy_logs_a_transient_reason(self):
        # the OUTER `captured` param is bare (matches what the caller already
        # verified via `pane_at_idle_prompt`); the SECOND, LIVE re-capture
        # `_send_goal_verified` takes right before typing (#176-F3) is busy —
        # a genuine race, not a delivery failure.
        tmux = FakeTmux(PANE_DARK, cap_seq=[PANE_BUSY])
        logs = []
        ok = wd._send_goal_verified("%1", "/goal x", tmux, captured=PANE_DARK,
                                    sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertFalse(tmux.typed(), "zero keystrokes must be sent: %r"
                         % tmux.sent)
        self.assertTrue(logs, "must log a reason, never stay silent")
        self.assertIn(logs[-1], wd._GOAL_REARM_TRANSIENT_REASONS, logs)
        self.assertEqual(logs[-1], "goal-verify-abort: raced-busy")

    def test_entry_check_refusal_also_logs_a_transient_reason(self):
        # defensive symmetry for any future caller that passes a `captured`
        # it has NOT already pre-verified bare itself.
        tmux = FakeTmux(PANE_DRAFT)
        logs = []
        ok = wd._send_goal_verified("%1", "/goal x", tmux, captured=PANE_DRAFT,
                                    sleep_fn=lambda s: None, logs=logs)
        self.assertFalse(ok)
        self.assertFalse(tmux.typed(), tmux.sent)
        self.assertTrue(logs, logs)
        self.assertIn(logs[-1], wd._GOAL_REARM_TRANSIENT_REASONS, logs)
        self.assertEqual(logs[-1], "goal-verify-abort: not-bare")


PANE_COLLAPSED_PASTE = CONV + FOOTER_DARK.replace(
    "❯ \n", "❯ paste again to expand\n")


class TestTypeLiteralChunking(unittest.TestCase):
    """#322 PRIMARY fix — a controlled live experiment (montalu2@subdev +
    an isolated scratch session, CC 2.1.226) DISPROVED the earlier "busy
    pane" hypothesis directly: a 23-char payload armed fine on a BUSY pane,
    while the SAME real 3960-char branch-merge template failed on an IDLE
    one, showing `paste again to expand` in the input row — CC treats one
    long `send-keys -l` burst as an unexpanded terminal paste, never parsed
    as a slash command. Typing the identical long payload in ~120-char
    chunks with a short pause between each armed correctly every time."""

    def _run(self, text):
        sent = []
        sleeps = []
        wd._type_literal("%1", lambda argv, timeout=8: sent.append(argv),
                         text, sleep_fn=lambda s: sleeps.append(s))
        return sent, sleeps

    def test_short_payload_is_a_single_unchanged_burst(self):
        text = "x" * (wd.GOAL_TYPE_CHUNK_THRESHOLD - 1)
        sent, sleeps = self._run(text)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual(sent[0][-1], text)
        self.assertEqual(sleeps, [])

    def test_long_payload_is_chunked_and_reassembles_byte_identically(self):
        text = "y" * (wd.GOAL_TYPE_CHUNK_THRESHOLD * 4 + 17)
        sent, sleeps = self._run(text)
        self.assertGreater(len(sent), 1, sent)
        self.assertEqual("".join(a[-1] for a in sent), text)
        for a in sent:
            self.assertLessEqual(len(a[-1]), wd.GOAL_TYPE_CHUNK_SIZE, sent)
        self.assertEqual(len(sleeps), len(sent) - 1,
                         "a pause between EACH chunk, none trailing: %r"
                         % sleeps)

    def test_threshold_boundary_chunks(self):
        # AT the threshold, not just above it -- the real 3960-char
        # incident payload is well past both, but the boundary itself is
        # where an off-by-one in the comparison would hide.
        text = "z" * wd.GOAL_TYPE_CHUNK_THRESHOLD
        sent, _sleeps = self._run(text)
        self.assertGreater(len(sent), 1, sent)

    def test_every_emitted_argv_has_the_end_of_options_guard(self):
        # #322 REOPENED (adversarial-review CRITICAL-1) -- a chunk (or a
        # short single-burst payload) whose FIRST character is `-` is read
        # by real tmux getopt as an unknown FLAG, not literal text --
        # verified live: `send-keys -l '-DASH'` fails with "unknown flag",
        # `send-keys -l -- '-DASH'` lands it correctly. Structural check:
        # `--` must sit immediately before the payload in EVERY emitted
        # argv, for both the short and the chunked path.
        for text in ("x" * (wd.GOAL_TYPE_CHUNK_THRESHOLD - 1),
                    "y" * (wd.GOAL_TYPE_CHUNK_THRESHOLD * 3)):
            sent, _sleeps = self._run(text)
            self.assertTrue(sent, text[:20])
            for a in sent:
                self.assertEqual(a[-2], "--",
                                 "the payload must be end-of-options guarded: %r" % a)

    def test_a_dash_leading_chunk_is_never_silently_dropped(self):
        # #322 REOPENED (adversarial-review CRITICAL-1) -- reproduced live
        # against a real tmux 3.7b pane: 2 of 7 shipped `/goal` templates
        # have a chunk boundary landing mid-word on a literal `-` (e.g.
        # "...`-self` (holds..."), which real tmux rejects as an unknown
        # flag when sent without `--`. `_default_run` swallows a non-zero
        # exit as `""` with NO exception and NO log -- the chunk vanishes
        # silently, every LATER chunk still lands, and the tail-based
        # `_typed_landed` check is satisfied by the (now internally
        # corrupted) remainder -- a GOAL WITH A 120-CHAR HOLE gets armed.
        # A fake `run` that models real tmux's getopt behavior (reject a
        # send-keys -l call whose literal text starts with `-` UNLESS `--`
        # immediately precedes it) is the "missing tooth" a plain
        # argv-recording lambda cannot provide.
        def tmux_like_run(argv, timeout=8):
            if argv[:2] == ["tmux", "send-keys"] and "-l" in argv:
                text = argv[-1]
                guarded = len(argv) >= 2 and argv[-2] == "--"
                if text.startswith("-") and not guarded:
                    return None          # real tmux: "unknown flag", rc != 0
            landed.append(argv[-1])
            return ""

        landed = []
        # a payload whose SECOND chunk boundary lands exactly on a dash --
        # chunk 0 is GOAL_TYPE_CHUNK_SIZE 'a's, chunk 1 starts with '-'.
        text = ("a" * wd.GOAL_TYPE_CHUNK_SIZE + "-leading-chunk-text"
                + "b" * wd.GOAL_TYPE_CHUNK_SIZE)
        wd._type_literal("%1", tmux_like_run, text, sleep_fn=lambda s: None)
        self.assertEqual("".join(landed), text,
                         "every chunk must land -- a dash-leading one must "
                         "never be silently dropped: %r" % landed)


class TestCollapsedPasteNeverSubmits(unittest.TestCase):
    """#322 — CC's "paste again to expand" is never parsed as a slash
    command; pressing Enter on it submits SOME content as an ordinary chat
    message instead of arming the goal. Both delivery primitives must
    refuse to submit it -- real keystrokes went out, so neither reason
    belongs in the zero-keystroke transient-refusal set."""

    def test_send_goal_verified_never_presses_enter_on_it(self):
        # cap_seq[0] is the fresh re-capture right before typing (bare, so
        # typing proceeds); every capture AFTER that (the type-verify poll,
        # and this fix's own post-poll check) falls back to the STATIC
        # collapsed-paste content.
        tmux = FakeTmux(PANE_COLLAPSED_PASTE, cap_seq=[PANE_DARK])
        logs = []
        ok = wd._send_goal_verified("%1", "/goal " + PAYLOAD, tmux,
                                    captured=PANE_DARK, sleep_fn=lambda s: None,
                                    logs=logs)
        self.assertFalse(ok)
        self.assertNotIn("Enter", tmux.keys(),
                         "never submit an unexpanded paste: %r" % tmux.sent)
        self.assertTrue(logs, logs)
        self.assertEqual(logs[-1], "goal-verify-abort: collapsed-paste")
        # real keystrokes DID go out (chunked typing happened) -- this must
        # NOT be exempted from the attempt-cap the way a pre-send refusal is
        self.assertNotIn(logs[-1], wd._GOAL_REARM_TRANSIENT_REASONS, logs)

    def test_deliver_with_stash_never_presses_enter_on_it(self):
        tmux = FakeTmux(PANE_COLLAPSED_PASTE, cap_seq=[PANE_DARK])
        logs = []
        ok = wd.deliver_with_stash("%1", "/goal " + PAYLOAD, tmux,
                                   captured=PANE_DARK, logs=logs,
                                   sleep_fn=lambda s: None)
        self.assertFalse(ok)
        self.assertNotIn("Enter", tmux.keys(),
                         "never submit an unexpanded paste: %r" % tmux.sent)
        self.assertTrue(logs, logs)
        self.assertEqual(logs[-1], "stash-abort: collapsed-paste")


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
# #161 — the BOUNDED-WAIT half of a genuine ❓ NEEDS YOU block. Job 20's own
# `_goal_stall_nudge` (above) refuses to nudge a pane whose last marker is
# `❓` — that refusal is UNCHANGED (`test_question_marker_is_never_nudged_past`
# still passes green). This is a SEPARATE, independent decision: past ~30
# minutes with NO reply, the SAME episode gets ONE nudge with a
# STRUCTURALLY DIFFERENT payload (never "continue", never the question
# itself) instructing the session to park the ticket and work something
# else — never re-printing the question into the chat (the camera-box wall,
# 2026-07-05, must stay dead).
# --------------------------------------------------------------------------- #

QUESTION_TURN = ("**Otázka — projekt demo:** kontext úvodu.\n"
                 "• Áno (odporúčam) — pokračuje\n"
                 "❓ NEEDS YOU: pokračovať teraz?")


class TestGoalQuestionTimeoutPark(GoalRearmBase):

    def _questions_path(self, sid, ts, question="pokračovať teraz?"):
        p = Path(self.tmp.name) / "discord-questions.json"
        p.write_text(json.dumps(
            {"1": {"session": sid, "cwd": CWD, "channel": "1",
                   "ts": ts, "question": question}}))
        return str(p)

    def _empty_questions_path(self):
        # An ISOLATED, genuinely empty map — never `None` alone, which in
        # PRODUCTION means "fall through to the real ~/.claude/discord-
        # questions.json" (the correct default there) but in a test would
        # silently read whatever this developer's OWN real box happens to
        # hold. Every test in this class must stay hermetic regardless of
        # which sentinel it passes.
        p = Path(self.tmp.name) / "discord-questions-empty.json"
        if not p.exists():
            p.write_text("{}")
        return str(p)

    def _sweep(self, now=None, waited=None, captured=PANE_LIT, state=None,
              questions_path="unset", sid=SID, extra_entries=None,
              entry_ts=None, assistant_text=None):
        # `entry_ts` is the transcript's own assistant-entry timestamp — the
        # moment the ❓ NEEDS YOU block was WRITTEN. #161-review MAJOR M1
        # (scenario B) requires the delivered ping to be CLOSE to this
        # timestamp (a real Stop-hook delivery lands within seconds of it),
        # so it must track `delivered`, never a value merely close to `now`
        # (the two drift apart by exactly `waited`, which is often 30+
        # minutes — the fixture bug the review caught live).
        now = now or time.time()
        waited = wd.GOAL_QUESTION_TIMEOUT_S + 60 if waited is None else waited
        delivered = now - waited
        if entry_ts is None:
            entry_ts = delivered - 2
        text = QUESTION_TURN if assistant_text is None else assistant_text
        entries = [marker_entry("set", PAYLOAD),
                  {"type": "assistant", "timestamp": _iso(entry_ts),
                   "message": {"content": text}}]
        p = self._write(entries + (extra_entries or []))
        self._wrote = True
        mt = now - 60
        os.utime(p, (mt, mt))
        if questions_path == "unset":
            questions_path = self._questions_path(sid, delivered)
        elif questions_path is None:
            questions_path = self._empty_questions_path()
        tmux = FakeTmux(captured)
        logs = wd.goal_rearm(now, tmux, state if state is not None else {},
                             send_fn=self._send, projects_dir=self.tmp.name,
                             sleep_fn=lambda s: None,
                             questions_path=questions_path)
        return tmux, logs

    def test_a_stopped_question_past_30min_gets_parked(self):
        tmux, logs = self._sweep()
        typed = tmux.typed()
        self.assertTrue(typed, tmux.sent)
        self.assertIn(wd.GOAL_QUESTION_PARK_TEXT, typed)
        self.assertTrue(any("goal-question-timeout" in ln for ln in logs), logs)

    def test_a_mere_mention_of_needs_you_is_never_a_genuine_block(self):
        # #161-review MAJOR M1 scenario A: a completion report merely
        # NAMING "NEEDS YOU" in prose (discussing this very mechanism, or
        # any other text) — not a trailing status line — must never be
        # misread as a genuine, stopped block.
        report = ("## ✅ Work Complete\n\n"
                  "Implemented the ❓ NEEDS YOU timeout backstop.\n\n"
                  "✅ DONE: #161 hotové")
        tmux, _ = self._sweep(assistant_text=report)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_a_stale_sibling_question_never_starts_the_clock(self):
        # #161-review MAJOR M1 scenario B: an OLDER, unrelated question for
        # the SAME session (a stale sibling that outran pruning) must
        # never stand in for the CURRENT block's own — never delivered —
        # ping. The stale entry is hours away from the transcript's own
        # block timestamp, well outside GOAL_QUESTION_MATCH_WINDOW_S.
        now = time.time()
        entry_ts = now - 60                  # the CURRENT block, written just now
        stale_ts = now - 6 * 3600            # a sibling delivered 6h ago
        qpath = self._questions_path(SID, stale_ts, question="iny stary dopyt")
        tmux, _ = self._sweep(now=now, entry_ts=entry_ts,
                              questions_path=qpath)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_a_delivery_shortly_before_the_block_was_written_still_matches(self):
        # the proximity window tolerates a SMALL amount of clock skew —
        # delivery landing a few seconds BEFORE the entry's own timestamp
        # must still match (GOAL_QUESTION_MATCH_SLOP_S).
        now = time.time()
        entry_ts = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        delivered = entry_ts - (wd.GOAL_QUESTION_MATCH_SLOP_S - 5)
        qpath = self._questions_path(SID, delivered)
        tmux, _ = self._sweep(now=now, entry_ts=entry_ts,
                              questions_path=qpath)
        self.assertIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_persist_fires_before_the_keystroke_send(self):
        # #161-review MAJOR M2: a process killed between the state
        # mutation and the keystroke send must never lose the "already
        # parked" memory and re-send on the next sweep — persist() must
        # fire FIRST, mirroring jobs 8/11's own established shape
        # (test_bounce_backstop.py::TestStatePersistedBeforeTyping).
        order = []
        now = time.time()
        delivered = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        entries = [marker_entry("set", PAYLOAD),
                  {"type": "assistant", "timestamp": _iso(delivered - 2),
                   "message": {"content": QUESTION_TURN}}]
        p = self._write(entries)
        self._wrote = True
        os.utime(p, (now - 60, now - 60))
        qpath = self._questions_path(SID, delivered)
        real_tmux = FakeTmux(PANE_LIT)
        real_call = real_tmux.__call__

        def spy(argv, timeout=8):
            if "-l" in argv:
                order.append("send")
            return real_call(argv, timeout)
        wd.goal_rearm(now, spy, {}, send_fn=self._send,
                     projects_dir=self.tmp.name, sleep_fn=lambda s: None,
                     questions_path=qpath,
                     persist=lambda: order.append("persist"))
        self.assertIn("persist", order)
        self.assertIn("send", order)
        self.assertLess(order.index("persist"), order.index("send"))

    def test_the_nudge_never_repeats_the_question_text(self):
        # the whole point: this must NEVER be the camera-box wall shape —
        # the typed payload must not contain the actual question's own
        # words, only the park-and-switch instruction.
        tmux, _ = self._sweep()
        typed = tmux.typed()
        self.assertTrue(typed)
        self.assertNotIn("pokračovať teraz", typed[0])
        self.assertNotIn("❓", typed[0])

    def test_before_the_timeout_nothing_happens(self):
        tmux, _ = self._sweep(waited=wd.GOAL_QUESTION_TIMEOUT_S - 60)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_a_never_delivered_question_never_starts_the_clock(self):
        # no discord-questions.json entry for this session at all — the
        # transcript itself may be arbitrarily stale, but a ping that never
        # sent must never be treated as having started a clock.
        tmux, _ = self._sweep(questions_path=None)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_a_stale_transcript_marker_belonging_to_a_DIFFERENT_session_is_ignored(self):
        tmux, _ = self._sweep(sid="some-other-session")
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_the_same_question_is_never_parked_twice(self):
        state = {}
        now = time.time()
        ts = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        qpath = self._questions_path(SID, ts)
        t1, _ = self._sweep(state=state, questions_path=qpath, now=now,
                            entry_ts=ts - 2)
        self.assertIn(wd.GOAL_QUESTION_PARK_TEXT, t1.typed())
        t2, _ = self._sweep(state=state, questions_path=qpath,
                            now=now + 600, entry_ts=ts - 2)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, t2.typed(),
                         "the SAME outstanding question must not be re-nudged")

    def test_a_new_question_after_the_first_gets_its_own_fresh_episode(self):
        state = {}
        now1 = time.time()
        first_ts = now1 - wd.GOAL_QUESTION_TIMEOUT_S - 3600
        t1, _ = self._sweep(state=state, now=now1, entry_ts=first_ts - 2,
                            questions_path=self._questions_path(SID, first_ts))
        self.assertIn(wd.GOAL_QUESTION_PARK_TEXT, t1.typed())
        now2 = time.time()
        second_ts = now2 - wd.GOAL_QUESTION_TIMEOUT_S - 60
        t2, _ = self._sweep(state=state, now=now2, entry_ts=second_ts - 2,
                            questions_path=self._questions_path(SID, second_ts))
        self.assertIn(wd.GOAL_QUESTION_PARK_TEXT, t2.typed(),
                      "a NEW outstanding question gets its own fresh nudge")

    def test_busy_pane_is_never_nudged(self):
        busy = CONV + FOOTER_LIT.replace("❯ \n", "✳ Baking… (esc to interrupt)\n")
        tmux, _ = self._sweep(captured=busy)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_draft_is_never_typed_over(self):
        draft = CONV + FOOTER_LIT.replace("❯ \n", "❯ rozpísaný draft\n")
        tmux, _ = self._sweep(captured=draft)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())

    def test_the_skip_draft_log_line_is_deduped_per_episode(self):
        # #161-review MINOR m3: an unresolvable draft blocking the park
        # (or any busy/scrolled state hitting the same "skip" branch) must
        # not print a fresh log line every single sweep for the SAME
        # outstanding question — one line per episode is enough.
        draft = CONV + FOOTER_LIT.replace("❯ \n", "❯ rozpísaný draft\n")
        state = {}
        now = time.time()
        delivered = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        _t1, logs1 = self._sweep(captured=draft, state=state, now=now,
                                 entry_ts=delivered - 2,
                                 questions_path=self._questions_path(
                                     SID, delivered))
        self.assertTrue(any("goal-question-timeout" in ln for ln in logs1),
                        logs1)
        _t2, logs2 = self._sweep(captured=draft, state=state,
                                 now=now + 60, entry_ts=delivered - 2,
                                 questions_path=self._questions_path(
                                     SID, delivered))
        self.assertFalse(
            any("goal-question-timeout" in ln for ln in logs2), logs2)

    def test_dry_run_never_types(self):
        now = time.time()
        delivered = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        entries = [marker_entry("set", PAYLOAD),
                  {"type": "assistant", "timestamp": _iso(delivered - 2),
                   "message": {"content": QUESTION_TURN}}]
        p = self._write(entries)
        self._wrote = True
        os.utime(p, (now - 60, now - 60))
        qpath = self._questions_path(SID, delivered)
        tmux = FakeTmux(PANE_LIT)
        logs = wd.goal_rearm(now, tmux, {}, send_fn=self._send,
                             projects_dir=self.tmp.name,
                             sleep_fn=lambda s: None, dry_run=True,
                             questions_path=qpath)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln for ln in logs), logs)

    def test_a_working_asked_turn_is_never_this_branch(self):
        # ask-and-continue: the turn ends ⏳ WORKING, never ❓ — job 4's
        # domain, never a genuine stopped block.
        now = time.time()
        delivered = now - wd.GOAL_QUESTION_TIMEOUT_S - 60
        entries = [marker_entry("set", PAYLOAD),
                  {"type": "assistant", "timestamp": _iso(delivered - 2),
                   "message": {"content": "❓ ASKED: ktorý dizajn?\n\n"
                              "⏳ WORKING: medzitým iný tiket"}}]
        p = self._write(entries)
        self._wrote = True
        os.utime(p, (now - 60, now - 60))
        qpath = self._questions_path(SID, delivered)
        tmux = FakeTmux(PANE_LIT)
        wd.goal_rearm(now, tmux, {}, send_fn=self._send,
                      projects_dir=self.tmp.name,
                      sleep_fn=lambda s: None, questions_path=qpath)
        self.assertNotIn(wd.GOAL_QUESTION_PARK_TEXT, tmux.typed())


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
        the job's own sweep capture, `_goal_template_drift`'s own FRESH
        pre-delivery re-check, #271's own SECOND re-capture immediately
        before typing (still bare), then post-type and post-Enter."""
        typed = CONV + FOOTER_LIT.replace("❯ \n", "❯ " + text[-40:] + "\n")
        return [PANE_LIT, PANE_LIT, PANE_LIT, typed, PANE_LIT]

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


class TestGoalDriftRespectsHandEdits(GoalDriftBase):
    """#186 — the live incident: the user hand-widened a variant's stop
    condition. The armed text now matches NO current template, but `tvar`
    is still set from the earlier observation that it once did — the old
    code read that mismatch as drift (indistinguishable from a real template
    push) and silently retyped the shipped text over the user's edit."""

    def test_a_hand_edited_goal_is_never_reverted(self):
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        hand_edited = (TPL_FULL[:-1] + ', and also count a ticket only the '
                       'supervisor box can action as done.')
        tmux, logs = self._sweep(hand_edited[len("/goal "):], templates_path=tp,
                                 state=state)
        self.assertFalse(tmux.typed(),
                         "a deliberate hand-edit must never be retyped over")

    def test_the_hand_edit_reason_is_logged_once_not_every_sweep(self):
        state, seen = {}, []
        tp = self._templates(TPL_FULL)
        seen += self._sweep(TPL_FULL[len("/goal "):], templates_path=tp,
                            state=state)[1]
        hand_edited = TPL_FULL[:-1] + ', and never stop on a bare backlog count.'
        seen += self._sweep(hand_edited[len("/goal "):], templates_path=tp,
                            state=state)[1]
        for _ in range(3):
            seen += self._sweep(templates_path=tp, state=state)[1]
        self.assertEqual(len([ln for ln in seen if "hand-edit" in ln]), 1,
                         "a hand-edited goal must not spam the journal every "
                         "sweep")

    def test_a_hand_edit_survives_a_LATER_template_push_too(self):
        """The mismatch persisting across a genuine template push must not
        suddenly look like drift again — once the armed text has diverged
        from what was last confirmed, only an EXACT match to a current
        template re-establishes tracking."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        hand_edited = TPL_FULL[:-1] + ', and also close it once merely reviewed.'
        self._sweep(hand_edited[len("/goal "):], templates_path=tp, state=state)
        tp = self._templates(TPL_FULL_V2, TPL_BRANCH)          # a real push
        tmux, logs = self._sweep(templates_path=tp, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(),
                         "a template push must not resurrect the reverted text "
                         "for an already-hand-edited goal")

    def test_returning_to_a_current_template_resumes_drift_tracking(self):
        """Self-healing: if the user later types the shipped text exactly
        (e.g. re-running `/autopilot`), the job resumes protecting it against
        a real future template push."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        hand_edited = TPL_FULL[:-1] + ', and also close a ticket the moment it ' \
                                       'is merely reviewed.'
        self._sweep(hand_edited[len("/goal "):], templates_path=tp, state=state)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        tp2 = self._templates(TPL_FULL_V2, TPL_BRANCH)
        tmux, logs = self._sweep(templates_path=tp2, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(tmux.typed(), [TPL_FULL_V2], logs)

    def test_reverting_to_the_stale_confirmed_text_never_resumes_drift(self):
        """#186-review finding — clearing `tvar` itself (not just the
        delivery bookkeeping) on a hand-edit matters: without it, the STALE
        `armed_hash` recorded BEFORE the edit can coincidentally match again
        if the user later types the OLD confirmed text back, which must
        never resurrect drift-healing on evidence that predates the edit."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        hand_edited = TPL_FULL[:-1] + ', and also close it once merely reviewed.'
        self._sweep(hand_edited[len("/goal "):], templates_path=tp, state=state)
        tp2 = self._templates(TPL_FULL_V2, TPL_BRANCH)          # a real push
        # the OLD confirmed text (never the new current one) comes back
        tmux, logs = self._sweep(TPL_FULL[len("/goal "):], templates_path=tp2,
                                 state=state, cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertFalse(tmux.typed(),
                         "a hand-edited session must not resume drift "
                         "protection from evidence recorded before the edit")

    def test_a_legacy_record_missing_armed_hash_still_migrates_when_up_to_date(self):
        """#186-review finding 🟡 (kills the "only refresh armed_hash when
        something ELSE changed" mutant) — a session whose `tvar` predates
        this fix (no `armed_hash` yet) but is STILL currently running the
        shipped template must have `armed_hash` re-established the very
        next time it is observed matching, even though `tvar`/`dhash`/`dq`
        all already look unchanged — or it can never migrate at all."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        state["goal_rearm"][SID].pop("armed_hash", None)   # simulate legacy state
        self._sweep(templates_path=tp, state=state)   # still up to date, re-observed
        self.assertIn("armed_hash", state["goal_rearm"][SID],
                     "armed_hash must be re-established on re-observation")
        tp2 = self._templates(TPL_FULL_V2, TPL_BRANCH)          # NOW a real push
        tmux, logs = self._sweep(templates_path=tp2, state=state,
                                 cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(tmux.typed(), [TPL_FULL_V2], logs)

    def test_a_legacy_record_that_no_longer_matches_gets_pinged_once(self):
        """#186-review finding 1 — a session whose `tvar` predates this fix
        (armed_hash never recorded) that ALSO no longer matches any current
        template must not silently vanish with zero signal: the pre-fix
        behaviour would eventually reach the GAVE UP ping for such a loop,
        and this branch now short-circuits before ever reaching it. A
        genuinely OBSERVED hand-edit (armed_hash present but different)
        stays quiet — this is the LEGACY, unmeasurable-either-way case."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        state["goal_rearm"][SID].pop("armed_hash", None)
        tp2 = self._templates(TPL_FULL_V2, TPL_BRANCH)          # push landed
        tmux, logs = self._sweep(templates_path=tp2, state=state)
        self.assertFalse(tmux.typed(), logs)
        self.assertEqual(len(self.pings), 1, logs)
        self.assertIn("#186", self.pings[0][0])
        for _ in range(3):
            self._sweep(templates_path=tp2, state=state)
        self.assertEqual(len(self.pings), 1,
                         "the legacy ping must fire at most once per episode")

    def test_dry_run_never_downgrades_real_state(self):
        """#186-review finding 🟡 — a manual `--dry-run` diagnostic sweep
        must never permanently disable healing for a real session: the
        `tvar` downgrade plus the one-shot log flag are irreversible state
        MUTATIONS, exactly the class `#238-review 🟡F4` already fixed once
        for `dark_pinged` in this same file."""
        state = {}
        tp = self._templates(TPL_FULL, TPL_BRANCH)
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state)
        hand_edited = TPL_FULL[:-1] + ', and also stop counting a bare handoff ' \
                                       'as done.'
        tmux, logs = self._sweep(hand_edited[len("/goal "):], templates_path=tp,
                                 state=state, dry_run=True)
        self.assertFalse(tmux.typed())
        self.assertTrue(any("READY" in ln and "goal-drift" in ln for ln in logs),
                        logs)
        rec = state["goal_rearm"][SID]
        self.assertEqual(rec.get("tvar"), 0,
                         "a dry-run sweep must not downgrade tvar")
        self.assertFalse(rec.get("untracked_logged"),
                         "a dry-run sweep must not consume the one-shot log flag")
        # a REAL sweep afterward must still detect and correctly handle it
        tmux2, logs2 = self._sweep(templates_path=tp, state=state)
        self.assertFalse(tmux2.typed())
        self.assertTrue(state["goal_rearm"][SID].get("untracked_logged"), logs2)

    def test_own_delivery_landing_survives_a_SECOND_push_before_confirmation(self):
        """#186-review finding S3 — CC echoing our OWN just-typed re-arm
        delivery must never be misread as a hand-edit merely because a
        SECOND template push landed before the confirming sweep ran."""
        state = {}
        tp = self._templates(TPL_FULL)
        now = time.time()
        self._sweep(TPL_FULL[len("/goal "):], templates_path=tp, state=state,
                    now=now)
        tp2 = self._templates(TPL_FULL_V2)
        first, _ = self._sweep(templates_path=tp2, state=state, now=now + 10,
                               cap_seq=self._lit_seq(TPL_FULL_V2))
        self.assertEqual(first.typed(), [TPL_FULL_V2])
        v3 = TPL_FULL_V2 + " Also verify the deployed version twice."
        tp3 = self._templates(v3)
        # CC echoes our delivery (payload becomes TPL_FULL_V2) WHILE v3 has
        # already landed, before we ever confirmed TPL_FULL_V2
        tmux, logs = self._sweep(TPL_FULL_V2[len("/goal "):], templates_path=tp3,
                                 state=state, now=now + 40,
                                 cap_seq=self._lit_seq(v3))
        self.assertFalse(any("hand-edited" in ln for ln in logs), logs)
        self.assertEqual(tmux.typed(), [v3], logs)


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

    def test_progress_dir_reaches_the_job(self):
        calls = self._cycle(goal_rearm_enabled=True, progress_dir="/tmp/x")
        self.assertEqual(calls[0].get("progress_dir"), "/tmp/x")

    def test_progress_dir_defaults_to_unwired(self):
        calls = self._cycle(goal_rearm_enabled=True)
        self.assertIsNone(calls[0].get("progress_dir"))


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

    # ----------------------------------------------------------------- #
    # #312 item 3 -- `claim_present=False` (no FRESH claim in the
    # session's last turn) used to unconditionally trust "loop finished
    # legitimately" even when the session's OWN tracked goal PAYLOAD
    # (persisted independently of what the last turn said) is
    # unambiguously backlog-shaped -- every shipped `/goal` template
    # embeds the literal marker text verbatim in its own STOP CONDITIONS.
    # Extended to fall through into the SAME cached live check instead of
    # blindly skipping whenever the payload itself proves this loop DOES
    # care about the backlog.
    # ----------------------------------------------------------------- #

    BACKLOG_PAYLOAD = ("STOP CONDITIONS: the loop is done once every open "
                       "issue is closed -- prove it with the line "
                       "🏁 BACKLOG EMPTY: 0 open, main green directly above "
                       "the terminal ✅ DONE: marker.")

    def test_no_fresh_claim_but_backlog_shaped_payload_still_verifies(self):
        entries = [marker_entry("set", self.BACKLOG_PAYLOAD)]
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            cap_seq=[self.ACHIEVED_PANE] + self._typed_seq()[1:],
            backlog_fetch=lambda cwd: 3)
        self.assertTrue(tmux.typed(), logs)
        self.assertTrue(any("FALSE-ACHIEVED" in ln for ln in logs), logs)

    def test_no_fresh_claim_and_backlog_shaped_payload_verified_empty_skips(self):
        entries = [marker_entry("set", self.BACKLOG_PAYLOAD)]
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            backlog_fetch=lambda cwd: 0)
        self.assertFalse(tmux.typed(), logs)
        self.assertTrue(any("verified empty" in ln for ln in logs), logs)

    def test_no_fresh_claim_and_non_backlog_payload_stays_unaffected(self):
        # a plain, non-batch /goal whose payload never mentions the
        # backlog marker at all -- unchanged legacy behavior, never spends
        # gh.
        entries = [marker_entry("set", "Stop once the bug is fixed.")]
        calls = []
        tmux, logs = self._go(
            self.ACHIEVED_PANE, entries=entries,
            backlog_fetch=lambda cwd: calls.append(cwd) or 5)
        self.assertFalse(tmux.typed(), logs)
        self.assertEqual(calls, [],
                         "a non-backlog-shaped payload must never spend gh")


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
    silent dead end. It still pings ONCE — but #173 narrowed WHEN this
    branch is even reached: a marker this old with NO explicit clear is
    now revived past the cap instead (see
    `TestGoalRearmStaleMarkerIsNeverRevived`). This class exercises the
    residual case that still lands here — the transcript genuinely
    cannot be RE-READ to confirm "no clear" at the exact moment of the
    dark decision (a transient permission/IO failure AFTER job 20
    already recorded `mark == 'set'` from a real earlier sweep) — the
    ticket's own "transcript file missing/unreadable = fail-safe =
    current conservative behavior, no re-arm" edge case."""

    def _establish_and_break(self, state, now):
        """Sweep 1 genuinely reads the transcript and records mark=='set'
        (ARMED, so `last_armed` is set); then the transcript becomes
        unreadable for every later sweep, so `_goal_dark_died_by_outage`'s
        own fresh re-derivation can never confirm "no clear" again — the
        fail-safe path, never a real deliberate clear."""
        self._go(PANE_LIT, entries=[marker_entry("set", PAYLOAD)],
                 state=state, now=now)
        p = (Path(self.tmp.name) / wd.encode_project_dir(CWD)
             / (SID + ".jsonl"))
        os.chmod(p, 0)
        self.addCleanup(os.chmod, p, 0o600)
        return p

    def test_stale_goal_pings_once(self):
        state = {}
        now = time.time()
        self._establish_and_break(state, now)
        later = now + wd.GOAL_REARM_MAX_DARK_S + 60
        self._go(PANE_DARK, state=state, now=later)
        self.assertEqual(len(self.pings), 1, self.pings)
        self.assertTrue(self.pings[0][0].startswith("⚠️"), self.pings)

    def test_never_retypes_the_payload(self):
        state = {}
        now = time.time()
        self._establish_and_break(state, now)
        later = now + wd.GOAL_REARM_MAX_DARK_S + 60
        tmux, _logs = self._go(PANE_DARK, state=state, now=later)
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
        p = self._establish_and_break(state, now)
        later = now + wd.GOAL_REARM_MAX_DARK_S + 60
        self._go(PANE_DARK, state=state, now=later)
        self.assertEqual(len(self.pings), 1, self.pings)
        # the goal comes back ARMED for real (a human re-armed it by hand)
        os.chmod(p, 0o600)
        self._go(PANE_LIT, state=state, now=later + 60)
        # ... then dies again, dark for another MAX_DARK_S window, still
        # unreadable so the outage check still cannot confirm "no clear"
        os.chmod(p, 0)
        even_later = later + 60 + wd.GOAL_REARM_MAX_DARK_S + 1
        self._go(PANE_DARK, state=state, now=even_later)
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
        self._establish_and_break(state, now)
        later = now + wd.GOAL_REARM_MAX_DARK_S + 60
        self._go(PANE_DARK, state=state, now=later, dry_run=True)
        self.assertEqual(self.pings, [])
        self._go(PANE_DARK, state=state, now=later + 60)
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
