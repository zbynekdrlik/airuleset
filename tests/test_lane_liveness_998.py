"""#998 item 8 (part 1) — lane liveness for OVERLAP = the agent is alive, not
"the worktree exists".

A MERGED lane (tip is an ancestor of the base) is FINISHED, so
cli_lane_overlap.gather_live_lanes EXCLUDES it — this fixes the false OVERLAP
the 8/8 merged worktrees caused (the reason #998's own dispatch needed an
OVERLAP-BYPASS). RED: merged worktree -> not live; unmerged -> live.

NOTE (part 2 deferred): item 8 also asked the sweep to reclaim a merged lane
IMMEDIATELY (no 24h idle threshold). That directly conflicts with the #513
data-loss guard (test_disk_hygiene_513.py's TestLiveWorkerGuard/TestReviewFixes
protect a fresh / intra-sweep-raced / overnight-blocked UNLOCKED-but-live
0-ahead worktree via exactly that 24h recency guard). Reversing a hard-won
data-loss guard on an inference is out of a worker's remit — flagged in the
LANE-RETURN for the owner/supervisor to resolve; the sweep is UNCHANGED here.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_overlap as lo  # noqa: E402

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True,
                   capture_output=True, text=True, env=_ENV)


class TestGatherLiveLanesMerged(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("base")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _add_lane(self, name, commit=True):
        wt = Path(self.tmp) / name
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-" + name, str(wt))
        if commit:
            (wt / (name + ".txt")).write_text("x")
            _git(wt, "add", name + ".txt")
            _git(wt, "commit", "-qm", name + " work")
        return wt

    def test_merged_lane_is_not_live_unmerged_is(self):
        self._add_lane("merged")           # commit on the branch
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "merge merged",
             "worktree-merged")            # now an ancestor of main
        self._add_lane("unmerged")         # commit, NOT merged
        lanes = lo.gather_live_lanes(str(self.repo))
        refs = {ln["ref"] for ln in lanes}
        self.assertIn("worktree-unmerged", refs)
        self.assertNotIn("worktree-merged", refs)

    def test_fresh_zero_ahead_lane_is_not_live(self):
        # forked at main, NO commit -> tip == main == ancestor of main = merged.
        self._add_lane("fresh", commit=False)
        lanes = lo.gather_live_lanes(str(self.repo))
        self.assertNotIn("worktree-fresh", {ln["ref"] for ln in lanes})


if __name__ == "__main__":
    main()
