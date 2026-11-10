"""#1296 (owner 10.11.2026): Marek's TUKE studies (dev1 skola + dev2
tuke/meetings) move to their own account `tuke` on dev1."""
import json
import unittest
from pathlib import Path

import cli_account_bootstrap as bootstrap
import cli_drop_lanes as drop
import cli_webterm as w
import cli_webterm_profiles as profiles

ROOT = Path(__file__).resolve().parent.parent


class TestTukeAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec("tuke")

    def test_declaration_is_valid_on_dev1_without_sudo(self):
        self.assertEqual(bootstrap.validate_account("tuke", self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["repo"], "zbynekdrlik/tuke")

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        self.assertEqual(set(self.spec["webterm_sessions"]), {"zbynek", "marek"})
        for human in ("zbynek", "marek"):
            sess = self.spec["webterm_sessions"][human]
            self.assertEqual(sess["start_dir_chain"], ["devel/tuke"])
            self.assertIn("tuke", w.WEBTERM_DASHBOARD_TABS[human])
        for human in ("david", "dominika", "timo"):
            self.assertNotIn("tuke", w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            self.assertIn("tuke", [e["id"] for e in inv])

    def test_pulse_for_the_recorder_sinks(self):
        self.assertIn("pulseaudio", self.spec["system_packages"])

    def test_drop_port_and_peer_link(self):
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", "tuke")], 8907)
        hosts = json.loads((ROOT / "peer-links.json").read_text(encoding="utf-8"))["hosts"]
        pairs = sorted(sorted(p) for p in hosts["dev1"]["links"] if "tuke" in p)
        self.assertEqual(pairs, [["airuleset", "tuke"]])


if __name__ == "__main__":
    unittest.main()
