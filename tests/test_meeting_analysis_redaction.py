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
        # finder is a phrase UNIQUE to the operative write-time line (#498) —
        # `[redigované]` alone appears on 5 lines, so it is a co-token here,
        # not the finder (hardened per the #576 review, finding R2-1).
        self.assert_teeth("for every personal name and every", "[redigované]", "customer")

    def test_keep_codes_and_data_not_names(self):
        self.assert_teeth("ZAK/OP/PV", "dimension")

    def test_redaction_is_scoped_to_the_customer_not_your_own_side(self):
        # #576 review R2-2: the rule must NOT over-redact the user's own
        # team/product/vendor names (they are analysis context, not the leak)
        self.assert_teeth("OWN side's names", "context", "leak")

    def test_hard_rule_3_is_qualified(self):
        self.assert_teeth("VERBATIM covers", "NEVER", "names")

    def test_phase5_critic_has_a_mandatory_redaction_scan(self):
        self.assert_teeth("Redaction check", "MANDATORY", "accepting")

    def test_anti_pattern_names_the_verbatim_trap(self):
        self.assert_teeth("verbatim because", "WRONG")

    # #1076 removed the screen-reader FAN-OUT (interpretation now runs in the
    # MAIN session, Hard Rule 0): there is no longer a dispatched sub-reader
    # that WRITES a deliverable, so the old "put the redaction rule into each
    # sub-reader prompt" instruction is obsolete. The Phase-5 completeness
    # critic is read-only over the already-redacted, main-authored output and
    # never writes a deliverable, so it carries no redaction-in-prompt burden.
    # The former test_subreader_prompt_must_carry_the_rule teeth were dropped
    # here; redaction is fully locked by the other teeth (Hard Rule 7 + the
    # Phase-5 mandatory redaction scan) and the fan-out removal is locked by
    # test_meeting_analysis_skill_1076.TestFanOutGone.
    def test_redaction_is_a_write_time_rule_in_main(self):
        # the redaction rule is enforced at write time (Hard Rule 7), now that
        # every screen is read + written in main (no fan-out sub-reader).
        self.assert_teeth("AT WRITE TIME", "OVERRIDES")


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


def _hook_strip_frontmatter():
    """Extract and exec the REAL `strip_frontmatter` from the hook, so the
    guard below measures EXACTLY what the injector measures — never a
    hand-typed re-implementation of the stripping (the #498 discipline:
    drive the real hook, not a guess at its rules). The injector truncates
    `strip_frontmatter(fh.read()).strip()`, i.e. the body WITHOUT the YAML
    frontmatter — so counting the whole file (frontmatter included) would
    over-count by ~500 chars and demand a cut the injector never needs."""
    src = INJECT_HOOK.read_text(encoding="utf-8")
    m = re.search(
        r"^def strip_frontmatter\(text\):\n(?:[ \t].*\n|\n)+", src, re.M
    )
    if not m:
        raise AssertionError(
            "strip_frontmatter no longer found in inject-situational-rule.sh"
        )
    ns = {}
    exec(m.group(0), ns)  # noqa: S102 — the hook's own function, not user input
    return ns["strip_frontmatter"]


class InjectionDeliversTheWholeSkill(unittest.TestCase):
    """`skills/meeting-analysis/SKILL.md` is injected by a
    `hooks/situational-triggers.conf` UserPromptSubmit row, and
    `inject-situational-rule.sh` TRUNCATES any injected body over `MAX_BODY`
    chars — which would silently drop the tail (Phase 5 critic /
    anti-patterns / Phase 6) from the nudge. The #576 additions grow the
    skill, so lock that the injected body still fits under the REAL cap.

    Both `MAX_BODY` and the frontmatter-stripping are read from the actual
    hook (never hand-typed), so this measures precisely what the injector
    delivers. If it fails, CONDENSE the skill's prose to fit — do NOT raise
    MAX_BODY here."""

    def test_injected_body_fits_under_inject_max_body(self):
        m = re.search(
            r"^MAX_BODY\s*=\s*(\d+)", INJECT_HOOK.read_text(encoding="utf-8"), re.M
        )
        self.assertIsNotNone(
            m, "MAX_BODY no longer defined in inject-situational-rule.sh"
        )
        max_body = int(m.group(1))
        # exactly the injector's own measurement: strip_frontmatter(...).strip()
        injected_body = _hook_strip_frontmatter()(_body()).strip()
        n = len(injected_body)
        self.assertLessEqual(
            n,
            max_body,
            "meeting-analysis injected body is %d chars > MAX_BODY %d — the "
            "situational-injection nudge will TRUNCATE its tail (Phase 5 "
            "critic / anti-patterns / Phase 6). Condense prose to fit (#576)."
            % (n, max_body),
        )


class DispatchMandateForGoalArmed(unittest.TestCase):
    """#926/#1137: dispatching the mechanical Phases 1-3 is OPTIONAL (main may
    run them itself); when dispatched, the worker prompt carries the
    MECHANICAL-ONLY marker, and the reading phases always stay in main."""

    def test_dispatch_is_optional_and_marked(self):
        norm = re.sub(r"\s+", " ", _body())
        self.assertIn("Optional dispatch of Phases 1-3", norm)
        self.assertIn("main may run them itself", norm)
        self.assertIn("MECHANICAL-ONLY: extract|asr|dedup", norm)

    def test_no_forced_main_restriction_is_cited(self):
        self.assertNotIn("block-main-implementation", _body())

    def test_phase4_stays_in_main(self):
        # #1076 strengthened this: the dispatch section now says "Phases 4-6 ...
        # stay in main" (reading screens IS interpretation, Hard Rule 0).
        # Whitespace-collapsed so a line wrap between "Phases 4-6" and "main"
        # does not defeat the check.
        norm = re.sub(r"\s+", " ", _body())
        self.assertRegex(
            norm, r"Phases? 4(-6)?[^.]{0,80}stay(s)? in\s+main",
            "#926/#1076: the dispatch section must say the reading phase(s) stay in main")


if __name__ == "__main__":
    unittest.main()
