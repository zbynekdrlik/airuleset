"""RED tests for #888: App-token streams must relay airuleset ticket filing
through gk-request, and the doctrine + constant must name this path."""
import unittest
from pathlib import Path


class TestAirulesRelay888(unittest.TestCase):
    """#888: the AIRULESET_RELAY_REPO constant exists and the doctrine
    names the gk-request relay path for App-token boxes."""

    def test_airuleset_relay_repo_constant_exists(self):
        """STREAM_APP_BOT_LOGIN has a sibling AIRULESET_RELAY_REPO naming
        the repo where App-token streams must use gk-request."""
        import airuleset
        self.assertTrue(hasattr(airuleset, "AIRULESET_RELAY_REPO"),
                        "AIRULESET_RELAY_REPO constant must be declared")
        self.assertEqual(airuleset.AIRULESET_RELAY_REPO,
                         "zbynekdrlik/airuleset")

    def test_doctrine_names_relay_path(self):
        """machine-identities.md names the gk-request relay for
        App-token boxes filing airuleset tickets."""
        repo_root = Path(__file__).resolve().parent.parent
        doctrine = (repo_root / "modules" / "core" /
                    "machine-identities.md").read_text()
        self.assertIn("gk-request", doctrine,
                      "doctrine must name gk-request as the relay path")
        self.assertIn("App-token", doctrine,
                      "doctrine must reference App-token boxes")

    def test_gk_request_repo_flag_exists(self):
        """gk-request argparser has --repo (the relay path depends on it).

        Content-lock: removing --repo from the gk-request parser in
        airuleset.py must make this test RED."""
        import inspect
        import airuleset
        self.assertIn("gk-request", airuleset.SUBCOMMANDS)
        # Lock the --repo flag on the REAL parser source: find the
        # gk-request parser section and assert --repo is declared there
        src = inspect.getsource(airuleset.main)
        gkr_idx = src.index("gk-request")
        # The --repo add_argument must follow the gk-request parser
        gkr_section = src[gkr_idx:gkr_idx + 600]
        self.assertIn('"--repo"', gkr_section,
                      "gk-request parser must have --repo argument")


if __name__ == "__main__":
    unittest.main()
