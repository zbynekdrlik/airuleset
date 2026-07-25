"""#40 — `rules/*.md` path-scoped rules referenced by the universal profile
were parsed by `categorize_entries` but `cmd_install` silently discarded them
(`modules, _rules = categorize_entries(...)` — the underscore prefix meant
"deliberately unused"). Live-verified on dev1 (2026-07-25): `~/.claude/rules/`
did not exist and `~/.claude/settings.json` had no `rules` key, so every
`rules/*.md` file (no-continue-on-error, coverage-thresholds,
browser-console-zero-errors, e2e-real-user-testing, database-migrations) was
installed nowhere and never enforced.

Confirmed against the installed Claude Code binary (2.1.220, `strings` +
byte-offset inspection): CC has a native "User"-scope rules directory,
computed as `join(<user config base>, "rules")` — the exact same base
function used to compute the "User" CLAUDE.md path (`join(<user config
base>, "CLAUDE.md")`), which is the well-known `~/.claude/CLAUDE.md`. So the
"User" rules dir is `~/.claude/rules`, exactly matching what airuleset always
intended to install into. This is the fix: mirror the existing
skill-symlinking pattern in `cmd_install` for `rules/*.md`.
"""

import os
import sys
import inspect
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class TestSymlinkGlobalRules(TestCase):
    def test_symlinks_each_rule_entry_into_claude_rules_dir(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            repo_dir = airuleset.REPO_DIR
            entries = ["rules/no-continue-on-error.md",
                       "rules/database-migrations.md"]
            airuleset.symlink_global_rules(entries, claude_dir, repo_dir)
            for entry in entries:
                link = claude_dir / "rules" / Path(entry).name
                self.assertTrue(link.is_symlink(), f"{link} is not a symlink")
                self.assertEqual(Path(os.readlink(link)), repo_dir / entry)

    def test_idempotent_second_call_is_a_noop_ok(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            repo_dir = airuleset.REPO_DIR
            entries = ["rules/no-continue-on-error.md"]
            airuleset.symlink_global_rules(entries, claude_dir, repo_dir)
            lines = airuleset.symlink_global_rules(entries, claude_dir, repo_dir)
            self.assertTrue(any("OK rule" in line for line in lines), lines)

    def test_backs_up_existing_non_symlink_file(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            (claude_dir / "rules").mkdir(parents=True)
            existing = claude_dir / "rules" / "no-continue-on-error.md"
            existing.write_text("stale hand-edited content")
            airuleset.symlink_global_rules(
                ["rules/no-continue-on-error.md"], claude_dir, airuleset.REPO_DIR)
            self.assertTrue(existing.is_symlink())
            backup = existing.with_suffix(".md.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(), "stale hand-edited content")

    def test_prunes_airuleset_owned_symlink_no_longer_referenced(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            repo_dir = airuleset.REPO_DIR
            airuleset.symlink_global_rules(
                ["rules/no-continue-on-error.md", "rules/database-migrations.md"],
                claude_dir, repo_dir)
            airuleset.symlink_global_rules(
                ["rules/no-continue-on-error.md"], claude_dir, repo_dir)
            self.assertFalse(
                (claude_dir / "rules" / "database-migrations.md").exists())
            self.assertTrue(
                (claude_dir / "rules" / "no-continue-on-error.md").exists())

    def test_never_prunes_a_foreign_non_airuleset_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            (claude_dir / "rules").mkdir(parents=True)
            foreign_target = Path(d) / "somewhere-else.md"
            foreign_target.write_text("not ours")
            foreign_link = claude_dir / "rules" / "foreign.md"
            foreign_link.symlink_to(foreign_target)
            airuleset.symlink_global_rules([], claude_dir, airuleset.REPO_DIR)
            self.assertTrue(foreign_link.exists())

    def test_skips_missing_source_without_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            claude_dir = Path(d) / "claudehome"
            lines = airuleset.symlink_global_rules(
                ["rules/does-not-exist.md"], claude_dir, airuleset.REPO_DIR)
            self.assertTrue(any("SKIP rule" in line for line in lines), lines)
            self.assertFalse((claude_dir / "rules" / "does-not-exist.md").exists())


class TestCmdInstallWiresGlobalRules(TestCase):
    def test_cmd_install_calls_symlink_global_rules(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("symlink_global_rules(", src)


class TestCmdDiffShowsGlobalRules(TestCase):
    def test_cmd_diff_previews_rules_symlinks(self):
        src = inspect.getsource(airuleset.cmd_diff)
        self.assertIn("rules/", src)
        self.assertIn(".claude/rules", src)


class TestUniversalProfileRulesHaveAHome(TestCase):
    """Regression proof: every rules/*.md entry the universal profile
    references now has somewhere it gets installed to (was previously
    silently discarded by cmd_install)."""

    def test_universal_profile_actually_references_rules(self):
        _modules, rules = airuleset.categorize_entries(
            airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE))
        self.assertGreater(len(rules), 0)

    def test_rules_dir_constant_exists(self):
        self.assertEqual(airuleset.RULES_DIR, airuleset.CLAUDE_DIR / "rules")


if __name__ == "__main__":
    main()
