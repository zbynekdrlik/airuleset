"""#1039 — the footer nudges badge NAMES the always-on RECOVERY kinds with a
`· rec` suffix, so an `oauth-resume` (a recovery nudge) no longer arrives under a
badge reading `nudges OFF` (the owner's 2026-09-15 contradiction).

`statusbar.nudges_off_segment` renders `nudges OFF · rec` / `nudges N/M · rec`.
The ` · rec` suffix renders IFF `watchdog.RECOVERY_NUDGE_KINDS` is non-empty —
read from the SAME watchdog constant the CLI (`airuleset.py nudges`) already
prints; `M = len(watchdog.MACHINE_NUDGE_KINDS)` is read from the constant, never
hard-coded; the segment stays empty on any read error. One net-neutral clause in
`modules/core/statusline-vocabulary.md` documents the badge (context-baseline
ratchet stays under its ceiling).

RED against the pre-#1039 tree: the badge is a bare `nudges OFF` / `nudges N/M`
with NO recovery suffix, and statusline-vocabulary.md carries no nudges clause.
GREEN once the suffix + clause land.
"""
import json
import os
import re
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import statusbar  # noqa: E402

VOCAB = REPO / "modules" / "core" / "statusline-vocabulary.md"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(seg):
    """Strip ANSI SGR codes so the badge text can be asserted exactly."""
    return _ANSI.sub("", seg)


def _stage(home, kinds):
    """Write the #1023 per-kind staging state file with `kinds` staged ON."""
    d = os.path.join(home, ".claude")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "nudges-kinds.json"), "w", encoding="utf-8") as fh:
        json.dump({"on": sorted(kinds)}, fh)


class TestRecoverySuffix(unittest.TestCase):
    """The ` · rec` suffix is present in every rendered form of the badge."""

    def test_off_case_has_rec_suffix(self):
        with TemporaryDirectory() as home:
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges OFF · rec")

    def test_one_staged_has_rec_suffix(self):
        with TemporaryDirectory() as home:
            one = sorted(wd.MACHINE_NUDGE_KINDS)[0]
            _stage(home, [one])
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 1/%d · rec" % mtotal)

    def test_all_staged_has_rec_suffix(self):
        with TemporaryDirectory() as home:
            _stage(home, wd.MACHINE_NUDGE_KINDS)
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges %d/%d · rec" % (mtotal, mtotal))


class TestConstantSourced(unittest.TestCase):
    """N and M and the suffix all read from the watchdog constants, never
    from a hard-coded literal."""

    def test_M_denominator_read_from_MACHINE_constant(self):
        # Shrink MACHINE_NUDGE_KINDS to 3 REAL kinds; stage one; expect `1/3`.
        # A hard-coded `13` would render `1/13` and fail this assertion.
        subset = frozenset({"card", "bounce", "gk-request"})
        self.assertTrue(subset <= wd.MACHINE_NUDGE_KINDS)
        with TemporaryDirectory() as home, \
                m.patch.object(wd, "MACHINE_NUDGE_KINDS", subset):
            _stage(home, ["card"])
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 1/3 · rec")

    def test_suffix_absent_when_recovery_kinds_empty(self):
        # A patched-empty RECOVERY set drops the suffix in BOTH forms — proving
        # the suffix is sourced from RECOVERY_NUDGE_KINDS, not a literal string.
        with TemporaryDirectory() as home, \
                m.patch.object(wd, "RECOVERY_NUDGE_KINDS", frozenset()):
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges OFF")
            one = sorted(wd.MACHINE_NUDGE_KINDS)[0]
            _stage(home, [one])
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 1/%d" % len(wd.MACHINE_NUDGE_KINDS))


class TestFailSafe(unittest.TestCase):
    def test_empty_segment_on_read_error(self):
        # A raising read must degrade to no segment (never a crash / partial),
        # exactly as the pre-#1039 function did.
        with TemporaryDirectory() as home, \
                m.patch.object(wd, "nudges_on_kinds",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(statusbar.nudges_off_segment(home=home), "")


class TestVocabularyClause(unittest.TestCase):
    """STATEMENT lock (#498/#500 family) — assert the actual clause bullet, never
    an H1/whole-file substring."""

    def _bullet(self):
        lines = VOCAB.read_text(encoding="utf-8").splitlines()
        return [ln for ln in lines if ln.startswith("- `nudges OFF · rec`")]

    def test_clause_bullet_present(self):
        bullets = self._bullet()
        self.assertEqual(len(bullets), 1,
                         "expected exactly one nudges-badge clause bullet, "
                         "got %d" % len(bullets))

    def test_clause_documents_both_forms_and_recovery(self):
        b = self._bullet()[0]
        self.assertIn("nudges N/M · rec", b)
        self.assertIn("recovery", b.lower())
        self.assertTrue(
            any(k in b for k in ("resume", "compact", "goal-arm")),
            "clause must name at least one always-on recovery kind")


class TestContextBaselineRatchet(unittest.TestCase):
    def test_context_baseline_check_passes(self):
        # The clause must be paid for by an in-module trim: the DOWN-ONLY
        # context-baseline ratchet must still pass (drive the REAL check).
        r = subprocess.run(
            [sys.executable, "airuleset.py", "context-baseline", "--check"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(
            r.returncode, 0,
            "context-baseline --check failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
