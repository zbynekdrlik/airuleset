"""#622 — a bare needs-acceptance QUEUED for owner approval belongs in U, not I.

Owner directive 2026-08-22 (verbatim): "ked vidim I29 tak to je 29 ticketov na
ktorych mozes robit a W 15 ma znamenat ze sa caka na cloveka alebo na nejaky
releae no nie na mna!". The I/U/W model: I = only dispatchable-now code work;
U = everything waiting on the OWNER (incl. an acceptance draft queued for #606
one-at-a-time delivery); W = a third party/release, never the owner.

This REVERSES #539's chained-I disposition. #539 routed a bare needs-acceptance
with NO delivered draft to `workable` (I), using "no delivered ping" as a proxy
for "the stream's own chained work". #606 (owner directive 2026-08-21, AFTER
#539) mandates delivering owner-questions ONE AT A TIME, so "no delivered ping"
overwhelmingly means QUEUED-behind-others, which is waiting on the owner → U.

The fix (existing machinery, applied correctly — NO new label; `needs-acceptance`
already carries the semantics):
  - a bare needs-acceptance → U unconditionally (`_partition_workable` drops its
    #539 chained-I branch);
  - `--waiting` tags an undelivered one `queued` (delivered ones stay `acceptance`);
  - `_no_question_flagged` EXEMPTS acceptance-reason members — the #606 queue is a
    legit reason for non-delivery, not a forgotten question.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import statusbar  # noqa: E402

SKILL = airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md"
VOCAB = airuleset.REPO_DIR / "modules" / "core" / "statusline-vocabulary.md"


def _labels(*names):
    return [{"name": n} for n in names]


def _run_quals(subcmd, flag, repo, home, bindir):
    return subprocess.run(
        [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"), subcmd, flag],
        capture_output=True, text=True, cwd=repo,
        env={**os.environ, "HOME": home, "PATH": f"{bindir}:{os.environ['PATH']}"})


class BareAcceptanceRoutesToUEndToEnd(unittest.TestCase):
    """The owner's core complaint, end-to-end: a bare needs-acceptance with no
    delivered draft is LISTED by `core-quals --waiting` (U) and is NOT in the
    workable `--count` (I). RED against #539's chained-I → I."""

    def _fake_gh(self, bindir, rows):
        gh = Path(bindir) / "gh"
        gh.write_text(
            "#!/usr/bin/env bash\n"
            'case "$*" in\n'
            '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
            '  *"issue view"*"comments"*) echo \'{"comments":[]}\';;\n'
            '  *"--search label:autopilot-skip"*) echo 0;;\n'
            "  *) echo '%s';;\n" % rows +
            'esac\n')
        gh.chmod(0o755)

    def _seed(self, home, entries):
        d = statusbar._claude_dir(home)
        d.mkdir(parents=True, exist_ok=True)
        (d / "discord-questions.json").write_text(json.dumps(entries))

    def _rows_by_reason(self, stdout):
        out = {}
        for ln in stdout.splitlines():
            if not ln.strip():
                continue
            f = ln.split("\t")
            if len(f) >= 4:
                out[f[0]] = f[3]
        return out

    def _nums(self, stdout):
        return {ln.split("\t", 1)[0] for ln in stdout.splitlines() if ln.strip()}

    def test_queued_acceptance_is_in_waiting_and_not_counted(self):
        rows = json.dumps([
            {"number": 1, "labels": _labels("bug")},
            {"number": 4, "labels": _labels("needs-acceptance")},  # no draft ping
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)   # no question map → #4 has no draft
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertIn("4", self._nums(w.stdout),
                          "#622: a bare needs-acceptance queued for owner approval "
                          "is LISTED in --waiting (U), not the workable I")
            c = _run_quals("core-quals", "--count", repo, home, bindir)
            self.assertEqual(c.returncode, 0, c.stderr)
            self.assertEqual(c.stdout.strip(), "1",
                             "#622: only #1 is workable now; the queued acceptance "
                             "#4 left I for U")

    def test_undelivered_acceptance_reason_tag_is_queued(self):
        rows = json.dumps([
            {"number": 4, "labels": _labels("needs-acceptance")},
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)   # no draft ping
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            reasons = self._rows_by_reason(w.stdout)
            self.assertEqual(reasons.get("4"), "queued",
                             "#622: an undelivered bare acceptance is tagged "
                             "`queued` (draft ready, awaiting #606 delivery)")

    def test_queued_member_is_not_no_question_flagged(self):
        rows = json.dumps([
            {"number": 4, "labels": _labels("needs-acceptance")},
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            reasons = self._rows_by_reason(w.stdout)
            self.assertIn("4", reasons,
                          "#622: the queued acceptance must be a U member")
            self.assertNotIn("no-question!", reasons.get("4", ""),
                             "#622: the #606 queue is a legit reason for "
                             "non-delivery — a queued member is NOT no-question!")

    def test_delivered_acceptance_stays_tagged_acceptance(self):
        # A bare acceptance WITH a delivered draft ping stays in U tagged
        # `acceptance` (a live owner-approval question), not `queued`.
        rows = json.dumps([
            {"number": 4, "labels": _labels("needs-acceptance")},
        ])
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            self._fake_gh(bindir, rows)
            self._seed(home, {"d": {"cwd": repo, "block": "draft na schvalenie #4",
                                    "ts": 1}})
            w = _run_quals("core-quals", "--waiting", repo, home, bindir)
            self.assertEqual(w.returncode, 0, w.stderr)
            reasons = self._rows_by_reason(w.stdout)
            self.assertEqual(reasons.get("4"), "acceptance",
                             "#622: a DELIVERED acceptance is a live owner question "
                             "→ tagged `acceptance`, not `queued`")


class NoQuestionFlagExemptsAcceptance(unittest.TestCase):
    """`_no_question_flagged` must NOT flag an acceptance-reason U member (a
    queued acceptance is legitimately undelivered); answer/decision/action
    members keep the flag. RED: current code flags a question-less acceptance."""

    def _rows(self, *specs):
        # specs: (number, label)
        return {n: {"number": n, "labels": _labels(lb)} for n, lb in specs}

    def test_bare_acceptance_member_is_not_flagged(self):
        import unittest.mock as mock
        rows = self._rows((4, "needs-acceptance"))
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               lambda cwd=None, home=None: set()):
            flagged = airuleset._no_question_flagged(
                rows, comment_state_fn=lambda n, cwd=None: False)
        self.assertEqual(flagged, set(),
                         "#622: an acceptance member (queued) is exempt from "
                         "no-question! — the #606 queue is the reason, not a "
                         "forgotten question")

    def test_needs_answer_member_is_still_flagged(self):
        import unittest.mock as mock
        rows = self._rows((5, "needs-answer"))
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               lambda cwd=None, home=None: set()):
            flagged = airuleset._no_question_flagged(
                rows, comment_state_fn=lambda n, cwd=None: False)
        self.assertEqual(flagged, {5},
                         "a question-less needs-answer member is still flagged "
                         "no-question! (the exemption is acceptance-scoped)")


class PartitionSignatureIsPure(unittest.TestCase):
    """#622: `_partition_workable` no longer takes an `acceptance_present` routing
    param — a bare needs-acceptance routes to U unconditionally, so the param has
    no purpose. RED: the current signature still carries it."""

    def test_partition_workable_has_no_acceptance_present_param(self):
        import inspect
        params = inspect.signature(airuleset._partition_workable).parameters
        self.assertNotIn(
            "acceptance_present", params,
            "#622: the #539 chained-I routing gate is removed; "
            "_partition_workable is a pure label partition again")

    def test_bare_acceptance_partitions_to_user_waiting(self):
        rows = {4: {"number": 4, "labels": _labels("needs-acceptance")}}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(user_waiting), {4})
        self.assertEqual(set(workable), set())
        self.assertEqual(set(ops_wait), set())


class DoctrineNames622QueuedU(unittest.TestCase):
    """Both governance surfaces must state the #622 rule (a queued acceptance is
    U, not I) and drop the stale #539 "chained-I → I / until a draft ping"
    disposition. RED: the doctrine still says chained-I stays in I."""

    def _norm(self, path):
        return " ".join(path.read_text(encoding="utf-8").split()).lower()

    def test_vocab_states_queued_acceptance_is_u(self):
        t = self._norm(VOCAB)
        self.assertIn("#622", t,
                      "statusline-vocabulary.md must carry the #622 doctrine")
        self.assertIn("queued", t,
                      "statusline-vocabulary.md must name the `queued` disposition")

    def test_skill_states_queued_acceptance_is_u(self):
        t = self._norm(SKILL)
        self.assertIn("#622", t,
                      "the autopilot SKILL Step 1 must carry the #622 doctrine")
        self.assertIn("queued", t,
                      "the autopilot SKILL must name the `queued` disposition")

    def test_vocab_no_longer_says_bare_acceptance_falls_to_I(self):
        # The #601 U-bullet contrast "na rozdiel od bare needs-acceptance (ktorý
        # bez doručeného draftu padá do I)" is now FALSE — a queued acceptance is
        # U. It must be retired/qualified so the doctrine stays self-consistent.
        t = self._norm(VOCAB)
        self.assertNotIn("bez doručeného draftu padá do `i`", t,
                         "#622: the stale '#539 bare acceptance falls to I' "
                         "contrast must be removed — it now goes to U (queued)")


if __name__ == "__main__":
    unittest.main()
