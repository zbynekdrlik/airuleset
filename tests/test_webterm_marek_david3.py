"""Owner 11.11.2026: marek gets an OBSERVE webterm tab on david3@subdev."""
import unittest

import cli_webterm
import cli_webterm_only
import cli_webterm_profiles as p


class TestMarekDavid3Tab(unittest.TestCase):
    def _entry(self):
        hits = [e for e in p.marek_inventory() if e["id"] == "david3-subdev"]
        self.assertEqual(len(hits), 1)
        return hits[0]

    def test_tab_uses_marek_key_and_is_observe_only(self):
        e = self._entry()
        self.assertEqual(e["user"], "david3")
        self.assertEqual(e["identity"], p.WEBTERM_MAREK_IDENTITY)
        self.assertNotIn("u_tenant", e)
        self.assertIn("david3-subdev", cli_webterm.WEBTERM_DASHBOARD_TABS["marek"])

    def test_marek_lane_key_is_authorized_on_david3_only(self):
        blob = cli_webterm_only.WEBTERM_CONTROLLER_LANE_PUBKEYS["marek"].split()[1]
        self.assertTrue(any(blob in str(k)
                            for k in cli_webterm_only.desired_keys_for_user("david3")))
        for other in ("david1", "david2", "david4"):
            self.assertFalse(any(blob in str(k) for k in
                                 cli_webterm_only.desired_keys_for_user(other)), other)


if __name__ == "__main__":
    unittest.main()
