"""#903 — RED tests for fleet sweep host classification + identity fix.

RED→GREEN: committed BEFORE the implementation so the tests fail.
Tests cover:
  1. classify_fleet_host() — paused, webterm-only, webterm-observer, active
  2. run_fleet() uses identity field in SSH command
  3. run_fleet() populates a 'skipped' key in the result
  4. act_on_due() renders skipped hosts as SKIPPED, not FAILED
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# classify_fleet_host — classification table
# ---------------------------------------------------------------------------

class TestClassifyFleetHost(unittest.TestCase):
    """classify_fleet_host must return (status, reason) for each host class."""

    def test_paused_host_skipped(self):
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "simap1@subdev",
            "host": "100.118.174.27",
            "user": "simap1",
            "paused": "owner 2026-09-02: paused",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "skipped")
        self.assertIn("paused", reason.lower())

    def test_webterm_only_user_skipped(self):
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "david1@subdev",
            "host": "100.118.174.27",
            "user": "david1",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "skipped")
        self.assertIn("webterm-only", reason.lower())

    def test_webterm_observer_skipped(self):
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "marek@subdev",
            "host": "100.118.174.27",
            "user": "marek",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "skipped")
        self.assertIn("observer", reason.lower())

    def test_active_host_classified_active(self):
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "dev2",
            "host": "100.82.64.27",
            "user": "newlevel",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "active")
        self.assertEqual(reason, "")

    def test_gatekeeper_active(self):
        """gatekeeper has an identity but is a real target — must be active."""
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "gatekeeper",
            "host": "100.90.94.41",
            "user": "gatekeeper",
            "identity": "~/.secrets/gatekeeper_access_ed25519",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "active")

    def test_dominika_webterm_only_takes_precedence(self):
        """dominika is in BOTH WEBTERM_ONLY_USERS and WEBTERM_OBSERVER_USERS;
        webterm-only should take precedence (it is checked first)."""
        from cli_mdreview_audit import classify_fleet_host
        host = {
            "name": "dominika@subdev",
            "host": "100.118.174.27",
            "user": "dominika",
        }
        status, reason = classify_fleet_host(host)
        self.assertEqual(status, "skipped")
        # Either webterm-only or observer is acceptable
        self.assertTrue(
            "webterm" in reason.lower(),
            f"expected webterm classification, got {reason}")


# ---------------------------------------------------------------------------
# run_fleet uses identity in SSH command
# ---------------------------------------------------------------------------

class TestFleetIdentity(unittest.TestCase):
    """run_fleet must pass -i <identity> -o IdentitiesOnly=yes when the host
    entry has an identity field."""

    def test_ssh_command_includes_identity(self):
        """When fleet_runner is None and the host has an identity, the
        subprocess.run SSH command must include -i and IdentitiesOnly."""
        from cli_mdreview_audit import run_fleet

        hosts_with_identity = [{
            "name": "gatekeeper",
            "host": "100.90.94.41",
            "user": "gatekeeper",
            "repo_path": "~/devel/airuleset",
            "identity": "~/.secrets/gatekeeper_access_ed25519",
        }]

        captured_cmds = []

        def capture_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return mock.Mock(
                stdout='{"schema":1,"host":"gatekeeper"}',
                returncode=0)

        with mock.patch("cli_mdreview_audit._fleet_hosts_for_audit",
                        return_value=hosts_with_identity):
            with mock.patch("subprocess.run", side_effect=capture_run):
                try:
                    run_fleet()
                except Exception:
                    pass  # airuleset:script-ok we need the captured cmd

        self.assertGreater(len(captured_cmds), 0,
                           "subprocess.run must be called for the host")
        cmd = captured_cmds[0]
        cmd_str = " ".join(str(c) for c in cmd)
        self.assertIn("-i", cmd_str,
                      f"SSH command must include -i for identity: {cmd}")
        self.assertIn("IdentitiesOnly", cmd_str,
                      f"SSH command must include IdentitiesOnly: {cmd}")


# ---------------------------------------------------------------------------
# run_fleet populates skipped list
# ---------------------------------------------------------------------------

class TestFleetSkipped(unittest.TestCase):
    """run_fleet result must contain a 'skipped' key with classified hosts."""

    def test_skipped_key_in_result(self):
        from cli_mdreview_audit import run_fleet
        hosts = [
            {"name": "david1@subdev", "host": "100.118.174.27",
             "user": "david1", "repo_path": "~/a",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        with mock.patch("cli_mdreview_audit._fleet_hosts_for_audit",
                        return_value=hosts):
            result = run_fleet(fleet_runner=lambda h: ('{"schema":1}', 0))
        self.assertIn("skipped", result,
                      "result must contain 'skipped' key")
        # david1 is webterm-only → should be in skipped
        skipped_names = [s["host"] for s in result["skipped"]]
        self.assertIn("david1@subdev", skipped_names,
                      f"david1 must be skipped, got {result['skipped']}")

    def test_active_host_not_in_skipped(self):
        from cli_mdreview_audit import run_fleet
        hosts = [
            {"name": "dev2", "host": "100.82.64.27",
             "user": "newlevel", "repo_path": "~/a"},
        ]
        def fake_runner(host):
            return json.dumps({"schema": 1, "host": host["name"]}), 0
        with mock.patch("cli_mdreview_audit._fleet_hosts_for_audit",
                        return_value=hosts):
            result = run_fleet(fleet_runner=fake_runner)
        skipped_names = [s["host"] for s in result.get("skipped", [])]
        self.assertNotIn("dev2", skipped_names)


# ---------------------------------------------------------------------------
# act_on_due renders skipped as SKIPPED, not FAILED
# ---------------------------------------------------------------------------

class TestActOnDueSkipped(unittest.TestCase):
    """The reopen comment must render skipped hosts as SKIPPED, not FAILED."""

    def test_skipped_rendered_separately(self):
        from watchdog.mdreview_cadence import act_on_due
        calls = []
        def fake_gh(argv):
            calls.append(argv)
            return "", 0
        audit_data = {
            "schema": 1, "date": "2026-09-06",
            "boxes": [],
            "failed": [{"host": "real-fail", "error": "rc=1"}],
            "skipped": [
                {"host": "david1@subdev", "reason": "webterm-only (#869)"},
            ],
        }
        act_on_due(999, "30d", audit_data, gh_runner=fake_gh)
        # Find the comment body
        comment_calls = [c for c in calls if "comment" in str(c)]
        self.assertGreater(len(comment_calls), 0,
                           "act_on_due must post a comment")
        body = " ".join(str(x) for x in comment_calls[0])
        self.assertIn("SKIPPED", body,
                      f"comment must mention SKIPPED: {body}")
        self.assertIn("david1@subdev", body)


if __name__ == "__main__":
    unittest.main()
