"""#1210: the hand-off RFR diff (`_handoff_changed_paths`, feeding the #1073
guide and #1077 agent-eval pre-flights) is taken against the INTEGRATION
branch. odoo-erp's origin/HEAD is `main`, but stream branches are cut from and
target `develop`; diffing `main...HEAD` listed every unreleased develop change
and refused #8790/#8778 for files never on the branch (1.10.2026).
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402


def fake_git(head, refs, diffs):
    """A `run(argv)` answering symbolic-ref (origin/HEAD -> ``head``) and
    `git diff --name-only <base>...HEAD` (rc 0 only for a ref in ``refs``)."""
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[:2] == ["git", "symbolic-ref"]:
            return subprocess.CompletedProcess(argv, 0 if head else 1,
                                               (head or "") + "\n", "")
        if argv[:3] == ["git", "diff", "--name-only"]:
            base = argv[3].split("...")[0]
            if base in refs:
                return subprocess.CompletedProcess(argv, 0, diffs.get(base, ""), "")
            return subprocess.CompletedProcess(argv, 128, "", "bad revision")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")
    return run, calls


BRANCH_ONLY = "tests/test_a.py\nchangelog.d/8790.md\n"
WITH_DEVELOP_DRIFT = BRANCH_ONLY + "docs/montalu/build-vyroba-guide.py\n"


class TestDiffBase(unittest.TestCase):

    def test_three_branch_repo_diffs_against_develop(self):
        run, calls = fake_git("origin/main", {"origin/main", "origin/develop"},
                              {"origin/main": WITH_DEVELOP_DRIFT,
                               "origin/develop": BRANCH_ONLY})
        paths = airuleset._handoff_changed_paths(run=run)
        self.assertEqual(paths, ["tests/test_a.py", "changelog.d/8790.md"])

    def test_two_branch_repo_keeps_origin_head(self):
        # no develop: dev -> main PRs, origin/HEAD = main is the base
        run, _ = fake_git("origin/main", {"origin/main"},
                          {"origin/main": BRANCH_ONLY})
        self.assertEqual(airuleset._handoff_changed_paths(run=run),
                         ["tests/test_a.py", "changelog.d/8790.md"])

    def test_dev_is_never_a_base(self):
        # on a 2-branch repo HEAD is dev itself: an origin/dev base = empty diff
        run, calls = fake_git("origin/main", {"origin/main", "origin/dev"},
                              {"origin/main": BRANCH_ONLY})
        self.assertEqual(airuleset._handoff_changed_paths(run=run),
                         ["tests/test_a.py", "changelog.d/8790.md"])
        self.assertNotIn("origin/dev...HEAD", [c[-1] for c in calls])

    def test_nothing_resolves_fails_open(self):
        run, _ = fake_git(None, set(), {})
        self.assertIsNone(airuleset._handoff_changed_paths(run=run))


if __name__ == "__main__":
    unittest.main()
