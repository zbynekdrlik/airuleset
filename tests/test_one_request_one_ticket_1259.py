"""#1259 — one owner request = one ticket (owner 2026-10-24).

Owner, verbatim: "aj ked ja zadam jednu ulohu claude vygeneruje desiatky novych
- som s tym extremne nespokojny".

Measured cause: the rules ordered a converged plan to be decomposed into filed
issues, and the filing gate exempted `planned-work` from every cap. Now a plan's
steps are a checklist in ONE ticket, `planned-work` is no longer an accepted
Scope-gate reason, and the daily cap for discovery reasons is 2.

Plus the /doctor finding: an unquoted `: ` in a frontmatter value is invalid
YAML, so Claude Code drops every field of that skill. `validate` catches it.
"""

import sys
import tempfile
import unittest
from pathlib import Path

from test_scope_gate import body_cmd, run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cli_config  # noqa: E402
from gates.filing import caps, parse  # noqa: E402


class TestPlannedWorkIsRetired(unittest.TestCase):

    def test_planned_work_is_not_an_accepted_reason(self):
        self.assertNotIn("planned-work", parse.ALLOWED)

    def test_a_planned_work_filing_is_blocked_with_the_checklist_way(self):
        r = run(body_cmd("step 2 of the plan", "the second step",
                         scope_gate="planned-work"),
                home=tempfile.mkdtemp(prefix="one1259-"))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("checklist", r.stderr)

    def test_the_owner_asking_still_files(self):
        r = run(body_cmd("owner asked for this", "the owner asked",
                         scope_gate="user-request"),
                home=tempfile.mkdtemp(prefix="one1259-"))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestCapsRemoved1297(unittest.TestCase):
    """#1297 (owner 10.11.2026) reversed the #1259 caps: no daily cap, no
    chain-width cap; planned-work stays retired."""

    def test_no_daily_or_width_cap(self):
        self.assertFalse(hasattr(caps, "DAILY_CAP"))
        self.assertFalse(hasattr(caps, "CHAIN_WIDTH_CAP"))

    def test_block_message_has_no_cap_and_planned_work_only_as_retired(self):
        from gates.filing import render
        self.assertNotIn("soft filing cap", render.BLOCK_MSG)
        self.assertIn("`planned-work` is retired", render.BLOCK_MSG)
        lists = [ln for ln in render.BLOCK_MSG.splitlines() if "| >300-loc |" in ln]
        self.assertEqual(len(lists), 2)
        self.assertFalse(any("planned-work" in ln for ln in lists), lists)


class TestFrontmatterYaml(unittest.TestCase):

    def test_unquoted_colon_space_in_a_value_is_an_error(self):
        errs = cli_config.frontmatter_errors(
            "---\nname: x\ndescription: Three layers: liveness and DOM.\n---\nbody\n")
        self.assertEqual(len(errs), 1)
        self.assertIn("description", errs[0])

    def test_quoted_or_colon_free_values_are_fine(self):
        self.assertEqual(cli_config.frontmatter_errors(
            '---\nname: x\ndescription: "Three layers: liveness."\n'
            "paths:\n  - \"a/b.md\"\nuser-invocable: false\n---\n"), [])

    def test_every_repo_frontmatter_is_valid(self):
        files = (list(ROOT.glob("skills/**/SKILL.md")) + list(ROOT.glob("agents/*.md"))
                 + list(ROOT.glob("rules/*.md")))
        bad = {str(f.relative_to(ROOT)): e for f in files
               for e in [cli_config.frontmatter_errors(f.read_text(encoding="utf-8"))] if e}
        self.assertEqual(bad, {})


class TestDoctrineOneTicket(unittest.TestCase):

    def test_durable_decisions_no_longer_orders_decomposition(self):
        text = (ROOT / "modules/quality/durable-decisions-to-tickets.md").read_text(encoding="utf-8")
        self.assertNotIn("decompose it into filed issues", text)
        self.assertIn("checklist", text)

    def test_no_dropped_work_keeps_plan_steps_off_tickets(self):
        # #1297 keeps this half of #1259: a plan step is never its own ticket
        text = (ROOT / "modules/quality/no-dropped-work.md").read_text(encoding="utf-8")
        self.assertIn("plan steps never become tickets", text)


if __name__ == "__main__":
    unittest.main()
