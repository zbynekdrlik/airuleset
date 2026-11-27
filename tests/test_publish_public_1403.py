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

    def test_dry_run_public_writes_nothing(self):
        fake = FakeCF(SOURCES)
        rc, log = self._publish(HOST, fake, dry_run=True, public=True)
        self.assertEqual(rc, 0, log)
        self.assertEqual(fake.writes, [])
        self.assertEqual(log.count("[would]"), 2)

    def test_path_scoped_access_app_under_the_host_stops(self):
        app = {"id": "p1", "domain": HOST + "/admin", "policies": []}
        fake = FakeCF(SOURCES + [app])
        rc, log = self._publish(HOST, fake, dry_run=False, public=True)
        self.assertEqual(rc, 1, log)
        self.assertEqual(fake.writes, [])

    def test_longer_host_with_the_same_prefix_is_not_a_hit(self):
        app = {"id": "o1", "domain": "listenx.newlevel.media", "policies": []}
        fake = FakeCF(SOURCES + [app])
        rc, log = self._publish(HOST, fake, dry_run=True, public=True)
        self.assertEqual(rc, 0, log)

    def test_paged_access_listing_fails_closed(self):
        fake = FakeCF(SOURCES)

        def paged(method, path, body):
            st, b = fake(method, path, body)
            if "/access/apps" in path and method == "GET":
                b["result_info"] = {"total_count": len(b["result"]) + 5}
            return st, b
        a, t, d = _clients(fake)
        a._transport = paged
        out = io.StringIO()
        rc = pa.publish(HOST, TUN, "http://localhost:8099", access_client=a,
                        tunnel_client=t, dns_client=d, out=out, dry_run=False,
                        public=True)
        self.assertEqual(rc, 1, out.getvalue())
        self.assertEqual(fake.writes, [])
        self.assertIn("paged", out.getvalue())

    def test_cli_forwards_public_to_publish(self):
        import argparse
        sub = argparse.ArgumentParser().add_subparsers()
        args = pa.register_parser(sub).parse_args(
            [HOST, "--tunnel", TUN, "--origin", "http://localhost:8099",
             "--public", "--apply"])
        fake = FakeCF(SOURCES)
        args._clients = _clients(fake)
        args._is_worktree_fn = lambda: False
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            rc = pa.cmd_publish_app(args)
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0, out.getvalue())
        self.assertTrue(fake.writes)
        self.assertFalse(any("/access/apps" in p for _m, p, _b in fake.writes))
        self.assertIn("PUBLIC", out.getvalue())

if __name__ == "__main__":
    unittest.main()
