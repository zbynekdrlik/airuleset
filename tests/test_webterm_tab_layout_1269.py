"""#1269 -- the owner reorders the webterm tabs and hides rarely used ones in a menu.

Owner (30.10.2026): "aby sa dali taby hore preusporadavat a hidnut do nejakeho
menu napravo ako posledny tab ktory sa vyroluje ako menu".

The layout (order + hidden set, by session id) lives server-side in the
dashboard's own dir (`tab-layout.json`), served and stored by the gateway at
`/tab-layout`, so it is the same on every device of that dashboard.

Three tiers:
* GATEWAY -- the pure parser and the route: auth-gated like `/`, POST only
  same-origin JSON under a size cap, ids validated, an atomic 0600 write, a
  corrupt file served as an empty layout.
* STRUCTURAL -- the template carries the bar container, the menu item and the
  persistence calls.
* REAL BROWSER (node playwright + chromium headless-shell + a real ttyd; SKIPS
  where absent) -- drag reorder, drag onto the menu item, right-click hide,
  opening a hidden tab from the menu, Ctrl+Alt+N over the visible order, the
  layout surviving a reload, putting a tab back, a touch drag, zero console
  errors/warnings.
"""
import asyncio
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
import cli_webterm_gateway as g  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402
from test_webterm_first_view_fit_1164 import _free_port, _stop, _wait_http  # noqa: E402
from test_webterm_gateway import _GatewayHarness  # noqa: E402
from test_webterm_keybar_1159 import _find_chromium, _find_node_playwright  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
NTABS = 5


class TestParseTabLayout(unittest.TestCase):

    def test_valid_layout_round_trips(self):
        body = json.dumps({"order": ["dev1", "gk", "sb"], "hidden": ["sb"]}).encode()
        self.assertEqual(g.parse_tab_layout(body),
                         {"order": ["dev1", "gk", "sb"], "hidden": ["sb"]})

    def test_missing_keys_are_empty_lists(self):
        self.assertEqual(g.parse_tab_layout(b"{}"), {"order": [], "hidden": []})

    def test_rejects_non_json_and_wrong_shapes(self):
        for raw in (b"", b"not json", b"[]", b'{"order": "dev1"}',
                    b'{"order": [1]}', b'{"hidden": [null]}',
                    b'{"order": ["a"], "extra": 1}'):
            self.assertIsNone(g.parse_tab_layout(raw), raw)

    def test_rejects_bad_ids(self):
        for bad in ("", "a b", "../x", "x" * 65, "<script>", "-lead", "dev1\n"):
            raw = json.dumps({"order": [bad]}).encode()
            self.assertIsNone(g.parse_tab_layout(raw), bad)

    def test_accepts_real_inventory_ids(self):
        ids = ["montalu4-subdev", "ar", "david1-subdev", "fohmixer@dev1", "codex.bridge"]
        self.assertEqual(g.parse_tab_layout(json.dumps({"order": ids}).encode())["order"], ids)

    def test_rejects_duplicates_and_too_many(self):
        self.assertIsNone(g.parse_tab_layout(b'{"order": ["a", "a"]}'))
        many = ["t%d" % i for i in range(g.TAB_LAYOUT_MAX_IDS + 1)]
        self.assertIsNone(g.parse_tab_layout(json.dumps({"order": many}).encode()))

    def test_rejects_non_utf8(self):
        self.assertIsNone(g.parse_tab_layout(b'{"order": ["\xff"]}'))


def _run(coro):
    return asyncio.run(coro)


class TestTabLayoutRoute(unittest.TestCase):

    async def _harness(self):
        h = _GatewayHarness()
        h.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, h.tmp, True)
        h.cred_path = Path(h.tmp) / "cred"
        h.cred_path.write_text(h.cred_text, encoding="utf-8")
        h.dash_path = Path(h.tmp) / "index.html"
        h.dash_path.write_text(h.dash_text, encoding="utf-8")
        h.ttyd_port = await h.fake.start()
        probe = await asyncio.start_server(lambda r, wr: wr.close(), "127.0.0.1", 0)
        port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        h.origin = "http://127.0.0.1:%d" % port
        h.sessions = g.SessionStore()
        h.server = await g.start_gateway(
            "127.0.0.1", port, str(h.dash_path), str(h.cred_path),
            ttyd_host="127.0.0.1", ttyd_port=h.ttyd_port, base_path="/t",
            origins=[h.origin], sessions=h.sessions)
        h.port = port
        h.tok = h.sessions.create()
        return h

    async def _teardown(self, h):
        h.server.close()
        await h.server.wait_closed()
        await h.fake.stop()

    def _get(self, h, cookie=True):
        head = b"GET /tab-layout HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % h.port
        if cookie:
            head += b"Cookie: webterm_session=%s\r\n" % h.tok.encode()
        return h.request(head + b"\r\n")

    def _post(self, h, body, origin=True, ctype=b"application/json", cookie=True,
              length=None):
        head = b"POST /tab-layout HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % h.port
        if cookie:
            head += b"Cookie: webterm_session=%s\r\n" % h.tok.encode()
        if origin is True:
            head += b"Origin: %s\r\n" % h.origin.encode()
        elif origin:
            head += b"Origin: %s\r\n" % origin
        if ctype:
            head += b"Content-Type: %s\r\n" % ctype
        head += b"Content-Length: %d\r\n\r\n" % (len(body) if length is None else length)
        return h.request(head + body)

    @staticmethod
    def _json(resp):
        return json.loads(resp.split(b"\r\n\r\n", 1)[1])

    def test_get_without_a_file_serves_an_empty_layout(self):
        async def go():
            h = await self._harness()
            try:
                r = await self._get(h)
                self.assertIn(b"200 OK", r)
                self.assertIn(b"application/json", r)
                self.assertIn(b"no-store", r)
                self.assertEqual(self._json(r), {"order": [], "hidden": []})
            finally:
                await self._teardown(h)
        _run(go())

    def test_post_stores_and_get_returns_it(self):
        async def go():
            h = await self._harness()
            try:
                lay = {"order": ["s2", "s0", "s1"], "hidden": ["s1"]}
                r = await self._post(h, json.dumps(lay).encode())
                self.assertIn(b"204 No Content", r)
                f = Path(h.tmp) / "tab-layout.json"
                self.assertEqual(json.loads(f.read_text()), lay)
                self.assertEqual(f.stat().st_mode & 0o777, 0o600)
                self.assertEqual(self._json(await self._get(h)), lay)
                self.assertEqual([p.name for p in Path(h.tmp).iterdir()
                                  if p.name.startswith(".tab-layout")], [])
            finally:
                await self._teardown(h)
        _run(go())

    def test_unauthenticated_requests_are_refused(self):
        async def go():
            h = await self._harness()
            try:
                (Path(h.tmp) / "tab-layout.json").write_text('{"order": ["secret1"]}')
                r = await self._get(h, cookie=False)
                self.assertNotIn(b"200 OK", r)
                self.assertNotIn(b"secret1", r)
                r = await self._post(h, b'{"order": ["x"]}', cookie=False)
                self.assertNotIn(b"204", r)
                self.assertIn("secret1", (Path(h.tmp) / "tab-layout.json").read_text())
            finally:
                await self._teardown(h)
        _run(go())

    def test_post_needs_same_origin(self):
        async def go():
            h = await self._harness()
            try:
                for origin in (None, b"https://evil.example"):
                    r = await self._post(h, b'{"order": ["x"]}', origin=origin)
                    self.assertIn(b"403", r, origin)
                self.assertFalse((Path(h.tmp) / "tab-layout.json").exists())
            finally:
                await self._teardown(h)
        _run(go())

    def test_post_needs_json_content_type(self):
        async def go():
            h = await self._harness()
            try:
                for ctype in (None, b"text/plain", b"application/x-www-form-urlencoded"):
                    r = await self._post(h, b'{"order": ["x"]}', ctype=ctype)
                    self.assertIn(b"415", r, ctype)
                r = await self._post(h, b'{"order": ["x"]}',
                                     ctype=b"application/json; charset=utf-8")
                self.assertIn(b"204", r)
            finally:
                await self._teardown(h)
        _run(go())

    def test_post_refuses_an_invalid_body(self):
        async def go():
            h = await self._harness()
            try:
                r = await self._post(h, b'{"order": ["../etc"]}')
                self.assertIn(b"400", r)
                self.assertFalse((Path(h.tmp) / "tab-layout.json").exists())
            finally:
                await self._teardown(h)
        _run(go())

    def test_post_refuses_an_oversize_body_before_reading_it(self):
        async def go():
            h = await self._harness()
            try:
                r = await self._post(h, b"{}", length=g.MAX_TAB_LAYOUT_BODY + 1)
                self.assertIn(b"413", r)
            finally:
                await self._teardown(h)
        _run(go())

    def test_other_methods_are_refused(self):
        async def go():
            h = await self._harness()
            try:
                r = await h.request(
                    b"DELETE /tab-layout HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                    b"Cookie: webterm_session=%s\r\n\r\n" % (h.port, h.tok.encode()))
                self.assertIn(b"405", r)
            finally:
                await self._teardown(h)
        _run(go())

    def test_the_dashboard_inlines_the_stored_layout(self):
        async def go():
            h = await self._harness()
            try:
                h.dash_path.write_text("<script>const L = /*wt-tab-layout*/null;</script>")
                req = (b"GET / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                       b"Cookie: webterm_session=%s\r\n\r\n" % (h.port, h.tok.encode()))
                self.assertIn(b"/*wt-tab-layout*/null;", await h.request(req))
                lay = {"order": ["s1", "s0"], "hidden": ["s0"]}
                (Path(h.tmp) / "tab-layout.json").write_text(json.dumps(lay))
                r = await h.request(req)
                self.assertIn(b"/*wt-tab-layout*/" + json.dumps(lay).encode() + b";", r)
                (Path(h.tmp) / "tab-layout.json").write_text('{"order": ["</script>"]}')
                self.assertIn(b"/*wt-tab-layout*/null;", await h.request(req))
            finally:
                await self._teardown(h)
        _run(go())

    def test_a_corrupt_file_is_served_as_empty(self):
        async def go():
            h = await self._harness()
            try:
                (Path(h.tmp) / "tab-layout.json").write_text("{broken")
                self.assertEqual(self._json(await self._get(h)), {"order": [], "hidden": []})
            finally:
                await self._teardown(h)
        _run(go())


def _inv(n=NTABS):
    return [{"id": "s%d" % i, "label": "s%d" % i, "kind": "owner", "local": False,
             "host": "10.0.0.%d" % (i + 1), "user": "s%d" % i} for i in range(n)]


def _render(n=NTABS):
    return w.render_dashboard_html(_inv(n), ttyd_base="/t")


class TestTemplateStructure1269(unittest.TestCase):

    def setUp(self):
        self.html = _render()

    def test_tabmenu_module_is_a_pure_constant_leaf(self):
        import ast
        src = (REPO / "cli_webterm_tabmenu.py").read_text(encoding="utf-8")
        banned = (ast.Import, ast.ImportFrom, ast.FunctionDef,
                  ast.AsyncFunctionDef, ast.ClassDef)
        self.assertEqual([type(n).__name__ for n in ast.walk(ast.parse(src))
                          if isinstance(n, banned)], [])
        import cli_webterm_tabmenu as tm
        self.assertIn(tm.TABMENU_CSS, self.html)
        self.assertIn(tm.TABMENU_JS, self.html)
        # the JS shares the main <script>: activate/onHotkey call into it
        main = self.html[self.html.index("<script>"):self.html.index("</script>")]
        self.assertIn(tm.TABMENU_JS, main)

    def test_the_gateway_mark_is_in_the_page_once(self):
        self.assertEqual(self.html.count(g.TAB_LAYOUT_MARK.decode() + "null"), 1)

    def test_bar_has_a_scrolling_tab_strip_and_a_menu_item(self):
        self.assertIn('<div id="tabs">', self.html)
        self.assertIn('id="tabmore"', self.html)
        self.assertIn('id="tabmenu"', self.html)
        # the menu item sits AFTER the tab strip: the last item of the bar
        self.assertLess(self.html.index('<div id="tabs">'), self.html.index('id="tabmore"'))

    def test_a_hidden_tab_is_not_displayed(self):
        self.assertIn(".tab[hidden] { display: none; }", self.html)

    def test_layout_is_loaded_and_saved_through_the_gateway(self):
        self.assertIn("fetch('/tab-layout'", self.html)
        self.assertIn("method: 'POST'", self.html)

    def test_every_tab_is_still_rendered(self):
        for i in range(NTABS):
            self.assertIn('data-idx="%d"' % i, self.html)


_DRIVER = r"""
const [,, PW, CH, BASE] = process.argv;
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const NOISE = [/\[ttyd\] maybe unknown option: arg=/, /GL Driver Message/, /GPU stall/];
(async () => {
  const { chromium } = require(PW);
  const out = { consoleHits: [] };
  const b = await chromium.launch({ executablePath: CH, headless: true, args: ['--no-sandbox'] });
  const watch = (p, name) => {
    p.on('console', m => { const t = m.type(), x = m.text();
      if ((t === 'error' || t === 'warning') && !NOISE.some(re => re.test(x))) out.consoleHits.push(name + ':' + t + ':' + x); });
    p.on('pageerror', e => out.consoleHits.push(name + ':pageerror:' + e.message));
    p.on('dialog', d => d.accept());
  };
  const open = async (ctx, name) => {
    const p = await ctx.newPage();
    watch(p, name);
    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForFunction(() => document.querySelectorAll('#frames iframe').length > 0
      && document.body.dataset.wtLayout === 'ready', null, { timeout: 30000 });
    return p;
  };
  const state = (p) => p.evaluate(() => {
    const al = (t) => t.querySelector('.al').textContent;
    const tabs = [...document.querySelectorAll('#tabs .tab')];
    const vis = [...document.querySelectorAll('#frames iframe')].filter(f => f.style.display !== 'none');
    const shown = vis.length === 1 ? [...document.querySelectorAll('#frames iframe')].indexOf(vis[0]) : -1;
    const more = document.getElementById('tabmore');
    return {
      visible: tabs.filter(t => !t.hidden && getComputedStyle(t).display !== 'none').map(al),
      hidden: tabs.filter(t => t.hidden).map(al),
      menu: [...document.querySelectorAll('#tabmenu .mrow .mopen')].map(x => x.textContent),
      menuOpen: !document.getElementById('tabmenu').hidden,
      active: (document.querySelector('#tabs .tab.active .al') || {}).textContent || null,
      moreActive: more.classList.contains('active'),
      moreLabel: more.querySelector('.al').textContent,
      shownIdx: shown,
    };
  });
  const center = async (p, sel) => { const r = await p.locator(sel).boundingBox(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, r }; };
  const tabSel = (alias) => '#tabs .tab:has(.al:text-is("' + alias + '"))';
  const drag = async (p, fromSel, toX, toY) => {
    const a = await center(p, fromSel);
    await p.mouse.move(a.x, a.y); await p.mouse.down();
    await p.mouse.move(toX, toY, { steps: 12 }); await p.mouse.up();
    await sleep(200);
  };
  const saved = (p) => p.evaluate(() => fetch('/tab-layout', { cache: 'no-store' }).then(r => r.json()));
  const waitSaved = async (p, pred) => {
    for (let i = 0; i < 50; i++) { const s = await saved(p); if (pred(s)) return s; await sleep(100); }
    return await saved(p);
  };

  const ctx = await b.newContext({ viewport: { width: 1600, height: 900 } });
  const lr = await ctx.request.post(BASE + '/login', { form: { username: 'u', password: 'p' }, maxRedirects: 0 });
  if (lr.status() !== 303) throw new Error('login failed ' + lr.status());
  let p = await open(ctx, 'desktop');
  out.initial = await state(p);

  // reorder: drag s3 onto the left half of s0
  const s0 = await center(p, tabSel('s0'));
  await drag(p, tabSel('s3'), s0.r.x + 4, s0.y);
  out.afterReorder = await state(p);
  out.savedReorder = await waitSaved(p, s => (s.order || [])[0] === 's3');

  // hide by dragging onto the menu item
  const m = await center(p, '#tabmore');
  await drag(p, tabSel('s1'), m.x, m.y);
  out.afterDragHide = await state(p);

  // hide by right-click
  await p.click(tabSel('s4'), { button: 'right' });
  await sleep(200);
  out.afterRightHide = await state(p);
  out.savedHidden = await waitSaved(p, s => (s.hidden || []).length === 2);

  // open a hidden tab from the menu
  await p.click('#tabmore');
  out.menuOpened = await state(p);
  await p.click('#tabmenu .mrow:has(.mopen:text-is("s1")) .mopen');
  await sleep(200);
  out.openedHidden = await state(p);

  // Ctrl+Alt+N follows the visible order
  await p.keyboard.press('Control+Alt+1');
  await sleep(200);
  out.hotkey1 = await state(p);
  await p.keyboard.press('Control+Alt+3');
  await sleep(200);
  out.hotkey3 = await state(p);

  // a plain click still switches
  await p.click(tabSel('s0'));
  await sleep(200);
  out.clicked = await state(p);

  // the layout survives a reload; the landing tab is the first visible one
  await p.close();
  p = await open(ctx, 'reload');
  await sleep(300);
  out.reloaded = await state(p);

  // put a tab back into the bar
  await p.click('#tabmore');
  await p.click('#tabmenu .mrow:has(.mopen:text-is("s1")) .mpin');
  await sleep(200);
  out.unhidden = await state(p);
  out.savedUnhidden = await waitSaved(p, s => (s.hidden || []).length === 1);
  await p.close();
  await ctx.close();

  // touch: hold a tab, then drag it
  const tctx = await b.newContext({ viewport: { width: 1280, height: 800 }, hasTouch: true, isMobile: true });
  const lr2 = await tctx.request.post(BASE + '/login', { form: { username: 'u', password: 'p' }, maxRedirects: 0 });
  if (lr2.status() !== 303) throw new Error('login failed ' + lr2.status());
  const tp = await open(tctx, 'touch');
  out.touchInitial = await state(tp);
  const cdp = await tctx.newCDPSession(tp);
  const vis = out.touchInitial.visible;
  const src = await center(tp, tabSel(vis[vis.length - 1]));
  const dst = await center(tp, tabSel(vis[0]));
  const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y }] });
  await touch('touchStart', src.x, src.y);
  await sleep(600);
  for (let i = 1; i <= 12; i++) await touch('touchMove', src.x + (dst.r.x + 4 - src.x) * i / 12, src.y);
  await touch('touchEnd', 0, 0);
  await sleep(300);
  out.touchAfter = await state(tp);
  await tctx.close();

  // review 🔴: a drag released over the terminal must end, and a button-less
  // hover over the bar afterwards must not move anything
  const dctx = await b.newContext({ viewport: { width: 1600, height: 900 } });
  const lr3 = await dctx.request.post(BASE + '/login', { form: { username: 'u', password: 'p' }, maxRedirects: 0 });
  if (lr3.status() !== 303) throw new Error('login failed ' + lr3.status());
  const dp = await open(dctx, 'offbar');
  const dv = (await state(dp)).visible;
  const a = await center(dp, tabSel(dv[1])), z = await center(dp, tabSel(dv[0]));
  await dp.mouse.move(a.x, a.y); await dp.mouse.down();
  await dp.mouse.move(z.r.x + 4, z.y, { steps: 10 });
  await dp.mouse.move(z.r.x + 4, z.y + 300, { steps: 10 });     // down into the terminal
  await dp.mouse.up();
  await sleep(300);
  out.offbarReleased = await state(dp);
  out.offbarDragging = await dp.evaluate(() => document.querySelectorAll('#tabs .tab.dragging').length);
  out.offbarSaved = await waitSaved(dp, s => (s.order || [])[0] === dv[1]);
  const last = await center(dp, tabSel(out.offbarReleased.visible[out.offbarReleased.visible.length - 1]));
  await dp.mouse.move(last.x, last.y, { steps: 10 });               // hover, no button
  await dp.mouse.move(z.r.x + 4, z.y, { steps: 10 });
  await sleep(200);
  out.offbarHover = await state(dp);
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""


class TestTabLayoutRealBrowser1269(unittest.TestCase):

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
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium, base],
                           capture_output=True, text=True, timeout=600,
                           env=dict(os.environ))
        if r.returncode != 0:
            raise AssertionError("browser driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])
        cls.layout_file = d / "tab-layout.json"
        cls.layout_on_disk = json.loads(cls.layout_file.read_text())

    def test_initial_bar_is_the_default_order_with_the_menu_item(self):
        o = self.out["initial"]
        self.assertEqual(o["visible"], ["s0", "s1", "s2", "s3", "s4"])
        self.assertEqual(o["active"], "s0")
        self.assertFalse(o["moreActive"])
        self.assertFalse(o["menuOpen"])

    def test_drag_reorders_without_switching(self):
        o = self.out["afterReorder"]
        self.assertEqual(o["visible"], ["s3", "s0", "s1", "s2", "s4"])
        self.assertEqual(o["active"], "s0")
        self.assertEqual(self.out["savedReorder"]["order"], ["s3", "s0", "s1", "s2", "s4"])

    def test_drop_on_the_menu_item_hides_the_tab(self):
        o = self.out["afterDragHide"]
        self.assertEqual(o["visible"], ["s3", "s0", "s2", "s4"])
        self.assertEqual(o["hidden"], ["s1"])
        self.assertEqual(o["menu"], ["s1"])

    def test_right_click_hides_the_tab(self):
        o = self.out["afterRightHide"]
        self.assertEqual(o["visible"], ["s3", "s0", "s2"])
        self.assertEqual(o["menu"], ["s1", "s4"])
        self.assertEqual(sorted(self.out["savedHidden"]["hidden"]), ["s1", "s4"])

    def test_menu_opens_and_a_row_opens_the_hidden_tab(self):
        self.assertTrue(self.out["menuOpened"]["menuOpen"])
        o = self.out["openedHidden"]
        self.assertEqual(o["shownIdx"], 1)            # s1's own iframe is shown
        self.assertIsNone(o["active"])                # no bar tab is active
        self.assertTrue(o["moreActive"])
        self.assertEqual(o["moreLabel"], "s1")
        self.assertFalse(o["menuOpen"])

    def test_hotkeys_follow_the_visible_order(self):
        self.assertEqual(self.out["hotkey1"]["active"], "s3")
        self.assertEqual(self.out["hotkey1"]["shownIdx"], 3)
        self.assertFalse(self.out["hotkey1"]["moreActive"])
        self.assertEqual(self.out["hotkey3"]["active"], "s2")

    def test_a_plain_click_still_switches(self):
        self.assertEqual(self.out["clicked"]["active"], "s0")
        self.assertEqual(self.out["clicked"]["shownIdx"], 0)

    def test_layout_survives_a_reload(self):
        o = self.out["reloaded"]
        self.assertEqual(o["visible"], ["s3", "s0", "s2"])
        self.assertEqual(o["menu"], ["s1", "s4"])
        self.assertEqual(o["active"], "s3")
        self.assertEqual(o["shownIdx"], 3)

    def test_a_tab_goes_back_into_the_bar_at_its_place(self):
        o = self.out["unhidden"]
        self.assertEqual(o["visible"], ["s3", "s0", "s1", "s2"])
        self.assertEqual(o["menu"], ["s4"])
        self.assertEqual(self.layout_on_disk["hidden"], ["s4"])

    def test_touch_hold_then_drag_reorders(self):
        before = self.out["touchInitial"]["visible"]
        after = self.out["touchAfter"]["visible"]
        self.assertEqual(after, [before[-1]] + before[:-1])

    def test_a_drag_released_over_the_terminal_ends_and_saves(self):
        released = self.out["offbarReleased"]["visible"]
        self.assertEqual(self.out["offbarDragging"], 0)
        self.assertEqual(self.out["offbarSaved"]["order"][:2], released[:2])
        self.assertEqual(self.out["offbarHover"]["visible"], released)

    def test_console_is_clean(self):
        self.assertEqual(self.out["consoleHits"], [])


if __name__ == "__main__":
    unittest.main()
