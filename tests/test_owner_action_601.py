"""#601 — `needs-owner-action` → U, never W (owner is NOT a third party).

Owner ruling (camera-box, 2026-08-20): a ticket waiting on the OWNER's own
physical/manual step at the rig ("tvoje ruky") is `U`, NEVER `W` — the owner is
never a third party. Before #601 such a ticket was parked as `ops-wait` → W (the
only park label a stream knew), doctrinally wrong.

Layers, mirroring the #539 test shape:
  (a) ROUTING — `needs-owner-action` is a new USER_WAITING label routed to U with
      `--waiting` reason `action`; owner beats third-party (+ops-wait → U); it
      NEVER enters the W (ops_wait) bucket, so the #570 stale! path can never
      touch it; it is always U (as is a bare needs-acceptance since #622 removed
      the #539 chained-I gate — both are the owner's court).
  (b) DISPLAY — a flagged action member shows `no-action!` (missing delivered
      ACTION notice), not `no-question!`.
  (c) LABELS — `needs-owner-action` is NOT in OWNER_DECISION_LABELS (a step,
      not a decision; the #461 digest is #707-RETIRED, job 32 still consumes).
  (d) DOCTRINE — statusline U + W bullets and autopilot SKILL Step-1 name the
      rule; the job-20 partition-audit nudge names the owner-blocked mis-shape so
      running loops fleet-wide re-label their own W tickets.
"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_quals_cmd  # noqa: E402
import statusbar  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import ops_wait_recheck  # noqa: E402

SKILL = airuleset.REPO_DIR / "skills" / "autopilot" / "SKILL.md"
VOCAB = airuleset.REPO_DIR / "modules" / "core" / "statusline-vocabulary.md"
# #859 batch 3: the deep U/W state-machine detail (incl. the #601 clauses this
# class locks) moved to these companions.
VOCAB_DEEP1 = (airuleset.REPO_DIR / "skills" / "statusline-vocabulary-deep"
               / "DEEP-1.md")
VOCAB_DEEP2 = (airuleset.REPO_DIR / "skills" / "statusline-vocabulary-deep"
               / "DEEP-2.md")


def _labels(*names):
    return [{"name": n} for n in names]


def _row(n, *names, created="2026-08-10T00:00:00Z", title="t"):
    return {"number": n, "createdAt": created, "title": title,
            "labels": _labels(*names)}


# --------------------------------------------------------------------------- #
# (a) ROUTING
# --------------------------------------------------------------------------- #
class OwnerActionRoutesToU(unittest.TestCase):
    def test_needs_owner_action_is_a_user_waiting_label(self):
        self.assertIn("needs-owner-action", cli_quals.USER_WAITING_LABELS)

    def test_bare_needs_owner_action_routes_to_U_reason_action(self):
        rows = {881: _row(881, "needs-owner-action")}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(user_waiting), {881},
                         "#601: an owner physical-action ticket is U")
        self.assertEqual(set(ops_wait), set())
        self.assertEqual(set(workable), set())
        self.assertEqual(
            airuleset._user_waiting_reason(_labels("needs-owner-action")),
            "action", "#601: the --waiting reason tag is `action`")

    def test_owner_action_plus_ops_wait_routes_to_U_owner_beats_third_party(self):
        # The camera-box incident: an owner-blocked ticket carrying BOTH
        # needs-owner-action AND ops-wait must go to U — the owner ruling
        # "owner beats third-party framing".
        rows = {688: _row(688, "needs-owner-action", "ops-wait")}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(user_waiting), {688},
                         "#601: needs-owner-action + ops-wait → U, never W")
        self.assertEqual(set(ops_wait), set(),
                         "#601: an owner-action ticket must NEVER enter the W "
                         "bucket (owner is not a third party)")

    def test_owner_action_stays_out_of_W_when_it_is_the_dominant_label(self):
        # When needs-owner-action is the HIGHEST-precedence user-waiting label
        # (no co-present needs-acceptance, which would outrank it), it never
        # reaches the ops_wait bucket — that is what keeps the #570 stale!
        # W-freshness path from ever touching it. needs-answer + ops-wait STAYS
        # in U too (#526: a pending owner answer beats a sent thread), so those
        # combos are also out of W.
        for combo in (("needs-owner-action",),
                      ("needs-owner-action", "ops-wait"),
                      ("needs-owner-action", "needs-answer"),
                      ("needs-owner-action", "needs-answer", "ops-wait")):
            rows = {5: _row(5, *combo)}
            _, _, ops_wait = airuleset._partition_workable(rows)
            self.assertEqual(set(ops_wait), set(),
                             "#601: %r must not land in W" % (combo,))

    def test_acceptance_precedence_dominates_a_co_present_owner_action(self):
        # The lowest-precedence design honestly documented (review A 🟡): a
        # PATHOLOGICAL row carrying BOTH needs-acceptance AND needs-owner-action
        # follows the higher-precedence acceptance routing, NOT action's — so
        # needs-acceptance + needs-owner-action + ops-wait reads reason
        # `acceptance` and routes to W by the acceptance-scoped override. This is
        # the byte-exact preservation of #526 (action is additive, never a
        # regression of an existing label's routing). The combo is contradictory
        # and never occurs in practice; the test pins the documented behavior.
        rows = {5: _row(5, "needs-acceptance", "needs-owner-action", "ops-wait")}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(ops_wait), {5},
                         "acceptance (higher precedence) dominates a co-present "
                         "owner-action → W, not U (#601 lowest-precedence design)")
        self.assertEqual(
            airuleset._user_waiting_reason(
                _labels("needs-acceptance", "needs-owner-action")), "acceptance",
            "acceptance outranks action in the reason precedence")

    def test_answer_precedence_over_action_in_tag(self):
        # A live owner QUESTION (needs-answer) is the more time-sensitive of the
        # two; both are U, but the tag reports `answer` (action is LAST in the
        # precedence). This keeps needs-answer/decision/acceptance byte-exact.
        self.assertEqual(
            airuleset._user_waiting_reason(
                _labels("needs-owner-action", "needs-answer")), "answer")
        self.assertEqual(
            airuleset._user_waiting_reason(
                _labels("needs-owner-action", "needs-decision")), "decision")

    def test_owner_action_is_always_U(self):
        # An owner-action is ALWAYS the owner's court → U. (#622 removed the #539
        # chained-I gate, so a bare needs-acceptance is ALSO always U now — both
        # are the owner's court, and `_partition_workable` is a pure label
        # partition with no acceptance_present routing param.)
        rows = {9: _row(9, "needs-owner-action")}
        workable, user_waiting, ops_wait = airuleset._partition_workable(rows)
        self.assertEqual(set(user_waiting), {9},
                         "#601: owner-action is a physical owner step → U")
        self.assertEqual(set(workable), set())

    def test_acceptance_present_set_never_includes_an_action_member(self):
        # `_acceptance_present_set` only considers bare needs-acceptance rows, so
        # the #622 `queued`-vs-`acceptance` display tag never touches an
        # owner-action ticket (it stays tagged `action`).
        rows = {9: _row(9, "needs-owner-action")}
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               lambda cwd=None, home=None: set()):
            self.assertEqual(airuleset._acceptance_present_set(rows), set())


# --------------------------------------------------------------------------- #
# (a) #570 stale! never touches an action member
# --------------------------------------------------------------------------- #
class OwnerActionNeverStale(unittest.TestCase):
    def test_stale_flag_only_ever_sees_the_W_bucket_which_excludes_action(self):
        # `_stale_ops_wait_flagged` is called with the ops_wait bucket only; an
        # owner-action ticket is never in it, so it can never be `stale!`-tagged.
        rows = {688: _row(688, "needs-owner-action", "ops-wait")}
        _, _, ops_wait = airuleset._partition_workable(rows)
        # The W bucket handed to the stale checker is empty here.
        flagged = airuleset._stale_ops_wait_flagged(
            ops_wait, cwd=None, now=0.0, self_login="me",
            ages_fn=lambda n, sl, now, cwd=None: (0.0, 0.0))
        self.assertEqual(flagged, set(),
                         "#601: an owner-action ticket is never in W, so #570 "
                         "stale! can never flag it")


# --------------------------------------------------------------------------- #
# (b) DISPLAY — no-action! (not no-question!) for an action member
# --------------------------------------------------------------------------- #
class OwnerActionWaitingDisplay(unittest.TestCase):
    def _waiting_line(self, rows, flag_numbers):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(
                rows, own_stream=None,
                reason_fn=airuleset._user_waiting_reason,
                flag_numbers=flag_numbers)
        return buf.getvalue().strip()

    def test_action_member_reason_column_is_action(self):
        line = self._waiting_line({7: _row(7, "needs-owner-action")}, set())
        self.assertIn("\taction\t", line,
                      "#601: the --waiting reason column is `action`")

    def test_flagged_action_member_shows_no_action_not_no_question(self):
        line = self._waiting_line({7: _row(7, "needs-owner-action")}, {7})
        self.assertIn("no-action!", line,
                      "#601: a flagged action member shows `no-action!`")
        self.assertNotIn("no-question!", line,
                         "#601: an action member is never `no-question!`")

    def test_flagged_non_action_member_still_shows_no_question(self):
        line = self._waiting_line({7: _row(7, "needs-answer")}, {7})
        self.assertIn("no-question!", line,
                      "a non-action U member keeps the `no-question!` tag")
        self.assertNotIn("no-action!", line)


# --------------------------------------------------------------------------- #
# (b) #527 invariant — _no_question_flagged covers action members
# --------------------------------------------------------------------------- #
class OwnerActionNoQuestionFlag(unittest.TestCase):
    def test_action_member_with_no_delivered_notice_is_flagged(self):
        rows = {7: _row(7, "needs-owner-action")}
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               lambda cwd=None, home=None: set()):
            flagged = airuleset._no_question_flagged(
                rows, comment_state_fn=lambda n, cwd=None: False)
        self.assertEqual(flagged, {7},
                         "#601: an action member with no delivered notice is "
                         "flagged (adapted #527 invariant)")

    def test_action_member_with_delivered_notice_is_not_flagged(self):
        rows = {7: _row(7, "needs-owner-action")}
        with mock.patch.object(statusbar, "question_map_ticket_refs",
                               lambda cwd=None, home=None: {7}):
            flagged = airuleset._no_question_flagged(
                rows, comment_state_fn=lambda n, cwd=None: False)
        self.assertEqual(flagged, set(),
                         "#601: a delivered ❓ ping (map ref) proves the owner "
                         "was told — not flagged")


# --------------------------------------------------------------------------- #
# (c) action excluded from OWNER_DECISION_LABELS (digest #707-retired; job 32)
# --------------------------------------------------------------------------- #
class OwnerActionExcludedFromDailyDigest(unittest.TestCase):
    def test_needs_owner_action_not_in_owner_decision_labels(self):
        self.assertNotIn("needs-owner-action", wd.OWNER_DECISION_LABELS,
                         "#601: a physical owner step is NOT a decision "
                         "(label set lives on for job 32 post-#707)")

    def test_owner_decision_labels_are_the_two_answer_decision_only(self):
        self.assertEqual(set(wd.OWNER_DECISION_LABELS),
                         {"needs-answer", "needs-decision"})


# --------------------------------------------------------------------------- #
# (d) DOCTRINE content-locks (#498/#578 shape)
# --------------------------------------------------------------------------- #
STATUS_U_FINDER = "Owner fyzická akcia = U, nikdy W (#601"
STATUS_W_FINDER = "Owner-blocked ops-wait = CHYBNÉ zaradenie (#601"
SKILL_FINDER = "Owner fyzická akcia = U, nikdy W (#601"

# Tokens UNIQUE to the #601 clause on each surface (a partial revert drops them).
U_TOKENS = ("needs-owner-action", "owner NIE JE tretia strana")
W_TOKENS = ("needs-owner-action", "VÝHRADNE tretia strana")


def _line_with(text, finder):
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


def _norm_window(text, start_token, end_marker="\n- **"):
    i = text.index(start_token)
    j = text.find(end_marker, i)
    return " ".join(text[i:(j if j > 0 else len(text))].split())


class OwnerActionDoctrineLock(unittest.TestCase):
    def test_status_U_bullet_carries_601_clause(self):
        text = VOCAB_DEEP1.read_text(encoding="utf-8")  # #859 batch 3: moved to companion
        line = _line_with(text, STATUS_U_FINDER)
        self.assertTrue(line, "the STATUS U bullet must carry the #601 clause")
        for tok in U_TOKENS:
            self.assertTrue(tok in line,
                            "STATUS U bullet lost the #601 token %r" % tok)

    def test_status_W_bullet_carries_601_misshape_clause(self):
        text = VOCAB_DEEP2.read_text(encoding="utf-8")  # #859 batch 3: moved to companion
        line = _line_with(text, STATUS_W_FINDER)
        self.assertTrue(line, "the STATUS W bullet must carry the #601 clause")
        for tok in W_TOKENS:
            self.assertTrue(tok in line,
                            "STATUS W bullet lost the #601 token %r" % tok)

    def test_autopilot_skill_step1_carries_601_clause(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(SKILL_FINDER in text,
                        "the autopilot SKILL Step-1 must carry the #601 clause")
        win = _norm_window(text, SKILL_FINDER)
        for tok in ("needs-owner-action", "owner nie je tretia strana"):
            self.assertTrue(tok.lower() in win.lower(),
                            "autopilot SKILL lost the #601 token %r" % tok)

    def test_doctrine_states_owner_is_not_a_third_party_on_all_surfaces(self):
        # #859 batch 3: the VOCAB-side clauses moved to companions
        for path in (VOCAB_DEEP1, VOCAB_DEEP2, SKILL):
            t = " ".join(path.read_text(encoding="utf-8").split()).lower()
            self.assertIn("needs-owner-action", t)
            self.assertIn("owner", t)
            # the operative distinction: owner ≠ third party
            self.assertTrue("nie je tretia strana" in t or
                            "NIE JE tretia strana".lower() in t,
                            "%s must state owner ≠ third party (#601)" % path.name)


# --------------------------------------------------------------------------- #
# (d) job-20 partition-audit nudge names the owner-blocked mis-shape
# --------------------------------------------------------------------------- #
class Job20MisshapeNudge(unittest.TestCase):
    def test_W_trigger_names_owner_action_relabel(self):
        # #714: the compact `_W_TRIGGER` keeps the #601 owner-blocked mis-shape
        # re-label label (the full doctrine lives in the session's modules).
        clause = ops_wait_recheck._W_TRIGGER
        self.assertIn("needs-owner-action", clause,
                      "#601: the job-20 W→I trigger must tell the loop to "
                      "re-label an owner-blocked W ticket needs-owner-action")
        self.assertIn("owner", clause.lower())

    def test_nudge_text_carries_owner_action_relabel_when_W_present(self):
        # A composed nudge with W members names the mis-shape instruction.
        txt = ops_wait_recheck._nudge_text(0, [688], now=0.0)
        self.assertIn("needs-owner-action", txt,
                      "#601: the composed job-20 nudge must carry the "
                      "owner-blocked mis-shape re-label instruction")


if __name__ == "__main__":
    unittest.main()
