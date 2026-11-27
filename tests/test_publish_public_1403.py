"""#1403 — `publish-app --public`: an owner-confirmed PUBLIC app (no Access app).

Owner 27.11.2026 (issue 1403, answer "ano"): listen.newlevel.media, the
presenter guest live-translation page, is published with NO Cloudflare Access
app — guests are strangers with no account. Only a host in the owner-confirmed
`PUBLIC_APPS` table may go public; everything else keeps Access first.
"""
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import cli_publish_app as pa                 # noqa: E402
import cli_venue_bypass_apps as table        # noqa: E402
from test_venue_bypass_1370 import SOURCES, TUN, FakeCF, _clients  # noqa: E402

HOST = "listen.newlevel.media"


class TestPublicTable(unittest.TestCase):

    def test_listen_is_the_only_public_app_with_a_confirmation(self):
        self.assertEqual(sorted(table.PUBLIC_APPS), [HOST])
        self.assertIn("1403", table.PUBLIC_APPS[HOST])

    def test_no_host_is_both_public_and_venue_bypass(self):
        self.assertFalse(set(table.PUBLIC_APPS) & set(table.VENUE_BYPASS_APPS))


class TestPublishPublic(unittest.TestCase):

    def _publish(self, host, fake, **kw):
        a, t, d = _clients(fake)
        out = io.StringIO()
        rc = pa.publish(host, TUN, "http://localhost:8099", access_client=a,
                        tunnel_client=t, dns_client=d, out=out, **kw)
        return rc, out.getvalue()

    def test_public_host_gets_ingress_and_dns_but_no_access_app(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish(HOST, fake, dry_run=False, public=True)
        self.assertEqual(rc, 0, log)
        kinds = [p.split("/")[3] if "/accounts/" in p else "zones"
                 for _m, p, _b in fake.writes]
        self.assertEqual(kinds, ["cfd_tunnel", "zones"])
        _m, _p, cfg = fake.writes[0]
        self.assertEqual(cfg["config"]["ingress"][-2],
                         {"hostname": HOST, "service": "http://localhost:8099"})
        self.assertIn("PUBLIC", log)

    def test_public_refused_for_a_host_not_in_the_table(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish("newapp.newlevel.media", fake, dry_run=False,
                                public=True)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.writes, [])
        self.assertEqual(fake.order, [])
        self.assertIn("PUBLIC_APPS", log)

    def test_public_with_an_existing_access_app_stops_before_ingress(self):
        app = {"id": "a1", "domain": HOST, "policies": [
            {"decision": "allow", "include": [{"email": {"email": "o@x"}}]}]}
        fake = FakeCF(SOURCES + [app])
        rc, log = self._publish(HOST, fake, dry_run=False, public=True)
        self.assertEqual(rc, 1, log)
        self.assertEqual(fake.writes, [])
        self.assertNotIn("ingress", fake.order)

    def test_public_and_venue_bypass_together_are_refused(self):
        fake = FakeCF(SOURCES)
        rc, _log = self._publish(HOST, fake, dry_run=False, public=True,
                                 venue_bypass=True)
        self.assertEqual(rc, 2)
        self.assertEqual(fake.order, [])

    def test_table_host_without_public_still_gets_access_first(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish(HOST, fake, dry_run=False)
        self.assertEqual(rc, 0, log)
        self.assertIn("/access/apps", fake.writes[0][1])

    def test_cli_exposes_the_flag(self):
        import argparse
        sub = argparse.ArgumentParser().add_subparsers()
        p = pa.register_parser(sub)
        args = p.parse_args([HOST, "--tunnel", TUN, "--origin", "http://x", "--public"])
        self.assertTrue(args.public)


if __name__ == "__main__":
    unittest.main()
