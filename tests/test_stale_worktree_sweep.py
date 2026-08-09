"""Systemic stale-worktree sweep (#345) — dead-worker worktree+branch leaks.

Root cause: the harness auto-removes a worker's `.claude/worktrees/agent-<id>`
directory only on a NORMAL agent exit — a worker killed by an API error /
session limit leaves the worktree registered (and thus locked forever, so
even `git branch -D` refuses) with no code path anywhere that ever deletes
the branch once someone eventually removes the directory by hand. A round's
own close-out (`skills/autopilot/SKILL.md` ROUND INTEGRATION step 5) only
ever cleans branches it actually merged — never a sibling round's dead
leftovers. So dead workers leak one worktree + one branch each,
unboundedly, fleet-wide.

This mirrors #315's `purge_stale_tier0_targets` shape exactly (a plain
Python function, cadence-gated via its OWN state file — never leaning on
the 60s watchdog timer, which the FREEZE forbids adding to): discovery is
pure/side-effect-free (`discover_stale_worktrees`), the actual sweep is a
separate cadence-gated function (`sweep_stale_worktrees`) wired as a
non-fatal best-effort step inside `cmd_install()`, plus a manual/testable
CLI entry point (`airuleset.py sweep-worktrees`).

Safety criteria (NON-NEGOTIABLE, verbatim from the ticket): never the
PRIMARY worktree, never a branch named `main`/`dev`/`master`, never a
LOCKED worktree, only a branch with ZERO commits ahead of the repo's own
base — and `git worktree remove` is NEVER passed `--force`, so a dirty
tree survives and is reported, never destroyed.

Built against REAL temporary git repos with real `git worktree add`/
`git commit` (this repo's own established convention for testing
git-based logic per #99/#314's own bullets — a mocked git would silently
drift from real `git worktree remove`/`branch -D` semantics with nothing
to catch it).
"""

import json
import subprocess
import sys
import unittest
import unittest.mock as m
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402

NOW = 1786176246.0          # fixed; never time.time() (repo convention)
DAY = 86400.0


# ---------------------------------------------------------------------------
# Fixture builders — REAL git repos, real worktrees
# ---------------------------------------------------------------------------

def _git(args, cwd):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, cwd, r.stderr))
    return r.stdout


def _mkrepo(root, name="proj", base_branch="main"):
    """A real git repo with one commit on `base_branch`."""
    r = root / name
    r.mkdir(parents=True)
    _git(["init", "-q", "-b", base_branch], r)
    _git(["config", "user.email", "test@example.com"], r)
    _git(["config", "user.name", "Test"], r)
    (r / "README.md").write_text("x\n")
    _git(["add", "."], r)
    _git(["commit", "-q", "-m", "init"], r)
    return r


def _add_worktree(repo, branch, name=None, locked=False, commits=0, dirty=False,
                  base_ref=None):
    """A real `git worktree add -b <branch> <path>` under
    `<repo>/.claude/worktrees/<name>`, optionally with N extra commits,
    an uncommitted (dirty) file, and/or `git worktree lock`ed."""
    name = name or branch
    wt_dir = repo / ".claude" / "worktrees" / name
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", "-b", branch, str(wt_dir)]
    if base_ref:
        args.append(base_ref)
    _git(args, repo)
    for i in range(commits):
        (wt_dir / ("f%d.txt" % i)).write_text("work %d\n" % i)
        _git(["add", "."], wt_dir)
        _git(["commit", "-q", "-m", "work %d" % i], wt_dir)
    if dirty:
        (wt_dir / "dirty.txt").write_text("uncommitted\n")
    if locked:
        _git(["worktree", "lock", str(wt_dir)], repo)
    return wt_dir


# ---------------------------------------------------------------------------
# discover_stale_worktrees — pure classification, no side effects
# ---------------------------------------------------------------------------

class TestDiscoverStaleWorktrees(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _by_branch(self, candidates, branch):
        for c in candidates:
            if c.get("branch") == branch:
                return c
        return None

    def test_genuine_dead_worker_worktree_is_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "worktree-agent-dead1")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"], "a genuine 0-commit, unlocked, "
                          "clean worktree must be a real candidate")

    def test_primary_worktree_alone_yields_no_candidates(self):
        _mkrepo(self.root, "proj")
        cands = airuleset.discover_stale_worktrees(home=self.root)
        self.assertEqual(cands, [])

    def test_locked_worktree_is_never_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-live", locked=True)
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "worktree-agent-live")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("locked", row["reason"].lower())

    def test_worktree_with_commits_ahead_is_never_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-hasrealwork", commits=2)
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "worktree-agent-hasrealwork")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("ahead", row["reason"].lower())

    def test_a_second_worktree_literally_checked_out_to_main_is_protected(self):
        # `main` cannot be checked out a SECOND time while the primary
        # worktree already has it -- create the repo on a differently-
        # named default branch, then check `main` out only in the second
        # worktree (never in the primary).
        repo = _mkrepo(self.root, "proj", base_branch="trunk")
        _git(["branch", "main"], repo)
        wt = repo / ".claude" / "worktrees" / "second-main"
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", str(wt), "main"], repo)
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "main")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])

    def test_dev_is_preferred_over_main_for_a_two_branch_repo(self):
        """A two-branch repo: `dev` has moved ahead of `main` with real,
        unreleased work BEFORE the dead worker's worktree was ever
        created. A worker's worktree branch, forked from `dev` with ZERO
        commits of its own, must be discovered as a genuine candidate —
        comparing against bare `main` instead would count every one of
        dev's own in-flight commits as "the worker's own work" and
        wrongly exclude it forever."""
        repo = _mkrepo(self.root, "proj", base_branch="main")
        _git(["checkout", "-q", "-b", "dev"], repo)
        (repo / "inflight.txt").write_text("unreleased work\n")
        _git(["add", "."], repo)
        _git(["commit", "-q", "-m", "unreleased dev work"], repo)
        _git(["checkout", "-q", "main"], repo)
        # Worker's worktree forked from dev's current tip, no work of its own.
        _add_worktree(repo, "worktree-agent-fromdev", base_ref="dev")
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "worktree-agent-fromdev")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"],
                          "must be a candidate when compared against dev "
                          "(its real fork base), not bare main")

    def test_no_base_branch_available_never_guesses(self):
        repo = _mkrepo(self.root, "proj", base_branch="trunk")
        _add_worktree(repo, "worktree-agent-orphanbase")
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "worktree-agent-orphanbase")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])

    def test_a_worktree_in_a_repo_with_no_worktrees_dir_naming_is_still_found(self):
        """The ticket's own evidence names FIVE stale worktrees from the
        OLD custom-naming convention (ticket-313/315/316,
        issue-307-quals-fix, agent-333resume) — none matches
        `worktree-agent-*`. Discovery must never filter by branch-name
        shape; the objective safety criteria alone decide."""
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "ticket-313", name="ticket-313")
        cands = airuleset.discover_stale_worktrees(home=self.root)
        row = self._by_branch(cands, "ticket-313")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"])

    def test_the_worktree_itself_is_never_discovered_as_its_own_repo_root(self):
        """`_checkout_roots` finds a worktree's own `.git` FILE as a
        candidate root too — discovery must filter those out (only a
        `.git` DIRECTORY is a primary checkout) or it would try to run
        `git worktree list` FROM inside a worktree and either double-count
        or misbehave."""
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        # Sanity: this must not raise, and must resolve exactly one repo's
        # worth of candidates (not one per worktree-as-fake-root too).
        cands = airuleset.discover_stale_worktrees(home=self.root)
        self.assertEqual(len(cands), 1)


# ---------------------------------------------------------------------------
# sweep_stale_worktrees — the actual mutation, real removal
# ---------------------------------------------------------------------------

class TestSweepStaleWorktrees(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log_path = self.root / "logs" / "worktree-sweep.log"
        self.state_path = self.root / "state" / "worktree-sweep-state.json"

    def _sweep(self, **kw):
        kw.setdefault("home", self.root)
        kw.setdefault("now", NOW)
        kw.setdefault("log_path", self.log_path)
        kw.setdefault("state_path", self.state_path)
        kw.setdefault("force", True)
        return airuleset.sweep_stale_worktrees(**kw)

    def test_genuine_candidate_is_removed_and_branch_deleted(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-dead1")
        results = self._sweep(dry_run=False)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["removed"])
        self.assertFalse(wt.exists(), "the worktree directory must actually be gone")
        listed = _git(["worktree", "list"], repo)
        self.assertNotIn("worktree-agent-dead1", listed)
        branches = _git(["branch", "--list", "worktree-agent-dead1"], repo)
        self.assertEqual(branches.strip(), "", "the branch must be deleted too")

    def test_locked_worktree_survives_the_sweep(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-live", locked=True)
        results = self._sweep(dry_run=False)
        self.assertFalse(results[0]["removed"])
        self.assertTrue(wt.exists(), "a locked (active worker) worktree must survive")
        listed = _git(["worktree", "list"], repo)
        self.assertIn("worktree-agent-live", listed)

    def test_dirty_tree_survives_never_force_removed(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-dirty", dirty=True)
        results = self._sweep(dry_run=False)
        self.assertFalse(results[0]["removed"])
        self.assertTrue(wt.exists(), "a dirty worktree must NEVER be force-removed")
        self.assertTrue((wt / "dirty.txt").exists(), "the uncommitted file must survive intact")
        listed = _git(["worktree", "list"], repo)
        self.assertIn("worktree-agent-dirty", listed)

    def test_worktree_with_real_work_survives_untouched(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-hasrealwork", commits=2)
        results = self._sweep(dry_run=False)
        self.assertFalse(results[0]["removed"])
        self.assertTrue(wt.exists())
        log = _git(["log", "--oneline", "worktree-agent-hasrealwork"], repo)
        self.assertEqual(len(log.strip().splitlines()), 3,  # init + 2 work commits
                         "the branch's own commits must be untouched")

    def test_dry_run_never_removes_anything(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-dead1")
        results = self._sweep(dry_run=True)
        self.assertFalse(results[0]["removed"])
        self.assertTrue(wt.exists())
        self.assertIn("dry", results[0]["reason"].lower())

    def test_never_passes_force_to_git_worktree_remove(self):
        """Structural lock (NON-NEGOTIABLE per the ticket) — never trust a
        behavioural test alone here: assert directly, via an injected
        git_run spy, that no invocation of `worktree remove` ever carries
        `--force` anywhere in its argv."""
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        calls = []
        real = airuleset._worktree_git

        def spy(args, cwd, timeout=15):
            calls.append(list(args))
            return real(args, cwd, timeout=timeout)

        self._sweep(dry_run=False, git_run=spy)
        remove_calls = [c for c in calls if c[:2] == ["worktree", "remove"]]
        self.assertTrue(remove_calls, "the sweep must have attempted a real removal")
        for c in remove_calls:
            self.assertNotIn("--force", c)
            self.assertNotIn("-f", c)

    def test_only_rows_with_reason_none_are_ever_acted_on(self):
        """Direct test of sweep's OWN filtering, independent of discovery —
        a hand-built mixed candidate list must only ever act on the
        genuinely-clean row."""
        repo = _mkrepo(self.root, "proj")
        wt_ok = _add_worktree(repo, "worktree-agent-ok")
        wt_locked = _add_worktree(repo, "worktree-agent-locked", locked=True)
        candidates = [
            {"path": str(wt_ok), "branch": "worktree-agent-ok", "repo": str(repo), "reason": None},
            {"path": str(wt_locked), "branch": "worktree-agent-locked", "repo": str(repo),
             "reason": "locked (active worker)"},
        ]
        results = self._sweep(dry_run=False, candidates=candidates)
        removed = {r["branch"]: r["removed"] for r in results}
        self.assertTrue(removed["worktree-agent-ok"])
        self.assertFalse(removed["worktree-agent-locked"])
        self.assertTrue(wt_locked.exists())

    # --- logging ------------------------------------------------------
    def test_removal_is_logged(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        self._sweep(dry_run=False)
        text = self.log_path.read_text()
        self.assertIn("REMOVED", text)
        self.assertIn("worktree-agent-dead1", text)

    def test_skip_is_also_logged(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-live", locked=True)
        self._sweep(dry_run=False)
        text = self.log_path.read_text()
        self.assertIn("SKIP", text)
        self.assertIn("locked", text.lower())

    def test_dry_run_log_says_would_remove_not_removed(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        self._sweep(dry_run=True)
        text = self.log_path.read_text()
        self.assertIn("WOULD-REMOVE", text)
        self.assertNotIn("\nREMOVED", "\n" + text)

    # --- cadence gate (mirrors #315's own established shape) --------------
    def test_second_call_within_interval_is_a_noop_without_force(self):
        repo = _mkrepo(self.root, "proj")
        wt1 = _add_worktree(repo, "worktree-agent-first")
        first = self._sweep(dry_run=False, force=False)
        self.assertTrue(first[0]["removed"])
        self.assertFalse(wt1.exists())
        wt2 = _add_worktree(repo, "worktree-agent-second")
        second = self._sweep(dry_run=False, force=False, now=NOW + 60)
        self.assertEqual(second, [])
        self.assertTrue(wt2.exists(), "the cadence gate must block a too-soon second sweep")

    def test_force_always_bypasses_cadence(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-first")
        self._sweep(dry_run=False, force=True)
        wt2 = _add_worktree(repo, "worktree-agent-second")
        second = self._sweep(dry_run=False, force=True, now=NOW + 60)
        self.assertEqual(len(second), 1)
        self.assertFalse(wt2.exists())

    def test_dry_run_always_bypasses_cadence_too(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-first")
        self._sweep(dry_run=False, force=True)
        _add_worktree(repo, "worktree-agent-second")
        second = self._sweep(dry_run=True, force=False, now=NOW + 60)
        self.assertEqual(len(second), 1)

    def test_future_cadence_stamp_does_not_wedge_the_gate_forever(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-dead1")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"last_run": NOW + 30 * DAY}))
        results = self._sweep(dry_run=False, force=False)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["removed"])
        self.assertFalse(wt.exists())

    def test_discovery_error_is_logged_and_does_not_stamp_cadence(self):
        _mkrepo(self.root, "proj")
        with m.patch.object(airuleset, "discover_stale_worktrees",
                            side_effect=RuntimeError("boom")):
            results = self._sweep(dry_run=False, force=False)
        self.assertEqual(len(results), 1)
        self.assertIn("discovery error", results[0]["reason"])
        log_text = self.log_path.read_text()
        self.assertIn("discovery error", log_text)
        self.assertFalse(self.state_path.exists())


# ---------------------------------------------------------------------------
# CLI wiring — `airuleset.py sweep-worktrees`
# ---------------------------------------------------------------------------

class TestCmdSweepWorktreesWiring(unittest.TestCase):
    def test_subcommand_registered(self):
        self.assertIn("sweep-worktrees", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["sweep-worktrees"], airuleset.cmd_sweep_worktrees)

    def test_forwards_dry_run_and_forces(self):
        ns = SimpleNamespace(dry_run=True)
        with m.patch.object(airuleset, "sweep_stale_worktrees") as p:
            p.return_value = [{"path": "/x/wt", "branch": "worktree-agent-x", "repo": "/x",
                               "removed": True, "reason": "would remove (dry-run)"}]
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.cmd_sweep_worktrees(ns)
        p.assert_called_once()
        kwargs = p.call_args.kwargs
        self.assertTrue(kwargs.get("dry_run"))
        self.assertTrue(kwargs.get("force"),
                        "a manual CLI invocation must always bypass the cadence gate")

    def test_real_run_reports_removed_not_would_remove(self):
        ns = SimpleNamespace(dry_run=False)
        with m.patch.object(airuleset, "sweep_stale_worktrees") as p:
            p.return_value = [{"path": "/x/wt", "branch": "worktree-agent-x", "repo": "/x",
                               "removed": True, "reason": "removed"}]
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.cmd_sweep_worktrees(ns)
        text = out.getvalue()
        self.assertIn("REMOVED", text)
        self.assertNotIn("WOULD REMOVE", text)


if __name__ == "__main__":
    unittest.main()
