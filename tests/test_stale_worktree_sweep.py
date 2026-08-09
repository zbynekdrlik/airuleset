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
import os
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


def _add_worktree_with_reason(repo, branch, reason, name=None, commits=0, dirty=False):
    """Same as `_add_worktree` but locked with an EXPLICIT `--reason`
    text (#348) — the shape `discover_stale_worktrees`'s locked-dead-
    session classification actually parses a pid/starttime out of."""
    name = name or branch
    wt_dir = repo / ".claude" / "worktrees" / name
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "-b", branch, str(wt_dir)], repo)
    for i in range(commits):
        (wt_dir / ("f%d.txt" % i)).write_text("work %d\n" % i)
        _git(["add", "."], wt_dir)
        _git(["commit", "-q", "-m", "work %d" % i], wt_dir)
    if dirty:
        (wt_dir / "dirty.txt").write_text("uncommitted\n")
    _git(["worktree", "lock", str(wt_dir), "--reason", reason], repo)
    return wt_dir


def _mkbranch(repo, branch, from_ref="HEAD", commits=0):
    """A real local branch with NO registered worktree (#348's own
    "hand-removed directory" root cause) — `git branch <branch>
    <from_ref>`, optionally with N extra commits made through a
    throwaway worktree that is immediately `worktree remove`d again, so
    the branch stays genuinely orphaned afterward."""
    _git(["branch", branch, from_ref], repo)
    if commits:
        tmp_wt = repo / ".claude" / "worktrees" / ("_mkbranch_tmp_" + branch)
        tmp_wt.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", str(tmp_wt), branch], repo)
        for i in range(commits):
            (tmp_wt / ("f%d.txt" % i)).write_text("work %d\n" % i)
            _git(["add", "."], tmp_wt)
            _git(["commit", "-q", "-m", "work %d" % i], tmp_wt)
        _git(["worktree", "remove", str(tmp_wt)], repo)


def _backdate(path, now, age_s):
    """Set both atime and mtime of `path` to `now - age_s` — how a test
    proves a branch ref / lock marker is "several days old" without
    waiting for real wall-clock time to pass. `now` is always the
    module's own FIXED `NOW` constant, never `time.time()`, so this is
    deterministic regardless of when the test actually runs."""
    t = now - age_s
    os.utime(str(path), (t, t))


def _fake_proc_stat(pid, start):
    """A syntactically real-shaped `/proc/<pid>/stat` line — `comm` is a
    fixed name, 19 filler fields (state..itrealvalue) precede `start`
    (field 22 overall == index 19 once pid+comm are stripped), matching
    the REAL field layout `_pid_is_dead` parses (verified live against a
    genuine `/proc/<pid>/stat` on this box before writing this helper)."""
    fields_after_comm = ["0"] * 19 + [str(start)] + ["0", "0"]
    return "%d (proc) %s" % (pid, " ".join(fields_after_comm))


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

    def test_worktree_remove_uses_a_generous_timeout(self):
        """A worktree can carry several GB of build artefacts (#315's own
        camera-box/songplayer/spinbike measurements) -- `worktree remove`
        must not share the lightweight 15s default every other git-plumbing
        call in this module uses, or a genuinely large, removable worktree
        reads as permanently 'refused' on every sweep."""
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-dead1")
        calls = []
        real = airuleset._worktree_git

        def spy(args, cwd, timeout=15):
            calls.append((list(args), timeout))
            return real(args, cwd, timeout=timeout)

        self._sweep(dry_run=False, git_run=spy)
        remove_calls = [t for a, t in calls if a[:2] == ["worktree", "remove"]]
        self.assertTrue(remove_calls, "the sweep must have attempted a real removal")
        for t in remove_calls:
            self.assertEqual(t, airuleset.STALE_WORKTREE_REMOVE_TIMEOUT_S)
            self.assertGreater(t, 15, "must be strictly more generous than the plumbing default")

    def test_new_commit_between_discovery_and_removal_saves_the_branch(self):
        """TOCTOU close: a candidate list handed to sweep_stale_worktrees
        (candidates=..., bypassing a fresh discover_stale_worktrees call)
        may be stale by the time this specific candidate's turn comes up --
        something could have added a genuine commit to the branch in the
        meantime. `git worktree remove` itself only refuses on a DIRTY
        (uncommitted) tree, never on extra COMMITS, so the worktree removal
        succeeds regardless -- the branch delete must independently
        re-verify 0-ahead immediately before acting, or a real commit is
        silently destroyed (salvage-before-discarding-work.md)."""
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-racer")
        # Discover BEFORE the extra commit lands -- proves ahead=0 as of
        # discovery time, exactly like a real (possibly slow) sweep would.
        stale_candidates = airuleset.discover_stale_worktrees(home=self.root)
        self.assertEqual(len(stale_candidates), 1)
        self.assertIsNone(stale_candidates[0]["reason"])
        # Now something adds a REAL commit to the branch -- e.g. a stray
        # process still attached to the worktree between discovery and this
        # candidate's turn in a long candidate list.
        (wt / "late.txt").write_text("real work\n")
        _git(["add", "."], wt)
        _git(["commit", "-q", "-m", "late real work"], wt)

        results = self._sweep(dry_run=False, candidates=stale_candidates)
        self.assertEqual(len(results), 1)
        # The worktree directory itself is gone (git worktree remove only
        # refuses on UNCOMMITTED changes -- a committed extra commit is not
        # "dirty").
        self.assertFalse(wt.exists())
        self.assertTrue(results[0]["removed"])
        # But the branch -- carrying real work now -- must survive.
        self.assertFalse(results[0]["branch_deleted"],
                         "a branch that gained real commits must never be deleted")
        branches = _git(["branch", "--list", "worktree-agent-racer"], repo)
        self.assertIn("worktree-agent-racer", branches)
        log = _git(["log", "--oneline", "worktree-agent-racer"], repo)
        self.assertIn("late real work", log)

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


# ---------------------------------------------------------------------------
# Adversarial-review findings (dispatched fable review, #345) -- each fixed
# with its own dedicated, real (unmocked git) regression test.
# ---------------------------------------------------------------------------

class TestReviewMajor1_DryRunCliReporting(unittest.TestCase):
    """MAJOR-1: `sweep_stale_worktrees(dry_run=True)` leaves `removed=False`
    on every candidate (correct -- dry-run must never claim it removed
    anything) -- but `cmd_sweep_worktrees` keyed its per-row tag AND its
    final count on that SAME `removed` field, so a real `--dry-run`
    invocation printed every genuine candidate as `skip` and reported
    "0 worktree(s) would be removed" -- the exact opposite of what an
    operator previewing a destructive sweep needs. This test mocks
    `sweep_stale_worktrees` with the EXACT shape it really produces for a
    dry-run candidate (unlike the two pre-existing CLI tests, which both
    mock `removed: True` regardless of dry_run and so never exercised
    this real mismatch at all)."""

    def test_dry_run_cli_counts_and_labels_the_real_candidate_shape(self):
        ns = SimpleNamespace(dry_run=True)
        with m.patch.object(airuleset, "sweep_stale_worktrees") as p:
            # This is sweep_stale_worktrees's OWN real dry-run output shape:
            # removed stays False (nothing was actually deleted).
            p.return_value = [{"path": "/x/wt", "branch": "worktree-agent-x",
                               "repo": "/x", "removed": False,
                               "reason": "would remove (dry-run)"}]
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.cmd_sweep_worktrees(ns)
        text = out.getvalue()
        self.assertIn("WOULD REMOVE", text)
        self.assertNotIn("skip: /x/wt", text)
        self.assertRegex(text, r"1 worktree\(s\) would be removed\.")


class TestReviewMajor2_AmbiguousRefResolution(unittest.TestCase):
    """MAJOR-2 (data loss, confirmed): every `git rev-list --count
    base..branch` comparison used bare short names -- if a TAG shares a
    worktree branch's name, `git` silently resolves the short name to the
    tag (refs/tags/ before refs/heads/ in gitrevisions ref-resolution
    order), with only a stderr warning and rc 0. A branch carrying real,
    unmerged commits was then read as "0 ahead" and deleted outright."""

    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_tag_sharing_the_branch_name_never_causes_deletion_of_real_work(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "ticket-999", commits=2)
        # A tag with the SAME name as the worktree's branch -- git resolves
        # the bare short name to the tag ahead of the branch.
        _git(["tag", "ticket-999", "main"], repo)

        candidates = airuleset.discover_stale_worktrees(home=self.root)
        row = next((c for c in candidates if c["branch"] == "ticket-999"), None)
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"],
                             "a branch carrying real commits must NEVER be a "
                             "candidate, even when a same-named tag exists")

        airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True,
            log_path=self.root / "log", state_path=self.root / "state")
        branches = _git(["branch", "--list", "ticket-999"], repo)
        self.assertIn("ticket-999", branches, "the real-work branch must survive")
        # `ticket-999` alone is itself AMBIGUOUS (the same tag/branch name
        # collision under test) -- read the BRANCH back via a fully
        # qualified ref, exactly like the fix under test now does, so this
        # assertion is not the same ambiguity bug in disguise.
        log_out = _git(["log", "--oneline", "refs/heads/ticket-999"], repo)
        self.assertIn("work 1", log_out, "its real commits must survive")


class TestReviewTheoretical1_PorcelainNulSafety(unittest.TestCase):
    """THEORETICAL-1: `git worktree list --porcelain` (newline-delimited)
    parsed via `str.splitlines()` is corrupted by a literal newline inside
    a worktree PATH (legal on Linux) -- a phantom entry can then point at
    an unrelated, healthy worktree, which the sweep then removes. `-z`
    (NUL-delimited, git's own documented fix for exactly this) closes it."""

    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_worktree_path_containing_a_newline_never_corrupts_a_sibling(self):
        repo = _mkrepo(self.root, "proj")
        # The victim carries REAL, unmerged commits -- it must NEVER be
        # touched under ANY correct classification. (A victim that is
        # itself a genuine 0-ahead/unlocked candidate would legitimately
        # get removed regardless of any parsing bug, which would prove
        # nothing about the injection.)
        victim = _add_worktree(repo, "worktree-agent-victim", commits=2)
        # A second worktree whose DIRECTORY PATH itself embeds a literal
        # newline followed by "worktree <victim's real path>" -- legal on
        # Linux (a path component may contain any byte but NUL and `/`).
        # Under a naive newline-split parse, the evil worktree's OWN
        # "worktree <path>" LINE splits into a PHANTOM entry whose path is
        # the VICTIM's real path but whose branch/lock fields are actually
        # the EVIL worktree's (0-ahead, unlocked) -- so the sweep schedules
        # the victim's real path for removal using the evil branch's SAFE
        # classification, deleting the victim's real, unmerged commits.
        evil_dir = repo / ".claude" / "worktrees" / ("evil\nworktree " + str(victim))
        evil_dir.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", "evilbranch", str(evil_dir)], repo)

        entries = airuleset._worktree_porcelain_entries(repo, git_run=airuleset._worktree_git)
        paths = [e["path"] for e in entries]
        self.assertIn(str(victim), paths,
                      "the victim worktree's own real path must appear verbatim, "
                      "never replaced by a phantom entry")
        # The victim must never be swept away as a side effect of the evil entry.
        airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True,
            log_path=self.root / "log", state_path=self.root / "state")
        self.assertTrue(victim.exists(),
                        "a newline in another worktree's branch name must never "
                        "cause an unrelated, healthy worktree to be removed")


# ---------------------------------------------------------------------------
# #348 -- pure-unit tests for the small parsing/liveness helpers, no git
# ---------------------------------------------------------------------------

class TestWorktreeLockPidParsing(unittest.TestCase):
    def test_parses_pid_and_start(self):
        self.assertEqual(
            airuleset._worktree_lock_pid("claude agent agent-x (pid 123 start 456)"),
            (123, 456))

    def test_parses_pid_with_no_start(self):
        self.assertEqual(airuleset._worktree_lock_pid("(pid 789)"), (789, None))

    def test_no_match_returns_none_none(self):
        self.assertEqual(airuleset._worktree_lock_pid("some other reason"), (None, None))

    def test_empty_or_none_returns_none_none(self):
        self.assertEqual(airuleset._worktree_lock_pid(""), (None, None))
        self.assertEqual(airuleset._worktree_lock_pid(None), (None, None))


class TestPidIsDead(unittest.TestCase):
    def test_no_such_process_is_positively_dead(self):
        self.assertTrue(airuleset._pid_is_dead(123, 456, stat_reader=lambda p: None))

    def test_alive_matching_start_is_alive(self):
        raw = _fake_proc_stat(123, 456)
        self.assertFalse(airuleset._pid_is_dead(123, 456, stat_reader=lambda p: raw))

    def test_alive_but_different_start_means_pid_reused_original_is_dead(self):
        raw = _fake_proc_stat(123, 999)   # pid 123 is now a DIFFERENT process
        self.assertTrue(airuleset._pid_is_dead(123, 456, stat_reader=lambda p: raw))

    def test_alive_with_no_start_to_check_is_never_guessed_dead(self):
        raw = _fake_proc_stat(123, 456)
        self.assertFalse(airuleset._pid_is_dead(123, None, stat_reader=lambda p: raw))

    def test_malformed_stat_is_undeterminable(self):
        self.assertIsNone(airuleset._pid_is_dead(123, 456, stat_reader=lambda p: "garbage"))

    def test_non_int_pid_is_undeterminable(self):
        self.assertIsNone(airuleset._pid_is_dead(None, 456))
        self.assertIsNone(airuleset._pid_is_dead(-1, 456))


# ---------------------------------------------------------------------------
# #348 -- discover_orphaned_worktree_branches (real repos, no worktree at all)
# ---------------------------------------------------------------------------

class TestDiscoverOrphanedBranches(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _by_branch(self, candidates, branch):
        for c in candidates:
            if c.get("branch") == branch:
                return c
        return None

    def test_no_orphans_when_nothing_but_primary_exists(self):
        _mkrepo(self.root, "proj")
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        self.assertEqual(cands, [])

    def test_orphan_branch_old_and_clean_is_a_genuine_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-orphan-empty")
        _backdate(repo / ".git" / "refs" / "heads" / "old-orphan-empty", NOW, 10 * DAY)
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "old-orphan-empty")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"])
        self.assertEqual(row["kind"], "orphan_branch")
        self.assertEqual(row["path"], "")

    def test_orphan_branch_too_recent_is_never_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "fresh-orphan")
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "fresh-orphan")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])

    def test_orphan_branch_with_commits_ahead_is_never_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-workinprogress", commits=2)
        _backdate(repo / ".git" / "refs" / "heads" / "old-workinprogress", NOW, 10 * DAY)
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "old-workinprogress")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("ahead", row["reason"].lower())

    def test_branch_with_a_registered_worktree_is_never_in_the_orphan_list(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree(repo, "worktree-agent-live")
        _backdate(repo / ".git" / "refs" / "heads" / "worktree-agent-live", NOW, 10 * DAY)
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        self.assertIsNone(self._by_branch(cands, "worktree-agent-live"),
                          "a branch with a live registered worktree must never appear "
                          "in the orphan list, however old its ref looks")

    def test_orphan_dev_branch_is_protected_even_though_unregistered(self):
        repo = _mkrepo(self.root, "proj", base_branch="main")
        _mkbranch(repo, "dev")
        _backdate(repo / ".git" / "refs" / "heads" / "dev", NOW, 10 * DAY)
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "dev")
        self.assertIsNotNone(row)
        self.assertIn("protected", row["reason"].lower())

    def test_packed_ref_orphan_branch_is_never_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-orphan-packed")
        _backdate(repo / ".git" / "refs" / "heads" / "old-orphan-packed", NOW, 10 * DAY)
        _git(["pack-refs", "--all"], repo)
        self.assertFalse((repo / ".git" / "refs" / "heads" / "old-orphan-packed").exists(),
                         "sanity: the loose ref file must genuinely be gone after packing")
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "old-orphan-packed")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])

    def test_toctou_recheck_saves_a_branch_that_gained_commits(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-orphan-racer")
        _backdate(repo / ".git" / "refs" / "heads" / "old-orphan-racer", NOW, 10 * DAY)
        cands = airuleset.discover_orphaned_worktree_branches(home=self.root, now=NOW)
        row = self._by_branch(cands, "old-orphan-racer")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"])
        # Something checks the branch out and commits real work between
        # discovery and the candidate's own turn in sweep.
        tmp_wt = repo / ".claude" / "worktrees" / "racer-tmp"
        tmp_wt.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", str(tmp_wt), "old-orphan-racer"], repo)
        (tmp_wt / "late.txt").write_text("real work\n")
        _git(["add", "."], tmp_wt)
        _git(["commit", "-q", "-m", "late real work"], tmp_wt)
        _git(["worktree", "remove", str(tmp_wt)], repo)

        results = airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True, now=NOW,
            log_path=self.root / "log", state_path=self.root / "state",
            candidates=[row])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["removed"], "the branch must survive -- it gained real work")
        branches = _git(["branch", "--list", "old-orphan-racer"], repo)
        self.assertIn("old-orphan-racer", branches)


# ---------------------------------------------------------------------------
# #348 -- sweep_stale_worktrees mutation for orphan branches
# ---------------------------------------------------------------------------

class TestSweepOrphanBranches(unittest.TestCase):
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

    def test_genuine_orphan_branch_is_deleted_end_to_end(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-orphan")
        _backdate(repo / ".git" / "refs" / "heads" / "old-orphan", NOW, 10 * DAY)
        results = self._sweep(dry_run=False)
        row = next((r for r in results if r.get("branch") == "old-orphan"), None)
        self.assertIsNotNone(row)
        self.assertTrue(row["removed"])
        branches = _git(["branch", "--list", "old-orphan"], repo)
        self.assertEqual(branches.strip(), "")

    def test_dry_run_never_deletes_an_orphan_branch(self):
        repo = _mkrepo(self.root, "proj")
        _mkbranch(repo, "old-orphan")
        _backdate(repo / ".git" / "refs" / "heads" / "old-orphan", NOW, 10 * DAY)
        self._sweep(dry_run=True)
        branches = _git(["branch", "--list", "old-orphan"], repo)
        self.assertIn("old-orphan", branches)


# ---------------------------------------------------------------------------
# #348 -- locked worktree, dead-session reclamation
# ---------------------------------------------------------------------------

class TestLockedDeadSession(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _by_branch(self, candidates, branch):
        for c in candidates:
            if c.get("branch") == branch:
                return c
        return None

    def test_dead_pid_old_clean_zero_ahead_is_a_candidate(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree_with_reason(repo, "worktree-agent-longdead",
                                       "claude agent agent-x (pid 424242 start 1)")
        admin = airuleset._worktree_admin_dir(repo, wt)
        self.assertIsNotNone(admin)
        _backdate(admin / "locked", NOW, 10 * DAY)
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: True)
        row = self._by_branch(cands, "worktree-agent-longdead")
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"])
        self.assertEqual(row["kind"], "locked_dead")

    def test_alive_pid_is_never_touched(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree_with_reason(repo, "worktree-agent-alive",
                                  "claude agent agent-x (pid 1 start 1)")
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: False)
        row = self._by_branch(cands, "worktree-agent-alive")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("active worker", row["reason"].lower())

    def test_undeterminable_liveness_is_never_touched(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree_with_reason(repo, "worktree-agent-unknownlive",
                                  "claude agent agent-x (pid 1 start 1)")
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: None)
        row = self._by_branch(cands, "worktree-agent-unknownlive")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("undeterminable", row["reason"].lower())

    def test_unparseable_lock_reason_never_guessed(self):
        repo = _mkrepo(self.root, "proj")
        # A manual `git worktree lock` with NO --reason -- mirrors the
        # already-existing pre-#348 test's own fixture shape exactly.
        _add_worktree(repo, "worktree-agent-manuallock", locked=True)
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: True)
        row = self._by_branch(cands, "worktree-agent-manuallock")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("no parseable session pid", row["reason"].lower())

    def test_dead_but_lock_too_recent_is_never_touched(self):
        repo = _mkrepo(self.root, "proj")
        _add_worktree_with_reason(repo, "worktree-agent-recentdead",
                                  "claude agent agent-x (pid 424242 start 1)")
        # No backdating -- the lock marker is fresh.
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: True)
        row = self._by_branch(cands, "worktree-agent-recentdead")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("recent", row["reason"].lower())

    def test_dead_old_but_has_unmerged_work_is_never_touched(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree_with_reason(repo, "worktree-agent-deadwork",
                                       "claude agent agent-x (pid 424242 start 1)",
                                       commits=2)
        admin = airuleset._worktree_admin_dir(repo, wt)
        _backdate(admin / "locked", NOW, 10 * DAY)
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: True)
        row = self._by_branch(cands, "worktree-agent-deadwork")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("unmerged work", row["reason"].lower())

    def test_dead_old_but_dirty_tree_is_never_touched(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree_with_reason(repo, "worktree-agent-deaddirty",
                                       "claude agent agent-x (pid 424242 start 1)",
                                       dirty=True)
        admin = airuleset._worktree_admin_dir(repo, wt)
        _backdate(admin / "locked", NOW, 10 * DAY)
        cands = airuleset.discover_stale_worktrees(
            home=self.root, now=NOW, pid_is_dead=lambda pid, start: True)
        row = self._by_branch(cands, "worktree-agent-deaddirty")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertIn("not provably clean", row["reason"].lower())
        self.assertTrue((wt / "dirty.txt").exists(), "the dirty file must survive intact")

    def test_sweep_unlocks_removes_and_deletes_branch_end_to_end(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree_with_reason(repo, "worktree-agent-reclaim",
                                       "claude agent agent-x (pid 424242 start 1)")
        admin = airuleset._worktree_admin_dir(repo, wt)
        _backdate(admin / "locked", NOW, 10 * DAY)
        results = airuleset.sweep_stale_worktrees(
            home=self.root, dry_run=False, force=True, now=NOW,
            log_path=self.root / "log", state_path=self.root / "state",
            pid_is_dead=lambda pid, start: True)
        row = next((r for r in results if r.get("branch") == "worktree-agent-reclaim"), None)
        self.assertIsNotNone(row)
        self.assertTrue(row["removed"])
        self.assertFalse(wt.exists())
        branches = _git(["branch", "--list", "worktree-agent-reclaim"], repo)
        self.assertEqual(branches.strip(), "")

    def test_the_existing_pre_348_locked_test_still_never_becomes_a_candidate(self):
        """Regression guard: `test_locked_worktree_is_never_a_candidate`
        (pre-#348) must keep excluding a plainly-locked, no-reason
        worktree even with the REAL (non-injected) `_pid_is_dead` -- the
        unparseable-reason refusal must fire before any liveness check is
        even attempted."""
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-live2", locked=True)
        cands = airuleset.discover_stale_worktrees(home=self.root, now=NOW)
        row = self._by_branch(cands, "worktree-agent-live2")
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["reason"])
        self.assertTrue(wt.exists())


class TestWorktreeAdminDirResolution(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_resolves_the_real_admin_dir_for_a_registered_worktree(self):
        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree(repo, "worktree-agent-x")
        admin = airuleset._worktree_admin_dir(repo, wt)
        self.assertIsNotNone(admin)
        self.assertTrue((admin / "gitdir").exists())
        self.assertTrue((admin / "HEAD").exists())

    def test_unknown_path_resolves_to_none(self):
        repo = _mkrepo(self.root, "proj")
        admin = airuleset._worktree_admin_dir(repo, self.root / "not-a-real-worktree")
        self.assertIsNone(admin)


# ---------------------------------------------------------------------------
# #348 -- the ONE test that does NOT inject `pid_is_dead`: proves the real
# /proc/<pid>/stat mechanism against a genuinely spawned-and-exited process.
# ---------------------------------------------------------------------------

class TestRealProcessDeathDetection(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-worktree-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_genuinely_exited_process_is_positively_confirmed_dead(self):
        proc = subprocess.Popen(["sleep", "0.05"])
        stat_before = Path("/proc/%d/stat" % proc.pid).read_text()
        after_comm = stat_before.rsplit(")", 1)[1]
        start = int(after_comm.split()[19])
        proc.wait(timeout=5)
        import time as _t
        for _ in range(50):
            if not Path("/proc/%d" % proc.pid).exists():
                break
            _t.sleep(0.05)
        self.assertFalse(Path("/proc/%d" % proc.pid).exists(),
                         "sanity: the exited process's /proc entry must be gone")

        repo = _mkrepo(self.root, "proj")
        wt = _add_worktree_with_reason(
            repo, "worktree-agent-realdead",
            "claude agent agent-x (pid %d start %d)" % (proc.pid, start))
        admin = airuleset._worktree_admin_dir(repo, wt)
        _backdate(admin / "locked", NOW, 10 * DAY)

        # No `pid_is_dead=` injection here -- exercises the REAL /proc reader.
        cands = airuleset.discover_stale_worktrees(home=self.root, now=NOW)
        row = next((c for c in cands if c.get("branch") == "worktree-agent-realdead"), None)
        self.assertIsNotNone(row)
        self.assertIsNone(row["reason"],
                          "a genuinely exited process's OWN pid+starttime must be "
                          "positively confirmed dead by the real /proc reader")


if __name__ == "__main__":
    unittest.main()
