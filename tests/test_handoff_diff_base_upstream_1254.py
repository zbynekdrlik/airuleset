"""#1254: on a fork-no-merge stream `origin` is the stream's fork, whose
`develop` goes stale behind upstream. The hand-off RFR diff
(`_handoff_changed_paths`) must use `upstream/develop` when that ref exists, the
same base the hand-off gate uses, else upstream-merged files count as the
lane's own and the #1073/#1077 pre-flights raise false blocks (#1289 live case).
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402


def fake_git(head, refs, diffs):
    """`run(argv)` answering symbolic-ref (origin/HEAD -> ``head``) and
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


LANE_ONLY = "addons/x/models.py\n"
STALE_FORK = LANE_ONLY + "docs/slovnormal/build-granc-guide.py\n"


class TestUpstreamBase(unittest.TestCase):

    def test_fork_clone_diffs_against_upstream_develop(self):
        run, _ = fake_git("origin/main",
                          {"origin/main", "origin/develop", "upstream/develop"},
                          {"origin/develop": STALE_FORK,
                           "upstream/develop": LANE_ONLY,
                           "origin/main": STALE_FORK})
        self.assertEqual(airuleset._handoff_changed_paths(run=run),
                         ["addons/x/models.py"])

    def test_plain_clone_without_upstream_keeps_origin_develop(self):
        run, calls = fake_git("origin/main", {"origin/main", "origin/develop"},
                              {"origin/develop": LANE_ONLY,
                               "origin/main": STALE_FORK})
        self.assertEqual(airuleset._handoff_changed_paths(run=run),
                         ["addons/x/models.py"])


if __name__ == "__main__":
    unittest.main()
