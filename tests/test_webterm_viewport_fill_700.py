"""#700 — the 176x51 grid must fill the viewport EXACTLY (owner: "potrebujem
hlavne pracovnu plochu").

Owner screenshot (PWA, 2879x1798 phys / DPR 1.5): side margins ~78 CSS px each
and a bottom gap ~1-2 row heights under the tmux status bar. Root cause locked
here: fitFixedGrid (integer fontSize) + fillFixedGrid (#678: INTEGER px/cell
letterSpacing/lineHeight) both quantize, so a residual letterbox of up to
cols/rows x 1px per axis (~176 px horizontal) remains BY DESIGN and the
centered flex splits it into margins + the perceived "empty row" (which is NOT
a grid row: the status bar occupies grid row 51 — see the #700 validation
comment's row-math; geometry is correct and stays).

The #700 fix is a THIRD fill layer, `stretchFrameToFill`: the PARENT document
scales the tab's IFRAME by the sub-cell residual (origin center, clamped to
[1, WT_FRAME_FILL_MAX_STRETCH]), and `#frames{overflow:hidden}` clips the
spilled letterbox. Mouse-safe where the #655 same-document transform was not
(#678): the child xterm document's coordinate space is insulated from a
PARENT-document transform (pointer events are inverse-mapped by the browser),
so the child screen transform must STAY identity — asserted here on every run.
"""
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_tmux_provisioning as prov  # noqa: E402
import cli_webterm as w  # noqa: E402
from test_webterm import _FIT_HARNESS, _extract_js_function, _run_fit_harness  # noqa: E402


def _inv():
    return [{"id": "s1", "label": "sess 1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


class TestGeometryCanonDecision700(unittest.TestCase):
    """#700 geometry DECISION lock (not a bug fix — this passes before and
    after): canonical geometry stays window 176x50 + client grid 176x51.
    The three sources are single-source-derived, NEVER one literal: forcing
    window 176x51 would crop the owner's own 176x51 WT client (#613 class);
    forcing the browser grid to 176x50 would crop the CC footer (#672)."""

    def test_three_geometry_sources_share_one_source_of_truth(self):
        import inspect
        self.assertEqual(prov.TMUX_DEFAULT_SIZE, "176x50")   # window size
        cols, rows = w._webterm_term_grid()                   # client grid
        dw, dh = (int(x) for x in prov.TMUX_DEFAULT_SIZE.split("x"))
        self.assertEqual((cols, rows), (dw, dh + w.WEBTERM_STATUS_ROWS))
        self.assertEqual((cols, rows), (176, 51))
        # the #685 live-convergence helper targets the SAME constant object —
        # its default_size can never drift from the conf pin.
        sig = inspect.signature(prov.converge_tmux_window_geometry)
        self.assertIs(sig.parameters["default_size"].default,
                      prov.TMUX_DEFAULT_SIZE)


class TestViewportExactFill700(unittest.TestCase):
    """The third fill layer exists, fills EXACTLY, stays capped, and never
    touches the child screen transform (the #678 mouse invariant)."""

    def test_stretch_source_locks(self):
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        # the residual layer + its cap ship in the dashboard script
        self.assertIn("function stretchFrameToFill", html)
        self.assertRegex(html, r"WT_FRAME_FILL_MAX_STRETCH\s*=\s*1\.25\b")
        fn = _extract_js_function(html, "stretchFrameToFill")
        # PARENT-side mechanism: the iframe element, origin center, clamped
        self.assertIn("frameElement", fn)
        self.assertIn("transformOrigin", fn)
        self.assertIn("WT_FRAME_FILL_MAX_STRETCH", fn)
        # runs wherever the native fill runs (immediate + RO + timed passes)
        self.assertIn("stretchFrameToFill", _extract_js_function(html, "scheduleFill"))
        # the spilled letterbox is clipped by the frames container
        self.assertRegex(html, r"#frames\s*\{[^}]*overflow:\s*hidden")
        # the node harness's cap can never silently drift from what ships
        # (same lock pattern as test_fit_fill_caps_match_source)
        self.assertIn("const WT_FRAME_FILL_MAX_STRETCH = 1.25;", _FIT_HARNESS)

    def test_stretch_fills_viewport_exactly_and_keeps_mouse_exact(self):
        # BEHAVIOURAL (node): after fit+fill+stretch the grid spans the
        # viewport to sub-pixel on BOTH axes (the #700 margins + "empty row"
        # gone), while the CHILD screen transform stays identity — the #678
        # mouse doctrine — and the frame stretch stays within its cap.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        for vw, vh, tag in ((1920, 1076, "owner PWA (DPR 1.5)"),
                            (1536, 864, "laptop 125%")):
            out = _run_fit_harness(html, vw, vh)
            self.assertTrue(out["stretched"], "%s: stretch pass must run" % tag)
            for axis, fs in (("X", out["frameScaleX"]), ("Y", out["frameScaleY"])):
                self.assertGreaterEqual(fs, 1, "%s: never a shrink (%s)" % (tag, axis))
                self.assertLessEqual(fs, 1.25 + 1e-6, "%s: capped (%s)" % (tag, axis))
            self.assertAlmostEqual(out["gridW"] * out["frameScaleX"], out["availW"],
                                   delta=1.5, msg="%s: EXACT horizontal fill" % tag)
            self.assertAlmostEqual(out["gridH"] * out["frameScaleY"], out["availH"],
                                   delta=1.5, msg="%s: EXACT vertical fill" % tag)
            # centered-origin scaling is what lands the grid on the frame edges
            self.assertEqual(out["frameOrigin"], "50% 50%", tag)
            # the child screen is NEVER transformed (#678 mouse hit-test)
            self.assertEqual(out["scaleX"], 1, "%s: child screen untouched" % tag)
            self.assertEqual(out["scaleY"], 1, "%s: child screen untouched" % tag)

    def test_stretch_capped_on_extreme_viewport_degrades_to_letterbox(self):
        # an absurd viewport must clamp at the cap (bounded distortion) and
        # keep a residual letterbox instead of stretching grotesquely.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        out = _run_fit_harness(html, 6000, 400)
        self.assertAlmostEqual(out["frameScaleX"], 1.25, places=4)
        self.assertLess(out["gridW"] * out["frameScaleX"], out["availW"],
                        "capped stretch must leave the letterbox, not fill")
        self.assertLessEqual(out["frameScaleY"], 1.25 + 1e-6)
        self.assertEqual(out["scaleX"], 1)   # child screen still untouched
        self.assertEqual(out["scaleY"], 1)

    def test_stretch_is_identity_none_when_grid_already_exact(self):
        # a viewport that the integer fill already fills exactly needs NO
        # transform at all — the stretch must set 'none', not scale(1,1)
        # (no pointless compositing layer). At (1408, 816) the harness model
        # lands fontSize 13 / ls 0 / lh 1 → grid exactly 1408x816.
        if shutil.which("node") is None:
            self.skipTest("node not available")
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        out = _run_fit_harness(html, 1408, 816)
        self.assertEqual(out["frameTransform"], "none")
        self.assertEqual(out["frameScaleX"], 1)
        self.assertEqual(out["frameScaleY"], 1)


if __name__ == "__main__":
    unittest.main()
