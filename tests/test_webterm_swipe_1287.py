"""#1287 round 3 -- a phone swipe scrolls Claude Code like the PC mouse wheel.

Owner (17.11.2026): "ten scrool v historii nechapem lebo vobec sa to nesprava ako
mouse kolecka a tiez to tlaciko dole scrool co ide do tmux scrollu je uplne
zbytocne". Claude Code runs fullscreen on the alternate screen, so tmux holds no
history (`history_size` 0) and the key bar's `scroll` key (`Ctrl+B [`, tmux
copy-mode) shows nothing useful: it is removed.

On the PC a wheel notch over the xterm becomes an SGR wheel report
(`ESC[<64;x;yM` up, `ESC[<65;x;yM` down), tmux `mouse on` passes it to Claude
(Claude turned on SGR any-event tracking), and Claude scrolls its own view
(measured on a private tmux + `claude --bare`, ticket comment). The phone gets
the same path: a one-finger vertical swipe over a terminal on a touch-only
device dispatches one `WheelEvent` per row of finger travel into the frame, and
xterm encodes it exactly as for a real wheel.

Three tiers:
* STRUCTURAL -- the hook ships in the keybar leaf, is attached per terminal by
  `applyFixedGrid`, is touch-only gated and never types or pastes bytes itself.
* NODE -- the REAL shipped keybar script against stub frames: rows of travel
  become wheel ticks (finger down = wheel up), a tap, a horizontal move, a long
  hold and a two-finger touch send none, a non-touch device attaches nothing.
* REAL BROWSER (node playwright + chromium + a real loopback ttyd behind the
  real gateway; SKIPS where absent) -- an app in SGR any-event mode receives
  wheel reports for a swipe on the phone and for a mouse wheel on the desktop;
  a tap focuses the terminal and sends no wheel; zero console errors.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
import cli_webterm_keybar as kb  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402
from test_webterm import _extract_js_function  # noqa: E402
from test_webterm_first_view_fit_1164 import _free_port, _stop, _wait_http  # noqa: E402
from test_webterm_keybar_1159 import _find_chromium, _find_node_playwright, _keybar_script  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _render(n=1):
    inv = [{"id": "s%d" % i, "label": "s%d" % i, "kind": "owner", "local": False,
            "host": "10.0.0.%d" % (i + 1), "user": "u"} for i in range(n)]
    return w.render_dashboard_html(inv, ttyd_base="/t")


class TestSwipeStructure1287(unittest.TestCase):

    def setUp(self):
        self.html = _render(3)
        self.js = _keybar_script(self.html)

    def test_the_copy_mode_scroll_key_is_gone(self):
        self.assertNotIn('data-k="scroll"', kb.KEYBAR_HTML)
        self.assertNotIn("scroll:", self.js)
        self.assertNotIn("\\x02[", self.js)

    def test_every_terminal_gets_the_swipe_hook(self):
        poll = _extract_js_function(self.html, "applyFixedGrid")
        self.assertIn("keybarAttachSwipe(win)", poll)
        # the keybar script comes after the main one: guard the forward reference
        self.assertIn("typeof keybarAttachSwipe === 'function'", poll)
        self.assertIn("function keybarAttachSwipe(", self.js)

    def test_touch_only_and_wheel_only(self):
        fn = self.js[self.js.index("function keybarAttachSwipe("):]
        self.assertIn("matchMedia(KEYBAR_MEDIA)", fn)
        self.assertIn("passive: false", fn)
        wheel = self.js[self.js.index("function keybarWheel("):]
        wheel = wheel[:wheel.index("\n}\n")]
        self.assertIn("WheelEvent", wheel)
        for banned in ("paste(", ".input(", "triggerDataEvent", "\\x1b"):
            self.assertNotIn(banned, wheel)


_NODE = r"""
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
function mkWin(rows, screenH) {
  const ls = {}, wheels = [], win = {};
  const el = { addEventListener(t, f, o) { (ls[t] = ls[t] || []).push([f, o]); } };
  class WE { constructor(type, init) { Object.assign(this, init, { type }); } }
  win.WheelEvent = WE;
  win.term = { rows, element: el };
  win.document = {
    querySelector: (q) => q === '.xterm-screen' ? { getBoundingClientRect: () => ({ height: screenH }) } : null,
    elementFromPoint: () => ({ dispatchEvent: (e) => { wheels.push([e.deltaY, e.deltaMode, e.clientX, e.clientY, e.bubbles]); return true; } }),
  };
  return { win, ls, wheels };
}
let now = 1000;
const ctx = { console, Date: { now: () => now }, Math, document: { getElementById: () => null },
  made: {}, current: 0, focusTerminal() {} };
ctx.window = { matchMedia: (q) => ({ matches: ctx.touch && q === '(pointer: coarse) and (hover: none) and (not (any-hover: hover))' }) };
vm.createContext(ctx);
vm.runInContext(src, ctx);
function fire(s, type, pts) {
  const ev = { touches: pts.map(([x, y]) => ({ clientX: x, clientY: y })), prevented: 0, stopped: 0,
    preventDefault() { this.prevented++; }, stopPropagation() { this.stopped++; } };
  (s.ls[type] || []).forEach(([f]) => f(ev));
  return ev;
}
// rows 50 over a 1000 px screen: one row = 20 px of finger travel
function run(name, steps) {
  const s = mkWin(50, 1000);
  ctx.keybarAttachSwipe(s.win);
  ctx.keybarAttachSwipe(s.win);              // idempotent
  const prevented = [];
  for (const st of steps) {
    if (st.wait) { now += st.wait; continue; }
    const ev = fire(s, st.t, st.p || []);
    if (st.t === 'touchmove') prevented.push(ev.prevented);
  }
  return { wheels: s.wheels, prevented, nStart: (s.ls.touchstart || []).length,
    opts: Object.fromEntries(Object.entries(s.ls).map(([k, v]) => [k, v[0][1]])) };
}
const out = {};
ctx.touch = true;
out.down = run('down', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchmove', p: [[101, 130]] },
  { t: 'touchmove', p: [[101, 165]] }, { t: 'touchend' }]);
out.up = run('up', [{ t: 'touchstart', p: [[100, 300]] }, { t: 'touchmove', p: [[100, 255]] }, { t: 'touchend' }]);
out.small = run('small', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchmove', p: [[100, 106]] }, { t: 'touchend' }]);
out.tap = run('tap', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchend' }]);
out.horiz = run('horiz', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchmove', p: [[160, 110]] },
  { t: 'touchmove', p: [[160, 170]] }, { t: 'touchend' }]);
out.hold = run('hold', [{ t: 'touchstart', p: [[100, 100]] }, { wait: 900 }, { t: 'touchmove', p: [[100, 200]] }, { t: 'touchend' }]);
out.two = run('two', [{ t: 'touchstart', p: [[100, 100], [200, 100]] }, { t: 'touchmove', p: [[100, 200], [200, 200]] }, { t: 'touchend' }]);
out.big = run('big', [{ t: 'touchstart', p: [[100, 0]] }, { t: 'touchmove', p: [[100, 5000]] }, { t: 'touchend' }]);
out.after = run('after', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchmove', p: [[100, 145]] }, { t: 'touchend' },
  { t: 'touchmove', p: [[100, 300]] }]);
ctx.touch = false;
out.desktop = run('desktop', [{ t: 'touchstart', p: [[100, 100]] }, { t: 'touchmove', p: [[100, 200]] }]);
out.noTerm = (() => { try { ctx.keybarAttachSwipe({}); ctx.keybarAttachSwipe(null); return 'ok'; } catch (e) { return String(e); } })();
process.stdout.write(JSON.stringify(out));
"""


class TestSwipeNode1287(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not available")
        d = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, str(d), True)
        (d / "h.js").write_text(_NODE, encoding="utf-8")
        (d / "kb.js").write_text(_keybar_script(_render()), encoding="utf-8")
        r = subprocess.run([node, str(d / "h.js"), str(d / "kb.js")],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout)

    def test_finger_down_scrolls_back_one_wheel_tick_per_row(self):
        # 65 px of travel at 20 px per row = 3 ticks, wheel UP (older content),
        # line mode, at the finger's point, bubbling like a real wheel
        self.assertEqual(self.out["down"]["wheels"],
                         [[-1, 1, 101, 165, True]] * 3)
        self.assertEqual(self.out["down"]["prevented"], [1, 1])

    def test_finger_up_scrolls_forward(self):
        self.assertEqual([x[0] for x in self.out["up"]["wheels"]], [1, 1])

    def test_a_tap_and_a_tiny_move_send_nothing_and_keep_the_default(self):
        for k in ("tap", "small"):
            self.assertEqual(self.out[k]["wheels"], [], k)
        self.assertEqual(self.out["small"]["prevented"], [0])

    def test_horizontal_long_hold_and_two_fingers_are_left_alone(self):
        for k in ("horiz", "hold", "two"):
            self.assertEqual(self.out[k]["wheels"], [], k)
            self.assertEqual(sum(self.out[k]["prevented"]), 0, k)

    def test_one_move_is_capped(self):
        self.assertEqual(len(self.out["big"]["wheels"]), 30)

    def test_a_move_after_the_touch_ended_sends_nothing(self):
        self.assertEqual(len(self.out["after"]["wheels"]), 2)

    def test_listeners_attach_once_capture_and_movable(self):
        d = self.out["down"]
        self.assertEqual(d["nStart"], 1)
        self.assertTrue(d["opts"]["touchmove"]["capture"])
        self.assertIs(d["opts"]["touchmove"]["passive"], False)

    def test_a_device_with_a_hover_pointer_attaches_nothing(self):
        self.assertEqual(self.out["desktop"]["nStart"], 0)
        self.assertEqual(self.out["desktop"]["wheels"], [])

    def test_no_terminal_yet_is_harmless(self):
        self.assertEqual(self.out["noTerm"], "ok")


# The app under the real ttyd: alternate screen + SGR any-event mouse, exactly
# the modes Claude Code turns on; it logs every byte it receives.
_APP = r"""
import os, sys, termios, tty
log = sys.argv[1]
old = termios.tcgetattr(0)
tty.setraw(0)
os.write(1, b"\x1b[?1049h\x1b[?1003h\x1b[?1006hREADY\r\n")
try:
    with open(log, "ab", buffering=0) as f:
        while True:
            b = os.read(0, 4096)
            if not b:
                break
            f.write(b)
finally:
    termios.tcsetattr(0, termios.TCSADRAIN, old)
"""

_DRIVER = r"""
const [,, PW, CH, BASE, LOG] = process.argv;
const fs = require('fs');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const NOISE = [/\[ttyd\] maybe unknown option: arg=/, /GL Driver Message/, /GPU stall/];
const logLen = () => { try { return fs.statSync(LOG).size; } catch (e) { return 0; } };
const since = (n) => { try { return fs.readFileSync(LOG).subarray(n).toString('latin1'); } catch (e) { return ''; } };
const count = (s, re) => (s.match(re) || []).length;
const UP = /\x1b\[<64;\d+;\d+M/g, DOWN = /\x1b\[<65;\d+;\d+M/g;
(async () => {
  const { chromium } = require(PW);
  const out = { consoleHits: [] };
  const open = async (name, opts) => {
    const b = await chromium.launch({ executablePath: CH, headless: true, args: ['--no-sandbox'] });
    const ctx = await b.newContext(opts);
    const lr = await ctx.request.post(BASE + '/login', { form: { username: 'u', password: 'p' }, maxRedirects: 0 });
    if (lr.status() !== 303) throw new Error('login failed ' + lr.status());
    const p = await ctx.newPage();
    p.on('console', m => { const t = m.type(), x = m.text();
      if ((t === 'error' || t === 'warning') && !NOISE.some(re => re.test(x))) out.consoleHits.push(name + ':' + t + ':' + x); });
    p.on('pageerror', e => out.consoleHits.push(name + ':pageerror:' + e.message));
    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForFunction(() => {
      const f = document.querySelector('#frames iframe');
      try { const t = f.contentWindow.term; return t && t.modes && t.modes.mouseTrackingMode === 'any'; } catch (e) { return false; }
    }, null, { timeout: 30000 });
    await sleep(2500);       // past scheduleFill's timed passes
    const g = await p.evaluate(() => {
      const f = document.querySelector('#frames iframe'), w = f.contentWindow;
      const s = w.document.querySelector('.xterm-screen').getBoundingClientRect();
      const t = getComputedStyle(f).transform, m = new DOMMatrix(t === 'none' ? undefined : t);
      const fr = f.getBoundingClientRect();
      const left = fr.left + m.e + m.a * s.left, top = fr.top + m.f + m.d * s.top;
      return { cx: left + m.a * s.width / 2, cy: top + m.d * s.height / 2, row: m.d * s.height / w.term.rows,
               hooked: w.term.__wtSwipe === true };
    });
    return { b, p, g };
  };
  // ---- phone: swipes through CDP touch input ----
  const ph = await open('phone', { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  const cdp = await ph.p.context().newCDPSession(ph.p);
  const touch = (type, pts) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: pts.map(([x, y]) => ({ x, y })) });
  const swipe = async (x0, y0, x1, y1, steps, hold) => {
    const n = logLen();
    await touch('touchStart', [[x0, y0]]);
    if (hold) await sleep(hold);
    for (let i = 1; i <= steps; i++) { await touch('touchMove', [[x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps]]); await sleep(16); }
    await touch('touchEnd', []);
    await sleep(500);
    const s = since(n);
    return { up: count(s, UP), down: count(s, DOWN) };
  };
  const { cx, cy, row } = ph.g;
  out.phone = { row, hooked: ph.g.hooked };
  out.phone.swipeDown = await swipe(cx, cy - 80, cx, cy + 80, 8);
  out.phone.swipeUp = await swipe(cx, cy + 80, cx, cy - 80, 8);
  out.phone.horiz = await swipe(cx - 80, cy, cx + 80, cy + 4, 8);
  out.phone.hold = await swipe(cx, cy - 40, cx, cy + 40, 4, 900);
  const n = logLen();
  await touch('touchStart', [[cx, cy]]); await touch('touchEnd', []);
  await sleep(500);
  const s = since(n);
  out.phone.tap = { up: count(s, UP), down: count(s, DOWN), clicks: count(s, /\x1b\[<0;\d+;\d+[Mm]/g),
    focused: await ph.p.evaluate(() => { const f = document.querySelector('#frames iframe');
      return document.activeElement === f && f.contentWindow.document.activeElement.tagName; }) };
  await ph.b.close();
  // ---- desktop: the PC wheel, the reference behaviour ----
  const dk = await open('desktop', { viewport: { width: 1280, height: 800 } });
  out.desktop = { hooked: dk.g.hooked };
  let m = logLen();
  await dk.p.mouse.move(dk.g.cx, dk.g.cy);
  await dk.p.mouse.wheel(0, -100);
  await sleep(500);
  out.desktop.wheelUp = count(since(m), UP);
  m = logLen();
  await dk.p.mouse.wheel(0, 100);
  await sleep(500);
  out.desktop.wheelDown = count(since(m), DOWN);
  await dk.b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""


class TestSwipeRealBrowser1287(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node, ttyd = shutil.which("node"), shutil.which("ttyd")
        pw = os.environ.get("WEBTERM_TEST_PLAYWRIGHT") or _find_node_playwright()
        chromium = _find_chromium()
        if not (node and ttyd and pw and chromium):
            raise unittest.SkipTest("node + playwright + chromium + ttyd not all available")
        d = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, str(d), True)
        (d / "index.html").write_text(_render(), encoding="utf-8")
        pwa.write_pwa_assets(d, "zbynek")
        (d / "cred").write_text("u:p\n", encoding="utf-8")
        (d / "driver.js").write_text(_DRIVER, encoding="utf-8")
        log = d / "app.log"
        tp, gp = _free_port(), _free_port()
        # a throwaway REAL ttyd (real xterm.js) on loopback; never a live service
        tt = subprocess.Popen([ttyd, "-p", str(tp), "-i", "127.0.0.1", "-b", "/t", "-a", "-W",
                               sys.executable, "-c", _APP, str(log)],
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
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium, base, str(log)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise AssertionError("browser driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_the_pc_wheel_reaches_the_app_as_sgr_wheel_reports(self):
        dk = self.out["desktop"]
        self.assertGreaterEqual(dk["wheelUp"], 1, dk)
        self.assertGreaterEqual(dk["wheelDown"], 1, dk)
        self.assertFalse(dk["hooked"])          # no swipe hook where a mouse can hover

    def test_a_phone_swipe_down_scrolls_back_like_the_wheel(self):
        ph = self.out["phone"]
        self.assertTrue(ph["hooked"])
        rows = 160 / ph["row"]                  # finger travel in terminal rows
        self.assertEqual(ph["swipeDown"]["down"], 0, ph)
        self.assertAlmostEqual(ph["swipeDown"]["up"], rows, delta=1.5, msg=ph)
        self.assertGreaterEqual(ph["swipeDown"]["up"], 3, ph)

    def test_a_phone_swipe_up_scrolls_forward(self):
        ph = self.out["phone"]
        self.assertEqual(ph["swipeUp"]["up"], 0, ph)
        self.assertAlmostEqual(ph["swipeUp"]["down"], 160 / ph["row"], delta=1.5, msg=ph)

    def test_horizontal_and_held_moves_send_no_wheel(self):
        for k in ("horiz", "hold"):
            self.assertEqual(self.out["phone"][k], {"up": 0, "down": 0}, k)

    def test_a_tap_still_focuses_and_clicks_without_a_wheel(self):
        tap = self.out["phone"]["tap"]
        self.assertEqual((tap["up"], tap["down"]), (0, 0), tap)
        self.assertGreaterEqual(tap["clicks"], 1, tap)
        self.assertEqual(tap["focused"], "TEXTAREA", tap)

    def test_zero_console_errors(self):
        self.assertEqual(self.out["consoleHits"], [])
