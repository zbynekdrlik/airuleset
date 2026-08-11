"""Install-time timezone enforcement — the fleet must run Slovak time.

#387: the user rebooted the gatekeeper VPS and Claude + `date` reported UTC.
airuleset provisioned `RUNTIME_DEPS` on every box but never the TIMEZONE, so a
Hetzner image's `Etc/UTC` default survived every reboot/rebuild. The user is in
Slovakia and has flagged UTC as a hard, repeatedly-reported regression. Fix:
`ensure_timezone()` runs in `cmd_install` alongside `check_runtime_deps()` —
self-healing where sudo exists, LOUD where it does not, exactly like the deps
check. Mirrors tests/test_runtime_deps.py.
"""

import sys
import unittest
import unittest.mock as m
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


def _show(tz):
    return m.Mock(stdout=tz + "\n", returncode=0)


class EnsureTimezone(unittest.TestCase):
    def test_fleet_timezone_is_slovak(self):
        # the user is in Slovakia; UTC (or any other zone) is a hard regression
        self.assertEqual(airuleset.FLEET_TIMEZONE, "Europe/Bratislava")

    def test_already_correct_is_a_silent_noop(self):
        # a box already on Bratislava must not run set-timezone and must stay quiet
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["timedatectl", "show"]:
                return _show("Europe/Bratislava")
            raise AssertionError("must not set timezone when already correct: %r" % (argv,))

        with m.patch("subprocess.run", side_effect=run):
            out = StringIO()
            with m.patch("sys.stdout", out):
                tz = airuleset.ensure_timezone()
        self.assertEqual(tz, "Europe/Bratislava")
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(all("set-timezone" not in a for a in calls), calls)

    def test_utc_box_with_sudo_sets_and_verifies(self):
        # gatekeeper 2026-08-11: Etc/UTC + passwordless sudo → self-heal + verify
        state = {"tz": "Etc/UTC"}
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            if argv[:2] == ["timedatectl", "show"]:
                return _show(state["tz"])
            if argv[:4] == ["sudo", "-n", "timedatectl", "set-timezone"]:
                state["tz"] = argv[4]          # the set takes effect
                return m.Mock(returncode=0)
            raise AssertionError("unexpected argv %r" % (argv,))

        with m.patch("subprocess.run", side_effect=run):
            out = StringIO()
            with m.patch("sys.stdout", out):
                tz = airuleset.ensure_timezone()
        self.assertEqual(tz, "Europe/Bratislava")
        self.assertIn("✓ timezone", out.getvalue())
        set_calls = [a for a in seen if a[:4] == ["sudo", "-n", "timedatectl", "set-timezone"]]
        self.assertEqual(len(set_calls), 1, seen)
        self.assertEqual(set_calls[0][4], "Europe/Bratislava")

    def test_utc_box_without_sudo_prints_loud_warning(self):
        # subdev isolated users (no sudo): the set fails -> the gap must be LOUD,
        # naming both the wrong tz and the target, never a silent skip
        def run(argv, **kw):
            if argv[:2] == ["timedatectl", "show"]:
                return _show("Etc/UTC")
            if argv[:4] == ["sudo", "-n", "timedatectl", "set-timezone"]:
                return m.Mock(returncode=1)     # no sudo
            raise AssertionError("unexpected argv %r" % (argv,))

        with m.patch("subprocess.run", side_effect=run):
            out = StringIO()
            with m.patch("sys.stdout", out):
                tz = airuleset.ensure_timezone()
        self.assertEqual(tz, "Etc/UTC")
        v = out.getvalue()
        self.assertIn("TIMEZONE", v)
        self.assertIn("Etc/UTC", v)
        self.assertIn("Europe/Bratislava", v)

    def test_survives_timedatectl_failure_without_crashing(self):
        # timedatectl absent / erroring must never abort install — warn, don't raise
        def run(argv, **kw):
            raise FileNotFoundError("timedatectl")

        with m.patch("subprocess.run", side_effect=run):
            out = StringIO()
            with m.patch("sys.stdout", out):
                tz = airuleset.ensure_timezone()   # must not raise
        self.assertIn("TIMEZONE", out.getvalue())
        self.assertNotEqual(tz, "Europe/Bratislava")

    def test_install_runs_ensure_timezone_after_deps(self):
        # wired into cmd_install right after check_runtime_deps(), like the deps check
        src = Path(airuleset.__file__).read_text()
        i = src.index("def cmd_install")
        chunk = src[i:i + 600]
        self.assertIn("ensure_timezone()", chunk)
        self.assertLess(chunk.index("check_runtime_deps()"), chunk.index("ensure_timezone()"))


if __name__ == "__main__":
    unittest.main()
