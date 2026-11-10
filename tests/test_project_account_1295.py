"""#1295 (owner 10.11.2026): the SNV finances app moves to its own account."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop
import cli_webterm as w
import cli_webterm_profiles as profiles

ROOT = Path(__file__).resolve().parent.parent


class TestFinancesAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec("finances")

    def test_declaration_is_valid_and_has_no_sudo(self):
        self.assertEqual(bootstrap.validate_account("finances", self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["repo"], "zbynekdrlik/finances")

    def test_only_the_owner_gets_a_terminal_tab(self):
        # a tab is a shell as the account, which holds the church bank token
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek"})
        self.assertIn("finances", w.WEBTERM_DASHBOARD_TABS["zbynek"])
        self.assertNotIn("finances", w.WEBTERM_DASHBOARD_TABS["marek"])
        ids = [e["id"] for e in profiles.marek_inventory()]
        self.assertNotIn("finances", ids)

    def test_drop_port_and_peer_link(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "finances")], 8906)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "finances" in p)
        self.assertEqual(pairs, [["airuleset", "finances"]])

    def test_the_runner_and_deploy_tools_are_shared_pins(self):
        self.assertIn("pipx:uv==0.9.18", self.spec["tools"])
        self.assertIn("playwright:1.63.0/chromium", self.spec["tools"])


if __name__ == "__main__":
    unittest.main()
