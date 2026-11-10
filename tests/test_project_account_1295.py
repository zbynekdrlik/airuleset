"""#1295 (owner 10.11.2026): the SNV finances app moves to its own account,
renamed nlch-finances the same day (owner: "nlch finances")."""
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
        self.spec = bootstrap.account_spec("nlch-finances")

    def test_declaration_is_valid_and_has_no_sudo(self):
        self.assertEqual(bootstrap.validate_account("nlch-finances", self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["repo"], "zbynekdrlik/nlch-finances")

    def test_owner_and_marek_get_a_terminal_tab(self):
        # owner 10.11.: Marek sees it too; nobody else gets a shell as the account
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            self.assertIn("nlch-finances", w.WEBTERM_DASHBOARD_TABS[human])
        for human in ("david", "dominika", "timo"):
            self.assertNotIn("nlch-finances", w.WEBTERM_DASHBOARD_TABS[human])
        ids = [e["id"] for e in profiles.marek_inventory()]
        self.assertIn("nlch-finances", ids)

    def test_drop_port_and_peer_link(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "nlch-finances")], 8906)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "nlch-finances" in p)
        self.assertEqual(pairs, [["airuleset", "nlch-finances"]])

    def test_the_runner_and_deploy_tools_are_shared_pins(self):
        self.assertIn("pipx:uv==0.9.18", self.spec["tools"])
        self.assertIn("playwright:1.63.0/chromium", self.spec["tools"])


if __name__ == "__main__":
    unittest.main()
