"""#468 — user-waiting tickets (`needs-answer`/`needs-decision`) leave the
workable obligation set (footer `I N` + the /goal stop-proof) and surface as a
SEPARATE `U N` bucket, so `I` = only what THIS box must action and `U` = what is
parked on the USER's answer.

The consistency requirement (#367 lesson — ONE definition, no parallel
derivation): the workable count and the user-waiting count are derived from the
SAME already-fetched rows by `_partition_user_waiting`, never from two
independent gh queries that could drift. These tests lock:

  1. the pure partition helpers (`_row_is_user_waiting`, `_partition_user_waiting`)
  2. `core-quals --count` counts WORKABLE only (user-waiting excluded)
  3. `core-quals --waiting` LISTS the user-waiting remainder
  4. `core-quals --list` stays WORKABLE-only, so `--count == len(--list)`
  5. the reduced-authority `slice-quals` mirror
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


def _labels(*names):
    return [{"name": n} for n in names]


class PartitionHelpers(unittest.TestCase):
    """`_row_is_user_waiting` / `_partition_user_waiting` — pure Python, no gh.
    A row whose labels cannot be read falls to WORKABLE (the safe side: never
    hide a ticket from THIS box's responsibility because of an unreadable
    label — the mirror of `_ticket_is_stream_labeled(None) is False`)."""

    def test_needs_answer_is_user_waiting(self):
        self.assertTrue(airuleset._row_is_user_waiting(_labels("needs-answer")))

    def test_needs_decision_is_user_waiting(self):
        self.assertTrue(airuleset._row_is_user_waiting(_labels("needs-decision")))

    def test_an_unrelated_label_is_not_user_waiting(self):
        self.assertFalse(airuleset._row_is_user_waiting(_labels("bug", "needs-design")))

    def test_missing_or_malformed_labels_are_not_user_waiting(self):
        # None (no labels key), an empty list, a bare-string label, an explicit
        # null entry — all read as NOT user-waiting (workable), never crash.
        self.assertFalse(airuleset._row_is_user_waiting(None))
        self.assertFalse(airuleset._row_is_user_waiting([]))
        self.assertFalse(airuleset._row_is_user_waiting(["needs-answer"]))
        self.assertFalse(airuleset._row_is_user_waiting([None]))

    def test_partition_splits_workable_from_waiting_by_label(self):
        rows = {
            1: {"number": 1, "labels": _labels("bug")},
            2: {"number": 2, "labels": _labels("needs-answer")},
            3: {"number": 3, "labels": _labels("needs-decision")},
            4: {"number": 4, "labels": []},
        }
        workable, waiting = airuleset._partition_user_waiting(rows)
        self.assertEqual(set(workable), {1, 4})
        self.assertEqual(set(waiting), {2, 3})

    def test_partition_row_with_no_labels_key_is_workable(self):
        # A failed label lookup (no "labels" key) must NOT hide the ticket.
        rows = {9: {"number": 9}}
        workable, waiting = airuleset._partition_user_waiting(rows)
        self.assertEqual(set(workable), {9})
        self.assertEqual(waiting, {})

    def test_labels_constant_is_exactly_the_two_user_waiting_labels(self):
        self.assertEqual(set(airuleset.USER_WAITING_LABELS),
                         {"needs-answer", "needs-decision"})


def _run_quals(subcmd, flag, repo, home, bindir, marker=None):
    if marker is not None:
        Path(repo, "CLAUDE.md").write_text(marker + "\n")
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True,
        env={**os.environ, "HOME": home, "PATH": f"{bindir}:{os.environ['PATH']}"})


# Five obligation rows: #1/#2/#3 workable, #4 needs-answer, #5 needs-decision.
_CORE_FIVE = json.dumps([
    {"number": 1, "labels": _labels("bug")},
    {"number": 2, "labels": _labels("enhancement")},
    {"number": 3, "labels": []},
    {"number": 4, "labels": _labels("needs-answer")},
    {"number": 5, "labels": _labels("needs-decision")},
])


class CoreQualsExcludesUserWaiting(unittest.TestCase):
    """Full-authority /goal stop-proof: `core-quals --count` counts WORKABLE
    only, `--waiting` lists the parked remainder, `--list` stays workable-only."""

    def _fake_gh(self, bindir):
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % _CORE_FIVE +
            'esac\n')
        gh.chmod(0o755)

    def test_count_excludes_user_waiting(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--count", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "3",
                             "core-quals --count must exclude the 2 user-waiting "
                             "tickets (#4 needs-answer, #5 needs-decision)")

    def test_waiting_lists_the_parked_remainder(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"4", "5"},
                             "core-quals --waiting must list ONLY the user-waiting tickets")

    def test_list_is_workable_only_matching_count(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--list", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"1", "2", "3"},
                             "core-quals --list must be workable-only, so "
                             "len(--list) == --count")


class SliceQualsExcludesUserWaiting(unittest.TestCase):
    """Reduced-authority stop-proof mirror: `slice-quals --count` excludes both
    handed-off AND user-waiting; `--waiting` lists the parked remainder."""

    def _fake_gh(self, bindir):
        # own-account 3-qual slice (assignee ∪ author ∪ stream), same fixture
        # shape as test_statusbar's reduced tests. `api user` hits the
        # non-maintainer catch-all, so `_slice_quals` takes the 3-qual branch.
        gh = Path(bindir) / "gh"
        A = json.dumps([{"number": 1, "labels": _labels("bug")},
                        {"number": 4, "labels": _labels("needs-answer")}])
        B = json.dumps([{"number": 2, "labels": _labels("needs-decision")}])
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "kvaskodev/odoo-erp";;\n'
            '  */comments*) echo "[]";;\n'
            '  *assignee:@me*) echo \'%s\';;\n' % A +
            '  *author:@me*)   echo \'%s\';;\n' % B +
            '  *label:stream:*) echo "[]";;\n'
            '  *) echo "kvaskodev";;\n'
            'esac\n')
        gh.chmod(0o755)

    def test_count_excludes_user_waiting(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("slice-quals", "--count", repo, home, bindir,
                           marker="<!-- airuleset:authority=fork-no-merge -->")
            self.assertEqual(r.returncode, 0, r.stderr)
            # slice = {1,4} ∪ {2} = 3; #4 needs-answer + #2 needs-decision are
            # user-waiting → workable unhandled = {1} = 1.
            self.assertEqual(r.stdout.strip(), "1",
                             "slice-quals --count must exclude the 2 user-waiting tickets")

    def test_waiting_lists_the_parked_remainder(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("slice-quals", "--waiting", repo, home, bindir,
                           marker="<!-- airuleset:authority=fork-no-merge -->")
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"2", "4"},
                             "slice-quals --waiting must list ONLY the user-waiting tickets")


if __name__ == "__main__":
    unittest.main()
