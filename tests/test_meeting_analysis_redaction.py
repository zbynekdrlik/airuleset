"""#576: meeting-analysis must REDACT customer/company names at write time.

Root cause: `skills/meeting-analysis/SKILL.md` carried THREE reinforcing,
UNqualified "transcribe verbatim / field by field" mandates (Hard Rule 3,
Phase 4, an anti-pattern) and NO redaction/privacy rule at all — so screen
readers copied customer/company names verbatim into `screen_inventory.md`
(the montalu dominik-call2 leak). The fix bakes a write-time redaction
OUTPUT-FORMAT rule into the strongest surface (a new Hard Rule 7), qualifies
Hard Rule 3, reinforces it at the Phase 4 write moment + the Phase 5
completeness critic, and adds an anti-pattern.

Content-lock uses the repo's #498/#500 per-line TEETH pattern: each teeth
picks a `finder` token UNIQUE to the operative line and asserts that ONE
finder-matching line carries ALL co-tokens — so a PARTIAL revert of that
line (dropping only the redaction wording, leaving the surrounding prose)
FAILS the test, not just a full deletion. The finder is never a nearby
why-prose token (#498). Every teeth was mutation-verified by hand (revert
the operative line to its pre-#576 form → the specific test fails →
restore); see the ticket's evidence block. The coarse whole-file presence
class catches a FULL deletion; both kinds are kept per #500.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "meeting-analysis" / "SKILL.md"
INJECT_HOOK = REPO / "hooks" / "inject-situational-rule.sh"


def _body():
    return SKILL.read_text(encoding="utf-8")


class _Teeth:
    """#500 per-line teeth: at least ONE physical line containing `finder`
    also contains every co-token. A partial revert drops finder+co-tokens
    together, so the test fails — it is not satisfied by a nearby why-prose
    line (the finder is chosen unique to the operative line)."""

    def assert_teeth(self, finder, *cotokens):
        lines = [ln for ln in _body().splitlines() if finder in ln]
        self.assertTrue(
            lines,
            "no line contains finder %r — the #576 operative line is gone" % finder,
        )
        ok = any(all(c in ln for c in cotokens) for ln in lines)
        self.assertTrue(
            ok,
            "no single line carrying %r also carries all of %r — a partial "
            "revert dropped the redaction wording (#576)" % (finder, list(cotokens)),
        )


class RedactionRuleIsBakedIn(_Teeth, unittest.TestCase):
    def test_hard_rule_7_write_time_override(self):
        # the OUTPUT-FORMAT rule at the strongest (Hard Rules) surface
        self.assert_teeth("OVERRIDES", "REDACT", "verbatim", "WRITE TIME")

    def test_redacted_token_replaces_personal_and_company_names(self):
        self.assert_teeth("[redigované]", "personal name", "customer")

    def test_keep_codes_and_data_not_names(self):
        self.assert_teeth("ZAK/OP/PV", "dimension")

    def test_hard_rule_3_is_qualified(self):
        self.assert_teeth("VERBATIM covers", "NEVER", "names")

    def test_phase5_critic_has_a_mandatory_redaction_scan(self):
        self.assert_teeth("Redaction check", "MANDATORY", "accepting")

    def test_anti_pattern_names_the_verbatim_trap(self):
        self.assert_teeth("verbatim because", "WRONG")

    def test_subreader_prompt_must_carry_the_rule(self):
        # #104: a skill body does not reach a dispatched sub-reader, so the
        # coordinator must put the redaction rule into each sub-reader prompt
        self.assert_teeth("sub-reader", "prompt")


class CoarseWholeFilePresence(unittest.TestCase):
    """A FULL deletion is caught here; the per-line teeth above catch a
    PARTIAL (operative-line-only) revert. Both kept per #500."""

    def test_core_redaction_tokens_present(self):
        b = _body()
        for tok in (
            "[redigované]",
            "OVERRIDES",
            "Redaction check",
            "VERBATIM covers",
            "verbatim because",
        ):
            self.assertIn(tok, b, "#576 redaction token %r missing from the skill" % tok)


class InjectionDeliversTheWholeSkill(unittest.TestCase):
    """`skills/meeting-analysis/SKILL.md` is injected by a
    `hooks/situational-triggers.conf` UserPromptSubmit row, and
    `inject-situational-rule.sh` TRUNCATES any body over `MAX_BODY` chars —
    which would silently drop the tail (Phase 5 critic / anti-patterns /
    Phase 6) from the nudge. The #576 additions grow the file, so lock that
    the whole skill still fits under the REAL hook's cap (read from the hook,
    never a hand-typed copy). If this fails, CONDENSE the skill's prose to
    fit — do NOT raise MAX_BODY here."""

    def test_skill_body_fits_under_inject_max_body(self):
        m = re.search(
            r"^MAX_BODY\s*=\s*(\d+)", INJECT_HOOK.read_text(encoding="utf-8"), re.M
        )
        self.assertIsNotNone(
            m, "MAX_BODY no longer defined in inject-situational-rule.sh"
        )
        max_body = int(m.group(1))
        n = len(_body())
        self.assertLessEqual(
            n,
            max_body,
            "meeting-analysis SKILL.md is %d chars > MAX_BODY %d — the "
            "situational-injection nudge will TRUNCATE its tail (Phase 5 "
            "critic / anti-patterns / Phase 6). Condense prose to fit (#576)."
            % (n, max_body),
        )


if __name__ == "__main__":
    unittest.main()
