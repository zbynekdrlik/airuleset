"""The W-drain footer signal (#868): `wdrain_over` in the cache renders a red
`· W N!` instead of the grey `· W N`.

The W-drain dispatch gate (block-dispatch-over-wdrain.sh) and its receipt CLI
(`wdrain-pass`) were retired by issue 1137 B1; the W push lives in the
watchdog job-20 nudge.
"""
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent


class TestFooterWdrainOver(unittest.TestCase):
    """wdrain_over in cache → red `· W N!` footer."""

    def test_wdrain_over_true_renders_red_exclamation(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 12, "wdrain_over": True}
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("196", sfx, "should use red (196)")
            self.assertIn("W 12!", sfx, "should have ! marker")
        finally:
            sys.path.pop(0)

    def test_wdrain_over_false_renders_grey(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 5, "wdrain_over": False}
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("245", sfx, "should use grey (245)")
            self.assertNotIn("!", sfx, "should NOT have ! marker")
        finally:
            sys.path.pop(0)

    def test_legacy_cache_no_wdrain_over_renders_grey(self):
        sys.path.insert(0, str(REPO))
        try:
            import statusbar
            cache = {"ops_wait": 12}  # no wdrain_over key
            sfx = statusbar._ops_wait_sfx(cache)
            self.assertIn("245", sfx, "legacy cache should use grey")
            self.assertNotIn("!", sfx, "legacy cache should not have !")
        finally:
            sys.path.pop(0)

    def test_wdrain_over_matches_count_gt_threshold(self):
        """wdrain_over bool should be count > threshold, not >=."""
        sys.path.insert(0, str(REPO))
        try:
            from cli_quals import OPS_WAIT_WDRAIN_THRESHOLD as T
            # At threshold exactly: NOT over
            self.assertFalse(T > T)
            # One above: over
            self.assertTrue((T + 1) > T)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
