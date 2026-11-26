"""#1399 (owner 26.11.2026: "preco s nim o tom nekomunikujes cez peer"): `tell --to
gk-infra` answered "no linked project of gk-infra is reachable" although
peer-links.json links gatekeeper <-> gk-infra. relay_for read only registry rows,
so a project declared under a host's `locals` or `streams` was never found."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_peer_channel as cpc  # noqa: E402

LINKS = {"hosts": {"gatekeeper": {
    "links": [["gatekeeper", "gk-infra"]], "topics": {},
    "locals": {"gatekeeper": {"account": "gatekeeper", "path": "~/devel/odoo/odoo-erp"},
               "gk-infra": {"account": "gatekeeper", "path": "~/devel/odoo/odoo-erp-infra"}}}}}


class TestRelayLocals(unittest.TestCase):

    def test_local_project_is_reached_over_its_linked_local(self):
        got = cpc.relay_for("gk-infra", LINKS, [], usable=lambda acct: acct == "gatekeeper")
        self.assertEqual(got, ("gatekeeper", "gatekeeper", "~/devel/odoo/odoo-erp"))

    def test_live_links_reach_gk_infra(self):
        got = cpc.relay_for("gk-infra", usable=lambda acct: True)
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "gatekeeper")


if __name__ == "__main__":
    unittest.main()
