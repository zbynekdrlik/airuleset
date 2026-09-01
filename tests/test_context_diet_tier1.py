"""Context diet tier 1 (#92) — content-preserving moves off the always-on prefix.

Measured baseline (real `usage` of the first assistant turn of a fresh session,
airuleset repo, CC 2.1.220): 162,726 prefix tokens. The project CLAUDE.md is
80,802 B of which `## Development Rules` alone is 69,286 B (85.7%) — pure
airuleset-internals gotchas that only matter when someone is actually working
on airuleset.py / watchdog / hooks / notify / filedrop.

Nothing here DELETES a solved problem. Each move lands the content, verbatim,
on a surface #91 proved actually loads:

  * `.claude/rules/*.md` + `paths:` -> injected automatically as a
    `nested_memory` attachment when a matching file is read. Project-scope, so
    the globs are relative to the repo root and it can never leak into another
    repo's session.
"""

import re
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
INTERNALS = ROOT / ".claude" / "rules" / "airuleset-internals.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# Anchors sampled from the START, MIDDLE and END of the original
# `## Development Rules` section, so the test proves the whole block moved and
# nothing was summarised away.
INTERNALS_ANCHORS = [
    "A `{{PLACEHOLDER}}` that is a SUBSTRING of another",
    "must be `.replace()`d LONGEST-FIRST",
    "An HTML/JS page served RAW (`PAGE.encode()`, NOT `.format()`) must have "
    "SINGLE braces",
    "Python stdlib only — no third-party dependencies",
    "api-watchdog timer executes the WORKING TREE live",
    "GitHub issue SEARCH tokenizes quoted phrases",
    "A tmux pane's AGENT STRIP rows",
    "CC's prompt STASH (Ctrl+S) is SINGLE-SLOT with a SILENT overwrite",
    "Truncating user-visible UTF-8 in a bash hook: NEVER `cut -c`",
    "A Python regex `\\b` boundary silently fails right after `_`",
    "An epoch-hour bucket",
    "Claude Code COLLAPSES a long literal",
    "A classifier-style hook (allow-list / block-list over real commands) can "
    "only be verified by REPLAYING a real command CORPUS",
]


class TestDevelopmentRulesMoved(TestCase):
    def test_the_rule_file_exists_and_is_path_scoped(self):
        self.assertTrue(
            INTERNALS.exists(), "the internals gotchas need a surface that loads"
        )
        text = INTERNALS.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "needs YAML frontmatter")
        head = text.split("---")[1]
        self.assertIn("paths:", head)
        for glob in ["airuleset.py", "watchdog/**", "hooks/**", "notify/**",
                     "filedrop/**", "tests/**"]:
            self.assertIn(glob, head, f"{glob} must trigger the internals rule")

    def test_paths_are_not_effectively_unconditional(self):
        """`paths: "**"` silently turns the rule back into an always-on file."""
        head = INTERNALS.read_text(encoding="utf-8").split("---")[1]
        globs = re.findall(r'"([^"]+)"', head)
        self.assertTrue(globs)
        self.assertNotIn("**", globs, "a bare ** glob makes the rule always-on")

    def test_every_anchor_survived_the_move_verbatim(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in ([ROOT / ".claude" / "rules" / "airuleset-internals.md"] + sorted((ROOT / ".claude" / "rules").glob("internals-*.md")) + [ROOT / ".claude" / "rules-reference" / "internals-archive.md"]) if p.exists())  # #482: split across surfaces
        for anchor in INTERNALS_ANCHORS:
            self.assertIn(anchor, text, f"lost in the move: {anchor[:60]}")

    def test_claude_md_no_longer_carries_the_bullets(self):
        text = read("CLAUDE.md")
        for anchor in INTERNALS_ANCHORS:
            self.assertNotIn(
                anchor, text, f"still on the always-on prefix: {anchor[:60]}"
            )

    def test_claude_md_points_at_the_new_home(self):
        text = read("CLAUDE.md")
        self.assertIn(".claude/rules/airuleset-internals.md", text)

    def test_claude_md_shrinks_below_a_quarter_of_its_old_size(self):
        self.assertLess(
            len(read("CLAUDE.md")),
            20000,
            "the project CLAUDE.md is loaded on every turn of every session here",
        )

    def test_sections_that_must_stay_inline_are_untouched(self):
        text = read("CLAUDE.md")
        for keep in [
            "## Overview",
            "## Services",
            "## Structure",
            "## Commands",
            "## Deployment Policy",
            "## Rule intake gate",
            "## Skill Ownership",
            "Mechanically checkable?",
            "originating incident + date",
        ]:
            self.assertIn(keep, text, f"must remain always-on: {keep}")

    def test_the_rule_file_is_tracked_by_git(self):
        """.claude/ is gitignored — the carve-out is what deploys this file."""
        ignore = read(".gitignore")
        self.assertIn("!.claude/rules/", ignore)


class TestProductDocsDropped(TestCase):
    """claude-code-tooling.md carried four blocks of Claude Code PRODUCT
    documentation — what a feature is, not how the agent must behave. They
    changed no behaviour and were locked by no test (verified: zero references
    anywhere in tests/, modules/, skills/, profiles/).
    """

    def test_product_documentation_subsections_are_gone(self):
        t = read("modules/core/claude-code-tooling.md")
        for gone in [
            "#### Recaps",
            "#### `/focus` mode",
            "#### `--channels` flag",
            "#### `/fewer-permission-prompts` skill",
        ]:
            self.assertNotIn(gone, t, f"product doc still on the prefix: {gone}")

    def test_the_behavioural_sections_stay(self):
        t = read("modules/core/claude-code-tooling.md")
        for keep in [
            "#### Auto Mode (Shift+Tab in CLI)",
            "#### Effort levels",
            "#### Dynamic Workflows (the `Workflow` tool)",
            "#### Autonomous Goals (`/goal`)",
            "#### Verification tools",
            # anchor updated for the 2026-08-13 Opus 5 ban rewrite: the
            # fable-stage guidance survives, now phrased as the gated
            # judgment-stage rule (same behavioural content, new lineup)
            "never bake in an ungated Fable stage",
            "Applies to all rewordings and semantic equivalents",
        ]:
            self.assertIn(keep, t, f"behavioural content must stay: {keep}")


class TestNotificationInternalsMoved(TestCase):
    """milestone-notifications.md described what three shell scripts do
    INTERNALLY. The hooks run identically whether or not the model read that —
    so it belongs in the notification-mechanics skill, which the #91 trigger
    table loads on `airuleset.py notify` / `notify-discord`.
    """

    MECHANICS = "skills/notification-mechanics/SKILL.md"

    def test_hook_internals_left_the_always_on_module(self):
        t = read("modules/core/milestone-notifications.md")
        for gone in [
            "`notify-discord-pending.sh` (Stop) scans the message",
            "`notify-discord.sh` (Notification : idle_prompt) sends the pending",
            "`notify-discord-send.sh` is the single send path both call",
        ]:
            self.assertNotIn(gone, t, f"hook internals still on the prefix: {gone}")

    def test_hook_internals_landed_in_the_mechanics_skill_verbatim(self):
        t = read(self.MECHANICS)
        for anchor in [
            "`notify-discord-pending.sh` (Stop) scans the message",
            "`notify-discord-send.sh` is the single send path both call",
            "clear-question-dedup.sh",
        ]:
            self.assertIn(anchor, t, f"lost in the move: {anchor}")

    def test_the_behavioural_core_stays_inline(self):
        t = read("modules/core/milestone-notifications.md")
        for keep in [
            "do NOT call the discord `reply` tool",
            "❓ ASKED",
            "ARMED GOAL",
            "Slovak",
            "24/7",   # #791: replaces the removed "Sleep window" anchor
        ]:
            self.assertIn(keep, t, f"behaviour must stay always-on: {keep}")

    def test_the_module_points_at_the_mechanics_skill(self):
        t = read("modules/core/milestone-notifications.md")
        self.assertIn("notification-mechanics", t)


if __name__ == "__main__":
    main()
