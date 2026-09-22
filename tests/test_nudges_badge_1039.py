"""#1039 fix-forward — the footer nudges badge NEVER says `OFF` while the
always-on recovery arming is on: it states both switches in plain words for
every state, so an all-off box reads `nudges 0/13 · recovery on` (owner, dev1
22.9.2026: „preco tam stale pise nudges off v paticke ked ty tvrdis ze je to
zapnute!").

`statusbar.nudges_off_segment` renders `nudges N/M · recovery on` for EVERY
state — `N` = staged machine kinds on (`0` when none), `M =
len(watchdog.MACHINE_NUDGE_KINDS)` read from the constant (never hard-coded).
The ` · recovery on` suffix renders IFF `watchdog.RECOVERY_NUDGE_KINDS` is
non-empty (the SAME constant the CLI `airuleset.py nudges` already prints); the
segment stays empty on any read error. There is NO `OFF` word in any rendered
form. One byte-neutral-or-shorter clause in
`modules/core/statusline-vocabulary.md` documents the badge (the down-only
context-baseline ratchet stays under its ceiling).

RED against the current tree: the badge still renders `nudges OFF · rec`
(all-off) / `nudges N/M · rec` (staged), and statusline-vocabulary.md carries the
old `nudges OFF · rec` clause. GREEN once the one-branch renderer + the reworded
clause land.
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


class TestRecoveryOnEveryState(unittest.TestCase):
    """The `· recovery on` suffix and the `N/M` fraction render in EVERY state;
    the badge never says `OFF`."""

    def test_all_off_shows_zero_fraction_and_recovery_on(self):
        with TemporaryDirectory() as home:
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 0/%d · recovery on" % mtotal)

    def test_one_staged_shows_recovery_on(self):
        with TemporaryDirectory() as home:
            one = sorted(wd.MACHINE_NUDGE_KINDS)[0]
            _stage(home, [one])
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 1/%d · recovery on" % mtotal)

    def test_all_staged_shows_recovery_on(self):
        with TemporaryDirectory() as home:
            _stage(home, wd.MACHINE_NUDGE_KINDS)
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges %d/%d · recovery on" % (mtotal, mtotal))


class TestNoOffWordEver(unittest.TestCase):
    """The word `OFF` must not appear in ANY rendered form — the whole point of
    the fix-forward (the mutation target: restoring `OFF` in the renderer must
    turn these RED)."""

    def test_no_off_substring_all_off(self):
        with TemporaryDirectory() as home:
            self.assertNotIn(
                "OFF", _plain(statusbar.nudges_off_segment(home=home)))

    def test_no_off_substring_when_staged(self):
        with TemporaryDirectory() as home:
            _stage(home, sorted(wd.MACHINE_NUDGE_KINDS)[:2])
            self.assertNotIn(
                "OFF", _plain(statusbar.nudges_off_segment(home=home)))

    def test_no_off_substring_when_recovery_empty(self):
        with TemporaryDirectory() as home, \
                m.patch.object(wd, "RECOVERY_NUDGE_KINDS", frozenset()):
            self.assertNotIn(
                "OFF", _plain(statusbar.nudges_off_segment(home=home)))


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
                "nudges 1/3 · recovery on")

    def test_suffix_absent_when_recovery_kinds_empty(self):
        # A patched-empty RECOVERY set drops the suffix in BOTH forms — proving
        # the suffix is sourced from RECOVERY_NUDGE_KINDS, not a literal string.
        # The `N/M` fraction (and the absence of `OFF`) is unaffected.
        with TemporaryDirectory() as home, \
                m.patch.object(wd, "RECOVERY_NUDGE_KINDS", frozenset()):
            mtotal = len(wd.MACHINE_NUDGE_KINDS)
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 0/%d" % mtotal)
            one = sorted(wd.MACHINE_NUDGE_KINDS)[0]
            _stage(home, [one])
            self.assertEqual(
                _plain(statusbar.nudges_off_segment(home=home)),
                "nudges 1/%d" % mtotal)


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
        return [ln for ln in lines if ln.startswith("- `nudges N/M · recovery on`")]

    def test_clause_bullet_present(self):
        bullets = self._bullet()
        self.assertEqual(len(bullets), 1,
                         "expected exactly one nudges-badge clause bullet, "
                         "got %d" % len(bullets))

    def test_clause_documents_form_and_recovery(self):
        b = self._bullet()[0]
        self.assertIn("recovery", b.lower())
        self.assertTrue(
            any(k in b for k in ("resume", "compact", "goal-arm")),
            "clause must name at least one always-on recovery kind")

    def test_clause_carries_no_off_word(self):
        # The doctrine line must not reintroduce the `OFF` wording the fix
        # removed from the footer.
        b = self._bullet()[0]
        self.assertNotIn("OFF", b)


class TestContextBaselineRatchet(unittest.TestCase):
    def test_context_baseline_check_passes(self):
        # The reworded clause must be paid for by an in-module trim: the
        # DOWN-ONLY context-baseline ratchet must still pass (drive the REAL
        # check).
        r = subprocess.run(
            [sys.executable, "airuleset.py", "context-baseline", "--check"],
            cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(
            r.returncode, 0,
            "context-baseline --check failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
            % (r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
