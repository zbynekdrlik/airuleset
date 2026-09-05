"""#882 re-introduction lock — marek must stay ABSENT from all live fleet surfaces.

The marek subdev stream was decommissioned (odoo-erp issue 6257, 2026-09-05).
These tests assert marek is NOT present in any live data structure, so a future
change that accidentally re-adds marek breaks loudly.
"""
import unittest

import airuleset
import cli_fleet
from cli_webterm_profiles import profile_for_host


class TestMarekAbsentFromLiveSurfaces882(unittest.TestCase):
    def test_not_in_remote_hosts(self):
        users = {e["user"] for e in cli_fleet.REMOTE_HOSTS
                 if not e.get("paused")}
        self.assertNotIn("marek", users,
                         "marek must not be a live REMOTE_HOSTS target (#882)")

    def test_not_in_authority_by_user(self):
        self.assertNotIn("marek", airuleset.AUTHORITY_BY_USER,
                         "marek must not be in AUTHORITY_BY_USER (#882)")

    def test_not_in_question_ping_owners_off(self):
        from notify import QUESTION_PING_OWNERS_OFF
        self.assertNotIn("marek", QUESTION_PING_OWNERS_OFF,
                         "marek must not be in QUESTION_PING_OWNERS_OFF (#882)")

    def test_not_in_webterm_dashboard_tabs(self):
        import cli_webterm as w
        self.assertNotIn("marek", w.WEBTERM_DASHBOARD_TABS,
                         "marek must not have a WEBTERM_DASHBOARD_TABS entry (#882)")

    def test_profile_for_host_subdev_marek_does_not_return_marek(self):
        result = profile_for_host("subdev", "marek")
        self.assertNotEqual(result, "marek",
                            "profile_for_host must not resolve marek account (#882)")


if __name__ == "__main__":
    unittest.main()
