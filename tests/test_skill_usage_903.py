"""#903 follow-up — RED test for cli_skill_usage.py fleet SSH identity.

cli_skill_usage.run_fleet() builds its own SSH command (independent of
cli_mdreview_audit.run_fleet(), fixed by #903's own lane) and had the SAME
bug: no `-i <identity>` for a REMOTE_HOSTS entry that carries an `identity`
field (e.g. gatekeeper), so every identity-keyed host fails rc=255 before
ever reaching `skill-usage --json` on the remote box.

RED→GREEN: committed BEFORE the fix so this test fails first.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class TestSkillUsageFleetIdentity(unittest.TestCase):
    """run_fleet must pass -i <identity> -o IdentitiesOnly=yes when the host
    entry has an identity field -- same shape as cli_mdreview_audit's #903
    fix, reused here rather than a parallel invention."""

    def test_ssh_command_includes_identity(self):
        from cli_skill_usage import run_fleet

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
                stdout='{"schema":1,"host":"gatekeeper","skills":{},'
                       '"slash":{}}',
                returncode=0)

        with mock.patch("cli_remote._deployable_hosts",
                        return_value=hosts_with_identity):
            with mock.patch("subprocess.run", side_effect=capture_run):
                run_fleet()

        self.assertGreater(len(captured_cmds), 0,
                            "subprocess.run must be called for the host")
        cmd = captured_cmds[0]
        cmd_str = " ".join(str(c) for c in cmd)
        self.assertIn("-i", cmd_str,
                      f"SSH command must include -i for identity: {cmd}")
        self.assertIn("IdentitiesOnly", cmd_str,
                      f"SSH command must include IdentitiesOnly: {cmd}")

    def test_no_identity_host_omits_dash_i(self):
        """A host with no `identity` field (e.g. dev2, default newlevel
        key) must NOT get a spurious -i flag."""
        from cli_skill_usage import run_fleet

        hosts_no_identity = [{
            "name": "dev2",
            "host": "100.82.64.27",
            "user": "newlevel",
            "repo_path": "~/devel/airuleset",
        }]

        captured_cmds = []

        def capture_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            return mock.Mock(
                stdout='{"schema":1,"host":"dev2","skills":{},"slash":{}}',
                returncode=0)

        with mock.patch("cli_remote._deployable_hosts",
                        return_value=hosts_no_identity):
            with mock.patch("subprocess.run", side_effect=capture_run):
                run_fleet()

        self.assertGreater(len(captured_cmds), 0)
        cmd = captured_cmds[0]
        self.assertNotIn("-i", cmd,
                          f"no-identity host must not get -i: {cmd}")


if __name__ == "__main__":
    unittest.main()
