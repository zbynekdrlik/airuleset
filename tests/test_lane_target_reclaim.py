"""#545 -- merged worktree-lane target/ reclaim.

`purge_merged_lane_targets` reclaims ONLY the `target/` of a worktree LANE
whose branch is MERGED into its base (0-ahead) AND whose reflog shows a real
AUTHORED commit -- the robust distinguisher #513 said 0-ahead alone cannot
give between a merged-DONE lane and a FRESH 0-ahead live worker. Guarded five
ways (0-ahead + authored-commit + Tier-0 + not-in-live-use + idle grace) + a
re-check before delete; the worktree and branch are NEVER removed (the #345
sweep owns whole-lane removal at 24h).

Real git repos + real `git worktree add` + real reflogs throughout -- the
classification turns on genuine git state, never a mock of it (only /proc and
the Tier-0 hook are injected, for determinism).
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_worktree_sweep                                   # noqa: E402

# A FIXED reference time -- every backdate is relative to this, so recency is
# deterministic regardless of when the test actually runs.
NOW = 1_800_000_000.0


def _git(args, cwd):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, cwd, r.stderr))
    return r.stdout


def _mkrepo(root, name="proj", base_branch="main", claude_md=True, marker=None):
    r = root / name
    r.mkdir(parents=True)
    _git(["init", "-q", "-b", base_branch], r)
    _git(["config", "user.email", "test@example.com"], r)
    _git(["config", "user.name", "Test"], r)
    (r / "README.md").write_text("x\n")
    if claude_md:
        body = "# Test project\n"
        if marker:
            body += "\n<!-- airuleset:local-builds=%s -->\n" % marker
        (r / "CLAUDE.md").write_text(body)
    _git(["add", "."], r)
    _git(["commit", "-q", "-m", "init"], r)
    return r


def _add_lane(repo, branch, name=None, commits=0, base_ref=None):
    """Real `git worktree add -b <branch>` under
    `<repo>/.claude/worktrees/<name>`, with N authored commits."""
    name = name or branch
    wt = repo / ".claude" / "worktrees" / name
    wt.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", "-b", branch, str(wt)]
    if base_ref:
        args.append(base_ref)
    _git(args, repo)
    # Branch-namespaced filenames + content so a SECOND lane forked from a
    # base that already merged an earlier lane still produces a genuine
    # change (otherwise an identical `f0.txt` is "nothing to commit").
    for i in range(commits):
        (wt / ("%s_f%d.txt" % (branch, i))).write_text("%s work %d\n" % (branch, i))
        _git(["add", "."], wt)
        _git(["commit", "-q", "-m", "feat(%s): work %d" % (branch, i)], wt)
    return wt


def _merge_into_base(repo, branch, base_branch="main"):
    """Merge <branch> INTO the primary checkout's base branch, leaving the
    branch 0-ahead of base (a real merged lane)."""
    _git(["merge", "--no-edit", branch], repo)  # primary is on base_branch


def _make_target(lane, size_files=3):
    tgt = lane / "target"
    (tgt / "debug").mkdir(parents=True, exist_ok=True)
    (tgt / ".rustc_info.json").write_text('{"x":1}\n')
    for i in range(size_files):
        (tgt / "debug" / ("art%d.bin" % i)).write_text("A" * 4096)
    return tgt


def _backdate_recency(repo, lane, name, age_s, now=NOW):
    """Set every mtime `_worktree_recency_age_s` reads (git-admin HEAD/index/
    logs/HEAD/ORIG_HEAD + the lane's top-level entries + the lane dir itself)
    to `now - age_s`, so the lane reads as idle for `age_s`."""
    t = now - age_s
    admin = repo / ".git" / "worktrees" / name
    for rel in ("HEAD", "index", "logs/HEAD", "ORIG_HEAD"):
        p = admin / rel
        if p.exists():
            os.utime(str(p), (t, t))
    if admin.exists():
        os.utime(str(admin), (t, t))
    for entry in os.scandir(lane):
        os.utime(entry.path, (t, t))
    os.utime(str(lane), (t, t))


def _mkfakeproc(root, entries):
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for e in entries:
        pdir = proc / e["pid"]
        pdir.mkdir()
        if e.get("cwd") is not None:
            os.symlink(e["cwd"], pdir / "cwd")
        fdd = pdir / "fd"
        fdd.mkdir()
        for i, tgt in enumerate(e.get("fds", [])):
            os.symlink(tgt, fdd / str(i))
    return proc


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-lane-target-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.empty_proc = self.root / "empty-proc"
        self.empty_proc.mkdir()
        self.log = self.root / "lane.log"
        self.state = self.root / "lane-state.json"

    def _run(self, **kw):
        kw.setdefault("home", self.root)
        kw.setdefault("now", NOW)
        kw.setdefault("force", True)
        kw.setdefault("proc_dir", self.empty_proc)
        kw.setdefault("tier0_fn", lambda cwd: True)
        kw.setdefault("log_path", self.log)
        kw.setdefault("state_path", self.state)
        return cli_worktree_sweep.purge_merged_lane_targets(**kw)

    def _by_branch(self, results, branch):
        for r in results:
            if r.get("branch") == branch:
                return r
        return None


class TestPurgeMergedLaneTargets(_Base):
    def _merged_lane(self, repo, branch="worktree-merged", name=None, age_s=5 * 3600):
        name = name or branch
        lane = _add_lane(repo, branch, name=name, commits=2)
        _merge_into_base(repo, branch)
        tgt = _make_target(lane)
        _backdate_recency(repo, lane, name, age_s)
        return lane, tgt

    def test_merged_lane_target_is_reclaimed(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo)
        results = self._run(dry_run=False)
        row = self._by_branch(results, "worktree-merged")
        self.assertIsNotNone(row)
        self.assertTrue(row["purged"], "a merged, idle, tier-0 lane target must be reclaimed: %s"
                        % row.get("reason"))
        self.assertFalse(tgt.exists(), "target/ must be gone after a live purge")

    def test_target_only_worktree_and_branch_survive(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo)
        self._run(dry_run=False)
        self.assertTrue(lane.exists(), "the lane worktree dir must NOT be removed")
        self.assertTrue((lane / "README.md").exists(), "lane source must survive")
        wts = _git(["worktree", "list"], repo)
        self.assertIn("worktree-merged", wts, "the worktree must stay registered")
        branches = _git(["branch", "--list", "worktree-merged"], repo)
        self.assertIn("worktree-merged", branches, "the branch must NOT be deleted")

    def test_fresh_zero_ahead_worker_target_is_kept(self):
        # 0-ahead but NEVER authored a commit -> a fresh live worker (#513's
        # exact ambiguity). Its reflog holds only "branch: Created from ...".
        repo = _mkrepo(self.root)
        lane = _add_lane(repo, "worktree-fresh", commits=0)
        tgt = _make_target(lane)
        _backdate_recency(repo, lane, "worktree-fresh", 5 * 3600)
        results = self._run(dry_run=False)
        row = self._by_branch(results, "worktree-fresh")
        self.assertIsNotNone(row)
        self.assertFalse(row["purged"], "a fresh 0-ahead worker's target must be kept")
        self.assertIn("reflog", row["reason"].lower())
        self.assertTrue(tgt.exists())

    def test_unmerged_lane_target_is_kept(self):
        repo = _mkrepo(self.root)
        lane = _add_lane(repo, "worktree-unmerged", commits=2)  # NOT merged
        tgt = _make_target(lane)
        _backdate_recency(repo, lane, "worktree-unmerged", 5 * 3600)
        results = self._run(dry_run=False)
        row = self._by_branch(results, "worktree-unmerged")
        self.assertIsNotNone(row)
        self.assertFalse(row["purged"], "an unmerged lane's target must be kept")
        self.assertIn("ahead", row["reason"].lower())
        self.assertTrue(tgt.exists())

    def test_too_recent_merged_lane_is_kept(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo, age_s=1 * 3600)  # < 2h grace
        results = self._run(dry_run=False)
        row = self._by_branch(results, "worktree-merged")
        self.assertIsNotNone(row)
        self.assertFalse(row["purged"], "a merged lane idle < grace must be kept")
        self.assertIn("grace", row["reason"].lower())
        self.assertTrue(tgt.exists())

    def test_tier1_lane_is_kept(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo)
        results = self._run(dry_run=False, tier0_fn=lambda cwd: False)
        row = self._by_branch(results, "worktree-merged")
        self.assertFalse(row["purged"], "a Tier-1/2 lane's target must never be touched")
        self.assertIn("tier 0", row["reason"].lower())
        self.assertTrue(tgt.exists())

    def test_in_live_use_target_is_kept(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo)
        proc = _mkfakeproc(self.root, [{"pid": "4242", "cwd": str(tgt),
                                        "fds": [str(tgt / "debug")]}])
        results = self._run(dry_run=False, proc_dir=proc)
        row = self._by_branch(results, "worktree-merged")
        self.assertFalse(row["purged"], "an in-live-use target must be kept")
        self.assertIn("live use", row["reason"].lower())
        self.assertTrue(tgt.exists())

    def test_dry_run_never_deletes(self):
        repo = _mkrepo(self.root)
        lane, tgt = self._merged_lane(repo)
        results = self._run(dry_run=True)
        row = self._by_branch(results, "worktree-merged")
        self.assertTrue(row["purged"], "dry-run marks a would-be-reclaim")
        self.assertTrue(tgt.exists(), "dry-run must never actually delete target/")

    def test_symlink_target_is_never_followed(self):
        repo = _mkrepo(self.root)
        name = "worktree-symlink"
        lane = _add_lane(repo, "worktree-symlink", name=name, commits=2)
        _merge_into_base(repo, "worktree-symlink")
        real_elsewhere = self.root / "elsewhere"
        real_elsewhere.mkdir()
        (real_elsewhere / "keep.txt").write_text("do not delete\n")
        os.symlink(str(real_elsewhere), str(lane / "target"))
        _backdate_recency(repo, lane, name, 5 * 3600)
        results = self._run(dry_run=False)
        row = self._by_branch(results, "worktree-symlink")
        self.assertFalse(row["purged"])
        self.assertIn("symlink", row["reason"].lower())
        self.assertTrue((real_elsewhere / "keep.txt").exists(),
                        "a symlinked target/ must never be followed/deleted")

    def test_primary_checkout_target_is_never_touched(self):
        # A primary checkout's own target/ (a human's main-checkout build) is
        # the #315 sweep's job (7d floor), never this aggressive path.
        repo = _mkrepo(self.root)
        prim_tgt = _make_target(repo)
        self._merged_lane(repo)  # a real lane so discovery has something to do
        self._run(dry_run=False)
        self.assertTrue(prim_tgt.exists(), "the primary checkout's target/ must never be reclaimed")

    def test_real_hook_tier0_merged_lane_is_reclaimed(self):
        # No injected tier0_fn -> the REAL block-tier0-local-build.sh hook.
        # A plain CLAUDE.md (no marker) = Tier 0 -> reclaimed.
        repo = _mkrepo(self.root, claude_md=True)
        lane, tgt = self._merged_lane(repo)
        results = self._run(dry_run=False, tier0_fn=None)
        row = self._by_branch(results, "worktree-merged")
        self.assertTrue(row["purged"], "real-hook Tier-0 merged lane must be reclaimed: %s"
                        % row.get("reason"))
        self.assertFalse(tgt.exists())

    def test_real_hook_tier1_marker_lane_is_kept(self):
        # A CLAUDE.md carrying the local-builds=allowed marker = Tier 1.
        repo = _mkrepo(self.root, claude_md=True, marker="allowed")
        lane, tgt = self._merged_lane(repo)
        results = self._run(dry_run=False, tier0_fn=None)
        row = self._by_branch(results, "worktree-merged")
        self.assertFalse(row["purged"], "a Tier-1 marker lane must never be reclaimed by the real hook")
        self.assertTrue(tgt.exists())


class TestReflogDistinguisher(_Base):
    def test_merged_lane_reflog_has_authored_commit(self):
        repo = _mkrepo(self.root)
        _add_lane(repo, "worktree-work", commits=2)
        self.assertTrue(cli_worktree_sweep._branch_reflog_has_authored_commit(
            repo, "worktree-work"))

    def test_fresh_worker_reflog_has_no_authored_commit(self):
        repo = _mkrepo(self.root)
        _add_lane(repo, "worktree-fresh0", commits=0)
        self.assertFalse(cli_worktree_sweep._branch_reflog_has_authored_commit(
            repo, "worktree-fresh0"))

    def test_base_sync_only_reflog_has_no_authored_commit(self):
        # A lane that only base-synced (a merge op, never an authored commit)
        # must NOT read as merged-done.
        repo = _mkrepo(self.root)
        lane = _add_lane(repo, "worktree-synced", commits=0)
        # advance base, then merge it into the lane (a real reflog merge op)
        (repo / "advance.txt").write_text("base moved\n")
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "base advance"], repo)
        _git(["merge", "--no-edit", "main"], lane)
        self.assertFalse(cli_worktree_sweep._branch_reflog_has_authored_commit(
            repo, "worktree-synced"),
            "a base-sync-only lane (merge op, no authored commit) is not merged-done")

    def test_absent_branch_reflog_is_false_fail_safe(self):
        repo = _mkrepo(self.root)
        self.assertFalse(cli_worktree_sweep._branch_reflog_has_authored_commit(
            repo, "worktree-nonexistent"))


class TestCadenceGate(_Base):
    def _merged_lane(self, repo):
        lane = _add_lane(repo, "worktree-merged", commits=2)
        _merge_into_base(repo, "worktree-merged")
        _make_target(lane)
        _backdate_recency(repo, lane, "worktree-merged", 5 * 3600)
        return lane

    def test_second_call_within_interval_is_noop_without_force(self):
        repo = _mkrepo(self.root)
        self._merged_lane(repo)
        first = self._run(force=False, dry_run=False)
        self.assertTrue(any(r.get("purged") for r in first))
        # second lane, but the state stamp is fresh -> gate closed
        lane2 = _add_lane(repo, "worktree-merged2", commits=2)
        _merge_into_base(repo, "worktree-merged2")
        tgt2 = _make_target(lane2)
        _backdate_recency(repo, lane2, "worktree-merged2", 5 * 3600)
        second = self._run(force=False, dry_run=False)
        self.assertEqual(second, [], "a second call within the interval is a no-op without force")
        self.assertTrue(tgt2.exists(), "the gated no-op must not have touched the second lane")

    def test_force_bypasses_gate(self):
        repo = _mkrepo(self.root)
        self._merged_lane(repo)
        self._run(force=False, dry_run=False)  # stamps state
        forced = self._run(force=True, dry_run=False)
        # force always runs discovery (even if nothing left to purge)
        self.assertIsInstance(forced, list)


class TestLogging(_Base):
    def test_purge_is_logged(self):
        repo = _mkrepo(self.root)
        lane = _add_lane(repo, "worktree-merged", commits=2)
        _merge_into_base(repo, "worktree-merged")
        _make_target(lane)
        _backdate_recency(repo, lane, "worktree-merged", 5 * 3600)
        self._run(dry_run=False)
        text = self.log.read_text()
        self.assertIn("PURGED", text)
        self.assertIn("worktree-merged", text)

    def test_skip_is_logged(self):
        repo = _mkrepo(self.root)
        lane = _add_lane(repo, "worktree-unmerged", commits=2)
        _make_target(lane)
        _backdate_recency(repo, lane, "worktree-unmerged", 5 * 3600)
        self._run(dry_run=False)
        text = self.log.read_text()
        self.assertIn("SKIP", text)


if __name__ == "__main__":
    unittest.main()
