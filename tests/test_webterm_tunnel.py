"""#635: airuleset-managed cloudflared tunnel provisioning (owner + david).

The webterm public front (a cloudflared tunnel) was unmanaged runtime state on
BOTH lanes — the owner's did not exist (manual NAT patch instead) and david's was
a HAND-MADE systemd unit airuleset did not reconcile. These tests prove the shared
render helpers (cli_webterm_tunnel) + the two prerequisite-gated provisioners write
the right config + systemd --user unit, and are a SAFE no-op until the per-tunnel
creds JSON exists (the one-time cert.pem bootstrap), mirroring
cli_webterm_david.prerequisites_ready.
"""
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock as m

import cli_webterm_david as dv
import cli_webterm_tunnel as tun


class TestRenderTunnelConfig(unittest.TestCase):
    def test_config_has_tunnel_creds_and_ingress(self):
        cfg = tun.render_cloudflared_tunnel_config(
            "abc-123", "/home/u/.cloudflared/abc-123.json",
            "zbynek.newlevel.media", "http://127.0.0.1:8080")
        self.assertIn("tunnel: abc-123", cfg)
        self.assertIn("credentials-file: /home/u/.cloudflared/abc-123.json", cfg)
        self.assertIn("hostname: zbynek.newlevel.media", cfg)
        self.assertIn("service: http://127.0.0.1:8080", cfg)
        # catch-all 404 so an unmatched host never leaks to the origin
        self.assertIn("service: http_status:404", cfg)


class TestRenderTunnelUnit(unittest.TestCase):
    def test_unit_execstart_config_run_and_restart(self):
        unit = tun.render_cloudflared_tunnel_unit(
            "owner webterm tunnel", "/home/u/.cloudflared/webterm-owner.yml",
            "/usr/local/bin/cloudflared",
            after="network-online.target webterm-gateway.service")
        self.assertIn("Description=owner webterm tunnel", unit)
        self.assertIn(
            "ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate "
            "--config /home/u/.cloudflared/webterm-owner.yml run", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("After=network-online.target webterm-gateway.service", unit)
        self.assertIn("WantedBy=default.target", unit)
        # airuleset-managed marker (NOT hand-managed — the whole point of #635)
        self.assertIn("airuleset-managed", unit)


class _TunnelIsolate:
    """Redirect the owner tunnel path constants into a temp dir + mock systemctl,
    so a run on dev1 (where the REAL creds JSON now exists) never writes real
    ~/.cloudflared / ~/.config/systemd/user files."""

    def _iso(self, stack, tmp):
        p = Path(tmp)
        (p / ".cloudflared").mkdir(parents=True, exist_ok=True)
        (p / ".config" / "systemd" / "user").mkdir(parents=True, exist_ok=True)
        pt = {
            "WEBTERM_CLOUDFLARED_DIR": p / ".cloudflared",
            "WEBTERM_OWNER_TUNNEL_CREDS": p / ".cloudflared" / (tun.WEBTERM_OWNER_TUNNEL_UUID + ".json"),
            "WEBTERM_OWNER_TUNNEL_CONFIG": p / ".cloudflared" / "webterm-owner.yml",
            "WEBTERM_OWNER_TUNNEL_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-owner-tunnel.service",
        }
        for name, val in pt.items():
            stack.enter_context(m.patch.object(tun, name, val))
        stack.enter_context(m.patch.object(tun.shutil, "which", return_value="/usr/local/bin/cloudflared"))
        import cli_filedrop_watchdog as fw
        self.sysctl = []
        stack.enter_context(m.patch.object(
            fw, "_run_systemctl", lambda args: (self.sysctl.append(list(args)), (0, "", ""))[1]))
        stack.enter_context(m.patch.object(fw, "_whoami", lambda: "zbynek"))
        return pt


class TestOwnerTunnelProvision(_TunnelIsolate, unittest.TestCase):
    def test_no_op_when_creds_absent(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._iso(st, tmp)
            ok = tun.setup_webterm_owner_tunnel(run=lambda *a, **k: None)
            self.assertFalse(ok)                         # prereq-gated safe no-op
            self.assertFalse(pt["WEBTERM_OWNER_TUNNEL_CONFIG"].exists())
            self.assertFalse(pt["WEBTERM_OWNER_TUNNEL_SERVICE_DEST"].exists())
            self.assertEqual(self.sysctl, [])            # touched no systemd

    def test_provisions_when_creds_present(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._iso(st, tmp)
            pt["WEBTERM_OWNER_TUNNEL_CREDS"].write_text('{"TunnelID":"x"}\n')
            ok = tun.setup_webterm_owner_tunnel(run=lambda *a, **k: None)
            self.assertTrue(ok)
            cfg = pt["WEBTERM_OWNER_TUNNEL_CONFIG"].read_text()
            self.assertIn("tunnel: " + tun.WEBTERM_OWNER_TUNNEL_UUID, cfg)
            self.assertIn("hostname: zbynek.newlevel.media", cfg)
            # #663: front the gateway's mode-0700 UNIX socket, not a TCP loopback port
            self.assertIn("service: unix:/run/user/", cfg)
            self.assertIn("webterm-gateway.sock", cfg)
            self.assertNotIn("http://127.0.0.1", cfg)
            unit = pt["WEBTERM_OWNER_TUNNEL_SERVICE_DEST"].read_text()
            self.assertIn("--config", unit)
            self.assertIn(str(pt["WEBTERM_OWNER_TUNNEL_CONFIG"]), unit)
            # enabled + restarted (managed, reboot-durable)
            self.assertIn(["enable", "--now", "webterm-owner-tunnel.service"], self.sysctl)
            self.assertIn(["restart", "webterm-owner-tunnel.service"], self.sysctl)


class _DavidTunnelIsolate:
    def _iso(self, stack, tmp):
        p = Path(tmp)
        (p / ".cloudflared").mkdir(parents=True, exist_ok=True)
        (p / ".config" / "systemd" / "user").mkdir(parents=True, exist_ok=True)
        stack.enter_context(m.patch.object(tun, "WEBTERM_CLOUDFLARED_DIR", p / ".cloudflared"))
        pt = {
            "WEBTERM_DAVID_TUNNEL_CREDS": p / ".cloudflared" / (dv.WEBTERM_DAVID_TUNNEL_UUID + ".json"),
            "WEBTERM_DAVID_TUNNEL_CONFIG": p / ".cloudflared" / "config.yml",
            "WEBTERM_DAVID_TUNNEL_SERVICE_DEST": p / ".config" / "systemd" / "user" / "webterm-david-tunnel.service",
        }
        for name, val in pt.items():
            stack.enter_context(m.patch.object(dv, name, val))
        # dv.shutil.which resolves the bin in setup_webterm_david_tunnel; tun.shutil.which
        # is the shared helper's bin-present check — patch BOTH so the test is
        # deterministic regardless of whether the box happens to have cloudflared.
        stack.enter_context(m.patch.object(dv.shutil, "which", return_value="/home/u/.local/bin/cloudflared"))
        stack.enter_context(m.patch.object(tun.shutil, "which", return_value="/home/u/.local/bin/cloudflared"))
        import cli_filedrop_watchdog as fw
        self.sysctl = []
        stack.enter_context(m.patch.object(
            fw, "_run_systemctl", lambda args: (self.sysctl.append(list(args)), (0, "", ""))[1]))
        stack.enter_context(m.patch.object(fw, "_whoami", lambda: "david1"))
        return pt


class TestDavidTunnelProvision(_DavidTunnelIsolate, unittest.TestCase):
    def test_no_op_when_creds_absent(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._iso(st, tmp)
            ok = dv.setup_webterm_david_tunnel(run=lambda *a, **k: None)
            self.assertFalse(ok)
            self.assertFalse(pt["WEBTERM_DAVID_TUNNEL_SERVICE_DEST"].exists())
            self.assertEqual(self.sysctl, [])

    def test_provisions_managed_unit_for_existing_tunnel(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as st:
            pt = self._iso(st, tmp)
            pt["WEBTERM_DAVID_TUNNEL_CREDS"].write_text('{"TunnelID":"x"}\n')
            ok = dv.setup_webterm_david_tunnel(run=lambda *a, **k: None)
            self.assertTrue(ok)
            cfg = pt["WEBTERM_DAVID_TUNNEL_CONFIG"].read_text()
            self.assertIn("tunnel: " + dv.WEBTERM_DAVID_TUNNEL_UUID, cfg)
            self.assertIn("hostname: david.newlevel.media", cfg)
            # #663: UNIX socket origin (account boundary), not TCP loopback :8081
            self.assertIn("service: unix:/run/user/", cfg)
            self.assertIn("webterm-david-gateway.sock", cfg)
            self.assertNotIn("http://127.0.0.1", cfg)
            unit = pt["WEBTERM_DAVID_TUNNEL_SERVICE_DEST"].read_text()
            self.assertIn("airuleset-managed", unit)      # no longer hand-managed
            self.assertIn(["enable", "--now", "webterm-david-tunnel.service"], self.sysctl)


if __name__ == "__main__":
    unittest.main()
