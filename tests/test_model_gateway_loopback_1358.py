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
    def test_setup_does_not_resolve_tailscale(self):
        # The install step takes its bind from GATEWAY_BIND_HOST, never from the
        # live tailscale address (the pre-#1358 source).
        src = Path(mg.__file__).read_text(encoding="utf-8")
        body = src.split("def setup_model_gateway_service", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("_tailscale_ip(", body)
        self.assertIn("GATEWAY_BIND_HOST", body)


if __name__ == "__main__":
    unittest.main()
