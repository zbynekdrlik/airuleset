"""Autopilot default without dialogs (#52, 2026-07-25).

User directive: "chcel by som aby by default autopilot rovno pracoval a daval
goal a nepytal sa co skipnut co uzavriet etc ... chcem vzdy smerovat k nula
ticketov." The default `/autopilot` invocation must ask ZERO start-of-run
questions: preflight -> banner -> print the /goal line -> stop, respecting
existing `autopilot-skip` labels silently. The full interactive picker flow
(Step 1b skip-review/add-skip, Step 1c close-obsolete) moves behind a new
`dialog` argument, and a thin alias skill `autopilot-dialog` makes the literal
`/autopilot-dialog` command work too.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "autopilot" / "SKILL.md"
ALIAS = ROOT / "skills" / "autopilot-dialog" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


class TestUsageArgumentUpdated(TestCase):
    def test_argument_hint_includes_dialog(self):
        t = read(SKILL)
        self.assertIn('argument-hint: "[status] [manual] [dialog]"', t)

    def test_description_states_default_is_zero_questions(self):
        t = read(SKILL)
        desc_line = next(ln for ln in t.splitlines()
                          if ln.startswith("description:"))
        self.assertIn("DEFAULT (no dialog arg) = zero questions at start", desc_line)
        self.assertIn("dialog = run the interactive start-of-run flow", desc_line)

    def test_usage_bullets_document_dialog_arg(self):
        t = read(SKILL)
        self.assertIn("`/autopilot [status] [manual] [dialog]`", t)
        self.assertIn("default: ZERO questions at start", t)
        self.assertIn("`dialog`", t)


class TestDefaultPathHasNoPickers(TestCase):
    """The default run's path (Step 1 -> branch -> Step 2) must never mention
    AskUserQuestion — that lives ONLY inside the dialog-gated Step 1b/1c."""

    def test_branch_statement_present_between_step1_and_step1b(self):
        t = read(SKILL)
        self.assertIn("**Branch here on the invocation argument", t)
        self.assertIn("skip Step 1b and Step 1c ENTIRELY", t)
        self.assertIn("nothing is closed or asked about", t)
        # positional: the branch statement sits between Step 1 and Step 1b
        self.assertLess(t.index("**Branch here on the invocation argument"),
                        t.index("### Step 1b"))
        self.assertGreater(t.index("**Branch here on the invocation argument"),
                           t.index("## Step 1 — Preflight"))

    def test_default_branch_text_has_no_askuserquestion(self):
        t = read(SKILL)
        start = t.index("**Branch here on the invocation argument")
        end = t.index("### Step 1b")
        branch_text = t[start:end]
        self.assertNotIn("AskUserQuestion", branch_text)

    def test_step1_preflight_body_has_no_askuserquestion(self):
        t = read(SKILL)
        start = t.index("## Step 1 — Preflight")
        end = t.index("**Branch here on the invocation argument")
        step1_text = t[start:end]
        self.assertNotIn("AskUserQuestion", step1_text)


class TestStep1bAnd1cAreDialogGated(TestCase):
    def test_headers_marked_dialog_only(self):
        t = read(SKILL)
        self.assertIn("### Step 1b — Skip review + picker (DIALOG ONLY", t)
        self.assertIn("### Step 1c — Close obsolete issues (DIALOG ONLY", t)

    def test_both_steps_state_runs_only_with_dialog_arg(self):
        t = read(SKILL)
        start = t.index("### Step 1b")
        end = t.index("## Step 2 — Start the engine")
        dialog_text = t[start:end]
        self.assertEqual(dialog_text.count("Runs ONLY when invoked with the `dialog` argument"), 2)
        self.assertIn("skipped entirely per the branch above", dialog_text)

    def test_dialog_path_still_has_the_interactive_pickers(self):
        t = read(SKILL)
        start = t.index("### Step 1b")
        end = t.index("## Step 2 — Start the engine")
        dialog_text = t[start:end]
        self.assertIn("AskUserQuestion", dialog_text)
        self.assertIn("multiSelect", dialog_text)


class TestReconciliationSweepStaysUnconditional(TestCase):
    def test_step4a_sweep_runs_regardless_of_dialog(self):
        t = read(SKILL)
        self.assertIn("Step 4a — End-of-run reconciliation sweep", t)
        self.assertIn("this sweep is UNCONDITIONAL, dialog or not", t)


class TestAutopilotDialogAliasSkill(TestCase):
    def test_alias_skill_exists_and_registered(self):
        self.assertTrue(ALIAS.exists())
        self.assertIn("autopilot-dialog", airuleset.SKILL_NAMES)

    def test_alias_skill_invokes_autopilot_with_dialog(self):
        t = read(ALIAS)
        self.assertIn("/autopilot dialog", t)
        self.assertIn("Step 1b", t)
        self.assertIn("Step 1c", t)

    def test_alias_skill_frontmatter_is_user_invocable(self):
        t = read(ALIAS)
        self.assertIn("name: autopilot-dialog", t)
        self.assertIn("user-invocable: true", t)

    def test_alias_unrestricted_same_boxes_as_autopilot(self):
        # autopilot itself carries no maintainer/full-authority scoping — the
        # alias must not either, or /autopilot-dialog would silently vanish
        # on exactly the boxes that also have plain /autopilot.
        self.assertNotIn("autopilot-dialog", airuleset.SKILLS_MAINTAINER_ONLY)
        self.assertNotIn("autopilot-dialog", airuleset.SKILLS_FULL_AUTHORITY_ONLY)
        for user in ("newlevel", "gatekeeper", "david", "marek", "montalu"):
            names = airuleset.skill_names_for_user(user)
            self.assertIn("autopilot", names, user)
            self.assertIn("autopilot-dialog", names, user)


class TestValidateCoversNewSkill(TestCase):
    def test_skill_names_list_resolves_to_a_real_file(self):
        self.assertIn("autopilot-dialog", airuleset.SKILL_NAMES)
        p = ROOT / "skills" / "autopilot-dialog" / "SKILL.md"
        self.assertTrue(p.exists())


if __name__ == "__main__":
    main()
