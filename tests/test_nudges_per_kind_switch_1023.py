"""#1023 — per-KIND staging in the nudge kill switch. Default: EVERY machine-nudge
kind is OFF (the state file absent reads as all-OFF). `nudges on --kind <k>` /
`nudges off --kind <k>` / `nudges on --all` stage kinds one at a time; a bare
`nudges on` REFUSES and prints the kinds. `nudges_enabled(kind)` is the predicate
the ONE keystroke primitive consults via the threaded `nudge=` identity. The badge
renders `nudges OFF` when all off, `nudges N/M` when some are on.

RED against the pre-#1023 tree: `nudges_enabled` is global (existence of
`~/.claude/nudges-off`); there is no per-kind state, no `--kind`, no `nudge=`
identity on `keys`, and the badge is a bare `nudges OFF`. GREEN once the per-kind
switch lands.
"""
import argparse
import os
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import statusbar  # noqa: E402
import airuleset  # noqa: E402


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout=8):
        self.calls.append(argv)
        return ""

    def sent_keys(self):
        return [a for a in self.calls if "send-keys" in " ".join(map(str, a))]


def _no_bypass():
    """The suite sets AIRULESET_TEST_IGNORE_DISABLE (conftest autouse) so a real
    box's staged state never fails the suite; pop it to exercise the real
    per-kind predicate."""
    patcher = m.patch.dict(os.environ)
    patcher.start()
    os.environ.pop("AIRULESET_TEST_IGNORE_DISABLE", None)
    return patcher


class TestPerKindPredicate(unittest.TestCase):
    def setUp(self):
        p = _no_bypass()
        self.addCleanup(p.stop)

    def test_absent_state_all_off(self):
        with TemporaryDirectory() as home:
            for k in wd.MACHINE_NUDGE_KINDS:
                self.assertFalse(wd.nudges_enabled(k, home=home),
                                 "%s must be OFF by default (absent state)" % k)

    def test_enable_one_kind_only(self):
        with TemporaryDirectory() as home:
            wd.set_nudge_kind("queue-arrival", True, home=home)
            self.assertTrue(wd.nudges_enabled("queue-arrival", home=home))
            self.assertFalse(wd.nudges_enabled("lane-occupancy", home=home))

    def test_off_kind_disables_it(self):
        with TemporaryDirectory() as home:
            wd.set_nudge_kind("queue-arrival", True, home=home)
            wd.set_nudge_kind("queue-arrival", False, home=home)
            self.assertFalse(wd.nudges_enabled("queue-arrival", home=home))

    def test_test_bypass_env_forces_on(self):
        with TemporaryDirectory() as home:
            with m.patch.dict(os.environ, {"AIRULESET_TEST_IGNORE_DISABLE": "1"}):
                self.assertTrue(wd.nudges_enabled("queue-arrival", home=home))


class TestCLI(unittest.TestCase):
    def setUp(self):
        p = _no_bypass()
        self.addCleanup(p.stop)

    def _args(self, **kw):
        d = dict(nudges_action="status", kind=None, all=False, reason=None,
                 fleet=False)
        d.update(kw)
        return argparse.Namespace(**d)

    def test_bare_on_refuses_and_lists_kinds(self):
        import contextlib
        import io
        with TemporaryDirectory() as home, \
                m.patch("os.path.expanduser",
                        side_effect=lambda p: p.replace("~", home, 1)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = airuleset.cmd_nudges(self._args(nudges_action="on"))
            out = buf.getvalue()
            self.assertNotEqual(rc, 0, "bare `nudges on` must refuse")
            self.assertIn("queue-arrival", out)   # lists the kinds
            self.assertFalse(wd.nudges_enabled("queue-arrival", home=home))

    def test_on_kind_enables_only_that(self):
        with TemporaryDirectory() as home, \
                m.patch("os.path.expanduser",
                        side_effect=lambda p: p.replace("~", home, 1)):
            airuleset.cmd_nudges(self._args(nudges_action="on", kind="queue-arrival"))
            self.assertTrue(wd.nudges_enabled("queue-arrival", home=home))
            self.assertFalse(wd.nudges_enabled("lane-occupancy", home=home))


class TestBadge(unittest.TestCase):
    def test_all_off_shows_zero_fraction_recovery_on(self):
        # #1039 fix-forward: all-off renders `nudges 0/M · recovery on`, never the
        # word OFF (deliberately overturned wording, not a weakened invariant —
        # the badge still shows the exact staged-kind state, in plain words).
        with TemporaryDirectory() as home:
            seg = statusbar.nudges_off_segment(home=home)
            self.assertIn("nudges 0/", seg)
            self.assertIn("recovery on", seg)
            self.assertNotIn("OFF", seg)

    def test_some_on_shows_fraction(self):
        with TemporaryDirectory() as home:
            wd.set_nudge_kind("queue-arrival", True, home=home)
            seg = statusbar.nudges_off_segment(home=home)
            self.assertIn("nudges 1/", seg)
            self.assertNotIn("OFF", seg)


class TestPrimitivePerKind(unittest.TestCase):
    PID = "%9"

    def test_disabled_kind_suppressed_at_primitive(self):
        rec = _Recorder()
        logs = []
        with m.patch.object(wd, "nudges_enabled",
                            lambda kind=None, home=None: False):
            ok = wd.keys(self.PID, "Enter", kind="send", nudge="queue-arrival",
                         run=rec, logs=logs)
        self.assertFalse(ok)
        self.assertEqual(rec.sent_keys(), [])

    def test_enabled_kind_delivers_at_primitive(self):
        rec = _Recorder()
        with m.patch.object(wd, "nudges_enabled",
                            lambda kind=None, home=None: kind == "queue-arrival"):
            ok = wd.keys(self.PID, "Enter", kind="send", nudge="queue-arrival",
                         run=rec)
            self.assertTrue(ok)
            self.assertEqual(rec.calls,
                             [["tmux", "send-keys", "-t", self.PID, "Enter"]])
        # a DIFFERENT (disabled) kind is suppressed
        rec2 = _Recorder()
        with m.patch.object(wd, "nudges_enabled",
                            lambda kind=None, home=None: kind == "queue-arrival"):
            ok2 = wd.keys(self.PID, "Enter", kind="send", nudge="lane-occupancy",
                          run=rec2)
        self.assertFalse(ok2)
        self.assertEqual(rec2.sent_keys(), [])


class TestRecoveryAlwaysOn(unittest.TestCase):
    """#1023 addendum (owner, 2026-09-14): RECOVERY revivals (the api-error/401
    OAuth resume + limit `continue` = `resume`; the /compact delivery = `compact`)
    are NEVER suppressed by the kill switch — they revive a dead/blocked session,
    not backlog pushes. They are NOT in the stageable set (badge/CLI/floor), so
    the default all-OFF never mutes them.

    RED against the pre-addendum tree: `resume`/`compact` are in MACHINE_NUDGE_KINDS
    and default OFF, so `nudges_enabled("resume")` is False (the exact 401/limit
    revival the owner reported never firing). GREEN once they are RECOVERY (always-on).
    """
    def setUp(self):
        p = _no_bypass()
        self.addCleanup(p.stop)

    def test_recovery_kinds_always_on_even_when_all_off(self):
        with TemporaryDirectory() as home:
            self.assertTrue(wd.nudges_enabled("resume", home=home),
                            "the api-error/limit resume must fire even with nudges OFF")
            self.assertTrue(wd.nudges_enabled("compact", home=home),
                            "the /compact delivery must fire even with nudges OFF")

    def test_priority_kind_still_off_by_default(self):
        with TemporaryDirectory() as home:
            self.assertFalse(wd.nudges_enabled("queue-arrival", home=home))

    def test_recovery_kind_is_not_stageable(self):
        with TemporaryDirectory() as home:
            wd.set_nudge_kind("resume", True, home=home)
            # a recovery kind is always-on; it is never persisted into the staged set
            self.assertNotIn("resume", wd.nudges_on_kinds(home=home))

    def test_recovery_set_is_disjoint_from_stageable(self):
        # #1038 adds `goal-arm` (a DECLARED managed window's post-reboot arm) to
        # the always-on recovery set — a session revival, never machine-staged.
        # #1034 adds `wake-parked` (waking a session parked on the usage-limit
        # auto-continue banner after a claudy account switch) — same class.
        # #1063 adds `goal-disarm` (the `/goal clear` #522 question-repoke
        # backstop) — a damage-control action that must fire with machine kinds
        # OFF; before #1063 it rode the machine kind `goal-sweep` and was dead.
        self.assertEqual(
            wd.RECOVERY_NUDGE_KINDS,
            frozenset({"resume", "compact", "goal-arm", "wake-parked",
                       "goal-disarm"}))
        self.assertTrue(wd.MACHINE_NUDGE_KINDS.isdisjoint(wd.RECOVERY_NUDGE_KINDS))
        # ALL_NUDGE_KINDS is the union — every threaded identity is known
        self.assertEqual(wd.ALL_NUDGE_KINDS,
                         wd.MACHINE_NUDGE_KINDS | wd.RECOVERY_NUDGE_KINDS)


class TestLegacyNudgesOffRemoval1020(unittest.TestCase):
    """#1020 fold-in: the dead legacy global marker ~/.claude/nudges-off (#994,
    per-kind is the source of truth since v0.1.278) is removed by every `nudges`
    verb so a human reading ~/.claude is not misled."""

    def test_helper_deletes_the_marker(self):
        with TemporaryDirectory() as home:
            claude = Path(home) / ".claude"
            claude.mkdir()
            (claude / "nudges-off").write_text("")
            self.assertTrue(airuleset._remove_legacy_nudges_off_marker(home=home))
            self.assertFalse((claude / "nudges-off").exists())

    def test_helper_noop_when_absent(self):
        with TemporaryDirectory() as home:
            (Path(home) / ".claude").mkdir()
            self.assertFalse(airuleset._remove_legacy_nudges_off_marker(home=home))

    def test_nudges_status_verb_clears_the_marker(self):
        with TemporaryDirectory() as home:
            claude = Path(home) / ".claude"
            claude.mkdir()
            (claude / "nudges-off").write_text("")
            args = argparse.Namespace(nudges_action="status", fleet=False,
                                      kind=None, all=False)
            with m.patch.dict(os.environ, {"HOME": home}):
                rc = airuleset.cmd_nudges(args)
            self.assertEqual(rc, 0)
            self.assertFalse((claude / "nudges-off").exists(),
                             "a nudges verb must clear the dead legacy marker")


if __name__ == "__main__":
    unittest.main()
