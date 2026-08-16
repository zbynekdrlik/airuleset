"""#510 — ops-wait / evidence-gated tickets (`ops-wait` label) leave the workable
obligation set (footer `I N`, the /goal stop-proof `core-quals`/`slice-quals
--count`, and — for free, since it runs those commands — the lane guard) and
surface as a SEPARATE `W N` footer bucket + `core-quals --ops-wait`, mirroring the
#468 `U N` user-waiting bucket EXACTLY. The only difference from the U-bucket is
WHY the ticket is parked (an external event / evidence with no dispatchable code
lane, vs the user's answer).

The consistency requirement (#367/#468 lesson — ONE derivation, no parallel gh
query): the workable count, the user-waiting count AND the ops-wait count are all
derived from the SAME already-fetched rows by `_partition_workable`, never from
independent gh queries that could drift. These tests lock:

  1. the pure partition helpers (`_row_is_ops_wait`, `_partition_workable`)
  2. `core-quals --count` counts WORKABLE only (user-waiting AND ops-wait excluded)
  3. `core-quals --ops-wait` LISTS the ops-wait remainder
  4. `core-quals --waiting` STILL lists ONLY the user-waiting remainder (unchanged)
  5. `core-quals --list` stays WORKABLE-only, so `--count == len(--list)`
  6. the reduced-authority `slice-quals` mirror
  7. the footer: `entry["ops_wait"]` written by --refresh, `· W N` rendered, hidden at 0
"""

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import statusbar


def _labels(*names):
    return [{"name": n} for n in names]


def _seed_cache(home, cwd, open_n=None, name="", ts=None, gk=None, scope=None,
                skipped=None, user_waiting=None, ops_wait=None):
    d = statusbar.cache_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    entry = {"open": open_n, "name": name, "root": str(cwd),
             "ts": int(time.time() if ts is None else ts)}
    for k, v in (("gk", gk), ("scope", scope), ("skipped", skipped),
                 ("user_waiting", user_waiting), ("ops_wait", ops_wait)):
        if v is not None:
            entry[k] = v
    (d / (statusbar.cwd_key(cwd) + ".json")).write_text(json.dumps(entry))


class PartitionHelpers(unittest.TestCase):
    """`_row_is_ops_wait` / `_partition_workable` — pure Python, no gh. Mirror of
    `_row_is_user_waiting` with the same unreadable→workable safe-side convention."""

    def test_ops_wait_label_is_ops_wait(self):
        fn = getattr(airuleset, "_row_is_ops_wait", None)
        self.assertIsNotNone(fn, "_row_is_ops_wait must exist")
        self.assertTrue(fn(_labels("ops-wait")))

    def test_an_unrelated_label_is_not_ops_wait(self):
        fn = getattr(airuleset, "_row_is_ops_wait", None)
        self.assertIsNotNone(fn, "_row_is_ops_wait must exist")
        self.assertFalse(fn(_labels("bug", "needs-answer")))

    def test_missing_or_malformed_labels_are_not_ops_wait(self):
        fn = getattr(airuleset, "_row_is_ops_wait", None)
        self.assertIsNotNone(fn, "_row_is_ops_wait must exist")
        self.assertFalse(fn(None))
        self.assertFalse(fn([]))
        self.assertFalse(fn(["ops-wait"]))   # bare-string entry, not a dict
        self.assertFalse(fn([None]))

    def test_labels_constant_is_exactly_the_ops_wait_label(self):
        self.assertEqual(set(getattr(airuleset, "OPS_WAIT_LABELS", ())),
                         {"ops-wait"})

    def test_partition_splits_three_ways(self):
        part = getattr(airuleset, "_partition_workable", None)
        self.assertIsNotNone(part, "_partition_workable must exist (3-way split)")
        rows = {
            1: {"number": 1, "labels": _labels("bug")},
            2: {"number": 2, "labels": []},
            3: {"number": 3, "labels": _labels("needs-answer")},
            4: {"number": 4, "labels": _labels("needs-decision")},
            5: {"number": 5, "labels": _labels("ops-wait")},
        }
        workable, user_waiting, ops_wait = part(rows)
        self.assertEqual(set(workable), {1, 2})
        self.assertEqual(set(user_waiting), {3, 4})
        self.assertEqual(set(ops_wait), {5})

    def test_partition_user_waiting_wins_when_both_labels_present(self):
        # A ticket carrying BOTH a user-waiting and an ops-wait label goes to
        # user_waiting (both leave workable; only the display bucket is chosen).
        part = getattr(airuleset, "_partition_workable", None)
        self.assertIsNotNone(part, "_partition_workable must exist")
        rows = {7: {"number": 7, "labels": _labels("ops-wait", "needs-answer")}}
        workable, user_waiting, ops_wait = part(rows)
        self.assertEqual(set(workable), set())
        self.assertEqual(set(user_waiting), {7})
        self.assertEqual(set(ops_wait), set())

    def test_partition_row_with_no_labels_key_is_workable(self):
        part = getattr(airuleset, "_partition_workable", None)
        self.assertIsNotNone(part, "_partition_workable must exist")
        workable, user_waiting, ops_wait = part({9: {"number": 9}})
        self.assertEqual(set(workable), {9})
        self.assertEqual(user_waiting, {})
        self.assertEqual(ops_wait, {})


def _run_quals(subcmd, flag, repo, home, bindir, marker=None):
    if marker is not None:
        Path(repo, "CLAUDE.md").write_text(marker + "\n")
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True, cwd=repo,
        env={**os.environ, "HOME": home, "PATH": f"{bindir}:{os.environ['PATH']}"})


# #1/#2 workable, #3 needs-answer (user-waiting), #4/#5 ops-wait.
_CORE_FIVE = json.dumps([
    {"number": 1, "labels": _labels("bug")},
    {"number": 2, "labels": _labels("enhancement")},
    {"number": 3, "labels": _labels("needs-answer")},
    {"number": 4, "labels": _labels("ops-wait")},
    {"number": 5, "labels": _labels("ops-wait")},
])


class CoreQualsExcludesOpsWait(unittest.TestCase):
    """Full-authority /goal stop-proof: `core-quals --count`/`--list` exclude
    ops-wait, `--ops-wait` lists it, `--waiting` still lists ONLY user-waiting."""

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

    def _prep(self, home, repo, bindir):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        self._fake_gh(bindir)

    def test_count_excludes_ops_wait_and_user_waiting(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("core-quals", "--count", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "2",
                             "core-quals --count must exclude the 2 ops-wait (#4,#5) "
                             "AND the 1 user-waiting (#3) ticket → 2 workable")

    def test_ops_wait_lists_only_the_ops_wait_tickets(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("core-quals", "--ops-wait", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"4", "5"},
                             "core-quals --ops-wait must list ONLY the ops-wait tickets")

    def test_waiting_still_lists_only_user_waiting(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"3"},
                             "core-quals --waiting must STILL list ONLY user-waiting (#3), "
                             "never the ops-wait tickets (#468 semantics unchanged)")

    def test_list_is_workable_only_matching_count(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("core-quals", "--list", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"1", "2"},
                             "core-quals --list must be workable-only (exclude ops-wait)")


class SliceQualsExcludesOpsWait(unittest.TestCase):
    """Reduced-authority mirror: `slice-quals --count` excludes handed-off AND
    user-waiting AND ops-wait; `--ops-wait` lists the ops-wait remainder."""

    def _fake_gh(self, bindir):
        gh = Path(bindir) / "gh"
        A = json.dumps([{"number": 1, "labels": _labels("bug")},
                        {"number": 4, "labels": _labels("ops-wait")}])
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

    def _prep(self, home, repo, bindir):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        self._fake_gh(bindir)

    def test_count_excludes_ops_wait(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("slice-quals", "--count", repo, home, bindir,
                           marker="<!-- airuleset:authority=fork-no-merge -->")
            self.assertEqual(r.returncode, 0, r.stderr)
            # slice = {1,4} ∪ {2} = 3; #4 ops-wait + #2 needs-decision leave
            # workable → unhandled workable = {1} = 1.
            self.assertEqual(r.stdout.strip(), "1",
                             "slice-quals --count must exclude ops-wait (#4) + user-waiting (#2)")

    def test_ops_wait_lists_the_ops_wait_remainder(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            self._prep(home, repo, bindir)
            r = _run_quals("slice-quals", "--ops-wait", repo, home, bindir,
                           marker="<!-- airuleset:authority=fork-no-merge -->")
            self.assertEqual(r.returncode, 0, r.stderr)
            nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines() if ln.strip()}
            self.assertEqual(nums, {"4"},
                             "slice-quals --ops-wait must list ONLY the ops-wait ticket (#4)")


class OpsWaitFooterSegment(unittest.TestCase):
    """A `· W N` bucket for tickets parked on an external event/evidence
    (`ops-wait`). Leaves the workable `I N`, hidden at 0, on BOTH scopes."""

    def _seg(self, home, cwd):
        return statusbar.tickets_segment(cwd, home=home, spawn=False)

    def test_render_shows_ops_wait_when_positive(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=17, name="demo", ops_wait=2)
            seg = self._seg(home, cwd)
            self.assertIn("I 17", seg)
            self.assertIn("W 2", seg)

    def test_render_hides_ops_wait_at_zero_or_missing(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=12, name="demo", ops_wait=0)
            self.assertNotIn("W ", self._seg(home, cwd))
            # a legacy cache with NO ops_wait key must not crash + not render W
            _seed_cache(home, cwd, open_n=12, name="demo")
            seg = self._seg(home, cwd)
            self.assertIn("I 12", seg)
            self.assertNotIn("W ", seg)

    def test_render_combines_ops_wait_with_user_waiting_gk_and_skip(self):
        with TemporaryDirectory() as home:
            cwd = "/home/x/devel/demo"
            _seed_cache(home, cwd, open_n=3, name="demo", gk=5, scope="mine",
                        skipped=2, user_waiting=4, ops_wait=1)
            seg = self._seg(home, cwd)
            self.assertIn("I 3", seg)
            self.assertIn("U 4", seg)
            self.assertIn("W 1", seg)
            self.assertIn("gk 5", seg)
            self.assertIn("skip 2", seg)

    def test_refresh_full_authority_partitions_ops_wait_out_of_open(self):
        # obligation union returns 5 rows; #3 user-waiting, #4/#5 ops-wait →
        # open=2 (workable), user_waiting=1, ops_wait=2. ONE fetch, one partition.
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            fake_gh = Path(bindir) / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                "  *) echo '%s';;\n" % _CORE_FIVE +
                'esac\n')
            fake_gh.chmod(0o755)
            r = subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "tickets-status", "--refresh", "--cwd", repo],
                capture_output=True, text=True,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            cache = json.loads((statusbar.cache_dir(home) /
                                (statusbar.cwd_key(repo) + ".json")).read_text())
            self.assertEqual(cache["open"], 2, "ops-wait + user-waiting must leave I N")
            self.assertEqual(cache.get("user_waiting"), 1)
            self.assertEqual(cache.get("ops_wait"), 2)
            seg = statusbar.tickets_segment(repo, home=home, spawn=False)
            self.assertIn("I 2", seg)
            self.assertIn("U 1", seg)
            self.assertIn("W 2", seg)


if __name__ == "__main__":
    unittest.main()
