"""#607 časti 1 + 4 (+ časť 2 nudge escalation) — job-20 W-clause content + fleet rule.

Part 1 (hodinový thread-check): the fleet W-doctrine (statusline-vocabulary.md)
+ the job-20 `_W_CLAUSE` name the ≥1×/hodinu Discuss-thread-read duty; the
watchdog cannot verify Discuss reads itself (#550), so the module docstring
DEFERS the hourly-granularity verification with named reopen triggers, and the
observable proxy is the weekend-aware `stale!` (part 2).

Part 2 escalation: `_W_STALE_CLAUSE` escalates from "a `stale!` tag exists" to
"a substantive reminder MUST be SENT into the client thread TODAY" (working-day
24h passed).

Part 4 (W-hides-owner-decision): `_W_CLAUSE` names the owner-DECISION mis-shape —
a W member whose re-entry event is the OWNER's answer/decision (not a third
party, not a physical rig step) is buried wrong ("na U sa vždy pýtam"): relabel
`needs-answer`/`needs-decision` (U) with a DELIVERED question.

Content-lock tokens are UNIQUE to the #607 clauses (verified absent from the
neighbouring #526/#547/#570/#578/#601 clauses on the same physical W-bullet line,
the #578 whole-line teeth rule).
"""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog.ops_wait_recheck as owr  # noqa: E402

STATUS = REPO / "modules" / "core" / "statusline-vocabulary.md"

FLEET_FINDER = "24h-push kontrakt (#607)"
# co-tokens UNIQUE to the #607 fleet sentence within the W bullet's single line:
FLEET_TOKENS = ("1×/hodinu", "pracovných dní", "OWNER-DECISION")


def _line_with(text, finder):
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


class FleetRuleContentLock(unittest.TestCase):
    """statusline-vocabulary.md W bullet carries the #607 24h-push kontrakt."""

    def test_status_W_bullet_carries_607_contract(self):
        text = STATUS.read_text(encoding="utf-8")
        line = _line_with(text, FLEET_FINDER)
        self.assertTrue(line, "W bullet must carry the #607 24h-push kontrakt")
        for tok in FLEET_TOKENS:
            self.assertIn(tok, line,
                          "W bullet lost the #607 operative token %r" % tok)

    def test_607_finder_is_unique_on_the_line(self):
        # #578 teeth rule: the finder must occur exactly once across the whole
        # W-bullet line so a partial revert of the #607 clause genuinely fails.
        text = STATUS.read_text(encoding="utf-8")
        self.assertEqual(text.count(FLEET_FINDER), 1,
                         "the #607 finder must be unique (partial-revert teeth)")


class WClauseHourlyAndOwnerDecision(unittest.TestCase):
    """`_W_CLAUSE` names the hourly thread-check (part 1) + owner-decision
    mis-shape (part 4)."""

    def test_w_clause_names_hourly_thread_check(self):
        self.assertIn("1×/hodinu", owr._W_CLAUSE)

    def test_w_clause_names_owner_decision_misshape(self):
        c = owr._W_CLAUSE
        # the owner ANSWER/DECISION variant (distinct from the #601 physical-step
        # variant already present) -> U with a delivered question
        self.assertIn("ROZHODNUTIE", c)
        self.assertIn("needs-answer", c)
        self.assertIn("needs-decision", c)
        self.assertIn("DORUČ", c)          # deliver the question ("na U sa vždy pýtam")

    def test_owner_decision_rendered_in_nudge_text(self):
        # a W-only nudge carries the owner-decision mis-shape instruction
        t = owr._nudge_text(None, [41], now=1000.0, w_seen={"41": 1000.0})
        self.assertIn("ROZHODNUTIE", t)


class WStaleClauseEscalation(unittest.TestCase):
    """`_W_STALE_CLAUSE` escalates tag -> SEND a reminder into the thread (part 2)."""

    def test_stale_clause_escalates_to_send_into_thread(self):
        c = owr._W_STALE_CLAUSE
        self.assertIn("MUSÍ", c)           # a reminder MUST be sent
        self.assertIn("vlákna", c)         # into the client Discuss thread
        self.assertIn("pracovných", c)     # 24h working-day window
        # existing #570 assertions must survive (NudgeNamesStaleMembers locks these)
        self.assertIn("DNES", c)

    def test_stale_subclause_still_fires_in_nudge(self):
        members = [{"number": 41, "stale": True}]
        t = owr._nudge_text(None, members, now=1000.0, w_seen={"41": 1000.0})
        self.assertIn("STALE", t)
        self.assertIn("#41", t)


class ModuleDocstringDefersHourlyVerification(unittest.TestCase):
    """The module docstring records the #607 hourly-check deferral (mirrors the
    #550 phase-2 defer shape) with named reopen triggers."""

    def test_docstring_carries_607_hourly_defer(self):
        doc = owr.__doc__ or ""
        self.assertIn("#607", doc)
        self.assertIn("hodinov", doc.lower())     # hourly thread-check named
        self.assertIn("REOPEN", doc.upper())      # named reopen triggers present


if __name__ == "__main__":
    unittest.main()
