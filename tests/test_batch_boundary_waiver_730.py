"""Locks the #730 RE-DERIVABLE-waiter waiver on the #723/#724 batch-boundary
compact doctrine.

Incident (owner report, 2026-08-26, verbatim): gk `/autopilot-master` crossed
"davka 1 -> drain -> davka 2 -> drain -> davka 3 BEZ jedineho compactu" -- on
every drained batch boundary at least one live LANE 2 release-lane waiter
(shadow rerun -> deploy -> slovnormal lock-retry) was still running, and a
compact with live background tasks is correctly forbidden (CC #29193,
`watchdog/compact.py`'s live-tasks veto). The #723 batch doctrine defines the
compact boundary as "zero live tasks", but never answered what happens when a
background waiter is CROSS-BATCH by nature (spans every boundary) -- so the
boundary genuinely never arrived and the loop ran a whole batch with zero
compacts. gk behaved doctrinally CORRECTLY; the doctrine had the gap.

The fix (Fable design phase, ROZHODNUTE) is a WAIVER, not a veto change: a
background task earns the waiver ONLY as a RE-DERIVABLE WAITER (its entire
state lives in a durable, externally-readable resource -- a `gh run id`, a
release/promotion ticket, a lock target -- exactly the shape `ci-monitoring.md`
already tells you to re-derive from after any compaction). At a drained
boundary where the only live background tasks are re-derivable waiters, the
supervisor (1) records the durable anchor(s), (2) `TaskStop`s them
DELIBERATELY, (3) runs `compact-request --self` on the now genuinely-drained
boundary (the live-tasks veto in `watchdog/compact.py` is UNCHANGED -- CC
#29193 is respected literally, the boundary is real zero because the waiters
were stopped, not because the check was loosened), (4) relaunches each waiter
fresh from its durable anchor after the compact. Worker LANES get NO waiver --
they are drained exactly as before. A drained boundary must never be crossed
into the next batch uncompacted just because a re-derivable waiter spans it.

This is a docs-only ticket (no watchdog/compact.py change -- that file belongs
to the separate #727 lane). These are content-locks (the
`test_batch_orchestration.py` pattern) over `skills/autopilot/SKILL.md` and
`skills/autopilot-master/SKILL.md` -- a NEW, dedicated file so it never
collides with the parallel #723/#727 lanes that own
`tests/test_batch_orchestration.py` / `tests/test_compact.py`.

Assertions use a whitespace-NORMALIZED haystack/needle (the repo's own
`_norm` idiom, e.g. `tests/test_no_question_flag.py`) so a future re-wrap of
the prose (line-width changes) cannot silently break these locks the way a
literal-newline substring match would.
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SKILL = "skills/autopilot/SKILL.md"
SKILL_MASTER = "skills/autopilot-master/SKILL.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def norm(text):
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# skills/autopilot/SKILL.md -- the canonical waiver protocol (Step 5)
# --------------------------------------------------------------------------- #

class TestAutopilotSkillCarriesTheWaiverProtocol(TestCase):
    """The Step-5 batch-boundary compact section states the full #730 waiver
    protocol: durable anchors -> TaskStop -> compact-request --self -> relaunch."""

    def test_waiver_is_named_and_dated(self):
        body = norm(read(SKILL))
        self.assertIn("#730", body)
        self.assertIn("2026-08-26", body)
        self.assertIn(norm(
            "gk `/autopilot-master` crossed batch 1 → drain → batch 2 → drain → "
            "batch 3 with ZERO compacts"), body)

    def test_re_derivable_waiter_is_defined_by_a_durable_anchor(self):
        body = norm(read(SKILL))
        self.assertIn(norm("RE-DERIVABLE WAITER"), body)
        self.assertIn(norm("durable, externally-readable resource"), body)
        self.assertIn(norm("a `gh run id`"), body)

    def test_the_taskstop_compact_relaunch_sequence_is_stated(self):
        body = norm(read(SKILL))
        # step 1: record the durable anchor
        self.assertIn(norm("Record the durable anchor(s)"), body)
        # step 2: TaskStop deliberately
        self.assertIn(norm("`TaskStop` those waiters DELIBERATELY"), body)
        # step 3: compact-request --self on the now-drained boundary
        self.assertIn(
            norm("Run `compact-request --self` on this now genuinely-drained boundary"),
            body)
        # step 4: relaunch from the durable anchor
        self.assertIn(norm("RELAUNCH each waiter fresh from its durable anchor"), body)

    def test_compact_py_veto_is_explicitly_not_touched(self):
        body = norm(read(SKILL))
        self.assertIn(norm(
            "`watchdog/compact.py`'s live-tasks veto itself is NOT touched, "
            "not by one line: CC #29193"), body)

    def test_worker_lanes_get_no_waiver(self):
        body = norm(read(SKILL))
        self.assertIn(norm(
            "Worker LANES get NO waiver — they are drained exactly as today, "
            "no exception."), body)

    def test_a_boundary_must_never_be_crossed_uncompacted_for_a_spanning_waiter(self):
        body = norm(read(SKILL))
        self.assertIn(norm(
            "A drained batch boundary must NEVER be crossed into the next batch "
            "uncompacted just because a re-derivable watch/poll waiter spans it"), body)


class TestAutopilotSkillOtherWaiterMentionsCarryTheWaiver(TestCase):
    """The two OTHER spots in skills/autopilot/SKILL.md that used to read as
    an unconditional "an in-flight CI waiter blocks the boundary forever" now
    point at the #730 waiver instead of contradicting it (ticket item 3:
    grep + update ALL occurrences consistently)."""

    def test_batch_cap_section_names_the_waiver(self):
        raw = read(SKILL)
        idx = raw.index("**Batch cap — up to 5 lanes per batch")
        window = norm(raw[idx:idx + 2200])
        self.assertIn(norm("in-flight integration CI waiter"), window)
        self.assertIn(norm("#730 waiver"), window)
        self.assertIn(norm("never lets a boundary sit uncompacted indefinitely"), window)

    def test_batch_dispatch_section_names_the_waiver(self):
        raw = read(SKILL)
        idx = raw.index("**Batch dispatch — up to 5 lanes, NO refill")
        window = norm(raw[idx:idx + 2200])
        self.assertIn(norm("no in-flight integration CI waiter"), window)
        self.assertIn(norm("#730 waiver"), window)
        self.assertIn(norm("worker lanes get NO such waiver"), window)

    def test_old_unconditional_batch_dispatch_phrasing_is_gone(self):
        # the pre-#730 phrasing asserted the waiter unconditionally blocked
        # the boundary -- the fixed sentence must no longer read this way
        # (a re-derivable waiter is now covered by the waiver, so "waiter"
        # and "the main session compacts" must no longer sit immediately
        # adjacent with nothing qualifying them in between).
        body = norm(read(SKILL))
        self.assertNotIn(
            norm("no in-flight integration CI waiter — the main session compacts"),
            body)

    def test_old_unconditional_batch_cap_phrasing_is_gone(self):
        body = norm(read(SKILL))
        self.assertNotIn(norm("open until it lands. What a rate-limit signal"), body)


# --------------------------------------------------------------------------- #
# skills/autopilot-master/SKILL.md -- LANE 2 release-lane doctrine
# --------------------------------------------------------------------------- #

class TestAutopilotMasterGoalLineNamesTheWaiver(TestCase):
    """The literal /goal MASTER LOOP condition (the text that caused the live
    incident) points at the #730 waiver instead of an unconditional wait."""

    def _master_goal_line(self):
        text = read(SKILL_MASTER)
        lines = re.findall(r"^/goal MASTER LOOP.*$", text, re.MULTILINE)
        self.assertEqual(len(lines), 1, "expected exactly one master /goal line")
        return lines[0]

    def test_goal_line_names_the_waiver(self):
        line = self._master_goal_line()
        self.assertIn("(waiver #730)", line)

    def test_goal_line_still_carries_the_batch_compact_clause(self):
        line = self._master_goal_line()
        self.assertIn("BATCH+COMPACT", line)
        self.assertIn("DRAIN WINDOW", line)
        self.assertIn("compact-request --self", line)

    def test_goal_line_stays_within_the_4000_char_cap_with_healthy_headroom(self):
        # mirrors tests/test_goal_backlog_proof.py's own MIN_MASTER_HEADROOM=120
        # -- re-asserted here so THIS ticket's own edit is locked against
        # future erosion too, not just the shared sibling lock.
        line = self._master_goal_line()
        cap = 4000
        headroom = cap - len(line)
        self.assertGreaterEqual(headroom, 120,
                                 "master /goal line headroom %d < 120" % headroom)

    def test_old_unwaivered_goal_line_phrasing_is_gone(self):
        text = read(SKILL_MASTER)
        self.assertNotIn(
            "ZERO live background subagents/Bash remain, then `compact-request --self`",
            text)


class TestAutopilotMasterCompactBoundaryProseCarriesTheWaiver(TestCase):
    """The COMPACT BOUNDARY (#724) prose paragraph -- the non-char-capped
    Step-3 documentation the loop actually consults -- states the FULL #730
    waiver protocol for LANE 2's release waiter."""

    def _compact_boundary_window(self):
        body = read(SKILL_MASTER)
        idx = body.index("**COMPACT BOUNDARY (#724):**")
        end = body.index("- **LANE 4 QUESTIONS**")
        self.assertGreater(end, idx)
        return body[idx:end]

    def test_incident_is_cited(self):
        window = norm(self._compact_boundary_window())
        self.assertIn("#730", window)
        self.assertIn("2026-08-26", window)
        self.assertIn(
            norm("crossed batch 1 → drain → batch 2 → drain → batch 3"), window)

    def test_re_derivable_waiter_is_defined(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm("RE-DERIVABLE WAITER"), window)
        self.assertIn(norm("durable, externally-readable resource"), window)

    def test_taskstop_compact_relaunch_sequence_is_stated(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm("record its durable anchor"), window)
        self.assertIn(norm("`TaskStop` it DELIBERATELY"), window)
        self.assertIn(norm("run `compact-request --self`"), window)
        self.assertIn(norm("RELAUNCH the waiter fresh"), window)

    def test_compact_py_veto_is_explicitly_not_touched(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm(
            "`watchdog/compact.py`'s live-tasks veto itself is NOT touched, "
            "CC #29193 is still respected LITERALLY"), window)

    def test_worker_lanes_get_no_waiver(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm(
            "Worker LANES get NO waiver — they are drained exactly as today, "
            "no exception."), window)

    def test_a_boundary_must_never_be_crossed_uncompacted_for_a_spanning_waiter(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm(
            "A drained batch boundary must NEVER be crossed into the next batch "
            "uncompacted just because a re-derivable watch/poll waiter spans it"), window)

    def test_the_any_ci_waiter_sentence_now_points_at_the_waiver(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm(
            "any CI waiter — a RE-DERIVABLE one is `TaskStop`ped first per the "
            "#730 waiver"), window)

    def test_old_unqualified_any_ci_waiter_phrasing_is_gone(self):
        # pre-#730: "(any CI waiter)" with nothing qualifying it -- the fixed
        # text must no longer close the parenthetical right there.
        window = self._compact_boundary_window()
        self.assertNotIn("(any CI waiter)**", window)


class TestAutopilotMasterLane2KeepsDurableAnchorsContinuously(TestCase):
    """Ticket item 2: the release lane must continuously keep its durable
    anchors noted (not just at hand-off) so a #730 waiver relaunch is
    trivial -- stated inside LANE 2's own bullet, not just cross-referenced
    from the compact-boundary paragraph."""

    def _lane2_window(self):
        body = read(SKILL_MASTER)
        idx = body.index("- **LANE 2 RELEASE**")
        end = body.index("- **LANE 3 CORE**")
        self.assertGreater(end, idx)
        return body[idx:end]

    def test_lane2_names_durable_anchor_discipline(self):
        window = norm(self._lane2_window())
        self.assertIn("#730", window)
        self.assertIn(norm("Durable anchors, continuously kept"), window)
        self.assertIn("run-id", window)
        self.assertIn(norm("promotion ticket"), window)
        self.assertIn(norm("lock target"), window)

    def test_lane2_anchor_discipline_is_ongoing_not_only_at_the_end(self):
        window = norm(self._lane2_window())
        self.assertIn(norm("AS THE RELEASE PROGRESSES"), window)


if __name__ == "__main__":
    main()
