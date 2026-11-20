"""#1381 -- a `/goal` arm typed into a COLLAPSED input box is verified and submitted.

Claude Code 2.1.327 caps the input box at about half the pane (20 rows at the
fleet pin 176x50) and draws a longer buffer COLLAPSED: the top border becomes
`─── ↑ N more ───`, only the last rows are shown and the `❯` head row is not drawn.
Every box reader found the box by its `❯` row, so the dasio/dcontroller arms
(20.11.2026) logged `SKIP verify-failed` + `box=empty` + `cleanup=declined`
over 3.4 kB of our own text and nothing ever pressed Enter.

The fixtures under `tests/fixtures/collapsed_box_1381/` are REAL CC 2.1.327
captures (private tmux server, `claude --bare`, fake API key, nothing
submitted); only the conversation ABOVE the box was replaced. `<name>.payload`
is the exact text that was typed. `CollapsingBoxFake` models the same render
for the end-to-end `deliver_goal` runs.
"""

import json
import os
import sys
import time
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import unicodedata  # noqa: E402

import goal_registry  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal, stash  # noqa: E402
from _goal_arm_helpers import (DeliverGoalFakeTmux, _isolate_goal_state,  # noqa: E402
                               _write_marker_transcript)

FIX = REPO / "tests" / "fixtures" / "collapsed_box_1381"
PID = "%9"
CWD = "/home/newlevel/devel/collapsed1381"
SID = "sess-collapsed-1381"
SPINNER = "✳ Pondering… (12s · ↓ 1.2k tokens · esc to interrupt)"
TEXT = goal_registry.render_goal_line("full")       # 22 rows at 176 cols


def fixture(name):
    return ((FIX / (name + ".txt")).read_text(encoding="utf-8"),
            (FIX / (name + ".payload")).read_text(encoding="utf-8"))


def _cap_run(*caps):
    """A `run` seam answering `capture-pane` with `caps` in order (last repeats)."""
    seq = list(caps)

    def run(argv, timeout=8):
        if "capture-pane" in argv:
            return seq.pop(0) if len(seq) > 1 else seq[0]
        return ""
    return run


def _with_spinner(cap):
    """The same capture with a running-turn spinner right above the top border."""
    lines = cap.splitlines()
    top = next(i for i, ln in enumerate(lines) if "more ─" in ln)
    return "\n".join(lines[:top - 1] + [SPINNER] + lines[top:]) + "\n"


def _drop_last_row(cap):
    """Our collapsed text with its last row not rendered yet (a lagging redraw)."""
    lines = cap.splitlines()
    bot = max(i for i, ln in enumerate(lines) if set(ln.strip()) == {"─"})
    return "\n".join(lines[:bot - 1] + lines[bot:]) + "\n"


class TypeVerifyReadsACollapsedBox(unittest.TestCase):
    """Item 1: the scroll-length goal type ends LANDED on a collapsed box whose
    visible rows are our own tail (then the ONE Enter), and never otherwise."""

    def test_own_collapsed_tail_is_landed(self):
        for name in ("own_full_176x50", "own_review_100x50", "own_quality_176x50"):
            cap, text = fixture(name)
            with self.subTest(name=name):
                self.assertEqual(stash._type_verify_class(
                    PID, _cap_run(cap), text, allow_scrolled=True), stash._TV_LANDED)

    def test_without_the_head_checkpoint_it_stays_hold(self):
        cap, text = fixture("own_full_176x50")     # #720: a head-swallow looks the same
        self.assertEqual(stash._type_verify_class(PID, _cap_run(cap), text),
                         stash._TV_HOLD)

    def test_a_foreign_collapsed_box_never_lands_and_is_never_undone(self):
        cap, _ = fixture("foreign_176x50")
        _own, text = fixture("own_full_176x50")
        sleeps = []
        cls = stash._settle_type_verify(PID, _cap_run(cap), text, sleeps.append,
                                        allow_scrolled=True)
        self.assertEqual(cls, stash._TV_HOLD)     # HOLD: no Enter AND no undo backspace

    def test_a_late_last_row_settles_to_landed(self):
        cap, text = fixture("own_full_176x50")
        sleeps = []
        cls = stash._settle_type_verify(PID, _cap_run(_drop_last_row(cap), cap), text,
                                        sleeps.append, allow_scrolled=True)
        self.assertEqual(cls, stash._TV_LANDED)
        self.assertEqual(len(sleeps), 1)

    def test_a_tail_that_never_matches_ends_hold_after_the_settle_window(self):
        cap, text = fixture("own_full_176x50")
        sleeps = []
        cls = stash._settle_type_verify(PID, _cap_run(_drop_last_row(cap)), text,
                                        sleeps.append, allow_scrolled=True)
        self.assertEqual(cls, stash._TV_HOLD)
        self.assertEqual(len(sleeps), stash.TYPE_VERIFY_SETTLE_POLLS - 1)


def _box_lines(cap):
    """(above incl. the top border, box rows, bottom border + footer) of a capture."""
    lines = cap.splitlines()
    top = next(i for i, ln in enumerate(lines) if "more ─" in ln)
    bot = max(i for i, ln in enumerate(lines) if set(ln.strip()) == {"─"})
    return lines[:top + 1], lines[top + 1:bot], lines[bot:]


class CollapsedReaderRefusesLookalikes(unittest.TestCase):
    """The reader's own guards: each one is the only thing between a lookalike
    render and an Enter on the stranded path."""

    def setUp(self):
        from watchdog import collapsed_box
        self.cb = collapsed_box
        self.cap, self.text = fixture("own_full_176x50")
        self.above, self.body, self.foot = _box_lines(self.cap)

    def _join(self, *parts):
        return "\n".join(ln for part in parts for ln in part) + "\n"

    def test_the_real_box_with_an_overflowing_agent_strip_still_reads(self):
        strip = ["  ● main", "  ◯ opus-5.5 local_agent · Listing issues via gh · 118k",
                 "  ↓ 1 more"]
        cap = self._join(self.above, self.body, self.foot, strip)
        self.assertTrue(self.cb.own_tail(cap, self.text))
        self.assertIs(wd.pane_goal_armed(cap), False)

    def test_a_quoted_collapsed_box_above_a_real_draft_is_not_the_box(self):
        real = ["  owner draft row one", "  owner draft row two",
                "─── ↓ 1 more " + "─" * 150]           # the real box, cursor moved up
        cap = self._join(self.above, self.body, self.foot[:1], real, self.foot[1:])
        self.assertIsNone(self.cb.rows(cap))
        self.assertFalse(self.cb.own_tail(cap, self.text))

    def test_the_walk_never_crosses_a_strict_separator(self):
        cap = self._join(self.above, self.body[:5], ["─" * 176], self.body[5:], self.foot)
        self.assertIsNone(self.cb.rows(cap))

    def test_a_zero_hidden_count_proves_nothing(self):
        cap = self.cap.replace("↑ 2 more", "↑ 0 more")
        self.assertFalse(self.cb.own_tail(cap, self.text))

    def test_too_few_visible_chars_prove_nothing(self):
        cap = self._join(self.above, self.body[-1:], self.foot)   # 66 chars visible
        self.assertFalse(self.cb.own_tail(cap, self.text))

    def test_a_blank_row_inside_the_box_is_content(self):
        cap = self._join(self.above, self.body[:10], [""], self.body[10:], self.foot)
        self.assertFalse(self.cb.own_tail(cap, self.text))

    def test_the_row_count_must_match_the_pane_width(self):
        self.assertTrue(self.cb.own_tail(self.cap, self.text, cols=176))
        self.assertFalse(self.cb.own_tail(self.cap, self.text, cols=150))
        self.assertFalse(self.cb.own_tail(self.cap, self.text, cols=0))   # unknown width

    def test_holds_sees_our_collapsed_tail(self):
        self.assertTrue(self.cb.holds(self.cap, self.text))
        fcap, _ = fixture("foreign_176x50")
        self.assertFalse(self.cb.holds(fcap, self.text))


class BoxReadersRecogniseOurCollapsedText(unittest.TestCase):
    """Item 2: never `box=empty`, never `not our own leftover` for our own text."""

    def test_box_kind_is_ours_for_own_text_and_other_for_a_foreign_draft(self):
        cap, text = fixture("own_full_176x50")
        self.assertEqual(goal._goal_box_kind(cap, text), "ours")
        self.assertEqual(goal._goal_box_kind(cap, TEXT), "ours")   # any shipped variant
        fcap, _ = fixture("foreign_176x50")
        self.assertEqual(goal._goal_box_kind(fcap, text), "other")

    def test_armed_reads_the_footer_under_a_collapsed_box(self):
        cap, _ = fixture("own_full_176x50")
        self.assertIs(wd.pane_goal_armed(cap), False)
        lines = cap.splitlines()
        top = next(i for i, ln in enumerate(lines) if "more ─" in ln)
        armed = "\n".join(lines[:top] + [" " * 120 + "◎ /goal active (3m)"]
                          + lines[top:]) + "\n"
        self.assertIs(wd.pane_goal_armed(armed), True)

    def test_arm_confirm_fail_keeps_our_collapsed_goal_for_the_submit(self):
        cap, text = fixture("own_full_176x50")
        _isolate_goal_state(self)
        lines = []
        with unittest.mock.patch.object(goal, "_log_goal_sync", lines.append):
            goal._log_arm_confirm_fail(SID, CWD, text, PID, _cap_run(cap),
                                       sleep_fn=lambda *_: None, state={},
                                       now=time.time())
        diag = " ".join(lines)
        self.assertIn("box=ours", diag)
        self.assertIn("boundary=input", diag)
        self.assertNotIn("not our own leftover", diag)
        self.assertIn("kept", diag)

    def test_arm_confirm_fail_declines_a_foreign_collapsed_box(self):
        cap, _ = fixture("foreign_176x50")
        _isolate_goal_state(self)
        lines, sent = [], []
        run = _cap_run(cap)

        def recording(argv, timeout=8):
            if "send-keys" in argv:
                sent.append(argv)
            return run(argv, timeout)
        with unittest.mock.patch.object(goal, "_log_goal_sync", lines.append):
            goal._log_arm_confirm_fail(SID, CWD, TEXT, PID, recording,
                                       sleep_fn=lambda *_: None, state={},
                                       now=time.time())
        self.assertIn("box=other", " ".join(lines))
        self.assertEqual(sent, [])


def _cells(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _cc_rows(text, cols):
    """CC's box rows for an ASCII-or-emoji text (test-local, so the fake never
    leans on the production reader it exercises)."""
    width, rows, cur = cols - 4, [], ""
    for word in text.split(" "):
        cand = (cur + " " + word) if cur else word
        if _cells(cand) <= width:
            cur = cand
        else:
            rows.append(cur)
            cur = word
    return rows + [cur]


class CollapsingBoxFake(DeliverGoalFakeTmux):
    """`DeliverGoalFakeTmux` drawing CC 2.1.327's box: a buffer longer than
    `MAX_ROWS` rows is COLLAPSED (`─── ↑ N more ───` top border, last rows only,
    no `❯` row). Models the `-H 3b` trailing-semicolon send (#1319) too."""

    COLS, MAX_ROWS = 176, 20
    HEAD = ["● Autopilot beží: /goal riadok je vypísaný.", "", "✻ Worked for 2m 41s", ""]
    FOOT = ["  wk 91%(3d)  opus5.5  I 24▲  ctx 735K  caveman",
            "  ⏵⏵ bypass permissions on · 1 shell · ↓ to manage"]

    def __init__(self, spinner=False, client_activity=None, lag=False,
                 session_activity=1, **kw):
        bare = "\n".join(self.HEAD + ["─" * self.COLS, "❯", "─" * self.COLS]
                         + self.FOOT) + "\n"
        super().__init__([(PID, "claude", CWD, "111")], bare, model_type=True,
                         wrap_width=self.COLS, **kw)
        self.spinner = spinner
        self.client_activity = client_activity     # epoch of an attached client's input
        self.lag = lag                             # the last row never redraws
        self.session_activity = session_activity   # last human attach / key (epoch)

    def _render_wrapped(self):
        rows = _cc_rows(self.box, self.COLS)
        head = self.HEAD[:-1] + ([SPINNER] if self.spinner else [""])
        if len(rows) > self.MAX_ROWS:
            top = ["─── ↑ %d more " % (len(rows) - self.MAX_ROWS) + "─" * 150]
            body = ["  " + r for r in rows[-self.MAX_ROWS:]]
            body = body[:-1] if self.lag else body
        else:
            top = ["─" * self.COLS]
            body = ["❯\xa0" + rows[0]] + ["  " + r for r in rows[1:]]
        return "\n".join(head + top + body + ["─" * self.COLS] + self.FOOT) + "\n"

    def __call__(self, argv, timeout=8):
        if "send-keys" in argv and "-H" in argv:
            self.sent.append(argv)
            self.box += bytes.fromhex("".join(argv[argv.index("-H") + 1:])).decode()
            return ""
        if "display-message" in argv and argv[-1] == "#{pane_width}":
            return "%d\n" % self.COLS
        if "display-message" in argv and argv[-1] == "#{session_activity}":
            return "%d\n" % self.session_activity
        if "list-clients" in argv:
            return "" if self.client_activity is None else "%d\n" % self.client_activity
        return super().__call__(argv, timeout)


def _project(testcase, human_prompt=None):
    d = TemporaryDirectory()
    testcase.addCleanup(d.cleanup)
    proj = Path(d.name)
    tpath = _write_marker_transcript(proj, CWD, SID)
    if human_prompt:        # the owner's /autopilot, typed minutes before the arm
        aged = time.time() - 360
        iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(aged))
        with open(tpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"content": human_prompt},
                                "timestamp": iso}) + "\n")
        os.utime(tpath, (aged, aged))
    return proj, tpath


def _deliver(testcase, fake, proj, origin="self-callback", state=None, now=None):
    now = time.time() if now is None else now
    return goal.deliver_goal(SID, CWD, TEXT, "full", run=fake, projects_dir=proj,
                             now=now, state={} if state is None else state,
                             request_ts=now - 60, sleep_fn=lambda *_: None, origin=origin)


def _typed_state(now, text=TEXT, ago=120):
    """The first-person record our own earlier (head-checkpointed) type left."""
    from watchdog import collapsed_box
    state = {}
    collapsed_box.note_typed(state, PID, text, now - ago)
    return state


class DeliverGoalArmsThroughACollapsedBox(unittest.TestCase):
    """End to end through the real `deliver_goal` (job 9's delivery)."""

    def setUp(self):
        _isolate_goal_state(self)

    def test_the_fake_collapses_the_full_payload(self):
        # the FAKE's own wrap (CC's real render is locked in test_goal_box_rows_1381)
        self.assertGreater(len(_cc_rows(TEXT, 176)), CollapsingBoxFake.MAX_ROWS)

    def test_a_fresh_arm_is_verified_and_submitted_once(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        fake = CollapsingBoxFake(transcript_path=str(tpath))
        word = _deliver(self, fake, proj)
        self.assertEqual(word, "sent", fake.keys())
        self.assertEqual(fake.keys().count("Enter"), 1)
        self.assertEqual("".join(fake.typed_texts()).replace(";", ""),
                         TEXT.replace(";", ""))

    def test_our_stranded_collapsed_goal_is_submitted_not_retyped(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        now = time.time()
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=TEXT,
                                 session_activity=now - 600)   # his last key: before our type
        word = _deliver(self, fake, proj, state=_typed_state(now), now=now)
        self.assertEqual(word, "sent", fake.keys())
        self.assertEqual(fake.keys(), ["Enter"])     # no C-s, no retype, no clear

    def _refused(self, box, state, session_activity=1):
        proj, tpath = _project(self, human_prompt="/autopilot")
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=box,
                                 session_activity=session_activity)
        word = _deliver(self, fake, proj, state=state, now=self.now)
        self.assertEqual(word, "skip:collapsed-unsubmitted", fake.keys())
        self.assertEqual(fake.sent, [])               # no Enter, and never a C-s stash
        self.assertNotIn(word, goal._GOAL_KEYSTROKE_SKIPS)   # zero keys: never counted (#611)

    def test_without_our_typed_record_a_collapsed_goal_gets_no_key(self):
        self.now = time.time()
        self._refused(TEXT, {})

    def test_a_record_for_another_text_proves_nothing(self):
        self.now = time.time()
        self._refused(TEXT, _typed_state(self.now, text="/goal reply ok, then stop"))

    def test_a_head_swallowed_collapsed_box_gets_no_key(self):
        self.now = time.time()                   # #720: a junk prompt, never submitted
        self._refused(TEXT[1:], {})

    def test_any_human_attach_or_key_since_we_typed_vetoes_the_hidden_head(self):
        # the owner may have edited the hidden head and detached again (no client
        # listed any more); tmux keeps `session_activity`
        self.now = time.time()
        self._refused("NIE, toto nespúšťaj, chcem iné zadanie: " + TEXT,
                      _typed_state(self.now), session_activity=self.now - 60)
        self._refused(TEXT, _typed_state(self.now), session_activity=self.now - 60)

    def test_the_veto_is_anchored_at_our_record(self):
        self.now = time.time()                   # input 6 min ago, after a 10-min-old record
        self._refused(TEXT, _typed_state(self.now, ago=600),
                      session_activity=self.now - 360)

    def test_a_doubled_goal_fails_the_hidden_row_count(self):
        self.now = time.time()
        self._refused(TEXT + " " + TEXT, _typed_state(self.now))

    def test_the_submit_recheck_refuses_a_doubled_box(self):
        fake = CollapsingBoxFake(initial_box=TEXT + " " + TEXT)
        self.assertFalse(wd.submit_own_goal_verified(PID, TEXT, run=fake,
                                                     sleep_fn=lambda *_: None))
        self.assertEqual(fake.sent, [])

    def test_a_keyed_but_unconfirmed_submit_is_counted(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        now = time.time()
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=TEXT,
                                 enters_swallowed=2)
        word = _deliver(self, fake, proj, state=_typed_state(now), now=now)
        self.assertEqual(fake.keys(), ["Enter", "Escape", "Enter"])
        self.assertIn(word, goal._GOAL_KEYSTROKE_SKIPS)

    def test_a_record_is_written_only_when_a_key_reached_the_box(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        for typed in (False, True):
            def fake_send(*a, out=None, **k):
                if typed:
                    out["typed"] = True
                return False
            state = {}
            with unittest.mock.patch.object(goal, "_send_goal_verified", fake_send):
                _deliver(self, CollapsingBoxFake(transcript_path=str(tpath)), proj,
                         state=state)
            with self.subTest(typed=typed):
                self.assertEqual(PID in state.get("goal_collapsed_typed", {}), typed)

    def test_an_unconfirmed_type_records_it_and_the_next_sweep_submits(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        state, now = {}, time.time()
        fake = CollapsingBoxFake(transcript_path=str(tpath), lag=True)
        self.assertEqual(_deliver(self, fake, proj, state=state, now=now),
                         "skip:verify-failed")
        self.assertNotIn("Enter", fake.keys())        # never an Enter on an unproven box
        self.assertIn(PID, state.get("goal_collapsed_typed", {}))
        fake.lag, fake.sent = False, []               # the redraw caught up
        self.assertEqual(_deliver(self, fake, proj, state=state, now=now + 300), "sent")
        self.assertEqual(fake.keys(), ["Enter"])
        self.assertNotIn(PID, state["goal_collapsed_typed"])

    def test_a_swallowed_enter_gets_one_corrective_escape_enter(self):
        proj, tpath = _project(self, human_prompt="/autopilot")
        fake = CollapsingBoxFake(transcript_path=str(tpath), enters_swallowed=1)
        self.assertEqual(_deliver(self, fake, proj), "sent", fake.keys())
        self.assertEqual(fake.keys().count("Escape"), 1)

    def test_a_twice_swallowed_enter_backs_our_own_text_out(self):
        # #306 kept for a collapsed box: it was verified bare before our type, so
        # every char is ours; the box ends bare, never two Escapes.
        proj, tpath = _project(self, human_prompt="/autopilot")
        fake = CollapsingBoxFake(transcript_path=str(tpath), enters_swallowed=2)
        self.assertEqual(_deliver(self, fake, proj), "skip:verify-failed")
        self.assertEqual(fake.box, "")
        self.assertEqual(fake.keys().count("Escape"), 1)

    def test_a_foreign_collapsed_draft_gets_no_keystroke(self):
        proj, tpath = _project(self)
        _cap, foreign = fixture("foreign_176x50")
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=foreign)
        word = _deliver(self, fake, proj)
        self.assertTrue(word.startswith("skip:"), word)
        self.assertEqual(fake.sent, [])

    def test_our_collapsed_goal_under_a_running_turn_waits(self):
        proj, tpath = _project(self)
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=TEXT,
                                 spinner=True)
        self.assertEqual(_deliver(self, fake, proj), "skip:busy")
        self.assertEqual(fake.sent, [])

    def test_with_a_record_his_earlier_autopilot_keys_do_not_veto(self):
        # an uncollapsed own goal we typed: the veto is anchored at our record,
        # so his /autopilot keystrokes before it never stash our own /goal
        proj, tpath = _project(self, human_prompt="/autopilot")
        now = time.time()
        short = "/goal work the backlog until every issue is closed"
        fake = CollapsingBoxFake(transcript_path=str(tpath), initial_box=short,
                                 client_activity=now - 180, session_activity=now - 180)
        word = goal.deliver_goal(SID, CWD, short, "full", run=fake, projects_dir=proj,
                                 now=now, state=_typed_state(now, text=short, ago=120),
                                 request_ts=now - 60, sleep_fn=lambda *_: None,
                                 origin="self-callback")
        self.assertEqual(word, "sent", fake.keys())
        self.assertNotIn("C-s", fake.keys())

    def test_the_owner_arm_submits_its_own_uncollapsed_goal_despite_the_prompt(self):
        # #566's submit-in-place vetoed on the 30-min recent-human window, which
        # the owner's own /autopilot prompt always fills: it never fired for him.
        proj, tpath = _project(self, human_prompt="/autopilot")
        short = "/goal work the backlog until every issue is closed"
        fake = DeliverGoalFakeTmux([(PID, "claude", CWD, "111")],
                                   "● Hotovo.\n❯ \n  ctx ███░  caveman:lite\n",
                                   model_type=True, initial_box=short,
                                   transcript_path=str(tpath))
        now = time.time()
        word = goal.deliver_goal(SID, CWD, short, "full", run=fake, projects_dir=proj,
                                 now=now, state={}, request_ts=now - 60,
                                 sleep_fn=lambda *_: None, origin="self-callback")
        self.assertEqual(word, "sent")
        self.assertNotIn("C-s", fake.keys())


if __name__ == "__main__":
    unittest.main()
