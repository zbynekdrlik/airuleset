"""#1340 -- `_janitor_clear_box` must clear a FULL stranded own /goal in a
narrow pane (the phone webterm, ~40 cols).

Each pass used to backspace only the box TAIL row (`_input_line_text`), and the
loop stopped after `JANITOR_CLEAR_MAX_ITER` (25) passes: ~25 x 36 chars, far
short of the ~3.6k-char /goal template, so the clear never converged and the
cleanup gave up. Claude Code also SCROLLS a long box (only its last rows are
drawn, the first visible row re-glyphed `❯`), which the fake models.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import goal_registry  # noqa: E402
import watchdog as wd  # noqa: E402

from test_goal_box_kind_wrap_1303 import _capture, _cc_rows  # noqa: E402

_GOAL = goal_registry.render_goal_line("full", None)


class _NarrowPane:
    """A CC pane `width` cols wide whose box shows at most `rows` rows; a
    BSpace run trims the buffer unless `dead` (keys that never land)."""

    def __init__(self, box, width=40, rows=3, dead=False):
        self.box, self.width, self.rows, self.dead = box, width, rows, dead
        self.bspaces = 0
        self.captures = 0

    def render(self):
        if not self.box:
            return _capture(["❯\xa0"])
        rows = _cc_rows(self.box, self.width)
        if len(rows) > self.rows:
            rows = rows[-self.rows:]
            rows[0] = "❯\xa0" + rows[0].lstrip("❯ \xa0")
        return _capture(rows)

    def __call__(self, argv, timeout=8):
        if "send-keys" in argv:
            keys = argv[4:]
            if keys and all(k == "BSpace" for k in keys):
                self.bspaces += len(keys)
                if not self.dead:
                    self.box = self.box[:-len(keys)] if len(keys) < len(self.box) else ""
            return ""
        if "capture-pane" in argv:
            self.captures += 1
            return self.render()
        return ""


class JanitorClearNarrowPane(unittest.TestCase):
    def setUp(self):
        p = m.patch.object(wd, "_keystroke_suppressed", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.logs = []

    def _clear(self, pane):
        return wd._janitor_clear_box("%9", pane, lambda *_a: None, self.logs.append)

    def test_fixture_goal_is_long(self):
        # the case is only meaningful while the template is ~3.6k chars.
        self.assertGreater(len(_GOAL), 2500)

    def test_full_goal_clears_in_40_col_scrolled_pane(self):
        pane = _NarrowPane(_GOAL, width=40, rows=3)
        self.assertTrue(self._clear(pane), self.logs)
        self.assertEqual(pane.box, "")

    def test_full_goal_clears_in_40_col_tall_pane(self):
        pane = _NarrowPane(_GOAL, width=40, rows=200)
        self.assertTrue(self._clear(pane), self.logs)
        self.assertEqual(pane.box, "")

    def test_never_backspaces_more_than_was_typed_plus_one_pass(self):
        # every batch is sized to text SEEN on screen: the total backspaced may
        # not exceed the buffer (no blind burst into whatever follows).
        pane = _NarrowPane(_GOAL, width=40, rows=3)
        self._clear(pane)
        self.assertLessEqual(pane.bspaces, len(_GOAL))

    def test_keys_that_never_land_stop_fast(self):
        # a box that does not change after a pass is not converging: abort,
        # never spin the whole budget into a pane that ignores the keys.
        pane = _NarrowPane(_GOAL, width=40, rows=3, dead=True)
        self.assertFalse(self._clear(pane))
        self.assertLessEqual(pane.captures, 3)
        self.assertTrue(any("no progress" in ln for ln in self.logs), self.logs)

    def test_short_box_still_one_pass(self):
        pane = _NarrowPane("/compact", width=40, rows=3)
        self.assertTrue(self._clear(pane))
        self.assertEqual(pane.bspaces, len("/compact"))


if __name__ == "__main__":
    unittest.main()
