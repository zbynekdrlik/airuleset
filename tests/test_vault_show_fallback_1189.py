"""#1189 — the vault endpoints (`secret show`, `secret request`) probe the public
lane BEFORE handing out its URL; #1192 then made that URL the ONLY one.

Incident (29.9.2026): `secret show` on dev2 printed ONLY
`https://drop-dev2.newlevel.media/<token>/`, unprobed; the controller tunnel was
mid-restart, Cloudflare answered 1033, and the owner had nothing else to open.
#1189 added a probe + an always-printed tailscale fallback line; the owner
reversed the fallback on 30.9. (#1192: „vadi mi ze stale vsade pchas
tailscale“). What stays locked here: the probe (token-free, skipped for a dead
origin), a dead link is never handed out, and the #1189 BIND set (only what is
PRINTED changed). A dead lane now prints NO URL and exits 1.

The harness mirrors `test_delivery_channel_1115.TestSecretShowFallbackLabelled`
(everything outside the command is patched; nothing binds, nothing reaches the
network). `_secret_url_line` / the public URL line stay REAL.
"""
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import cli_drop_gateway as dg
import cli_drop_lanes as dl
import cli_vault
import cli_vault_delivery as vd
import filedrop

HOST = "drop-dev2.newlevel.media"
TS_IP = "100.64.0.1"
PORT = 8870


class _Harness(unittest.TestCase):
    def run_cmd(self, probe_code, bind_ip=TS_IP, lane_access=True, request=False,
                names=("X",), dead_ips=()):
        self.probed = []
        self.popen_argv = None

        def probe(url, timeout=3, user_agent="x"):
            self.probed.append(url)
            return probe_code

        def popen(argv, **kw):
            self.popen_argv = argv
            return types.SimpleNamespace(pid=4321, poll=lambda: None,
                                         terminate=lambda: None)

        lane = types.SimpleNamespace(access=lane_access)
        with tempfile.TemporaryDirectory() as td:
            st = mock.MagicMock()
            st.DEFAULT_ENDPOINT_TTL_S = 300
            st.DEFAULT_KEEP_S = 3600
            st.state.return_value = "absent"
            st.register_request.return_value = "NONCEVALUE"
            st.log_path.return_value = Path(td) / "vault.log"
            self.st = st
            def _open(u, timeout=2):
                if any("//%s:" % ip in u for ip in dead_ips):
                    raise OSError("refused")
                return types.SimpleNamespace(status=204)
            opener = types.SimpleNamespace(open=_open)
            with mock.patch.object(filedrop, "bind_ips", lambda: [TS_IP]), \
                 mock.patch.object(filedrop, "vault", st, create=True), \
                 mock.patch.object(cli_vault, "_secret_show_source",
                                   lambda args, s: ("env", "X", "X")), \
                 mock.patch.object(cli_vault, "_secret_request_names",
                                   lambda args: list(names)), \
                 mock.patch.object(cli_vault, "_secret_parse_persist_map",
                                   lambda args, names: {}), \
                 mock.patch.object(cli_vault, "_secret_bindable", lambda ip: True), \
                 mock.patch.object(cli_vault, "_secret_public_lane",
                                   lambda args: (HOST, PORT, bind_ip)), \
                 mock.patch.object(cli_vault, "_pick_free_port",
                                   lambda ips, rng: PORT), \
                 mock.patch.object(cli_vault, "_secret_opener", lambda: opener), \
                 mock.patch.object(dg, "drop_lane_for_account", lambda *a, **k: lane), \
                 mock.patch.object(dl, "_probe_public_status", probe), \
                 mock.patch("subprocess.Popen", popen):
                out, err = io.StringIO(), io.StringIO()
                args = types.SimpleNamespace(
                    name="X", file=None, cmd=[], ttl=30, keep=None, port=None,
                    allow_plain=False, public=False, replace=False,
                    persist=None, persist_map=None)
                self.exit_code = None
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        (cli_vault._secret_request if request
                         else cli_vault._secret_show)(args)
                    except SystemExit as e:
                        self.exit_code = e.code
                return out.getvalue(), err.getvalue()

    @staticmethod
    def url_lines(out):
        return [ln for ln in out.splitlines() if "://" in ln]


class TestPublicLaneDead(_Harness):
    def test_1033_prints_no_url_and_exits_loudly(self):
        out, err = self.run_cmd(530)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.url_lines(out), [], out)   # a dead URL is never handed out
        self.assertIn("secret show: !!! ŽIADNA URL", err)
        self.assertIn("1033", err)
        self.assertIn("--private", err)
        self.st.log_event.assert_any_call("public-lane-dead", "X")

    def test_connection_error_is_dead_too(self):
        out, err = self.run_cmd(None)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.url_lines(out), [])
        self.assertIn("no answer", err)

    def test_probe_is_token_free(self):
        out, _ = self.run_cmd(204)
        token = self.url_lines(out)[0].split("/")[3]
        self.assertEqual(self.probed, ["https://%s/healthz" % HOST])
        self.assertNotIn(token, self.probed[0])


class TestPublicLaneAlive(_Harness):
    def test_origin_204_is_the_one_public_line(self):
        out, err = self.run_cmd(204)
        lines = self.url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST), lines)
        self.assertNotIn(TS_IP, out)
        self.assertNotIn("NOT verified", err)

    def test_access_302_does_not_claim_the_tunnel_is_up(self):
        # Access answers at the edge, before the tunnel: a dead tunnel looks
        # exactly like this, so the gap is named and --private is the way out.
        out, err = self.run_cmd(302)
        lines = self.url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST), lines)
        self.assertIn("NOT verified", err)
        self.assertIn("--private", err)


class TestBindSet(_Harness):
    """#1192 changed only what is PRINTED; the #1189 bind set is locked as-is."""

    def test_controller_topology_binds_origin_plus_tailscale(self):
        self.run_cmd(204, bind_ip="100.99.0.9")
        self.assertEqual(self.popen_argv[3], "100.99.0.9,%s" % TS_IP)

    def test_local_token_only_lane_binds_loopback_plus_tailscale(self):
        out, _ = self.run_cmd(204, bind_ip="127.0.0.1", lane_access=False)
        self.assertEqual(self.popen_argv[3], "127.0.0.1,%s" % TS_IP)
        self.assertEqual(len(self.url_lines(out)), 1, out)
        self.assertNotIn("127.0.0.1", out)               # loopback is never offered
        self.assertNotIn(TS_IP, out)

    def test_local_access_lane_stays_loopback_only(self):
        # a tailnet bind there would be a way in that skips Cloudflare Access
        out, err = self.run_cmd(530, bind_ip="127.0.0.1", lane_access=True)
        self.assertEqual(self.popen_argv[3], "127.0.0.1")
        self.assertEqual(self.url_lines(out), [], out)   # no "last resort" URL
        self.assertEqual(self.exit_code, 1)
        self.assertIn("ŽIADNA URL", err)

    def test_dead_tunnel_origin_is_dead_even_when_the_edge_answers(self):
        out, err = self.run_cmd(302, bind_ip="100.99.0.9", dead_ips=("100.99.0.9",))
        self.assertEqual(self.url_lines(out), [])
        self.assertEqual(self.exit_code, 1)
        self.assertIn("tunnel origin 100.99.0.9 down", err)
        self.assertEqual(self.probed, [])                # no probe of a dead origin

    def test_lane_lookup_error_fails_closed(self):
        def boom():
            raise RuntimeError("x")
        self.assertEqual(vd.public_bind_ips("127.0.0.1", [TS_IP], lane_lookup=boom),
                         ["127.0.0.1"])


class TestVaultRequest(_Harness):
    def test_request_dead_lane_refuses_with_its_own_prog(self):
        out, err = self.run_cmd(530, request=True)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(self.url_lines(out), [])
        self.assertIn("secret: !!! ŽIADNA URL", err)
        self.assertNotIn("secret show", err)

    def test_request_several_names_logs_each(self):
        self.run_cmd(530, request=True, names=("A", "B"))
        calls = [c.args for c in self.st.log_event.call_args_list
                 if c.args and c.args[0] == "public-lane-dead"]
        self.assertEqual(calls, [("public-lane-dead", "A"), ("public-lane-dead", "B")])

    def test_request_live_lane_is_the_one_public_line(self):
        out, _ = self.run_cmd(204, request=True)
        lines = self.url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST), lines)


class TestPrivateKeepsLoopback(unittest.TestCase):
    """`--private` on a box with no tailscale (a CI runner, a bare box) prints
    the loopback URL — the pre-#1189 private path must never print ZERO URLs
    (main CI e9954d17: test_vault_channel
    `test_a_real_request_prints_at_least_one_url`)."""

    def test_loopback_only_box_still_prints_its_url(self):
        out, err = io.StringIO(), io.StringIO()
        channel = vd.emit_urls(
            "secret", None, "TOK", ["127.0.0.1"],
            lambda ip: "http://%s:8830/TOK/   [loopback]" % ip,
            lambda ip: True, private=True, out=out, err=err)
        self.assertEqual(channel, "private")
        self.assertIn("http://127.0.0.1:8830/TOK/", out.getvalue())


if __name__ == "__main__":
    unittest.main()
