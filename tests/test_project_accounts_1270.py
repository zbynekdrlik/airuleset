"""#1270: dante-asio, dante-stack and iemmixer are declared project accounts on
dev1 (owner 30.10.: two new projects plus iemmixer out of `newlevel`), and the
peer links between them name what each pair talks about."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_drop_lanes as drop  # noqa: E402
import cli_peer_channel as cpc  # noqa: E402

NEW = ("dante-asio", "dante-stack", "iemmixer")


class TestDeclaration(unittest.TestCase):

    def test_shape(self):
        for acct in NEW:
            spec = bootstrap.account_spec(acct)
            self.assertEqual(bootstrap.validate_account(acct, spec), [], acct)
            self.assertEqual(spec["host"], "dev1")
            self.assertIs(spec["sudo"], False)
            self.assertEqual(spec["repo"], "zbynekdrlik/" + acct)
            self.assertIs(spec["github_app"], True)
            self.assertEqual(spec["repo_secrets"] if acct != "iemmixer" else [], [])
            self.assertEqual(set(spec["webterm_sessions"]), {"zbynek", "marek"})
            self.assertIn(("dev1", acct), drop._GENERATED_DROP_PORTS)

    def test_new_sessions_start_from_a_handover_document(self):
        for acct in ("dante-asio", "dante-stack"):
            self.assertEqual(bootstrap.account_spec(acct)["handover"], "docs/HANDOVER.md")

    def test_iemmixer_keeps_its_ci_secret_and_pins(self):
        spec = bootstrap.account_spec("iemmixer")
        self.assertIn("DENYLIST", spec["repo_secrets"])
        self.assertIn("rust-target:wasm32-unknown-unknown", spec["tools"])
        self.assertIn("playwright:1.58.2/chromium", spec["tools"])


class TestLinks(unittest.TestCase):

    def setUp(self):
        self.links = {frozenset(k.split("|")): v
                      for k, v in cpc.load_links()["hosts"]["dev1"]["topics"].items()}

    def test_the_committed_links_are_valid(self):
        self.assertEqual(cpc.validate_links(cpc.load_links(), cpc.load_registry()), [])

    def test_dante_stack_is_the_hub_for_dante_questions(self):
        for other in ("dante-asio", "iemmixer", "camera-box", "fohmixer"):
            self.assertIn(frozenset(("dante-stack", other)), self.links, other)

    def test_consumers_are_not_linked_to_each_other_for_dante(self):
        self.assertNotIn(frozenset(("iemmixer", "camera-box")), self.links)
        self.assertNotIn(frozenset(("iemmixer", "dante-asio")), self.links)

    def test_a_bug_goes_to_the_dante_session(self):
        self.assertIn("bug", self.links[frozenset(("dante-stack", "iemmixer"))])


if __name__ == "__main__":
    unittest.main()
