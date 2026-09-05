"""RED test for #888 install-time probe: on an App-token box, cmd_install
must probe whether the box can reach airuleset issues and print a LOUD
finding when it cannot (HTTP 403 = expected on an App-token box that
uses the relay path)."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset  # noqa: E402


class TestRelayProbe888(unittest.TestCase):
    """#888: install-time probe on App-token boxes."""

    def test_probe_function_exists(self):
        """airuleset must expose a _probe_relay_repo_reach function."""
        self.assertTrue(
            hasattr(airuleset, "_probe_relay_repo_reach"),
            "_probe_relay_repo_reach must be defined in airuleset.py")

    def test_probe_returns_finding_on_403(self):
        """On an App-token box where gh api returns 403, the probe
        returns a non-empty finding string."""
        fake_result = mock.Mock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "HTTP 403"
        with mock.patch.object(airuleset, "_is_gh_app_token_box",
                               return_value=True):
            finding = airuleset._probe_relay_repo_reach(
                run_fn=lambda *a, **kw: fake_result)
        self.assertIn("AIRULESET_RELAY_REPO", finding)
        self.assertIn("gk-request", finding)

    def test_probe_returns_empty_on_non_app_box(self):
        """On a non-App-token box, the probe returns empty (no finding)."""
        with mock.patch.object(airuleset, "_is_gh_app_token_box",
                               return_value=False):
            finding = airuleset._probe_relay_repo_reach()
        self.assertEqual(finding, "")

    def test_probe_returns_empty_on_200(self):
        """On an App-token box where gh api returns 200, no finding."""
        fake_result = mock.Mock()
        fake_result.returncode = 0
        fake_result.stdout = "[{}]"
        fake_result.stderr = ""
        with mock.patch.object(airuleset, "_is_gh_app_token_box",
                               return_value=True):
            finding = airuleset._probe_relay_repo_reach(
                run_fn=lambda *a, **kw: fake_result)
        self.assertEqual(finding, "")


if __name__ == "__main__":
    unittest.main()
