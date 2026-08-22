"""Push-gate mid-run tree-mutation detection (#629).

Root cause (settled in the ticket, not re-litigated here): `cmd_push` runs the
whole suite as `unittest discover` against the SHARED main checkout. Four wiring
tests assert on `inspect.getsource(airuleset.<fn>)`, which is correct ONLY while
`airuleset.py` on disk stays byte-identical to the version the process imported.
When a concurrent integration merges the NEXT lane into the shared checkout
WHILE the gate's suite is still running, the file changes size mid-run,
`getsource` slices the NEW source at the OLD `co_firstlineno`, and four tests
fail with a wrong-non-empty-content signature that reads EXACTLY like a code
regression — costing a full diagnostic cycle.

The gap this covers: the gate had no idea the tree moved underneath it. This
adds a before/after tracked-tree content fingerprint bracketing the suite
subprocess so the gate REPORTS "tracked files changed on disk during the test
run" (its own named cause) instead of letting it masquerade as a test failure —
never a silent retry, never swallowing a genuine failure. When a real failure
and a mid-run mutation coincide, both stay visible.

Covers four units in `cli_remote`:
  - `_tracked_tree_fingerprint(repo)` — never-raising snapshot of every
    git-tracked file's working-tree content (+ HEAD), keyed by relpath.
  - `_diff_tracked_tree_fingerprints(before, after)` — (moved, changed, available).
  - `_render_tree_moved_report(...)` — the unambiguous VOID report text.
  - `_classify_push_gate_outcome(...)` — the single decision function folding the
    existing test/litter branches + the new tree-moved branch, each with its own
    explicit reason.
  - `cmd_push` wiring (source-lock, #548 pattern) — that it actually calls them
    and keeps the enforcement branch.
"""

import inspect
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_remote  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


def _init_repo(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")


# --------------------------------------------------------------------------- #
# The fingerprint + diff — the actual "tree changed mid-run" reproduction.
# --------------------------------------------------------------------------- #

class TestTrackedTreeFingerprint(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-629-fp-")
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        _init_repo(self.repo)
        (self.repo / "airuleset.py").write_text("def cmd_install():\n    pass\n")
        (self.repo / "b.txt").write_text("hello\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")

    def test_snapshot_shape(self):
        fp = cli_remote._tracked_tree_fingerprint(self.repo)
        self.assertIsNone(fp["error"])
        self.assertIsInstance(fp["files"], dict)
        self.assertIn("airuleset.py", fp["files"])
        self.assertIn("b.txt", fp["files"])
        # each value is a 64-hex sha256 of the working-tree content
        self.assertRegex(fp["files"]["airuleset.py"], r"^[0-9a-f]{64}$")
        # HEAD is captured for the report
        head = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(fp["head"], head)

    def test_unchanged_tree_is_not_moved(self):
        before = cli_remote._tracked_tree_fingerprint(self.repo)
        after = cli_remote._tracked_tree_fingerprint(self.repo)
        moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(before, after)
        self.assertTrue(available)
        self.assertFalse(moved)
        self.assertEqual(changed, [])

    def test_mid_run_content_change_is_detected_and_named(self):
        """THE reproduction: a tracked file's content changes between the two
        snapshots (a concurrent merge landing mid-run) → moved, and the changed
        file is named."""
        before = cli_remote._tracked_tree_fingerprint(self.repo)
        # simulate the mid-run merge: airuleset.py grows a line, shifting
        # every function's co_firstlineno — exactly the #629 trigger.
        (self.repo / "airuleset.py").write_text(
            "import os  # a new top line\ndef cmd_install():\n    pass\n")
        after = cli_remote._tracked_tree_fingerprint(self.repo)
        moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(before, after)
        self.assertTrue(available)
        self.assertTrue(moved, "a mid-run content change must be detected")
        self.assertIn("airuleset.py", changed)

    def test_added_tracked_file_is_detected(self):
        before = cli_remote._tracked_tree_fingerprint(self.repo)
        (self.repo / "c.txt").write_text("new\n")
        _git(self.repo, "add", "c.txt")
        after = cli_remote._tracked_tree_fingerprint(self.repo)
        moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(before, after)
        self.assertTrue(moved)
        self.assertIn("c.txt", changed)

    def test_removed_tracked_file_is_detected(self):
        before = cli_remote._tracked_tree_fingerprint(self.repo)
        _git(self.repo, "rm", "-q", "b.txt")
        after = cli_remote._tracked_tree_fingerprint(self.repo)
        moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(before, after)
        self.assertTrue(moved)
        self.assertIn("b.txt", changed)

    def test_untracked_file_never_false_triggers(self):
        """An UNtracked file created during the run (ordinary test litter, or a
        concurrent worker's gitignored worktree) must NOT trip detection — only
        tracked-file mutation (a supervisor merge/checkout) does."""
        before = cli_remote._tracked_tree_fingerprint(self.repo)
        (self.repo / "scratch-litter.tmp").write_text("noise\n")  # never git add'd
        after = cli_remote._tracked_tree_fingerprint(self.repo)
        moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(before, after)
        self.assertTrue(available)
        self.assertFalse(moved, "an untracked file must never be a mid-run mutation")

    def test_non_repo_is_unavailable_never_raises(self):
        with TemporaryDirectory() as plain:
            fp = cli_remote._tracked_tree_fingerprint(Path(plain))
            self.assertIsNone(fp["files"])
            self.assertTrue(fp["error"])
            moved, changed, available = cli_remote._diff_tracked_tree_fingerprints(fp, fp)
            self.assertFalse(available, "an unavailable snapshot disables detection")
            self.assertFalse(moved)


# --------------------------------------------------------------------------- #
# The report — must be unambiguous.
# --------------------------------------------------------------------------- #

class TestTreeMovedReport(unittest.TestCase):
    def _fp(self, head, files):
        return {"head": head, "files": files, "error": None}

    def test_report_is_unambiguous(self):
        before = self._fp("aaaaaaa", {"airuleset.py": "h1"})
        after = self._fp("bbbbbbb", {"airuleset.py": "h2"})
        msg = cli_remote._render_tree_moved_report(before, after, ["airuleset.py"], 1)
        low = msg.lower()
        # names its own cause
        self.assertIn("tracked files changed on disk during the test run", low)
        self.assertIn("void", low)
        # HEAD before/after for diagnosis
        self.assertIn("aaaaaaa", msg)
        self.assertIn("bbbbbbb", msg)
        # the changed file is listed
        self.assertIn("airuleset.py", msg)
        # tells the reader NOT to read it as a regression, and how to recover
        self.assertIn("regression", low)
        self.assertIn("settled", low)
        # the suite's own exit code is surfaced, marked void
        self.assertIn("1", msg)

    def test_changed_file_list_is_capped(self):
        many = ["f%03d.py" % i for i in range(50)]
        before = self._fp("a", {p: "h1" for p in many})
        after = self._fp("b", {p: "h2" for p in many})
        msg = cli_remote._render_tree_moved_report(before, after, many, 1)
        # does not dump all 50 lines; states the true total
        self.assertIn("more", msg.lower())
        self.assertIn("50", msg)


# --------------------------------------------------------------------------- #
# The decision function — precedence + every branch logs its own reason.
# --------------------------------------------------------------------------- #

class TestClassifyPushGateOutcome(unittest.TestCase):
    # The classifier owns tree-moved / tests-failed / clean; the TMPDIR litter
    # guard stays its own separate branch in cmd_push (checked AFTER a clean
    # classifier verdict), so the tree-moved precedence beats litter too.
    def _fp(self, files, error=None):
        return {"head": "h", "files": files, "error": error}

    def _stable(self):
        f = self._fp({"airuleset.py": "same"})
        return f, f

    def _moved(self):
        return self._fp({"airuleset.py": "old"}), self._fp({"airuleset.py": "new"})

    def test_clean_run_proceeds(self):
        b, a = self._stable()
        ok, reason, msg = cli_remote._classify_push_gate_outcome(0, b, a)
        self.assertTrue(ok)
        self.assertEqual(reason, "clean")

    def test_tree_moved_voids_even_when_tests_failed(self):
        b, a = self._moved()
        ok, reason, msg = cli_remote._classify_push_gate_outcome(1, b, a)
        self.assertFalse(ok)
        self.assertEqual(reason, "tree-moved",
                         "a mid-run mutation takes precedence over the test result")
        # the raw suite failure must not be swallowed — the report says so
        self.assertIn("void", msg.lower())

    def test_tree_moved_voids_even_a_green_run(self):
        """A pass DURING a mid-run mutation is not trustworthy either — still void."""
        b, a = self._moved()
        ok, reason, msg = cli_remote._classify_push_gate_outcome(0, b, a)
        self.assertFalse(ok)
        self.assertEqual(reason, "tree-moved")

    def test_stable_tree_tests_failed_is_a_real_regression(self):
        b, a = self._stable()
        ok, reason, msg = cli_remote._classify_push_gate_outcome(1, b, a)
        self.assertFalse(ok)
        self.assertEqual(reason, "tests-failed")
        self.assertIn("TESTS FAILED", msg)

    def test_detection_unavailable_never_blocks_a_clean_run(self):
        """git unavailable → detection skipped, NOT a block; the normal result stands."""
        fp = self._fp(None, error="git not available")
        ok, reason, msg = cli_remote._classify_push_gate_outcome(0, fp, fp)
        self.assertTrue(ok)
        self.assertEqual(reason, "clean")

    def test_detection_unavailable_notes_it_when_tests_failed(self):
        """git unavailable + tests failed → still 'tests-failed', but the message
        notes detection could not rule out a mid-run mutation (never swallowed)."""
        fp = self._fp(None, error="git not available")
        ok, reason, msg = cli_remote._classify_push_gate_outcome(1, fp, fp)
        self.assertFalse(ok)
        self.assertEqual(reason, "tests-failed")
        self.assertIn("detection", msg.lower())


# --------------------------------------------------------------------------- #
# cmd_push wiring — source-lock (#548 pattern): the guard fails no test if
# silently deleted otherwise.
# --------------------------------------------------------------------------- #

class TestCmdPushWiring(unittest.TestCase):
    def test_cmd_push_brackets_the_suite_and_wires_the_classifier(self):
        src = inspect.getsource(cli_remote.cmd_push)
        # snapshot bracketing the suite subprocess (before AND after)
        self.assertEqual(src.count("_tracked_tree_fingerprint("), 2,
                         "must snapshot the tracked tree BEFORE and AFTER the suite")
        # the single decision function drives the outcome
        self.assertIn("_classify_push_gate_outcome(", src)
        # the enforcement branch — deleting it would silently stop refusing to push
        self.assertIn("if not _gate_ok", src)
        self.assertIn("sys.exit(1)", src)


if __name__ == "__main__":
    unittest.main()
