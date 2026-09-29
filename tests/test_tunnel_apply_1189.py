"""#1189 — a managed cloudflared tunnel is restarted ONLY on a real change, and
then WITHOUT a dark window (blue/green overlap connector).

Incident: every install/push restarted the controller's ONE shared tunnel
(every webterm hostname + every `drop-<box>` lane); the old connector drained
its 30 s grace period before the new one registered, so Cloudflare answered
1033 for ~30 s (the owner's `secret show` URL at 18:26:22Z).

The fake systemd below models exactly what the real one does for these calls:
`start --no-block <overlap>` / `restart --no-block <main>` spawn a connector
that writes its `TUNNEL_PIDFILE` when it registers (cloudflared `writePidFile`
after `connectedSignal`). No real systemd, no real cloudflared, no sleeps.
"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_filedrop_watchdog as fw
import cli_tunnel_apply as ta
import cli_webterm_tunnel as tun

SVC = "webterm-controller-tunnel.service"
OVERLAP = "webterm-controller-tunnel-overlap.service"


class FakeSystemd:
    """Records every systemctl argv; simulates units + pidfile registration."""

    def __init__(self, cfg, active=True, overlap_registers=True,
                 main_registers=True):
        self.cfg = Path(cfg)
        self.calls = []
        self.active = {SVC: active}
        self.main_pid = 111 if active else 0
        self.overlap_registers = overlap_registers
        self.main_registers = main_registers
        # snapshot: was the overlap REGISTERED at the moment main was restarted?
        self.overlap_registered_at_restart = None

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        verb = args[0]
        if verb == "show" and args[2] == "ActiveState":
            return 0, "active\n" if self.active.get(args[-1]) else "inactive\n", ""
        if verb == "show" and args[2] == "MainPID":
            return 0, "%d\n" % (self.main_pid if args[-1] == SVC else 0), ""
        if verb == "start" and args[-1] == OVERLAP:
            self.active[OVERLAP] = True
            if self.overlap_registers:
                ta.overlap_pidfile(self.cfg).write_text("555")
        if verb == "restart" and args[-1] == SVC:
            self.overlap_registered_at_restart = (
                ta.overlap_pidfile(self.cfg).exists() and self.active.get(OVERLAP))
            self.main_pid = 222
            if self.main_registers:
                ta.tunnel_pidfile(self.cfg).write_text("222")
        if verb == "enable" and not self.active.get(SVC):
            self.active[SVC] = True
            self.main_pid = 333
        if verb == "stop" and args[-1] == OVERLAP:
            self.active[OVERLAP] = False
        return 0, "", ""

    def verbs_for(self, unit):
        return [c for c in self.calls if c[-1] == unit and c[0] != "show"]


class _Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = root / ".cloudflared" / "controller-webterm.yml"
        self.unit = root / "systemd" / SVC
        self.creds = root / ".cloudflared" / "controller-webterm.json"
        self.creds.parent.mkdir(parents=True)
        self.creds.write_text('{"TunnelID":"x"}')
        self.cfg_text = tun.render_cloudflared_multi_ingress_config(
            "u", str(self.creds), [("drop-dev2.newlevel.media", "http://100.1.1.1:8870")])
        self.unit_text = tun.render_cloudflared_tunnel_unit(
            "ctl", str(self.cfg), "/bin/cloudflared")

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, sysd, cfg_text=None):
        clock = _Clock()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = ta.apply_managed_tunnel(
                self.creds, "/bin/cloudflared", self.cfg,
                cfg_text or self.cfg_text, self.unit, SVC, self.unit_text,
                run=lambda *a, **k: None, whoami=lambda: "airuleset",
                systemctl=sysd, lane="(controller)", sleep=clock.sleep,
                clock=clock.now)
        return ok, err.getvalue()


class TestChangeGate(_Base):
    def test_byte_identical_render_does_not_restart(self):
        self.apply(FakeSystemd(self.cfg))                  # first apply: stamps
        sysd = FakeSystemd(self.cfg)
        ok, err = self.apply(sysd)
        self.assertTrue(ok)
        self.assertEqual(sysd.verbs_for(OVERLAP), [])
        self.assertNotIn("restart", [c[0] for c in sysd.calls], sysd.calls)
        self.assertIn("unchanged", err)

    def test_changed_config_restarts(self):
        self.apply(FakeSystemd(self.cfg))
        sysd = FakeSystemd(self.cfg)
        ok, _ = self.apply(sysd, cfg_text=self.cfg_text + "# new rule\n")
        self.assertTrue(ok)
        self.assertIn(["restart", "--no-block", SVC], sysd.calls)

    def test_changed_credentials_alone_restart(self):
        self.apply(FakeSystemd(self.cfg))
        self.creds.write_text('{"TunnelID":"rotated"}')
        sysd = FakeSystemd(self.cfg)
        self.apply(sysd)
        self.assertIn(["restart", "--no-block", SVC], sysd.calls)

    def test_inactive_unit_is_started_not_restarted(self):
        sysd = FakeSystemd(self.cfg, active=False)
        ok, _ = self.apply(sysd)
        self.assertTrue(ok)
        self.assertIn(["enable", "--now", SVC], sysd.calls)
        self.assertEqual([c for c in sysd.calls if c[0] == "restart"], [])
        self.assertEqual(sysd.verbs_for(OVERLAP), [])
        # the started render is recorded, so the next identical push is a no-op
        sysd2 = FakeSystemd(self.cfg)
        self.apply(sysd2)
        self.assertEqual([c for c in sysd2.calls if c[0] == "restart"], [])


class TestNoDarkWindow(_Base):
    def test_overlap_registers_before_main_restart_and_stops_after(self):
        sysd = FakeSystemd(self.cfg)
        ok, err = self.apply(sysd)
        self.assertTrue(ok)
        self.assertTrue(sysd.overlap_registered_at_restart,
                        "main was restarted while no overlap connector served")
        order = [tuple(c) for c in sysd.calls if c[0] != "show"]
        i_start = order.index(("start", "--no-block", OVERLAP))
        i_restart = order.index(("restart", "--no-block", SVC))
        i_stop = order.index(("stop", "--no-block", OVERLAP))
        self.assertLess(i_start, i_restart)
        self.assertLess(i_restart, i_stop)
        self.assertNotIn(("restart", SVC), order)          # never a blocking stop+start
        self.assertIn("overlap", err)

    def test_overlap_never_registers_falls_back_loudly(self):
        sysd = FakeSystemd(self.cfg, overlap_registers=False)
        ok, err = self.apply(sysd)
        self.assertTrue(ok)                                # config still applied
        self.assertIn(["restart", "--no-block", SVC], sysd.calls)
        self.assertIn("LOUD", err)
        self.assertIn("DARK", err)

    def test_main_never_reregisters_leaves_overlap_serving(self):
        sysd = FakeSystemd(self.cfg, main_registers=False)
        ok, err = self.apply(sysd)
        self.assertFalse(ok)
        self.assertNotIn(["stop", "--no-block", OVERLAP], sysd.calls)
        self.assertTrue(sysd.active[OVERLAP])
        self.assertFalse(ta.applied_stamp_path(self.cfg).exists())   # next push retries
        self.assertIn("LEFT SERVING", err)

    def test_stale_main_pidfile_is_not_mistaken_for_registration(self):
        # the OLD process wrote its pidfile long ago; after the restart the new
        # MainPID differs, so only a fresh write by the NEW process counts.
        ta.tunnel_pidfile(self.cfg).parent.mkdir(parents=True, exist_ok=True)
        ta.tunnel_pidfile(self.cfg).write_text("111")
        sysd = FakeSystemd(self.cfg, main_registers=False)
        ok, _ = self.apply(sysd)
        self.assertFalse(ok)


class TestRenderedUnits(_Base):
    def test_main_unit_carries_pidfile_env(self):
        self.assertIn("Environment=TUNNEL_PIDFILE=%s" % ta.tunnel_pidfile(self.cfg),
                      self.unit_text)

    def test_overlap_unit_same_tunnel_short_grace_never_enabled(self):
        self.apply(FakeSystemd(self.cfg))
        ov = (self.unit.parent / OVERLAP).read_text()
        self.assertIn("--config %s run" % self.cfg, ov)
        self.assertIn("Environment=TUNNEL_PIDFILE=%s" % ta.overlap_pidfile(self.cfg), ov)
        self.assertIn("Environment=TUNNEL_GRACE_PERIOD=5s", ov)
        self.assertIn("Restart=no", ov)
        self.assertNotIn("[Install]", ov)


class TestProvisionerIsChangeGated(unittest.TestCase):
    """End-to-end through the shared `_provision_managed_tunnel` (the path
    `_setup_controller_webterm` calls for the controller tunnel)."""

    def test_second_identical_provision_does_not_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = root / "controller-webterm.yml"
            creds = root / "controller-webterm.json"
            creds.write_text("{}")
            unit = root / SVC
            cfg_text = tun.render_cloudflared_multi_ingress_config(
                "u", str(creds), [("a.example.com", "http://1.2.3.4:1")])
            unit_text = tun.render_cloudflared_tunnel_unit("c", str(cfg), "/bin/cf")
            runs = []
            for _ in range(2):
                sysd = FakeSystemd(cfg)
                runs.append(sysd)
                with mock.patch.object(fw, "_run_systemctl", sysd), \
                     mock.patch.object(fw, "_whoami", lambda: "airuleset"), \
                     mock.patch.object(tun.shutil, "which", return_value="/bin/cf"), \
                     contextlib.redirect_stderr(io.StringIO()):
                    self.assertTrue(tun._provision_managed_tunnel(
                        creds, "/bin/cf", cfg, cfg_text, unit, SVC, unit_text,
                        run=lambda *a, **k: None, lane="(controller)"))
            self.assertIn(["restart", "--no-block", SVC], runs[0].calls)
            self.assertEqual([c for c in runs[1].calls if c[0] == "restart"], [])


if __name__ == "__main__":
    unittest.main()
