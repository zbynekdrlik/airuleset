"""#607 časti 1 + 4 (+ časť 2 nudge escalation) — job-20 W-clause content + fleet rule.

Part 1 (hodinový thread-check): the fleet W-doctrine (statusline-vocabulary.md)
+ the job-20 `_W_TRIGGER` name the ≥1×/hodinu Discuss-thread-read duty (#714:
the full doctrine moved to the modules, the compact trigger carries the #607
pointer); the watchdog cannot verify Discuss reads itself (#550), so the module
docstring DEFERS the hourly-granularity verification with named reopen triggers,
and the observable proxy is the weekend-aware `stale!` (part 2).

Part 2 escalation (#714: now a compact `STALE` flag count + #607 pointer + DNES,
the members in `slice-quals --ops-wait`): the `stale!` tag surfaces "a
substantive reminder MUST be SENT into the client thread TODAY" (working-day 24h
passed) — the full doctrine lives in the session's modules.

Part 4 (W-hides-owner-decision): the `_W_TRIGGER` names the owner mis-shape —
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
    """#714: the full W→I doctrine (ROZHODNUTIE/DORUČ/hourly-check) now lives in
    the session's MODULES (statusline-vocabulary.md, content-locked by the
    Status* tests above); the compact `_W_TRIGGER` carries the TRIGGER — the
    hourly-check pointer (#607, 1×/hod) + the owner mis-shape label (#601)."""

    def test_w_trigger_names_hourly_thread_check(self):
        self.assertIn("1×/hod", owr._W_TRIGGER)
        self.assertIn("#607", owr._W_TRIGGER)

    def test_w_trigger_names_owner_misshape_pointer(self):
        c = owr._W_TRIGGER
        self.assertIn("needs-owner-action", c)   # owner-blocked -> U #601
        self.assertIn("#601", c)

    def test_owner_misshape_rendered_in_nudge_text(self):
        # a W-only nudge carries the owner mis-shape re-label pointer
        t = owr._nudge_text(None, [41], now=1000.0)
        self.assertIn("needs-owner-action", t)
        self.assertIn("#601", t)


class WStaleClauseEscalation(unittest.TestCase):
    """#714: the STALE escalation is a compact flag (count + #607 pointer +
    DNES) — the full 'send a reminder into the thread' doctrine lives in the
    session's modules; the tag still SURFACES in the nudge as a count."""

    def test_stale_flag_fires_in_nudge_with_pointer(self):
        members = [{"number": 41, "stale": True}]
        t = owr._nudge_text(None, members, now=1000.0)
        self.assertIn("STALE", t)          # the flag fires
        self.assertIn("#607", t)           # the 24h working-day contract pointer
        self.assertIn("DNES", t)           # act today

    def test_no_stale_flag_when_none_stale(self):
        members = [{"number": 41, "stale": False}]
        t = owr._nudge_text(None, members, now=1000.0)
        self.assertNotIn("STALE", t)
        self.assertIn("W=1", t)            # #714 compact count, no member name


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
