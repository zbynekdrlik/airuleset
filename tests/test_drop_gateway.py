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

    def test_out_of_range_port_reads_none(self):
        # An out-of-range port must fail-safe to None, never reach socket.bind
        # (which would raise OverflowError) — #664 review.
        for bad in ("0", "-1", "70000", "999999"):
            Path(self.path).write_text("host=h\nport=%s\n" % bad, encoding="utf-8")
            self.assertIsNone(dg.read_drop_marker(self.path), bad)

    def test_marker_file_is_mode_600(self):
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)


class TestResolvePublicLane(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.present = os.path.join(self.tmp, "present.conf")
        self.absent = os.path.join(self.tmp, "absent.conf")
        # A marker matching subdev's registered lane host.
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.present)

    def _r(self, want, have, marker, nodename="subdev", username="newlevel"):
        # username defaults to a NON-consumer account so the pre-#786 truth-table
        # tests stay deterministic regardless of the REAL invoking account — the
        # suite also runs on david1@subdev (a #786 consumer), where a username-less
        # resolve would resolve the live account and flip the default to public.
        return dg.resolve_public_lane(want, have, marker_path=marker,
                                      nodename=nodename, username=username)

    def test_no_marker_never_offers_public(self):
        # Even with --public, no live drop lane on this box → None (no 404 URL).
        self.assertIsNone(self._r(True, False, self.absent))
        self.assertIsNone(self._r(True, True, self.absent))

    def test_explicit_public_uses_lane_from_registry(self):
        # Returned host/port are the REGISTRY's, not the marker's raw strings.
        self.assertEqual(self._r(True, True, self.present),
                         ("drop-david.newlevel.media", dg.DROP_PORT))

    def test_auto_fallback_when_no_encrypted_private(self):
        # No tailscale (have_encrypted_private=False) → public even without --public.
        self.assertEqual(self._r(False, False, self.present),
                         ("drop-david.newlevel.media", dg.DROP_PORT))

    def test_encrypted_private_and_no_public_flag_keeps_today(self):
        # tailscale present + no --public → None (today's private-only behaviour).
        self.assertIsNone(self._r(False, True, self.present))

    def test_box_with_no_registered_lane_never_offers_public(self):
        # A marker on a box that isn't in DROP_LANES → None (registry-gated).
        self.assertIsNone(self._r(True, False, self.present, nodename="dev1"))

    def test_mismatched_marker_host_is_refused(self):
        # A stale/foreign marker whose host != this box's registered lane host is
        # refused — a credential URL is never routed to a mutable marker host (A-M2).
        wrong = os.path.join(self.tmp, "wrong.conf")
        dg.write_drop_marker("attacker.example.com", 8828, path=wrong)
        self.assertIsNone(self._r(True, False, wrong))

    # --- #786: the CONSUMER, not the box, drives the default channel. ---
    def test_david_consumer_defaults_to_public_without_flag(self):
        # subdev HAS tailscale (have_encrypted_private=True) and no --public, but
        # david1/david2's CONSUMER (David's laptop) has none — so the public lane
        # is the DEFAULT, no --public needed. THIS is the #786 fix.
        self.assertEqual(
            dg.resolve_public_lane(False, True, marker_path=self.present,
                                   nodename="subdev", username="david1"),
            ("drop-david.newlevel.media", dg.DROP_PORT))
        self.assertEqual(
            dg.resolve_public_lane(False, True, marker_path=self.present,
                                   nodename="subdev", username="david2"),
            ("drop-david.newlevel.media", dg.DROP_PORT))

    def test_non_david_consumer_on_subdev_keeps_private(self):
        # marek/montalu consumers on the SAME box DO have tailscale → unchanged
        # private-by-default behaviour (the force is per-account, not per-box).
        self.assertIsNone(
            dg.resolve_public_lane(False, True, marker_path=self.present,
                                   nodename="subdev", username="marek"))
        self.assertIsNone(
            dg.resolve_public_lane(False, True, marker_path=self.present,
                                   nodename="subdev", username="montalu"))

    def test_david_consumer_force_still_needs_a_live_marker(self):
        # The consumer force flips only the DEFAULT — it can NEVER invent a lane:
        # no live marker on this box → None even for david1 (no 404 URL).
        self.assertIsNone(
            dg.resolve_public_lane(False, True, marker_path=self.absent,
                                   nodename="subdev", username="david1"))


class TestConsumerForcesPublic(unittest.TestCase):
    def test_david_accounts_on_subdev_force_public(self):
        self.assertTrue(dg.consumer_forces_public("subdev", "david1"))
        self.assertTrue(dg.consumer_forces_public("subdev", "david2"))

    def test_non_david_account_on_subdev_does_not_force(self):
        for u in ("marek", "montalu", "newlevel", "gatekeeper"):
            self.assertFalse(dg.consumer_forces_public("subdev", u), u)

    def test_david_name_on_other_box_does_not_force(self):
        # The force is keyed on (box, account); a david-named account elsewhere
        # (were there one) does NOT force — only the registered subdev tuples do.
        self.assertFalse(dg.consumer_forces_public("dev1", "david1"))
        self.assertFalse(dg.consumer_forces_public("spinbike", "david1"))

    def test_unresolvable_username_fails_safe_false(self):
        # Any error resolving the invoking account → False (today's box-driven
        # behaviour), never a spurious public force.
        def _boom():
            raise OSError("no pwd entry")
        orig = dg._current_username
        dg._current_username = _boom
        self.addCleanup(setattr, dg, "_current_username", orig)
        self.assertFalse(dg.consumer_forces_public("subdev", None))


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


class TestRestartEnv(unittest.TestCase):
    """#826: a `--user` tunnel restart runs over a NON-LOGIN ssh install session,
    where XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS are unset, so `systemctl
    --user` cannot find the per-user bus and fails 'Failed to connect to bus: No
    medium found'. The restart env MUST carry both, pointing at /run/user/<uid>.
    A SYSTEM unit runs via `sudo -n systemctl` (which resets env itself) and
    needs no such env → inherit (None)."""

    def test_user_unit_env_carries_the_user_bus(self):
        import unittest.mock as m
        uid = os.getuid()
        # Clear any ambient values so we exercise the deterministic fallback a
        # non-login ssh install session actually gets (both genuinely unset there).
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            env = dg._restart_env(dg.drop_lane_for_box("subdev"))
        self.assertIsNotNone(env, "a --user restart MUST carry an explicit env")
        self.assertEqual(env.get("XDG_RUNTIME_DIR"), "/run/user/%d" % uid)
        self.assertEqual(env.get("DBUS_SESSION_BUS_ADDRESS"),
                         "unix:path=/run/user/%d/bus" % uid)

    def test_user_unit_env_keeps_an_ambient_value(self):
        # setdefault: a real logind session's own XDG_RUNTIME_DIR must WIN over
        # the deterministic fallback (never clobber a correct live value) — AND
        # the DBUS address must stay COHERENT with it (derived from the EFFECTIVE
        # XDG, never re-derived from the uid), or sd-bus would prefer a mismatched
        # /run/user/<uid>/bus over the working /run/user/4242/bus (review #826).
        import unittest.mock as m
        with m.patch.dict(os.environ,
                          {"XDG_RUNTIME_DIR": "/run/user/4242"}, clear=False):
            os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
            env = dg._restart_env(dg.drop_lane_for_box("subdev"))
        self.assertEqual(env.get("XDG_RUNTIME_DIR"), "/run/user/4242")
        self.assertEqual(env.get("DBUS_SESSION_BUS_ADDRESS"),
                         "unix:path=/run/user/4242/bus")

    def test_system_unit_env_is_inherit_none(self):
        self.assertIsNone(dg._restart_env(dg.drop_lane_for_box("spinbike")))


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
        lane = dg.drop_lane_for_box("subdev")
        with mock.patch("filedrop.bind_ips", return_value=["127.0.0.1"]), \
             mock.patch("filedrop._is_tailscale", return_value=False), \
             mock.patch.object(dg, "drop_lane_for_box", return_value=lane), \
             mock.patch.object(dg, "DROP_MARKER", self.marker):
            host, port = self.cli_vault._secret_public_lane(
                types.SimpleNamespace(public=False))
        self.assertEqual((host, port), ("drop-david.newlevel.media", dg.DROP_PORT))

    def test_tailscale_box_without_flag_keeps_private(self):
        from unittest import mock
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.marker)
        lane = dg.drop_lane_for_box("subdev")
        with mock.patch("filedrop.bind_ips", return_value=["100.100.0.1"]), \
             mock.patch("filedrop._is_tailscale", return_value=True), \
             mock.patch.object(dg, "drop_lane_for_box", return_value=lane), \
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


class TestCmdDropGatewayRerunAfterFailedRestart(unittest.TestCase):
    """#664 review C1: a re-run after a failed restart must RESTART again (not
    silently write the LIVE marker over a tunnel that never reloaded the ingress).
    Uses the spinbike lane (access=False) so Access never gates the marker."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")
        Path(self.cfg).write_text(SPINBIKE_CONFIG, encoding="utf-8")
        self._orig = dg.DROP_LANES["spinbike"].tunnel_config
        dg.DROP_LANES["spinbike"].tunnel_config = Path(self.cfg)

    def tearDown(self):
        dg.DROP_LANES["spinbike"].tunnel_config = self._orig

    def test_second_apply_still_restarts_and_only_then_marks_live(self):
        calls1, calls2 = [], []

        def failing(argv, **kw):
            calls1.append(argv)
            return types.SimpleNamespace(returncode=1, stdout="", stderr="down")

        def ok(argv, **kw):
            calls2.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        # Apply #1: config gets the ingress, restart FAILS → no marker.
        rc1 = dg.cmd_drop_gateway(_args(apply=True, _nodename="spinbike",
                                        _marker_path=self.marker, _run=failing))
        self.assertEqual(rc1, 1)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(calls1, "apply #1 must have attempted a restart")

        # Apply #2: config already carries the ingress (changed=False) AND the
        # marker is still absent → it MUST restart again, then mark live.
        rc2 = dg.cmd_drop_gateway(_args(apply=True, _nodename="spinbike",
                                        _marker_path=self.marker, _run=ok))
        self.assertEqual(rc2, 0)
        self.assertTrue(calls2, "apply #2 must restart even though config is unchanged")
        self.assertEqual(dg.read_drop_marker(self.marker),
                         ("drop-spinbike.newlevel.media", dg.DROP_PORT))


class TestAccessGatedMarker(unittest.TestCase):
    """#664 review B-M3: an access-gated lane goes LIVE only when the Access
    reconcile succeeds — a failing Access must block the marker and return 1."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")
        # A david-shaped config (its tunnel uuid) so the m1 uuid check passes.
        Path(self.cfg).write_text(
            "tunnel: 1564fe31-a95f-4053-93d4-baff2b8a6e97\n"
            "credentials-file: /home/david1/.cloudflared/x.json\n\n"
            "ingress:\n"
            "  - hostname: david.newlevel.media\n"
            "    service: http://127.0.0.1:8081\n"
            "  - service: http_status:404\n", encoding="utf-8")
        self._orig = dg.DROP_LANES["subdev"].tunnel_config
        dg.DROP_LANES["subdev"].tunnel_config = Path(self.cfg)

    def tearDown(self):
        dg.DROP_LANES["subdev"].tunnel_config = self._orig

    def test_failed_access_reconcile_blocks_the_marker(self):
        from unittest import mock

        def ok_restart(argv, **kw):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(dg, "_reconcile_access",
                               return_value=(False, "Access ERROR: boom")):
            rc = dg.cmd_drop_gateway(_args(apply=True, _nodename="subdev",
                                           _marker_path=self.marker, _run=ok_restart))
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(self.marker))


class TestUuidMismatchRefused(unittest.TestCase):
    """#664 review m1: refuse to edit a config whose `tunnel:` UUID is not the
    lane's — never graft the drop ingress onto the wrong tunnel."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        Path(self.cfg).write_text(
            "tunnel: 00000000-dead-beef-0000-000000000000\n"
            "ingress:\n  - service: http_status:404\n", encoding="utf-8")
        self._orig = dg.DROP_LANES["spinbike"].tunnel_config
        dg.DROP_LANES["spinbike"].tunnel_config = Path(self.cfg)

    def tearDown(self):
        dg.DROP_LANES["spinbike"].tunnel_config = self._orig

    def test_wrong_tunnel_uuid_refused(self):
        rc = dg.cmd_drop_gateway(_args(apply=True, _nodename="spinbike",
                                       _marker_path=os.path.join(self.tmp, "m")))
        self.assertEqual(rc, 1)


class TestReconcileDropIngressOnInstall(unittest.TestCase):
    """#664 review A-M1: install re-asserts a live drop ingress that a webterm
    tunnel re-provision would otherwise clobber."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")
        self._orig = dg.DROP_LANES["spinbike"].tunnel_config
        dg.DROP_LANES["spinbike"].tunnel_config = Path(self.cfg)

    def tearDown(self):
        dg.DROP_LANES["spinbike"].tunnel_config = self._orig

    def _run_noop(self):
        calls = []

        def r(argv, **kw):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return calls, r

    def test_no_lane_box_is_ok_noop(self):
        # #826: a box with NO drop lane (dev1, and the overwhelming majority of
        # the fleet) is a benign no-op — reconcile must report OK (True), NOT the
        # same False a genuine failure returns, or `cmd_install`'s
        # `if not reconcile(): install_failed = True` would fail EVERY box.
        calls, r = self._run_noop()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="dev1", marker_path=self.marker))
        self.assertEqual(calls, [])

    def test_lane_without_marker_is_ok_noop(self):
        # #826: a lane that never went live (no marker) is also a benign no-op → OK.
        Path(self.cfg).write_text(SPINBIKE_CONFIG, encoding="utf-8")
        calls, r = self._run_noop()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="spinbike", marker_path=self.marker))  # no marker
        self.assertEqual(calls, [])

    def test_restart_failure_is_not_ok(self):
        # #826: a GENUINE failure (marker present, ingress clobbered, restart
        # rc!=0) must report NOT-ok (False) so cmd_install latches install_failed
        # and `push` exits non-zero instead of reporting OK over a stale tunnel.
        Path(self.cfg).write_text(SPINBIKE_CONFIG, encoding="utf-8")
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828, path=self.marker)

        def failing(argv, **kw):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="down")
        self.assertFalse(dg.reconcile_drop_ingress_on_install(
            run=failing, nodename="spinbike", marker_path=self.marker))

    def test_unreadable_config_is_not_ok(self):
        # #826: marker present but the tunnel config is unreadable/missing is a
        # genuine failure (a live drop lane whose config we cannot heal) → NOT-ok.
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828, path=self.marker)
        # self.cfg was never written → read_text raises OSError inside reconcile.
        calls, r = self._run_noop()
        self.assertFalse(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="spinbike", marker_path=self.marker))
        self.assertEqual(calls, [], "no restart attempted when the config is unreadable")

    def test_clobbered_ingress_is_re_added_and_restarted(self):
        # Simulate the webterm re-provision: config WITHOUT the drop ingress, but
        # the marker says the lane already went live.
        Path(self.cfg).write_text(SPINBIKE_CONFIG, encoding="utf-8")
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828, path=self.marker)
        calls, r = self._run_noop()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="spinbike", marker_path=self.marker))
        self.assertIn("- hostname: drop-spinbike.newlevel.media",
                      Path(self.cfg).read_text(encoding="utf-8"))
        self.assertTrue(calls, "must restart after re-adding the ingress")

    def test_present_ingress_is_noop_no_restart(self):
        once = dg.render_drop_ingress_augmentation(
            SPINBIKE_CONFIG, "drop-spinbike.newlevel.media")
        Path(self.cfg).write_text(once, encoding="utf-8")
        dg.write_drop_marker("drop-spinbike.newlevel.media", 8828, path=self.marker)
        calls, r = self._run_noop()
        self.assertTrue(dg.reconcile_drop_ingress_on_install(
            run=r, nodename="spinbike", marker_path=self.marker))
        self.assertEqual(calls, [], "no restart when the ingress is already present")


class TestReconcileRestartCarriesUserBusEnv(unittest.TestCase):
    """#826: the restart INSIDE reconcile_drop_ingress_on_install must pass the
    user-bus env to `run` for a --user lane — the exact call that failed 'No
    medium found' on david1@subdev over a non-login ssh install (bare
    subprocess.run with no env)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.cfg = os.path.join(self.tmp, "config.yml")
        self.marker = os.path.join(self.tmp, "airuleset-drop.conf")
        # A david-shaped config (subdev lane uuid, --user unit) WITHOUT the drop
        # ingress, but the marker says the lane already went live → reconcile
        # re-adds the ingress and restarts.
        Path(self.cfg).write_text(
            "tunnel: 1564fe31-a95f-4053-93d4-baff2b8a6e97\n"
            "credentials-file: /home/david1/.cloudflared/x.json\n\n"
            "ingress:\n"
            "  - hostname: david.newlevel.media\n"
            "    service: http://127.0.0.1:8081\n"
            "  - service: http_status:404\n", encoding="utf-8")
        self._orig = dg.DROP_LANES["subdev"].tunnel_config
        dg.DROP_LANES["subdev"].tunnel_config = Path(self.cfg)
        dg.write_drop_marker("drop-david.newlevel.media", 8828, path=self.marker)

    def tearDown(self):
        dg.DROP_LANES["subdev"].tunnel_config = self._orig

    def test_user_lane_restart_receives_env_with_the_bus(self):
        captured = {}

        def r(argv, **kw):
            captured["argv"] = argv
            captured["kw"] = kw
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        dg.reconcile_drop_ingress_on_install(
            run=r, nodename="subdev", marker_path=self.marker)
        self.assertEqual(captured["argv"][:2], ["systemctl", "--user"])
        env = captured["kw"].get("env")
        self.assertIsNotNone(env, "the --user restart MUST carry an env (#826)")
        self.assertIn("XDG_RUNTIME_DIR", env)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS", env)


class TestSecretShowLeadingFlagName(unittest.TestCase):
    """#664 review: `secret show --public NAME` (flag before name) must recover
    the NAME that argparse's REMAINDER swallowed, mirroring `secret request`."""

    def test_recovers_name_from_remainder(self):
        import cli_vault
        # _secret_apply_remainder has already stripped the leading --public into
        # args.public, leaving the NAME in cmd. _secret_show must adopt it and
        # then fail at "not stored" (exit 1), NOT at "needs a NAME" (exit 2).
        args = types.SimpleNamespace(name=None, file=None, cmd=["NOTSTORED664"],
                                     public=True, ttl=None, allow_plain=False, port=None)
        with self.assertRaises(SystemExit) as cm:
            cli_vault._secret_show(args)
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(args.name, "NOTSTORED664")

    def test_no_name_anywhere_still_errors(self):
        import cli_vault
        args = types.SimpleNamespace(name=None, file=None, cmd=[],
                                     public=False, ttl=None, allow_plain=False, port=None)
        with self.assertRaises(SystemExit) as cm:
            cli_vault._secret_show(args)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
