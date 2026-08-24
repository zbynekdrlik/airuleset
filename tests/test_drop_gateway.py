"""Tests for cli_drop_gateway — the public-TLS drop lane for secret/upload (#664).

Offline + hermetic: no network, no real systemctl, no real cloudflared. The
tunnel-config augmentation, the marker round-trip, the channel-decision truth
table, and the reconcile command are all exercised with fakes/temp dirs.
"""
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_drop_gateway as dg  # noqa: E402


# A realistic spinbike-shaped cloudflared config: an ingress list whose last
# entry is the catch-all 404, and which ALSO serves the live spinbike.sk site.
SPINBIKE_CONFIG = (
    "tunnel: 4093c494-b31d-4eb7-8fcb-6c5948f5d4b2\n"
    "credentials-file: /home/newlevel/.cloudflared/4093c494.json\n"
    "\n"
    "ingress:\n"
    "  - hostname: spinbike.newlevel.media\n"
    "    service: http://localhost:8080\n"
    "  - hostname: spinbike-dev.newlevel.media\n"
    "    service: http://localhost:8081\n"
    "  - hostname: spinbike.sk\n"
    "    service: http://localhost:8080\n"
    "  - hostname: www.spinbike.sk\n"
    "    service: http://localhost:8080\n"
    "  - service: http_status:404\n"
)


class TestConstants(unittest.TestCase):
    def test_drop_port_distinct_from_every_other_range(self):
        # filedrop 8788, upload 8799-8819, secret 8830-8849, show 8850-8869.
        self.assertNotEqual(dg.DROP_PORT, 8788)
        for lo, hi in [(8799, 8819), (8830, 8849), (8850, 8869)]:
            self.assertFalse(lo <= dg.DROP_PORT <= hi,
                             "DROP_PORT %d collides with %d-%d" % (dg.DROP_PORT, lo, hi))

    def test_drop_hosts_are_flat_single_level_under_newlevel_media(self):
        # LOAD-BEARING: *.newlevel.media Universal SSL is ONE level, so a 2-level
        # host would have no valid edge cert and break mandatory TLS.
        for host in (dg.DROP_HOST_SPINBIKE, dg.DROP_HOST_DAVID):
            self.assertTrue(host.endswith(".newlevel.media"), host)
            label = host[: -len(".newlevel.media")]
            self.assertNotIn(".", label,
                             "%s is multi-level — no *.newlevel.media cert" % host)


class TestDropLaneRegistry(unittest.TestCase):
    def test_spinbike_lane(self):
        lane = dg.drop_lane_for_box("spinbike")
        self.assertIsNotNone(lane)
        self.assertEqual(lane.host, "drop-spinbike.newlevel.media")
        self.assertFalse(lane.access)            # owner box → token-only TLS
        self.assertTrue(lane.tunnel_system_unit)  # system unit → sudo restart

    def test_subdev_lane_is_access_gated(self):
        lane = dg.drop_lane_for_box("subdev")
        self.assertIsNotNone(lane)
        self.assertEqual(lane.host, "drop-david.newlevel.media")
        self.assertTrue(lane.access)             # double protection Access+token
        self.assertFalse(lane.tunnel_system_unit)  # --user unit

    def test_unknown_box_has_no_lane(self):
        self.assertIsNone(dg.drop_lane_for_box("dev1"))
        self.assertIsNone(dg.drop_lane_for_box("some-random-box"))


class TestIngressAugmentation(unittest.TestCase):
    def test_inserts_before_catchall_and_preserves_existing(self):
        out = dg.render_drop_ingress_augmentation(SPINBIKE_CONFIG,
                                                  "drop-spinbike.newlevel.media")
        # Every existing hostname survives.
        for h in ("spinbike.newlevel.media", "spinbike-dev.newlevel.media",
                  "spinbike.sk", "www.spinbike.sk"):
            self.assertIn("hostname: %s" % h, out)
        # The new ingress points at loopback:DROP_PORT.
        self.assertIn("- hostname: drop-spinbike.newlevel.media", out)
        self.assertIn("service: http://127.0.0.1:%d" % dg.DROP_PORT, out)
        # It sits BEFORE the catch-all (that ordering is what makes cloudflared
        # route the drop host instead of 404-ing it).
        self.assertLess(out.index("drop-spinbike.newlevel.media"),
                        out.index("http_status:404"))

    def test_new_entry_matches_existing_indentation(self):
        out = dg.render_drop_ingress_augmentation(SPINBIKE_CONFIG,
                                                  "drop-spinbike.newlevel.media")
        self.assertIn("  - hostname: drop-spinbike.newlevel.media\n"
                      "    service: http://127.0.0.1:%d\n" % dg.DROP_PORT, out)

    def test_idempotent_second_pass_is_a_noop(self):
        once = dg.render_drop_ingress_augmentation(SPINBIKE_CONFIG,
                                                  "drop-spinbike.newlevel.media")
        twice = dg.render_drop_ingress_augmentation(once,
                                                   "drop-spinbike.newlevel.media")
        self.assertEqual(once, twice)
        self.assertEqual(once.count("- hostname: drop-spinbike.newlevel.media"), 1)

    def test_refuses_when_no_catchall(self):
        broken = "ingress:\n  - hostname: x.newlevel.media\n    service: http://localhost:1\n"
        with self.assertRaises(ValueError):
            dg.render_drop_ingress_augmentation(broken, "drop-spinbike.newlevel.media")


class TestMarker(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "airuleset-drop.conf")

    def test_round_trip(self):
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.path)
        self.assertEqual(dg.read_drop_marker(self.path),
                         ("drop-david.newlevel.media", 8828))

    def test_absent_reads_none(self):
        self.assertIsNone(dg.read_drop_marker(self.path))

    def test_malformed_reads_none(self):
        Path(self.path).write_text("host=onlyhost\n", encoding="utf-8")
        self.assertIsNone(dg.read_drop_marker(self.path))  # no port → None
        Path(self.path).write_text("host=h\nport=notaninteger\n", encoding="utf-8")
        self.assertIsNone(dg.read_drop_marker(self.path))


class TestResolvePublicLane(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.present = os.path.join(self.tmp, "present.conf")
        self.absent = os.path.join(self.tmp, "absent.conf")
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.present)

    def test_no_marker_never_offers_public(self):
        # Even with --public, no live drop lane on this box → None (no 404 URL).
        self.assertIsNone(dg.resolve_public_lane(True, False, marker_path=self.absent))
        self.assertIsNone(dg.resolve_public_lane(True, True, marker_path=self.absent))

    def test_explicit_public_uses_lane(self):
        self.assertEqual(
            dg.resolve_public_lane(True, True, marker_path=self.present),
            ("drop-david.newlevel.media", 8828))

    def test_auto_fallback_when_no_encrypted_private(self):
        # No tailscale (have_encrypted_private=False) → public even without --public.
        self.assertEqual(
            dg.resolve_public_lane(False, False, marker_path=self.present),
            ("drop-david.newlevel.media", 8828))

    def test_encrypted_private_and_no_public_flag_keeps_today(self):
        # tailscale present + no --public → None (today's private-only behaviour).
        self.assertIsNone(dg.resolve_public_lane(False, True, marker_path=self.present))


class TestUrlLine(unittest.TestCase):
    def test_https_token_and_label(self):
        line = dg.public_url_line("drop-david.newlevel.media", "toktoktoktoktok664")
        self.assertIn("https://drop-david.newlevel.media/toktoktoktoktok664/", line)
        self.assertIn("TLS", line)


class TestRestartArgv(unittest.TestCase):
    def test_system_unit_uses_sudo(self):
        argv = dg._restart_argv(dg.drop_lane_for_box("spinbike"))
        self.assertEqual(argv[:3], ["sudo", "-n", "systemctl"])
        self.assertIn("spinbike-tunnel.service", argv)

    def test_user_unit_uses_user_flag(self):
        argv = dg._restart_argv(dg.drop_lane_for_box("subdev"))
        self.assertNotIn("sudo", argv)
        self.assertEqual(argv[:2], ["systemctl", "--user"])


def _args(**kw):
    return types.SimpleNamespace(**kw)


class TestCmdDropGateway(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")
        Path(self.cfg).write_text(SPINBIKE_CONFIG, encoding="utf-8")
        # Point the spinbike lane at our temp config for the duration of the test.
        self._orig = dg.DROP_LANES["spinbike"].tunnel_config
        dg.DROP_LANES["spinbike"].tunnel_config = Path(self.cfg)

    def tearDown(self):
        dg.DROP_LANES["spinbike"].tunnel_config = self._orig

    def test_no_lane_box_is_a_clean_noop(self):
        rc = dg.cmd_drop_gateway(_args(apply=False, _nodename="dev1"))
        self.assertEqual(rc, 0)

    def test_dry_run_does_not_write_config_or_marker(self):
        before = Path(self.cfg).read_text(encoding="utf-8")
        rc = dg.cmd_drop_gateway(_args(apply=False, _nodename="spinbike",
                                       _marker_path=self.marker))
        self.assertEqual(rc, 0)
        self.assertEqual(Path(self.cfg).read_text(encoding="utf-8"), before)
        self.assertFalse(os.path.exists(self.marker))

    def test_apply_writes_ingress_and_marker_and_restarts(self):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        rc = dg.cmd_drop_gateway(_args(apply=True, _nodename="spinbike",
                                       _marker_path=self.marker, _run=fake_run))
        self.assertEqual(rc, 0)
        out = Path(self.cfg).read_text(encoding="utf-8")
        self.assertIn("- hostname: drop-spinbike.newlevel.media", out)
        self.assertIn("http_status:404", out)  # catch-all preserved
        self.assertEqual(dg.read_drop_marker(self.marker),
                         ("drop-spinbike.newlevel.media", dg.DROP_PORT))
        # The spinbike tunnel (system unit) was restarted via sudo systemctl.
        self.assertTrue(any(a[:3] == ["sudo", "-n", "systemctl"] for a in calls))

    def test_apply_reports_failure_when_restart_fails(self):
        def failing_run(argv, **kw):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")

        rc = dg.cmd_drop_gateway(_args(apply=True, _nodename="spinbike",
                                       _marker_path=self.marker, _run=failing_run))
        self.assertEqual(rc, 1)
        # A failed restart must NOT claim the lane is live.
        self.assertFalse(os.path.exists(self.marker))


def _free_port():
    import socket
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    p = sk.getsockname()[1]
    sk.close()
    return p


class TestSecretPublicLaneHelper(unittest.TestCase):
    """The cli_vault `_secret_public_lane` channel-decision helper (#664)."""

    def setUp(self):
        import cli_vault
        self.cli_vault = cli_vault
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")

    def test_no_tailscale_box_with_live_marker_auto_uses_public(self):
        from unittest import mock
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.marker)
        with mock.patch("filedrop.bind_ips", return_value=["127.0.0.1"]), \
             mock.patch("filedrop._is_tailscale", return_value=False), \
             mock.patch.object(dg, "DROP_MARKER", self.marker):
            host, port = self.cli_vault._secret_public_lane(
                types.SimpleNamespace(public=False))
        self.assertEqual((host, port), ("drop-david.newlevel.media", 8828))

    def test_tailscale_box_without_flag_keeps_private(self):
        from unittest import mock
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.marker)
        with mock.patch("filedrop.bind_ips", return_value=["100.100.0.1"]), \
             mock.patch("filedrop._is_tailscale", return_value=True), \
             mock.patch.object(dg, "DROP_MARKER", self.marker):
            host, port = self.cli_vault._secret_public_lane(
                types.SimpleNamespace(public=False))
        self.assertIsNone(host)


class TestUploadPublicLaneEndToEnd(unittest.TestCase):
    """Full round-trip (#664): with a live drop lane, `cmd_upload` binds loopback
    on the drop port and advertises ONE public HTTPS URL — never the un-routable
    loopback address, never an scp/ssh -L ask."""

    def test_cmd_upload_public_lane(self):
        import airuleset
        import contextlib
        import io
        import tempfile
        from unittest import mock
        port = _free_port()          # ephemeral so parallel runs never collide
        dest = tempfile.mkdtemp()
        with mock.patch.object(dg, "resolve_public_lane",
                               return_value=("drop-david.newlevel.media", port)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_upload(types.SimpleNamespace(
                    dir=dest, ttl=4, port=None, public=True))
            out = buf.getvalue()
        self.assertIn("https://drop-david.newlevel.media/", out)
        self.assertIn("[verejné", out)                 # the labelled public line
        # The loopback origin is NEVER advertised to the user.
        self.assertNotIn("http://127.0.0.1", out)


if __name__ == "__main__":
    unittest.main()
