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
                    "http://ar.newlevel.media:4000", "http://AIRULESET:4000/",
                    "100.101.214.103:4000", "airuleset:4000",
                    "http://airuleset.:4000", "http://airuleset.tail1234.ts.net:4000",
                    "http://[::ffff:100.101.214.103]:4000",
                    "http://127.0.0.1:4000", "http://localhost:4000",
                    "http://[::1]:4000", "ftp://10.1.2.3:4000"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as d, \
                    self.assertRaises(mb.ModelBackendError):
                self._set(d, base_url=url)

    def test_other_gateway_still_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            entry = self._set(d, base_url="http://10.1.2.3:4000")
            self.assertEqual(entry["base_url"], "http://10.1.2.3:4000")


class TestShipperSkipsControllerEntry(unittest.TestCase):
    def test_registry_entry_naming_controller_is_not_shipped(self):
        """A hand-edited / pre-1358 registry entry pointing at the controller
        is skipped loudly by the deploy shipper — nothing is shipped."""
        import io
        import json
        from unittest import mock as m

        from cli_remote import provision_model_backend_markers as prov
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return m.Mock(returncode=0, stdout="", stderr="")

        entry = {"base_url": "http://100.101.214.103:4000", "cwd": "/proj",
                 "key_file": mb.TARGET_KEY_FILE, "main": "impl-main",
                 "sub": "impl-sub", "fast": "impl-fast"}
        with tempfile.TemporaryDirectory() as d:
            reg = Path(d) / "reg.json"
            reg.write_text(json.dumps({"miva1@subdev": entry}))
            key = Path(d) / "master.key"
            key.write_text("sk-test\n")
            # A pinned identity, so only the #1358 check can stop the shipping.
            hosts = [{"name": "miva1@subdev", "host": "100.118.174.27",
                      "user": "miva1", "identity": str(Path(d) / "id")}]
            with m.patch("sys.stdout", io.StringIO()), \
                    m.patch("sys.stderr", io.StringIO()) as err:
                fails = prov(hosts=hosts, run=run, registry_path=str(reg),
                             master_key_source=str(key))
        self.assertEqual(calls, [], "nothing may be shipped")
        self.assertEqual([n for n, _ in fails], ["miva1@subdev"])
        self.assertIn("1358", err.getvalue())


if __name__ == "__main__":
    unittest.main()
