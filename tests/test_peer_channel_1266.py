"""#1266 — cross-account peer channel (peer_channel.py runtime + cli_peer_channel managed side)."""
import inspect
import io
import json
import os
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_peer_channel as cpc  # noqa: E402
import peer_channel as pc  # noqa: E402

ME = pwd.getpwuid(os.getuid()).pw_name
OTHER = "root" if ME != "root" else "daemon"
HOST = "testhost"


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, **fields):
        self.events.append((event, fields))

    def names(self):
        return [e for e, _ in self.events]


def _write_config(d, links, topics=None):
    reg = [{"name": "alpha", "host": HOST, "account": ME, "path": "~/devel/alpha"},
           {"name": "beta", "host": HOST, "account": ME, "path": "~/devel/beta"},
           {"name": "gamma", "host": HOST, "account": ME, "path": "~/devel/gamma"},
           {"name": "foreign", "host": HOST, "account": OTHER, "path": "/srv/foreign"},
           {"name": "elsewhere", "host": "otherhost", "account": ME, "path": "~/x"}]
    Path(d, pc.REGISTRY_FILE).write_text(json.dumps(reg))
    Path(d, pc.LINKS_FILE).write_text(json.dumps({"hosts": {HOST: {"links": links, "topics": topics or {}}}}))


def _scratch(testcase, prefix):
    # AF_UNIX paths are capped at 108 bytes: keep socket dirs short, under /tmp.
    d = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    testcase.addCleanup(lambda: subprocess.run(["rm", "-rf", d], check=False))
    return d


class _Env(unittest.TestCase):
    LINKS = [["alpha", "beta"], ["alpha", "foreign"]]

    def setUp(self):
        self.home = _scratch(self, "pch")
        self.cfg = _scratch(self, "pcc")
        self.sock = _scratch(self, "pcs")
        os.makedirs(os.path.join(self.sock, ME))
        _write_config(self.cfg, self.LINKS)

    def peer(self, project, **kw):
        delivered = []
        log = _Recorder()
        kw.setdefault("armed", True)
        p = pc.Peer(HOST, ME, os.getuid(), "/", self.home, project_arg=project,
                    sock_dir=self.sock, config_dir=self.cfg, log=log,
                    notify=lambda text, meta: delivered.append((text, meta)), **kw)
        p.delivered, p.rec = delivered, log
        self.addCleanup(p.close)
        return p


class TestConfig(unittest.TestCase):
    def test_a_link_without_a_topic_is_refused(self):
        reg = [{"name": "a", "host": "h", "path": "/a", "account": "a"},
               {"name": "b", "host": "h", "path": "/b", "account": "b"}]
        for topics in (None, {}, {"a|b": " "}, {"a|b": "x" * 201}, {"b|a": "wrong order"}):
            entry = {"links": [["a", "b"]]}
            if topics is not None:
                entry["topics"] = topics
            errs = cpc.validate_links({"hosts": {"h": entry}}, reg, root_hosts={"h"})
            self.assertTrue(any("topic" in e for e in errs), (topics, errs))

    def test_a_reader_of_bare_pairs_keeps_working(self):
        # #1270 review: running sessions keep old code; the link list stays bare pairs
        for link in cpc.load_links()["hosts"]["dev1"]["links"]:
            self.assertIsInstance(link, list)

    def test_committed_links_valid_against_registry_and_root_hosts(self):
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])

    def test_validate_links_rejects_unknown_self_wrong_host_and_non_root_host(self):
        reg = [{"name": "a", "host": "h", "account": "u", "path": "/a"},
               {"name": "b", "host": "other", "account": "u", "path": "/b"}]
        doc = {"hosts": {"h": {"links": [["a", "a"], ["a", "b"], ["a"]]},
                         "nope": {"links": []}}}
        errs = cpc.validate_links(doc, reg, root_hosts={"h"})
        self.assertTrue(any("self link" in e for e in errs))
        self.assertTrue(any("'b' is not a registry project on h" in e for e in errs))
        self.assertTrue(any("malformed" in e for e in errs))
        self.assertTrue(any(e.startswith("nope: not a root-leg host") for e in errs))

    def test_accounts_for_host(self):
        reg = [{"name": "a", "host": "h", "account": "ua", "path": "/a"},
               {"name": "b", "host": "h", "account": "ub", "path": "/b"},
               {"name": "c", "host": "h", "account": "uc", "path": "/c"}]
        doc = {"hosts": {"h": {"links": [["a", "b"]], "topics": {"a|b": "t"}}}}
        self.assertEqual(cpc.accounts_for_host("h", doc, reg), ["ua", "ub"])
        self.assertEqual(cpc.accounts_for_host("x", doc, reg), [])

    def test_resolve_project_longest_path_own_account_only(self):
        projects = {"outer": {"account": "u", "path": "~/devel"},
                    "inner": {"account": "u", "path": "~/devel/inner"},
                    "theirs": {"account": "v", "path": "~/devel/inner/deep"}}
        self.assertEqual(pc.resolve_project(projects, "u", "/h/devel/inner/deep/x", "/h"), "inner")
        self.assertEqual(pc.resolve_project(projects, "u", "/h/devel/other", "/h"), "outer")
        self.assertIsNone(pc.resolve_project(projects, "u", "/h/devels", "/h"))

    def test_resolve_project_follows_a_symlinked_cwd(self):
        d = _scratch(self, "pcr")
        os.makedirs(os.path.join(d, "devel", "inner"))
        os.symlink(os.path.join(d, "devel", "inner"), os.path.join(d, "link"))
        projects = {"inner": {"account": "u", "path": "~/devel/inner"}}
        self.assertEqual(pc.resolve_project(projects, "u", os.path.join(d, "link"), d), "inner")

    def test_project_arg_must_belong_to_this_account(self):
        d = _scratch(self, "pca")
        _write_config(d, [])
        p = pc.Peer(HOST, ME, os.getuid(), "/", d, project_arg="foreign",
                    sock_dir=d, config_dir=d, log=_Recorder())
        self.assertIsNone(p.project)
        self.assertIn("not a testhost project", p.problem)

    def test_mcp_json_runs_the_fleet_checkout(self):
        import cli_fleet
        args = json.loads(cpc.MCP_JSON_PATH.read_text())["mcpServers"]["peer"]["args"]
        self.assertIn('"$HOME/devel/airuleset/peer_channel.py"', args[-1])
        hosts = {h["host"] for h in cli_fleet.DISK_GUARD_ROOT_HOSTS
                 if h["name"] in cpc.load_links()["hosts"]}
        for r in cli_fleet.REMOTE_HOSTS:
            if r.get("host") in hosts:
                self.assertEqual(r.get("repo_path"), "~/devel/airuleset", r["name"])


class TestChannelArmed(unittest.TestCase):
    def _pid_with(self, *extra):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"] + list(extra))
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)      # LIFO: kill first, then reap
        # /proc/<pid>/cmdline reads empty for a moment right after exec; a real
        # parent claude has long been running when its MCP server starts.
        deadline = time.monotonic() + 5
        while not Path("/proc/%d/cmdline" % p.pid).read_bytes() and time.monotonic() < deadline:
            time.sleep(0.01)
        return p.pid

    def test_armed_only_with_our_channel_flag(self):
        self.assertTrue(pc.channel_armed(self._pid_with("--channels", pc.CHANNEL_ARG)))
        self.assertTrue(pc.channel_armed(self._pid_with("--channels", "x", pc.CHANNEL_ARG)))
        self.assertTrue(pc.channel_armed(self._pid_with("--channels=" + pc.CHANNEL_ARG)))
        self.assertFalse(pc.channel_armed(self._pid_with("--channels", "plugin:other@x")))
        self.assertFalse(pc.channel_armed(self._pid_with("--model", pc.CHANNEL_ARG)))
        self.assertFalse(pc.channel_armed(self._pid_with()))

    def test_not_armed_session_never_listens(self):
        env = _Env("setUp")
        env.setUp()
        try:
            p = env.peer("beta", armed=False)
            self.assertFalse(p.start_listener())
            self.assertIn("listen-skipped", p.rec.names())
            self.assertEqual(os.listdir(os.path.join(env.sock, ME)), [])
        finally:
            env.doCleanups()


class TestDelivery(_Env):
    def test_linked_message_delivered_as_channel_event(self):
        a, b = self.peer("alpha"), self.peer("beta")
        self.assertTrue(b.start_listener())
        ok, out = a.send("beta", "ahoj z alpha")
        self.assertTrue(ok, out)
        self.assertEqual(b.delivered, [("ahoj z alpha", {"from": "alpha"})])
        self.assertIn("deliver", b.rec.names())
        self.assertIn("send", a.rec.names())

    def test_socket_in_own_subdir_mode_and_cleanup(self):
        b = self.peer("beta")
        b.start_listener()
        path = b.sock_path
        self.assertEqual(os.path.dirname(path), os.path.join(self.sock, ME))
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o666)
        b.close()
        self.assertFalse(os.path.lexists(path))

    def test_project_without_link_does_not_listen(self):
        g = self.peer("gamma")
        self.assertFalse(g.start_listener())
        self.assertEqual(g.rec.events[-1], ("listen-skipped", {"project": "gamma",
                                                                "reason": "no link"}))

    def test_missing_own_dir_is_reported_not_fatal(self):
        os.rmdir(os.path.join(self.sock, ME))
        p = self.peer("alpha")
        self.assertFalse(p.start_listener())
        self.assertIn("root leg not applied", p.problem)

    def test_unlinked_pair_refused_both_ends(self):
        b = self.peer("beta")
        ok, out = b.send("gamma", "hi")
        self.assertFalse(ok)
        self.assertIn("not linked", out)
        a = self.peer("alpha")
        reply = a.handle_request({"v": 1, "op": "msg", "from": "gamma", "to": "alpha",
                                  "text": "x"}, os.getuid())
        self.assertFalse(reply["ok"])
        self.assertIn("no link", reply["error"])
        self.assertEqual(a.delivered, [])

    def test_sender_spoofing_another_accounts_project_refused(self):
        a = self.peer("alpha")
        reply = a.handle_request({"v": 1, "op": "msg", "from": "foreign", "to": "alpha",
                                  "text": "spoof"}, os.getuid())
        self.assertFalse(reply["ok"])
        self.assertIn("not a testhost project of %s" % ME, reply["error"])
        self.assertEqual(a.delivered, [])

    def test_genuine_other_account_sender_accepted_when_linked(self):
        a = self.peer("alpha")
        reply = a.handle_request({"v": 1, "op": "msg", "from": "foreign", "to": "alpha",
                                  "text": "real"}, pwd.getpwnam(OTHER).pw_uid)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(a.delivered, [("real", {"from": "foreign"})])

    def test_wrong_target_and_unknown_uid_refused(self):
        a = self.peer("alpha")
        self.assertIn("wrong target", a.handle_request(
            {"v": 1, "op": "msg", "from": "beta", "to": "gamma", "text": "x"},
            os.getuid())["error"])
        self.assertIn("unknown uid", a.handle_request(
            {"v": 1, "op": "msg", "from": "beta", "to": "alpha", "text": "x"}, 2 ** 31 - 7)["error"])

    def test_target_dir_not_owned_by_target_account_is_never_connected(self):
        # a dir named after OTHER's account but owned by ME (a planted namespace)
        os.makedirs(os.path.join(self.sock, OTHER))
        planted = os.path.join(self.sock, OTHER, pc.socket_name("foreign", 4242))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(planted)
        srv.listen(1)
        srv.settimeout(0.5)
        self.addCleanup(srv.close)
        ok, out = self.peer("alpha").send("foreign", "secret text")
        self.assertFalse(ok)
        self.assertIn("no live session", out)
        with self.assertRaises(socket.timeout):
            srv.accept()

    def test_listener_uid_mismatch_refused_before_text_is_sent(self):
        b = self.peer("beta")
        b.start_listener()
        a = self.peer("alpha")
        r = a._call(b.sock_path, pwd.getpwnam(OTHER).pw_uid,
                    {"v": 1, "op": "msg", "from": "alpha", "to": "beta", "text": "x"},
                    time.monotonic() + 3)
        self.assertEqual(r, {"ok": False, "error": "impostor socket"})
        self.assertEqual(b.delivered, [])

    def test_symlink_entry_skipped(self):
        b = self.peer("beta")
        b.start_listener()
        link = os.path.join(self.sock, ME, pc.socket_name("beta", 7))
        os.symlink(b.sock_path, link)
        a = self.peer("alpha")
        self.assertEqual(a._target_sockets("beta", os.getuid(), ME), [b.sock_path])

    def test_rate_limit(self):
        now = [0.0]
        a = self.peer("alpha", clock=lambda: now[0])
        req = {"v": 1, "op": "msg", "from": "beta", "to": "alpha", "text": "x"}
        for _ in range(pc.RATE_MAX):
            self.assertTrue(a.handle_request(dict(req), os.getuid())["ok"])
        self.assertIn("rate limit", a.handle_request(dict(req), os.getuid())["error"])
        now[0] = pc.RATE_WINDOW_S + 1
        self.assertTrue(a.handle_request(dict(req), os.getuid())["ok"])

    def test_text_limits_and_control_chars(self):
        a, b = self.peer("alpha"), self.peer("beta")
        b.start_listener()
        self.assertFalse(a.send("beta", "x" * (pc.MAX_TEXT + 1))[0])
        self.assertFalse(a.send("beta", "   ")[0])
        self.assertTrue(a.send("beta", "ok\x1b[31mred\x07\nline2")[0])
        self.assertEqual(b.delivered[-1][0], "ok[31mred\nline2")

    def test_a_long_reply_is_one_message(self):
        # #1269 owner (30.10.): songplayer split a 4000+ char answer into parts
        a, b = self.peer("alpha"), self.peer("beta")
        b.start_listener()
        text = "stav bridge ľščťž " * 2000          # 36000 chars, non-ASCII escapes
        self.assertTrue(a.send("beta", text)[0])
        self.assertEqual(b.delivered[-1][0], text)

    def test_peers_lists_live_and_offline(self):
        a, b = self.peer("alpha"), self.peer("beta")
        ok, out = a.list_peers()
        self.assertTrue(ok)
        self.assertIn("beta: offline", out)
        b.start_listener()
        ok, out = a.list_peers()
        self.assertIn("beta: live (1)", out)
        self.assertIn("foreign: offline", out)
        self.assertEqual(b.delivered, [])

    def test_send_retries_listener_lazily(self):
        os.rmdir(os.path.join(self.sock, ME))
        a = self.peer("alpha")
        self.assertFalse(a.start_listener())
        os.makedirs(os.path.join(self.sock, ME))
        a.send("beta", "x")
        self.assertIsNotNone(a.listener)

    def test_stale_own_socket_swept(self):
        dead = os.path.join(self.sock, ME, pc.socket_name("beta", 2 ** 22 + 12345))
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(dead)
        s.close()
        self.peer("beta").start_listener()
        self.assertFalse(os.path.lexists(dead))


class TestListenerHardening(_Env):
    def _connect(self, path):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(path)
        self.addCleanup(s.close)
        return s

    def test_connection_cap(self):
        b = self.peer("beta")
        b.start_listener()
        idle = [self._connect(b.sock_path) for _ in range(pc.MAX_CONNS)]
        time.sleep(0.3)
        extra = self._connect(b.sock_path)
        self.assertEqual(extra.recv(10), b"")        # closed at once, no thread
        self.assertIn("too many connections",
                      [f.get("reason") for _, f in b.rec.events])
        self.assertEqual(len(idle), pc.MAX_CONNS)

    def test_slow_drip_bounded_by_one_overall_deadline(self):
        with mock.patch.object(pc, "IO_TIMEOUT_S", 0.6):
            b = self.peer("beta")
            b.start_listener()
            s = self._connect(b.sock_path)
            t0 = time.monotonic()
            for _ in range(5):
                try:
                    s.sendall(b" ")
                except OSError:
                    break
                time.sleep(0.25)
            reply = s.recv(200)
            self.assertLess(time.monotonic() - t0, 2.5)
            self.assertIn(b"bad request", reply)

    def test_unlinked_uid_closed_before_reading(self):
        _write_config(self.cfg, [["beta", "foreign"]])   # ME's uid owns no linked project
        b = self.peer("beta")
        b.start_listener()
        s = self._connect(b.sock_path)
        self.assertEqual(s.recv(100), b"")       # closed by the listener, nothing read
        self.assertEqual(b.delivered, [])
        self.assertIn("uid not linked", [f.get("reason") for _, f in b.rec.events])

    def test_deep_json_answered_not_crashed(self):
        b = self.peer("beta")
        b.start_listener()
        s = self._connect(b.sock_path)
        s.sendall(b"[" * 20000 + b"\n")
        self.assertIn(b"bad request", s.recv(200))

    def test_oversized_line_refused(self):
        b = self.peer("beta")
        b.start_listener()
        s = self._connect(b.sock_path)
        try:
            s.sendall(b"x" * (pc.MAX_WIRE + 10000))
        except OSError:
            pass
        self.assertIn(b"bad request", s.recv(200))

    def test_log_survives_lone_surrogate(self):
        path = os.path.join(self.home, "l", "peer.log")
        pc.Log(path)("deliver", preview="\ud800x")
        self.assertIn("\\ud800x", Path(path).read_text())


class TestMcp(unittest.TestCase):
    def _server(self):
        d = _scratch(self, "pcm")
        _write_config(d, [])
        peer = pc.Peer(HOST, ME, os.getuid(), "/", d, project_arg="alpha",
                       sock_dir=d, config_dir=d, log=_Recorder())
        out = io.StringIO()
        return pc.McpServer(peer, out=out), out

    @staticmethod
    def _msgs(out):
        return [json.loads(x) for x in out.getvalue().splitlines()]

    def test_handshake_declares_channel_and_stays_legacy_era(self):
        srv, out = self._server()
        srv.handle({"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": {}})
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25"}})
        srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        m = self._msgs(out)
        self.assertEqual(m[0]["error"]["code"], -32601)
        res = m[1]["result"]
        self.assertEqual(res["capabilities"]["experimental"], {"claude/channel": {}})
        self.assertEqual(res["protocolVersion"], "2025-11-25")
        self.assertLess(len(res["instructions"]), 300)
        self.assertIn("not from the owner", res["instructions"])
        self.assertEqual([t["name"] for t in m[2]["result"]["tools"]], ["peers", "peer_send"])

    def test_messages_before_initialized_are_bounded(self):
        # #1269 review: at 100k chars per message the pre-handshake buffer is capped
        srv, out = self._server()
        for i in range(pc.PENDING_MAX + 10):
            srv.channel_event("m%d" % i, {})
        srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        got = [m["params"]["content"] for m in self._msgs(out)]
        self.assertEqual(len(got), pc.PENDING_MAX)
        self.assertEqual(got[-1], "m%d" % (pc.PENDING_MAX + 9))     # the newest are kept

    def test_instructions_name_each_link_and_its_topic(self):
        # #1270 owner: a project must know who it may talk to and about what
        d = _scratch(self, "pct")
        _write_config(d, [["alpha", "beta"], ["alpha", "gamma"]],
                      {"alpha|beta": "clock contract", "alpha|gamma": "shared PC restarts"})
        peer = pc.Peer(HOST, ME, os.getuid(), "/", d, project_arg="alpha",
                       sock_dir=d, config_dir=d, log=_Recorder())
        out = io.StringIO()
        srv = pc.McpServer(peer, out=out)
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}})
        text = self._msgs(out)[0]["result"]["instructions"]
        self.assertIn("not from the owner", text)
        self.assertIn("beta: clock contract", text)
        self.assertIn("gamma: shared PC restarts", text)
        self.assertIn("no other project", text)
        self.assertLess(len(text), 2000)
        ok, listed = peer.list_peers()
        self.assertTrue(ok)
        self.assertIn("clock contract", listed)

    def test_unknown_protocol_falls_back(self):
        srv, out = self._server()
        srv.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2026-07-28"}})
        self.assertEqual(self._msgs(out)[0]["result"]["protocolVersion"], pc.DEFAULT_PROTOCOL)

    def test_channel_event_buffered_until_initialized(self):
        srv, out = self._server()
        srv.channel_event("early", {"from": "beta"})
        self.assertEqual(out.getvalue(), "")
        srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        srv.channel_event("late", {"from": "beta"})
        m = self._msgs(out)
        self.assertEqual([x["params"]["content"] for x in m], ["early", "late"])
        self.assertEqual(m[0]["method"], "notifications/claude/channel")
        self.assertEqual(m[0]["params"]["meta"], {"from": "beta"})

    def test_tools_call_errors_are_flagged(self):
        srv, out = self._server()
        srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "peer_send", "arguments": {"to": "gamma", "text": "x"}}})
        r = self._msgs(out)[0]["result"]
        self.assertTrue(r["isError"])
        self.assertIn("not linked", r["content"][0]["text"])

    def test_real_process_stdio_roundtrip(self):
        proc = subprocess.run(
            [sys.executable, "-I", str(REPO / "peer_channel.py"), "serve"],
            input="\n".join(json.dumps(x) for x in [
                {"jsonrpc": "2.0", "id": "p", "method": "server/discover"},
                {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": "peers", "arguments": {}}}]) + "\n",
            capture_output=True, text=True, timeout=30, cwd="/",
            env={**os.environ, "HOME": _scratch(self, "pcp")})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        m = [json.loads(x) for x in proc.stdout.splitlines()]
        self.assertEqual([x.get("id") for x in m], ["p", 0, 1])
        self.assertIn("claude/channel", m[1]["result"]["capabilities"]["experimental"])
        self.assertTrue(m[2]["result"]["isError"])  # "/" is no registered project


class TestRootScript(unittest.TestCase):
    def test_policy_keeps_official_channels_and_adds_peer(self):
        pol = json.loads(cpc.render_policy())
        self.assertTrue(pol["channelsEnabled"])
        pairs = {(e["marketplace"], e["plugin"]) for e in pol["allowedChannelPlugins"]}
        self.assertIn(("airuleset", "peer"), pairs)
        self.assertIn(("claude-plugins-official", "telegram"), pairs)

    def test_mount_unit_is_a_small_tmpfs(self):
        unit = cpc.render_mount_unit()
        self.assertIn("Where=/run/claude-peers", unit)
        self.assertIn("size=1m", unit)
        self.assertIn("nr_inodes=1024", unit)

    def test_bad_or_empty_account_set_refused(self):
        with self.assertRaises(ValueError):
            cpc.render_root_script(["x; rm -rf /"])
        with self.assertRaises(ValueError):
            cpc.render_root_script([])

    def _fake_root(self, accounts, extra_acl=""):
        """Execute the REAL rendered script against a scratch root: real setfacl/
        getfacl on a scratch dir; only root-only tools are stubbed (#1223 lesson)."""
        root = _scratch(self, "pcf")
        script = cpc.render_root_script(accounts)
        script = (script.replace("/etc/", root + "/etc/")
                  .replace(pc.SOCK_DIR, root + "/run/claude-peers")
                  .replace("cd /\n", "cd %s\n" % root))
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        log = os.path.join(root, "calls.log")
        stubs = {
            "id": "#!/bin/sh\nexit 0\n",
            "systemctl": "#!/bin/sh\necho \"systemctl $*\" >> %s\n" % log,
            "findmnt": "#!/bin/sh\necho tmpfs\n",
            "systemd-tmpfiles": (
                "#!/bin/sh\nset -e\necho \"tmpfiles $*\" >> %s\nconf=\"$2\"\n"
                "dir=$(awk '$1==\"d\"{print $2; exit}' \"$conf\")\nmkdir -p \"$dir\"\n"
                "chmod 0750 \"$dir\"\n"
                "setfacl --set \"$(awk '$1==\"a\"{print $7}' \"$conf\")\" \"$dir\"\n"
                "%s"
                "awk 'NR>2 && $1==\"d\"{print $2}' \"$conf\" | while read -r s; do "
                "mkdir -p \"$s\"; chmod 0755 \"$s\"; done\n"
                % (log, ("setfacl -m %s \"$dir\"\n" % extra_acl) if extra_acl else "")),
            "stat": ("#!/bin/sh\ncase \"$2\" in\n"
                     "  '%U:%G %a') echo \"root:root $(/usr/bin/stat -c %a \"$3\")\" ;;\n"
                     "  '%U %a') echo \"$(basename \"$3\") $(/usr/bin/stat -c %a \"$3\")\" ;;\n"
                     "  *) exec /usr/bin/stat \"$@\" ;;\nesac\n"),
        }
        for name, body in stubs.items():
            p = os.path.join(bindir, name)
            Path(p).write_text(body)
            os.chmod(p, 0o755)
        env = {**os.environ, "PATH": bindir + ":" + os.environ["PATH"]}
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        return r, root, log

    def test_rendered_script_applies_and_passes_strict_readback(self):
        r, root, log = self._fake_root([ME, OTHER])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("peer-channel-root: ok", r.stdout)
        pol = json.loads(Path(root + cpc.POLICY_PATH).read_text())
        self.assertIn({"marketplace": "airuleset", "plugin": "peer"}, pol["allowedChannelPlugins"])
        self.assertIn("u:%s:r-x" % ME, Path(root + cpc.TMPFILES_PATH).read_text())
        self.assertEqual(stat.S_IMODE(os.stat(root + cpc.POLICY_PATH).st_mode), 0o644)
        calls = Path(log).read_text()
        self.assertIn("daemon-reload", calls)
        self.assertIn("enable --now run-claude\\x2dpeers.mount", calls)
        self.assertEqual([x for x in os.listdir(root + "/etc/tmpfiles.d") if x.startswith(".")], [])

    def test_second_run_rewrites_nothing_and_skips_tmpfiles(self):
        r, root, log = self._fake_root([ME, OTHER])
        script = (cpc.render_root_script([ME, OTHER]).replace("/etc/", root + "/etc/")
                  .replace(pc.SOCK_DIR, root + "/run/claude-peers")
                  .replace("cd /\n", "cd %s\n" % root))
        Path(log).write_text("")
        env = {**os.environ, "PATH": os.path.join(root, "bin") + ":" + os.environ["PATH"]}
        r2 = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("changed=[]", r2.stdout)
        self.assertNotIn("tmpfiles", Path(log).read_text())

    def test_readback_fails_on_extra_acl_entry(self):
        r, _, _ = self._fake_root([ME], extra_acl="u:%s:r-x" % OTHER)
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("ACL mismatch", r.stderr)

    def test_teardown_removes_only_our_files(self):
        root = _scratch(self, "pct")
        script = (cpc.render_teardown_script().replace("/etc/", root + "/etc/")
                  .replace("cd /\n", "cd %s\n" % root))
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        Path(bindir, "systemctl").write_text("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(bindir, "systemctl"), 0o755)
        env = {**os.environ, "PATH": bindir + ":" + os.environ["PATH"]}
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertIn("nothing to tear down", r.stdout)
        for p in (cpc.TMPFILES_PATH, cpc.POLICY_PATH, cpc.MOUNT_UNIT_PATH):
            os.makedirs(os.path.dirname(root + p), exist_ok=True)
            Path(root + p).write_text("x")
        Path(root, "etc", "keep.conf").write_text("x")
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        for p in (cpc.TMPFILES_PATH, cpc.POLICY_PATH, cpc.MOUNT_UNIT_PATH):
            self.assertFalse(os.path.exists(root + p))
        self.assertTrue(Path(root, "etc", "keep.conf").exists())

    def _hosts(self):
        return [{"name": "h1", "host": "1.2.3.4", "admin_user": "newlevel", "sudo": True,
                 "identity": "~/k"},
                {"name": "h2", "host": "5.6.7.8", "admin_user": "root", "identity": "~/k"},
                {"name": "h3", "host": "9.9.9.9"}]

    def test_provision_full_leg_named_teardown_elsewhere_identity_required(self):
        calls = []

        class R:
            returncode, stdout, stderr = 0, "peer-channel-root: ok", ""

        def run(argv, **kw):
            calls.append(argv)
            return R()
        doc = {"hosts": {"h1": {"links": [["a", "b"]], "topics": {"a|b": "t"}}}}
        reg = [{"name": "a", "host": "h1", "account": "ua", "path": "/a"},
               {"name": "b", "host": "h1", "account": "ub", "path": "/b"}]
        failed = cpc.provision_peer_channel_root(hosts=self._hosts(), run=run, links_doc=doc,
                                                 registry=reg)
        self.assertEqual(failed, [("h3", "no-identity")])
        self.assertEqual([c[-2] for c in calls], ["newlevel@1.2.3.4", "root@5.6.7.8"])
        self.assertTrue(calls[0][-1].startswith("sudo -n bash -c "))
        self.assertIn("u:ua:r-x,u:ub:r-x", calls[0][-1])
        self.assertIn("nothing to tear down", calls[1][-1])

    def test_provision_refuses_invalid_config(self):
        failed = cpc.provision_peer_channel_root(
            hosts=self._hosts(), run=lambda *a, **k: None,
            links_doc={"hosts": {"h1": {"links": [["a", "zz"]], "topics": {"a|zz": "t"}}}},
            registry=[{"name": "a", "host": "h1", "account": "u", "path": "/a"}])
        self.assertEqual(failed, [("config", "invalid")])


class TestInstallStep(unittest.TestCase):
    DOC = {"hosts": {"h": {"links": [["a", "b"]], "topics": {"a|b": "t"}}}}
    REG = [{"name": "a", "host": "h", "account": "ua", "path": "/a"},
           {"name": "b", "host": "h", "account": "ub", "path": "/b"}]

    def _run(self, calls, rcs=None):
        rcs = rcs or {}

        def run(argv, **kw):
            sub = argv[2:]
            calls.append(sub)

            class R:
                returncode = rcs.get(sub[0] if sub[0] != "marketplace" else sub[1], 0)
                stdout, stderr = "", ""
            return R()
        return run

    def _dir(self, installed=False, market=None):
        d = Path(_scratch(self, "pci"))
        (d / "plugins").mkdir()
        if installed:
            (d / "plugins" / "installed_plugins.json").write_text(
                json.dumps({"plugins": {"peer@airuleset": [{}]}}))
        if market:
            (d / "plugins" / "known_marketplaces.json").write_text(
                json.dumps({"airuleset": {"source": {"source": "directory", "path": market}}}))
        return d

    def step(self, user, d, calls, rcs=None):
        return cpc.install_step(user, "h", d, run=self._run(calls, rcs), links_doc=self.DOC,
                                registry=self.REG)

    def test_linked_account_registers_installs_and_marks(self):
        d, calls = self._dir(), []
        self.assertTrue(self.step("ua", d, calls))
        self.assertEqual(calls, [["marketplace", "add", str(cpc.MARKETPLACE_DIR)],
                                 ["install", "peer@airuleset", "--scope", "user"]])
        self.assertEqual((d / cpc.MARKER_NAME).read_text().strip(), "plugin:peer@airuleset")

    def test_already_set_up_makes_no_calls(self):
        d, calls = self._dir(installed=True, market=str(cpc.MARKETPLACE_DIR)), []
        self.assertTrue(self.step("ua", d, calls))
        self.assertEqual(calls, [])
        self.assertTrue((d / cpc.MARKER_NAME).exists())

    def test_marketplace_at_other_path_is_replaced(self):
        d, calls = self._dir(installed=True, market="/tmp/old"), []
        self.assertTrue(self.step("ua", d, calls))
        self.assertEqual(calls, [["marketplace", "remove", "airuleset"],
                                 ["marketplace", "add", str(cpc.MARKETPLACE_DIR)]])

    def test_failed_install_removes_a_stale_marker(self):
        d, calls = self._dir(market=str(cpc.MARKETPLACE_DIR)), []
        (d / cpc.MARKER_NAME).write_text("stale")
        self.assertFalse(self.step("ua", d, calls, rcs={"install": 1}))
        self.assertEqual(calls, [["install", "peer@airuleset", "--scope", "user"]])
        self.assertFalse((d / cpc.MARKER_NAME).exists())

    def test_failed_marketplace_remove_is_a_failure(self):
        d, calls = self._dir(market="/tmp/old"), []
        self.assertFalse(self.step("ua", d, calls, rcs={"remove": 1}))
        self.assertEqual(calls, [["marketplace", "remove", "airuleset"]])

    def test_unlinked_account_removes_marker_plugin_and_marketplace(self):
        d, calls = self._dir(installed=True, market=str(cpc.MARKETPLACE_DIR)), []
        (d / cpc.MARKER_NAME).write_text("x")
        self.assertTrue(self.step("uc", d, calls))
        self.assertFalse((d / cpc.MARKER_NAME).exists())
        self.assertEqual(calls, [["uninstall", "peer@airuleset"],
                                 ["marketplace", "remove", "airuleset"]])

    def test_failed_uninstall_is_a_failure(self):
        d, calls = self._dir(installed=True), []
        self.assertFalse(self.step("uc", d, calls, rcs={"uninstall": 1}))

    def test_identity_comes_from_pwd_not_user_env(self):
        d, calls = self._dir(), []
        reg = [{"name": "a", "host": "h", "account": ME, "path": "/a"},
               {"name": "b", "host": "h", "account": "ub", "path": "/b"}]
        with mock.patch.dict(os.environ, {"USER": "somebody-else"}):
            self.assertTrue(cpc.install_step(None, "h", d, run=self._run(calls),
                                             links_doc=self.DOC, registry=reg))
        self.assertTrue((d / cpc.MARKER_NAME).exists())

    def test_default_runners_inert_under_pytest(self):
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}):
            self.assertTrue(cpc.install_step())
            self.assertEqual(cpc.provision_peer_channel_root(), [])


class TestWiring(unittest.TestCase):
    def test_install_step_wired_and_latches_failure(self):
        import airuleset
        src = inspect.getsource(airuleset.cmd_install)
        i = src.index('__import__("cli_peer_channel").install_step()')
        self.assertIn("install_failed = True", src[i:i + 120])

    def test_push_wires_the_root_leg(self):
        import cli_remote
        self.assertIn('__import__("cli_peer_channel").provision_peer_channel_root()',
                      inspect.getsource(cli_remote.cmd_push))


class TestLauncher(unittest.TestCase):
    def _launch(self, mode, marker, conversation=False):
        import cli_claude_scripts
        home = _scratch(self, "pcl")
        os.makedirs(os.path.join(home, ".claude"))
        if marker:
            Path(home, ".claude", cpc.MARKER_NAME).write_text(cpc.CHANNEL_ARG)
        if conversation:
            key = home.replace("/", "-").replace(".", "-").replace("_", "-")
            os.makedirs(os.path.join(home, ".claude", "projects", key))
            Path(home, ".claude", "projects", key, "x.jsonl").write_text("{}\n")
        bindir = os.path.join(home, ".local", "bin")
        os.makedirs(bindir)
        fake = os.path.join(bindir, "claude")
        Path(fake).write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        os.chmod(fake, 0o755)
        script = os.path.join(home, "launch.sh")
        Path(script).write_text(cli_claude_scripts.render_claude_launch_script())
        r = subprocess.run(["bash", script, mode], capture_output=True, text=True, cwd=home,
                           env={"HOME": home, "PATH": bindir + ":/usr/bin:/bin"}, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split("\n")

    def test_channel_flag_only_with_marker_every_managed_mode(self):
        for mode in ("default", "new", "ultracode", "fullscreen"):
            for conv in (False, True):
                args = self._launch(mode, True, conv)
                i = args.index("--channels")
                self.assertEqual(args[i + 1], "plugin:peer@airuleset", (mode, conv))
                self.assertEqual("-c" in args, conv and mode != "new", (mode, conv))
                self.assertNotIn("--channels", self._launch(mode, False, conv), (mode, conv))

    def test_plain_mode_stays_vanilla(self):
        self.assertNotIn("--channels", self._launch("plain", True))


if __name__ == "__main__":
    unittest.main()
