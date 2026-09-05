"""#882 hybrid lock — marek DEV STREAM cancelled, webterm OBSERVER lane survives.

Owner scope correction 2026-09-05: marek's dev stream is decommissioned
(odoo-erp issue 6257) but his webterm observe dashboard (marek.newlevel.media)
must survive. These tests lock BOTH sides of the split:
  - STREAM surfaces: marek ABSENT (no stream provisioning, no soniox, no notify
    stream reroute, no QUESTION_PING_OWNERS_OFF)
  - LANE surfaces: marek PRESENT (REMOTE_HOSTS, AUTHORITY_BY_USER fork-no-merge,
    WEBTERM_OBSERVER_USERS, WEBTERM_DASHBOARD_TABS, profile_for_host, Access realm)
"""
import unittest

import airuleset
import cli_fleet
import cli_webterm as w
from cli_webterm_profiles import profile_for_host


class TestMarekStreamAbsent882(unittest.TestCase):
    """marek must stay OUT of stream-provisioning surfaces."""

    def test_not_in_question_ping_owners_off(self):
        from notify import QUESTION_PING_OWNERS_OFF
        self.assertNotIn("marek", QUESTION_PING_OWNERS_OFF,
                         "marek must not be in QUESTION_PING_OWNERS_OFF (#882)")

    def test_not_in_skills_extra(self):
        self.assertNotIn("marek", getattr(airuleset, "SKILLS_EXTRA_BY_USER", {}),
                         "marek must not have dev-stream skill extras (#882)")


class TestMarekLanePresent882(unittest.TestCase):
    """marek's webterm observer lane must stay LIVE."""

    def test_in_remote_hosts(self):
        users = {e["user"] for e in cli_fleet.REMOTE_HOSTS
                 if not e.get("paused")}
        self.assertIn("marek", users,
                      "marek must be a live REMOTE_HOSTS target (lane host, #882)")

    def test_in_authority_by_user_fork_no_merge(self):
        self.assertIn("marek", airuleset.AUTHORITY_BY_USER,
                      "marek must be in AUTHORITY_BY_USER (#882)")
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "fork-no-merge",
                         "marek must be fork-no-merge (least-privilege observer, #882)")

    def test_in_webterm_observer_users(self):
        self.assertIn("marek", cli_fleet.WEBTERM_OBSERVER_USERS,
                      "marek must be a webterm observer (#882)")

    def test_in_webterm_dashboard_tabs(self):
        self.assertIn("marek", w.WEBTERM_DASHBOARD_TABS,
                      "marek must have a WEBTERM_DASHBOARD_TABS entry (#882)")

    def test_montalu1_in_dashboard_tabs(self):
        tabs = w.WEBTERM_DASHBOARD_TABS.get("marek", [])
        self.assertIn("montalu1-subdev", tabs,
                      "montalu1-subdev must be in marek's tab list (#882)")

    def test_marek_subdev_not_in_dashboard_tabs(self):
        tabs = w.WEBTERM_DASHBOARD_TABS.get("marek", [])
        self.assertNotIn("marek-subdev", tabs,
                         "marek-subdev LOCAL tab must be removed (#882)")

    def test_profile_for_host_subdev_marek_returns_marek(self):
        result = profile_for_host("subdev", "marek")
        self.assertEqual(result, "marek",
                         "profile_for_host must resolve marek account (#882)")

    def test_in_webterm_only_users(self):
        self.assertIn("marek", cli_fleet.WEBTERM_ONLY_USERS,
                      "marek must be webterm-only (#882)")


if __name__ == "__main__":
    unittest.main()
