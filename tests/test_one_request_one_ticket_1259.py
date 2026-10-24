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
        self.assertNotIn("planned-work", parse.EXEMPT_FROM_CAP)

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


class TestDailyCap(unittest.TestCase):

    def test_daily_cap_is_two(self):
        self.assertEqual(caps.DAILY_CAP, 2)

    def test_block_message_names_the_new_cap_and_no_planned_work(self):
        from gates.filing import render
        # the criterion token, not the `complete-planned-work.md` file name
        self.assertNotRegex(render.BLOCK_MSG, r"(?<!complete-)planned-work")
        self.assertIn("soft filing cap of 2", render.BLOCK_MSG)


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

    def test_no_dropped_work_records_on_the_current_ticket(self):
        text = (ROOT / "modules/quality/no-dropped-work.md").read_text(encoding="utf-8")
        self.assertIn("ONE owner request = ONE ticket", text)


if __name__ == "__main__":
    unittest.main()
