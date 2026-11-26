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


class TestFleetAccountRemote(unittest.TestCase):
    """The relay account `gatekeeper` is a fleet user, not a project account:
    account_remote() alone returns None, so the controller never reached it."""

    def test_fleet_user_resolves_to_its_one_remote_row(self):
        got = cpc._account_remote("gatekeeper")
        self.assertIsNotNone(got)
        self.assertEqual(got["host"], "100.90.94.41")
        self.assertTrue(cpc._account_reachable("gatekeeper"))

    def test_user_on_several_boxes_stays_unresolved(self):
        self.assertIsNone(cpc._account_remote("newlevel"))


class TestTellScriptPath(unittest.TestCase):
    """A `locals` path is `~/...`; the remote Peer resolves its project from the cwd,
    and realpath("~/x") is a relative path, so it must be expanded first."""

    def test_tilde_path_resolves_to_the_relay_project(self):
        import peer_channel as pc
        projects = {"gatekeeper": {"account": "gatekeeper", "path": "~/devel/odoo/odoo-erp"}}
        ns = {}
        line = next(x for x in cpc._TELL_SCRIPT.splitlines() if "pc.Peer(" in x)
        cwd_expr = line.split("os.getuid(), ", 1)[1].split(", home", 1)[0]
        import os
        ns.update(os=os, req={"path": "~/devel/odoo/odoo-erp"})
        cwd = eval(cwd_expr, ns)  # noqa: S307 - the script's own cwd expression
        home = os.path.expanduser("~")
        self.assertEqual(pc.resolve_project(projects, "gatekeeper", cwd, home), "gatekeeper")


if __name__ == "__main__":
    unittest.main()
