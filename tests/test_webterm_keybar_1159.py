"""#1159 -- a touch-only on-screen key bar for the webterm.

Owner (26.9.2026): on a phone the webterm has no Ctrl chord, no arrows and no
Esc, so `Ctrl+B w` / `Ctrl+B s` (switch tmux window / session) and `Up` (prompt
history) are impossible. The main's design (Approach 1): a compact bottom bar in
the PARENT dashboard, shown only on a coarse pointer with no hover, whose
buttons feed exact byte sequences into the ACTIVE tab's xterm through the
same-origin `window.term` bridge, "as typed" -- `term.input(data, true)` (ttyd
1.7.7's xterm) or the core `triggerDataEvent(data, true)` (ttyd 1.7.4's xterm has
no public `input`) -- NEVER `term.paste()` (bracketed paste would hide the tmux
prefix). Arrows follow xterm's application-cursor mode. Focus returns to the
terminal after each press (the #661 `focusTerminal`).

The bar's CSS + markup + script live in the sibling pure-constant leaf
`cli_webterm_keybar.py` (the template and cli_webterm.py are at their size
ceilings), injected by two new sentinels in the same single substitution pass.

Three tiers:
* STRUCTURAL -- the leaf is pure, the markup/order/labels ship once, the CSS is
  gated by `(pointer: coarse) and (hover: none)`, the byte table literals are
  exact, the send path never pastes, placement is after the main script.
* NODE harness -- runs the REAL shipped keybar script in a vm context against
  stub terms: exact bytes per key in both cursor modes, ACTIVE tab only, the
  `input` path and the 1.7.4 core fallback, focus returns.
* REAL BROWSER (node playwright + chromium headless-shell; SKIPS where absent) --
  the full rendered dashboard served on 127.0.0.1 with a same-origin stub ttyd
  page per tab: phone viewport + hasTouch/isMobile shows the bar with 44 px
  targets and exact bytes to the ACTIVE tab only, focus returns, zero console
  errors/warnings; a desktop viewport shows no bar and no layout change.
"""
import ast
import functools
import glob
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_webterm as w  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# The owner-facing key order + labels (design issuecomment-5844841632).
KEY_ORDER = ["esc", "tab", "up", "down", "left", "right", "ctrlc",
             "sessions", "windows", "prevwin", "nextwin", "scroll"]
LABELS = ["Esc", "Tab", "\u2191", "\u2193", "\u2190", "\u2192", "^C",
          "sessions", "windows", "\u25c0 win", "win \u25b6", "scroll"]
# Exact bytes. Arrows: (normal cursor mode CSI, application cursor mode SS3).
ARROWS = {"up": ("\x1b[A", "\x1bOA"), "down": ("\x1b[B", "\x1bOB"),
          "right": ("\x1b[C", "\x1bOC"), "left": ("\x1b[D", "\x1bOD")}
PLAIN = {"esc": "\x1b", "tab": "\t", "ctrlc": "\x03",
         "sessions": "\x02s", "windows": "\x02w", "prevwin": "\x02p",
         "nextwin": "\x02n", "scroll": "\x02["}


def expected(k, app_cursor=False):
    if k in ARROWS:
        return ARROWS[k][1 if app_cursor else 0]
    return PLAIN[k]


def _inv():
    return [{"id": i, "label": i, "kind": "owner", "local": False,
             "host": "10.0.0.%d" % n, "user": "u"}
            for n, i in enumerate(("s1", "s2", "legacy"), start=1)]


def _render():
    return w.render_dashboard_html(_inv(), ttyd_base="/t")


def _keybar_script(html):
    """The shipped key bar <script> body (the first script after #keybar)."""
    start = html.index('<div id="keybar"')
    s = html.index("<script>", start) + len("<script>")
    return html[s:html.index("</script>", s)]


def _css_blocks(css):
    """(outside-media CSS, {media-query: inner CSS}) -- brace-matched."""
    media, outside, i = {}, [], 0
    while True:
        m = re.compile(r"@media\s*([^{]+)\{").search(css, i)
        if not m:
            outside.append(css[i:])
            break
        outside.append(css[i:m.start()])
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        media[m.group(1).strip()] = css[m.end():j - 1]
        i = j
    return "".join(outside), media


class TestKeybarStructure1159(unittest.TestCase):

    def setUp(self):
        self.html = _render()

    def test_keybar_module_is_a_pure_constant_leaf(self):
        src = (REPO / "cli_webterm_keybar.py").read_text(encoding="utf-8")
        banned = (ast.Import, ast.ImportFrom, ast.FunctionDef,
                  ast.AsyncFunctionDef, ast.ClassDef)
        self.assertEqual([type(n).__name__ for n in ast.walk(ast.parse(src))
                          if isinstance(n, banned)], [])
        import cli_webterm_keybar as kb
        self.assertIn(kb.KEYBAR_CSS, self.html)
        self.assertIn(kb.KEYBAR_HTML, self.html)

    def test_bar_markup_ships_once_with_every_key_in_order(self):
        self.assertEqual(self.html.count('id="keybar"'), 1)
        bar = self.html[self.html.index('<div id="keybar"'):]
        bar = bar[:bar.index("</div>")]
        keys = re.findall(r'<button type="button" data-k="([a-z]+)"[^>]*>', bar)
        self.assertEqual(keys, KEY_ORDER)
        import html as _h
        labels = [_h.unescape(x) for x in
                  re.findall(r'data-k="[a-z]+"[^>]*>([^<]*)</button>', bar)]
        self.assertEqual(labels, LABELS)

    def test_glyph_only_buttons_carry_an_invisible_aria_label(self):
        bar = self.html[self.html.index('<div id="keybar"'):]
        bar = bar[:bar.index("</div>")]
        named = dict(re.findall(r'data-k="([a-z]+)" aria-label="([^"]+)"', bar))
        self.assertEqual(named, {"up": "up", "down": "down", "left": "left",
                                 "right": "right", "prevwin": "previous window",
                                 "nextwin": "next window"})

    def test_no_explanatory_text_in_the_bar(self):
        # #671 ruling: no hints. The bar holds ONLY its buttons (no title=
        # tooltips, no free text between them).
        bar = self.html[self.html.index('<div id="keybar"'):]
        bar = bar[:bar.index("</div>") + len("</div>")]
        self.assertNotIn("title=", bar)
        stripped = re.sub(r"<button[^>]*>[^<]*</button>", "", bar)
        stripped = re.sub(r"<span class=\"kb-gap\"></span>", "", stripped)
        self.assertEqual(re.sub(r"<[^>]+>", "", stripped).strip(), "")

    def test_css_is_gated_by_coarse_pointer_and_no_hover(self):
        import cli_webterm_keybar as kb
        outside, media = _css_blocks(kb.KEYBAR_CSS)
        self.assertRegex(outside, r"#keybar\s*\{\s*display:\s*none;")
        self.assertNotRegex(outside, r"display:\s*flex")
        gate = "(pointer: coarse) and (hover: none)"
        self.assertEqual(list(media), [gate])
        inner = media[gate]
        self.assertRegex(inner, r"#keybar\s*\{[^}]*display:\s*flex")
        self.assertRegex(inner, r"#keybar\s*\{[^}]*overflow-x:\s*auto")
        self.assertRegex(inner, r"#keybar button\s*\{[^}]*min-width:\s*44px")
        self.assertRegex(inner, r"#keybar button\s*\{[^}]*height:\s*44px")

    def test_css_lands_in_the_head_style(self):
        import cli_webterm_keybar as kb
        head = self.html[:self.html.index("</head>")]
        style = head[head.index("<style>"):head.index("</style>")]
        self.assertIn(kb.KEYBAR_CSS, style)

    def test_markup_and_script_follow_the_main_script(self):
        # the bar's script calls focusTerminal / reads made+current from the
        # main script, so it must come after it.
        self.assertLess(self.html.index("function focusTerminal("),
                        self.html.index('<div id="keybar"'))
        self.assertLess(self.html.index('<div id="keybar"'),
                        self.html.index("function keybarPress("))

    def test_byte_table_literals_are_exact(self):
        js = _keybar_script(self.html)
        for k, v in PLAIN.items():
            lit = "".join("\\x%02x" % ord(c) if ord(c) < 0x20 else c for c in v)
            self.assertRegex(js, r"\b%s: '%s'" % (k, re.escape(lit)), k)
        for k, (csi, _ss3) in ARROWS.items():
            self.assertRegex(js, r"\b%s: '%s'" % (k, csi[-1]), k)

    def test_send_path_types_never_pastes(self):
        js = _keybar_script(self.html)
        self.assertNotIn("paste(", js)
        self.assertIn(".input(data, true)", js)
        self.assertIn("triggerDataEvent(data, true)", js)
        self.assertIn("applicationCursorKeysMode", js)
        press = js[js.index("function keybarPress("):]
        self.assertIn("made[current]", press)
        self.assertIn("focusTerminal(f, current)", press)

    def test_viewport_meta_is_untouched(self):
        # The keyboard overlay is handled by keybarFitViewport, not by the
        # `interactive-widget` viewport key: iOS Safari does not support it and
        # WebKit logs an unrecognized viewport key as a console error.
        self.assertIn('<meta name="viewport" content="width=device-width, '
                      'initial-scale=1">', self.html)
        self.assertNotIn("interactive-widget", self.html)

    def test_viewport_fit_follows_the_visual_viewport_resize(self):
        js = _keybar_script(self.html)
        self.assertIn("visualViewport.addEventListener('resize', keybarFitViewport)", js)


# ---------------------------------------------------------------------------
# NODE harness: the REAL shipped keybar script in a vm context, stub terms.
# ---------------------------------------------------------------------------
_NODE_HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const KEYS = JSON.parse(process.argv[3]);
function mkTerm(kind) {
  const t = { sent: [], pasted: [], modes: { applicationCursorKeysMode: false },
              paste(d) { this.pasted.push(d); } };
  if (kind === 'input') t.input = function (d, u) { t.sent.push([d, u, 'input']); };
  else t._core = { coreService: { triggerDataEvent(d, u) { t.sent.push([d, u, 'core']); } } };
  return t;
}
const terms = [mkTerm('input'), mkTerm('input'), mkTerm('core')];
const focus = [];
const ctx = {
  made: { 0: { contentWindow: { term: terms[0] } }, 1: { contentWindow: { term: terms[1] } },
          2: { contentWindow: { term: terms[2] } } },
  current: 0,
  focusTerminal: (f, idx) => focus.push([f === ctx.made[idx], idx]),
  document: { getElementById: () => null },
  console,
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
const out = { normal: {}, app: {} };
for (const k of KEYS) { terms[0].sent = []; ctx.keybarPress(k); out.normal[k] = terms[0].sent; }
terms[0].modes.applicationCursorKeysMode = true;
for (const k of KEYS) { terms[0].sent = []; ctx.keybarPress(k); out.app[k] = terms[0].sent; }
terms[0].sent = [];
ctx.current = 1; ctx.keybarPress('sessions');
out.activeOnly = terms.map(t => t.sent.slice());   // snapshot: the next press appends
ctx.current = 2; ctx.keybarPress('windows');
out.core = terms[2].sent;
out.pasted = terms.map(t => t.pasted.length);
out.focus = focus;
// keybarFitViewport: [pointerCoarseNoHover, vv (or null), innerHeight] -> body height
out.fit = [];
for (const [touch, vv, ih] of [
    [true, { height: 400.4, scale: 1 }, 800],   // keyboard up -> page = visual viewport
    [true, { height: 800, scale: 1 }, 800],     // keyboard down -> natural height
    [true, { height: 400, scale: 2 }, 800],     // pinch-zoom, not a keyboard -> untouched
    [false, { height: 400, scale: 1 }, 800],    // desktop -> untouched
    [true, null, 800]]) {                       // no visualViewport -> untouched
  ctx.window = { visualViewport: vv, innerHeight: ih, matchMedia: (q) => ({ matches: touch && q === '(pointer: coarse) and (hover: none)' }) };
  ctx.document.body = { style: { height: 'stale' } };
  ctx.keybarFitViewport();
  out.fit.push(ctx.document.body.style.height);
}
process.stdout.write(JSON.stringify(out));
"""


class TestKeybarNodeHarness1159(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not available")
        d = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, d, True)
        (Path(d) / "h.js").write_text(_NODE_HARNESS, encoding="utf-8")
        (Path(d) / "kb.js").write_text(_keybar_script(_render()), encoding="utf-8")
        r = subprocess.run([node, str(Path(d) / "h.js"), str(Path(d) / "kb.js"),
                            json.dumps(KEY_ORDER)],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node harness failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout)

    def test_exact_bytes_normal_cursor_mode(self):
        for k in KEY_ORDER:
            self.assertEqual(self.out["normal"][k], [[expected(k), True, "input"]], k)

    def test_arrows_follow_application_cursor_mode(self):
        for k in KEY_ORDER:
            self.assertEqual(self.out["app"][k], [[expected(k, True), True, "input"]], k)

    def test_only_the_active_tab_receives(self):
        self.assertEqual(self.out["activeOnly"], [[], [["\x02s", True, "input"]], []])

    def test_ttyd_174_core_fallback_types_as_user_input(self):
        self.assertEqual(self.out["core"], [["\x02w", True, "core"]])

    def test_never_pastes(self):
        self.assertEqual(self.out["pasted"], [0, 0, 0])

    def test_page_follows_the_visual_viewport_only_under_a_phone_keyboard(self):
        self.assertEqual(self.out["fit"], ["400px", "", "", "", "stale"])

    def test_focus_returns_to_the_active_terminal_after_every_press(self):
        focus = self.out["focus"]
        self.assertEqual(len(focus), 2 * len(KEY_ORDER) + 2)
        self.assertTrue(all(ok for ok, _ in focus))
        self.assertEqual([i for _, i in focus[-2:]], [1, 2])


# ---------------------------------------------------------------------------
# REAL BROWSER: the full rendered dashboard + a same-origin stub ttyd page.
# ---------------------------------------------------------------------------
_STUB_TTYD = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>stub ttyd</title></head>
<body><textarea class="xterm-helper-textarea" aria-label="t"></textarea>
<script>
const arg = new URLSearchParams(location.search).get('arg');
window.__sent = []; window.__focus = 0; window.__pasted = 0; window.__blur = 0;
const ta = document.querySelector('textarea');
ta.addEventListener('blur', () => { window.__blur++; });
const t = { options: {}, modes: { applicationCursorKeysMode: false },
  resize() {}, paste() { window.__pasted++; },
  focus() { window.__focus++; ta.focus(); } };
if (arg === 'legacy') {   // ttyd 1.7.4: no public input(), core path only
  t._core = { coreService: { triggerDataEvent(d, u) { window.__sent.push([d, u, 'core']); } } };
} else {
  t.input = function (d, u) { window.__sent.push([d, u, 'input']); };
}
window.term = t;
</script></body></html>
"""

_BROWSER_DRIVER = r"""
const [,, PW, CH, BASE, KEYS_JSON] = process.argv;
const KEYS = JSON.parse(KEYS_JSON);
(async () => {
  const { chromium } = require(PW);
  const b = await chromium.launch({ executablePath: CH, headless: true, args: ['--no-sandbox'] });
  const out = { consoleHits: [] };
  const watch = (page, tag) => {
    page.on('console', m => { const t = m.type(); if (t === 'error' || t === 'warning') out.consoleHits.push(tag + ':' + t + ':' + m.text()); });
    page.on('pageerror', e => out.consoleHits.push(tag + ':pageerror:' + e.message));
  };
  const ready = (page) => page.waitForFunction(() => {
    const fs = document.querySelectorAll('#frames iframe');
    if (fs.length !== 3) return false;
    for (const f of fs) { try { if (!f.contentWindow.term) return false; } catch (e) { return false; } }
    return true;
  }, null, { timeout: 20000 });
  const layout = (page) => page.evaluate(() => {
    const bar = document.getElementById('keybar');
    const cs = getComputedStyle(bar), r = bar.getBoundingClientRect();
    return { display: cs.display, overflowX: cs.overflowX, top: r.top, bottom: r.bottom, height: r.height,
      vh: innerHeight, vw: innerWidth, scrollW: bar.scrollWidth, clientW: bar.clientWidth,
      docScrollW: document.documentElement.scrollWidth, text: bar.innerText,
      btns: [...bar.querySelectorAll('button')].map(x => { const q = x.getBoundingClientRect(); return { k: x.dataset.k, w: q.width, h: q.height }; }),
      framesBottom: document.getElementById('frames').getBoundingClientRect().bottom,
      coarse: matchMedia('(pointer: coarse)').matches, hoverNone: matchMedia('(hover: none)').matches };
  });
  // ---- phone ----
  const phone = await b.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  const p = await phone.newPage(); watch(p, 'phone');
  await p.goto(BASE + '/', { waitUntil: 'load' });
  await ready(p);
  out.phone = await layout(p);
  const frameState = (i) => p.evaluate((i) => {
    const f = document.querySelectorAll('#frames iframe')[i], fw = f.contentWindow;
    return { sent: fw.__sent.slice(), focus: fw.__focus, pasted: fw.__pasted, blur: fw.__blur,
             parentActive: document.activeElement === f,
             innerActive: fw.document.activeElement && fw.document.activeElement.tagName };
  }, i);
  const tap = async (k) => {
    const sel = '#keybar button[data-k="' + k + '"]';
    await p.locator(sel).scrollIntoViewIfNeeded();
    await p.tap(sel);
  };
  out.normal = {}; out.focusAfter = {};
  const blur0 = (await frameState(0)).blur;
  for (const k of KEYS) {
    const before = await frameState(0);
    await tap(k);
    const after = await frameState(0);
    out.normal[k] = after.sent.slice(before.sent.length);
    out.focusAfter[k] = { refocused: after.focus > before.focus, parentActive: after.parentActive, innerActive: after.innerActive };
  }
  out.blurDuringKeys = (await frameState(0)).blur - blur0;   // a tap must never blur the terminal
  await p.evaluate(() => { document.querySelectorAll('#frames iframe')[0].contentWindow.term.modes.applicationCursorKeysMode = true; });
  out.app = {};
  for (const k of ['up', 'down', 'left', 'right']) {
    const before = (await frameState(0)).sent.length;
    await tap(k);
    out.app[k] = (await frameState(0)).sent.slice(before);
  }
  await p.tap('.tab[data-idx="1"]');
  const counts = async () => [(await frameState(0)).sent.length, (await frameState(1)).sent.length, (await frameState(2)).sent.length];
  const c0 = await counts();
  await tap('sessions');
  const c1 = await counts();
  out.activeOnly = { before: c0, after: c1, last: (await frameState(1)).sent.slice(-1) };
  await p.tap('.tab[data-idx="2"]');
  const lb = (await frameState(2)).sent.length;
  await tap('windows');
  out.legacy = (await frameState(2)).sent.slice(lb);
  out.pasted = [(await frameState(0)).pasted, (await frameState(1)).pasted, (await frameState(2)).pasted];
  await phone.close();
  // ---- desktop ----
  const desk = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const d = await desk.newPage(); watch(d, 'desktop');
  await d.goto(BASE + '/', { waitUntil: 'load' });
  await ready(d);
  out.desktop = await layout(d);
  await desk.close();
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""


def _find_node_playwright():
    node = shutil.which("node")
    if not node:
        return None
    for path in (sorted(glob.glob(os.path.expanduser("~/.npm/_npx/*/node_modules/playwright")))
                 + sorted(glob.glob(os.path.expanduser("~/node_modules/playwright")))):
        r = subprocess.run([node, "-e", "require(process.argv[1])", path],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return path
    return None


def _find_chromium():
    for pat in ("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"):
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = dict(http.server.SimpleHTTPRequestHandler.extensions_map,
                          **{".webmanifest": "application/manifest+json",
                             ".js": "text/javascript"})

    def log_message(self, *args):
        pass


class TestKeybarBrowser1159(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        pw, chromium = _find_node_playwright(), _find_chromium()
        if not (node and pw and chromium):
            raise unittest.SkipTest("node + playwright + chromium not all available")
        d = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, str(d), True)
        (d / "index.html").write_text(_render(), encoding="utf-8")
        pwa.write_pwa_assets(d, "zbynek")
        (d / "t").mkdir()
        (d / "t" / "index.html").write_text(_STUB_TTYD, encoding="utf-8")
        (d / "driver.js").write_text(_BROWSER_DRIVER, encoding="utf-8")
        # loopback ONLY (#681): never a wildcard bind for a test harness
        srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(d)))
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium, base,
                            json.dumps(KEY_ORDER)],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise AssertionError("browser driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_phone_shows_the_bar_at_the_bottom(self):
        ph = self.out["phone"]
        self.assertTrue(ph["coarse"] and ph["hoverNone"], ph)
        self.assertEqual(ph["display"], "flex")
        self.assertAlmostEqual(ph["bottom"], ph["vh"], delta=1)
        self.assertAlmostEqual(ph["framesBottom"], ph["top"], delta=1)
        self.assertEqual([b["k"] for b in ph["btns"]], KEY_ORDER)
        for b in ph["btns"]:
            self.assertGreaterEqual(b["h"], 44, b)
            self.assertGreaterEqual(b["w"], 44, b)

    def test_phone_bar_is_one_scrollable_row_without_page_overflow(self):
        ph = self.out["phone"]
        self.assertEqual(ph["overflowX"], "auto")
        self.assertGreater(ph["scrollW"], ph["clientW"])     # scrolls sideways
        self.assertLessEqual(ph["docScrollW"], ph["vw"])      # page does not
        self.assertLess(ph["height"], 60)                     # ONE row

    def test_every_key_sends_its_exact_bytes_as_typed(self):
        for k in KEY_ORDER:
            self.assertEqual(self.out["normal"][k], [[expected(k), True, "input"]], k)

    def test_up_follows_application_cursor_mode(self):
        for k in ARROWS:
            self.assertEqual(self.out["app"][k], [[expected(k, True), True, "input"]], k)

    def test_sessions_goes_to_the_active_tab_only(self):
        a = self.out["activeOnly"]
        self.assertEqual(a["after"], [a["before"][0], a["before"][1] + 1, a["before"][2]])
        self.assertEqual(a["last"], [["\x02s", True, "input"]])

    def test_ttyd_174_tab_uses_the_core_fallback(self):
        self.assertEqual(self.out["legacy"], [["\x02w", True, "core"]])

    def test_never_pastes(self):
        self.assertEqual(self.out["pasted"], [0, 0, 0])

    def test_a_tap_never_blurs_the_terminal(self):
        # the mousedown preventDefault: without it every tap blurs the terminal
        # (drops the phone keyboard) before focusTerminal refocuses it.
        self.assertEqual(self.out["blurDuringKeys"], 0)

    def test_focus_returns_to_the_terminal(self):
        for k, st in self.out["focusAfter"].items():
            self.assertTrue(st["refocused"], k)
            self.assertTrue(st["parentActive"], k)
            self.assertEqual(st["innerActive"], "TEXTAREA", k)

    def test_desktop_shows_no_bar(self):
        dk = self.out["desktop"]
        self.assertFalse(dk["coarse"])
        self.assertEqual(dk["display"], "none")
        self.assertEqual(dk["height"], 0)
        self.assertAlmostEqual(dk["framesBottom"], dk["vh"], delta=1)

    def test_console_is_clean(self):
        hits = [h for h in self.out["consoleHits"]
                if "GL Driver Message" not in h and "GPU stall" not in h]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
