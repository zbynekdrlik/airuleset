"""#1290: bw.newlevel.media is a PUBLIC controller route (17 users' native
Bitwarden apps cannot pass Cloudflare Access) with /admin answered 404 at the
edge. It must be in the rendered controller ingress, or every push after the
18.11. cutover deletes the live route."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import cli_service_routes as sr  # noqa: E402


class TestPublicServiceRoute(unittest.TestCase):
    def test_bw_rules_block_admin_before_the_origin(self):
        rules = sr.controller_public_ingress_rules()
        bw = [r for r in rules if r[0] == "bw.newlevel.media"]
        self.assertEqual(bw, [
            ("bw.newlevel.media", "^/admin", "http_status:404"),
            ("bw.newlevel.media", "http://127.0.0.1:8095"),
        ])

    def test_controller_render_includes_the_public_route(self):
        import cli_controller_tunnel as ct
        rules = ct._rules_for([])
        self.assertIn(("bw.newlevel.media", "^/admin", "http_status:404"), rules)
        i = rules.index(("bw.newlevel.media", "^/admin", "http_status:404"))
        self.assertEqual(rules[i + 1], ("bw.newlevel.media", "http://127.0.0.1:8095"))

    def test_a_public_route_never_reaches_the_access_reconcile(self):
        hosts = {r["hostname"] for r in sr.CONTROLLER_SERVICE_ROUTES}
        self.assertNotIn("bw.newlevel.media", hosts)

    def test_an_incomplete_public_route_gets_no_rule(self):
        self.assertEqual(sr.controller_public_ingress_rules(
            [{"hostname": "x.newlevel.media", "origin": "", "name": "x",
              "blocked_paths": [], "why_public": "y"}]), [])

    def test_a_public_route_needs_a_reason(self):
        self.assertEqual(sr.controller_public_ingress_rules(
            [{"hostname": "x.newlevel.media", "origin": "http://127.0.0.1:1",
              "name": "x", "blocked_paths": [], "why_public": ""}]), [])


if __name__ == "__main__":
    unittest.main()
