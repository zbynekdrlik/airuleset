"""#1115 reopen (owner report 2026-09-23): the drop Access apps must not
re-ask the email OTP every day, and every programmer who reads a lane must be
on its include list.

The webterm Access apps are the proven, working source: a programmer's
webterm email is the address they actually log in with, and webterm uses a
720h session. The drop lanes of the same people must match both."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as g
import cli_webterm_access as w


class TestDropAccessMatchesWebterm1115(unittest.TestCase):
    def test_every_drop_app_uses_the_webterm_session_length(self):
        webterm = {s["session_duration"] for s in w.WEBTERM_ACCESS_APPS.values()}
        self.assertEqual(webterm, {"720h"})
        for host, spec in g.DROP_ACCESS_APPS.items():
            self.assertEqual(spec["session_duration"], "720h",
                             f"{host} re-asks the OTP every "
                             f"{spec['session_duration']}")

    def test_programmer_drop_lanes_include_their_webterm_login(self):
        lanes = {
            "david": ["drop-david.newlevel.media",
                      "drop-subdev-david2.newlevel.media",
                      "drop-subdev-david3.newlevel.media",
                      "drop-subdev-david4.newlevel.media"],
            "dominika": ["drop-subdev-dominika.newlevel.media"],
        }
        for profile, hosts in lanes.items():
            need = set(w.WEBTERM_ACCESS_APPS[profile]["allowed_emails"])
            for host in hosts:
                have = set(g.DROP_ACCESS_APPS[host]["allowed_emails"])
                self.assertTrue(need <= have,
                                f"{host} is missing {sorted(need - have)}")

    def test_owner_on_every_drop_lane(self):
        for host, spec in g.DROP_ACCESS_APPS.items():
            self.assertIn("drlik.zbynek@gmail.com", spec["allowed_emails"], host)


if __name__ == "__main__":
    unittest.main()
