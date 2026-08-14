"""claude-code-log skill (#420 — adopt claude-code-log for transcript
browsing/archiving, reconciled with #410 gzip-at-rest; audit #416).

Locks the ADOPT decision so no future edit silently strips the load-bearing
facts: the external tool's identity + run command, and — most important —
the #410 gzip interplay (claude-code-log reads ONLY plain `.jsonl`, a
`.jsonl.gz` is invisible to it, so `gunzip -k` or airuleset's own gzip-aware
`claude-history` is the documented path for an old compressed session). A
model-loaded (user-invocable: false) knowledge/workflow skill, deployed
everywhere on demand — like its cloudflare-api-tokens / view-image-urls /
deliver-files-as-urls siblings.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "claude-code-log" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


class TestSkillRegisteredAndDeployedEverywhere(TestCase):
    def test_in_skill_names(self):
        # the inventory/lock: install/push deploys it, cmd_validate counts it.
        self.assertIn("claude-code-log", airuleset.SKILL_NAMES)

    def test_skill_md_exists(self):
        self.assertTrue(SKILL.exists(), SKILL)

    def test_deploys_to_every_box_including_reduced_authority(self):
        # a hidden on-demand utility skill: no scoping exclusion, so every
        # user's set includes it (transcript browsing can happen anywhere).
        self.assertNotIn("claude-code-log", airuleset.SKILLS_MAINTAINER_ONLY)
        self.assertNotIn("claude-code-log", airuleset.SKILLS_FULL_AUTHORITY_ONLY)
        for u in ("newlevel", "gatekeeper", "montalu", "david", "marek"):
            self.assertIn("claude-code-log",
                          airuleset.skill_names_for_user(u), u)

    def test_hidden_from_slash_picker(self):
        # model-loaded by description, never a user-typed slash command — so it
        # stays out of the picker (matches cloudflare-api-tokens et al.).
        self.assertIn("user-invocable: false", read(SKILL))


class TestLoadBearingKnowledge(TestCase):
    """The facts this skill exists to carry must stay in the body."""

    def test_names_the_external_tool_and_run_command(self):
        t = read(SKILL)
        self.assertIn("daaain/claude-code-log", t)      # the real upstream repo
        self.assertIn("uvx claude-code-log@latest", t)  # the no-install run path

    def test_adopt_dont_build_provenance(self):
        # audit #416 verdict — never re-implement a maintained tool.
        self.assertIn("ADOPT, don't build", read(SKILL))

    def test_gzip_caveat_is_explicit(self):
        # THE crux of #420: claude-code-log cannot read a compressed transcript.
        t = read(SKILL)
        self.assertIn(".jsonl.gz", t)
        self.assertIn("NO gzip support", t)
        # a .gz is skipped, not a crash — same accepted horizon as /resume.
        self.assertIn("silently skipped", t)
        self.assertIn("/resume", t)

    def test_gunzip_workaround_documented(self):
        # the -k flag KEEPS the .gz — the one-liner to browse an old session.
        self.assertIn("gunzip -k", read(SKILL))

    def test_claude_history_is_the_gzip_aware_fallback(self):
        t = read(SKILL)
        self.assertIn("claude-history", t)
        self.assertIn("gzip-aware fallback reader", t)

    def test_410_live_compression_is_report_only_default(self):
        # the reason compatibility is full TODAY: nothing is compressed yet.
        t = read(SKILL)
        self.assertIn("report-only by default", t)
        self.assertIn("AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1", t)

    def test_native_retention_bugs_cited(self):
        # why we don't lean on cleanupPeriodDays.
        t = read(SKILL)
        for issue in ("#58154", "#23710", "#59248"):
            self.assertIn(issue, t)


if __name__ == "__main__":
    main()
