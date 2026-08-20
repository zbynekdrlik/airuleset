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

    def test_bare_needs_acceptance_is_user_waiting(self):
        # #512: a BARE needs-acceptance ticket (done, awaiting owner acceptance)
        # now waits on the OWNER -> U, no longer the stream's own workable I N.
        self.assertTrue(airuleset._row_is_user_waiting(_labels("needs-acceptance")))

    def test_needs_acceptance_with_ready_for_review_is_not_user_waiting(self):
        # #507 precedence preserved: a needs-acceptance ticket that is ALSO a
        # genuine re-hand-off (ready-for-review / needs-gatekeeper) is back in the
        # gatekeeper's court -> stays workable so the gk/handed logic counts it,
        # NEVER U.
        self.assertFalse(airuleset._row_is_user_waiting(
            _labels("needs-acceptance", "ready-for-review")))
        self.assertFalse(airuleset._row_is_user_waiting(
            _labels("needs-acceptance", "needs-gatekeeper")))

    def test_needs_acceptance_with_bounce_is_not_user_waiting(self):
        # #507/#313 precedence: a needs-acceptance ticket returned via prio:bounce
        # is reworkable by the stream -> stays workable (the stream's I), NEVER U.
        self.assertFalse(airuleset._row_is_user_waiting(
            _labels("needs-acceptance", "prio:bounce")))

    def test_answer_override_does_not_apply_to_needs_answer(self):
        # The gk/bounce override is scoped to needs-acceptance ONLY — a
        # needs-answer ticket that also carries ready-for-review stays U (its
        # #468 routing is unchanged; the override never applies to it).
        self.assertTrue(airuleset._row_is_user_waiting(
            _labels("needs-answer", "ready-for-review")))

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

    def test_labels_constant_is_the_four_owner_waiting_labels(self):
        # #512 (owner decision 2026-08-16): the "waiting on the OWNER" family is
        # consolidated to answer + decision + acceptance (was the two #468
        # labels; needs-acceptance folded in, its A-bucket never shipped).
        # #601 (owner ruling 2026-08-20): needs-owner-action joins — an owner's
        # own physical/manual step ("čo čaká na TEBA") is U, never a W third
        # party.
        self.assertEqual(set(airuleset.USER_WAITING_LABELS),
                         {"needs-answer", "needs-decision", "needs-acceptance",
                          "needs-owner-action"})

    def test_bare_needs_acceptance_partitions_into_user_waiting(self):
        # #512: the ONE derivation routes a bare needs-acceptance row to the
        # user_waiting bucket (leaves workable), while a re-hand-off / bounce
        # variant stays workable (#507 precedence).
        rows = {
            1: {"number": 1, "labels": _labels("needs-acceptance")},
            2: {"number": 2, "labels": _labels("needs-acceptance", "ready-for-review")},
            3: {"number": 3, "labels": _labels("needs-acceptance", "prio:bounce")},
            4: {"number": 4, "labels": _labels("bug")},
        }
        workable, waiting = airuleset._partition_user_waiting(rows)
        self.assertEqual(set(waiting), {1})
        self.assertEqual(set(workable), {2, 3, 4})

    def test_user_waiting_reason_maps_each_label(self):
        self.assertEqual(airuleset._user_waiting_reason(_labels("needs-answer")), "answer")
        self.assertEqual(airuleset._user_waiting_reason(_labels("needs-decision")), "decision")
        self.assertEqual(airuleset._user_waiting_reason(_labels("needs-acceptance")), "acceptance")
        self.assertEqual(airuleset._user_waiting_reason(_labels("bug")), "")


def _run_quals(subcmd, flag, repo, home, bindir, marker=None):
    # core-quals/slice-quals resolve authority + the repo root from the PROCESS
    # cwd (_repo_root → git rev-parse), so the subprocess MUST run inside the
    # temp repo — that is where its CLAUDE.md authority marker lives.
    if marker is not None:
        Path(repo, "CLAUDE.md").write_text(marker + "\n")
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True, cwd=repo,
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


class WaitingReasonAndPingMerge(unittest.TestCase):
    """#512: `core-quals --waiting` tags each labeled member with a reason
    (answer/decision/acceptance) AND lists the ticketless ❓ pings (reason=ping),
    so the `--waiting` member list matches the footer's `U N` count."""

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

    def _seed_questions(self, home, entries):
        import statusbar
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def test_waiting_tags_each_labeled_member_with_a_reason(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            # column 4 (0-based index 3) is the reason tag.
            reasons = {}
            for ln in r.stdout.splitlines():
                if not ln.strip():
                    continue
                f = ln.split("\t")
                reasons[f[0]] = f[3]
            self.assertEqual(reasons.get("4"), "answer")     # #4 needs-answer
            self.assertEqual(reasons.get("5"), "decision")   # #5 needs-decision

    def test_waiting_lists_ticketless_pings_but_not_ticket_referencing_ones(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir)
            # scoped to the subprocess cwd (= repo); one ticketless, one with #N.
            self._seed_questions(home, {
                "1": {"cwd": repo, "block": "otazka bez ticketu", "ts": 1},
                "2": {"cwd": repo, "block": "otazka k #742", "ts": 2},
            })
            r = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(r.returncode, 0, r.stderr)
            ping_lines = [ln for ln in r.stdout.splitlines()
                          if ln.split("\t")[3:4] == ["ping"]]
            self.assertEqual(len(ping_lines), 1,
                             "exactly the ONE ticketless ping is listed as a "
                             "ping member; the #742-referencing ping is deduped")


if __name__ == "__main__":
    unittest.main()
