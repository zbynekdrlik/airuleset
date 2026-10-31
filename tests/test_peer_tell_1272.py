"""#1272: airuleset tells a project session directly, over a linked account,
because a running session does not watch tickets (owner 31.10.2026)."""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_peer_channel as cpc  # noqa: E402

LINKS = {"hosts": {"dev1": {"links": [["a", "b"], ["b", "c"], ["c", "d"]], "topics": {}}}}
REGISTRY = [
    {"name": "a", "host": "dev1", "path": "/home/a/devel/a", "account": "a"},
    {"name": "b", "host": "dev1", "path": "/home/b/devel/b", "account": "b"},
    {"name": "c", "host": "dev1", "path": "/home/newlevel/devel/c", "account": "newlevel"},
    {"name": "d", "host": "dev1", "path": "/home/d/devel/d", "account": "d"},
]


class TestRelay(unittest.TestCase):

    def relay(self, to, usable=("a", "b", "d")):
        return cpc.relay_for(to, LINKS, REGISTRY, usable=lambda acct: acct in usable)

    def test_picks_a_linked_project_with_a_usable_account(self):
        self.assertEqual(self.relay("a"), ("b", "b", "/home/b/devel/b"))

    def test_skips_an_account_it_cannot_reach(self):
        self.assertEqual(self.relay("b", usable=("a", "d")),
                         ("a", "a", "/home/a/devel/a"))

    def test_no_relay_is_none(self):
        self.assertIsNone(self.relay("d", usable=("a",)))

    def test_unknown_project_is_none(self):
        self.assertIsNone(self.relay("nobody"))


class TestTell(unittest.TestCase):

    def run_tell(self, stdout="(True, 'delivered')\n", rc=0, relay=("b", "b", "/home/b/devel/b")):
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, rc, stdout, "boom")
        code = cpc.tell("a", "hello", relay=relay, ssh=lambda acct: ["ssh", acct], run=fake)
        return code, calls

    def test_the_text_goes_on_stdin_and_names_airuleset(self):
        code, calls = self.run_tell()
        self.assertEqual(code, 0)
        argv, kw = calls[0]
        self.assertEqual(argv[:2], ["ssh", "b"])
        self.assertNotIn("hello", " ".join(argv))
        self.assertIn("hello", kw["input"])
        self.assertIn("From airuleset", kw["input"])

    def test_no_live_session_fails_loud(self):
        self.assertEqual(self.run_tell(stdout="(False, 'a has no live session')\n")[0], 1)

    def test_ssh_failure_fails_loud(self):
        self.assertEqual(self.run_tell(rc=255, stdout="")[0], 1)

    def test_no_relay_fails_loud(self):
        self.assertEqual(cpc.tell("a", "x", relay=None, ssh=None, run=None), 1)


if __name__ == "__main__":
    unittest.main()
