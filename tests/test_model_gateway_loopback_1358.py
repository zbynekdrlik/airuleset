"""#1358 — the model gateway binds loopback only and serves no admin UI.

The #1062 pilot is over and no box points its model backend at the controller
gateway (registry `{}`), so the LiteLLM proxy must not listen on the tailnet:
every tailnet node could otherwise reach :4000 and the `/ui`. The render guard
is the single choke point — any bind that is not a loopback literal is refused,
so no install path can put the gateway back on tailscale or a wildcard.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_model_gateway as mg  # noqa: E402


class TestGatewayBindsLoopback(unittest.TestCase):
    def test_bind_host_is_loopback(self):
        self.assertEqual(mg.GATEWAY_BIND_HOST, "127.0.0.1")

    def test_render_with_bind_host(self):
        out = mg.render_gateway_unit(mg.GATEWAY_BIND_HOST)
        self.assertIn("--host 127.0.0.1 --port 4000", out)
        self.assertNotIn("{{HOST_IP}}", out)

    def test_tailscale_bind_refused(self):
        with self.assertRaises(mg.ModelGatewayError):
            mg.render_gateway_unit("100.101.214.103")

    def test_wildcard_binds_refused(self):
        for host in ("0.0.0.0", "::", "0"):
            with self.subTest(host=host), self.assertRaises(mg.ModelGatewayError):
                mg.render_gateway_unit(host)

    def test_lan_and_hostname_refused(self):
        for host in ("10.77.9.21", "192.168.1.5", "localhost", "airuleset"):
            with self.subTest(host=host), self.assertRaises(mg.ModelGatewayError):
                mg.render_gateway_unit(host)


class TestAdminUiDisabled(unittest.TestCase):
    def test_rendered_unit_disables_admin_ui(self):
        out = mg.render_gateway_unit(mg.GATEWAY_BIND_HOST)
        self.assertIn("Environment=DISABLE_ADMIN_UI=True", out)


class TestInstallUsesBindHost(unittest.TestCase):
    def test_install_writes_loopback_unit_and_probes_loopback(self):
        """Drive the real install step on a temp home with the side-effecting
        seams (venv, systemctl, health probe, secrets) faked: the written unit
        binds 127.0.0.1 and the health probe targets 127.0.0.1."""
        import json
        import tempfile
        from unittest import mock as m

        probed = []

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            amp = d / "map.json"
            amp.write_text(json.dumps({
                "aliases": {"impl-main": "openrouter/x", "impl-sub": "openrouter/x",
                            "impl-fast": "openrouter/x"},
                "providers": {"openrouter": {"key_file": str(d / "or.key")}},
            }))
            unit = d / "systemd" / "model-gateway.service"
            with m.patch.object(mg, "CONFIG_DIR", d / "cfg"), \
                    m.patch.object(mg, "YAML_PATH", d / "cfg" / "gw.yaml"), \
                    m.patch.object(mg, "ENV_PATH", d / "cfg" / "gw.env"), \
                    m.patch.object(mg, "SPEND_LOGGER_DEST", d / "cfg" / "sl.py"), \
                    m.patch.object(mg, "MASTER_KEY_FILE", d / "master"), \
                    m.patch.object(mg, "SERVICE_DEST", unit), \
                    m.patch.object(mg, "_service_state", return_value="inactive"), \
                    m.patch.object(mg, "_read_secret_file", return_value="v"), \
                    m.patch.object(mg, "_ensure_venv", return_value=(True, False)), \
                    m.patch.object(mg, "_enable_service", return_value=True), \
                    m.patch.object(mg, "_health_check",
                                   side_effect=lambda h, deep=True: probed.append(h) or True), \
                    m.patch("sys.stdout"):
                ok = mg.setup_model_gateway_service(alias_map_path=str(amp))
            text = unit.read_text(encoding="utf-8")
        self.assertTrue(ok)
        self.assertIn("--host 127.0.0.1 --port 4000", text)
        self.assertIn("Environment=DISABLE_ADMIN_UI=True", text)
        self.assertEqual(probed, ["127.0.0.1"])


if __name__ == "__main__":
    unittest.main()
