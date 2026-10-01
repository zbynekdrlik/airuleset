"""#1211 nlvpn + #1212 resolume: two legacy `newlevel` projects declared as
their own #1184 project accounts (migrate-on-touch). Declaration only: the live
migration waits for the owner's verify gate, and `github_app` is added then
(the 30-min minter must not fail on an account that does not exist yet, the
camera-box precedent)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_account_bootstrap as bootstrap  # noqa: E402


def _lan(spec):
    return {e["cidr"]: e["ports"] for e in spec["reach"] if isinstance(e, dict)}


class TestNlvpn(unittest.TestCase):

    def test_declaration(self):
        spec = bootstrap.account_spec("nlvpn")
        self.assertEqual(spec["host"], "dev1")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(_lan(spec), {"10.77.8.1/32": [22], "100.122.204.47/32": [22]})
        self.assertEqual(spec["repo"], "zbynekdrlik/nlvpn")
        self.assertEqual(spec["project_dir"], "devel/nlvpn")
        self.assertEqual(spec["tmux_session"], "nlvpn")
        self.assertEqual(set(spec["webterm_sessions"]), {"zbynek"})
        self.assertNotIn("github_app", spec)

    def test_the_render_carries_its_lan_reach(self):
        script = bootstrap.render_root_bootstrap("nlvpn")
        self.assertIn("10.77.8.1", script)
        self.assertIn("100.122.204.47", script)


class TestResolume(unittest.TestCase):

    def test_declaration(self):
        spec = bootstrap.account_spec("resolume")
        self.assertEqual(spec["host"], "dev2")
        self.assertIs(spec["sudo"], False)
        self.assertEqual(_lan(spec), {"10.77.9.201/32": [8092], "10.77.9.212/32": [8092],
                                      "10.76.8.201/32": [8092]})
        self.assertEqual(spec["repo"], "zbynekdrlik/resolume")
        self.assertEqual(spec["project_dir"], "devel/newlevelmedia/resolume")
        self.assertEqual(spec["tmux_session"], "resolume")
        self.assertIn("WIN_RESOLUME_PP_KEY", spec["secrets"])
        self.assertEqual(set(spec["webterm_sessions"]), {"zbynek"})
        self.assertNotIn("github_app", spec)


class TestTable(unittest.TestCase):

    def test_the_table_stays_clean(self):
        self.assertEqual(bootstrap.validate_all(), {})


if __name__ == "__main__":
    unittest.main()
