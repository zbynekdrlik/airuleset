"""#1347 — a controller ingress change must not restart cloudflared.

Owner 17.11.2026: every webterm tab dropped 5x on 16.11. Each route change
re-rendered the LOCALLY managed `controller-webterm.yml`, and the #1189 apply
restarted the connector through the overlap. The overlap keeps the hostname
up, but a websocket is pinned to the process that accepted it, so every open
tab died at the end of the old process's 30 s grace (measured on a scratch
tunnel: SIGTERM 09:19:39Z, websocket dead 09:20:10Z).

Measured fix (scratch tunnel, cloudflared 2026.8.3): a REMOTELY managed tunnel
applies a `PUT …/cfd_tunnel/<id>/configurations` live in ~2 s, and an open
websocket survived two such changes (72/72 echoes). A connector that registered
while the tunnel was locally managed never receives the pushes, so the first
conversion needs ONE restart.

No network, no systemd, no journal here: the Cloudflare transport, systemctl
and the journal reader are fakes.
"""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_controller_tunnel as ct
import cli_tunnel_remote_config as rc
import cli_webterm_tunnel as tun

TID = "f85ea304-920b-4ba4-96bc-a68001ce6fb4"
SVC = "webterm-controller-tunnel.service"
RULES_A = [("zbynek.newlevel.media", "unix:/run/user/1000/z.sock"),
           ("drop-x.newlevel.media", "^/s/", "http://100.64.0.1:8870")]
RULES_B = RULES_A + [("svc.newlevel.media", "http://100.64.0.2:9000")]


class FakeCF:
    """Records every call; answers like the Cloudflare tunnel API."""

    def __init__(self, remote=True, ingress=None, version=7, fail=None):
        self.remote, self.version, self.fail = remote, version, fail
        self.ingress = ingress if ingress is not None else []
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if self.fail and self.fail in (method, path.rsplit("/", 1)[-1]):
            return 403, {"success": False, "errors": [{"code": 10000,
                                                       "message": "Authentication error"}]}
        if method == "GET" and path.endswith("/configurations"):
            return 200, {"success": True, "result": {
                "tunnel_id": TID, "version": self.version,
                "config": {"ingress": self.ingress}}}
        if method == "GET":
            return 200, {"success": True, "result": {
                "id": TID, "remote_config": self.remote,
                "config_src": "cloudflare" if self.remote else "local"}}
        if method == "PUT":
            self.version += 1
            self.remote = True
            self.ingress = body["config"]["ingress"]
            return 200, {"success": True, "result": {
                "tunnel_id": TID, "version": self.version, "config": body["config"]}}
        raise AssertionError("unexpected %s %s" % (method, path))

    def puts(self):
        return [c for c in self.calls if c[0] == "PUT"]


def _client(fake):
    return rc.TunnelConfigClient("acc", token="t", transport=fake)


class TestRemoteIngress(unittest.TestCase):
    def test_two_and_three_tuples_and_catch_all(self):
        self.assertEqual(rc.remote_ingress(RULES_A), [
            {"hostname": "zbynek.newlevel.media",
             "service": "unix:/run/user/1000/z.sock"},
            {"hostname": "drop-x.newlevel.media", "path": "^/s/",
             "service": "http://100.64.0.1:8870"},
            {"service": "http_status:404"}])

    def test_bad_rule_raises(self):
        with self.assertRaises(ValueError):
            rc.remote_ingress([("only-one",)])

    def test_same_order_as_local_render(self):
        """The remote list keeps the local YAML's top-to-bottom order (a path
        rule must precede the same host's catch-all)."""
        local = tun.render_cloudflared_multi_ingress_config(TID, "/c.json", RULES_B)
        hosts = [r.get("hostname") for r in rc.remote_ingress(RULES_B)]
        self.assertEqual([h for h in hosts if h],
                         [ln.split(": ", 1)[1] for ln in local.splitlines()
                          if ln.startswith("  - hostname: ")])


class TestSyncIngress(unittest.TestCase):
    def test_unchanged_remote_does_not_put(self):
        fake = FakeCF(remote=True, ingress=[
            dict(r, originRequest={}) for r in rc.remote_ingress(RULES_A)])
        ok, version, action, _err = rc.sync_ingress(_client(fake), TID,
                                                     rc.remote_ingress(RULES_A))
        self.assertEqual((ok, version, action), (True, 7, "unchanged"))
        self.assertEqual(fake.puts(), [])

    def test_changed_ingress_puts_the_whole_list(self):
        fake = FakeCF(remote=True, ingress=rc.remote_ingress(RULES_A))
        ok, version, action, _err = rc.sync_ingress(_client(fake), TID,
                                                    rc.remote_ingress(RULES_B))
        self.assertEqual((ok, version, action), (True, 8, "updated"))
        self.assertEqual(fake.puts()[0][2],
                         {"config": {"ingress": rc.remote_ingress(RULES_B)}})

    def test_local_tunnel_is_converted_even_with_equal_ingress(self):
        """A locally managed tunnel's configurations endpoint holds what its
        connector pushed, so equal ingress there is no proof of remote mode."""
        fake = FakeCF(remote=False, ingress=rc.remote_ingress(RULES_A), version=60)
        ok, version, action, _err = rc.sync_ingress(_client(fake), TID,
                                                    rc.remote_ingress(RULES_A))
        self.assertEqual((ok, version, action), (True, 61, "converted"))
        self.assertEqual(len(fake.puts()), 1)

    def test_api_error_never_puts(self):
        fake = FakeCF(fail="GET")
        ok, _v, _a, err = rc.sync_ingress(_client(fake), TID, rc.remote_ingress(RULES_A))
        self.assertFalse(ok)
        self.assertIn("Authentication error", err)
        self.assertEqual(fake.puts(), [])


class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeConnector:
    """systemctl + journal for ONE main unit. `applies` = whether the running
    process takes remote pushes (False = registered while locally managed)."""

    def __init__(self, applies=True, applied_version=None):
        self.pid, self.applies = 100, applies
        self.journal = {100: [] if applied_version is None else [
            'INF Updated to new configuration config="{}" version=%d' % applied_version]}
        self.restarts = []

    def systemctl(self, args):
        if args[:3] == ["show", "-p", "MainPID"]:
            return 0, "%d\n" % self.pid, ""
        return 0, "", ""

    def read_journal(self, pid):
        return "\n".join(self.journal.get(pid, []))

    def push(self, version):
        if self.applies:
            self.journal[self.pid].append(
                'INF Updated to new configuration config="{}" version=%d' % version)

    def overlap_restart(self, systemctl, service, cfg, **_k):
        self.restarts.append(service)
        self.pid += 1
        self.applies = True
        self.journal[self.pid] = []
        return True, "overlap"


def _apply(fake_cf, conn, rules, provision=None):
    clock = _Clock()
    put_hook = fake_cf.__call__

    def transport(method, path, body):
        st, d = put_hook(method, path, body)
        if method == "PUT" and st == 200:
            conn.push(d["result"]["version"])
        return st, d
    calls = []

    def _provision():
        calls.append("provision")
        return True
    with mock.patch.object(rc.cli_tunnel_apply, "overlap_restart",
                           side_effect=conn.overlap_restart), \
            mock.patch.object(rc, "read_journal", side_effect=conn.read_journal), \
            contextlib.redirect_stderr(io.StringIO()) as err, \
            contextlib.redirect_stdout(io.StringIO()):
        ok = rc.apply_remote_tunnel(
            _client(transport), TID, rc.remote_ingress(rules),
            provision=provision or _provision, service_name=SVC,
            config_path=Path("/nonexistent/controller-webterm.yml"),
            systemctl=conn.systemctl, sleep=clock.sleep, clock=clock.now)
    return ok, calls, err.getvalue()


class TestApplyRemoteTunnel(unittest.TestCase):
    def test_hot_change_never_restarts(self):
        fake = FakeCF(remote=True, ingress=rc.remote_ingress(RULES_A))
        conn = FakeConnector(applies=True, applied_version=7)
        ok, calls, _err = _apply(fake, conn, RULES_B)
        self.assertTrue(ok)
        self.assertEqual(calls, ["provision"])
        self.assertEqual(conn.restarts, [], "a remote ingress change must not restart")

    def test_unchanged_and_applied_is_a_noop(self):
        fake = FakeCF(remote=True, ingress=rc.remote_ingress(RULES_A))
        conn = FakeConnector(applies=True, applied_version=7)
        ok, _calls, _err = _apply(fake, conn, RULES_A)
        self.assertTrue(ok)
        self.assertEqual(fake.puts(), [])
        self.assertEqual(conn.restarts, [])

    def test_conversion_restarts_once_through_the_overlap(self):
        """The running connector registered as locally managed: it never sees
        the push, so ONE overlap restart; the new process applies at
        registration (cloudflared pushes the latest version to it)."""
        fake = FakeCF(remote=False, ingress=rc.remote_ingress(RULES_A), version=60)
        conn = FakeConnector(applies=False)
        orig = conn.overlap_restart

        def restart_and_register(*a, **k):
            res = orig(*a, **k)
            conn.push(fake.version)
            return res
        conn.overlap_restart = restart_and_register
        ok, _calls, err = _apply(fake, conn, RULES_A)
        self.assertTrue(ok)
        self.assertEqual(conn.restarts, [SVC])
        self.assertIn("#1347", err)

    def test_put_failure_touches_nothing(self):
        fake = FakeCF(remote=True, ingress=rc.remote_ingress(RULES_A), fail="PUT")
        conn = FakeConnector(applies=True, applied_version=7)
        ok, calls, err = _apply(fake, conn, RULES_B)
        self.assertFalse(ok)
        self.assertEqual(calls, [], "no bootstrap write after a failed PUT")
        self.assertEqual(conn.restarts, [])
        self.assertIn("LOUD", err)

    def test_never_applied_after_restart_is_loud_false(self):
        fake = FakeCF(remote=True, ingress=rc.remote_ingress(RULES_A))
        conn = FakeConnector(applies=False)
        conn.overlap_restart = lambda *a, **k: (conn.restarts.append(SVC)
                                                or (True, "overlap"))
        ok, _calls, err = _apply(fake, conn, RULES_B)
        self.assertFalse(ok)
        self.assertEqual(conn.restarts, [SVC], "exactly one restart, never a loop")
        self.assertIn("LOUD", err)

    def test_applied_version_reads_only_the_newest_line(self):
        text = ('INF Starting tunnel\n'
                'INF Updated to new configuration config="{\\"a\\":1}" version=3\n'
                'INF Updated to new configuration config="{}" version=5\n')
        self.assertEqual(rc.applied_version(text), 5)
        self.assertIsNone(rc.applied_version("INF Starting tunnel\n"))


class TestControllerApplyTunnel(unittest.TestCase):
    """The controller render: in remote mode a route change leaves the files
    the connector RUNS byte-identical, so the #1189 gate never restarts it."""

    def _render(self, rules, remote):
        captured = {}

        def fake_provision(creds, bin_, cfg_path, config_text, unit_path,
                           service, unit_text, **k):
            captured["config"], captured["unit"] = config_text, unit_text
            return True

        def fake_remote(client, tid, ingress, *, provision, **k):
            captured["ingress"] = ingress
            return provision()
        with mock.patch.object(ct, "_rules_for", return_value=list(rules)), \
                mock.patch.object(tun, "_provision_managed_tunnel",
                                  side_effect=fake_provision), \
                mock.patch.object(rc, "apply_remote_tunnel",
                                  side_effect=fake_remote):
            ok = ct.apply_tunnel([], remote_client=object() if remote else None)
        self.assertTrue(ok)
        return captured

    def test_remote_mode_route_change_keeps_config_and_unit(self):
        a = self._render(RULES_A, remote=True)
        b = self._render(RULES_B, remote=True)
        self.assertEqual(a["config"], b["config"])
        self.assertEqual(a["unit"], b["unit"])
        self.assertNotEqual(a["ingress"], b["ingress"])
        self.assertNotIn("ingress:", a["config"])
        self.assertIn("tunnel: %s" % TID, a["config"])
        self.assertIn("credentials-file: ", a["config"])

    def test_local_mode_unchanged(self):
        """No token: the full local YAML exactly as before (#1238/#1131)."""
        a = self._render(RULES_A, remote=False)
        self.assertEqual(a["config"], tun.render_cloudflared_multi_ingress_config(
            TID, a["config"].split("credentials-file: ")[1].split("\n")[0],
            RULES_A))
        self.assertNotIn("ingress", a)

    def test_under_pytest_the_live_token_is_never_used(self):
        with mock.patch.object(rc, "load_client",
                               side_effect=AssertionError("live token read")):
            self.assertIsNone(ct._default_remote_client())


class TestLoadClient(unittest.TestCase):
    def test_absent_token_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(rc.load_client(os.path.join(d, "nope")))

    def test_token_is_stripped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tok"
            p.write_text("abc\n")
            c = rc.load_client(str(p))
            self.assertEqual(c._token, "abc")
            self.assertEqual(c.account_id, rc.ACCOUNT_ID)


if __name__ == "__main__":
    unittest.main()
