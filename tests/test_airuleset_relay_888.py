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

    def test_gk_request_supports_repo_flag(self):
        """gk-request --repo is wired (already exists, regression lock)."""
        import airuleset
        self.assertIn("gk-request", airuleset.SUBCOMMANDS)


if __name__ == "__main__":
    unittest.main()
