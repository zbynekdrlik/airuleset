"""A leaked per-sweep memo must not reach the owner-stop refresh tests
(ref issue 1257; push 0.1.568 Pass A flake)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_owner_stop_fixes_1257 import AnNpmClaudeIsAClaude  # noqa: E402


class SweepMemoLeftActive(unittest.TestCase):
    """A run_once that raised mid-sweep leaves the per-sweep memo ACTIVE
    (`subprocess_budget` documents it); the next direct `refresh` here was then
    served that sweep's pane inventory instead of its own `run`, and the npm
    claude read as absent (push 0.1.568 Pass A: 'SN' latched as an exit)."""

    def test_the_npm_case_holds_after_a_leaked_sweep_memo(self):
        from watchdog import subprocess_budget as sb
        sb.begin_sweep_memo()
        self.addCleanup(sb.end_sweep_memo)
        # what the previous test of the class left: its OWN npm pid, now dead
        import subprocess
        gone = subprocess.Popen(["true"])
        gone.wait()
        cwd = AnNpmClaudeIsAClaude.CWD
        sb.memoized(("panes_raw",), lambda: "%%S\tbash\t%s\t333\n%%N\tnode\t%s\t%d\n"
                    % (cwd, cwd, gone.pid))
        case = AnNpmClaudeIsAClaude(
            "test_an_npm_claude_keeps_its_cwd_live_for_the_exit_scan")
        result = unittest.TestResult()
        case.run(result)
        self.assertEqual(result.failures + result.errors, [])


if __name__ == "__main__":
    unittest.main()
