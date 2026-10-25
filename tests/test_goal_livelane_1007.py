"""#1007 item 4 — the `/goal` stop-condition (A) must say it applies only when
no background lane is live; with lanes live use ASK-AND-CONTINUE and let the
footer U carry the question.

The sentence renders into every `variant_specs()` (authority, role) variant
via a new `stop-a-livelane` clause (byte-identical join after `stop-a`); the
tightest-arming gk-review variant DROPS it (it already carries the #1007 rule in
its own (B) block) so it stays byte-identical + under its headroom cap. The #1074
gk-quality variant likewise substitutes the (B) block but EMBEDS the #1007
sentence verbatim in its own (B) condition, so the `variant_specs` sweep still
finds it. `goal-inventory --check` re-locks the shipped SKILL.md lines against
the registry.
"""
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import goal_registry as gr  # noqa: E402

SENTENCE_KEY = "applies only when no background agent/lane is live"
SENTENCE_TAIL = "ASK-AND-CONTINUE and let the footer U carry the question"


class GoalLiveLane1007(TestCase):
    def test_sentence_present_in_every_variant_spec(self):
        # #1074: the quality (B)-block embeds the #1007 sentence verbatim, so
        # the sweep finds it in every variant but review (#1137 put review in
        # variant_specs; it carries the rule in its own Slovak (B) block).
        for authority, role in gr.variant_specs():
            if role == "review":
                continue
            line = gr.render_goal_line(authority, role)
            self.assertIn(SENTENCE_KEY, line,
                          "%s/%s missing the #1007 live-lane sentence"
                          % (authority, role))
            self.assertIn(SENTENCE_TAIL, line,
                          "%s/%s missing the ASK-AND-CONTINUE tail"
                          % (authority, role))

    def test_stop_a_livelane_is_a_required_clause(self):
        self.assertIn("stop-a-livelane", gr.REQUIRED_CLAUSES)
        for p in gr.PROFILES:
            self.assertEqual(gr.missing_required(p), [], p)

    def test_review_variant_drops_the_clause_and_stays_under_headroom(self):
        line = gr.render_goal_line("full", "review")
        # dropped from review (it already carries the #1007 rule in its (B) block)
        self.assertNotIn(SENTENCE_KEY, line)
        # and it must still arm with the #384/#730 headroom margin
        self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP - 150,
                             "review variant headroom < 150 (len=%d)" % len(line))

    def test_every_variant_under_budget(self):
        for authority, role in gr.variant_specs():
            line = gr.render_goal_line(authority, role)
            self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP,
                                 "%s/%s over budget: %d"
                                 % (authority, role, len(line)))

    def test_variant_check_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_shipped_skill_lines_carry_the_sentence_and_no_drift(self):
        skill = (REPO / gr.SKILL_REL).read_text(encoding="utf-8")
        self.assertEqual([p for p, _, _ in gr.drift(skill)], [],
                         "SKILL.md /goal lines drifted from the registry")
        for p in gr.PROFILES:
            self.assertIn(SENTENCE_KEY, gr.render(p), p)

    def test_goal_inventory_check_passes(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"), "goal-inventory", "--check"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    main()
