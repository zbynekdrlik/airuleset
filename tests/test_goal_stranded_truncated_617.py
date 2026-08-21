"""#617 -- `_clear_stranded_truncated_goal`: clear a STRANDED, TRUNCATED own
`/goal` draft (a partial arm type left in the box) that the provenance-gated
janitor refuses once a later successful send cleared its watch mark -- the live
montalu1 674-char stuck /goal. Ownership is proven by a byte-exact CONTIGUOUS
prefix of THIS pane's own template (>= GOAL_STRANDED_MIN_MATCH chars,
reconstructed from every wrapped row -- NOT head+tail), gated on a clean idle
boundary + the FAIL-CLOSED recent-human check + a bounded give-up, and runs
regardless of arm state. A FOREIGN draft, a MIDDLE-EDITED paste, a SHORT
opening, or a COMPLETE own draft is never touched by this path.
"""

import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.goal as goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, _write_marker_transcript,
    _write_goal_marker, _isolate_goal_state,
)

PID = "%7"
SID = "sess-trunc-617"
CWD = "/home/montalu1/devel/odoo"
LOC = "montalu1:0.0"

# A realistic single-spaced /goal template (the byte-exact match target), long
# enough that a truncated prefix comfortably clears GOAL_STRANDED_MIN_MATCH
# (200) while staying < the whole. The distinctive `/goal STOP CONDITIONS`
# opening + the interior `python3 ~` are the real montalu1 shape; single spaces
# so the fake's word-wrap round-trips exactly.
TEXT = ("/goal STOP CONDITIONS the loop is DONE the moment EITHER holds, both "
        "checkable from the transcript: (A) BLOCKED ON MY ANSWER, the latest "
        "assistant message ends with a line starting NEEDS YOU and there is no "
        "user message after it; (B) SLICE EMPTY, proven in this turn, never "
        "claimed, and (B) holds ONLY when my final message carries the pasted "
        "OUTPUT of all four proof commands: python3 ~/devel/airuleset/"
        "airuleset.py slice-quals --count printing exactly 0 under it, then "
        "the flag line directly above the DONE marker which means CONTINUE.")

# a byte-exact truncated prefix ending mid-template (well over MIN, under whole)
TRUNC_N = TEXT.index("python3 ~") + len("python3 ~")


class _WrapFake:
    """Renders `box` as a REAL wrapped input box (head/tail DIFFER) between
    separator borders with the `◎ /goal active` glyph on the ctx line; Escape
    is a no-op, a BSpace run trims the box, and `_janitor_clear_box` converges
    it to bare. `busy=True` renders NO input box (a running-turn spinner)."""

    def __init__(self, box, wrap_width=60, busy=False):
        self.box = box
        self.w = wrap_width
        self.busy = busy
        self.sent = []

    def _render(self):
        if self.busy:
            return "● thinking… (esc to interrupt)\n  ctx ██  ◎ /goal active\n"
        if not self.box:
            return "● wip\n\n%s\n❯ \n%s\n  ctx ██  ◎ /goal active\n" % (
                "─" * self.w, "─" * self.w)
        rows, cur, prefix = [], "", "❯\xa0"
        for word in self.box.split(" "):
            cand = (cur + " " + word) if cur else word
            if len(prefix) + len(cand) > self.w:
                rows.append(prefix + cur)
                cur, prefix = word, "  "
            else:
                cur = cand
        rows.append(prefix + cur)
        return "\n".join(["● wip", "", "─" * self.w] + rows
                         + ["─" * self.w, "  ctx ██  ◎ /goal active"]) + "\n"

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "send-keys" in j:
            self.sent.append(argv)
            keys = argv[4:]
            if keys and all(k == "BSpace" for k in keys):
                n = len(keys)
                self.box = self.box[:-n] if n < len(self.box) else ""
            return ""
        if "capture-pane" in j:
            return self._render()
        return ""


def _tails(sent):
    return [a[-1] for a in sent if a]


class ClearStrandedTruncatedGoal617(unittest.TestCase):
    def setUp(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.rescue_dir = Path(d.name)
        # a REAL transcript file so `_recovery_recent_human`'s fail-closed
        # os.path.getsize probe succeeds and the patched recency verdict is
        # what decides (a missing tpath fails closed to VETO).
        self.tpath = str(Path(d.name) / "t.jsonl")
        Path(self.tpath).write_text("{}\n", encoding="utf-8")
        p = m.patch.object(wd, "draft_rescue_dir", return_value=self.rescue_dir)
        p.start()
        self.addCleanup(p.stop)
        g = m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                           return_value=(False, ""))
        self.human = g.start()
        self.addCleanup(g.stop)

    def _call(self, box, dry_run=False, rearm_text=TEXT, state=None, busy=False):
        fake = _WrapFake(box, busy=busy)
        cap = fake._render()
        logs, cleared = goal._clear_stranded_truncated_goal(
            SID, CWD, cap, self.tpath, PID, fake,
            {} if state is None else state, 1000.0, lambda *_a: None, dry_run,
            lambda _cwd: (rearm_text, "branch-merge"), LOC)
        return fake, logs, cleared

    def test_truncated_own_goal_prefix_is_cleared(self):
        fake, logs, cleared = self._call(TEXT[:TRUNC_N])
        self.assertTrue(cleared)
        self.assertTrue(any("CLEARED" in ln for ln in logs), logs)
        self.assertIn("BSpace", _tails(fake.sent))
        self.assertEqual(fake.box, "")                 # box is bare again

    def test_clear_persists_a_draft_rescue_snapshot(self):
        # #617-review: the rescue snapshot is the ONLY recovery mitigation for a
        # wrong clear -- it must actually be written (mutation-7 was toothless).
        self._call(TEXT[:TRUNC_N])
        files = list(self.rescue_dir.glob("*.txt"))
        self.assertTrue(files, "a draft-rescue snapshot must be persisted "
                        "before the destructive clear")

    def test_short_prefix_under_min_is_never_cleared(self):
        # #617-review 🔴: no minimum length let a bare hand-typed opening match.
        short = "/goal STOP CONDITIONS the loop is"      # < 200 chars
        self.assertLess(len(short), goal.GOAL_STRANDED_MIN_MATCH)
        fake, logs, cleared = self._call(short)
        self.assertFalse(cleared)
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_middle_edited_paste_is_never_cleared(self):
        # #617-review 🔴: head+tail both match a template start + a template
        # tail, but the user's OWN edit sits in the middle -- the contiguity
        # (full-content byte-exact prefix) proof must reject it.
        box = TEXT[:120] + " MOJA VLASTNA PODMIENKA tu navyse a este dalsie " + \
            TEXT[200:280]
        self.assertGreaterEqual(len(" ".join(box.split())),
                                goal.GOAL_STRANDED_MIN_MATCH)
        fake, logs, cleared = self._call(box)
        self.assertFalse(cleared)
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_foreign_draft_is_never_touched(self):
        fake, logs, cleared = self._call(
            "píšem si vlastnú dlhú poznámku o tom čo znamená /goal STOP "
            "CONDITIONS a ako to funguje a čo všetko treba a preco to je tak "
            "a nie inak a este vela textu aby to bolo nad limitom dvesto znakov")
        self.assertFalse(cleared)
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_complete_own_draft_is_left_for_the_submit_path(self):
        fake, logs, cleared = self._call(TEXT)      # the WHOLE template
        self.assertFalse(cleared)
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_busy_pane_is_never_escaped(self):
        # #617-review 🟡: no input boundary (spinner) -> never keystroke.
        fake, logs, cleared = self._call(TEXT[:TRUNC_N], busy=True)
        self.assertFalse(cleared)
        self.assertEqual(fake.sent, [])

    def test_recent_human_vetoes_the_clear(self):
        self.human.return_value = (True, "presence marker 20s old")
        fake, logs, cleared = self._call(TEXT[:TRUNC_N])
        self.assertFalse(cleared)
        self.assertTrue(any("recent-human VETO" in ln for ln in logs), logs)
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_unreadable_transcript_fails_closed_to_veto(self):
        # #617-review 🟡: the fail-CLOSED wrapper vetoes on an unreadable
        # transcript (the raw gate would fail OPEN and clear).
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                            return_value=(False, "")):
            with m.patch("os.path.getsize", side_effect=OSError("boom")):
                fake, logs, cleared = self._call(TEXT[:TRUNC_N])
        self.assertFalse(cleared)
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_dry_run_never_keystrokes(self):
        fake, logs, cleared = self._call(TEXT[:TRUNC_N], dry_run=True)
        self.assertFalse(cleared)
        self.assertTrue(any("would CLEAR" in ln for ln in logs), logs)
        self.assertEqual(fake.sent, [])

    def test_unresolvable_template_is_a_noop(self):
        fake, logs, cleared = self._call(TEXT[:TRUNC_N], rearm_text=None)
        self.assertFalse(cleared)
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_non_goal_head_is_a_noop(self):
        fake, logs, cleared = self._call("just some ordinary conversation input")
        self.assertFalse(cleared)
        self.assertEqual(logs, [])

    def test_non_converging_clear_gives_up_after_bound(self):
        # #617-review 🟡: a box that never converges is escalated ONCE, never
        # retried forever. A fake whose BSpace does NOT shrink the box models
        # a non-converging clear.
        class _StuckFake(_WrapFake):
            def __call__(self, argv, timeout=8):
                j = " ".join(argv)
                if "send-keys" in j:
                    self.sent.append(argv)
                    return ""                       # BSpace never trims
                if "capture-pane" in j:
                    return self._render()
                return ""
        state = {}
        gaveup = False
        for i in range(goal.GOAL_STRANDED_CLEAR_GIVEUP + 2):
            fake = _StuckFake(TEXT[:TRUNC_N])
            cap = fake._render()
            logs, cleared = goal._clear_stranded_truncated_goal(
                SID, CWD, cap, self.tpath, PID, fake, state, 1000.0,
                lambda *_a: None, False,
                lambda _cwd: (TEXT, "branch-merge"), LOC)
            self.assertFalse(cleared)
            if any("giving up" in ln for ln in logs):
                gaveup = True
                break
        self.assertTrue(gaveup, "must escalate/give up after the bound")
        # after give-up, a further sweep neither re-attempts nor logs
        fake = _StuckFake(TEXT[:TRUNC_N])
        logs, cleared = goal._clear_stranded_truncated_goal(
            SID, CWD, fake._render(), self.tpath, PID, fake, state, 1000.0,
            lambda *_a: None, False, lambda _cwd: (TEXT, "branch-merge"), LOC)
        self.assertFalse(cleared)
        self.assertEqual(logs, [])
        self.assertEqual(fake.sent, [])


class DefaultRearmOverCapLog617(unittest.TestCase):
    """#617-review 🟡: fix (a)'s PRODUCTION wiring -- `_default_rearm_fn`
    forwards an over-cap refusal into the goal-sync forensic trail."""

    def test_over_cap_template_surfaces_into_goal_sync(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        syncp = Path(d.name) / "goal-sync.log"
        long_line = "/goal " + ("x" * (goal.GOAL_ARM_CHAR_CAP + 50))
        skill = Path(d.name) / "SKILL.md"
        skill.write_text("**AUTHORITY: full**\n\n```\n%s\n```\n" % long_line,
                         encoding="utf-8")
        with m.patch.object(goal, "goal_sync_log_path", return_value=syncp), \
             m.patch.object(wd, "goal_templates_path", return_value=skill), \
             m.patch("airuleset.resolve_authority", return_value="full"):
            text, auth = goal._default_rearm_fn("/x")
        self.assertIsNone(text)
        self.assertEqual(auth, "full")
        body = syncp.read_text(encoding="utf-8")
        self.assertIn("REFUSED oversize", body)


class WiredIntoDarkWatch617(unittest.TestCase):
    """The clear actually RUNS in goal_dark_watch's per-pane sweep -- once a
    later successful send cleared the janitor watch mark, the provenance-gated
    `_janitor_recover` above refuses the stuck draft, so ONLY this content-proof
    path reclaims it (the montalu1 shape)."""

    def setUp(self):
        self.reqp, self.syncp = _isolate_goal_state(self)
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.proj = Path(d.name)
        rd = TemporaryDirectory()
        self.addCleanup(rd.cleanup)
        r = m.patch.object(wd, "draft_rescue_dir", return_value=Path(rd.name))
        r.start()
        self.addCleanup(r.stop)
        g = m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                           return_value=(False, ""))
        g.start()
        self.addCleanup(g.stop)

    def test_dark_watch_clears_a_stranded_truncated_goal_draft(self):
        cwd = "/home/montalu1/devel/odoo"
        sid = "sess-dark-trunc"
        _write_marker_transcript(self.proj, cwd, sid)
        _write_goal_marker(self.proj, cwd, sid, "Goal set: /goal x",
                           ts_epoch=500)
        tmux = DeliverGoalFakeTmux([("%9", "claude", cwd, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   initial_box=TEXT[:TRUNC_N], wrap_width=60)
        logs = goal.goal_dark_watch(
            1000, run=tmux, projects_dir=self.proj, sleep_fn=lambda *_a: None,
            rearm_fn=lambda _cwd: (TEXT, "branch-merge"))
        self.assertTrue(any("CLEARED stranded truncated" in ln for ln in logs),
                        logs)
        self.assertIn("BSpace", [a[-1] for a in tmux.sent if a])
        self.assertEqual(tmux.box, "")     # the poison is gone


if __name__ == "__main__":
    unittest.main()
