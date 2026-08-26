"""#574 — evidence-based recalibration of the lane-fill memory gate.

Locks the four deliverables of #574:
  1. a CALL-TIME env override `AIRULESET_LANE_MIN_MEM_MB` on the lane-fill
     memory floor (never frozen at import — #545), malformed/non-positive
     falling back to the default;
  2. an evidence-based DEFAULT lowered from the uncalibrated #442 1536 to
     1024 (so gk's historically-working 5-lane state at ~1.2GB MemAvailable
     passes the fill gate);
  3. the `skip:low-mem` + `CAPACITY-CAPPED` messages printing the EFFECTIVE
     threshold, not a hardcoded 1536;
  4. the api-watchdog systemd unit carrying an optional per-box
     `EnvironmentFile` so the override is reachable by the timer's env.

The goal.py gate/message tests reuse the `TestGoalLaneOccupancyNudge`
harness shape (shared `_goal_arm_helpers`), driving the REAL
`goal_lane_occupancy_nudge`.
"""

import os
import unittest
import unittest.mock as m
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))          # tests/

import airuleset                                        # noqa: E402
from watchdog import goal                               # noqa: E402


class LaneMinMemEffectiveThreshold(unittest.TestCase):
    """`_lane_min_mem_avail_mb()` — the effective lane-fill memory floor:
    env `AIRULESET_LANE_MIN_MEM_MB` overrides the recalibrated default,
    read at CALL time, malformed/non-positive falling back."""

    ENV = "AIRULESET_LANE_MIN_MEM_MB"

    def setUp(self):
        # Never let a real box env leak into the pure-unit assertions.
        self._saved = os.environ.pop(self.ENV, None)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop(self.ENV, None)
        else:
            os.environ[self.ENV] = self._saved

    def test_default_is_recalibrated_1024(self):
        # #574: lowered from the uncalibrated #442 1536 (born c703967d,
        # zero cited OOM evidence) so gk's historically-working 5-lane
        # state (~1.2GB MemAvailable) passes the under-saturated fill gate.
        self.assertEqual(goal.GOAL_LANE_MIN_MEM_AVAIL_MB, 1024)

    def test_no_env_returns_default(self):
        self.assertEqual(goal._lane_min_mem_avail_mb(),
                         goal.GOAL_LANE_MIN_MEM_AVAIL_MB)

    def test_env_override_read_at_call_time(self):
        with m.patch.dict(os.environ, {self.ENV: "1400"}):
            self.assertEqual(goal._lane_min_mem_avail_mb(), 1400)
        # unset -> back to default, PROVING the read is at call time, not a
        # value cached at import (#545: an import-time env constant fires on
        # every airuleset invocation incl. the 60s watchdog and cannot be
        # per-box overridden via the unit EnvironmentFile).
        self.assertEqual(goal._lane_min_mem_avail_mb(),
                         goal.GOAL_LANE_MIN_MEM_AVAIL_MB)

    def test_two_different_envs_two_different_results(self):
        # A mutant that froze the value at import (`_X = _lane_min_...()`)
        # would return the SAME number for both — this fails it.
        with m.patch.dict(os.environ, {self.ENV: "900"}):
            a = goal._lane_min_mem_avail_mb()
        with m.patch.dict(os.environ, {self.ENV: "1300"}):
            b = goal._lane_min_mem_avail_mb()
        self.assertEqual((a, b), (900, 1300))

    def test_malformed_or_nonpositive_env_falls_back_to_default(self):
        for bad in ("", "abc", "0", "-512", "12.5", "  "):
            with m.patch.dict(os.environ, {self.ENV: bad}):
                self.assertEqual(
                    goal._lane_min_mem_avail_mb(),
                    goal.GOAL_LANE_MIN_MEM_AVAIL_MB,
                    "bad value %r must fall back to the default (never "
                    "silently disable the OOM guard)" % bad)


class WatchdogUnitCarriesEnvironmentFile(unittest.TestCase):
    """#574 wiring-seam lock: the api-watchdog systemd --user unit carries an
    OPTIONAL per-box EnvironmentFile so `AIRULESET_LANE_MIN_MEM_MB` (and any
    other AIRULESET_* watchdog knob) is reachable by the timer's env. The `-`
    prefix keeps it optional; `%h` is systemd's user-home specifier."""

    def _template(self):
        return (airuleset.REPO_DIR / "settings"
                / "api-watchdog.service.template").read_text()

    def test_service_template_has_optional_env_file(self):
        t = self._template()
        self.assertIn("EnvironmentFile=-%h/.claude/watchdog.env", t)

    def test_env_file_is_optional_dash_prefixed(self):
        # The `-` prefix makes systemd ignore a missing file (a box that
        # sets no override is byte-identical in behavior); a bare
        # `EnvironmentFile=` would FAIL the unit start when absent.
        t = self._template()
        # #574 review 🔵-1: assert the line is PRESENT before the per-line
        # loop, so a full template revert (zero EnvironmentFile lines) fails
        # HERE too instead of passing vacuously (the loop asserts nothing when
        # there is nothing to iterate).
        self.assertTrue(
            any(ln.strip().startswith("EnvironmentFile")
                for ln in t.splitlines()),
            "no EnvironmentFile line present in the template at all")
        for ln in t.splitlines():
            if ln.strip().startswith("EnvironmentFile"):
                self.assertTrue(ln.strip().startswith("EnvironmentFile=-"),
                                "EnvironmentFile must be `-`-prefixed "
                                "(optional): %r" % ln)

    def test_validate_watchdog_asserts_env_file_present(self):
        # The config validator locks the wiring seam so a silent template
        # revert is caught (the #534 wiring-seam-lock discipline).
        self.assertEqual(airuleset._validate_watchdog(), [])


if __name__ == "__main__":
    unittest.main()
