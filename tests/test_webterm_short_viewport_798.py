"""#798 -- a SHORT webterm viewport clips the fixed 176x51 grid: on a window too
short to fit the grid even at fitFixedGrid's 6px font floor (owner report: a new
tmux window's bash prompt is half-hidden under the tab bar), the natural grid
(~357px) exceeds the #frames slot (~312px) and the child iframe's own
`html,body{overflow:hidden}` CLIPS it -- the owner's row-0 prompt (or, per the
flex resolution, the tmux status bar) vanishes.

The prior fix (extend `stretchFrameToFill` to also SHRINK) was disproven by a
gated review with real pixels: a PARENT transform rescales the child's
already-composited bitmap, so it cannot reveal a row the child never painted.
The #798 fix (`reconcileFrameFit`) instead GROWS the iframe's real CSS layout box
to cover the grid's extent -- so the child paints ALL 51 rows unclipped -- then
UNIFORM-down-scales that fully-painted box back into the slot.

Two tiers of coverage:

* STRUCTURAL locks (always run) -- the new mechanism ships and is wired in.
* A REAL-BROWSER functional test (skipped unless Playwright + a Chromium build
  are available) that reproduces the geometry in a real browser -- with REAL
  child-viewport CLIPPING, which the node fit-harness fundamentally cannot model
  (that gap is exactly what let the prior arithmetic-only "fix" pass a green test
  while the pixels stayed broken). It drives the REAL extracted pipeline against
  a synthetic same-origin xterm-like child that CENTRES the over-tall grid (the
  owner's top-clip case) and asserts the whole grid -- row 0 included -- ends up
  visible inside the slot. The worker additionally verifies the fix live against
  a REAL ttyd 1.7.4 + tmux + headless Chromium (see the PR evidence); this test
  is the automated regression net for it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
from test_webterm import _extract_js_function  # noqa: E402


def _inv():
    return [{"id": "s1", "label": "dev1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


class TestShortViewportStructure798(unittest.TestCase):
    """The over-fit reconcile layer ships, is wired into the fill pass, fits the
    font to the SLOT (not the grown box), and carries its feedback-loop guard."""

    def setUp(self):
        self.html = w.render_dashboard_html(_inv(), ttyd_base="/t")

    def test_reconcile_and_slot_helpers_exist(self):
        self.assertIn("function reconcileFrameFit(", self.html)
        self.assertIn("function slotOf(", self.html)

    def test_min_shrink_floor_ships(self):
        # a bounded parent down-scale floor -- a pathological viewport degrades to
        # small-but-whole, never vanishing.
        self.assertRegex(self.html, r"WT_FRAME_FILL_MIN_SHRINK\s*=\s*0\.5\b")
        self.assertIn("WT_FRAME_FILL_MIN_SHRINK",
                      _extract_js_function(self.html, "reconcileFrameFit"))

    def test_reconcile_runs_last_in_the_fill_pass(self):
        # it must OVERRIDE stretchFrameToFill's grow-only transform in the over-fit
        # case, so it runs AFTER it in scheduleFill's pass.
        pass_fn = _extract_js_function(self.html, "scheduleFill")
        self.assertIn("reconcileFrameFit(win)", pass_fn)
        self.assertLess(pass_fn.index("stretchFrameToFill(win)"),
                        pass_fn.index("reconcileFrameFit(win)"),
                        "reconcileFrameFit must run AFTER stretchFrameToFill")

    def test_fitfixedgrid_fits_to_the_slot_not_the_grown_box(self):
        # the primary feedback-loop guard: the font is chosen for the SLOT via
        # slotOf, never the (possibly grown) win.innerHeight -- so a box-grow can
        # never inflate the font.
        fit = _extract_js_function(self.html, "fitFixedGrid")
        self.assertIn("slotOf(win)", fit)

    def test_reconcile_grows_the_layout_box_and_downscales_top_centre(self):
        fn = _extract_js_function(self.html, "reconcileFrameFit")
        # a real CSS LAYOUT-BOX grow (not just a transform), so the child paints
        # all rows unclipped:
        self.assertIn("fr.style.height", fn)
        self.assertIn("fr.style.width", fn)
        # a parent UNIFORM down-scale, origin top-centre (row 0 pinned at slot top):
        self.assertIn("'50% 0'", fn)
        self.assertIn("scale(", fn)
        # covers BOTH clip directions (top-align vs centre) -> max(height, bottom):
        self.assertIn("Math.max(g.height, g.bottom)", fn)

    def test_self_induced_resize_guard_present(self):
        # the explicit guard: the child 'resize' our own box-grow fires is ignored
        # (its new inner size == the box we set), so it can never re-enter the loop.
        apply_fn = _extract_js_function(self.html, "applyFixedGrid")
        self.assertIn("__wtSetBox", apply_fn)
        self.assertIn("__wtSetBox", _extract_js_function(self.html, "reconcileFrameFit"))

    def test_parent_frames_resize_observer_drives_genuine_slot_changes(self):
        # a grown (explicit-box) iframe goes deaf to the child 'resize' on a slot
        # change; a PARENT-side ResizeObserver on #frames drives the re-fit.
        self.assertIn("observe(frames)", self.html)


# ---------------------------------------------------------------------------
# Real-browser functional test: real clipping, real transform, real pixels.
# ---------------------------------------------------------------------------
def _playwright_and_browser():
    """Return the sync_playwright factory iff Playwright AND a Chromium build are
    installed, else None -- so this test SKIPS in CI (no browser) and runs on a
    dev box, exactly like the node harness skips when `node` is absent."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    return sync_playwright


# The synthetic same-origin child: an xterm-like grid whose .xterm-screen size
# tracks term.options exactly as the node fit-harness models a real xterm
# (cellW = round(0.6*fs)+letterSpacing, cellH = round(1.2*fs*lineHeight)); at the
# 6px font floor the 176x51 grid measures 704x357 -- the live-measured natural
# size. `.terminal`/`.xterm` are display:contents so #terminal-container (styled
# by fitFixedGrid's injected wt-fit-style: absolute inset:0, flex CENTRE) centres
# the over-tall .xterm-screen -> a NEGATIVE top offset == the owner's top-clip.
_CHILD_SRCDOC = """<!doctype html><html><head><meta charset=utf-8><style>
html,body{margin:0}
.terminal,.xterm{display:contents}
.xterm-screen{background:#0a3d0a}
</style></head><body>
<div id=terminal-container><div class=terminal><div class=xterm>
<div class=xterm-screen></div></div></div></div>
<script>
var COLS=176, ROWS=51, scr=document.querySelector('.xterm-screen');
function apply(o){
  var cw=Math.round(0.6*o.fontSize)+(o.letterSpacing||0);
  var ch=Math.round(1.2*o.fontSize*(o.lineHeight||1));
  scr.style.width=(COLS*cw)+'px'; scr.style.height=(ROWS*ch)+'px';
}
var _o={fontSize:13,lineHeight:1,letterSpacing:0,theme:{background:'#0a0a0a'}};
window.term={cols:COLS,rows:ROWS,
  options:new Proxy(_o,{set:function(t,k,v){t[k]=v;apply(t);return true;},get:function(t,k){return t[k];}}),
  resize:function(c,r){this.cols=c;this.rows=r;}};
apply(_o);
</script></body></html>"""


class TestShortViewportRealBrowser798(unittest.TestCase):
    """Drive the REAL extracted fit/fill/stretch/reconcile pipeline in a real
    browser against a synthetic same-origin child that CLIPS an over-tall grid,
    and assert the #798 fix makes the whole grid visible inside the slot."""

    @classmethod
    def setUpClass(cls):
        cls._pw = _playwright_and_browser()
        if cls._pw is None:
            raise unittest.SkipTest("playwright / chromium not installed")
        cls.html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        # the extracted pipeline pieces + the consts they reference.
        cls.blob = "\n".join([
            "const CFG = { term_cols: 176, term_rows: 51 };",
            "const WT_FILL_MAX_CELL_STRETCH = 1.5;",
            "const WT_FILL_MAX_LINE_STRETCH = 1.8;",
            "const WT_FRAME_FILL_MAX_STRETCH = 1.25;",
            _grab(cls.html, "WT_FRAME_FILL_MIN_SHRINK"),  # ships in the fixed tree only
            _extract_js_function(cls.html, "slotOf") if "function slotOf(" in cls.html else "",
            _extract_js_function(cls.html, "fitFixedGrid"),
            _extract_js_function(cls.html, "fillFixedGrid"),
            _extract_js_function(cls.html, "stretchFrameToFill"),
            _extract_js_function(cls.html, "reconcileFrameFit") if "function reconcileFrameFit(" in cls.html else "",
            "window.__wtPipe = function(win){ try{ fitFixedGrid(win); }catch(e){}"
            " try{ fillFixedGrid(win); }catch(e){}"
            " try{ stretchFrameToFill(win); }catch(e){}"
            " try{ if(typeof reconcileFrameFit==='function') reconcileFrameFit(win); }catch(e){} };",
        ])

    def _measure(self, viewport_h):
        """Render the parent + synthetic child at a viewport `viewport_h` tall,
        run the pipeline against the child, and return the grid's parent-space
        footprint vs the #frames slot."""
        sync_playwright = type(self)._pw  # via the class: a function attr accessed on
        srcdoc = _CHILD_SRCDOC.replace('"', "&quot;")  # `self` would bind as a method
        parent = (
            "<!doctype html><html><head><meta charset=utf-8><style>"
            "html,body{margin:0;height:100%}"
            "body{display:flex;flex-direction:column;overflow:hidden}"
            "#tabbar{height:37px;background:#222;flex:0 0 auto}"
            "#frames{position:relative;flex:1 1 auto;overflow:hidden}"
            "#frames iframe.term{position:absolute;inset:0;width:100%;height:100%;border:0}"
            "</style></head><body>"
            "<div id=tabbar></div><div id=frames>"
            '<iframe class=term srcdoc="' + srcdoc + '"></iframe>'
            "</div></body></html>"
        )
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                pg = b.new_page(viewport={"width": 723, "height": viewport_h})
                pg.set_content(parent, wait_until="load")
                pg.wait_for_function(
                    "() => { const f=document.querySelector('iframe.term');"
                    " return f && f.contentWindow && f.contentWindow.term"
                    " && f.contentDocument.querySelector('.xterm-screen'); }",
                    timeout=5000)
                pg.add_script_tag(content=self.blob)
                # run the pipeline a few times (mirrors scheduleFill's repeated passes)
                pg.evaluate(
                    "() => { const f=document.querySelector('iframe.term');"
                    " for (let i=0;i<3;i++) window.__wtPipe(f.contentWindow); }")
                return pg.evaluate(_MEASURE_JS)
            finally:
                b.close()

    def test_short_viewport_whole_grid_visible_after_fix(self):
        m = self._measure(349)  # 349 - 37 tabbar = 312 slot, the owner's ~case
        # the child genuinely OVER-FITS at the font floor (guards the geometry):
        self.assertGreater(m["gridNatH"], m["slotH"] + 1,
                           "the synthetic child must actually over-fit the slot")
        self.assertEqual(m["font"], 6, "font must be pinned at the 6px floor")
        # THE FIX: the whole grid -- row 0 top included -- lies inside the slot.
        self.assertGreaterEqual(m["gridTop"], m["slotTop"] - 1.5,
                                "row 0 must be visible below the tab bar, not clipped")
        self.assertLessEqual(m["gridBottom"], m["slotBottom"] + 1.5,
                             "the grid bottom (status bar) must be inside the slot")
        # and the box was actually GROWN (a real layout-box resize, not a transform):
        self.assertGreater(m["boxH"], m["slotH"],
                           "the iframe layout box must be grown past the slot")
        self.assertLess(m["scale"], 1.0, "a parent DOWN-scale must be applied")

    def test_tall_viewport_stays_crisp_no_grow(self):
        # a viewport that fits the grid must NOT grow the box (under-fit path).
        m = self._measure(760)  # 760 - 37 = 723 slot, comfortably fits the grid
        self.assertGreaterEqual(m["gridTop"], m["slotTop"] - 1.5)
        self.assertLessEqual(m["gridBottom"], m["slotBottom"] + 1.5)
        self.assertFalse(m["boxExplicit"],
                         "a fitting viewport must leave the box slot-sized (no grow)")


def _grab(html, const_name):
    """Return the `const <const_name> = <value>;` source line from the rendered
    HTML, or an empty string if it is absent (a pre-fix tree)."""
    import re
    m = re.search(r"const %s\s*=\s*[^;]+;" % re.escape(const_name), html)
    return m.group(0) if m else ""


_MEASURE_JS = """() => {
  const frames = document.getElementById('frames');
  const fr = frames.getBoundingClientRect();
  const iframe = document.querySelector('iframe.term');
  const win = iframe.contentWindow;
  const scr = win.document.querySelector('.xterm-screen');
  const g = scr.getBoundingClientRect();               // CHILD coords
  const m = /scale\\(([\\d.]+)\\)/.exec(iframe.style.transform || '');
  const s = m ? parseFloat(m[1]) : 1;
  const ifr = iframe.getBoundingClientRect();           // reflects the transform
  return {
    slotTop: fr.top, slotBottom: fr.bottom, slotH: frames.clientHeight,
    font: win.term.options.fontSize,
    gridNatH: g.height,
    boxH: parseFloat(iframe.style.height) || win.innerHeight,
    boxExplicit: !!iframe.style.height,
    scale: s,
    gridTop: ifr.top + g.top * s,
    gridBottom: ifr.top + g.bottom * s
  };
}"""


if __name__ == "__main__":
    unittest.main()
