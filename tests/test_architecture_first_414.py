"""#414 -- SOTA architecture: locks the module-wording additions to
`architecture-first.md` / `mvp-philosophy.md` / `investigate-existing-
first.md` (production-by-default, framework-first, the MVP=scope-not-
quality cross-link, and the MANDATORY named-candidates trigger).

Content-only lock (no execution): these three modules are markdown text
loaded into every session via the always-on profile, not code with its
own unit tests. This file is the "content test" the #414 dispatch calls
for -- it exists so a future edit that silently drops or waters down the
#414 wording gets caught the same way any other locked phrase in this
repo is caught, and so a future `/mdreview` pass has something concrete
to re-validate against.
"""
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestArchitectureFirstProductionByDefault(TestCase):

    def setUp(self):
        self.text = read("modules/quality/architecture-first.md")

    def test_unattended_prod_dependency_triggers_are_named(self):
        self.assertIn("Production-by-default", self.text)
        self.assertIn("UNATTENDED", self.text)
        self.assertIn("prod data", self.text)
        self.assertIn("DEPENDENCY of another component", self.text)

    def test_mvp_is_scope_not_quality_distinction_is_present(self):
        self.assertIn("MVP is a decision about SCOPE", self.text)
        self.assertIn("never about QUALITY", self.text)

    def test_reclassification_mandates_stop_and_redesign(self):
        self.assertIn("STOP and redesign the architecture", self.text)

    def test_framework_first_names_investigate_existing_first_mandatory(self):
        self.assertIn("Framework-first for new components", self.text)
        self.assertIn("`investigate-existing-first` is MANDATORY", self.text)
        self.assertIn("NAMED candidates", self.text)

    def test_framework_first_requires_the_architektura_section(self):
        self.assertIn("`Architektúra:` section", self.text)
        self.assertIn("why-none-fits", self.text)

    def test_both_new_bullets_reference_the_originating_ticket(self):
        self.assertIn("#414", self.text)

    def test_the_original_bullets_are_untouched(self):
        # this ticket ADDS, never rewrites the pre-existing content.
        self.assertIn("Follow existing patterns", self.text)
        self.assertIn("No patchwork.", self.text)
        self.assertIn("No circular development", self.text)

    def test_file_stays_short_house_style(self):
        # tight, precise, no essay -- this file loads into EVERY session.
        self.assertLess(len(self.text.splitlines()), 20)

    def test_never_gained_a_rewordings_coda(self):
        # locks the SAME invariant test_rules_ab_experiment.py's condition-C
        # ablation depends on (architecture-first.md is dropped WHOLESALE
        # by that experiment, never ablated in-place, specifically because
        # it carries no rewordings clause to strip) -- #414 must not
        # accidentally introduce one.
        self.assertNotIn(
            "applies to all rewordings and semantic equivalents",
            self.text.lower())


class TestMvpPhilosophyCrossLink(TestCase):

    def setUp(self):
        self.text = read("modules/quality/mvp-philosophy.md")

    def test_scope_vs_quality_cross_link_is_present(self):
        self.assertIn("SCOPE decision", self.text)
        self.assertIn("never a QUALITY or architecture exemption", self.text)
        self.assertIn("architecture-first.md", self.text)

    def test_references_the_originating_ticket(self):
        self.assertIn("#414", self.text)

    def test_original_bullets_are_untouched(self):
        self.assertIn("not a general-purpose framework", self.text)
        self.assertIn("Dead code is a maintenance burden", self.text)


class TestInvestigateExistingFirstMandatoryTrigger(TestCase):

    def setUp(self):
        self.text = read("modules/quality/investigate-existing-first.md")

    def test_mandatory_for_new_service_cli_daemon_component(self):
        self.assertIn("MANDATORY", self.text)
        self.assertIn("new service", self.text.lower())
        self.assertIn("daemon", self.text.lower())
        self.assertIn("long-lived component", self.text.lower())

    def test_points_at_the_architektura_section(self):
        self.assertIn("`Architektúra:` section", self.text)

    def test_references_the_originating_ticket(self):
        self.assertIn("#414", self.text)

    def test_stays_a_short_stub(self):
        # test_stub_remains_at_old_path_short_and_points_to_the_skill (in
        # test_ruleset_conversion_wave2.py) already locks the <12-line
        # ceiling for this exact file -- this is a same-number sanity
        # check kept local to this file so a reader sees the constraint
        # right next to the content it bounds.
        self.assertLess(len(self.text.splitlines()), 12)


if __name__ == "__main__":
    main()
