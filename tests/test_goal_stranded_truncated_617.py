"""#617 -- `_clear_stranded_truncated_goal`: clear a STRANDED, TRUNCATED own
`/goal` draft (a partial arm type left in the box) that the provenance-gated
janitor refuses once a later successful send cleared its watch mark -- the live
montalu1 674-char stuck /goal. Ownership is proven by CONTENT (byte-exact prefix
of THIS pane's own template), gated on the recent-human check, and runs
regardless of arm state. A FOREIGN draft or a COMPLETE own draft is never
touched by this path.
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

# A realistic single-spaced /goal template (the byte-exact match target). The
# distinctive `/goal STOP CONDITIONS` opening + the interior `python3 ~` are the
# real montalu1 shape; single spaces so the fake's word-wrap round-trips exactly.
TEXT = ("/goal STOP CONDITIONS the loop is DONE the moment EITHER holds, both "
        "checkable from the transcript, and (B) holds ONLY when my final "
        "message carries the pasted OUTPUT of all four proof commands: "
        "python3 ~/devel/airuleset/airuleset.py slice-quals --count printing "
        "exactly 0, then the flag line directly above the DONE marker.")


class _WrapFake:
    """Renders `box` as a REAL wrapped input box (head/tail DIFFER) with the
    `◎ /goal active` glyph on the ctx line; Escape is a no-op, a BSpace run
    trims the box, and `_janitor_clear_box` converges it to bare."""

    def __init__(self, box, wrap_width=60):
        self.box = box
        self.w = wrap_width
        self.sent = []

    def _render(self):
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
        # isolate the draft-rescue snapshot dir off the real ~/.claude.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = m.patch.object(wd, "draft_rescue_dir", return_value=Path(d.name))
        p.start()
        self.addCleanup(p.stop)
        # default: no recent human (the gate proceeds).
        g = m.patch.object(wd, "_goal_autoarm_recent_human_activity",
                           return_value=(False, ""))
        self.human = g.start()
        self.addCleanup(g.stop)

    def _call(self, box, dry_run=False, rearm_text=TEXT):
        fake = _WrapFake(box)
        cap = fake._render()
        logs = goal._clear_stranded_truncated_goal(
            SID, CWD, cap, "/x/t.jsonl", PID, fake, {}, 1000.0,
            lambda *_a: None, dry_run, lambda _cwd: (rearm_text, "branch-merge"),
            LOC)
        return fake, logs

    def test_truncated_own_goal_prefix_is_cleared(self):
        # the box holds text[:N] -- a byte-exact prefix ending mid-template.
        n = TEXT.index("python3 ~") + len("python3 ~")
        fake, logs = self._call(TEXT[:n])
        self.assertTrue(any("CLEARED" in ln for ln in logs), logs)
        self.assertIn("BSpace", _tails(fake.sent))
        self.assertEqual(fake.box, "")                 # box is bare again

    def test_foreign_draft_is_never_touched(self):
        # a genuine user draft that merely mentions /goal -- NOT our template.
        fake, logs = self._call("píšem si vlastnú poznámku o /goal STOP "
                                "CONDITIONS a čo to znamená")
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_complete_own_draft_is_left_for_the_submit_path(self):
        # the WHOLE template in the box is `_submit_stranded_own_goal`'s job
        # (it gets SUBMITTED), never cleared here.
        fake, logs = self._call(TEXT)
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_recent_human_vetoes_the_clear(self):
        self.human.return_value = (True, "presence marker 20s old")
        n = TEXT.index("python3 ~") + len("python3 ~")
        fake, logs = self._call(TEXT[:n])
        self.assertTrue(any("recent-human VETO" in ln for ln in logs), logs)
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_dry_run_never_keystrokes(self):
        n = TEXT.index("python3 ~") + len("python3 ~")
        fake, logs = self._call(TEXT[:n], dry_run=True)
        self.assertTrue(any("would CLEAR" in ln for ln in logs), logs)
        self.assertEqual(fake.sent, [])

    def test_unresolvable_template_is_a_noop(self):
        n = TEXT.index("python3 ~") + len("python3 ~")
        fake, logs = self._call(TEXT[:n], rearm_text=None)
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))

    def test_non_goal_head_is_a_noop(self):
        fake, logs = self._call("just some ordinary conversation input")
        self.assertEqual(logs, [])
        self.assertNotIn("BSpace", _tails(fake.sent))


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
        r = m.patch.object(wd, "draft_rescue_dir",
                           return_value=Path(TemporaryDirectory().name))
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
        n = TEXT.index("python3 ~") + len("python3 ~")
        tmux = DeliverGoalFakeTmux([("%9", "claude", cwd, "111")],
                                   GOAL_IDLE_CAP, model_type=True,
                                   initial_box=TEXT[:n], wrap_width=60)
        logs = goal.goal_dark_watch(
            1000, run=tmux, projects_dir=self.proj, sleep_fn=lambda *_a: None,
            rearm_fn=lambda _cwd: (TEXT, "branch-merge"))
        self.assertTrue(any("CLEARED stranded truncated" in ln for ln in logs),
                        logs)
        self.assertIn("BSpace", [a[-1] for a in tmux.sent if a])
        self.assertEqual(tmux.box, "")     # the poison is gone


if __name__ == "__main__":
    unittest.main()
