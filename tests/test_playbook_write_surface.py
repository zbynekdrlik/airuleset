"""The playbook mandate writes to a surface that actually loads (#93).

#93 measured the project `CLAUDE.md` growing ~10k tokens/day: `playbook-review`
mandates an append after every ticket and nothing ever removes, so a reference
manual accumulates at system-prompt position and is paid for in EVERY session
of EVERY project — including ones that never touch the area it describes.

#92 item 1 moved airuleset's own accumulated bullets out, but that was a
one-time cleanup: the MANDATE still pointed future writes at the two surfaces
that caused the problem —

  * `.claude/skills/<area>` for gotchas, which #91 PROVED almost never loads
    (a skill body enters context only when the model volunteers a `Skill`
    call; 32 of 53 skills had zero lifetime invocations, 1 of 342 `gh pr
    merge` transcripts ever loaded pr-merge-policy), and
  * project `CLAUDE.md` for "always-rules", the always-on surface itself.

So the structural fix is not "stop writing" — the mandate stays in force. It
is "write where it loads on demand": `.claude/rules/<area>.md` with `paths:`
frontmatter, which Claude Code injects as a nested_memory attachment the
moment a matching file is read, and costs nothing in a session that never
touches those files (#91's proven mechanism, already used by
`.claude/rules/airuleset-internals.md`).

This file locks that the mandate names the loading surface, that it steers
area knowledge AWAY from the always-on file, and that airuleset's own project
CLAUDE.md meets #93's acceptance size.
"""

from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestMandateTargetsThePathScopedSurface(TestCase):
    def test_skill_routes_area_knowledge_to_a_path_scoped_rule(self):
        t = read("skills/playbook-review/SKILL.md")
        self.assertIn(".claude/rules/", t)
        self.assertIn("paths:", t)
        # the routing table must name it as the destination for a gotcha
        self.assertRegex(t, r"(?i)(gotcha|procedure|how-to)[^\n|]*\|[^\n]*\.claude/rules/")

    def test_skill_warns_against_the_always_on_file(self):
        t = read("skills/playbook-review/SKILL.md")
        self.assertRegex(t, r"(?i)CLAUDE\.md[^\n]*(every session|always-on|always on)")

    def test_module_boundary_table_names_the_loading_surface(self):
        t = read("modules/core/project-playbook-maintenance.md")
        self.assertIn(".claude/rules/<area>.md", t)
        self.assertIn("paths:", t)

    def test_module_still_carries_the_mandate_and_its_marker(self):
        # the mandate is NOT weakened — only its destination changes
        t = read("modules/core/project-playbook-maintenance.md")
        self.assertIn("po každom tickete", t)
        self.assertIn("📔 Playbook:", t)

    def test_hook_reason_points_at_the_loading_surface(self):
        # the block message is the ONE piece of guidance a session sees at the
        # moment it must comply — it must not send the write to a dead surface
        t = read("hooks/stop-check-playbook-review.sh")
        self.assertIn(".claude/rules/", t)


class TestProjectClaudeMdMeetsTheAcceptance(TestCase):
    def test_project_claude_md_is_under_the_10k_acceptance(self):
        size = len(read("CLAUDE.md").encode("utf-8"))
        self.assertLess(size, 10_000, f"project CLAUDE.md is {size} B (#93 acceptance: <10k)")

    def test_the_sections_the_acceptance_keeps_are_still_there(self):
        t = read("CLAUDE.md")
        for section in ("## Overview", "## Services", "## Structure", "## Commands",
                        "## Deployment Policy", "## Rule intake gate", "## Skill Ownership"):
            self.assertIn(section, t, section)

    def test_service_internals_moved_verbatim_not_deleted(self):
        # every service still has a home; the deep internals live on the
        # path-scoped surface where they load only when relevant
        internals = read(".claude/rules/airuleset-internals.md")
        for needle in ("FILEDROP_HOSTS", "render_caveman_shim", "compact_boundary",
                       "DISCORD_MIRROR_", "stop-check-playbook-review.sh"):
            self.assertIn(needle, internals, needle)

    def test_claude_md_points_at_the_path_scoped_internals(self):
        t = read("CLAUDE.md")
        self.assertIn(".claude/rules/airuleset-internals.md", t)


if __name__ == "__main__":
    main()
