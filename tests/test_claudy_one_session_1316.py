"""#1316 (owner 13.11.2026): the claudy account ran two parallel Claude sessions,
`marek` and `zbynek`, because #960 gave each human his own tmux session. One
account = one project = one Claude (#1184): every human's claudy tab joins the
ONE session named after the account, the fohmixer shape (#1183)."""
from unittest import TestCase

import cli_account_bootstrap as bootstrap
import cli_webterm_profiles as profiles


def _claudy(inv):
    return [e for e in inv if e["id"] == "claudy"][0]


class TestClaudyOneSession(TestCase):
    def test_account_declares_one_session(self):
        self.assertEqual(bootstrap.SERVICE_ACCOUNTS["claudy"].get("tmux_session"), "claudy")

    def test_every_human_joins_it(self):
        sessions = bootstrap.SERVICE_ACCOUNTS["claudy"]["webterm_sessions"]
        self.assertEqual(set(sessions), {"zbynek", "marek"})
        for human, s in sessions.items():
            self.assertEqual(s["preferred"], "claudy", human)
            self.assertEqual(s["start_dir_chain"], ["devel/claudy"], human)

    def test_both_tabs_target_the_same_session(self):
        self.assertEqual(_claudy(profiles.zbynek_inventory())["preferred"], "claudy")
        self.assertEqual(_claudy(profiles.marek_inventory())["preferred"], "claudy")
