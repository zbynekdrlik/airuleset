"""airuleset #110 — ci-monitoring.md re-evaluated as a whole.

The module is always-on: it loads in every session of every project. #110
asked whether it is still worth that cost, and the answer had to be settled
with evidence rather than by adding another paragraph to it.

Evidence gathered for this ticket (2026-07-27), all reproducible:

* 64 native-now probes against a BARE model (isolated ``HOME``, no airuleset
  rules loaded — contamination-controlled: bare answered "NO, no CI rules in
  my instructions", the real ``HOME`` quoted this module verbatim), 8
  replicates per scenario. Falsifiable in both directions and it returned
  both answers: shown a run still in progress with only lint+test passing,
  8/8 declared "CI is green" and 5/8 added "no further action needed"
  (partial green is NOT native), while 8/8 fixed their own self-hosted
  runner and 0/8 asked the user whether to wait.
* Both subagent hooks executed against ``json.dumps`` payloads, not read:
  ``block-subagent-bg-ci-poll.sh`` denies subagent+background+``gh run``
  shapes, ``subagent-stop-check-bg-work.sh`` blocks a stop on an OWNED live
  task and not on a sibling's.
* ``gh run watch`` measured against a live public run: 71 API calls + 9.7 KB
  of output per MINUTE at the default interval (~4100/h against a 5000/h
  limit); ``--interval 30`` only reaches ~2200/h because it re-polls every
  job. ``gh run view --json status,conclusion`` costs 1 call per poll — so
  the ban stays, now with numbers, even though 8/8 bare models reach for the
  banned tool.

These tests lock the OUTCOME of that evaluation: what the module must keep
saying (the parts probes proved are not native), what it must NOT re-absorb
(content another always-on module or a verified hook already owns), and the
size discipline that keeps the next incident from appending its narrative
here instead of to the playbook.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI_MONITORING = REPO / "modules" / "core" / "ci-monitoring.md"
TEST_STRICTNESS = REPO / "modules" / "ci" / "test-strictness.md"
ASK_BEFORE_ASSUMING = REPO / "modules" / "core" / "ask-before-assuming.md"
PLAYBOOK = REPO / ".claude" / "rules" / "airuleset-internals.md"


def _text(path):
    return path.read_text(encoding="utf-8")


class TestLoadBearingContentStays(unittest.TestCase):
    """The clauses the non-leading probe proved are NOT native.

    These are the reason the module still earns its always-on slot; a future
    trimming pass must not reach them.
    """

    def test_all_jobs_not_just_lint_and_test(self):
        text = _text(CI_MONITORING)
        self.assertIn("not just lint and test", text)
        self.assertIn("Never stop at partial green", text)

    def test_nothing_else_starts_until_terminal(self):
        """3/8 bare models chose to 'move on to other useful work' instead."""
        text = _text(CI_MONITORING).lower()
        self.assertIn("nothing starts until ci reaches terminal state", text)

    def test_wait_happens_inside_one_bash_call(self):
        """The turn-cost rule — 0/8 bare models produced this shape."""
        text = _text(CI_MONITORING)
        self.assertIn("INSIDE ONE Bash call", text)


class TestGhRunWatchBanIsMeasured(unittest.TestCase):
    """The ban survives BECAUSE it was measured, not because it was asserted.

    8/8 bare models reach for ``gh run watch``; the module has to out-argue
    that with a number, not with an adjective.
    """

    def test_ban_is_stated(self):
        self.assertIn("gh run watch", _text(CI_MONITORING))

    def test_ban_cites_the_measured_api_cost(self):
        # Measurement detail moved to history (#859 batch 2); assert there
        hist = REPO / ".claude" / "rules-reference" / "ci-monitoring-history.md"
        text = _text(hist)
        self.assertIn("71 API calls", text)

    def test_ban_notes_interval_does_not_rescue_it(self):
        """--interval 30 detail moved to history (#859 batch 2)."""
        hist = REPO / ".claude" / "rules-reference" / "ci-monitoring-history.md"
        text = _text(hist)
        self.assertIn("--interval 30", text)


class TestDuplicatesLiveInTheirOwningModule(unittest.TestCase):
    """True duplicates leave this file — the content itself must survive."""

    def test_flaky_dismissal_is_owned_by_test_strictness(self):
        self.assertNotIn("flaky", _text(CI_MONITORING).lower())
        owner = _text(TEST_STRICTNESS).lower()
        self.assertIn("flaky", owner)
        self.assertIn("dismissing ci failures", owner)

    def test_want_me_to_wait_is_owned_by_ask_before_assuming(self):
        self.assertNotIn("want me to wait", _text(CI_MONITORING).lower())
        self.assertIn('"Should I monitor CI?"', _text(ASK_BEFORE_ASSUMING))

    def test_never_blindly_rerun_stays_here(self):
        """1/8 bare models still rerun first — no other module owns this."""
        text = _text(CI_MONITORING).lower()
        self.assertIn("rerun", text)


class TestIncidentNarrativeLivesInThePlaybook(unittest.TestCase):
    """Evidence belongs on the surface that loads when the rule is edited.

    ``.claude/rules/airuleset-internals.md`` is ``paths:``-scoped to
    ``modules/**`` and ``tests/**`` — it arrives exactly when someone works
    on this rule, and costs nothing in the sessions that never do.
    """

    def test_module_keeps_the_corrected_citation_pointer(self):
        text = _text(CI_MONITORING)
        self.assertIn("#29193", text)
        # "OPPOSITE failure mode" archaeology moved to history (#859 batch 2)
        hist = REPO / ".claude" / "rules-reference" / "ci-monitoring-history.md"
        self.assertIn("OPPOSITE failure mode", _text(hist))

    def test_module_does_not_carry_the_research_narrative(self):
        text = _text(CI_MONITORING)
        for narrative in (
            "closed as a duplicate of a narrower terminal-close scenario",
            "could not force a live mid-session compaction",
            "per the #88",
        ):
            self.assertNotIn(narrative, text)

    def test_playbook_still_carries_that_narrative(self):
        playbook = "\n".join(p.read_text(encoding="utf-8") for p in ([REPO / ".claude" / "rules" / "airuleset-internals.md"] + sorted((REPO / ".claude" / "rules").glob("internals-*.md")) + [REPO / ".claude" / "rules-reference" / "internals-archive.md"]) if p.exists())  # #482: narrative in the on-demand archive
        self.assertIn("#29193", playbook)
        self.assertIn("OPPOSITE", playbook)


class TestSubagentSectionIsAPointerNotAnEssay(unittest.TestCase):
    """Both hooks were executed against real payloads for #110 and do cover
    the ban — so the prose keeps the imperative and hands the enforcement
    detail to the hooks it names."""

    def _subagent_paragraph(self):
        for line in _text(CI_MONITORING).split("\n"):
            if "BROKEN in a subagent" in line:
                return line
        self.fail("no paragraph carrying 'BROKEN in a subagent'")

    def test_imperative_and_both_hooks_are_named(self):
        para = self._subagent_paragraph()
        self.assertIn("TERMINATES", para)
        self.assertIn("block-subagent-bg-ci-poll.sh", para)
        self.assertIn("subagent-stop-check-bg-work.sh", para)

    def test_paragraph_is_pointer_sized(self):
        para = self._subagent_paragraph()
        self.assertLessEqual(
            len(para.split()), 110,
            "the subagent ban is enforced by two verified hooks — the "
            "always-on prose carries the imperative and the hook names, "
            "not the incident essay",
        )


class TestLongWaitWaiterActuallyBlocks(unittest.TestCase):
    """A ``nohup ... &`` inside a ``run_in_background: true`` call returns
    immediately, so the harness task completes at once and the single
    notification the design depends on fires straight away."""

    def _long_wait_block(self):
        blocks = re.findall(r"```(?:bash)?\n(.*?)\n\s*```", _text(CI_MONITORING), re.S)
        self.assertGreaterEqual(len(blocks), 2, "expected a long-wait code block")
        return blocks[1]

    def test_waiter_does_not_detach_from_the_harness_task(self):
        block = self._long_wait_block()
        self.assertNotIn("nohup", block)
        self.assertFalse(
            re.search(r"&\s*$", block.strip()),
            "a trailing & detaches the waiter and fires the notification at once",
        )

    def test_waiter_still_self_bounds(self):
        block = self._long_wait_block()
        self.assertIn("AIRULESET_LONG_POLL_BUDGET_S", block)


class TestAccretionCap(unittest.TestCase):
    """#110's point: the file grew by storing evidence next to instruction.

    The cap is not a line-count target — it is the mechanism that sends the
    NEXT incident's narrative to the playbook instead of here.
    """

    def test_module_stays_under_its_word_cap(self):
        # Cap raised 1400 -> 1700 for the #588 DEPLOY / VERSION-LIVE watch
        # recipe (unblock on deployed-state, not run-terminal — a user-
        # requested standardization). The cap governs incident NARRATIVE
        # accretion; an operative poll recipe (the always-on content a
        # working session actually copies) is the sanctioned kind of growth,
        # not narrative. Keep it tight: any further raise needs the same
        # operative-vs-narrative justification, not just "one more paragraph".
        words = len(_text(CI_MONITORING).split())
        self.assertLessEqual(
            words, 1700,
            f"ci-monitoring.md is {words} words; incident narrative belongs "
            "in .claude/rules/airuleset-internals.md (auto-loads on "
            "modules/** and tests/**), not in the always-on prefix",
        )


if __name__ == "__main__":
    unittest.main()
