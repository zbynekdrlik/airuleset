"""#1189 — `secret show` probes the public lane BEFORE handing out its URL and
always gives the owner a working private (tailscale) fallback.

Incident (29.9.2026): `secret show` on dev2 printed ONLY
`https://drop-dev2.newlevel.media/<token>/`, unprobed; the controller tunnel was
mid-restart, Cloudflare answered 1033, and the owner had nothing else to open.

The harness mirrors `test_delivery_channel_1115.TestSecretShowFallbackLabelled`
(everything outside `_secret_show` is patched; nothing binds, nothing reaches
the network). `_secret_url_line` / `_secret_public_url_line` stay REAL so the
same one-shot token is visible on every printed line.
"""
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import cli_drop_lanes as dl
import cli_vault
import filedrop

HOST = "drop-dev2.newlevel.media"
TS_IP = "100.64.0.1"
PORT = 8870


class _ShowHarness(unittest.TestCase):
    def run_show(self, probe_code, bind_ip=TS_IP):
        self.probed = []
        self.popen_argv = None

        def probe(url, timeout=3, user_agent="x"):
            self.probed.append(url)
            return probe_code

        def popen(argv, **kw):
            self.popen_argv = argv
            return types.SimpleNamespace(pid=4321, poll=lambda: None)

        with tempfile.TemporaryDirectory() as td:
            st = mock.MagicMock()
            st.DEFAULT_ENDPOINT_TTL_S = 300
            st.log_path.return_value = Path(td) / "vault.log"
            opener = types.SimpleNamespace(
                open=lambda u, timeout=2: types.SimpleNamespace(status=204))
            with mock.patch.object(filedrop, "bind_ips", lambda: [TS_IP]), \
                 mock.patch.object(filedrop, "vault", st, create=True), \
                 mock.patch.object(cli_vault, "_secret_show_source",
                                   lambda args, s: ("env", "X", "X")), \
                 mock.patch.object(cli_vault, "_secret_bindable", lambda ip: True), \
                 mock.patch.object(cli_vault, "_secret_public_lane",
                                   lambda args: (HOST, PORT, bind_ip)), \
                 mock.patch.object(cli_vault, "_pick_free_port",
                                   lambda ips, rng: PORT), \
                 mock.patch.object(cli_vault, "_secret_opener", lambda: opener), \
                 mock.patch.object(dl, "_probe_public_status", probe), \
                 mock.patch("subprocess.Popen", popen):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    cli_vault._secret_show(types.SimpleNamespace(
                        name="X", file=None, cmd=[], ttl=30, port=None,
                        allow_plain=False, public=False))
                return out.getvalue(), err.getvalue()

    @staticmethod
    def url_lines(out):
        return [ln for ln in out.splitlines() if "://" in ln]


class TestPublicLaneDead(_ShowHarness):
    def test_1033_prints_tailscale_url_and_loud_degradation(self):
        out, err = self.run_show(530)
        lines = self.url_lines(out)
        self.assertTrue(lines, out)
        self.assertNotIn("https://%s" % HOST, out)       # a dead URL is never handed out
        self.assertIn("http://%s:%d/" % (TS_IP, PORT), lines[0])
        self.assertIn("DEGRADED", err)
        self.assertIn("1033", err)
        self.assertIn("public lane unreachable", err)    # the shared #1115 voice

    def test_connection_error_is_dead_too(self):
        out, err = self.run_show(None)
        self.assertNotIn("https://%s" % HOST, out)
        self.assertIn("http://%s:%d/" % (TS_IP, PORT), out)
        self.assertIn("DEGRADED", err)

    def test_probe_is_token_free(self):
        out, _ = self.run_show(530)
        token = self.url_lines(out)[0].split("/")[3]
        self.assertEqual(self.probed, ["https://%s/healthz" % HOST])
        self.assertNotIn(token, self.probed[0])


class TestPublicLaneAlive(_ShowHarness):
    def test_access_302_public_first_then_labelled_private(self):
        out, err = self.run_show(302)
        lines = self.url_lines(out)
        self.assertEqual(len(lines), 2, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST), lines)
        self.assertIn("http://%s:%d/" % (TS_IP, PORT), lines[1])
        self.assertIn("záloha", lines[1])
        self.assertNotIn("DEGRADED", err)
        # ONE endpoint, ONE token: both lines carry the same one-shot token
        self.assertEqual(lines[0].split("/")[3], lines[1].split("/")[3])

    def test_local_topology_also_binds_tailscale_for_the_fallback(self):
        out, _ = self.run_show(204, bind_ip="127.0.0.1")
        self.assertEqual(self.popen_argv[3], "127.0.0.1,%s" % TS_IP)
        lines = self.url_lines(out)
        self.assertEqual(len(lines), 2, out)
        self.assertNotIn("127.0.0.1", out)               # loopback is never offered


if __name__ == "__main__":
    unittest.main()
