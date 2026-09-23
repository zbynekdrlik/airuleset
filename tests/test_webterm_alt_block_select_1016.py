"""#1016 -- Alt+drag in the webterm makes a Windows-Terminal-style BLOCK
(column) selection.

Owner report (13.9.2026): selecting an INDENTED block (a `/goal` line offset two
spaces) with Alt+drag -- the Windows Terminal habit -- does not work in the
webterm. Root cause (design comment, main): the fleet tmux runs `mouse on`
(#646), so xterm.js only FORCES its own selection under mouse-tracking when
`shiftKey` is set (`shouldForceSelection`) and only makes it a COLUMN block when
`altKey` is set (`_shouldColumnSelect`); so a plain Alt+drag goes to tmux (line
selection) and the real block gesture is Shift+Alt+drag (which on Windows is the
input-language toggle).

Fix (Approach 1, fix-forward, MINIMAL): the dash template attaches ONE capture-phase
`mousedown` listener per same-origin xterm iframe (the #613/#886 `window.term`
integration point) that re-dispatches a plain Alt+left mousedown as Shift+Alt, so
xterm forces its own COLUMN selection and drives the rest of the drag NATIVELY, and
sets `altClickMovesCursor = false`.

The v0.1.416 first cut (also mousedown-only) failed the owner's acceptance ("cez alt
sa prepne kurzor na krizik ale nic sa neda vyznacit"). ROOT CAUSE: the synthetic
mousedown carried no `detail` -- a `new MouseEvent` defaults it to 0, and xterm starts
a selection ONLY in its `1 === e.detail` single-click branch, so the old synthetic
reached xterm with the right modifiers yet NEVER started a selection. FIX = carry
`detail` (`ev.detail || 1`). Proven in a real browser that `detail` alone yields the
column block (a FORCED selection never enters xterm's tracking branch, so the plain
moves are handled by xterm's own selection drag and NOT forwarded to tmux). The
supervisor area review dropped the earlier whole-drag translation as unneeded
machinery (MVP).

Three tiers of coverage:

* STRUCTURAL locks (always run) -- the translator ships, is wired into the
  per-iframe poll beside attachClipboard/themeTerminal, runs in the CAPTURE phase,
  guards on `altKey && !shiftKey && button === 0`, re-dispatches with
  `shiftKey: true` + `altKey: true` + `detail`, ONLY hooks mousedown (no
  mousemove/mouseup -- xterm drives the drag once the selection started), is
  idempotent, and disables altClickMovesCursor.
* A BEHAVIOURAL node harness (runs when `node` is present) that drives the REAL
  extracted `attachBlockSelect` against a stub `term.element` (an EventTarget) and
  a stub `win.MouseEvent`, proving the mousedown TRANSLATION: an Alt+drag reaches
  xterm as a Shift+Alt (column) event with the original suppressed; a plain drag
  is untouched; an already-Shift+Alt drag is not double-processed;
  `altClickMovesCursor` is false; the attach is idempotent and console-clean. The
  flat EventTarget cannot model DOM tree-capture or tmux mouse-tracking routing --
  the end-to-end column-block OUTCOME is the real-browser tier's job.
* A REAL-BROWSER tier (runs where ttyd + tmux + node + a requireable `playwright`
  + a chromium headless-shell are present -- the controller; SKIPS elsewhere like
  the sibling `test_webterm_short_viewport_798.py`). It drives real Playwright
  mouse input against a real xterm under a loopback ttyd on an isolated tmux server
  with `mouse on`, and asserts the end-to-end column-block OUTCOME that the node
  harness cannot reach. The owner's own-browser confirmation after deploy remains
  the #1015 acceptance.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
from test_webterm import _extract_js_function  # noqa: E402


def _inv():
    return [{"id": "s1", "label": "dev1", "kind": "owner",
             "local": False, "host": "10.0.0.1", "user": "u1"}]


# ---------------------------------------------------------------------------
# Node harness: run the REAL extracted attachBlockSelect against a stub term
# whose `element` is a node EventTarget and whose `win.MouseEvent` is a shim.
# A stand-in xterm listener (registered AFTER attachBlockSelect) records exactly
# what xterm would receive: node's flat EventTarget fires listeners in
# REGISTRATION order and honours stopImmediatePropagation, so the translator
# (registered first) suppresses the original at the recorder (registered second)
# and only the re-dispatched clone reaches it. NB this models REGISTRATION-order
# suppression, NOT a real DOM tree's capture-before-descendant phase — the actual
# capture flag is a FLAT-model no-op here, so the `, true` capture requirement is
# locked STRUCTURALLY by test_capture_phase_listener, not behaviourally; the
# tree-capture + tmux-tracking OUTCOME is the real-browser tier below.
# ---------------------------------------------------------------------------
_BLOCK_HARNESS = r"""
%(attach)s
// --- stub realm ------------------------------------------------------------
const consoleHits = [];
for (const m of ['error', 'warn', 'log', 'info', 'debug']) {
  console[m] = (...a) => { consoleHits.push(m + ':' + a.join(' ')); };
}
class MouseEvt extends Event {
  constructor(type, init) {
    init = init || {};
    super(type, { bubbles: !!init.bubbles, cancelable: !!init.cancelable });
    const num = ['button', 'buttons', 'clientX', 'clientY', 'screenX', 'screenY'];
    const flag = ['altKey', 'shiftKey', 'ctrlKey', 'metaKey'];
    for (const k of num) this[k] = (k in init) ? init[k] : 0;
    for (const k of flag) this[k] = (k in init) ? init[k] : false;
    this.view = ('view' in init) ? init.view : null;
  }
}
const el = new EventTarget();
const term = { element: el, options: { altClickMovesCursor: true, macOptionClickForcesSelection: false } };
const win = { MouseEvent: MouseEvt, term: term };

let attachError = null;
try {
  attachBlockSelect(win);
  attachBlockSelect(win);   // idempotent: second call must be a no-op
} catch (e) { attachError = String(e && e.message || e); }

// stand-in for xterm's OWN selection listener, registered AFTER the translator
// (models capture-phase-before-xterm: the translator's stopImmediatePropagation
// then hides the original from this listener, leaving only the re-dispatch).
const recorded = [];
el.addEventListener('mousedown', (ev) => {
  recorded.push({ altKey: ev.altKey, shiftKey: ev.shiftKey, button: ev.button });
});

function fire(init) {
  recorded.length = 0;
  el.dispatchEvent(new MouseEvt('mousedown',
    Object.assign({ bubbles: true, cancelable: true, button: 0, buttons: 1 }, init)));
  return recorded.slice();
}

const out = {
  attachError: attachError,
  altClickMovesCursor: term.options.altClickMovesCursor,
  macOptionClickForcesSelection: term.options.macOptionClickForcesSelection,
  altDrag: fire({ altKey: true, shiftKey: false }),         // plain Alt+left
  plainDrag: fire({ altKey: false, shiftKey: false }),      // ordinary drag
  shiftAltDrag: fire({ altKey: true, shiftKey: true }),     // already the block gesture
  altRight: fire({ altKey: true, shiftKey: false, button: 2 }),  // Alt + right button
  consoleHits: consoleHits,
};
process.stdout.write(JSON.stringify(out));
"""


def _run_block_harness(html):
    attach = _extract_js_function(html, "attachBlockSelect")
    src = _BLOCK_HARNESS % {"attach": attach}
    d = tempfile.mkdtemp()
    hp = Path(d) / "blockharness.js"
    hp.write_text(src, encoding="utf-8")
    r = subprocess.run(["node", str(hp)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
    return json.loads(r.stdout.strip().splitlines()[-1])



class TestAltBlockSelectStructure1016(unittest.TestCase):
    """The translator ships, is wired into the per-iframe poll, and carries the
    exact gesture/guard/capture semantics the design specifies."""

    def setUp(self):
        self.html = w.render_dashboard_html(_inv(), ttyd_base="/t")

    def test_translator_function_ships(self):
        self.assertIn("function attachBlockSelect(", self.html)

    def test_wired_into_the_per_iframe_poll(self):
        # attached in applyFixedGrid's poll, beside attachClipboard -- the same
        # same-origin window.term hook #643/#671/#886 use.
        poll = _extract_js_function(self.html, "applyFixedGrid")
        self.assertIn("attachBlockSelect(win)", poll)
        self.assertIn("attachClipboard(win)", poll)

    def test_disables_alt_click_moves_cursor(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("altClickMovesCursor", fn)
        self.assertRegex(fn, r"altClickMovesCursor\s*=\s*false")

    def test_forces_selection_on_macos(self):
        # on macOS xterm's shouldForceSelection keys on altKey &&
        # macOptionClickForcesSelection, NOT shiftKey -- so this must be enabled or
        # the synthetic Shift+Alt forces nothing on a Mac (the template is shared
        # across every lane, not only the Windows owner).
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertRegex(fn, r"macOptionClickForcesSelection\s*=\s*true")

    def test_only_mousedown_is_hooked(self):
        # MINIMAL fix (supervisor area review): only the mousedown is translated.
        # xterm forces + drives the whole drag natively once the selection starts,
        # so no mousemove/mouseup re-dispatch is needed -- a lock so a future edit
        # cannot silently reintroduce the dropped whole-drag machinery.
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertNotIn("'mousemove'", fn)
        self.assertNotIn("'mouseup'", fn)

    def test_synthetic_mousedown_preserves_detail(self):
        # the TRUE #1016 root cause: xterm starts a selection only in its
        # `1 === detail` single-click branch, and `new MouseEvent` defaults detail
        # to 0 -- so the synthetic MUST carry the click's detail (`ev.detail || 1`).
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertRegex(fn, r"detail\s*:\s*ev\.detail")

    def test_capture_phase_listener(self):
        # MUST be capture -- xterm's own selection listener lives on a descendant
        # and would consume the plain Alt+drag first in the bubble phase.
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("'mousedown'", fn)
        self.assertRegex(fn, r"addEventListener\(\s*'mousedown'[\s\S]*?,\s*true\s*\)")

    def test_gesture_guard_plain_alt_left_only(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("altKey", fn)
        self.assertIn("shiftKey", fn)
        self.assertIn("button", fn)

    def test_redispatch_forces_shift_and_alt(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertRegex(fn, r"shiftKey\s*:\s*true")
        self.assertRegex(fn, r"altKey\s*:\s*true")
        # the original is suppressed so xterm never sees the plain Alt+drag.
        self.assertIn("preventDefault", fn)
        self.assertRegex(fn, r"stop(Immediate)?Propagation")

    def test_idempotent_guard_flag(self):
        fn = _extract_js_function(self.html, "attachBlockSelect")
        self.assertIn("__wtBlockSel", fn)


class TestAltBlockSelectBehaviour1016(unittest.TestCase):
    """Drive the REAL extracted translator in node against a stub term.element."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node not available")
        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        cls.out = _run_block_harness(html)

    def test_attach_never_raises(self):
        self.assertIsNone(self.out["attachError"])

    def test_alt_click_moves_cursor_false_after_attach(self):
        self.assertIs(self.out["altClickMovesCursor"], False)

    def test_mac_option_click_forces_selection_true_after_attach(self):
        self.assertIs(self.out["macOptionClickForcesSelection"], True)

    def test_alt_drag_becomes_shift_alt_block_original_suppressed(self):
        # xterm receives exactly ONE event: the translated Shift+Alt (column
        # block). The original plain Alt+drag is stopped, so it never reaches
        # xterm -> tmux (which would make an ordinary line selection).
        self.assertEqual(self.out["altDrag"],
                         [{"altKey": True, "shiftKey": True, "button": 0}])

    def test_plain_drag_untouched(self):
        # no altKey -> today's behaviour, exactly one unchanged event.
        self.assertEqual(self.out["plainDrag"],
                         [{"altKey": False, "shiftKey": False, "button": 0}])

    def test_shift_alt_drag_not_double_processed(self):
        # already the block gesture -> pass through once, never re-dispatched.
        self.assertEqual(self.out["shiftAltDrag"],
                         [{"altKey": True, "shiftKey": True, "button": 0}])

    def test_alt_right_button_untouched(self):
        # only the left button (0) is a selection drag; right-button Alt is not.
        self.assertEqual(self.out["altRight"],
                         [{"altKey": True, "shiftKey": False, "button": 2}])

    def test_console_stays_empty(self):
        # the page must stay console-clean (browser-console-zero-errors).
        self.assertEqual(self.out["consoleHits"], [])


# ---------------------------------------------------------------------------
# REAL-BROWSER tier (#1016 fix-forward): drives the SHIPPED attachBlockSelect
# against a REAL xterm served by a loopback ttyd on an ISOLATED tmux server
# running `mouse on` (the fleet default), through real Playwright mouse input.
# This is the tier the node harness could NOT model (mouse-TRACKING event
# routing): under tmux `mouse on` a plain Alt+drag is forwarded to tmux and the
# xterm selection never forms unless the synthetic mousedown carries `detail`
# (xterm's `1===detail` single-click is what starts the selection; a 0 default --
# the pre-fix bug -- never does; once forced, xterm drives the drag natively).
# It RUNS wherever ttyd + tmux + node + a requireable `playwright` + a chromium
# (headless-shell) binary are present (the controller); elsewhere it SKIPS, like
# the sibling python-playwright tier in test_webterm_short_viewport_798.py.
# NEVER touches a live webterm: own -S tmux socket + loopback ttyd on an
# ephemeral port; every process is torn down via addClassCleanup.
# ---------------------------------------------------------------------------
_REAL_BROWSER_DRIVER = r"""
const [,, PW, CH, URL, ATTACH_FILE, MODE] = process.argv;
const fs = require('fs');
const attachSrc = fs.readFileSync(ATTACH_FILE, 'utf8');
(async () => {
  const { chromium } = require(PW);
  const b = await chromium.launch({ executablePath: CH, headless: true, args: ['--no-sandbox'] });
  const consoleHits = [];
  const page = await b.newPage({ viewport: { width: 1100, height: 640 } });
  page.on('console', m => { const t = m.type(); if (t === 'error' || t === 'warning') consoleHits.push(t + ':' + m.text()); });
  page.on('pageerror', e => consoleHits.push('pageerror:' + e.message));
  await page.goto(URL, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const t = window.term;
    if (!t || !t.element) return false;
    const buf = t.buffer && t.buffer.active; if (!buf) return false;
    const l0 = buf.getLine(0); if (!l0) return false;
    return l0.translateToString(true).indexOf('HELLOWORLD') !== -1;
  }, { timeout: 20000 });
  await page.addScriptTag({ content: attachSrc + '\n;window.__wtAttach = attachBlockSelect;' });
  await page.evaluate(() => window.__wtAttach(window));
  const geo = await page.evaluate(() => {
    const t = window.term;
    const screen = t.element.querySelector('.xterm-screen') || t.element;
    const r = screen.getBoundingClientRect();
    return { left: r.left, top: r.top, cols: t.cols, rows: t.rows, w: r.width, h: r.height };
  });
  const cw = geo.w / geo.cols, chh = geo.h / geo.rows;
  const X = c => Math.round(geo.left + (c + 0.5) * cw);
  const Y = r => Math.round(geo.top + (r + 0.5) * chh);
  // indent is 4 spaces -> HELLOWORLD occupies cols 4..13; drag a rectangle over
  // rows 0..1, cols 6..9 (a sub-column that starts PAST the indent).
  const startC = 6, endC = 10, r0 = 0, r1 = 1;
  if (MODE === 'alt') await page.keyboard.down('Alt');
  await page.mouse.move(X(startC), Y(r0));
  await page.mouse.down();
  await page.mouse.move(X(startC + 1), Y(r0), { steps: 2 });
  await page.mouse.move(X(endC), Y(r0), { steps: 3 });
  await page.mouse.move(X(endC), Y(r1), { steps: 3 });
  await page.mouse.up();
  if (MODE === 'alt') await page.keyboard.up('Alt');
  await page.waitForTimeout(150);
  const sel = await page.evaluate(() => (window.term.getSelection && window.term.getSelection()) || '');
  await b.close();
  process.stdout.write(JSON.stringify({ selection: sel, consoleHits }));
})().catch(e => { console.error('DRIVER-ERR', e && e.message || e); process.exit(1); });
"""


def _find_node_playwright():
    """First requireable `playwright` package path, or None (npx cache / global)."""
    node = shutil.which("node")
    if not node:
        return None
    candidates = []
    candidates += sorted(glob.glob(os.path.expanduser("~/.npm/_npx/*/node_modules/playwright")))
    candidates += sorted(glob.glob(os.path.expanduser("~/node_modules/playwright")))
    for path in candidates:
        try:
            r = subprocess.run([node, "-e", "require(process.argv[1])", path],
                               capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return path
        except Exception as e:
            print("playwright require probe failed for %s: %s" % (path, e), file=sys.stderr)
            continue
    # a globally resolvable install
    try:
        r = subprocess.run([node, "-e", "console.log(require.resolve('playwright'))"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return "playwright"
    except Exception as e:
        print("playwright global-resolve probe failed:", e, file=sys.stderr)
    return None


def _find_chromium():
    """A launchable chromium binary; prefer headless-shell (fewest shared-lib deps)."""
    pats = [
        "~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
        "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    ]
    for pat in pats:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_port(port, timeout=15.0):
    """Poll 127.0.0.1:port until it accepts a connection (ttyd ready) or timeout.
    Replaces a fixed sleep (no-timeout-band-aids): the driver's own
    waitForFunction(HELLOWORLD) is the content gate, this only gates connect."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.1)
    return False


class TestAltBlockBrowser1016(unittest.TestCase):
    """Alt+drag over an indented block yields a COLUMN selection in a real xterm
    under tmux `mouse on` -- the outcome the node harness cannot prove."""

    @classmethod
    def setUpClass(cls):
        for tool in ("node", "ttyd", "tmux"):
            if shutil.which(tool) is None:
                raise unittest.SkipTest("%s not available" % tool)
        cls.node = shutil.which("node")
        cls.pw = _find_node_playwright()
        cls.chromium = _find_chromium()
        if not cls.pw:
            raise unittest.SkipTest("no requireable playwright package")
        if not cls.chromium:
            raise unittest.SkipTest("no chromium binary")

        html = w.render_dashboard_html(_inv(), ttyd_base="/t")
        cls.attach = _extract_js_function(html, "attachBlockSelect")

        cls.procs = []
        cls.d = tempfile.mkdtemp()
        # short unix-socket path (tmux caps ~104 bytes) -- never under the long
        # per-run tempdir. Register cleanup NOW (addClassCleanup fires even if
        # setUpClass raises mid-way) so no process/socket/tempdir ever leaks
        # (cleanup-after-yourself, #925 -- kill-server does NOT unlink the socket).
        cls.sock = "/tmp/wt1016-%s.sock" % uuid.uuid4().hex[:8]
        cls.addClassCleanup(cls._cleanup_runtime)

        cls.driver = Path(cls.d) / "driver.js"
        cls.driver.write_text(_REAL_BROWSER_DRIVER, encoding="utf-8")
        cls.attach_file = Path(cls.d) / "attach.js"
        cls.attach_file.write_text(cls.attach, encoding="utf-8")

        cls._alt = cls._run_mode("alt")
        cls._plain = cls._run_mode("plain")

    @classmethod
    def _run_mode(cls, mode):
        subprocess.run(["tmux", "-S", cls.sock, "kill-server"],
                       capture_output=True, text=True)
        session_cmd = ('sh -c "printf \\"    HELLOWORLD\\n    HELLOWORLD\\n\\"; '
                       'exec sleep 100000"')
        r = subprocess.run(["tmux", "-S", cls.sock, "new-session", "-d", "-s", "bs",
                            "-x", "176", "-y", "50", session_cmd],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest("cannot start isolated tmux: %s" % r.stderr)
        subprocess.run(["tmux", "-S", cls.sock, "set", "-g", "mouse", "on"],
                       capture_output=True, text=True)
        port = _free_port()
        ttyd = subprocess.Popen(["ttyd", "-i", "127.0.0.1", "-p", str(port), "-W",
                                 "tmux", "-S", cls.sock, "attach", "-t", "bs"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.procs.append(ttyd)
        if not _wait_port(port):
            raise unittest.SkipTest("ttyd did not become reachable on 127.0.0.1:%d" % port)
        try:
            r = subprocess.run([cls.node, str(cls.driver), cls.pw, cls.chromium,
                                "http://127.0.0.1:%d/" % port, str(cls.attach_file), mode],
                               capture_output=True, text=True, timeout=120)
        finally:
            ttyd.terminate()
            try:
                ttyd.wait(timeout=5)
            except Exception as e:
                print("ttyd terminate wait failed, killing:", e, file=sys.stderr)
                ttyd.kill()
            subprocess.run(["tmux", "-S", cls.sock, "kill-server"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError("real-browser driver (%s) failed:\n%s\n%s"
                                 % (mode, r.stdout, r.stderr))
        return json.loads(r.stdout.strip().splitlines()[-1])

    @classmethod
    def _cleanup_runtime(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill()
            except Exception as e:
                print("teardown proc kill failed:", e, file=sys.stderr)
        try:
            subprocess.run(["tmux", "-S", cls.sock, "kill-server"],
                           capture_output=True, text=True)
        except Exception as e:
            print("teardown tmux kill-server failed:", e, file=sys.stderr)
        # kill-server does NOT remove the socket file -- unlink it so /tmp never
        # accumulates dead wt1016-*.sock across CI runs (#925).
        if os.path.exists(cls.sock):
            try:
                os.unlink(cls.sock)
            except OSError as e:
                print("teardown socket unlink failed:", e, file=sys.stderr)
        d = getattr(cls, "d", None)
        if d:
            shutil.rmtree(d, ignore_errors=True)

    @staticmethod
    def _non_gl(hits):
        # headless swiftshader emits GPU-driver PERFORMANCE warnings ("GPU stall
        # due to ReadPixels") that come from the WebGL renderer, not our page --
        # a real GPU (the owner's browser) never emits them. Filter ONLY those.
        return [h for h in hits
                if "GL Driver Message" not in h and "GPU stall" not in h]

    def test_alt_drag_makes_a_column_block(self):
        sel = self._alt["selection"]
        self.assertTrue(sel, "Alt+drag produced NO selection (the #1016 bug: the "
                             "gesture never forms a column block under tmux mouse-on)")
        # spans BOTH indented rows -> a rectangle, not a single line
        self.assertIn("\n", sel, "selection is a single line, not a column block")
        lines = [ln for ln in sel.replace("\r\n", "\n").split("\n") if ln != ""]
        self.assertGreaterEqual(len(lines), 2)
        for ln in lines:
            # the drag started PAST the 4-space indent -> no leading columns
            self.assertFalse(ln.startswith(" "),
                             "selection kept leading indent columns: %r" % sel)
            # a sub-column, never the whole line
            self.assertNotIn("HELLOWORLD", ln)
        # the two identical source rows yield two identical column slices
        self.assertEqual(lines[0], lines[1],
                         "column block rows differ (not a rectangle): %r" % sel)

    def test_plain_drag_still_goes_to_tmux(self):
        # no Alt -> today's behaviour: the drag goes to tmux, xterm holds no
        # native selection.
        self.assertEqual(self._plain["selection"].strip(), "")

    def test_console_stays_clean(self):
        self.assertEqual(self._non_gl(self._alt["consoleHits"]), [])
        self.assertEqual(self._non_gl(self._plain["consoleHits"]), [])


if __name__ == "__main__":
    unittest.main()
