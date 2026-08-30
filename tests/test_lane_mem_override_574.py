"""#574 wiring seam — the api-watchdog systemd unit's optional per-box
EnvironmentFile.

#729 REMOVED the #574 lane-fill memory floor (`_lane_min_mem_avail_mb` /
`GOAL_LANE_MIN_MEM_AVAIL_MB` / the `AIRULESET_LANE_MIN_MEM_MB` override) with the
rest of the dormant memory OOM subsystem, so this file no longer locks that
threshold. What it STILL locks is the GENERIC per-box override seam #574 built:
the api-watchdog `--user` unit carries an optional `EnvironmentFile` so ANY
per-box `AIRULESET_*` watchdog knob (e.g. `AIRULESET_GOAL_LANE_STUCK_ALERT_STREAK`,
#662) is reachable by the timer's env. That seam is still live and validated by
`_validate_watchdog()` (see cli_config.py), so its wiring lock stays.
"""

import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))          # tests/

import airuleset                                        # noqa: E402


class WatchdogUnitCarriesEnvironmentFile(unittest.TestCase):
    """#574 wiring-seam lock: the api-watchdog systemd --user unit carries an
    OPTIONAL per-box EnvironmentFile so a per-box `AIRULESET_*` watchdog knob is
    reachable by the timer's env. The `-` prefix keeps it optional; `%h` is
    systemd's user-home specifier."""

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
