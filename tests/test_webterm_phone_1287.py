"""#1287: the webterm phone text view (owner 3.11.2026, approach 1, mockup v1).

The tmux window stays 176x50 and the PC view stays as it is, so the read must
never touch a session, and every send goes through the tab's own xterm.
"""
import asyncio
import functools
import glob
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import cli_webterm as w  # noqa: E402
import cli_webterm_capture as cap  # noqa: E402
import cli_webterm_gateway as g  # noqa: E402
import cli_webterm_phone as phone  # noqa: E402
import test_webterm_keybar_1159 as kb  # noqa: E402

INVENTORY = [
    {"id": "alfa", "label": "alfa", "local": True, "host": None, "user": None,
     "identity": None, "preferred": "zbynek"},
    {"id": "beta", "label": "beta", "local": False, "host": "100.1.1.1", "user": "beta",
     "identity": "~/key", "preferred": "beta"},
]


class TestForcedCommandRead(unittest.TestCase):
    """Drives the real attach body in sh, with a tmux stub that logs its argv."""

    def _run(self, verb, sessions="zbynek"):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d), True)
        log = d / "tmux.log"
        stub = d / "tmux"
        stub.write_text('#!/bin/sh\necho "$*" >> %s\ncase "$1" in\n'
                        '  has-session) [ -n "%s" ]; exit $? ;;\n'
                        '  capture-pane) echo pane-text ;;\n'
                        "esac\nexit 0\n" % (log, sessions))
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH="%s:%s" % (d, os.environ["PATH"]), HOME=str(d))
        env.pop("SSH_ORIGINAL_COMMAND", None)
        if verb is not None:
            env["SSH_ORIGINAL_COMMAND"] = verb
        cmd = w._remote_command("zbynek")
        r = subprocess.run(["sh", "-c", cmd], env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=20)
        return r, log.read_text() if log.exists() else ""

    def test_the_verb_prints_the_pane_with_history(self):
        r, log = self._run(cap.WEBTERM_CAPTURE_VERB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "pane-text")
        self.assertIn("capture-pane -p -J -S -%d -t =zbynek:" % cap.WEBTERM_CAPTURE_LINES, log)

    def test_the_read_touches_nothing_in_the_session(self):
        _, log = self._run(cap.WEBTERM_CAPTURE_VERB)
        for word in ("attach-session", "new-session", "set-option", "resize-window"):
            self.assertNotIn(word, log)

    def test_a_missing_session_is_a_failure_and_never_a_new_session(self):
        r, log = self._run(cap.WEBTERM_CAPTURE_VERB, sessions="")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("new-session", log)

    def test_any_other_command_still_attaches(self):
        for verb in (None, "wt-capture; id", "cat /etc/passwd", "WT-CAPTURE"):
            _, log = self._run(verb)
            self.assertIn("attach-session", log, verb)
            self.assertNotIn("capture-pane", log, verb)


class TestHistoryRead(unittest.TestCase):
    """The history verb, through the real attach body, on a fake transcript."""

    def _hist(self, verb, data=b"", cwd="/home/x/devel/proj"):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d), True)
        proj = d / ".claude/projects" / "".join(ch if ch.isalnum() else "-" for ch in cwd)
        proj.mkdir(parents=True)
        (proj / "old.jsonl").write_bytes(b"old\n")
        os.utime(proj / "old.jsonl", (1, 1))
        (proj / "new.jsonl").write_bytes(data)
        stub = d / "tmux"
        stub.write_text('#!/bin/sh\necho "$*" >> %s/tmux.log\n'
                        'case "$1" in display) echo %s;; esac\nexit 0\n' % (d, cwd))
        stub.chmod(0o755)
        env = dict(os.environ, PATH="%s:%s" % (d, os.environ["PATH"]), HOME=str(d),
                   SSH_ORIGINAL_COMMAND=verb)
        r = subprocess.run(["sh", "-c", w._remote_command("zbynek")], env=env,
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=20)
        log = (d / "tmux.log").read_text() if (d / "tmux.log").exists() else ""
        return r, log

    def test_first_read_is_the_newest_transcript(self):
        r, log = self._hist("wt-history 0", b"abc\ndef\n")
        self.assertEqual(r.stdout, b"new.jsonl 8 0\nabc\ndef\n")
        self.assertNotIn("attach-session", log)

    def test_an_offset_reads_only_new_bytes(self):
        r, _ = self._hist("wt-history 4", b"abc\ndef\n")
        self.assertEqual(r.stdout, b"new.jsonl 8 4\ndef\n")

    def test_a_long_tail_is_capped(self):
        size = cap.WEBTERM_HISTORY_FIRST_BYTES + 10
        r, _ = self._hist("wt-history 0", b"x" * size)
        self.assertTrue(r.stdout.startswith(b"new.jsonl %d 10\n" % size))
        self.assertEqual(len(r.stdout.partition(b"\n")[2]), cap.WEBTERM_HISTORY_FIRST_BYTES)

    def test_a_bad_offset_is_refused_and_attaches_nothing(self):
        for verb in ("wt-history ", "wt-history 1;id", "wt-history -1", "wt-history 1 2"):
            r, log = self._hist(verb, b"abc\n")
            self.assertEqual(r.returncode, 2, verb)
            self.assertEqual(r.stdout, b"", verb)
            self.assertNotIn("attach-session", log, verb)


class TestHistoryParse(unittest.TestCase):
    def _line(self, **kw):
        return (json.dumps(kw) + "\n").encode()

    def test_keeps_what_was_typed_and_written(self):
        h = cap.History()
        data = (self._line(type="user", origin={"kind": "human"}, message={"content": "ahoj"})
                + self._line(type="assistant", message={"content": [
                    {"type": "thinking", "thinking": "x"}, {"type": "text", "text": "čau"}]})
                + self._line(type="assistant", message={"content": [{"type": "tool_use"}]})
                + self._line(type="user", message={"content": [{"type": "tool_result"}]})
                + self._line(type="user", origin={"kind": "channel"}, message={"content": "peer"})
                + self._line(type="user", isMeta=True, message={"content": "meta"})
                + self._line(type="user", message={"content": "<command-name>x"})
                + self._line(type="assistant", isSidechain=True,
                             message={"content": [{"type": "text", "text": "sub"}]}))
        self.assertTrue(h.feed(b"a.jsonl %d 0\n" % len(data) + data))
        self.assertEqual(h.msgs, [{"r": "u", "t": "ahoj"}, {"r": "a", "t": "čau"}])

    def test_reads_continue_across_a_split_line(self):
        h = cap.History()
        line = self._line(type="user", message={"content": "jeden"})
        h.feed(b"a.jsonl 5 0\n" + line[:5])
        self.assertEqual(h.msgs, [])
        h.feed(b"a.jsonl %d 5\n" % len(line) + line[5:])
        self.assertEqual(h.msgs, [{"r": "u", "t": "jeden"}])
        self.assertEqual(h.offset, len(line))

    def test_a_new_session_starts_over_and_moves_first(self):
        h = cap.History()
        h.feed(b"a.jsonl 10 0\n" + self._line(type="user", message={"content": "a"}))
        h.feed(b"b.jsonl 10 0\n" + self._line(type="user", message={"content": "b"}))
        self.assertEqual((h.first, h.msgs), (1, [{"r": "u", "t": "b"}]))

    def test_a_tail_drops_its_partial_first_line(self):
        h = cap.History()
        h.feed(b"a.jsonl 99 50\n" + b'ent": "x"}}\n' + self._line(type="user", message={"content": "ok"}))
        self.assertEqual(h.msgs, [{"r": "u", "t": "ok"}])

    def test_a_bad_header_is_a_failure(self):
        self.assertFalse(cap.History().feed(b"garbage\n"))

    def test_the_route_serves_from_since(self):
        async def read(sid, offset):
            line = (json.dumps({"type": "user", "message": {"content": "m"}}) + "\n").encode()
            return b"a.jsonl %d 0\n" % (2 * len(line)) + 2 * line
        r = cap.PhoneReader("inv", g._TAB_ID_RE, history=read)
        body = json.loads(asyncio.run(r.history_body("/phone-history?id=alfa&since=1")))
        self.assertEqual(body, {"ok": True, "first": 0, "msgs": [{"r": "u", "t": "m"}]})
        bad = asyncio.run(r.history_body("/phone-history?id=../x&since=0"))
        self.assertEqual(json.loads(bad), {"ok": False})


class TestCaptureArgv(unittest.TestCase):
    def test_remote_uses_the_tab_key_without_a_pty(self):
        argv, env = cap.build_capture_argv(INVENTORY[1])
        self.assertEqual(env, {})
        self.assertEqual(argv[-2:], ["beta@100.1.1.1", cap.WEBTERM_CAPTURE_VERB])
        self.assertIn("-T", argv)
        self.assertNotIn("-t", argv)
        self.assertIn(os.path.expanduser("~/key"), argv)

    def test_local_runs_the_body_with_the_verb(self):
        argv, env = cap.build_capture_argv(INVENTORY[0])
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertEqual(env, {"SSH_ORIGINAL_COMMAND": cap.WEBTERM_CAPTURE_VERB})

    def test_the_child_becomes_the_ssh_so_a_timeout_kills_it(self):
        # review 🔴: a python parent died on the timeout and left ssh holding the pipe.
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d), True)
        inv = d / "inv.json"
        inv.write_text(json.dumps(INVENTORY))
        with mock.patch.object(os, "execvpe", side_effect=SystemExit(0)) as ex:
            with self.assertRaises(SystemExit):
                cap.capture_main(["beta"], inventory_path=str(inv))
        self.assertEqual(ex.call_args[0][0], "ssh")

    def test_an_id_outside_the_inventory_is_refused(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d), True)
        inv = d / "inv.json"
        inv.write_text(json.dumps(INVENTORY))
        self.assertEqual(cap.capture_main(["gamma"], inventory_path=str(inv)), 2)
        self.assertEqual(cap.capture_main([], inventory_path=str(inv)), 2)


class _Writer:
    def __init__(self):
        self.sent = b""

    def write(self, data):
        self.sent += data

    async def drain(self):
        pass


class TestGatewayRoute(unittest.TestCase):
    HDR = "Cf-Access-Authenticated-User-Email"

    def _gw(self, inventory="inv.json", result=b"text"):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(d), True)
        self.calls = []

        async def capture(sid):
            self.calls.append(sid)
            if isinstance(result, Exception):
                raise result
            return result
        return g.Gateway(str(d / "index.html"), None, "127.0.0.1", 1, "/t", [],
                         trust_access_header=self.HDR, inventory=inventory,
                         phone_capture=capture)

    def _get(self, gw, target, authed=True, method="GET"):
        headers = [(self.HDR, "owner@example.com")] if authed else []
        writer = _Writer()
        asyncio.run(gw._route_phone_read(writer, target, method, headers))
        return writer.sent

    def test_an_unauthenticated_read_is_denied(self):
        gw = self._gw()
        self.assertIn(b"403", self._get(gw, "/phone-read?id=alfa", authed=False).split(b"\r\n")[0])
        self.assertEqual(self.calls, [])

    def test_no_inventory_means_no_route(self):
        gw = self._gw(inventory=None)
        self.assertIn(b"404", self._get(gw, "/phone-read?id=alfa").split(b"\r\n")[0])

    def test_only_get(self):
        self.assertIn(b"404", self._get(self._gw(), "/phone-read?id=alfa", method="POST").split(b"\r\n")[0])

    def test_returns_the_pane(self):
        sent = self._get(self._gw(), "/phone-read?id=alfa")
        self.assertEqual(json.loads(sent.split(b"\r\n\r\n", 1)[1]), {"ok": True, "text": "text"})

    def test_a_bad_id_never_reaches_the_capture(self):
        gw = self._gw()
        for q in ("", "?id=", "?id=../x", "?id=a%20b", "?id=a&id=b", "?id=-x"):
            sent = self._get(gw, "/phone-read" + q)
            self.assertEqual(json.loads(sent.split(b"\r\n\r\n", 1)[1]), {"ok": False}, q)
        self.assertEqual(self.calls, [])

    def test_a_failed_read_is_ok_false_and_not_an_error(self):
        sent = self._get(self._gw(result=None), "/phone-read?id=alfa")
        self.assertIn(b"200", sent.split(b"\r\n")[0])
        self.assertEqual(json.loads(sent.split(b"\r\n\r\n", 1)[1]), {"ok": False})
        sent = self._get(self._gw(result=asyncio.TimeoutError()), "/phone-read?id=alfa")
        self.assertEqual(json.loads(sent.split(b"\r\n\r\n", 1)[1]), {"ok": False})

    def test_a_burst_costs_one_capture(self):
        gw = self._gw()

        async def burst():
            await asyncio.gather(*[gw.phone.text("alfa") for _ in range(5)])
        asyncio.run(burst())
        self.assertEqual(self.calls, ["alfa"])

    def test_a_failing_tab_is_not_retried_at_poll_speed(self):
        gw = self._gw(result=None)

        async def twice():
            await gw.phone.text("alfa")
            gw.phone._cache["alfa"] = (gw.phone._cache["alfa"][0] - 5, None)
            await gw.phone.text("alfa")
        asyncio.run(twice())
        self.assertEqual(self.calls, ["alfa"])

    def test_spawns_are_capped_across_tabs(self):
        peak = [0, 0]

        async def capture(sid):
            peak[0] += 1
            peak[1] = max(peak)
            await asyncio.sleep(0.01)
            peak[0] -= 1
            return b"x"
        gw = self._gw()
        gw.phone.capture = capture

        async def burst():
            await asyncio.gather(*[gw.phone.text("t%d" % i) for i in range(12)])
        asyncio.run(burst())
        self.assertEqual(peak[1], cap.PHONE_READ_MAX_SPAWNS)

    def test_the_id_maps_stay_bounded(self):
        gw = self._gw()

        async def many():
            for i in range(cap.PHONE_READ_MAX_IDS + 5):
                await gw.phone.text("t%d" % i)
        asyncio.run(many())
        self.assertLessEqual(len(gw.phone._locks), cap.PHONE_READ_MAX_IDS + 1)
        self.assertLessEqual(len(gw.phone._cache), cap.PHONE_READ_MAX_IDS + 1)

    def test_units_pass_their_own_inventory(self):
        self.assertIn("--inventory " + str(w.WEBTERM_INVENTORY_PATH),
                      w._render_webterm_gateway_unit("127.0.0.1", access_mode=True))


class TestLeaf(unittest.TestCase):
    def test_pane_text_is_never_markup(self):
        self.assertNotIn("innerHTML", phone.PHONE_HTML)
        self.assertIn("screen.textContent = text", phone.PHONE_HTML)
        self.assertIn("div.textContent = m.t", phone.PHONE_HTML)

    def test_off_unless_the_gateway_has_the_route(self):
        html = w.render_dashboard_html(INVENTORY, ttyd_base="/t")
        self.assertIn('"phone_read": false', html.replace('"phone_read":false', '"phone_read": false'))
        self.assertIn("if (!CFG.phone_read", html)


_DRIVER = r"""
const [,, pwPath, chromium, BASE] = process.argv;
const { chromium: cr } = require(pwPath);
(async () => {
  const b = await cr.launch({ executablePath: chromium, args: ['--no-sandbox'] });
  const out = { console: [] };
  const watch = (p) => { p.on('console', m => { if (['error', 'warning'].includes(m.type())) out.console.push(m.text()); }); p.on('pageerror', e => out.console.push(String(e))); };
  const ph = await b.newContext({ viewport: { width: 390, height: 780 }, hasTouch: true, isMobile: true });
  const p = await ph.newPage(); watch(p);
  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForFunction(() => document.getElementById('phonelog').textContent.includes('tab0'));
  await p.waitForFunction(() => { const f = made[0]; return f && f.contentWindow && f.contentWindow.term; });
  const log = () => p.evaluate(() => document.getElementById('phonelog').textContent);
  out.text = await log();
  out.hist = await p.evaluate(() => [...document.querySelectorAll('#phonehist .m')].map(e => [e.className, e.textContent]));
  out.layout = await p.evaluate(() => { const r = (s) => document.querySelector(s).getBoundingClientRect();
    const l = document.getElementById('phonelog');
    return { on: getComputedStyle(document.getElementById('phone')).display, docW: document.documentElement.scrollWidth,
      vw: innerWidth, tabsTop: r('#tabbar').top, barBottom: r('#keybar').bottom, vh: innerHeight, atEnd: l.scrollHeight - l.scrollTop - l.clientHeight,
      font: parseFloat(getComputedStyle(l).fontSize), screenOver: document.getElementById('phonescreen').scrollWidth - document.getElementById('phonescreen').clientWidth, scrolls: l.scrollHeight > l.clientHeight, meta: document.querySelector('meta[name=viewport]').content }; });
  await p.evaluate(() => { const l = document.getElementById('phonelog'); l.scrollTop = 0; });
  await p.waitForSelector('#phonedown.on');
  await p.waitForTimeout(1800);
  out.keptScroll = await p.evaluate(() => document.getElementById('phonelog').scrollTop);
  await p.tap('#phonedown');
  await p.tap('.tab[data-idx="1"]');
  await p.waitForFunction(() => document.getElementById('phonelog').textContent.includes('tab1'));
  await p.fill('#phonebox', 'ahoj <b>');
  await p.tap('#phonesend');
  out.sent = await p.evaluate(() => { const t = made[1].contentWindow; return { pastes: t.__pastes || [], sent: t.__sent || [] }; });
  out.boxAfter = await p.inputValue('#phonebox');
  await p.tap('#phonetoggle');
  out.terminal = await p.evaluate(() => getComputedStyle(document.getElementById('phone')).display);
  await p.tap('#phonetoggle');
  out.text2 = await p.evaluate(() => getComputedStyle(document.getElementById('phone')).display);
  await p.tap('.tab[data-idx="2"]');
  await p.waitForSelector('#phonestate.on');
  out.failedTabText = await log();
  await p.tap('.tab[data-idx="0"]');
  await p.waitForFunction(() => document.getElementById('phonelog').textContent.includes('tab0'));
  await p.evaluate(() => document.querySelector('.tab[data-idx="0"]').classList.add('wt-offline'));
  await p.fill('#phonebox', 'zostan');
  await p.tap('#phonesend');
  out.offline = { box: await p.inputValue('#phonebox'),
    pastes: await p.evaluate(() => made[0].contentWindow.__pastes.length) };
  await p.evaluate(() => document.querySelector('.tab[data-idx="0"]').classList.remove('wt-offline'));
  await p.setViewportSize({ width: 390, height: 420 });
  await p.waitForTimeout(100);
  out.keyboardGap = await p.evaluate(() => { const l = document.getElementById('phonelog'); return l.scrollHeight - l.scrollTop - l.clientHeight; });
  await ph.close();
  const dk = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const d = await dk.newPage(); watch(d);
  await d.goto(BASE + '/', { waitUntil: 'load' });
  await d.waitForTimeout(500);
  out.desktop = await d.evaluate(() => ({ display: getComputedStyle(document.getElementById('phone')).display, on: !!window.wtPhoneOn,
    meta: document.querySelector('meta[name=viewport]').content }));
  out.desktopReads = await d.evaluate(() => performance.getEntriesByType('resource').filter(e => e.name.includes('phone-read')).length);
  await b.close();
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error('DRIVER-ERR', e && e.stack || e); process.exit(1); });
"""

_STUB = """<!DOCTYPE html><html><body><textarea class="xterm-helper-textarea"></textarea><script>
window.__sent = []; window.__pastes = [];
window.term = { options: {}, modes: {}, cols: 176, rows: 51, element: document.body,
  focus() {}, resize() {}, paste(t) { window.__pastes.push(t); },
  input(d) { window.__sent.push(d); }, onSelectionChange() {}, parser: {}, getSelection() { return ''; } };
</script></body></html>"""


class _Handler(kb._QuietHandler):
    def do_GET(self):
        if self.path.startswith("/phone-history"):
            sid = self.path.split("id=", 1)[1].split("&", 1)[0]
            since = int(self.path.split("since=", 1)[1])
            msgs = [{"r": "u", "t": "otázka <i>%s</i>" % sid}, {"r": "a", "t": "odpoveď " + sid}]
            return self._json({"ok": True, "first": 0, "msgs": msgs[since:]})
        if not self.path.startswith("/phone-read"):
            return super().do_GET()
        sid = self.path.split("id=", 1)[1]
        if sid == INV3[2]["id"]:
            body = {"ok": False}
        else:
            idx = [e["id"] for e in INV3].index(sid)
            wide = "x" * 174
            body = {"ok": True, "text": "tab%d\n<b>raw</b>\n%s\npokračovanie\n%s\n%s\n%s\n\n\n" % (
                idx, wide, "slovo " * 60, "─" * 176, "\n".join("riadok %d" % i for i in range(120)))}
        self._json(body)

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


INV3 = [dict(INVENTORY[0]), dict(INVENTORY[1]), dict(INVENTORY[1], id="gama", preferred="gama")]


class TestBrowser(unittest.TestCase):
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
            w.render_dashboard_html(INV3, ttyd_base="/t", phone_read=True), encoding="utf-8")
        kb.pwa.write_pwa_assets(d, "zbynek")
        (d / "t").mkdir()
        (d / "t" / "index.html").write_text(_STUB, encoding="utf-8")
        (d / "driver.js").write_text(_DRIVER, encoding="utf-8")
        srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_Handler, directory=str(d)))
        cls.addClassCleanup(srv.server_close)
        cls.addClassCleanup(srv.shutdown)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        r = subprocess.run([node, str(d / "driver.js"), pw, chromium,
                            "http://127.0.0.1:%d" % srv.server_address[1]],
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise AssertionError("driver failed:\n%s\n%s" % (r.stdout, r.stderr))
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_text_is_readable_on_a_phone_without_zoom(self):
        lay = self.out["layout"]
        self.assertEqual(lay["on"], "flex")
        self.assertLessEqual(lay["docW"], lay["vw"])
        self.assertGreaterEqual(lay["font"], 13)
        self.assertIn("user-scalable=no", lay["meta"])
        self.assertLessEqual(lay["screenOver"], 0)       # a long line wraps, never clips

    def test_tabs_and_keys_stay_on_screen(self):
        lay = self.out["layout"]
        self.assertEqual(lay["tabsTop"], 0)
        self.assertAlmostEqual(lay["barBottom"], lay["vh"], delta=1)

    def test_opens_at_the_newest_text_and_scrolls(self):
        self.assertTrue(self.out["layout"]["scrolls"])
        self.assertLess(self.out["layout"]["atEnd"], 2)

    def test_a_poll_keeps_the_place_in_history(self):
        self.assertEqual(self.out["keptScroll"], 0)

    def test_pane_text_stays_text(self):
        self.assertIn("<b>raw</b>", self.out["text"])

    def test_history_sits_above_the_screen_as_text(self):
        self.assertEqual(self.out["hist"], [["m u", "otázka <i>alfa</i>"], ["m a", "odpoveď alfa"]])
        self.assertLess(self.out["text"].index("odpoveď alfa"), self.out["text"].index("tab0"))

    def test_tidy_rejoins_and_cuts_rules(self):
        self.assertIn("x" * 174 + " pokračovanie", self.out["text"])
        self.assertIn("\n" + "─" * 24 + "\n", self.out["text"])

    def test_send_goes_through_the_tab_xterm(self):
        self.assertEqual(self.out["sent"], {"pastes": ["ahoj <b>"], "sent": ["\r"]})
        self.assertEqual(self.out["boxAfter"], "")

    def test_the_toggle_shows_the_terminal_and_returns(self):
        self.assertEqual(self.out["terminal"], "none")
        self.assertEqual(self.out["text2"], "flex")

    def test_the_desktop_stays_as_it_is(self):
        self.assertEqual(self.out["desktop"]["display"], "none")
        self.assertFalse(self.out["desktop"]["on"])
        self.assertNotIn("user-scalable", self.out["desktop"]["meta"])
        self.assertEqual(self.out["desktopReads"], 0)

    def test_a_failed_tab_never_shows_the_previous_tab(self):
        self.assertNotIn("tab1", self.out["failedTabText"])
        self.assertNotIn("beta", self.out["failedTabText"])

    def test_a_dead_socket_keeps_the_typed_text(self):
        self.assertEqual(self.out["offline"], {"box": "zostan", "pastes": 0})

    def test_the_keyboard_keeps_the_newest_text_in_view(self):
        self.assertLess(self.out["keyboardGap"], 2)

    def test_console_is_clean(self):
        self.assertEqual([c for c in self.out["console"] if "GL Driver" not in c and "GPU stall" not in c], [])


if __name__ == "__main__":
    unittest.main()
