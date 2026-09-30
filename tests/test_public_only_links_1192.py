"""#1192 — owner-facing links are PUBLIC ONLY; tailscale/LAN only via `--private`.

Owner ROZHODNUTÉ (30.9.2026): „vadi mi ze stale vsade pchas tailscale ktore je
bezpecnostne riziko lebo ta siet dava pristup k pocitacom ktore by nemali byt
volne pristupne!“ — reversing the #1189 always-print-tailscale fallback line
and returning to the #1115 ruling (ONE public TLS channel).

Contract locked here, for all four producers (`secret show`, `secret request`,
`share`, `upload`):
  - a live public lane -> EXACTLY one URL line, the https one, no tailscale/LAN;
  - `--private` -> the tailscale/LAN URLs only (the public lane is not used);
  - no public lane, or a DEAD one (530/1033, a dead tunnel origin) -> exit
    non-zero, a loud Slovak+English line naming `--private`, and NO URL at all;
  - an Access 302 (tunnel not verifiable) -> the public URL + the note.
Everything outside the command is patched; nothing reaches the network.
"""
import contextlib
import io
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import cli_drop_gateway as dg
import cli_drop_lanes as dl
import cli_filedrop_watchdog as fw
import cli_vault
import cli_vault_delivery as vd
import filedrop
from filedrop import share as fshare

ROOT = Path(__file__).resolve().parent.parent
HOST = "drop-dev2.newlevel.media"
TS_IP = "100.64.0.1"
PORT = 8870
PRIVATE_PORT = 8830


def url_lines(text):
    return [ln for ln in text.splitlines() if "://" in ln]


# --------------------------------------------------------------------------- #
# The shared emitter (cli_vault_delivery.emit_urls)
# --------------------------------------------------------------------------- #

class TestEmitUrls(unittest.TestCase):
    def emit(self, code, *, private=False, public_host=HOST, origin_ip=None,
             live=True):
        self.probed = []

        def probe(url):
            self.probed.append(url)
            return code

        self.logged = []
        out, err = io.StringIO(), io.StringIO()
        channel = vd.emit_urls(
            "secret show", public_host, "TOK", [TS_IP],
            lambda ip: "http://%s:%d/TOK/   [tailscale]" % (ip, PRIVATE_PORT),
            lambda ip: live, private=private, origin_ip=origin_ip,
            log=self.logged.append, probe=probe, out=out, err=err)
        return channel, out.getvalue(), err.getvalue()

    def test_live_public_is_exactly_one_https_line(self):
        channel, out, err = self.emit(204)
        self.assertEqual(channel, "public")
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/TOK/" % HOST), lines)
        self.assertNotIn(TS_IP, out + err)

    def test_access_302_prints_public_and_the_note_naming_private(self):
        channel, out, err = self.emit(302)
        self.assertEqual(channel, "public-unverified")
        self.assertEqual(len(url_lines(out)), 1, out)
        self.assertTrue(url_lines(out)[0].startswith("https://%s/" % HOST))
        self.assertIn("NOT verified", err)
        self.assertIn("--private", err)
        self.assertNotIn(TS_IP, out)

    def test_dead_lane_refuses_with_no_url(self):
        channel, out, err = self.emit(530)
        self.assertEqual(channel, "refused")
        self.assertEqual(url_lines(out), [], out)
        self.assertIn("--private", err)
        self.assertIn("1033", err)
        self.assertIn("ŽIADNA URL", err)          # Slovak half of the loud line
        self.assertIn("NO URL", err)              # English half
        self.assertEqual(self.logged, ["public-lane-dead"])

    def test_dead_tunnel_origin_refuses_without_probing(self):
        channel, out, err = self.emit(204, origin_ip="100.99.0.9", live=False)
        self.assertEqual(channel, "refused")
        self.assertEqual(url_lines(out), [])
        self.assertIn("tunnel origin 100.99.0.9 down", err)
        self.assertEqual(self.probed, [])

    def test_no_public_lane_refuses_with_no_url(self):
        channel, out, err = self.emit(204, public_host=None)
        self.assertEqual(channel, "refused")
        self.assertEqual(url_lines(out), [])
        self.assertIn("--private", err)

    def test_private_prints_tailscale_only_and_never_probes(self):
        channel, out, err = self.emit(204, private=True)
        self.assertEqual(channel, "private")
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertIn("http://%s:%d/TOK/" % (TS_IP, PRIVATE_PORT), lines[0])
        self.assertNotIn("https://", out)
        self.assertEqual(self.probed, [])

    def test_private_with_nothing_live_refuses(self):
        channel, out, _err = self.emit(204, private=True, live=False)
        self.assertEqual(channel, "refused")
        self.assertEqual(url_lines(out), [])


class TestSelectLane(unittest.TestCase):
    def test_private_drops_the_public_lane(self):
        self.assertEqual(vd.select_lane("share", (HOST, PORT, "127.0.0.1"), True),
                         (None, None, None))

    def test_a_lane_passes_through(self):
        lane = (HOST, PORT, "127.0.0.1")
        self.assertEqual(vd.select_lane("share", lane, False), lane)

    def test_no_lane_without_private_exits_loudly(self):
        err = io.StringIO()
        with mock.patch.object(dl, "delivery_channel",
                               lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
             contextlib.redirect_stderr(err), \
             self.assertRaises(SystemExit) as cm:
            vd.select_lane("upload", None, False)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("upload:", err.getvalue())
        self.assertIn("--private", err.getvalue())


# --------------------------------------------------------------------------- #
# secret show / secret request (cli_vault)
# --------------------------------------------------------------------------- #

class _VaultHarness(unittest.TestCase):
    def run_cmd(self, probe_code, *, lane=(HOST, PORT, TS_IP), private=False,
                request=False, names=("X",)):
        self.probed = []
        self.popen_argv = None
        self.terminated = []

        def probe(url, timeout=3, user_agent="x"):
            self.probed.append(url)
            return probe_code

        def popen(argv, **kw):
            self.popen_argv = argv
            return types.SimpleNamespace(
                pid=4321, poll=lambda: None,
                terminate=lambda: self.terminated.append(True))

        with tempfile.TemporaryDirectory() as td:
            st = mock.MagicMock()
            st.DEFAULT_ENDPOINT_TTL_S = 300
            st.DEFAULT_KEEP_S = 3600
            st.state.return_value = "absent"
            st.register_request.return_value = "NONCEVALUE"
            st.log_path.return_value = Path(td) / "vault.log"
            self.st = st
            opener = types.SimpleNamespace(
                open=lambda u, timeout=2: types.SimpleNamespace(status=204))
            args = types.SimpleNamespace(
                name="X", file=None, cmd=[], ttl=30, keep=None, port=None,
                allow_plain=False, public=False, private=private, replace=False,
                persist=None, persist_map=None)
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
                                   lambda args: lane), \
                 mock.patch.object(cli_vault, "_pick_free_port",
                                   lambda ips, rng: PORT if PORT in rng else PRIVATE_PORT), \
                 mock.patch.object(cli_vault, "_secret_opener", lambda: opener), \
                 mock.patch.object(dg, "drop_lane_for_account",
                                   lambda *a, **k: types.SimpleNamespace(access=False)), \
                 mock.patch.object(dl, "delivery_channel",
                                   lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
                 mock.patch.object(dl, "_probe_public_status", probe), \
                 mock.patch("subprocess.Popen", popen):
                out, err = io.StringIO(), io.StringIO()
                self.exit_code = None
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        (cli_vault._secret_request if request
                         else cli_vault._secret_show)(args)
                    except SystemExit as e:
                        self.exit_code = e.code
                return out.getvalue(), err.getvalue()


class TestSecretShow(_VaultHarness):
    def test_live_lane_is_one_https_line(self):
        out, _err = self.run_cmd(204)
        self.assertIsNone(self.exit_code)
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST), lines)
        self.assertNotIn(TS_IP, out)

    def test_dead_lane_exits_nonzero_no_url_and_stops_the_endpoint(self):
        out, err = self.run_cmd(530)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(url_lines(out), [], out)
        self.assertIn("--private", err)
        self.assertEqual(self.terminated, [True])

    def test_no_lane_refuses_before_any_endpoint_starts(self):
        out, err = self.run_cmd(204, lane=(None, None, None))
        self.assertEqual(self.exit_code, 1)
        self.assertIsNone(self.popen_argv)
        self.assertEqual(url_lines(out), [])
        self.assertIn("--private", err)

    def test_private_uses_the_private_path_only(self):
        out, _err = self.run_cmd(204, private=True)
        self.assertIsNone(self.exit_code)
        self.assertEqual(self.probed, [])
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertIn("http://%s:%d/" % (TS_IP, PRIVATE_PORT), lines[0])
        self.assertNotIn("https://", out)
        self.assertEqual(self.popen_argv[2], str(PRIVATE_PORT))


class TestSecretRequest(_VaultHarness):
    def test_live_lane_is_one_https_line(self):
        out, _err = self.run_cmd(204, request=True)
        self.assertEqual(len(url_lines(out)), 1, out)
        self.assertTrue(url_lines(out)[0].startswith("https://%s/" % HOST))

    def test_dead_lane_revokes_every_pending_name_and_exits(self):
        out, err = self.run_cmd(530, request=True, names=("A", "B"))
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(url_lines(out), [])
        self.assertIn("secret:", err)
        forgotten = [c.args[0] for c in self.st.forget.call_args_list]
        self.assertEqual(forgotten, ["A", "B"])

    def test_no_lane_refuses_before_registering(self):
        _out, _err = self.run_cmd(204, request=True, lane=(None, None, None))
        self.assertEqual(self.exit_code, 1)
        self.assertIsNone(self.popen_argv)
        self.st.register_request.assert_not_called()

    def test_private_prints_tailscale_only(self):
        out, _err = self.run_cmd(204, request=True, private=True)
        self.assertNotIn("https://", out)
        self.assertIn("http://%s:%d/" % (TS_IP, PRIVATE_PORT), out)


class TestPrivateFlagParsing(unittest.TestCase):
    def test_remainder_head_flag(self):
        args = types.SimpleNamespace(cmd=["--private", "NAME"], ttl=None, keep=None,
                                     port=None, env=None, persist=None, file=None,
                                     persist_map=None)
        cli_vault._secret_apply_remainder(args)
        self.assertTrue(getattr(args, "private", False))

    def test_request_names_trailing_flag(self):
        args = types.SimpleNamespace(name="A", cmd=["B", "--private"])
        self.assertEqual(cli_vault._secret_request_names(args), ["A", "B"])
        self.assertTrue(args.private)

    def test_argparse_offers_private_on_all_four(self):
        # `secret` covers both show and request (one parser); its REMAINDER
        # would swallow a trailing --help, so ask each subcommand's own help.
        for sub in ("secret", "share", "upload"):
            r = subprocess.run([sys.executable, str(ROOT / "airuleset.py"), sub,
                                "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, (sub, r.stderr))
            self.assertIn("--private", r.stdout, sub)


# --------------------------------------------------------------------------- #
# share (cli_filedrop_watchdog.cmd_share)
# --------------------------------------------------------------------------- #

class TestShare(unittest.TestCase):
    LOCAL_URL = "http://100.64.0.1:8790/TOKTOKTOKTOKTOKTOK12/rec.wav"
    LANE = ("drop-david.newlevel.media", 8870, "100.118.174.27")
    PRIVATE = ("http://100.64.0.1:8790/TOKTOKTOKTOKTOKTOK12/rec.wav",
               "http://192.168.1.5:8790/TOKTOKTOKTOKTOKTOK12/rec.wav")

    def run_share(self, lane_full, status, *, private=False):
        self.resolved = []

        def resolve(*a, **k):
            self.resolved.append(True)
            return lane_full

        with mock.patch.object(fshare, "share",
                               lambda path, base_dir=None:
                               (self.LOCAL_URL, Path("/tmp/x/TOK/rec.wav"))), \
             mock.patch.object(filedrop, "advertise_urls",
                               lambda port=None, path="": list(self.PRIVATE)), \
             mock.patch.object(fw, "_filedrop_is_live", lambda u, timeout=2: True), \
             mock.patch.object(dg, "resolve_public_lane_full", resolve), \
             mock.patch.object(dl, "delivery_channel",
                               lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
             mock.patch.object(fw, "_public_share_status",
                               lambda u, timeout=3: status):
            out, err = io.StringIO(), io.StringIO()
            self.exit_code = None
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    fw.cmd_share(types.SimpleNamespace(path="/tmp/rec.wav",
                                                       private=private))
                except SystemExit as e:
                    self.exit_code = e.code
            return out.getvalue(), err.getvalue()

    def test_live_is_exactly_one_https_line(self):
        out, _err = self.run_share(self.LANE, 200)
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://drop-david.newlevel.media/s/"))

    def test_access_302_is_one_https_line(self):
        out, _err = self.run_share(self.LANE, 302)
        self.assertEqual(len(url_lines(out)), 1, out)

    def test_dead_lane_exits_nonzero_with_no_url(self):
        out, err = self.run_share(self.LANE, 502)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(url_lines(out), [], out)
        self.assertIn("--private", err)
        self.assertIn("502", err)

    def test_no_lane_exits_nonzero_with_no_url(self):
        out, err = self.run_share(None, 200)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(url_lines(out), [])
        self.assertIn("--private", err)

    def test_private_prints_private_only(self):
        out, _err = self.run_share(self.LANE, 200, private=True)
        self.assertIsNone(self.exit_code)
        self.assertNotIn("https://", out)
        self.assertIn("192.168.1.5", out)
        self.assertEqual(self.resolved, [])


# --------------------------------------------------------------------------- #
# upload (airuleset.cmd_upload)
# --------------------------------------------------------------------------- #

class TestUpload(unittest.TestCase):
    def run_upload(self, lane, probe_code, *, private=False):
        import airuleset
        self.popen_argv = None
        self.terminated = []
        self.probed = []

        def popen(argv, **kw):
            self.popen_argv = argv
            return types.SimpleNamespace(
                pid=1, poll=lambda: None,
                terminate=lambda: self.terminated.append(True))

        def urlopen(u, timeout=2):
            return types.SimpleNamespace(status=200)

        def probe(url, timeout=3, user_agent="x"):
            self.probed.append(url)
            return probe_code

        dest = tempfile.mkdtemp()
        logdir = tempfile.mkdtemp()
        with mock.patch.object(filedrop, "bind_ips", lambda: [TS_IP, "192.168.1.5"]), \
             mock.patch.object(dg, "resolve_public_lane_full", lambda *a, **k: lane), \
             mock.patch.object(dl, "delivery_channel",
                               lambda *a, **k: (None, dl.CHANNEL_NO_LANE)), \
             mock.patch.object(dl, "_probe_public_status", probe), \
             mock.patch.object(airuleset, "_pick_free_port", lambda ips, rng: 8799), \
             mock.patch.object(airuleset, "_upload_log_path",
                               lambda port: Path(logdir) / ("u-%d.log" % port)), \
             mock.patch("urllib.request.urlopen", urlopen), \
             mock.patch("subprocess.Popen", popen):
            out, err = io.StringIO(), io.StringIO()
            self.exit_code = None
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    airuleset.cmd_upload(types.SimpleNamespace(
                        dir=dest, ttl=60, port=None, public=False, private=private))
                except SystemExit as e:
                    self.exit_code = e.code
            return out.getvalue(), err.getvalue()

    def test_live_lane_is_one_https_line_after_a_probe(self):
        out, _err = self.run_upload((HOST, PORT, "127.0.0.1"), 204)
        self.assertIsNone(self.exit_code)
        lines = url_lines(out)
        self.assertEqual(len(lines), 1, out)
        self.assertTrue(lines[0].startswith("https://%s/" % HOST))
        self.assertEqual(self.probed, ["https://%s/healthz" % HOST])

    def test_dead_lane_exits_nonzero_no_url_and_stops_the_endpoint(self):
        out, err = self.run_upload((HOST, PORT, "127.0.0.1"), 530)
        self.assertEqual(self.exit_code, 1)
        self.assertEqual(url_lines(out), [], out)
        self.assertIn("--private", err)
        self.assertEqual(self.terminated, [True])

    def test_no_lane_refuses_before_the_endpoint_starts(self):
        out, err = self.run_upload(None, 204)
        self.assertEqual(self.exit_code, 1)
        self.assertIsNone(self.popen_argv)
        self.assertIn("--private", err)
        self.assertEqual(url_lines(out), [])

    def test_private_prints_the_private_urls_only(self):
        out, _err = self.run_upload((HOST, PORT, "127.0.0.1"), 204, private=True)
        self.assertIsNone(self.exit_code)
        self.assertNotIn("https://", out)
        self.assertIn("http://%s:8799/" % TS_IP, out)
        self.assertEqual(self.probed, [])


class TestUploadServerHealthz(unittest.TestCase):
    """The upload endpoint answers the token-free `/healthz` (204) like the
    show/vault endpoints, so the public-lane probe is uniform (#1192)."""

    def test_healthz_is_204_and_token_free(self):
        import socket
        sk = socket.socket()
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
        sk.close()
        dest = tempfile.mkdtemp()
        proc = subprocess.Popen(
            [sys.executable, str(ROOT / "filedrop" / "upload_server.py"),
             "TOKENTOKENTOKEN1", str(port), "127.0.0.1", dest, "30"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        code = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and code is None:
            try:
                code = urllib.request.urlopen(
                    "http://127.0.0.1:%d/healthz" % port, timeout=1).status
            except urllib.error.HTTPError as e:
                code = e.code
            except OSError:
                time.sleep(0.1)
        self.assertEqual(code, 204)


class TestModulesStopOfferingTailscale(unittest.TestCase):
    def test_deliver_module_says_public_only(self):
        t = (ROOT / "modules/core/deliver-files-as-urls.md").read_text()
        self.assertNotIn("tailscale/LAN the labelled fallback", t)
        self.assertIn("--private", t)

    def test_receive_module_says_public_only(self):
        t = (ROOT / "modules/core/receive-files-via-upload-url.md").read_text()
        self.assertNotIn("one URL per interface", t)
        self.assertIn("--private", t)


if __name__ == "__main__":
    unittest.main()
