"""#1190 push 0.1.506 incident: Pass B runs `python -m unittest discover`, where
`PYTEST_CURRENT_TEST` is never set, so every "refuse the real default under
test" guard keyed on it (`watchdog.disk_guard_escalation.running_under_pytest`,
the #1195 reclaim guard, the #1190 timer guard) was OFF during the push gate.
Pass B must carry the same marker pytest sets for every test."""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_remote  # noqa: E402


class TestPassBCarriesTheTestMarker(unittest.TestCase):

    def test_pass_b_env_sets_pytest_current_test(self):
        captured = {}

        def _capture(cmd, **kw):
            if (isinstance(cmd, list) and len(cmd) > 3 and cmd[1] == "-m"
                    and cmd[2] == "unittest" and "env" not in captured):
                captured["env"] = dict(kw.get("env") or {})
            return subprocess.CompletedProcess(cmd, 0, "", "")

        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        # the harness itself may run under pytest: drop the inherited marker so
        # the assertion sees only what Pass B sets on its own
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cli_remote, "_run_pass_a", return_value=0), \
                mock.patch("subprocess.run", side_effect=_capture), \
                mock.patch.object(cli_remote, "_tracked_tree_fingerprint",
                                  return_value={}), \
                mock.patch.object(cli_remote, "_classify_push_gate_outcome",
                                  return_value=(True, "clean", "  Tests passed.")), \
                mock.patch.object(cli_remote, "_check_push_tmpdir_litter",
                                  return_value=(True, 0)), \
                mock.patch.object(cli_remote, "_deploy_to_all_remotes"):
            import argparse
            try:
                cli_remote.cmd_push(argparse.Namespace())
            except SystemExit as e:
                print("cmd_push exited (expected in harness): %s" % e)
        self.assertIn("env", captured, "no Pass B unittest call captured")
        self.assertTrue(captured["env"].get("PYTEST_CURRENT_TEST"),
                        "Pass B must set PYTEST_CURRENT_TEST so the under-test "
                        "guards fire exactly as under pytest and CI")


if __name__ == "__main__":
    unittest.main()
