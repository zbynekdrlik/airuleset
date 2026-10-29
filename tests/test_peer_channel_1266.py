"""#1266 — cross-account peer channel (peer_channel.py runtime + cli_peer_channel managed side)."""
import io
import json
import os
import pwd
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

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


def _write_config(d, links, extra=()):
    reg = [{"name": "alpha", "host": HOST, "account": ME, "path": "~/devel/alpha"},
           {"name": "beta", "host": HOST, "account": ME, "path": "~/devel/beta"},
           {"name": "gamma", "host": HOST, "account": ME, "path": "~/devel/gamma"},
           {"name": "foreign", "host": HOST, "account": OTHER, "path": "/srv/foreign"},
           {"name": "elsewhere", "host": "otherhost", "account": ME, "path": "~/x"}]
    reg += list(extra)
    Path(d, pc.REGISTRY_FILE).write_text(json.dumps(reg))
    Path(d, pc.LINKS_FILE).write_text(json.dumps({"hosts": {HOST: {"links": links}}}))


class _Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = os.path.join(self.tmp.name, "cfg")
        # AF_UNIX paths are capped at 108 bytes: keep the socket dir short.
        self.sock = tempfile.mkdtemp(prefix="pc", dir="/tmp")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.sock], check=False))
        os.makedirs(self.cfg)
        _write_config(self.cfg, [["alpha", "beta"], ["alpha", "foreign"]])

    def peer(self, project, **kw):
        delivered = []
        log = _Recorder()
        p = pc.Peer(HOST, ME, os.getuid(), "/", self.tmp.name, project_arg=project,
                    sock_dir=self.sock, config_dir=self.cfg, log=log,
                    notify=lambda text, meta: delivered.append((text, meta)), **kw)
        p.delivered, p.rec = delivered, log
        self.addCleanup(p.close)
        return p


class TestConfig(unittest.TestCase):
    def test_committed_links_valid_against_registry(self):
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])

    def test_validate_links_rejects_unknown_self_and_wrong_host(self):
        reg = [{"name": "a", "host": "h", "account": "u", "path": "/a"},
               {"name": "b", "host": "other", "account": "u", "path": "/b"}]
        errs = cpc.validate_links({"hosts": {"h": {"links": [["a", "a"], ["a", "b"], ["a"]]}}}, reg)
        self.assertTrue(any("self link" in e for e in errs))
        self.assertTrue(any("'b' is not a registry project on h" in e for e in errs))
        self.assertTrue(any("malformed" in e for e in errs))

    def test_accounts_for_host(self):
        reg = [{"name": "a", "host": "h", "account": "ua", "path": "/a"},
               {"name": "b", "host": "h", "account": "ub", "path": "/b"},
               {"name": "c", "host": "h", "account": "uc", "path": "/c"}]
        doc = {"hosts": {"h": {"links": [["a", "b"]]}}}
        self.assertEqual(cpc.accounts_for_host("h", doc, reg), ["ua", "ub"])
        self.assertEqual(cpc.accounts_for_host("x", doc, reg), [])

    def test_resolve_project_longest_path_own_account_only(self):
        projects = {"outer": {"account": "u", "path": "~/devel"},
                    "inner": {"account": "u", "path": "~/devel/inner"},
                    "theirs": {"account": "v", "path": "~/devel/inner/deep"}}
        self.assertEqual(pc.resolve_project(projects, "u", "/h/devel/inner/deep/x", "/h"), "inner")
        self.assertEqual(pc.resolve_project(projects, "u", "/h/devel/other", "/h"), "outer")
        self.assertIsNone(pc.resolve_project(projects, "u", "/h/devels", "/h"))

    def test_project_arg_must_belong_to_this_account(self):
        with tempfile.TemporaryDirectory() as d:
            _write_config(d, [])
            p = pc.Peer(HOST, ME, os.getuid(), "/", d, project_arg="foreign",
                        sock_dir=d, config_dir=d, log=_Recorder())
            self.assertIsNone(p.project)
            self.assertIn("not a testhost project", p.problem)


class TestDelivery(_Env):
    def test_linked_message_delivered_as_channel_event(self):
        a, b = self.peer("alpha"), self.peer("beta")
        self.assertTrue(b.start_listener())
        ok, out = a.send("beta", "ahoj z alpha")
        self.assertTrue(ok, out)
        self.assertEqual(b.delivered, [("ahoj z alpha", {"from": "alpha"})])
        self.assertIn("deliver", b.rec.names())
        self.assertIn("send", a.rec.names())

    def test_socket_mode_and_cleanup(self):
        b = self.peer("beta")
        b.start_listener()
        path = b.sock_path
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o666)
        b.close()
        self.assertFalse(os.path.lexists(path))

    def test_unlinked_pair_refused_both_ends(self):
        b, g = self.peer("beta"), self.peer("gamma")
        g.start_listener()
        ok, out = b.send("gamma", "hi")
        self.assertFalse(ok)
        self.assertIn("not linked", out)
        # a raw client bypassing the sender check is refused by the receiver
        reply = g.handle_request({"v": 1, "op": "msg", "from": "beta", "to": "gamma",
                                  "text": "x"}, os.getuid())
        self.assertFalse(reply["ok"])
        self.assertIn("no link", reply["error"])
        self.assertEqual(g.delivered, [])

    def test_sender_spoofing_another_accounts_project_refused(self):
        a = self.peer("alpha")
        # kernel says the caller is ME, but it claims to be 'foreign' (account OTHER)
        reply = a.handle_request({"v": 1, "op": "msg", "from": "foreign", "to": "alpha",
                                  "text": "spoof"}, os.getuid())
        self.assertFalse(reply["ok"])
        self.assertIn("not a testhost project of %s" % ME, reply["error"])
        self.assertEqual(a.delivered, [])

    def test_genuine_other_account_sender_accepted_when_linked(self):
        a = self.peer("alpha")
        other_uid = pwd.getpwnam(OTHER).pw_uid
        reply = a.handle_request({"v": 1, "op": "msg", "from": "foreign", "to": "alpha",
                                  "text": "real"}, other_uid)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(a.delivered, [("real", {"from": "foreign"})])

    def test_wrong_target_and_unknown_uid_refused(self):
        a = self.peer("alpha")
        self.assertIn("wrong target", a.handle_request(
            {"v": 1, "op": "msg", "from": "beta", "to": "gamma", "text": "x"},
            os.getuid())["error"])
        self.assertIn("unknown uid", a.handle_request(
            {"v": 1, "op": "msg", "from": "beta", "to": "alpha", "text": "x"}, 2 ** 31 - 7)["error"])

    def test_impostor_listener_refused_before_text_is_sent(self):
        # a socket named after 'foreign' (account OTHER) but owned by ME
        a = self.peer("alpha")
        planted = os.path.join(self.sock, pc.socket_name("foreign", 4242))
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(planted)
        srv.listen(1)
        self.addCleanup(srv.close)
        got = []

        def accept():
            c, _ = srv.accept()
            c.settimeout(2)
            try:
                got.append(c.recv(100))
            except OSError as e:
                got.append(repr(e))
            c.close()
        t = threading.Thread(target=accept, daemon=True)
        t.start()
        ok, out = a.send("foreign", "secret text")
        t.join(3)
        self.assertFalse(ok)
        self.assertIn("impostor", out)
        self.assertEqual(got, [b""])

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

    def test_no_live_session_reported(self):
        ok, out = self.peer("alpha").send("beta", "hi")
        self.assertFalse(ok)
        self.assertIn("no live session", out)

    def test_stale_own_socket_swept(self):
        dead = os.path.join(self.sock, pc.socket_name("beta", 2 ** 22 + 12345))
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(dead)
        s.close()
        self.peer("beta").start_listener()
        self.assertFalse(os.path.lexists(dead))

    def test_missing_dir_is_reported_not_fatal(self):
        p = pc.Peer(HOST, ME, os.getuid(), "/", self.tmp.name, project_arg="alpha",
                    sock_dir=os.path.join(self.tmp.name, "nope"), config_dir=self.cfg,
                    log=_Recorder())
        self.assertFalse(p.start_listener())
        self.assertIn("root leg not applied", p.problem)


class TestMcp(unittest.TestCase):
    def _server(self):
        log = _Recorder()
        with tempfile.TemporaryDirectory() as d:
            _write_config(d, [])
            peer = pc.Peer(HOST, ME, os.getuid(), "/", d, project_arg="alpha",
                           sock_dir=d, config_dir=d, log=log)
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
        self.assertLess(len(res["instructions"]), 200)
        self.assertEqual([t["name"] for t in m[2]["result"]["tools"]], ["peers", "peer_send"])

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
            env={**os.environ, "HOME": tempfile.mkdtemp()})
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

    def test_tmpfiles_acl_per_account_and_empty_set_clears(self):
        t = cpc.render_tmpfiles(["fohmixer", "newlevel"])
        self.assertIn("d /run/claude-peers 1700 root root -", t)
        self.assertIn("u:fohmixer:rwx,u:newlevel:rwx,m::rwx", t)
        self.assertIn("u::rwx,g::---,o::---", cpc.render_tmpfiles([]))

    def test_bad_account_name_refused(self):
        with self.assertRaises(ValueError):
            cpc.render_root_script(["x; rm -rf /"])

    def _run_fake_root(self, accounts, acl_users):
        """Execute the REAL rendered script against a scratch root with stubbed
        root tools (the #1223 lesson: string asserts cannot see logic bugs)."""
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", root], check=False))
        script = cpc.render_root_script(accounts)
        script = (script.replace("/etc/", root + "/etc/")
                  .replace(pc.SOCK_DIR, root + "/run/claude-peers"))
        script = script.replace("cd /\n", "cd %s\n" % root)
        bindir = os.path.join(root, "bin")
        os.makedirs(bindir)
        stubs = {
            "id": "#!/bin/sh\nexit 0\n",
            "systemd-tmpfiles": "#!/bin/sh\nmkdir -p %s/run/claude-peers && "
                                "chmod 1700 %s/run/claude-peers\n" % (root, root),
            "stat": "#!/bin/sh\necho root:root\n",
            "getfacl": "#!/bin/sh\necho user::rwx\n%s\necho mask::rwx\n" % "\n".join(
                "echo user:%s:rwx" % u for u in acl_users),
        }
        for name, body in stubs.items():
            p = os.path.join(bindir, name)
            Path(p).write_text(body)
            os.chmod(p, 0o755)
        env = {**os.environ, "PATH": bindir + ":" + os.environ["PATH"]}
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        return r, root

    def test_rendered_script_writes_files_and_passes_readback(self):
        r, root = self._run_fake_root(["fohmixer", "newlevel"], ["fohmixer", "newlevel"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("peer-channel-root: ok", r.stdout)
        pol = json.loads(Path(root + cpc.POLICY_PATH).read_text())
        self.assertIn({"marketplace": "airuleset", "plugin": "peer"}, pol["allowedChannelPlugins"])
        self.assertIn("u:fohmixer:rwx", Path(root + cpc.TMPFILES_PATH).read_text())
        self.assertEqual(stat.S_IMODE(os.stat(root + cpc.POLICY_PATH).st_mode), 0o644)

    def test_readback_fails_on_extra_acl_user(self):
        r, _ = self._run_fake_root(["fohmixer"], ["fohmixer", "intruder"])
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("ACL users", r.stderr)

    def test_provision_reaches_only_peer_hosts_with_sudo(self):
        calls = []

        class R:
            returncode, stdout, stderr = 0, "peer-channel-root: ok", ""

        def run(argv, **kw):
            calls.append(argv)
            return R()
        doc = {"hosts": {"h1": {"links": [["a", "b"]]}}}
        reg = [{"name": "a", "host": "h1", "account": "ua", "path": "/a"},
               {"name": "b", "host": "h1", "account": "ub", "path": "/b"}]
        hosts = [{"name": "h1", "host": "1.2.3.4", "admin_user": "newlevel", "sudo": True,
                  "identity": "~/k"}]
        self.assertEqual(cpc.provision_peer_channel_root(hosts=hosts, run=run, links_doc=doc,
                                                         registry=reg), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-2], "newlevel@1.2.3.4")
        self.assertTrue(calls[0][-1].startswith("sudo -n bash -c "))
        self.assertIn("u:ua:rwx,u:ub:rwx", calls[0][-1])

    def test_provision_refuses_invalid_config(self):
        failed = cpc.provision_peer_channel_root(
            hosts=[], run=lambda *a, **k: None,
            links_doc={"hosts": {"h": {"links": [["a", "zz"]]}}},
            registry=[{"name": "a", "host": "h", "account": "u", "path": "/a"}])
        self.assertEqual(failed, [("config", "invalid")])


class TestInstallStep(unittest.TestCase):
    DOC = {"hosts": {"h": {"links": [["a", "b"]]}}}
    REG = [{"name": "a", "host": "h", "account": "ua", "path": "/a"},
           {"name": "b", "host": "h", "account": "ub", "path": "/b"}]

    def _run(self, calls, rc=0):
        class R:
            returncode, stdout, stderr = rc, "", ""

        def run(argv, **kw):
            calls.append(argv[2:])
            return R()
        return run

    def test_linked_account_registers_installs_and_marks(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            ok = cpc.install_step("ua", "h", d, run=self._run(calls), links_doc=self.DOC,
                                  registry=self.REG)
            self.assertTrue(ok)
            self.assertEqual(calls[0], ["marketplace", "add", str(cpc.MARKETPLACE_DIR)])
            self.assertEqual(calls[1], ["install", "peer@airuleset", "--scope", "user"])
            self.assertEqual(Path(d, cpc.MARKER_NAME).read_text().strip(),
                             "plugin:peer@airuleset")

    def test_failed_install_leaves_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            ok = cpc.install_step("ua", "h", d, run=self._run([], rc=1), links_doc=self.DOC,
                                  registry=self.REG)
            self.assertFalse(ok)
            self.assertFalse(Path(d, cpc.MARKER_NAME).exists())

    def test_unlinked_account_removes_marker_and_uninstalls(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, cpc.MARKER_NAME).write_text("x")
            os.makedirs(Path(d, "plugins"))
            Path(d, "plugins", "installed_plugins.json").write_text(
                json.dumps({"plugins": {"peer@airuleset": [{}]}}))
            calls = []
            self.assertTrue(cpc.install_step("uc", "h", d, run=self._run(calls),
                                             links_doc=self.DOC, registry=self.REG))
            self.assertFalse(Path(d, cpc.MARKER_NAME).exists())
            self.assertEqual(calls, [["uninstall", "peer@airuleset"]])

    def test_default_runner_inert_under_pytest(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"PYTEST_CURRENT_TEST": "x"}):
            self.assertTrue(cpc.install_step())
            self.assertEqual(cpc.provision_peer_channel_root(), [])


class TestLauncher(unittest.TestCase):
    def _launch(self, mode, marker):
        import cli_claude_scripts
        home = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", home], check=False))
        os.makedirs(os.path.join(home, ".claude"))
        if marker:
            Path(home, ".claude", cpc.MARKER_NAME).write_text(cpc.CHANNEL_ARG)
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

    def test_channel_flag_only_with_marker_and_never_plain(self):
        args = self._launch("new", True)
        i = args.index("--channels")
        self.assertEqual(args[i + 1], "plugin:peer@airuleset")
        self.assertNotIn("--channels", self._launch("new", False))
        self.assertNotIn("--channels", self._launch("plain", True))
        self.assertIn("--channels", self._launch("default", True))


if __name__ == "__main__":
    unittest.main()
