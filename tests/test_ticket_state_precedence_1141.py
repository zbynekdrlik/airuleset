"""#1141 slice 2 — the precedence fixes inside `cli_ticket_state.classify()`.

The owner's ruling (design comment on #1141, slice 2):
1. An owner question beats any hand-off label. `needs-answer` /
   `needs-decision` / `needs-owner-action`, or an UNSENT `needs-acceptance`,
   lands in U even with `gk-processing` / `ready-for-review` /
   `needs-gatekeeper` (live: odoo-erp 8058 and 8180). A SENT acceptance
   (`ops-wait`) keeps its slice-1 route.
2. On the full-authority (gk) box a FOREIGN stream's owner question is hidden:
   neither I nor U there, it counts in that stream's own U on its own box. This
   reverses #654 for questions. A foreign hand-off with no question stays in the
   gk box's I (action-only).
3. Everything else is as in slice 1.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_ticket_state  # noqa: E402

GK = cli_ticket_state.Box()                       # full authority
MONTALU = cli_ticket_state.Box(own_stream="montalu1")
DAVID = cli_ticket_state.Box(own_stream="david1")
HANDOFFS = ("gk-processing", "ready-for-review", "needs-gatekeeper")
QUESTIONS = ("needs-answer", "needs-decision", "needs-owner-action")


def _row(*names):
    return {"number": 1, "title": "t", "labels": [{"name": n} for n in names]}


def _bucket(row, box):
    return cli_ticket_state.classify(row, None, box)[0]


def _where(number, parts):
    workable, waiting, ops_wait = parts
    return [b for b, d in (("I", workable), ("U", waiting), ("W", ops_wait))
            if number in d]


class OwnerQuestionBeatsHandoff(unittest.TestCase):
    """Rule 1: the question is decided before any hand-off label."""

    def test_8058_shape_is_U_on_the_montalu_box(self):
        row = _row("stream:montalu", "needs-answer", "gk-processing")
        bucket, reason = cli_ticket_state.classify(row, None, MONTALU)
        self.assertEqual(bucket, "U")
        self.assertEqual(
            _where(1, airuleset._partition_workable({1: row},
                                                    own_stream="montalu1")),
            ["U"])
        # a handed-off row parked on the owner is never counted in gk
        self.assertEqual(cli_ticket_state.classify(
            row, cli_ticket_state.Facts(handed=True), MONTALU)[0], "U")

    def test_unsent_acceptance_with_a_handoff_is_U_on_the_owning_box(self):
        for handoff in HANDOFFS:
            own = _row("stream:montalu", "needs-acceptance", handoff)
            bucket, reason = cli_ticket_state.classify(own, None, MONTALU)
            self.assertEqual(bucket, "U", handoff)
            self.assertIn("#1141", reason)
            self.assertIn(handoff, reason)
            self.assertEqual(_where(1, airuleset._partition_workable(
                {1: own}, own_stream="montalu1")), ["U"], handoff)
            # the gk box's own tickets (bare / stream:core) — U on gk
            for extra in ((), ("stream:core",)):
                core = _row("needs-acceptance", handoff, *extra)
                self.assertEqual(_bucket(core, GK), "U", (handoff, extra))
                self.assertEqual(
                    _where(1, airuleset._partition_workable({1: core})),
                    ["U"], (handoff, extra))

    def test_every_owner_question_beats_every_handoff_on_the_owning_box(self):
        for q in QUESTIONS + ("needs-acceptance",):
            for handoff in HANDOFFS:
                self.assertEqual(_bucket(_row(q, handoff), GK), "U",
                                 (q, handoff))
                self.assertEqual(
                    _bucket(_row("stream:montalu", q, handoff), MONTALU),
                    "U", (q, handoff))

    def test_sent_acceptance_keeps_its_slice1_route(self):
        # sent, no hand-off → W (#526)
        self.assertEqual(_bucket(_row("needs-acceptance", "ops-wait"), GK),
                         "W")
        # sent + hand-off → I on gk (#943: only the gk box acts on a hand-off)
        for handoff in HANDOFFS:
            self.assertEqual(
                _bucket(_row("needs-acceptance", "ops-wait", handoff), GK),
                "I", handoff)

    def test_prio_bounce_still_overrides_an_acceptance(self):
        # a bounce is the stream's own rework, not a hand-off label
        for extra in ((), ("gk-processing",), ("ops-wait",)):
            self.assertEqual(
                _bucket(_row("stream:montalu", "needs-acceptance",
                             "prio:bounce", *extra), MONTALU),
                "I", extra)


class ReviewRound1(unittest.TestCase):
    """Review round 1 (two adversarial reviews): the owner question is found
    by its OWN labels, never by the acceptance-first display order."""

    def test_owner_action_beats_a_sent_acceptance(self):
        for extra in ((),) + tuple((h,) for h in HANDOFFS):
            own = _row("stream:montalu", "needs-owner-action",
                       "needs-acceptance", "ops-wait", *extra)
            bucket, reason = cli_ticket_state.classify(own, None, MONTALU)
            self.assertEqual(bucket, "U", extra)
            self.assertIn("needs-owner-action", reason)
            self.assertEqual(_bucket(own, GK), cli_ticket_state.HIDDEN,
                             extra)
            core = _row("needs-owner-action", "needs-acceptance", "ops-wait",
                        *extra)
            self.assertEqual(_bucket(core, GK), "U", extra)

    def test_bounce_reason_names_the_bounce(self):
        row = _row("stream:montalu", "needs-acceptance", "prio:bounce",
                   "gk-processing")
        bucket, reason = cli_ticket_state.classify(row, None, MONTALU)
        self.assertEqual(bucket, "I")
        self.assertIn("prio:bounce", reason)
        self.assertNotIn("overridden by gk-processing", reason)

    def test_a_foreign_question_of_a_stream_without_a_live_box_stays_U(self):
        import cli_fleet
        row = _row("stream:montalu", "needs-answer", "gk-processing")
        paused = [dict(h, paused="test: host paused")
                  if h.get("user") == "montalu1" else h
                  for h in cli_fleet.REMOTE_HOSTS]
        with mock.patch.object(cli_fleet, "REMOTE_HOSTS", paused):
            bucket, reason = cli_ticket_state.classify(row, None, GK)
        self.assertEqual(bucket, "U")
        self.assertIn("montalu1", reason)
        observers = cli_fleet.WEBTERM_OBSERVER_USERS | {"montalu1"}
        with mock.patch.object(cli_fleet, "WEBTERM_OBSERVER_USERS",
                               observers):
            self.assertEqual(_bucket(row, GK), "U")
        # a live stream's question is still hidden on gk
        self.assertEqual(_bucket(row, GK), cli_ticket_state.HIDDEN)

    def test_supplement_skips_a_sent_acceptance(self):
        import statusbar
        obj = {"state": "OPEN", "title": "t", "createdAt": "",
               "labels": [{"name": "needs-acceptance"},
                          {"name": "ops-wait"}]}
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               return_value={7}):
            got = cli_quals._question_map_u_supplement(
                {}, "/r", lambda argv, cd: json.dumps(obj))
        self.assertEqual(got, {}, "a sent acceptance is W, never U")


class ForeignQuestionHiddenOnGk(unittest.TestCase):
    """Rule 2: a foreign stream's owner question is not the gk box's I or U."""

    def test_8058_shape_is_hidden_on_the_gk_box(self):
        row = _row("stream:montalu", "needs-answer", "gk-processing")
        bucket, reason = cli_ticket_state.classify(row, None, GK)
        self.assertEqual(bucket, cli_ticket_state.HIDDEN)
        self.assertNotIn(bucket, cli_ticket_state.BUCKETS)
        self.assertIn("montalu1", reason)
        self.assertIn("#1141", reason)
        self.assertEqual(_where(1, airuleset._partition_workable({1: row})),
                         [])

    def test_every_foreign_question_is_hidden_on_gk(self):
        for q in QUESTIONS + ("needs-acceptance",):
            for extra in ((),) + tuple((h,) for h in HANDOFFS):
                row = _row("stream:montalu", q, *extra)
                self.assertEqual(_bucket(row, GK), cli_ticket_state.HIDDEN,
                                 (q, extra))
                # the M and gk facts never resurrect it
                for facts in (cli_ticket_state.Facts(merged=True),
                              cli_ticket_state.Facts(handed=True)):
                    self.assertEqual(
                        cli_ticket_state.classify(row, facts, GK)[0],
                        cli_ticket_state.HIDDEN, (q, extra, facts))

    def test_a_foreign_handoff_without_a_question_stays_gk_I(self):
        for handoff in HANDOFFS:
            row = _row("stream:montalu", handoff)
            bucket, reason = cli_ticket_state.classify(row, None, GK)
            self.assertEqual(bucket, "I", handoff)
            self.assertEqual(
                _where(1, airuleset._partition_workable({1: row})), ["I"])

    def test_a_foreign_sent_acceptance_is_not_a_question(self):
        self.assertEqual(
            _bucket(_row("stream:montalu", "needs-acceptance", "ops-wait"),
                    GK), "W")

    def test_the_reduced_authority_box_keeps_its_slice1_route(self):
        # rule 2 is scoped to the full-authority box; a foreign question on a
        # slice box keeps the #654 action-only route of slice 1
        row = _row("stream:montalu", "needs-answer")
        self.assertEqual(_bucket(row, DAVID), "I")


class ExplainPrintsTheMovedRows(unittest.TestCase):
    """`--explain` prints each moved row with its new reason, and a hidden row
    is listed (never silently dropped) without entering any total."""

    def test_hidden_row_listed_with_reason_and_counted_apart(self):
        import cli_ticket_explain
        rows = {
            5: {"number": 5, "title": "own q",
                "labels": [{"name": "needs-acceptance"},
                           {"name": "gk-processing"}]},
            8: {"number": 8, "title": "foreign q",
                "labels": [{"name": "stream:montalu"},
                           {"name": "needs-answer"},
                           {"name": "gk-processing"}]},
        }
        w, u, o = airuleset._partition_workable(rows)
        with mock.patch.object(cli_ticket_explain, "_footer_extras",
                               return_value=[]), \
                mock.patch("builtins.print") as fake_print:
            cli_ticket_explain.explain_core(
                None, workable=w, merged_rows={}, waiting=u, ops_wait=o,
                merged_set=frozenset(), rows=rows)
        printed = [c.args[0] for c in fake_print.call_args_list]
        row8 = [ln for ln in printed if ln.startswith("8\t")]
        self.assertEqual(len(row8), 1, printed)
        self.assertEqual(row8[0].split("\t")[1], "hidden")
        self.assertIn("montalu1", row8[0].split("\t")[2])
        row5 = [ln for ln in printed if ln.startswith("5\t")]
        self.assertEqual(row5[0].split("\t")[1], "U")
        self.assertIn("#1141", row5[0].split("\t")[2])
        self.assertEqual(printed[-1],
                         "# explain: I=0 M=0 U=1 W=0 gk=0 hidden=1")
        self.assertFalse(any("mismatch" in ln for ln in printed), printed)


class DisplayConsumersAgree(unittest.TestCase):
    """The `--waiting` acceptance tag and the #948 question-map supplement read
    the SAME question predicate the partition uses."""

    def test_delivered_unsent_acceptance_with_handoff_is_not_queued(self):
        import statusbar
        row = _row("needs-acceptance", "gk-processing")
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               return_value={1}):
            self.assertEqual(cli_quals._acceptance_present_set({1: row}),
                             {1})

    def test_question_map_supplement_takes_an_unsent_acceptance(self):
        import statusbar
        obj = {"state": "OPEN", "title": "t", "createdAt": "",
               "labels": [{"name": "needs-acceptance"},
                          {"name": "gk-processing"}]}
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               return_value={7}):
            got = cli_quals._question_map_u_supplement(
                {}, "/r", lambda argv, cd: json.dumps(obj))
        self.assertEqual(set(got), {7})


class CoreQualsCli(unittest.TestCase):
    """End-to-end on the gk box: the foreign question is out of `--count` and
    `--waiting`, and `--explain` lists it as hidden."""

    _ROWS = json.dumps([
        {"number": 1, "title": "plain", "labels": [{"name": "bug"}]},
        {"number": 2, "title": "foreign q",
         "labels": [{"name": "stream:montalu"}, {"name": "needs-answer"},
                    {"name": "gk-processing"}]},
        {"number": 3, "title": "foreign hand-off",
         "labels": [{"name": "stream:montalu"}, {"name": "gk-processing"}]},
    ])

    def _run(self, *argv):
        with TemporaryDirectory() as home, TemporaryDirectory() as repo, \
                TemporaryDirectory() as bindir:
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            gh = Path(bindir) / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                'case "$*" in\n'
                '  *"repo view"*|repo*) echo "zbynekdrlik/demo";;\n'
                '  *"--search label:autopilot-skip"*) echo 0;;\n'
                "  *) echo '%s';;\n" % self._ROWS +
                'esac\n')
            gh.chmod(0o755)
            return subprocess.run(
                [sys.executable, str(airuleset.REPO_DIR / "airuleset.py"),
                 "core-quals"] + list(argv),
                capture_output=True, text=True, cwd=repo,
                env={**os.environ, "HOME": home,
                     "PATH": f"{bindir}:{os.environ['PATH']}"})

    def test_count_holds_the_foreign_handoff_but_not_the_question(self):
        r = self._run("--count")
        self.assertEqual(r.stdout.strip(), "2", r.stderr)

    def test_waiting_does_not_list_the_hidden_question(self):
        r = self._run("--waiting")
        self.assertEqual(r.returncode, 0, r.stderr)
        nums = {ln.split("\t", 1)[0] for ln in r.stdout.splitlines()
                if ln.strip() and not ln.startswith("#")}
        self.assertNotIn("2", nums, r.stdout)

    def test_explain_lists_the_hidden_row(self):
        r = self._run("--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        by_num = {ln.split("\t")[0]: ln.split("\t")[1]
                  for ln in r.stdout.splitlines() if ln and ln[0].isdigit()}
        self.assertEqual(by_num, {"1": "I", "2": "hidden", "3": "I"},
                         r.stdout)
        self.assertIn("# explain: I=2 M=0 U=0 W=0 gk=0 hidden=1",
                      r.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
