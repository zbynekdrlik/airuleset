"""#1083 — statusbar renders the `M N` footer badge (merged into develop/staging,
awaiting the release cut) right after `I N`, gk colour family (245), hidden at 0.
The width-budget drop order (email → caveman → ctx) is UNCHANGED.

RED-first: `_merged_sfx` does not exist and `tickets_segment` renders no `M`.
"""
import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar


def _seed(home, cwd, entry):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    base = {"open": 5, "name": "demo", "root": str(cwd),
            "ts": int(time.time())}
    base.update(entry)
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(base))


class MergedBadgeRender(unittest.TestCase):
    def setUp(self):
        self._home_cm = TemporaryDirectory()
        self._cwd_cm = TemporaryDirectory()
        self.home = self._home_cm.name
        self.cwd = self._cwd_cm.name
        self.addCleanup(self._home_cm.cleanup)
        self.addCleanup(self._cwd_cm.cleanup)

    def _seg(self, **entry):
        _seed(self.home, self.cwd, entry)
        return statusbar.tickets_segment(self.cwd, home=self.home, spawn=False)

    def test_merged_badge_shown_after_I(self):
        seg = self._seg(open=5, merged_unreleased=3)
        self.assertIn("· M 3", seg)
        # M is rendered in the gk colour family (245).
        self.assertIn("\033[38;5;245m· M 3", seg)
        # order: `I 5` appears before `· M 3`.
        self.assertLess(seg.index("I 5"), seg.index("· M 3"))

    def test_merged_badge_hidden_at_zero(self):
        seg = self._seg(open=5, merged_unreleased=0)
        self.assertNotIn("· M ", seg)

    def test_merged_badge_hidden_when_absent(self):
        seg = self._seg(open=5)   # legacy cache, no merged_unreleased key
        self.assertNotIn("· M ", seg)
        self.assertIn("I 5", seg)

    def test_merged_badge_before_bounce_and_U(self):
        seg = self._seg(open=5, merged_unreleased=2, bounce=1, user_waiting=1)
        self.assertIn("· M 2", seg)
        # M sits between I and the bounce/U badges.
        self.assertLess(seg.index("· M 2"), seg.index("· bounce 1"))


class CarryForwardKeys(unittest.TestCase):
    def test_merged_unreleased_is_carried_forward(self):
        self.assertIn("merged_unreleased", statusbar._CARRY_FORWARD_KEYS)


class WidthDropOrderUnchanged(unittest.TestCase):
    """The M badge lives INSIDE the atomic tickets segment, so fit_statusline's
    drop order (identity → caveman → ctx) is byte-identical to today."""

    def test_drop_order_still_email_then_caveman_then_ctx(self):
        tickets = "\033[38;5;75mI 5\033[0m \033[38;5;245m· M 3\033[0m"
        ctx = "ctx 20%"
        segs = [tickets, ctx]
        # Wide enough: nothing dropped.
        full = statusbar.fit_statusline(segs, "me@x", "cave", ctx, ctx, 200)
        self.assertIn("me@x", full)
        self.assertIn("cave", full)
        self.assertIn("· M 3", full)
        # Narrow: identity dropped FIRST, the tickets segment (incl. M) survives.
        narrow = statusbar.fit_statusline(segs, "me@x", "cave", ctx, ctx, 25)
        self.assertNotIn("me@x", narrow)
        self.assertIn("· M 3", narrow)


if __name__ == "__main__":
    unittest.main()
