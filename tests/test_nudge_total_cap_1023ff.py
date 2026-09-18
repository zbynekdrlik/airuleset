"""#1023 fix-forward (integration finding, 2026-09-14) — restore the cross-kind
TOTAL cap that the #1023 lane removed.

The #1023 lane deleted the #913 cross-kind family gap (`NUDGE_FAMILY_GAP_S`/
`_family_gap`) on the inference that per-kind staging bounds the total. That is
not the owner's word (#913, 2026-09-06, verbatim): "nikdy viac ako raz za
hodinu!!!! a ani iny nudge do promptu!!!" = at most ONE machine nudge per pane
per hour in TOTAL, across kinds. With two kinds staged on, two priority nudges
could reach one pane inside the hour.

This restores the total cap as a SHARED predicate used by BOTH decision sites:
  - `gate_ok` (the individual-rider path): a PRIORITY nudge of ANY kind is held
    while ANY OTHER priority kind was delivered within `NUDGE_TOTAL_GAP_S`.
  - `batch_eligible` (the primary batch path, driven from goal.py's sweep):
    returns [] while the total cap is closed, so a second batch never leaks a
    second interruption inside the hour.
The per-pane-per-KIND 60-min floor (#1023) is kept exactly as shipped. RECOVERY
identities (resume/compact) are exempt from BOTH the floor and the total cap and
never count as "another kind delivered".

RED against the merged tree (v0.1.278): `NUDGE_TOTAL_GAP_S`/`_total_gap` do not
exist and the total cap is not enforced. GREEN once the predicate lands.
"""
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import nudge_gate as ng  # noqa: E402
from watchdog import tmux_io as tio    # noqa: E402

NOW = 1_000_000
HOUR = 3600
MIN = 60


class TestTotalGapConstant(unittest.TestCase):
    def test_total_gap_default_is_three_hours(self):
        # Owner decision 2026-09-17 07:1x CEST ("3" on #1023): the cross-kind
        # total cap is 3 h per pane EVERYWHERE (gk has two Fable panes, so the
        # former 1 h read as two interruptions an hour). The hard floor moves
        # with it: env can only RAISE above 3 h.
        self.assertEqual(ng.NUDGE_TOTAL_GAP_S, 3 * HOUR)
        self.assertEqual(ng.NUDGE_TOTAL_GAP_MIN_S, 3 * HOUR)

    def test_total_gap_env_can_only_raise(self):
        # #504/#543 floor-clamp: the env override can RAISE but never lower the
        # owner's hard 3 h total cap (2026-09-17).
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_TOTAL_GAP_S": "5"}):
            self.assertEqual(ng._total_gap(), 3 * HOUR)
        with m.patch.dict(os.environ,
                          {"AIRULESET_NUDGE_TOTAL_GAP_S": str(4 * HOUR)}):
            self.assertEqual(ng._total_gap(), 4 * HOUR)

    def test_total_gap_garbage_env_falls_back(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_NUDGE_TOTAL_GAP_S": "not-a-number"}):
            self.assertEqual(ng._total_gap(), 3 * HOUR)


class TestGateOkTotalCap(unittest.TestCase):
    """Dispatch RED cases (1)/(2)/(4)."""

    def test_case1_other_kind_held_by_total_cap_then_allowed(self):
        # (1) kind A at t, kind B eligible at t+45m -> held; at t+61m -> STILL
        # held (the owner's 3 h total cap, 2026-09-17); at t+181m -> allowed.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)          # kind A
        self.assertFalse(ng.gate_ok(st, "s", "release-gap", NOW + 45 * MIN),
                         "a DIFFERENT kind within the total cap must be HELD")
        self.assertFalse(ng.gate_ok(st, "s", "release-gap", NOW + 61 * MIN),
                         "1 h is inside the 3 h total cap — still HELD")
        self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW + 181 * MIN),
                        "past the 3 h total gap the other kind is allowed")

    def test_case2_same_kind_is_floor_not_total_cap(self):
        # (2) kind A at t, kind A at t+45m -> held by the per-kind FLOOR
        # (unchanged #1023 behaviour), NOT the total cap.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "s", "queue-arrival", NOW + 45 * MIN))
        reason = ng.floor_hold_reason(st, "s", "queue-arrival", NOW + 45 * MIN)
        self.assertIn("hold:floor", reason)
        self.assertNotIn("hold:total-cap", reason)

    def test_case4_env_override_cannot_lower_below_one_hour(self):
        # (4) the env override cannot lower the total cap below 3600 -> a kind at
        # t+45m is STILL held even with the override set to 5s.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        with m.patch.dict(os.environ, {"AIRULESET_NUDGE_TOTAL_GAP_S": "5"}):
            self.assertFalse(ng.gate_ok(st, "s", "release-gap", NOW + 45 * MIN),
                             "env cannot lower the total cap below 3 h")

    def test_env_raise_extends_the_total_cap(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        with m.patch.dict(os.environ,
                          {"AIRULESET_NUDGE_TOTAL_GAP_S": str(4 * HOUR)}):
            self.assertFalse(ng.gate_ok(st, "s", "release-gap",
                                        NOW + 3 * HOUR + 30 * MIN))
            self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW + 4 * HOUR))

    def test_empty_state_allows(self):
        self.assertTrue(ng.gate_ok({}, "s", "release-gap", NOW))

    def test_total_cap_is_per_session(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        # a DIFFERENT session is unaffected by the total cap.
        self.assertTrue(ng.gate_ok(st, "other", "release-gap", NOW + 45 * MIN))

    def test_future_skew_other_kind_ignored_by_total_cap(self):
        # a future/corrupt ts of another kind must not mute this kind (fail-safe).
        st = {"nudge_cadence": {"s": {"u-freshness": NOW + 10 ** 9}}}
        self.assertTrue(ng.gate_ok(st, "s", "release-gap", NOW))


class TestRecoveryExempt(unittest.TestCase):
    """Dispatch RED case (3): recovery revivals are exempt from both bounds."""

    def test_recovery_kinds_defined_and_mirror_tmux_io(self):
        # nudge_gate stays a leaf module (no watchdog import); a drift-lock keeps
        # its recovery set identical to tmux_io's canonical one. #1038 adds
        # `goal-arm` (a DECLARED managed window's post-reboot arm = a session
        # revival, the same class as resume/compact). #1034 adds `wake-parked`
        # (waking a session parked on the usage-limit auto-continue banner after
        # a claudy account switch — the same session-revival class). #1063 adds
        # `goal-disarm` (the `/goal clear` #522 question-repoke backstop — a
        # damage-control action exempt from the switch/floor/total cap).
        self.assertEqual(
            ng.RECOVERY_NUDGE_KINDS,
            frozenset({"resume", "compact", "goal-arm", "wake-parked",
                       "goal-disarm"}))
        self.assertEqual(ng.RECOVERY_NUDGE_KINDS, tio.RECOVERY_NUDGE_KINDS)

    def test_recovery_does_not_count_for_the_total_cap(self):
        # (3a) a recovery 'resume' at t, kind A at t+5m -> A is ALLOWED (recovery
        # never counts as "another kind delivered").
        st = {}
        ng.mark_sent(st, "s", "resume", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "queue-arrival", NOW + 5 * MIN),
                        "a recovery delivery must NOT trigger the total cap")

    def test_recovery_kind_never_held_by_the_cap_or_floor(self):
        # (3b) a recovery resume is never held by the cap even when a priority
        # kind was just delivered, and never by its own floor.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "resume", NOW + 1 * MIN))
        ng.mark_sent(st, "s", "resume", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "resume", NOW + 1 * MIN))


class TestFloorHoldReasonToken(unittest.TestCase):
    def test_total_cap_reason_names_the_other_kind_and_minutes(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        reason = ng.floor_hold_reason(st, "s", "release-gap", NOW + 45 * MIN)
        self.assertIn("hold:total-cap", reason)
        self.assertIn("queue-arrival", reason)   # the blocking OTHER kind
        self.assertIn("45", reason)              # minutes since it was delivered
        self.assertNotIn("hold:floor", reason)

    def test_floor_reason_names_this_kind(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        reason = ng.floor_hold_reason(st, "s", "queue-arrival", NOW + 45 * MIN)
        self.assertIn("hold:floor", reason)
        self.assertIn("queue-arrival", reason)


class TestBatchEligibleTotalCap(unittest.TestCase):
    """The batch path (the primary delivery path) must respect the total cap —
    a recent priority delivery blocks a NEW batch this hour, else all
    floor-eligible kinds compose into ONE keystroke (the shared interruption)."""

    def test_recent_delivery_blocks_the_whole_batch(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        # 30 min later: the total cap is closed -> NO batch (was: other kinds
        # still eligible, the exact two-nudges-per-hour hole).
        self.assertEqual(ng.batch_eligible(st, "s", NOW + 30 * MIN), [])

    def test_batch_opens_when_total_cap_expires(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertEqual(ng.batch_eligible(st, "s", NOW + HOUR), [])   # 3 h cap
        result = ng.batch_eligible(st, "s", NOW + 3 * HOUR)
        self.assertGreater(len(result), 0)
        self.assertIn("queue-arrival", result)   # its own floor expired long ago
        self.assertIn("partition-audit", result)

    def test_empty_state_all_eligible(self):
        self.assertEqual(len(ng.batch_eligible({}, "s", NOW)),
                         len(ng.GATED_CATEGORIES))

    def test_recovery_delivery_does_not_block_a_batch(self):
        st = {}
        ng.mark_sent(st, "s", "resume", NOW)   # recovery — never counts
        self.assertEqual(len(ng.batch_eligible(st, "s", NOW + 30 * MIN)),
                         len(ng.GATED_CATEGORIES))


class TestSameKindCountsInTotalCap(unittest.TestCase):
    """#1023 fix-forward 2 (owner "3", 2026-09-17; live evidence gk-infra pane
    `zbynek:1.0`, 2026-09-18): the 3 h total cap must count the SAME kind too.

    Before this fix `_total_cap_block` skipped the deciding kind, so the cap only
    fired ACROSS different kinds. A pane on which ONE kind dominates (gk-infra:
    `queue-arrival` is the only kind whose condition keeps re-arising) was bounded
    solely by the 60-min per-kind floor and delivered up to once an hour —
    contradicting the owner's ruling ("at most ONE priority nudge per pane per
    3 h in TOTAL, across ALL kinds"). Live proof: `stuck-check: infra queue
    arrival` records at 09:48 / 16:03 / 18:36 / 21:35 CEST (gaps 2 h 33 min and
    2 h 59 min) plus a 20:34 attempt — three interruptions in 5.5 h.

    Now the deciding kind's OWN last send closes the cap; recovery kinds stay
    exempt. The per-kind floor still fires FIRST for the sub-60-min band (the
    honest `hold:floor` message), so the total cap only relabels the 60-179 min
    band as `hold:total-cap`.
    """

    def test_same_kind_held_by_total_cap_at_150min(self):
        # RED against the merged tree: 150 min > the 60-min floor, so the floor
        # passes; the total cap USED to skip queue-arrival -> gate_ok True. It
        # must now be HELD, with the existing hold:total-cap string naming itself.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "s", "queue-arrival", NOW + 150 * MIN),
                         "same kind within the 3 h total cap must be HELD")
        reason = ng.floor_hold_reason(st, "s", "queue-arrival", NOW + 150 * MIN)
        self.assertEqual(
            reason, "hold:total-cap (queue-arrival delivered 150 min ago)")

    def test_same_kind_allowed_past_the_total_cap_at_181min(self):
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "queue-arrival", NOW + 181 * MIN),
                        "past the 3 h total cap the same kind is allowed again")

    def test_same_kind_boundary_179_held_180_allowed(self):
        # RED: 179 min was allowed under the old skip; it must now be held. 180 min
        # is exactly the gap (now - ts < gap is False) -> allowed on both sides.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertFalse(ng.gate_ok(st, "s", "queue-arrival", NOW + 179 * MIN))
        self.assertTrue(ng.gate_ok(st, "s", "queue-arrival", NOW + 180 * MIN))

    def test_same_kind_within_floor_still_reads_as_floor(self):
        # the per-kind floor still fires FIRST for the sub-60-min window, so the
        # journal keeps the honest 'hold:floor (<kind>, N min since last send)'
        # for a same-kind repeat inside the hour (the total cap only relabels the
        # 60-179 min band).
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        reason = ng.floor_hold_reason(st, "s", "queue-arrival", NOW + 45 * MIN)
        self.assertIn("hold:floor", reason)
        self.assertNotIn("hold:total-cap", reason)

    def test_recovery_same_kind_still_exempt_from_the_total_cap(self):
        # a recovery kind is never held by the cap, even its own repeat at 150 min
        # (a revival is not a prompt interruption).
        st = {}
        ng.mark_sent(st, "s", "resume", NOW)
        self.assertTrue(ng.gate_ok(st, "s", "resume", NOW + 150 * MIN))

    def test_batch_same_kind_only_still_blocked_by_the_cap(self):
        # the batch path already passed exclude_category=None (never holed), but
        # lock that a lone queue-arrival delivery keeps a NEW batch closed for the
        # full 3 h, not just the 60-min floor.
        st = {}
        ng.mark_sent(st, "s", "queue-arrival", NOW)
        self.assertEqual(ng.batch_eligible(st, "s", NOW + 150 * MIN), [])
        self.assertGreater(len(ng.batch_eligible(st, "s", NOW + 181 * MIN)), 0)


if __name__ == "__main__":
    unittest.main()
