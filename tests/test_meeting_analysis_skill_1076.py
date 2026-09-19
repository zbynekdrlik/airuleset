"""#1076 -- skills/meeting-analysis/SKILL.md source locks.

The owner directive (2026-09-18): meeting interpretation runs in the MAIN
session (Fable), the subagent only does the mechanical phases. This test locks
the SKILL.md changes:
  - Hard Rule 0 ("Interpretation = the MAIN session") is PRESENT;
  - the phase-5 Workflow fan-out ("parallel readers over transcript segments +
    screen groups") is GONE;
  - the phase-1-3 dispatch tells the session to mark the worker prompt's first
    line `MECHANICAL-ONLY:` (so the delegation gate passes);
  - the phase-4/5 write steps stamp deliverables `Analysed-by: main <model>` via
    `cli_authorship.authorship_value(cwd)`.

Plus the #576 injected-body cap: meeting-analysis is situational-injected
(UserPromptSubmit row), so its frontmatter-stripped body must stay under the
injector's MAX_BODY or its tail (anti-patterns, phase 6) is silently truncated.
Measured by DRIVING the real hook's own strip_frontmatter + MAX_BODY.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "skills" / "meeting-analysis" / "SKILL.md"
INJECT = REPO / "hooks" / "inject-situational-rule.sh"


def _text():
    return SKILL.read_text(encoding="utf-8")


def _norm(t):
    """Whitespace-collapsed copy so a cross-LINE assertion is not defeated by
    where the prose happens to wrap (the rule may span two wrapped lines)."""
    return re.sub(r"\s+", " ", t)


class TestHardRuleZero(unittest.TestCase):
    def test_hard_rule_0_present(self):
        t = _norm(_text())
        # A Hard Rule 0 that puts interpretation in the MAIN session and the
        # subagent on mechanics only.
        self.assertRegex(t, r"(?i)(^|\s)0\.\s")  # a rule numbered 0 exists
        self.assertRegex(t, r"(?i)interpretation.*(main|fable)")
        self.assertRegex(t, r"(?i)subagent.*(mechan|extract|asr|dedup)")

    def test_phases_4_to_6_named_as_main_only(self):
        t = _text()
        self.assertRegex(t, r"(?i)phases?\s*4")


class TestFanOutGone(unittest.TestCase):
    def test_parallel_readers_fan_out_removed(self):
        t = _text().lower()
        self.assertNotIn("parallel readers over transcript", t)

    def test_no_workflow_fan_out_as_primary_reader(self):
        # the removed pattern: dispatching parallel readers over screen groups
        self.assertNotRegex(_text().lower(), r"fan out parallel readers")


class TestMechanicalMarker(unittest.TestCase):
    def test_dispatch_names_mechanical_only_marker(self):
        t = _text()
        self.assertIn("MECHANICAL-ONLY", t)


class TestAnalysedByStamp(unittest.TestCase):
    def test_stamp_instruction_present(self):
        t = _text()
        self.assertIn("Analysed-by: main", t)
        # the skill must cite cli_authorship.stamp_line (returns the FULL
        # `Analysed-by: main <model>` line), NOT authorship_value (value-only,
        # which would fail the Stop check) — #1076 review A/F4.
        self.assertRegex(t, r"cli_authorship")
        self.assertRegex(t, r'stamp_line\("Analysed"')

    def test_all_three_deliverables_named(self):
        t = _text()
        for name in ("screen_inventory.md", "NOTES.md", "MAPPING.md"):
            self.assertIn(name, t, "%s must be named as a stamped deliverable" % name)


class TestInjectedBodyUnderCap(unittest.TestCase):
    """#576: measure what inject-situational-rule.sh actually injects -- the
    frontmatter-stripped body against MAX_BODY -- by driving the hook's OWN
    strip_frontmatter + MAX_BODY, never a hand-typed re-implementation."""

    def _hook_strip_and_cap(self):
        src = INJECT.read_text(encoding="utf-8")
        m = re.search(r"^def strip_frontmatter\(text\):\n(?:[ \t].*\n|\n)+", src, re.M)
        assert m, "inject hook no longer defines strip_frontmatter"
        ns = {}
        exec(m.group(0), ns)  # noqa: S102 -- the hook's own function, not user input
        cap = int(re.search(r"^MAX_BODY\s*=\s*(\d+)", src, re.M).group(1))
        return ns["strip_frontmatter"], cap

    def test_body_under_max_body(self):
        strip, cap = self._hook_strip_and_cap()
        body = strip(_text()).strip()
        self.assertLessEqual(
            len(body), cap,
            "meeting-analysis injected body %d exceeds MAX_BODY %d -- its tail "
            "(anti-patterns / phase 6) would be silently truncated; condense "
            "verbose prose (never raise MAX_BODY)." % (len(body), cap))


if __name__ == "__main__":
    unittest.main()
