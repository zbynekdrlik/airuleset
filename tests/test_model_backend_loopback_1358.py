"""#1358 — `model-backend set` must not flip a box onto the controller gateway.

Since #1358 the gateway binds loopback only, so a remote box whose marker points
at the controller (the old default `http://<tailscale>:4000`) would be switched
onto a backend that refuses every connection. `set` refuses that target loudly;
reopening remote use is an owner decision plus a real auth path.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_model_backend as mb  # noqa: E402


class TestSetRefusesControllerGateway(unittest.TestCase):
    def _set(self, d, **kw):
        return mb.set_target("miva1@subdev", cwd="/home/miva1/devel/odoo",
                             path=str(Path(d) / "reg.json"), **kw)

    def test_default_base_url_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(mb.ModelBackendError) as cm:
                self._set(d)
            self.assertIn("1358", str(cm.exception))
            self.assertEqual(mb.load_registry(path=str(Path(d) / "reg.json")), {})

    def test_explicit_controller_urls_refused(self):
        for url in ("http://100.101.214.103:4000", "http://airuleset:4000",
                    "http://ar.newlevel.media:4000", "http://AIRULESET:4000/"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as d, \
                    self.assertRaises(mb.ModelBackendError):
                self._set(d, base_url=url)

    def test_other_gateway_still_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            entry = self._set(d, base_url="http://10.1.2.3:4000")
            self.assertEqual(entry["base_url"], "http://10.1.2.3:4000")


if __name__ == "__main__":
    unittest.main()
