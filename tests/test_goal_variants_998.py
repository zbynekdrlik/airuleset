"""#998 item 3 — goal renderer per (authority, role, mode) + no-turn-cap lock.

`goal_registry.render_goal_line` substitutes the refill clause for the
sequential one and appends the infra-role clause; NO variant carries a turn
cap. `watchdog.goal.goal_template_for` resolves the pane's (mode, role) from
cwd and uses the SAME renderer for variants (SKILL.md read for the default).
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import goal_registry as gr  # noqa: E402
from watchdog import goal  # noqa: E402


class TestRenderGoalLine(TestCase):
    def test_default_variant_equals_the_shipped_skill_line(self):
        # #998-review: NOT tautological (render delegates to render_goal_line, so
        # comparing the two proves nothing) — assert the DEFAULT (parallel,
        # no-role) variant byte-equals the line SHIPPED in SKILL.md, the real
        # drift target: if render_goal_line's default path ever diverges from the
        # shipped artifact the watchdog arms, this fails.
        with open(gr.skill_path(), encoding="utf-8") as fh:
            shipped = gr.shipped_lines(fh.read())
        self.assertEqual(set(shipped), set(gr.PROFILES))
        for p in gr.PROFILES:
            self.assertEqual(gr.render_goal_line(p, "parallel", None), shipped[p])

    def test_sequential_substitutes_refill(self):
        for p in gr.PROFILES:
            line = gr.render_goal_line(p, "sequential", None)
            self.assertIn("ONE unit at a time", line)
            self.assertNotIn("CONTINUOUS REFILL", line)

    def test_infra_role_appends_scope_clause(self):
        line = gr.render_goal_line("full", "sequential", "infra")
        self.assertIn("INFRA ROLE", line)
        self.assertIn("only tickets labelled", line)
        self.assertIn("box-maintenance steps stop", line)

    def test_no_variant_carries_a_turn_cap(self):
        for authority, mode, role in gr.variant_specs():
            line = gr.render_goal_line(authority, mode, role)
            self.assertIsNone(gr._TURN_CAP_RE.search(line),
                              "%s/%s/%s has a turn cap" % (authority, mode, role))

    def test_every_variant_under_budget(self):
        for authority, mode, role in gr.variant_specs():
            line = gr.render_goal_line(authority, mode, role)
            self.assertLessEqual(len(line), gr.GOAL_ARM_CHAR_CAP)

    def test_variant_check_clean(self):
        self.assertEqual(gr.variant_check(), [])

    def test_unknown_args_raise(self):
        with self.assertRaises(ValueError):
            gr.render_goal_line("full", "turbo", None)
        with self.assertRaises(ValueError):
            gr.render_goal_line("full", "parallel", "boss")
        with self.assertRaises(ValueError):
            gr.render_goal_line("nope", "parallel", None)


class TestGoalTemplateFor(TestCase):
    def test_default_delegates_to_skill_md(self):
        with tempfile.TemporaryDirectory() as d:
            got = goal.goal_template_for("full", d)
            self.assertEqual(got, goal.goal_template_for_authority("full"))

    def test_sequential_project_uses_variant_renderer(self):
        with tempfile.TemporaryDirectory() as d:
            claude = Path(d) / ".claude"
            claude.mkdir()
            (claude / "lane-resources.json").write_text(
                json.dumps({"mode": "sequential"}))
            got = goal.goal_template_for("full", d)
            self.assertIn("ONE unit at a time", got)
            self.assertNotIn("CONTINUOUS REFILL", got)

    def test_explicit_mode_role_override(self):
        got = goal.goal_template_for("full", "/x", role="infra", mode="sequential")
        self.assertIn("INFRA ROLE", got)


if __name__ == "__main__":
    main()
