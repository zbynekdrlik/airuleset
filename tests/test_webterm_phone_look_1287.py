"""#1287 round 2 (owner rejected the reading mode, 16.11.2026): the phone view
must look like the terminal and must not force itself on every load.

A. the capture keeps the colours (`capture-pane -e`) and an escape-only SGR
   converter draws them with DOM nodes, never markup;
B. the history uses the screen's font, size and palette: `●` before Claude's
   text, a small markdown subset, the `›` prompt bar for the owner's text;
C. the choice is remembered per device, the default is the terminal.
"""
import functools
import glob
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import cli_webterm as w  # noqa: E402
import cli_webterm_capture as cap  # noqa: E402
import cli_webterm_phone as phone  # noqa: E402
import test_webterm_keybar_1159 as kb  # noqa: E402
import test_webterm_phone_1287 as base  # noqa: E402

ESC = "\x1b"


class TestCaptureKeepsColour(unittest.TestCase):
    def test_capture_argv_carries_e(self):
        self.assertIn("exec tmux capture-pane -p -e -J -S -", cap.WEBTERM_READ_SH)

    def test_a_capped_capture_starts_at_a_whole_line(self):
        out = b"a" * 50 + b"\x1b[38;5;114m\xe2\x97\x8f\nline2\nline3\n"
        self.assertEqual(cap.tail_whole_lines(out, 1000), out)
        self.assertEqual(cap.tail_whole_lines(out, 20), b"line2\nline3\n")
        self.assertEqual(cap.tail_whole_lines(b"x" * 30, 10), b"x" * 10)

    def test_page_never_builds_markup_from_text(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser"):
            self.assertNotIn(bad, phone.PHONE_HTML)

    def test_the_leaf_stays_a_constant_leaf(self):
        src = (ROOT / "cli_webterm_phone.py").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^(import|from|def|class) ")


# Converter cases: [name, raw capture]. Each returns the rendered children of a
# detached <pre> as [text, color, background, weight, opacity, italic, decoration].
_CASES = {
    "bold": ESC + "[1mB" + ESC + "[0m.",
    "dim": ESC + "[2mD" + ESC + "[22m.",
    "italic_underline": ESC + "[3mI" + ESC + "[23m" + ESC + "[4mU" + ESC + "[24m.",
    "red": ESC + "[31mR" + ESC + "[39m.",
    "bright_red": ESC + "[91mR",
    "c256_cube": ESC + "[38;5;153mC",
    "c256_grey": ESC + "[38;5;244mG",
    "c256_low": ESC + "[38;5;3mY",
    "truecolor": ESC + "[38;2;10;20;30mT",
    "truecolor_colon": ESC + "[38:2::1:2:3mT",
    "bg256": ESC + "[48;5;37mK",
    "bg16": ESC + "[44mK",
    "inverse": ESC + "[7mV",
    "carry": ESC + "[1mA\nB" + ESC + "[m.",
    "foreign": ESC + "[2K" + ESC + "]8;;http://x" + ESC + "\\link" + ESC + "]8;;" + ESC + "\\" + ESC + "(B!",
    "bad256": ESC + "[38;5;999mX",
    "badrgb": ESC + "[38;2;300;0;0mX",
    "inject": ESC + "[31m<img src=x onerror=window.__pwned=1>",
    "unterminated": "ok" + ESC + "[38;5",
    "osc_open": "top" + ESC + "]8;;x\nline2",
    "ul58": ESC + "[58;2;1;2;3mU" + ESC + "[58;5;9mV",
}

# A grid-broken line in green continues bold on the next row; a coloured rule;
# trailing spaces painted with a background.
_TIDY = (ESC + "[32m" + "x" * 174 + "\n" + ESC + "[1mcont" + ESC + "[0m\n"
         + ESC + "[38;5;244m" + "─" * 176 + ESC + "[39m\n"
         + ESC + "[48;5;237m› hi" + " " * 100 + ESC + "[0m\n\n\n")

_MD = {"r": "a", "t": "# Nadpis\nText **tučné** a `kód` <b>x</b>\n- bod\n1. prvý\n```\ncode **no**\n```\nkoniec"}
_USER = {"r": "u", "t": "ahoj <i>x</i>\nriadok"}

_DRIVER = r"""
const [,, pwPath, chromium, BASE, CASES, TIDY, MD, USER] = process.argv;
const { chromium: cr } = require(pwPath);
(async () => {
  const b = await cr.launch({ executablePath: chromium, args: ['--no-sandbox'] });
  const out = { console: [] };
  const watch = (p) => { p.on('console', m => { if (['error', 'warning'].includes(m.type())) out.console.push(m.text()); }); p.on('pageerror', e => out.console.push(String(e))); };
  const opts = { viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true };
  const ph = await b.newContext(opts);
  const p = await ph.newPage(); watch(p);
  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForTimeout(400);
  const mode = () => p.evaluate(() => ({ display: getComputedStyle(document.getElementById('phone')).display,
    label: document.getElementById('phonetoggle').textContent, stored: localStorage.getItem('wt-phone-mode'),
    reads: performance.getEntriesByType('resource').filter(e => e.name.includes('phone-read')).length }));
  out.fresh = await mode();
  // ---- the converter, on detached nodes ----
  out.cases = await p.evaluate((cases) => {
    const desc = (pre) => [...pre.childNodes].map(n => n.nodeType === 3 ? [n.textContent, '', '', '', '', '', ''] :
      [n.textContent, n.style.color, n.style.backgroundColor, n.style.fontWeight, n.style.opacity, n.style.fontStyle, n.style.textDecoration]);
    const r = {};
    for (const [k, raw] of Object.entries(cases)) { const pre = document.createElement('pre');
      phoneRender(phoneAnsiLines(raw), pre); r[k] = { d: desc(pre), text: pre.textContent, img: !!pre.querySelector('img'), els: pre.querySelectorAll('*').length }; }
    return r;
  }, JSON.parse(CASES));
  out.pwned = await p.evaluate(() => !!window.__pwned);
  out.spacesMs = await p.evaluate(() => { const raw = ' '.repeat(100000) + 'x\n' + 'y'.repeat(10) + ' '.repeat(100000);
    const t0 = performance.now(); phoneTidy(phoneAnsiLines(raw)); return performance.now() - t0; });
  out.tidy = await p.evaluate((raw) => { const pre = document.createElement('pre');
    phoneRender(phoneTidy(phoneAnsiLines(raw)), pre);
    return { text: pre.textContent, spans: [...pre.querySelectorAll('span')].map(s => [s.textContent.slice(0, 30), s.style.color, s.style.fontWeight, s.style.backgroundColor]) }; }, TIDY);
  out.md = await p.evaluate((m) => { const d = phoneMsg(m);
    return { cls: d.className, text: d.textContent, mark: d.querySelector('.mk').textContent,
      bolds: [...d.querySelectorAll('b')].map(e => e.textContent), codes: [...d.querySelectorAll('.mc')].map(e => e.textContent),
      block: [...d.querySelectorAll('.mcode')].map(e => [e.textContent, e.querySelectorAll('*').length]),
      lines: [...d.querySelectorAll('.ml')].map(e => e.textContent), els: [...d.querySelectorAll('*')].map(e => e.tagName.toLowerCase()) }; }, JSON.parse(MD));
  out.user = await p.evaluate((m) => { const d = phoneMsg(m);
    return { cls: d.className, text: d.textContent, mark: d.querySelector('.mk').textContent, italics: d.querySelectorAll('i').length }; }, JSON.parse(USER));
  // ---- the persisted toggle ----
  await p.tap('#phonetoggle');
  await p.waitForFunction(() => document.getElementById('phonescreen').textContent.includes('tab0'));
  await p.waitForFunction(() => document.querySelectorAll('#phonehist .m').length >= 2);
  out.tapped = await mode();
  out.look = await p.evaluate(() => { const cs = (s) => getComputedStyle(document.querySelector(s));
    const m = cs('#phonehist .m.a'), sc = cs('#phonescreen'), u = cs('#phonehist .m.u');
    return { histFont: m.fontFamily, screenFont: sc.fontFamily, histSize: m.fontSize, screenSize: sc.fontSize,
      seam: sc.borderTopStyle, userBg: u.backgroundColor, logBg: cs('#phonelog').backgroundColor,
      screenSpans: [...document.querySelectorAll('#phonescreen span')].map(s => [s.textContent, s.style.color]),
      screenEsc: document.getElementById('phonescreen').textContent.includes('\x1b') }; });
  await p.reload({ waitUntil: 'load' });
  await p.waitForTimeout(400);
  out.reloadText = await mode();
  await p.tap('#phonetoggle');
  out.offAgain = await mode();
  await p.reload({ waitUntil: 'load' });
  await p.waitForTimeout(400);
  out.reloadTerminal = await mode();
  await ph.close();
  // ---- storage that throws (private mode, blocked site data) ----
  const blk = await b.newContext(opts);
  await blk.addInitScript(() => { Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } }); });
  const q = await blk.newPage(); watch(q);
  await q.goto(BASE + '/', { waitUntil: 'load' });
  await q.waitForTimeout(400);
  out.blocked = await q.evaluate(() => getComputedStyle(document.getElementById('phone')).display);
  await q.tap('#phonetoggle');
  out.blockedTap = await q.evaluate(() => getComputedStyle(document.getElementById('phone')).display);
  await blk.close();
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""

_SCREEN = ("tab0 " + ESC + "[38;5;114m●" + ESC + "[39m " + ESC + "[1mtučné" + ESC + "[0m\n"
           + ESC + "[31m<b>raw</b>" + ESC + "[0m\n")


class _Handler(base._Handler):
    def do_GET(self):
        if self.path.startswith("/phone-read"):
            return self._json({"ok": True, "text": _SCREEN})
        return super().do_GET()


def _rgb(hexs):
    h = hexs.lstrip("#")
    return "rgb(%d, %d, %d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


T = w.CAMPBELL_THEME


class TestPhoneLook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        pw = os.environ.get("WEBTERM_TEST_PLAYWRIGHT") or kb._find_node_playwright()
        chromium = (kb._find_chromium()
                    or next(iter(sorted(glob.glob("/opt/ms-playwright/chromium-*/chrome-linux64/chrome"))), None))
        if not (node and pw and chromium):
            raise unittest.SkipTest("node + playwright + chromium not all available")
        d = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, str(d), True)
        (d / "index.html").write_text(
            w.render_dashboard_html(base.INV3, ttyd_base="/t", phone_read=True), encoding="utf-8")
        kb.pwa.write_pwa_assets(d, "zbynek")
        (d / "t").mkdir()
        (d / "t" / "index.html").write_text(base._STUB, encoding="utf-8")
        (d / "driver.js").write_text(_DRIVER, encoding="utf-8")
        srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_Handler, directory=str(d)))
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium,
                            "http://127.0.0.1:%d" % srv.server_address[1],
                            json.dumps(_CASES), _TIDY, json.dumps(_MD), json.dumps(_USER)],
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise AssertionError("driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def _span(self, case, text):
        for row in self.out["cases"][case]["d"]:
            if row[0] == text:
                return row
        self.fail("%s: no node %r in %r" % (case, text, self.out["cases"][case]["d"]))

    # ---- A: the converter ----
    def test_bold_dim_italic_underline(self):
        self.assertEqual(self._span("bold", "B")[3], "bold")
        self.assertEqual(self._span("bold", ".")[3], "")
        self.assertTrue(self._span("dim", "D")[4])
        self.assertEqual(self._span("italic_underline", "I")[5], "italic")
        self.assertIn("underline", self._span("italic_underline", "U")[6])
        self.assertEqual(self._span("italic_underline", ".")[6], "")

    def test_sixteen_colours_use_the_terminal_palette(self):
        self.assertEqual(self._span("red", "R")[1], _rgb(T["red"]))
        self.assertEqual(self._span("red", ".")[1], "")
        self.assertEqual(self._span("bright_red", "R")[1], _rgb(T["brightRed"]))
        self.assertEqual(self._span("c256_low", "Y")[1], _rgb(T["yellow"]))
        self.assertEqual(self._span("bg16", "K")[2], _rgb(T["blue"]))

    def test_256_and_truecolor(self):
        self.assertEqual(self._span("c256_cube", "C")[1], "rgb(175, 215, 255)")
        self.assertEqual(self._span("c256_grey", "G")[1], "rgb(128, 128, 128)")
        self.assertEqual(self._span("bg256", "K")[2], "rgb(0, 175, 175)")
        self.assertEqual(self._span("truecolor", "T")[1], "rgb(10, 20, 30)")
        self.assertEqual(self._span("truecolor_colon", "T")[1], "rgb(1, 2, 3)")

    def test_inverse_swaps_the_default_colours(self):
        row = self._span("inverse", "V")
        self.assertEqual(row[1], _rgb(T["background"]))
        self.assertEqual(row[2], _rgb(T["foreground"]))

    def test_state_carries_across_lines_like_tmux_emits_it(self):
        self.assertEqual(self._span("carry", "B")[3], "bold")
        self.assertEqual(self.out["cases"]["carry"]["text"], "A\nB.")

    def test_other_escapes_are_dropped_not_shown(self):
        self.assertEqual(self.out["cases"]["foreign"]["text"], "link!")
        self.assertEqual(self.out["cases"]["unterminated"]["text"], "ok")
        for case in self.out["cases"].values():
            self.assertNotIn(ESC, case["text"])

    def test_an_open_osc_stops_at_the_line_end(self):
        self.assertEqual(self.out["cases"]["osc_open"]["text"], "top\nline2")

    def test_underline_colour_is_not_read_as_attributes(self):
        self.assertEqual(self.out["cases"]["ul58"]["text"], "UV")
        for row in self.out["cases"]["ul58"]["d"]:
            self.assertEqual(row[3:6], ["", "", ""])

    def test_long_space_runs_trim_in_linear_time(self):
        # /\s+$/ is quadratic on a long space run in node and older engines (10 s
        # for 100k spaces); current Chromium happens to run it fast, hence the lock.
        for slow in (r"/\s+$/", r"/^\s+/"):
            self.assertNotIn(slow, phone.PHONE_HTML)
        self.assertLess(self.out["spacesMs"], 1500)

    def test_out_of_range_colours_are_ignored(self):
        self.assertEqual(self.out["cases"]["bad256"]["text"], "X")
        self.assertEqual(self.out["cases"]["bad256"]["d"][0][1], "")
        self.assertEqual(self.out["cases"]["badrgb"]["d"][0][1], "")

    def test_pane_text_is_never_markup(self):
        inj = self.out["cases"]["inject"]
        self.assertFalse(inj["img"])
        self.assertEqual(inj["els"], 1)               # the one colour span
        self.assertEqual(inj["text"], "<img src=x onerror=window.__pwned=1>")
        self.assertFalse(self.out["pwned"])

    def test_tidy_works_on_visible_text_and_keeps_colours(self):
        tidy = self.out["tidy"]
        self.assertEqual(tidy["text"], "x" * 174 + " cont\n" + "─" * 24 + "\n› hi")
        green, bold = _rgb(T["green"]), ["cont", _rgb(T["green"]), "bold", ""]
        self.assertEqual(tidy["spans"][0][:2], ["x" * 30, green])
        self.assertIn(bold, tidy["spans"])
        self.assertIn(["─" * 24, "rgb(128, 128, 128)", "", ""], tidy["spans"])
        self.assertIn(["› hi", "", "", "rgb(58, 58, 58)"], tidy["spans"])

    # ---- B: history in the terminal look ----
    def test_markdown_subset_is_drawn_with_nodes(self):
        md = self.out["md"]
        self.assertEqual(md["cls"], "m a")
        self.assertEqual(md["mark"], "● ")
        self.assertEqual(md["bolds"], ["Nadpis", "tučné"])
        self.assertEqual(md["codes"], ["kód"])
        self.assertEqual(md["block"], [["code **no**", 0]])
        self.assertIn("Text tučné a kód <b>x</b>", md["lines"])
        self.assertIn("- bod", md["lines"])
        self.assertIn("1. prvý", md["lines"])
        self.assertIn("koniec", md["lines"])
        self.assertNotIn("**", md["text"].replace("code **no**", ""))
        self.assertNotIn("`", md["text"])
        self.assertEqual(sorted(set(md["els"])), ["b", "div", "span"])

    def test_owner_text_is_the_prompt_bar(self):
        u = self.out["user"]
        self.assertEqual(u["cls"], "m u")
        self.assertEqual(u["mark"], "› ")
        self.assertEqual(u["text"], "› ahoj <i>x</i>\nriadok")
        self.assertEqual(u["italics"], 0)

    def test_history_and_screen_are_one_terminal(self):
        look = self.out["look"]
        self.assertEqual(look["histFont"], look["screenFont"])
        self.assertEqual(look["histSize"], look["screenSize"])
        self.assertEqual(look["seam"], "none")
        self.assertNotEqual(look["userBg"], look["logBg"])
        self.assertEqual(look["logBg"], _rgb(T["background"]))

    def test_the_screen_shows_colour_from_the_capture(self):
        look = self.out["look"]
        self.assertIn(["●", "rgb(135, 215, 135)"], look["screenSpans"])
        self.assertIn(["<b>raw</b>", _rgb(T["red"])], look["screenSpans"])
        self.assertFalse(look["screenEsc"])

    # ---- C: the toggle ----
    def test_a_fresh_device_opens_the_terminal(self):
        fresh = self.out["fresh"]
        self.assertEqual(fresh["display"], "none")
        self.assertEqual(fresh["label"], "Text")
        self.assertIsNone(fresh["stored"])
        self.assertEqual(fresh["reads"], 0)

    def test_the_choice_survives_a_reload(self):
        self.assertEqual(self.out["tapped"]["display"], "flex")
        self.assertEqual(self.out["tapped"]["stored"], "text")
        self.assertEqual(self.out["reloadText"]["display"], "flex")
        self.assertEqual(self.out["reloadText"]["label"], "Terminál")
        self.assertEqual(self.out["offAgain"]["stored"], "terminal")
        self.assertEqual(self.out["reloadTerminal"]["display"], "none")

    def test_blocked_storage_still_works(self):
        self.assertEqual(self.out["blocked"], "none")
        self.assertEqual(self.out["blockedTap"], "flex")

    def test_console_is_clean(self):
        self.assertEqual([c for c in self.out["console"] if "GL Driver" not in c and "GPU stall" not in c], [])


if __name__ == "__main__":
    unittest.main()
