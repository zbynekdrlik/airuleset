"""#1374 — a peer_send to a project with no live session tells the session what to do.

Owner 19.11.2026 (via dstack): dstack's peer_send to iemmixer returned only
"iemmixer has no live session", and dstack parked its ticket in W. Only the owner
starts sessions, so that wait is an owner action (U, `needs-owner-action`, a ❓
asking to start the session), never an ops-wait/W park (#1197). The instruction
rides the failure text itself, so every caller (MCP peer_send, `tell` CLI) sees it
at the moment of failure.
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import peer_channel as pc  # noqa: E402
from test_peer_channel_1266 import _Env  # noqa: E402


class NoLiveSessionIsAnOwnerAction(_Env):
    def _offline_send(self):
        ok, text = self.peer("alpha").send("beta", "are you there?")
        self.assertFalse(ok)
        return text

    def test_the_failure_keeps_the_known_prefix(self):
        self.assertTrue(self._offline_send().startswith("beta has no live session"))

    def test_the_failure_names_the_owner_action(self):
        text = self._offline_send()
        self.assertIn("only the owner starts sessions", text)
        self.assertIn("needs-owner-action", text)
        self.assertIn("❓", text)
        self.assertIn("start beta's session", text)

    def test_the_failure_forbids_the_w_park(self):
        self.assertIn("never an ops-wait/W park", self._offline_send())

    def test_a_delivery_failure_with_a_session_is_not_the_owner_case(self):
        # sockets answered but failed: a session exists, nothing for the owner to start
        p = self.peer("alpha")
        p._calls = lambda to, req: [{"ok": False, "error": "unreachable (OSError)"}]
        ok, text = p.send("beta", "x")
        self.assertFalse(ok)
        self.assertNotIn("needs-owner-action", text)
        self.assertIn("not delivered", text)

    def test_the_text_is_one_constant(self):
        self.assertIn("{p}", pc.NO_SESSION)


if __name__ == "__main__":
    unittest.main()
