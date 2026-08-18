"""Merge-driver tests for the bookkeeping-conflict mechanization (#553).

Fleet integration conflicts are STRUCTURAL: every lane writes the same three
shared bookkeeping files, so two disjoint code lanes still collide on
`tests/size_ratchet.json` (per-key ceiling bumps), `internals-archive.md`
(both append at EOF) and `internals-<area>.md` (append + rotation). This
suite locks the two DETERMINISTIC halves:

* `scripts/ratchet_union_merge.py` — a custom 3-way, base-aware, per-key
  union-max git merge driver for `size_ratchet.json`.
* the git BUILT-IN `merge=union` for the append-only archive.

Two test layers:

1. Pure-logic unit tests on ``ratchet_union_merge.merge_snapshots`` — the
   3-way rule (take-the-changed-side, both-changed→max, delete/modify→
   conflict), fast, no git.
2. Real-git integration tests — a per-test throwaway repo (``git init`` under
   ``tempfile.TemporaryDirectory`` so NOTHING litters /tmp, even on failure),
   driver configured, branches diverged and merged. Proves the driver makes
   the real conflict shape merge CLEAN, that WITHOUT it the same shape still
   conflicts (RED baseline), and that a driver failure falls back to a normal
   git conflict (never a silent wrong merge).

Plus a wiring test that ``airuleset._configure_ratchet_merge_driver`` writes
the expected repo-local git config.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "ratchet_union_merge.py"

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))

import ratchet_union_merge as rum  # noqa: E402  (module under test)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check,
    )


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "arch").mkdir(parents=True)
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _configure_driver(repo: Path) -> None:
    """Point the throwaway repo's ratchet-union driver at the script under
    test (never the fleet-installed one), plus the versioned .gitattributes."""
    _git(repo, "config", "merge.ratchet-union.name", "ratchet union-max (test)")
    _git(repo, "config", "merge.ratchet-union.driver",
         f'python3 "{SCRIPT}" %O %A %B %P')
    (repo / ".gitattributes").write_text(
        "tests/size_ratchet.json merge=ratchet-union\n"
        "arch/internals-archive.md merge=union\n",
        encoding="utf-8",
    )


def _write_snapshot(repo: Path, data: dict) -> None:
    (repo / "tests" / "size_ratchet.json").write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_snapshot(repo: Path) -> dict:
    return json.loads((repo / "tests" / "size_ratchet.json").read_text())


def _default_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


# --------------------------------------------------------------------------- #
# 1. pure-logic unit tests: merge_snapshots (3-way, base-aware)
# --------------------------------------------------------------------------- #

class MergeSnapshotLogic(unittest.TestCase):

    def test_both_sides_bump_same_key_takes_max(self):
        base = {"files": {"airuleset.py": 5150}}
        ours = {"files": {"airuleset.py": 5175}}
        theirs = {"files": {"airuleset.py": 5162}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"files": {"airuleset.py": 5175}})

    def test_disjoint_new_keys_both_preserved(self):
        base = {"files": {"a.py": 10}}
        ours = {"files": {"a.py": 10, "b.py": 20}}
        theirs = {"files": {"a.py": 10, "c.py": 30}}
        self.assertEqual(
            rum.merge_snapshots(base, ours, theirs),
            {"files": {"a.py": 10, "b.py": 20, "c.py": 30}})

    def test_new_key_one_side_preserved(self):
        base = {"files": {}}
        ours = {"files": {"new.py": 100}}
        theirs = {"files": {}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"files": {"new.py": 100}})

    def test_new_key_both_sides_different_values_max(self):
        base = {"files": {}}
        ours = {"files": {"new.py": 100}}
        theirs = {"files": {"new.py": 120}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"files": {"new.py": 120}})

    def test_lowering_on_one_side_not_resurrected(self):
        # A file split lowers airuleset.py's ceiling on theirs; ours untouched.
        # 3-way MUST take the lowering, never resurrect the pre-split ceiling
        # (the batch-19 failure a naive 2-way union-max would cause).
        base = {"files": {"airuleset.py": 11408}}
        ours = {"files": {"airuleset.py": 11408}}
        theirs = {"files": {"airuleset.py": 9712}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"files": {"airuleset.py": 9712}})

    def test_deletion_on_one_side_unchanged_other_deletes(self):
        # theirs removed a key (a split pruned an old function); ours untouched.
        # The deletion is the intentional change -> it wins, NOT resurrection.
        base = {"functions": {"a::f": 50, "a::g": 60}}
        ours = {"functions": {"a::f": 50, "a::g": 60}}
        theirs = {"functions": {"a::f": 50}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"functions": {"a::f": 50}})

    def test_delete_vs_modify_conflicts(self):
        # ours deletes a key that theirs re-tuned -> a genuine semantic
        # conflict the driver must SIGNAL, never silently pick a side.
        base = {"files": {"a.py": 100}}
        ours = {"files": {}}
        theirs = {"files": {"a.py": 120}}
        with self.assertRaises(rum.MergeConflict):
            rum.merge_snapshots(base, ours, theirs)

    def test_identical_change_both_sides_no_conflict(self):
        base = {"files": {"a.py": 100}}
        ours = {"files": {"a.py": 130}}
        theirs = {"files": {"a.py": 130}}
        self.assertEqual(rum.merge_snapshots(base, ours, theirs),
                         {"files": {"a.py": 130}})

    def test_nested_sections_merge_independently(self):
        base = {"files": {"a.py": 100}, "functions": {"a::f": 50},
                "rule_bytes": {"r.md": 40000}}
        ours = {"files": {"a.py": 110}, "functions": {"a::f": 50},
                "rule_bytes": {"r.md": 40000}}
        theirs = {"files": {"a.py": 100}, "functions": {"a::f": 70},
                  "rule_bytes": {"r.md": 45000}}
        self.assertEqual(
            rum.merge_snapshots(base, ours, theirs),
            {"files": {"a.py": 110}, "functions": {"a::f": 70},
             "rule_bytes": {"r.md": 45000}})

    def test_extra_top_level_key_preserved(self):
        # A future 4th section must survive the merge (never silently dropped).
        base = {"files": {}, "future": {"x": 1}}
        ours = {"files": {"a.py": 5}, "future": {"x": 1}}
        theirs = {"files": {}, "future": {"x": 1}}
        merged = rum.merge_snapshots(base, ours, theirs)
        self.assertEqual(merged["future"], {"x": 1})


# --------------------------------------------------------------------------- #
# 2. real-git integration tests
# --------------------------------------------------------------------------- #

class RatchetDriverIntegration(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mergedrv553_")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()  # removes the whole per-test tree, even on failure

    def _base_repo(self, with_driver: bool) -> Path:
        repo = _init_repo(self.root)
        if with_driver:
            _configure_driver(repo)
        _write_snapshot(repo, {"files": {"airuleset.py": 5150,
                                         "notify/__init__.py": 2404}})
        (repo / "arch" / "internals-archive.md").write_text(
            "ARCHIVE HEADER\n\n- old lesson A\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
        return repo

    def _diverge_and_merge(self, repo: Path):
        """Two lanes off base: B1 bumps airuleset.py 5150->5175 + appends the
        archive; B2 bumps 5150->5162 + appends a different archive line. Then
        serially merge B2 into B1 (the real integration order)."""
        main = _default_branch(repo)
        _git(repo, "checkout", "-q", "-b", "laneB1")
        _write_snapshot(repo, {"files": {"airuleset.py": 5175,
                                        "notify/__init__.py": 2404}})
        with (repo / "arch" / "internals-archive.md").open("a", encoding="utf-8") as f:
            f.write("- lesson from lane B1\n")
        _git(repo, "commit", "-qam", "laneB1")

        _git(repo, "checkout", "-q", main)
        _git(repo, "checkout", "-q", "-b", "laneB2")
        _write_snapshot(repo, {"files": {"airuleset.py": 5162,
                                        "notify/__init__.py": 2404}})
        with (repo / "arch" / "internals-archive.md").open("a", encoding="utf-8") as f:
            f.write("- lesson from lane B2\n")
        _git(repo, "commit", "-qam", "laneB2")

        _git(repo, "checkout", "-q", "laneB1")
        return _git(repo, "merge", "--no-edit", "laneB2", check=False)

    def test_without_driver_conflicts_RED(self):
        # Baseline proof the mechanism is load-bearing: the SAME shape still
        # conflicts on both bookkeeping files with no driver configured.
        repo = self._base_repo(with_driver=False)
        res = self._diverge_and_merge(repo)
        self.assertNotEqual(res.returncode, 0, "expected a conflict without driver")
        conflicted = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout
        self.assertIn("tests/size_ratchet.json", conflicted)
        self.assertIn("arch/internals-archive.md", conflicted)

    def test_ratchet_and_archive_merge_clean_with_drivers(self):
        # Today's real conflict shape -> BOTH files merge clean with drivers.
        repo = self._base_repo(with_driver=True)
        res = self._diverge_and_merge(repo)
        self.assertEqual(res.returncode, 0,
                         f"expected a clean merge, got:\n{res.stdout}\n{res.stderr}")
        conflicted = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.strip()
        self.assertEqual(conflicted, "", f"unexpected conflicts: {conflicted}")
        # ratchet: per-key union-max (max(5175, 5162) == 5175)
        self.assertEqual(_read_snapshot(repo)["files"]["airuleset.py"], 5175)
        self.assertEqual(_read_snapshot(repo)["files"]["notify/__init__.py"], 2404)
        # archive: union kept BOTH appended lessons (append-only, nothing lost)
        arch = (repo / "arch" / "internals-archive.md").read_text()
        self.assertIn("- lesson from lane B1", arch)
        self.assertIn("- lesson from lane B2", arch)
        self.assertIn("- old lesson A", arch)
        # and the merged json is byte-shaped like save_snapshot output
        raw = (repo / "tests" / "size_ratchet.json").read_text()
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(json.dumps(json.loads(raw), indent=2, sort_keys=True) + "\n", raw)

    def test_disjoint_key_bumps_merge_clean(self):
        # Two lanes each grow a DIFFERENT file -> both ceilings preserved.
        repo = self._base_repo(with_driver=True)
        main = _default_branch(repo)
        _git(repo, "checkout", "-q", "-b", "laneX")
        _write_snapshot(repo, {"files": {"airuleset.py": 5170,
                                        "notify/__init__.py": 2404}})
        _git(repo, "commit", "-qam", "laneX")
        _git(repo, "checkout", "-q", main)
        _git(repo, "checkout", "-q", "-b", "laneY")
        _write_snapshot(repo, {"files": {"airuleset.py": 5150,
                                        "notify/__init__.py": 2450}})
        _git(repo, "commit", "-qam", "laneY")
        _git(repo, "checkout", "-q", "laneX")
        res = _git(repo, "merge", "--no-edit", "laneY", check=False)
        self.assertEqual(res.returncode, 0, f"{res.stdout}\n{res.stderr}")
        snap = _read_snapshot(repo)["files"]
        self.assertEqual(snap["airuleset.py"], 5170)
        self.assertEqual(snap["notify/__init__.py"], 2450)


class DriverFailSafe(unittest.TestCase):
    """A driver failure MUST degrade to a normal git conflict (non-zero exit),
    never a silent wrong merge."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mergedrv553fs_")
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_driver(self, base: str, ours: str, theirs: str):
        o = self.d / "O"
        a = self.d / "A"
        b = self.d / "B"
        o.write_text(base)
        a.write_text(ours)
        b.write_text(theirs)
        proc = subprocess.run(
            ["python3", str(SCRIPT), str(o), str(a), str(b), "tests/size_ratchet.json"],
            capture_output=True, text=True)
        return proc, a

    def test_corrupt_json_falls_back_to_conflict(self):
        # theirs is not valid JSON -> driver cannot union -> must exit non-zero.
        proc, a = self._run_driver(
            base='{"files": {"a.py": 1}}\n',
            ours='{"files": {"a.py": 2}}\n',
            theirs='{ this is not json',
        )
        self.assertNotEqual(proc.returncode, 0,
                            "corrupt input must NOT be reported as a clean merge")

    def test_clean_inputs_exit_zero_and_write_merge(self):
        proc, a = self._run_driver(
            base='{"files": {"a.py": 1}}\n',
            ours='{"files": {"a.py": 2}}\n',
            theirs='{"files": {"a.py": 3}}\n',
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(a.read_text())["files"]["a.py"], 3)


# --------------------------------------------------------------------------- #
# 3. install wiring
# --------------------------------------------------------------------------- #

class InstallWiring(unittest.TestCase):

    def test_configure_ratchet_merge_driver_writes_config(self):
        import airuleset
        with tempfile.TemporaryDirectory(prefix="mergedrv553cfg_") as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q", str(repo)],
                           check=True)
            value = airuleset._configure_ratchet_merge_driver(repo_dir=repo)
            self.assertIsNotNone(value, "should configure inside a git repo")
            got = subprocess.run(
                ["git", "-C", str(repo), "config", "--get", "merge.ratchet-union.driver"],
                capture_output=True, text=True).stdout.strip()
            self.assertIn("ratchet_union_merge.py", got)
            self.assertIn("%O", got)
            self.assertIn("%A", got)
            self.assertIn("%B", got)

    def test_configure_returns_none_outside_git_repo(self):
        import airuleset
        with tempfile.TemporaryDirectory(prefix="mergedrv553nogit_") as td:
            self.assertIsNone(
                airuleset._configure_ratchet_merge_driver(repo_dir=Path(td)))


if __name__ == "__main__":
    unittest.main()
