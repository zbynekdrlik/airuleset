"""Disk-pressure ageing/reclaim hygiene (#513).

Two independent live gaps, both reproduced on dev1 before this landed:

GAP A (safety) -- the #345/#348 stale-worktree sweep protected a live
worker ONLY via the harness worktree LOCK, but in-session
`isolation:"worktree"` workers are not lock-registered (0 of 11 locked
live). An unlocked live worker is legitimately 0-ahead-of-base + clean
until its first commit -- indistinguishable from a dead merged one -- so
the LIVE install/push sweep classified it a genuine candidate and could
`git worktree remove` it out from under the running worker.
`_target_in_live_use` cannot rescue it (the agent process cwd is the MAIN
checkout, not the worktree). The defensible signal is RECENCY: live
worktrees had activity 2-13 min old, dead ones 22-35 h. `sweep_stale_
worktrees` now keeps a recently-active (or in-live-use) worktree -- an
additive skip that can only prevent a removal, never cause one.

GAP B (disk) -- the batch-38 ENOSPC (`mkdtemp` `/tmp/tmpuoq3_vff`) was the
ext4 htree directory-index cap on a `/tmp` holding 503k uid-owned
`tempfile.mkdtemp` dirs (`tmp[a-z0-9_]{8}`), which `sweep_claude_scratch`
never targeted. `sweep_stray_tmp` reclaims them (report-only by default,
live under AIRULESET_TMP_PYTEST_RECLAIM_LIVE=1), with a precise-regex +
uid + age + inverted-/proc-live-use quad-gate.

Constraint #2 -- worktrees carrying real work (unmerged commits or a dirty
tree) are SALVAGE material: `discover_salvage_worktrees` reports them
loudly, NEVER auto-removed.

Built against REAL temporary git repos with real `git worktree add`/
`git commit` (this repo's convention for git-based logic) and real
`tempfile` dirs on disk with controlled mtimes.
"""

import os
import subprocess
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                       # noqa: E402
import cli_worktree_sweep              # noqa: E402
import cli_scratch_sweep               # noqa: E402

DAY = 86400.0
IDLE = cli_worktree_sweep.STALE_WORKTREE_IDLE_MIN_AGE_S


def _git(args, cwd):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, cwd, r.stderr))
    return r.stdout


def _mkrepo(root, name="proj"):
    r = root / name
    r.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], r)
    _git(["config", "user.email", "t@example.com"], r)
    _git(["config", "user.name", "T"], r)
    (r / "README.md").write_text("x\n")
    _git(["add", "."], r)
    _git(["commit", "-q", "-m", "init"], r)
    return r


def _add_worktree(repo, branch, commits=0, dirty=False):
    wt = repo / ".claude" / "worktrees" / branch
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-b", branch, str(wt)], repo)
    for i in range(commits):
        (wt / ("f%d.txt" % i)).write_text("work %d\n" % i)
        _git(["add", "."], wt)
        _git(["commit", "-q", "-m", "work %d" % i], wt)
    if dirty:
        (wt / "dirty.txt").write_text("uncommitted\n")
    return wt


# ---------------------------------------------------------------------------
# GAP A -- live-worker guard on sweep_stale_worktrees
# ---------------------------------------------------------------------------

class TestLiveWorkerGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-513-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _sweep(self, **kw):
        kw.setdefault("home", self.root)
        kw.setdefault("log_path", self.root / "log")
        kw.setdefault("state_path", self.root / "state")
        kw.setdefault("force", True)
        return airuleset.sweep_stale_worktrees(**kw)

    def test_recently_active_worktree_is_kept(self):
        """A live worker's worktree (fresh mtimes) is NEVER removed -- the
        #513 fix. `now` just after creation gives a tiny recency age
        (< idle threshold) so the guard protects it. RED before the guard:
        this fresh, 0-ahead, clean worktree was a genuine candidate and got
        removed."""
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-live")
        now = time.time() + 60      # activity 60s ago -- well within the idle window
        results = self._sweep(dry_run=False, now=now)
        row = next(r for r in results if r.get("branch") == "worktree-agent-live")
        self.assertFalse(row["removed"], "a recently-active worktree must NOT be removed")
        self.assertTrue(wt.exists(), "the live worker's checkout must survive")
        self.assertIn("live-worker guard", row["reason"])

    def test_dry_run_also_protects_a_recently_active_worktree(self):
        repo = _mkrepo(self.root)
        _add_worktree(repo, "worktree-agent-live")
        now = time.time() + 60
        results = self._sweep(dry_run=True, now=now)
        row = next(r for r in results if r.get("branch") == "worktree-agent-live")
        self.assertNotIn("would remove", row["reason"],
                         "dry-run must AGREE with live -- a live worktree is not 'would remove'")
        self.assertIn("live-worker guard", row["reason"])

    def test_idle_aged_worktree_is_still_reclaimed(self):
        """The guard must not over-block: a genuinely idle (no activity for
        > idle threshold), 0-ahead, clean worktree is still reclaimed."""
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-dead")
        now = time.time() + IDLE + DAY      # last activity is idle+DAY ago
        results = self._sweep(dry_run=False, now=now)
        row = next(r for r in results if r.get("branch") == "worktree-agent-dead")
        self.assertTrue(row["removed"], "an idle-aged 0-ahead clean worktree must be reclaimed")
        self.assertFalse(wt.exists())

    def test_in_live_use_worktree_is_kept_even_when_idle(self):
        """The weak `_target_in_live_use` signal is still honored: a process
        rooted in the tree keeps it even past the idle threshold."""
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-inuse")
        now = time.time() + IDLE + DAY
        with m.patch.object(cli_worktree_sweep, "_worktree_in_live_use", return_value=True):
            results = self._sweep(dry_run=False, now=now)
        row = next(r for r in results if r.get("branch") == "worktree-agent-inuse")
        self.assertFalse(row["removed"])
        self.assertTrue(wt.exists())
        self.assertIn("live use", row["reason"])

    def test_guard_never_creates_a_new_removal(self):
        """The guard is strictly additive: every path either removes exactly
        what the pre-guard sweep removed, or keeps MORE. An idle worktree
        with real commits is still protected by the ahead-of-base check
        (not by the guard)."""
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-work", commits=2)
        now = time.time() + IDLE + DAY
        results = self._sweep(dry_run=False, now=now)
        row = next(r for r in results if r.get("branch") == "worktree-agent-work")
        self.assertFalse(row["removed"])
        self.assertTrue(wt.exists())


# ---------------------------------------------------------------------------
# GAP B constraint #2 -- salvage report
# ---------------------------------------------------------------------------

class TestSalvageReport(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-513-sal-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _salvage(self, now):
        return airuleset.discover_salvage_worktrees(home=self.root, now=now)

    def test_abandoned_unmerged_worktree_is_reported(self):
        repo = _mkrepo(self.root)
        _add_worktree(repo, "worktree-agent-unmerged", commits=3)
        now = time.time() + IDLE + DAY      # abandoned (idle)
        rows = self._salvage(now)
        row = next(r for r in rows if r["branch"] == "worktree-agent-unmerged")
        self.assertEqual(row["ahead"], 3)
        self.assertGreater(row["age_s"], IDLE)

    def test_abandoned_dirty_worktree_is_reported(self):
        repo = _mkrepo(self.root)
        _add_worktree(repo, "worktree-agent-dirty", dirty=True)
        now = time.time() + IDLE + DAY
        rows = self._salvage(now)
        row = next(r for r in rows if r["branch"] == "worktree-agent-dirty")
        self.assertGreater(row["dirty"], 0)

    def test_actively_worked_worktree_is_NOT_salvage(self):
        """A worktree with real commits but RECENTLY touched is in-flight
        work, not salvage -- excluded."""
        repo = _mkrepo(self.root)
        _add_worktree(repo, "worktree-agent-active", commits=2)
        now = time.time() + 60          # recently active
        rows = self._salvage(now)
        self.assertIsNone(next((r for r in rows if r["branch"] == "worktree-agent-active"), None))

    def test_clean_merged_worktree_is_NOT_salvage(self):
        """A 0-ahead clean worktree has no real work -- the safe sweep
        handles it; it is never salvage."""
        repo = _mkrepo(self.root)
        _add_worktree(repo, "worktree-agent-clean")
        now = time.time() + IDLE + DAY
        rows = self._salvage(now)
        self.assertIsNone(next((r for r in rows if r["branch"] == "worktree-agent-clean"), None))

    def test_salvage_worktree_is_never_removed_by_the_sweep(self):
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-unmerged", commits=2)
        now = time.time() + IDLE + DAY
        airuleset.sweep_stale_worktrees(home=self.root, dry_run=False, force=True,
                                        now=now, log_path=self.root / "l",
                                        state_path=self.root / "s")
        self.assertTrue(wt.exists(), "unmerged work must never be auto-removed")
        log = _git(["log", "--oneline", "worktree-agent-unmerged"], repo)
        self.assertEqual(len(log.strip().splitlines()), 3)   # init + 2 commits


# ---------------------------------------------------------------------------
# GAP B -- stray tempfile.mkdtemp litter sweep
# ---------------------------------------------------------------------------

class TestStrayTmpSweep(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-513-tmp-")
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.uid = os.getuid()
        self.now = time.time()

    def _mk(self, name, age_days=10.0):
        p = self.d / name
        p.mkdir()
        (p / "f").write_text("x")
        t = self.now - age_days * DAY
        os.utime(p / "f", (t, t))
        os.utime(p, (t, t))         # backdate the dir LAST (a child write bumps it)
        return p

    def test_precise_regex_matches_only_tempfile_shape(self):
        rx = cli_scratch_sweep._TMP_MKDTEMP_RX
        for good in ("tmpabcd1234", "tmp_r6ofhxm", "tmp00hiixg5"):
            self.assertTrue(rx.match(good), good)
        for bad in ("tmux-1000", "tmp.ABCDEFG", "tmp", "tmpABCD1234", "tmpshort", "tmpabcd12345"):
            self.assertIsNone(rx.match(bad), bad)

    def test_aged_uid_owned_entry_is_a_candidate(self):
        self._mk("tmpaaaa1111", age_days=10)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, proc_dir="/proc")
        self.assertEqual(disc["total_matched"], 1)
        self.assertIsNone(disc["examined"][0]["reason"])

    def test_recent_entry_is_kept(self):
        self._mk("tmpbbbb2222", age_days=1)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, proc_dir="/proc")
        self.assertIn("too recent", disc["examined"][0]["reason"])

    def test_foreign_uid_entry_is_never_touched(self):
        self._mk("tmpcccc3333", age_days=10)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid + 12345, now=self.now, min_age_days=7, proc_dir="/proc")
        self.assertIn("another uid", disc["examined"][0]["reason"])

    def test_symlink_is_refused(self):
        target = self._mk("tmpdddd4444", age_days=10)
        link = self.d / "tmpeeee5555"
        link.symlink_to(target)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, proc_dir="/proc")
        row = next(r for r in disc["examined"] if r["path"].endswith("tmpeeee5555"))
        self.assertIn("symlink", row["reason"])

    def test_in_live_use_is_skipped(self):
        p = self._mk("tmpffff6666", age_days=10)
        key = os.path.realpath(str(p))
        with m.patch.object(cli_scratch_sweep, "_scan_live_tmp_tops", return_value={key}):
            disc = cli_scratch_sweep.discover_stray_tmp_candidates(
                tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, proc_dir="/proc")
        self.assertIn("in live use", disc["examined"][0]["reason"])

    def test_proc_unreadable_skips_everything_failsafe(self):
        self._mk("tmpgggg7777", age_days=10)
        # a non-existent proc_dir -> _scan_live_tmp_tops returns None -> all skipped
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7,
            proc_dir=str(self.d / "no-such-proc"))
        self.assertIn("in live use", disc["examined"][0]["reason"])

    def test_report_only_by_default_deletes_nothing(self):
        p = self._mk("tmphhhh8888", age_days=10)
        os.environ.pop(cli_scratch_sweep.TMP_STRAY_LIVE_ENV, None)
        s = cli_scratch_sweep.sweep_stray_tmp(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, force=True,
            proc_dir="/proc", log_path=self.d / "log", state_path=self.d / "st")
        self.assertEqual(s["removed"], 0)
        self.assertEqual(s["reclaimable"], 1)
        self.assertFalse(s["live"])
        self.assertTrue(p.exists(), "REPORT-ONLY must never delete")

    def test_live_flag_reclaims_aged_keeps_recent(self):
        aged = self._mk("tmpiiii9999", age_days=10)
        recent = self._mk("tmpjjjj0000", age_days=1)
        s = cli_scratch_sweep.sweep_stray_tmp(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, force=True,
            proc_dir="/proc", live=True, log_path=self.d / "log", state_path=self.d / "st")
        self.assertEqual(s["removed"], 1)
        self.assertFalse(aged.exists(), "aged tempfile dir reclaimed")
        self.assertTrue(recent.exists(), "recent tempfile dir kept")

    def test_max_scan_caps_classification_not_the_count(self):
        for i in range(5):
            self._mk("tmpk%07d" % i, age_days=10)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7,
            proc_dir="/proc", max_scan=2)
        self.assertEqual(disc["total_matched"], 5, "total is the true count")
        self.assertEqual(len(disc["examined"]), 2, "classification is capped")
        self.assertTrue(disc["capped"])

    def test_env_flag_enables_live_when_live_param_omitted(self):
        p = self._mk("tmpllll1111", age_days=10)
        with m.patch.dict(os.environ, {cli_scratch_sweep.TMP_STRAY_LIVE_ENV: "1"}):
            s = cli_scratch_sweep.sweep_stray_tmp(
                tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=7, force=True,
                proc_dir="/proc", log_path=self.d / "log", state_path=self.d / "st")
        self.assertTrue(s["live"])
        self.assertEqual(s["removed"], 1)
        self.assertFalse(p.exists())


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestWiring(unittest.TestCase):
    def test_stray_tmp_subcommand_registered(self):
        self.assertIn("sweep-stray-tmp", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["sweep-stray-tmp"], airuleset.cmd_sweep_stray_tmp)

    def test_salvage_and_guard_symbols_reexported(self):
        for name in ("discover_salvage_worktrees", "_worktree_recency_age_s",
                     "_worktree_in_live_use", "STALE_WORKTREE_IDLE_MIN_AGE_S",
                     "sweep_stray_tmp", "discover_stray_tmp_candidates",
                     "_scan_live_tmp_tops", "_TMP_MKDTEMP_RX"):
            self.assertTrue(hasattr(airuleset, name), name)

    def test_salvage_is_opt_in_not_run_without_flag(self):
        """A plain `sweep-worktrees` (no --salvage) must NOT trigger the
        fleet-wide + network salvage scan -- keeps the default path fast and
        the existing wiring tests hermetic."""
        with m.patch.object(cli_worktree_sweep, "sweep_stale_worktrees", return_value=[]):
            with m.patch.object(cli_worktree_sweep, "discover_salvage_worktrees") as sal:
                from io import StringIO
                with m.patch("sys.stdout", StringIO()):
                    airuleset.cmd_sweep_worktrees(SimpleNamespace(dry_run=True))   # no salvage attr
        sal.assert_not_called()

    def test_salvage_flag_runs_the_report(self):
        with m.patch.object(cli_worktree_sweep, "sweep_stale_worktrees", return_value=[]):
            with m.patch.object(cli_worktree_sweep, "discover_salvage_worktrees",
                                return_value=[{"path": "/r/wt", "branch": "b", "repo": "/r",
                                               "ahead": 2, "dirty": 0, "size": 1024,
                                               "age_s": 20 * 3600.0, "wip_backup": False}]) as sal:
                from io import StringIO
                out = StringIO()
                with m.patch("sys.stdout", out):
                    airuleset.cmd_sweep_worktrees(SimpleNamespace(dry_run=True, salvage=True))
        sal.assert_called_once()
        text = out.getvalue()
        self.assertIn("SALVAGE", text)
        self.assertIn("wip-backup=NO", text)

    def test_cmd_stray_tmp_report_only_prints_instruction(self):
        with TemporaryDirectory() as d:
            (Path(d) / "tmpzzzz9999").mkdir()
            from io import StringIO
            out = StringIO()
            with m.patch.object(cli_scratch_sweep, "sweep_stray_tmp",
                                return_value={"total_matched": 42, "classified": 42,
                                              "reclaimable": 10, "removed": 0, "in_use": 1,
                                              "too_recent": 2, "capped": False, "live": False,
                                              "reclaimed_bytes": 0}):
                with m.patch("sys.stdout", out):
                    airuleset.cmd_sweep_stray_tmp(SimpleNamespace(dry_run=False, min_age_days=None))
        text = out.getvalue()
        self.assertIn("REPORT-ONLY", text)
        self.assertIn("42", text)


# ---------------------------------------------------------------------------
# Adversarial-review fixes (2x fresh-context review, #513)
# ---------------------------------------------------------------------------

class TestReviewFixes(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-513-rev-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_intra_sweep_race_worktree_created_after_now_is_kept(self):
        """Worktree-half MINOR: a worktree whose mtimes are in the near
        FUTURE relative to `now` (created after `now` was captured, before
        discovery listed it -- a real multi-repo race) is a brand-new LIVE
        worker. Absolute-distance recency protects it. RED before the fix
        (past-only age → rec None → removed)."""
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-raced")
        now = time.time() - 60      # sweep 'started' 60s before this worktree's mtimes
        results = airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True, now=now,
            log_path=self.root / "l", state_path=self.root / "s")
        row = next(r for r in results if r.get("branch") == "worktree-agent-raced")
        self.assertFalse(row["removed"], "a just-created (near-future-mtime) worktree must be kept")
        self.assertTrue(wt.exists())

    def test_overnight_blocked_worker_within_24h_is_kept(self):
        """Worktree-half MAJOR: the idle window comfortably exceeds an
        overnight/sleep-window wait. A 0-ahead clean worktree idle 12h is
        still protected (< 24h default)."""
        self.assertGreaterEqual(cli_worktree_sweep.STALE_WORKTREE_IDLE_MIN_AGE_S, 24 * 3600)
        repo = _mkrepo(self.root)
        wt = _add_worktree(repo, "worktree-agent-overnight")
        now = time.time() + 12 * 3600      # idle 12h -- an overnight-question wait
        results = airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True, now=now,
            log_path=self.root / "l", state_path=self.root / "s")
        row = next(r for r in results if r.get("branch") == "worktree-agent-overnight")
        self.assertFalse(row["removed"], "a worker idle 12h (overnight) must be kept")
        self.assertTrue(wt.exists())

    def test_scan_live_tmp_tops_total_lockout_returns_None(self):
        """tmp-half MAJOR-1: a /proc that lists pids but exposes NO readable
        cwd/exe/fd link (hardened hidepid) must return None (undeterminable
        → fail safe), not an empty set read as 'nothing live'."""
        fake_proc = self.root / "proc"
        (fake_proc / "1234").mkdir(parents=True)   # a pid dir with no cwd/exe/fd at all
        out = cli_scratch_sweep._scan_live_tmp_tops(tmp_dir="/tmp", proc_dir=str(fake_proc))
        self.assertIsNone(out, "total lockout must be undeterminable (None), not empty set")

    def test_total_lockout_skips_every_stray_tmp_candidate(self):
        d = self.root / "tmp"
        d.mkdir()
        p = d / "tmpaged0001"
        p.mkdir()
        (p / "f").write_text("x")
        t = time.time() - 10 * DAY
        os.utime(p / "f", (t, t))
        os.utime(p, (t, t))
        fake_proc = self.root / "proc"
        (fake_proc / "1").mkdir(parents=True)
        disc = cli_scratch_sweep.discover_stray_tmp_candidates(
            tmp_dir=d, uid=os.getuid(), now=time.time(), min_age_days=7,
            proc_dir=str(fake_proc))
        self.assertIn("in live use", disc["examined"][0]["reason"])

    def test_scandir_failure_sets_examined_error_and_blocks_cadence(self):
        """tmp-half MINOR: a total scandir failure marks examined_error so
        sweep_stray_tmp does NOT advance its 24h cadence (which would
        suppress a retry for a day)."""
        d = self.root / "tmp"
        d.mkdir()
        state = self.root / "st"
        with m.patch.object(cli_scratch_sweep.os, "scandir", side_effect=OSError("boom")):
            disc = cli_scratch_sweep.discover_stray_tmp_candidates(tmp_dir=d, proc_dir="/proc")
            self.assertTrue(disc.get("examined_error"))
            cli_scratch_sweep.sweep_stray_tmp(tmp_dir=d, now=time.time(), force=True,
                                              proc_dir="/proc", log_path=self.root / "log",
                                              state_path=state)
        self.assertFalse(state.exists(), "cadence state must not advance after a scan failure")

    def test_reclaimed_bytes_reflects_tree_size_not_dir_entry(self):
        """tmp-half NIT: reclaimed_bytes must count the tree, not the ~4KB
        dir entry."""
        d = self.root / "tmp"
        d.mkdir()
        p = d / "tmpbig00001"
        p.mkdir()
        (p / "big").write_text("Z" * 50000)
        t = time.time() - 10 * DAY
        os.utime(p / "big", (t, t))
        os.utime(p, (t, t))
        s = cli_scratch_sweep.sweep_stray_tmp(
            tmp_dir=d, uid=os.getuid(), now=time.time(), min_age_days=7, force=True,
            proc_dir="/proc", live=True, log_path=self.root / "log", state_path=self.root / "st")
        self.assertEqual(s["removed"], 1)
        self.assertGreaterEqual(s["reclaimed_bytes"], 50000, "must count the file, not the dir entry")


if __name__ == "__main__":
    unittest.main()
