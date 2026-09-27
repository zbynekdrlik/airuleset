"""#1164 -- every tab is fitted when it is SHOWN; the touch key bar is touch-only.

Owner (27.9.2026): on the webterm's first load every terminal is shrunk and
offset; only the second visit shows it full size. Also the #1159 touch key bar
showed on his desktop (a touchscreen laptop with a mouse/trackpad).

Measured root cause (issuecomment on the ticket): `activate()` fitted the tab in
the same task that flipped its iframe from `display:none` to `block`. xterm
pauses its renderer while its element is not intersecting and QUEUES every
resize until it is shown again, so the never-shown frame still reported its
80x24 boot box (560x360) and `fitFixedGrid` picked font 29 instead of 17. The
grid then overflowed and the #798 over-fit path shrank it to scale(0.596).

Fix (the main's Approach 1): one fit entry `fitShown(f)` that never measures a
hidden or not-yet-unpaused frame (it keeps a dirty flag instead), and a per-frame
IntersectionObserver show signal `watchShown(f)` that consumes the flag one
animation frame after the frame is really shown. The bar is gated on a
touch-only device: coarse pointer AND `(hover: none)` AND no available pointer
can hover, `(not (any-hover: hover))` -- Chromium matches `(any-hover: none)` as
soon as ANY pointer (the touchscreen) cannot hover, so that form is not enough.

Three tiers:
* STRUCTURAL -- the gate string (one source, CSS + JS), the single fit entry,
  activate marks the tab dirty.
* NODE -- the REAL extracted fitShown/watchShown against stub frames: a hidden
  frame is never measured, a shown one is fitted exactly once after the show
  signal, a hide re-arms the flag.
* REAL BROWSER (node playwright + chromium headless-shell + a real ttyd;
  SKIPS where absent) -- the real dashboard behind the real gateway in front of
  a throwaway loopback ttyd (real xterm.js): the first-activation grid box equals
  the second and fills the slot for every tab, on desktop and on phone/tablet
  profiles; a hidden bar re-fits the active tab (landscape phone, over-fit
  path); a touchscreen laptop with a
  hover pointer shows no bar; zero console errors/warnings.
"""
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
import cli_webterm_keybar as kb  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402
from test_webterm import _extract_js_function  # noqa: E402
from test_webterm_keybar_1159 import _find_chromium, _find_node_playwright  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TOUCH_ONLY = "(pointer: coarse) and (hover: none) and (not (any-hover: hover))"
NTABS = 5


def _inv(n=NTABS):
    return [{"id": "s%d" % i, "label": "s%d" % i, "kind": "owner", "local": False,
             "host": "10.0.0.%d" % (i + 1), "user": "u"} for i in range(n)]


def _render(n=NTABS):
    return w.render_dashboard_html(_inv(n), ttyd_base="/t")


def _script(html):
    s = html.index("<script>") + len("<script>")
    return html[s:html.index("</script>", s)]


class TestTouchOnlyGate1164(unittest.TestCase):

    def test_gate_constant_is_touch_only(self):
        self.assertEqual(kb.KEYBAR_MEDIA, TOUCH_ONLY)

    def test_css_shows_the_bar_only_under_the_touch_only_gate(self):
        self.assertIn("@media %s {" % TOUCH_ONLY, kb.KEYBAR_CSS)
        self.assertEqual(kb.KEYBAR_CSS.count("@media"), 1)

    def test_keyboard_fit_uses_the_same_gate(self):
        html = _render()
        fn = _extract_js_function(html, "keybarFitViewport")
        self.assertIn("matchMedia(KEYBAR_MEDIA)", fn)
        self.assertIn("const KEYBAR_MEDIA = '%s';" % TOUCH_ONLY, html)


class TestFitLifecycleStructure1164(unittest.TestCase):

    def setUp(self):
        self.html = _render()
        self.js = _script(self.html)

    def test_fit_is_measured_through_one_entry_only(self):
        # every fitFixedGrid CALL (not the definition) lives inside fitShown
        calls = [m.start() for m in re.finditer(r"(?<!function )\bfitFixedGrid\(win\)", self.js)]
        fn = _extract_js_function(self.html, "fitShown")
        self.assertEqual(len(calls), 1, calls)
        self.assertIn("fitFixedGrid(win)", fn)

    def test_activate_marks_the_tab_dirty_and_attaches(self):
        fn = _extract_js_function(self.html, "activate")
        self.assertRegex(fn, r"__wtFitDirty\s*=\s*true")
        self.assertIn("applyFixedGrid(made[idx])", fn)

    def test_the_slot_observer_and_child_resize_use_the_gated_entry(self):
        apply_fn = _extract_js_function(self.html, "applyFixedGrid")
        self.assertIn("watchShown(f)", apply_fn)
        self.assertIn("fitShown(f)", apply_fn)
        ro = self.js[self.js.index("new ResizeObserver(() => {"):]
        ro = ro[:ro.index("observe(frames)")]
        # through the attach poll, so a tab whose poll gave up before its
        # terminal existed is still attached and fitted on a slot change
        self.assertIn("applyFixedGrid(made[current])", ro)
        self.assertNotIn("fitFixedGrid(", ro)

    def test_show_signal_is_an_intersection_observer_then_one_frame(self):
        fn = _extract_js_function(self.html, "watchShown")
        self.assertIn("IntersectionObserver", fn)
        self.assertIn("requestAnimationFrame(", fn)
        self.assertIn(".xterm-screen", fn)


# ---------------------------------------------------------------------------
# NODE: the real extracted lifecycle functions against stub frames.
# ---------------------------------------------------------------------------
_LIFECYCLE_HARNESS = r"""
let fits = 0, fills = 0, fitOk = true;
function fitFixedGrid(win) { fits++; return fitOk; }
function scheduleFill(win) { fills++; }
const rafQ = [];
function requestAnimationFrame(cb) { rafQ.push(cb); }
function flush() { while (rafQ.length) rafQ.shift()(); }
%(fns)s
function mk(withIO) {
  let cb = null;
  const el = {};
  const win = { term: {}, document: { querySelector: (s) => (s === '.xterm-screen' ? el : null) } };
  if (withIO) win.IntersectionObserver = function (c) { cb = c; this.observe = (x) => { this.el = x; }; };
  const f = { style: { display: 'none' }, contentWindow: win };
  return { f, io: (v) => cb([{ isIntersecting: v }]) };
}
const out = {};
const a = mk(true);
watchShown(a.f);
out.hiddenRet = fitShown(a.f); out.hidden = [fits, !!a.f.__wtFitDirty];
a.f.style.display = 'block';
fitShown(a.f); out.shownBeforeSignal = [fits, !!a.f.__wtFitDirty];
a.io(true); out.afterSignalBeforeFrame = fits;
flush(); out.afterFrame = [fits, fills, !!a.f.__wtFitDirty];
fitShown(a.f); out.whileShown = [fits, fills];
a.io(false);                       // not intersecting any more, display still block
fitShown(a.f); out.notIntersecting = [fits, !!a.f.__wtFitDirty];
a.f.style.display = 'none';
fitShown(a.f); out.afterHide = [fits, !!a.f.__wtFitDirty];
a.f.style.display = 'block'; a.io(true); a.f.style.display = 'none'; flush();
out.hiddenAgainBeforeFrame = [fits, !!a.f.__wtFitDirty];
a.f.style.display = 'block'; a.io(true); flush();
out.reshown = [fits, !!a.f.__wtFitDirty];
fitOk = false; out.failRet = fitShown(a.f); out.failDirty = !!a.f.__wtFitDirty; fitOk = true;
fits = 0;
const b = mk(false);
watchShown(b.f);
fitShown(b.f); out.noIOHidden = fits;
b.f.style.display = 'block'; fitShown(b.f); out.noIOShown = fits;
const c = mk(true); c.f.contentWindow.term = null;
out.noTermRet = fitShown(c.f);
process.stdout.write(JSON.stringify(out));
"""


class TestFitLifecycleNode1164(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not available")
        html = _render()
        fns = "\n".join(_extract_js_function(html, n) for n in ("fitShown", "watchShown"))
        d = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, d, True)
        (Path(d) / "h.js").write_text(_LIFECYCLE_HARNESS % {"fns": fns}, encoding="utf-8")
        r = subprocess.run([node, str(Path(d) / "h.js")], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout)

    def test_a_hidden_frame_is_never_measured(self):
        self.assertTrue(self.out["hiddenRet"])
        self.assertEqual(self.out["hidden"], [0, True])

    def test_a_just_shown_frame_waits_for_the_show_signal(self):
        # display:block alone is not enough: xterm is still paused
        self.assertEqual(self.out["shownBeforeSignal"], [0, True])
        self.assertEqual(self.out["afterSignalBeforeFrame"], 0)

    def test_the_signal_fits_once_one_frame_later(self):
        self.assertEqual(self.out["afterFrame"], [1, 1, False])

    def test_a_visible_frame_fits_immediately(self):
        self.assertEqual(self.out["whileShown"], [2, 2])

    def test_a_not_intersecting_signal_alone_rearms_the_deferral(self):
        self.assertEqual(self.out["notIntersecting"], [2, True])

    def test_a_hide_rearms_the_deferral(self):
        self.assertEqual(self.out["afterHide"], [2, True])
        self.assertEqual(self.out["hiddenAgainBeforeFrame"], [2, True])
        self.assertEqual(self.out["reshown"], [3, False])

    def test_a_failed_fit_on_a_shown_frame_asks_for_a_retry(self):
        self.assertFalse(self.out["failRet"])
        self.assertTrue(self.out["failDirty"])

    def test_without_intersection_observer_only_display_gates(self):
        self.assertEqual(self.out["noIOHidden"], 0)
        self.assertEqual(self.out["noIOShown"], 1)

    def test_no_term_yet_is_not_handled(self):
        self.assertFalse(self.out["noTermRet"])


# ---------------------------------------------------------------------------
# REAL BROWSER: real dashboard + real gateway + a throwaway loopback real ttyd.
# ---------------------------------------------------------------------------
_DRIVER = r"""
const [,, PW, CH, BASE, NTABS] = process.argv;
const N = +NTABS;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
// third-party noise that is not the dashboard's: ttyd's own frontend parses its
// URL query (our ?arg=) as client options and warns; the headless GPU driver.
const NOISE = [/\[ttyd\] maybe unknown option: arg=/, /GL Driver Message/, /GPU stall/];
(async () => {
  const { chromium } = require(PW);
  const out = { consoleHits: [] };
  const grid = (p) => p.evaluate(() => {
    const frames = document.getElementById('frames'), fr = frames.getBoundingClientRect();
    const f = [...document.querySelectorAll('#frames iframe')].find(x => x.style.display !== 'none');
    const w = f.contentWindow, g = w.document.querySelector('.xterm-screen').getBoundingClientRect();
    const t = getComputedStyle(f).transform, m = new DOMMatrix(t === 'none' ? undefined : t);
    const r = (x) => Math.round(x * 10) / 10;
    return { slotW: frames.clientWidth, slotH: frames.clientHeight,
      left: r(m.e + m.a * g.left), top: r(m.f + m.d * g.top), w: r(m.a * g.width), h: r(m.d * g.height),
      font: w.term.options.fontSize, framesBottom: fr.bottom, vh: innerHeight, boxExplicit: !!f.style.height };
  });
  const settle = async (p) => {       // until the grid box is unchanged for 600 ms
    await sleep(800);
    let last = JSON.stringify(await grid(p));
    for (let i = 0; i < 40; i++) {
      await sleep(300);
      const now = JSON.stringify(await grid(p));
      if (now === last) { await sleep(300); if (JSON.stringify(await grid(p)) === now) return JSON.parse(now); }
      last = now;
    }
    return Object.assign(JSON.parse(last), { unsettled: true });   // never converged: fails the test
  };
  const run = async (name, ctxOpts, launchArgs, extra) => {
    const b = await chromium.launch({ executablePath: CH, headless: true, args: ['--no-sandbox'].concat(launchArgs || []) });
    const ctx = await b.newContext(ctxOpts);
    const lr = await ctx.request.post(BASE + '/login', { form: { username: 'u', password: 'p' }, maxRedirects: 0 });
    if (lr.status() !== 303) throw new Error('login failed ' + lr.status());
    const p = await ctx.newPage();
    p.on('console', m => { const t = m.type(), x = m.text();
      if ((t === 'error' || t === 'warning') && !NOISE.some(re => re.test(x))) out.consoleHits.push(name + ':' + t + ':' + x); });
    p.on('pageerror', e => out.consoleHits.push(name + ':pageerror:' + e.message));
    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForFunction((n) => {
      const fs = document.querySelectorAll('#frames iframe');
      if (fs.length !== n) return false;
      for (const f of fs) { try { if (!f.contentWindow.term || !f.contentWindow.document.querySelector('.xterm-screen')) return false; } catch (e) { return false; } }
      return true;
    }, N, { timeout: 30000 });
    const r = { bar: await p.evaluate(() => getComputedStyle(document.getElementById('keybar')).display),
                first: [await settle(p)], second: [] };
    for (let i = 1; i < N; i++) { await p.click('.tab[data-idx="' + i + '"]'); r.first.push(await settle(p)); }
    for (let i = 0; i < N; i++) { await p.click('.tab[data-idx="' + i + '"]'); r.second.push(await settle(p)); }
    if (extra) await extra(p, r);
    out[name] = r;
    await b.close();
  };
  await run('desktop', { viewport: { width: 1920, height: 1080 } });
  await run('phone', { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  await run('tablet', { viewport: { width: 1280, height: 800 }, hasTouch: true, isMobile: true });
  // a landscape phone is too short for the grid: the #798 over-fit path gives the
  // iframe an explicit box, so its own 'resize' never fires on a slot change and
  // only the parent #frames observer can re-fit it when the bar's height goes away
  await run('phoneland', { viewport: { width: 844, height: 390 }, hasTouch: true, isMobile: true }, [], async (p, r) => {
    await sleep(2500);      // past scheduleFill's last timed pass (2000 ms), which would re-fit on its own
    await p.evaluate(() => { document.getElementById('keybar').style.display = 'none'; });
    r.barGone = await settle(p);
  });
  // a touchscreen laptop: the PRIMARY pointer is coarse and cannot hover, but a
  // mouse/trackpad that can hover is also available (Blink's own pointer settings)
  await run('touchlaptop', { viewport: { width: 1920, height: 1080 } },
    ['--blink-settings=primaryPointerType=2,primaryHoverType=1,availablePointerTypes=6,availableHoverTypes=3'],
    async (p, r) => { r.media = await p.evaluate(() => ({ coarse: matchMedia('(pointer: coarse)').matches,
      hoverNone: matchMedia('(hover: none)').matches, anyHover: matchMedia('(any-hover: hover)').matches })); });
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))              # loopback ONLY (#681)
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_http(url, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return                         # the server answers (302/401 is fine)
        except OSError:
            time.sleep(0.1)
    raise AssertionError("not up: %s" % url)


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


class TestFirstViewFitRealBrowser1164(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node, ttyd = shutil.which("node"), shutil.which("ttyd")
        pw, chromium = _find_node_playwright(), _find_chromium()
        if not (node and ttyd and pw and chromium):
            raise unittest.SkipTest("node + playwright + chromium + ttyd not all available")
        d = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, str(d), True)
        (d / "index.html").write_text(_render(), encoding="utf-8")
        pwa.write_pwa_assets(d, "zbynek")
        (d / "cred").write_text("u:p\n", encoding="utf-8")
        (d / "driver.js").write_text(_DRIVER, encoding="utf-8")
        tp, gp = _free_port(), _free_port()
        # a throwaway REAL ttyd (real xterm.js) on loopback; never a live service
        tt = subprocess.Popen([ttyd, "-p", str(tp), "-i", "127.0.0.1", "-b", "/t", "-a", "-W",
                               "bash", "-c", 'echo "tab $0"; exec cat'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(_stop, tt)
        gw = subprocess.Popen([sys.executable, str(REPO / "cli_webterm_gateway.py"),
                               "--bind", "127.0.0.1", "--port", str(gp),
                               "--dash-index", str(d / "index.html"), "--cred", str(d / "cred"),
                               "--ttyd-port", str(tp)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(_stop, gw)
        base = "http://127.0.0.1:%d" % gp
        _wait_http("http://127.0.0.1:%d/t/" % tp)
        _wait_http(base + "/login")
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium, base, str(NTABS)],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise AssertionError("browser driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def _assert_fills(self, g, tag):
        # settled, inside the slot, and the tighter axis fills it (the #678
        # native-cell residual is removed by the #700 stretch; the #798 over-fit
        # path keeps its +16 px box slack, a few % on a short phone)
        self.assertFalse(g.get("unsettled"), tag)
        self.assertGreaterEqual(g["left"], -1.5, tag)
        self.assertGreaterEqual(g["top"], -1.5, tag)
        self.assertLessEqual(g["left"] + g["w"], g["slotW"] + 1.5, tag)
        self.assertLessEqual(g["top"] + g["h"], g["slotH"] + 1.5, tag)
        self.assertGreaterEqual(max(g["w"] / g["slotW"], g["h"] / g["slotH"]), 0.95, tag)

    def _assert_first_equals_second(self, name):
        r = self.out[name]
        self.assertEqual(len(r["first"]), NTABS)
        for i, (a, b) in enumerate(zip(r["first"], r["second"])):
            tag = "%s tab %d first=%s second=%s" % (name, i, a, b)
            for k in ("left", "top", "w", "h"):
                self.assertAlmostEqual(a[k], b[k], delta=1.0, msg=tag)
            self.assertEqual(a["font"], b["font"], tag)
            self._assert_fills(a, tag)

    def test_desktop_first_view_equals_second_and_fills(self):
        self._assert_first_equals_second("desktop")
        for g in self.out["desktop"]["first"]:     # a desktop slot is filled on BOTH axes
            self.assertAlmostEqual(g["w"], g["slotW"], delta=2, msg=g)
            self.assertAlmostEqual(g["h"], g["slotH"], delta=2, msg=g)

    def test_phone_first_view_equals_second_and_fills(self):
        self._assert_first_equals_second("phone")

    def test_tablet_first_view_equals_second_and_fills(self):
        self._assert_first_equals_second("tablet")

    def test_landscape_phone_first_view_equals_second_and_fills(self):
        self._assert_first_equals_second("phoneland")

    def test_the_bar_height_change_refits_the_active_tab(self):
        r = self.out["phoneland"]
        before, after = r["second"][-1], r["barGone"]
        self.assertTrue(before["boxExplicit"], before)   # the path only the slot observer re-fits
        self.assertGreater(after["slotH"], before["slotH"] + 20)
        self._assert_fills(after, "after the bar went away: %s" % after)

    def test_desktop_shows_no_bar(self):
        g = self.out["desktop"]
        self.assertEqual(g["bar"], "none")
        self.assertAlmostEqual(g["first"][0]["framesBottom"], g["first"][0]["vh"], delta=1)

    def test_touch_devices_show_the_bar(self):
        self.assertEqual(self.out["phone"]["bar"], "flex")
        self.assertEqual(self.out["tablet"]["bar"], "flex")
        self.assertEqual(self.out["phoneland"]["bar"], "flex")

    def test_touchscreen_laptop_with_a_hover_pointer_shows_no_bar(self):
        r = self.out["touchlaptop"]
        # the profile really is the owner's case: coarse primary, no primary hover
        self.assertEqual(r["media"], {"coarse": True, "hoverNone": True, "anyHover": True})
        self.assertEqual(r["bar"], "none")
        self.assertAlmostEqual(r["first"][0]["framesBottom"], r["first"][0]["vh"], delta=1)
        self._assert_first_equals_second("touchlaptop")

    def test_console_is_clean(self):
        self.assertEqual(self.out["consoleHits"], [])


if __name__ == "__main__":
    unittest.main()
