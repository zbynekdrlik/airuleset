"""#1237 — `accounts verify` checks the base channels (secret / upload / share).

fohmixer passed the #1201 go-live gate while it had no drop lane: the gate
never asked whether the account can receive a secret or a file. The `drop`
check needs a declared lane, the box's go-live marker matching it, the lane
live in the controller's go-live cache, and the account's filedrop service
(the `share` server) active. The fake world is the #1201 harness: the REAL
rendered probe runs under a fake home.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_verify as verify      # noqa: E402
import cli_drop_gateway                   # noqa: E402
from test_accounts_verify_1201 import _Base   # noqa: E402

HOST = "drop-dev1-fohmixer.newlevel.media"


class TestDropCheck(_Base):

    def test_drop_is_a_go_live_check(self):
        self.assertIn("drop", verify.CHECKS)

    def test_a_live_lane_passes(self):
        self.assertEqual(self.verify(), 0, self.out + self.err)
        self.assertIn(" OK — ", self.line("drop"))
        self.assertIn(HOST, self.line("drop"))

    def test_no_declared_lane_fails(self):
        lanes = {k: v for k, v in cli_drop_gateway.DROP_LANES.items()
                 if k != ("dev1", "fohmixer")}
        with mock.patch.object(cli_drop_gateway, "DROP_LANES", lanes):
            self.assertEqual(self.verify(), 1)
        self.assertIn(" FAIL — no drop lane", self.line("drop"))

    def test_a_missing_marker_fails(self):
        os.remove(self.w.home / ".cloudflared" / "airuleset-drop.conf")
        self.assertEqual(self.verify(), 1)
        self.assertIn("go-live marker", self.line("drop"))

    def test_a_stale_marker_fails(self):
        (self.w.home / ".cloudflared" / "airuleset-drop.conf").write_text(
            "host=%s\nport=8878\n" % HOST)
        self.assertEqual(self.verify(), 1)
        self.assertIn("go-live marker", self.line("drop"))

    def test_a_lane_not_live_on_the_controller_fails(self):
        self.golive = {}
        self.assertEqual(self.verify(), 1)
        self.assertIn("not live", self.line("drop"))

    def test_an_inactive_filedrop_fails(self):
        self.w.knobs["FILEDROP"] = "inactive"
        self.assertEqual(self.verify(), 1)
        self.assertIn("filedrop", self.line("drop"))


if __name__ == "__main__":
    unittest.main()
