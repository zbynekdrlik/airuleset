"""#998 item 8 — lane liveness = the agent is alive, not "the worktree exists".

A MERGED lane (tip is an ancestor of main) is FINISHED: cli_lane_overlap.
gather_live_lanes EXCLUDES it (overlap ignores it) and cli_worktree_sweep
reclaims it IMMEDIATELY with no 24h idle threshold. An UNMERGED lane keeps
today's guards. RED: merged worktree -> not live; unmerged -> live.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_overlap as lo  # noqa: E402
import cli_worktree_sweep as ws  # noqa: E402

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


class TestSweepMergedImmediate(TestCase):
    """The sweep drops the 24h idle guard for a MERGED candidate (its work is on
    base); an UNMERGED candidate keeps today's recency guard."""

    def _fresh_dir(self):
        d = tempfile.mkdtemp()  # a real dir with fresh mtime (< 24h)
        self.addCleanup(lambda: None)
        return d

    def _fake_git(self, merged):
        def run(args, cwd, timeout=15):
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return "" if merged else None   # rc0="" (ancestor), rc1=None
            if args[:2] == ["rev-list", "--count"]:
                return "0"
            return ""                            # remove / branch -D succeed
        return run

    def _candidate(self, path):
        return {"path": path, "branch": "worktree-x", "repo": "/repo",
                "reason": None, "kind": "worktree", "base": "main"}

    def test_merged_recent_candidate_is_reclaimed(self):
        path = self._fresh_dir()
        results = ws.sweep_stale_worktrees(
            dry_run=True, force=True, now=time.time(),
            candidates=[self._candidate(path)], git_run=self._fake_git(True),
            log_path=Path(tempfile.mkdtemp()) / "log")
        row = [r for r in results if r.get("path") == path][0]
        self.assertIn("would remove", row["reason"])  # NOT kept by idle guard

    def test_unmerged_recent_candidate_is_kept(self):
        path = self._fresh_dir()
        results = ws.sweep_stale_worktrees(
            dry_run=True, force=True, now=time.time(),
            candidates=[self._candidate(path)], git_run=self._fake_git(False),
            log_path=Path(tempfile.mkdtemp()) / "log")
        row = [r for r in results if r.get("path") == path][0]
        self.assertIn("kept", row["reason"])          # recency guard still applies


if __name__ == "__main__":
    main()
