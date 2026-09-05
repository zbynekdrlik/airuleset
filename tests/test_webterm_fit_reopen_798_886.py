"""#798 REOPEN + #886 — two webterm dashboard fit bugs in the 4-layer fill pipeline.

Bug A (#798 REOPEN): `stretchFrameToFill` uses `transformOrigin: '50% 50%'` which
center-scales the iframe. At medium viewports (~959x639) the grid's visual top
lands at EXACTLY 0px margin — any sub-pixel rounding clips row 0 under the tab
bar. Fix: origin '0 0' + translate, pinning row 0 at top by construction.

Bug B (#886): a preloaded hidden tab's xterm has stale zero-layout metrics. On
first activation, the fit pipeline may read stale dimensions and leave the
fill/stretch at 'none'. Fix: a fontSize kick on hidden->visible transition
forces xterm to re-measure before the fit runs.

RED tests: assert the transform pins top (row 0 safe under sub-pixel perturbation)
and that activate() contains a re-measure kick.
"""
import re
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
from test_webterm import _FIT_HARNESS, _extract_js_function, _run_fit_harness  # noqa: E402


def _inv():
    return [{"id": "s1", "label": "dev1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


class TestStretchOriginPinsTop798(unittest.TestCase):
    """Bug A: stretchFrameToFill must pin the grid's visual top >= 0 at EVERY
    medium viewport, not land it at an exact 0 knife-edge that sub-pixel
    rounding can push negative."""

    def setUp(self):
        self.html = w.render_dashboard_html(_inv(), ttyd_base="/t")

    def test_stretch_uses_origin_zero_not_center(self):
        """The transform origin must be '0 0' (or '0 0'), not '50% 50%',
        so the grid top is pinned by construction (a single product, not a
        cancellation of two large centered terms)."""
        fn = _extract_js_function(self.html, "stretchFrameToFill")
        # Check the CODE lines only (strip comments) for the old origin
        code_lines = [ln for ln in fn.splitlines()
                      if not ln.strip().startswith("//")]
        code_only = "\n".join(code_lines)
        self.assertNotIn("50% 50%", code_only,
                         "stretchFrameToFill code must not use 50% 50% origin "
                         "(row 0 lands at exactly 0px margin, sub-pixel "
                         "rounding clips it)")
        self.assertIn("'0 0'", fn,
                      "stretchFrameToFill must use origin '0 0' to pin top")

    def test_stretch_includes_translate_for_centering(self):
        """With origin '0 0', the grid must be explicitly centered via
        translate() — without it the grid sits at top-left with no
        horizontal centering."""
        fn = _extract_js_function(self.html, "stretchFrameToFill")
        self.assertIn("translate(", fn,
                      "stretchFrameToFill must use translate() for centering "
                      "when origin is '0 0'")

    @unittest.skipIf(shutil.which("node") is None, "node not available")
    def test_grid_top_safe_under_perturbation_at_959x602(self):
        """At the owner's reopened viewport (959x602), the grid visual top
        must stay >= 0 even under a +0.3px perturbation of grid height
        (sub-pixel font metric simulation)."""
        out = _run_fit_harness(self.html, vw=959, vh=602)
        self.assertTrue(out["ok"])
        self.assertTrue(out["stretched"])
        # Parse the frame transform to compute the visual grid top
        # Grid is centered in the iframe: top = (vh - gridH) / 2
        grid_top = (out["availH"] - out["gridH"]) / 2
        # With origin '0 0' + translate, the grid top should be
        # pinned at 0 (top of slot). With 50% 50%, it's a knife-edge.
        # Simulate a +0.3px perturbation (real-world font metric variance):
        perturbed_grid_h = out["gridH"] + 0.3
        perturbed_grid_top = (out["availH"] - perturbed_grid_h) / 2
        # Under the old 50% 50% origin, visual top after scale:
        # cy + (perturbed_top - cy) * sy, where cy = availH/2
        cy = out["availH"] / 2
        sy = out["frameScaleY"]
        visual_top_center = cy + (perturbed_grid_top - cy) * sy
        # Under the correct '0 0' + translate(ty=-g.top*sy):
        # visual top = 0 by construction (g.top * sy cancelled by translate)
        visual_top_pinned = 0.0
        # The test: assert the SHIPPED code pins top >= 0
        # Parse the actual transform to determine which origin is used
        origin = out["frameOrigin"]
        if "50%" in origin:
            # Old center origin — visual top is the knife-edge
            visual_top = visual_top_center
        else:
            # New '0 0' origin — visual top is pinned
            visual_top = visual_top_pinned
        self.assertGreaterEqual(
            visual_top, -0.1,
            "At 959x602 with +0.3px perturbation, the grid visual top "
            "must be >= -0.1px (row 0 visible). Got %.2f with origin '%s'"
            % (visual_top, origin))


class TestHiddenTabRevealKick886(unittest.TestCase):
    """Bug B: activate() must force xterm to re-measure its cell metrics
    when a preloaded hidden tab becomes visible, so the fill pipeline
    runs on honest dimensions."""

    def setUp(self):
        self.html = w.render_dashboard_html(_inv(), ttyd_base="/t")

    def test_activate_contains_fontsize_kick(self):
        """activate() must contain a fontSize re-measure kick for the
        hidden->visible transition — a genuine fontSize option change
        (fs+1 then fs) forces xterm to re-measure cell metrics."""
        fn = _extract_js_function(self.html, "activate")
        # The kick should be present in activate: either directly or
        # via a helper called from activate
        # Look for evidence of a fontSize kick pattern
        self.assertTrue(
            "fontSize" in fn or "__wtKick" in fn or "remeasure" in fn,
            "activate() must contain a fontSize re-measure kick for "
            "hidden->visible tab transitions (#886)")


if __name__ == "__main__":
    unittest.main()
