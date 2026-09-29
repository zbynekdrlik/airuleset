"""#1191 — a local drop lane's tunnel restart goes through the #1189 overlap.

`drop-gateway` edits a LOCAL lane's cloudflared ingress (spinbike's system unit,
the marek/dominika `--user` units on subdev) and used to load it with a plain
`systemctl restart`: stop-then-start, 30 s grace, every hostname on the tunnel
answers 1033 meanwhile. These tests pin the fix:

- a `--user` lane's re-assert restarts via the overlap (overlap unit rendered
  beside the lane's unit, `systemctl --user` with the #826 bus env);
- the system-unit lane overlaps under its sudo grant (`sudo -n -l` checked) and
  falls back to a LOUD plain restart when the grant does not cover it;
- an unchanged ingress never restarts;
- the marek/dominika lanes edit the config their rendered unit RUNS (the #889
  copy-paste pointed them at `config.yml` while the unit runs
  `webterm-<name>.yml`), and that unit carries the matching `TUNNEL_PIDFILE`.

Hermetic: a fake systemd answers every `run` call; HOME and the system unit dir
point at temp dirs; `sleep` never waits (the fake registers synchronously).
"""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_drop_gateway as dg  # noqa: E402
import cli_tunnel_apply as ta  # noqa: E402

DOMINIKA_UUID = "7792f710-16fb-41da-b46d-1d7b1cd0f8a6"
MAREK_UUID = "1e9555d1-4d19-4e86-8064-361506fbc2cd"
SPINBIKE_UUID = "4093c494-b31d-4eb7-8fcb-6c5948f5d4b2"


def _config(uuid, hostname):
    return ("tunnel: %s\ncredentials-file: /x/%s.json\n\ningress:\n"
            "  - hostname: %s\n    service: http://127.0.0.1:8082\n"
            "  - service: http_status:404\n" % (uuid, uuid, hostname))


class FakeSystemd:
    """Answers the `run(argv, **kw)` seam like systemd + sudo would. The overlap
    and the restarted main 'register' synchronously by writing their pidfiles."""

    def __init__(self, config_path, unit, *, env_pidfile=True, user="",
                 exec_config=None, grant=True, binary="/opt/cf/cloudflared"):
        self.config_path = Path(config_path)
        self.unit = unit
        self.overlap = ta.overlap_service_name(unit)
        self.env_pidfile = env_pidfile
        self.user = user
        self.exec_config = exec_config or str(config_path)
        self.grant = grant
        self.binary = binary
        self.calls = []          # (argv, kw)
        self.main_pid = 0

    def systemctl_calls(self):
        """The systemctl argv tails (prefix stripped), in order."""
        out = []
        for argv, _kw in self.calls:
            if argv[:3] == ["sudo", "-n", "systemctl"]:
                out.append(argv[3:])
            elif argv[:2] == ["systemctl", "--user"]:
                out.append(argv[2:])
            elif argv[:1] == ["systemctl"]:
                out.append(argv[1:])
        return out

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        ok = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:3] == ["sudo", "-n", "-l"]:
            return ok if self.grant else types.SimpleNamespace(
                returncode=1, stdout="", stderr="not allowed")
        if argv[:3] == ["sudo", "-n", "systemctl"]:
            args = argv[3:]
        elif argv[:2] == ["systemctl", "--user"]:
            args = argv[2:]
        elif argv[:1] == ["systemctl"]:
            args = argv[1:]
        else:
            return ok                                    # sudo mkdir / tee
        if args[:1] == ["show"] and "ExecStart" in args:
            env = ("TUNNEL_PIDFILE=%s" % ta.tunnel_pidfile(self.config_path)
                   if self.env_pidfile else "")
            ok.stdout = (
                "ExecStart={ path=%s ; argv[]=%s tunnel --no-autoupdate --config %s "
                "run ; ignore_errors=no ; start_time=[n/a] ; pid=0 ; code=(null) }\n"
                "Environment=%s\nUser=%s\n"
                % (self.binary, self.binary, self.exec_config, env, self.user))
        elif args[:3] == ["show", "-p", "ActiveState"]:
            ok.stdout = "inactive\n"
        elif args[:3] == ["show", "-p", "MainPID"]:
            ok.stdout = "%d\n" % self.main_pid
        elif args[:3] == ["start", "--no-block", self.overlap]:
            ta.overlap_pidfile(self.config_path).write_text("111\n")
        elif args[:3] == ["restart", "--no-block", self.unit]:
            self.main_pid = 222
            ta.tunnel_pidfile(self.config_path).write_text("222\n")
        return ok


class _LaneCase(unittest.TestCase):
    """Points one lane at a temp config + marker; HOME at a temp dir."""
    KEY = None
    UUID = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / ".cloudflared").mkdir(parents=True)
        self.lane = dg.DROP_LANES[self.KEY]
        self.cfg = self.home / ".cloudflared" / Path(self.lane.tunnel_config).name
        self.marker = str(self.tmp / "airuleset-drop.conf")
        self._orig = self.lane.tunnel_config
        self.lane.tunnel_config = self.cfg
        self._env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.lane.tunnel_config = self._orig

    def _reconcile(self, fake):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = dg.reconcile_drop_ingress_on_install(
                run=fake, nodename=self.KEY[0], marker_path=self.marker,
                username=self.KEY[1])
        return ok, err.getvalue()


class TestUserLaneReassertOverlaps(_LaneCase):
    KEY = ("subdev", "dominika")

    def setUp(self):
        super().setUp()
        self.cfg.write_text(_config(DOMINIKA_UUID, "dominika.newlevel.media"))
        dg.write_drop_marker(self.lane.host, self.lane.port, path=self.marker)

    def test_edited_ingress_restarts_via_the_overlap(self):
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service)
        ok, err = self._reconcile(fake)
        self.assertTrue(ok, err)
        self.assertIn("- hostname: %s" % self.lane.host, self.cfg.read_text())
        calls = fake.systemctl_calls()
        overlap = ta.overlap_service_name(self.lane.tunnel_service)
        start = calls.index(["start", "--no-block", overlap])
        restart = calls.index(["restart", "--no-block", self.lane.tunnel_service])
        stop = calls.index(["stop", "--no-block", overlap])
        self.assertLess(start, restart, "the overlap serves BEFORE the main restarts")
        self.assertLess(restart, stop, "the overlap stops only after the main is back")
        self.assertNotIn(["restart", self.lane.tunnel_service], calls,
                         "no plain stop-then-start restart")
        self.assertIn("applied (overlap)", err)

    def test_overlap_unit_is_rendered_beside_the_lane_unit(self):
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service)
        self._reconcile(fake)
        unit = (self.home / ".config" / "systemd" / "user"
                / ta.overlap_service_name(self.lane.tunnel_service))
        self.assertTrue(unit.exists(), "overlap unit must sit beside the --user unit")
        text = unit.read_text()
        self.assertIn("--config %s run" % self.cfg, text)
        self.assertIn("/opt/cf/cloudflared tunnel", text,
                      "the overlap runs the SAME binary as the main unit")
        self.assertIn("TUNNEL_PIDFILE=%s" % ta.overlap_pidfile(self.cfg), text)
        self.assertNotIn("User=", text, "a --user unit never sets User=")
        self.assertIn(["daemon-reload"], fake.systemctl_calls())

    def test_every_user_call_carries_the_user_bus_env(self):
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service)
        self._reconcile(fake)
        user_calls = [kw for argv, kw in fake.calls if argv[:2] == ["systemctl", "--user"]]
        self.assertTrue(user_calls)
        for kw in user_calls:
            self.assertIn("XDG_RUNTIME_DIR", kw.get("env") or {})
            self.assertIn("DBUS_SESSION_BUS_ADDRESS", kw.get("env") or {})

    def test_unchanged_ingress_never_restarts(self):
        self.cfg.write_text(dg.render_drop_ingress_augmentation(
            self.cfg.read_text(), self.lane.host, self.lane.port))
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service)
        ok, _err = self._reconcile(fake)
        self.assertTrue(ok)
        self.assertEqual(fake.calls, [], "an unchanged ingress touches no unit")

    def test_unit_running_another_config_falls_back_loud(self):
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service,
                           exec_config=str(self.home / ".cloudflared" / "config.yml"))
        ok, err = self._reconcile(fake)
        self.assertTrue(ok, "the plain restart itself succeeded")
        self.assertIn("LOUD", err)
        self.assertIn("does not run", err)
        self.assertIn(["restart", self.lane.tunnel_service], fake.systemctl_calls())
        self.assertFalse((self.home / ".config" / "systemd" / "user").exists(),
                         "no overlap unit is written when the precondition fails")


class TestMarekApplyOverlaps(_LaneCase):
    KEY = ("subdev", "marek")

    def test_apply_restarts_via_the_overlap_and_marks_live(self):
        self.cfg.write_text(_config(MAREK_UUID, "marek.newlevel.media"))
        fake = FakeSystemd(self.cfg, self.lane.tunnel_service)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dg.cmd_drop_gateway(types.SimpleNamespace(
                apply=True, _nodename="subdev", _username="marek",
                _marker_path=self.marker, _run=fake,
                _is_worktree_fn=lambda: False))
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn(["restart", "--no-block", self.lane.tunnel_service],
                      fake.systemctl_calls())
        self.assertIn("(overlap)", out.getvalue())
        self.assertIsNotNone(dg.read_drop_marker(self.marker))


class TestSystemLane(_LaneCase):
    KEY = ("spinbike", "newlevel")

    def setUp(self):
        super().setUp()
        self.unit_dir = self.tmp / "etc-systemd-system"
        self._ud = mock.patch("cli_drop_tunnel_restart.SYSTEM_UNIT_DIR", self.unit_dir)
        self._ud.start()
        self.cfg.write_text(_config(SPINBIKE_UUID, "spinbike.sk"))
        dg.write_drop_marker(self.lane.host, self.lane.port, path=self.marker)

    def tearDown(self):
        self._ud.stop()
        super().tearDown()

    def _tee_inputs(self, fake):
        return {argv[3]: kw.get("input") for argv, kw in fake.calls
                if argv[:3] == ["sudo", "-n", "tee"]}

    def test_granted_sudo_overlaps_with_a_pidfile_dropin(self):
        fake = FakeSystemd(self.cfg, "spinbike-tunnel.service",
                           env_pidfile=False, user="newlevel")
        ok, err = self._reconcile(fake)
        self.assertTrue(ok, err)
        self.assertIn("applied (overlap)", err)
        tees = self._tee_inputs(fake)
        overlap_path = str(self.unit_dir / "spinbike-tunnel-overlap.service")
        dropin_path = str(self.unit_dir / "spinbike-tunnel.service.d"
                          / "airuleset-tunnel-pidfile.conf")
        self.assertIn(overlap_path, tees)
        self.assertIn("User=newlevel\n", tees[overlap_path],
                      "the overlap never runs with more privilege than the main unit")
        self.assertIn("Environment=TUNNEL_PIDFILE=%s" % ta.tunnel_pidfile(self.cfg),
                      tees[dropin_path])
        sudo_sc = [a[3:] for a, _ in fake.calls if a[:3] == ["sudo", "-n", "systemctl"]]
        self.assertIn(["daemon-reload"], sudo_sc)
        self.assertIn(["start", "--no-block", "spinbike-tunnel-overlap.service"], sudo_sc)
        # every privileged command was checked against the grant first
        probed = [a[3:] for a, _ in fake.calls if a[:3] == ["sudo", "-n", "-l"]]
        self.assertIn(["systemctl", "start", "--no-block",
                       "spinbike-tunnel-overlap.service"], probed)
        self.assertIn(["tee", overlap_path], probed)

    def test_uncovered_grant_falls_back_to_a_loud_plain_restart(self):
        fake = FakeSystemd(self.cfg, "spinbike-tunnel.service", grant=False)
        ok, err = self._reconcile(fake)
        self.assertTrue(ok, "the plain restart itself succeeded")
        self.assertIn("LOUD", err)
        self.assertIn("sudo grant does not cover", err)
        self.assertEqual(self._tee_inputs(fake), {}, "nothing written without the grant")
        sudo_sc = [a[3:] for a, _ in fake.calls if a[:3] == ["sudo", "-n", "systemctl"]]
        self.assertEqual(sudo_sc, [["restart", "spinbike-tunnel.service"]])


class TestLaneConfigMatchesTheRenderedUnit(unittest.TestCase):
    """The #1191 review claim: the dominika unit runs `webterm-dominika.yml` while
    drop-gateway edits `config.yml`. REAL in code (the #889 copy-paste) — the
    drop lane must edit the config its rendered unit runs, and that unit must
    carry the pidfile the overlap restart waits on."""

    def _check(self, key, mod_name, prefix):
        import importlib
        import cli_webterm_tunnel as tun
        mod = importlib.import_module(mod_name)
        lane = dg.DROP_LANES[key]
        unit_config = getattr(mod, "WEBTERM_%s_TUNNEL_CONFIG" % prefix)
        self.assertEqual(Path(lane.tunnel_config), Path(unit_config))
        unit_text = tun.render_cloudflared_tunnel_unit(
            "x", str(mod._spec().tunnel_config), "/bin/cloudflared")
        self.assertIn("--config %s run" % lane.tunnel_config, unit_text)
        self.assertIn("Environment=TUNNEL_PIDFILE=%s"
                      % ta.tunnel_pidfile(lane.tunnel_config), unit_text)

    def test_dominika_lane_edits_the_config_its_unit_runs(self):
        self._check(("subdev", "dominika"), "cli_webterm_dominika", "DOMINIKA")

    def test_marek_lane_edits_the_config_its_unit_runs(self):
        self._check(("subdev", "marek"), "cli_webterm_marek", "MAREK")


class TestOverlapUnitUser(unittest.TestCase):
    def test_user_adds_a_user_line_and_none_is_byte_identical(self):
        base = ta.render_overlap_unit("u.service", "/c/config.yml", "/bin/cf")
        self.assertEqual(ta.render_overlap_unit("u.service", "/c/config.yml",
                                                "/bin/cf", user=None), base)
        with_user = ta.render_overlap_unit("u.service", "/c/config.yml", "/bin/cf",
                                           user="newlevel")
        self.assertIn("[Service]\nUser=newlevel\n", with_user)
        self.assertEqual(with_user.replace("User=newlevel\n", ""), base)


if __name__ == "__main__":
    unittest.main()
