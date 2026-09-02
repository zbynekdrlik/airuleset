"""Locks the RETIREMENT of the #730 RE-DERIVABLE-waiter waiver (#848).

The #730 waiver existed to `TaskStop`-then-relaunch a live CI/release/deploy/lock
waiter so a batch boundary reached the fully-idle state the OLD live-tasks veto
required before it would deliver the drained-boundary compact (owner incident
2026-08-26: gk `/autopilot-master` crossed three batches with ZERO compacts
because a release-lane waiter spanned every drained boundary). #848 REMOVED that
veto outright — the STEP-0 experiment (CC 2.1.258) proved a `/compact` over live
worktree lanes + a bg-bash waiter + an armed `/goal` does NOT break the task
registry — so the compact delivers at EVERY integration cycle regardless of a
live waiter, and the whole TaskStop / relaunch dance is moot.

These are content-locks (flipped from the #730 positive-protocol locks,
flip-never-delete, #723 lesson): the waiver PROTOCOL phrases must be GONE from
both skills, and the RETIREMENT + continuous-refill compact must be stated. The
LANE 2 durable-anchor discipline is RETAINED (a lost completion notification is
still recovered from the anchor after a compaction) — now under #844/#848, not
#730's TaskStop waiver.

Assertions use a whitespace-NORMALIZED haystack/needle (the repo's own `_norm`
idiom) so a re-wrap of the prose cannot silently break the locks.
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


class TestAutopilotSkillWaiverIsRetired(TestCase):
    """skills/autopilot/SKILL.md Step 5 states the #730 waiver is RETIRED and
    the compact delivers over live lanes — the TaskStop/relaunch protocol is gone."""

    def test_retirement_is_stated(self):
        body = norm(read(SKILL))
        self.assertIn(norm("The #730 RE-DERIVABLE-WAITER WAIVER is RETIRED (#848)"), body)
        self.assertIn("#848", body)

    def test_the_taskstop_relaunch_protocol_is_gone(self):
        body = norm(read(SKILL))
        self.assertNotIn(norm("`TaskStop` those waiters DELIBERATELY"), body)
        self.assertNotIn(norm("RELAUNCH each waiter fresh from its durable anchor"), body)
        self.assertNotIn(norm("Run `compact-request --self` on this now genuinely-drained boundary"), body)

    def test_worker_lanes_no_longer_drained_before_the_compact(self):
        body = norm(read(SKILL))
        self.assertNotIn(norm(
            "Worker LANES get NO waiver — they are drained exactly as today"), body)
        self.assertIn(norm("Worker lanes, likewise, are no longer drained before the compact"), body)

    def test_the_batch_boundary_crossing_rule_is_gone(self):
        body = norm(read(SKILL))
        self.assertNotIn(norm(
            "A drained batch boundary must NEVER be crossed into the next batch"), body)

    def test_compact_delivers_over_live_lanes(self):
        body = norm(read(SKILL))
        self.assertIn(norm("compact over live lanes is safe"), body)
        # the old "veto NOT touched / CC #29193 respected" framing is gone
        self.assertNotIn(norm("live-tasks veto itself is NOT touched"), body)


class TestAutopilotMasterWaiverIsRetired(TestCase):
    """skills/autopilot-master/SKILL.md states the same retirement, and the
    /goal line + COMPACT BOUNDARY prose carry the continuous-refill compact."""

    def _master_goal_line(self):
        text = read(SKILL_MASTER)
        lines = re.findall(r"^/goal MASTER LOOP.*$", text, re.MULTILINE)
        self.assertEqual(len(lines), 1, "expected exactly one master /goal line")
        return lines[0]

    def test_goal_line_compacts_every_cycle_not_at_a_drain_window(self):
        line = self._master_goal_line()
        self.assertIn("compact-request --self", line)
        self.assertIn("#848", line)
        self.assertNotIn("(waiver #730)", line)
        self.assertNotIn("DRAIN WINDOW", line)
        self.assertNotIn("BATCH+COMPACT", line)

    def test_goal_line_stays_within_the_4000_char_cap_with_healthy_headroom(self):
        line = self._master_goal_line()
        headroom = 4000 - len(line)
        self.assertGreaterEqual(headroom, 120,
                                "master /goal line headroom %d < 120" % headroom)

    def _compact_boundary_window(self):
        body = read(SKILL_MASTER)
        idx = body.index("**COMPACT BOUNDARY (#848):**")
        end = body.index("- **LANE 4 QUESTIONS**")
        self.assertGreater(end, idx)
        return body[idx:end]

    def test_compact_boundary_is_every_cycle(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm("EVERY LANE 3 integration cycle"), window)
        self.assertIn(norm("live lanes or not"), window)

    def test_the_taskstop_relaunch_protocol_is_gone_from_master(self):
        window = norm(self._compact_boundary_window())
        self.assertIn(norm("The #730 re-derivable-waiter waiver is RETIRED (#848)"), window)
        self.assertNotIn(norm("`TaskStop` it DELIBERATELY"), window)
        self.assertNotIn(norm("RELAUNCH the waiter fresh"), window)
        self.assertNotIn(norm("live-tasks veto itself is NOT touched"), window)


class TestAutopilotMasterLane2KeepsDurableAnchorsContinuously(TestCase):
    """RETAINED (now #844/#848): the release lane must continuously keep its
    durable anchors noted (not just at hand-off) so a lost completion
    notification is recovered from the anchor after a compaction."""

    def _lane2_window(self):
        body = read(SKILL_MASTER)
        idx = body.index("- **LANE 2 RELEASE**")
        end = body.index("- **LANE 3 CORE**")
        self.assertGreater(end, idx)
        return body[idx:end]

    def test_lane2_names_durable_anchor_discipline(self):
        window = norm(self._lane2_window())
        self.assertIn(norm("Durable anchors, continuously kept"), window)
        self.assertIn("run-id", window)
        self.assertIn(norm("promotion ticket"), window)
        self.assertIn(norm("lock target"), window)

    def test_lane2_anchor_discipline_is_ongoing_not_only_at_the_end(self):
        window = norm(self._lane2_window())
        self.assertIn(norm("AS THE RELEASE PROGRESSES"), window)


if __name__ == "__main__":
    main()
