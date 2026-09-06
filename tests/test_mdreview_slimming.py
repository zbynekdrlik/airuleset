"""#908 — tests for the mandatory slimming pass.

Tests:
- slimming_candidates() identifies hook-enforced modules
- slimming_candidates() identifies reference-growth files
- slimming_candidates() returns empty when no candidates match
- context_snapshot() returns the expected shape
- act_on_due() marks model-generation triggers with slimming_required
- act_on_due() includes slim count in per-box summary
- Slimming section present in audit artifact shape
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class TestSlimmingCandidates(unittest.TestCase):
    """slimming_candidates() identifies hook-enforced modules."""

    def test_hook_enforced_module_flagged(self):
        """A module in the inventory with a matching hook is flagged."""
        from cli_mdreview_audit import slimming_candidates

        # Create a temp hooks dir with a known hook
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            (hooks_dir / "block-test-skips.sh").write_text("#!/bin/bash\n")

            # Build an inventory with the matching module
            mod_path = str(
                (REPO / "modules/ci/test-strictness.md").resolve())
            inventory = {
                "global_modules": {mod_path: 500},
            }

            result = slimming_candidates(inventory, hooks_dir=hooks_dir)
            self.assertTrue(len(result) > 0,
                            "hook-enforced module should produce a candidate")
            found = any(c["category"] == "hook-enforced"
                        and "test-strictness" in c["path"]
                        for c in result)
            self.assertTrue(found,
                            f"expected hook-enforced candidate, got {result}")

    def test_no_matching_hook_no_candidate(self):
        """A module WITHOUT a matching hook is NOT flagged."""
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            # No hooks present

            mod_path = str(
                (REPO / "modules/ci/test-strictness.md").resolve())
            inventory = {
                "global_modules": {mod_path: 500},
            }

            result = slimming_candidates(inventory, hooks_dir=hooks_dir)
            hook_enforced = [c for c in result
                            if c["category"] == "hook-enforced"]
            self.assertEqual(hook_enforced, [],
                             "no matching hook → no candidate")

    def test_module_not_in_inventory_no_candidate(self):
        """A hook exists but the module is NOT in the inventory."""
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            (hooks_dir / "block-test-skips.sh").write_text("#!/bin/bash\n")

            inventory = {"global_modules": {}}

            result = slimming_candidates(inventory, hooks_dir=hooks_dir)
            hook_enforced = [c for c in result
                            if c["category"] == "hook-enforced"]
            self.assertEqual(hook_enforced, [],
                             "module not in inventory → no candidate")

    def test_reference_growth_flagged(self):
        """A .claude/rules-reference file > 50 KB is flagged."""
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            with mock.patch("cli_mdreview_audit.REPO_DIR",
                            Path(tmpdir)):
                ref_dir = Path(tmpdir) / ".claude" / "rules-reference"
                ref_dir.mkdir(parents=True)
                big_file = ref_dir / "big-history.md"
                big_file.write_text("x" * 60000)

                result = slimming_candidates(
                    {"global_modules": {}}, hooks_dir=hooks_dir)
                ref_growth = [c for c in result
                              if c["category"] == "reference-growth"]
                self.assertTrue(len(ref_growth) > 0,
                                "large reference file should produce "
                                "a candidate")

    def test_small_reference_not_flagged(self):
        """A .claude/rules-reference file <= 50 KB is NOT flagged."""
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            with mock.patch("cli_mdreview_audit.REPO_DIR",
                            Path(tmpdir)):
                ref_dir = Path(tmpdir) / ".claude" / "rules-reference"
                ref_dir.mkdir(parents=True)
                small_file = ref_dir / "small.md"
                small_file.write_text("x" * 1000)

                result = slimming_candidates(
                    {"global_modules": {}}, hooks_dir=hooks_dir)
                ref_growth = [c for c in result
                              if c["category"] == "reference-growth"]
                self.assertEqual(ref_growth, [],
                                 "small reference file → no candidate")

    def test_verdict_hint_never_remove(self):
        """verdict_hint is never 'remove' — removal requires cited evidence
        from the /mdreview session, not the automated scan."""
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            for hook, _, _ in [
                ("block-test-skips.sh", "", ""),
                ("block-history-rewrite.sh", "", ""),
            ]:
                (hooks_dir / hook).write_text("#!/bin/bash\n")

            mod1 = str(
                (REPO / "modules/ci/test-strictness.md").resolve())
            mod2 = str(
                (REPO / "modules/git/commit-conventions.md").resolve())
            inventory = {"global_modules": {mod1: 500, mod2: 300}}

            with mock.patch("cli_mdreview_audit.REPO_DIR",
                            Path(tmpdir)):
                ref_dir = Path(tmpdir) / ".claude" / "rules-reference"
                ref_dir.mkdir(parents=True)
                (ref_dir / "big.md").write_text("x" * 60000)

                result = slimming_candidates(inventory, hooks_dir=hooks_dir)

            for c in result:
                self.assertIn(c["verdict_hint"], ("convert", "review"),
                              f"verdict_hint must be convert or review, "
                              f"got {c['verdict_hint']} for {c['path']}")


class TestContextSnapshot(unittest.TestCase):
    """context_snapshot() returns the expected shape."""

    def test_snapshot_shape(self):
        from cli_mdreview_audit import context_snapshot
        snap = context_snapshot()
        self.assertIn("modules_resolved_bytes", snap)
        self.assertIn("module_count", snap)
        self.assertIn("skill_desc_chars", snap)
        self.assertIn("ceilings", snap)
        self.assertIsInstance(snap["modules_resolved_bytes"], int)
        self.assertIsInstance(snap["module_count"], int)
        self.assertIsInstance(snap["ceilings"], dict)

    def test_snapshot_values_positive(self):
        from cli_mdreview_audit import context_snapshot
        snap = context_snapshot()
        self.assertGreater(snap["modules_resolved_bytes"], 0,
                           "should have resolved bytes > 0")
        self.assertGreater(snap["module_count"], 0,
                           "should have modules > 0")


class TestActOnDueSlimmingMarker(unittest.TestCase):
    """act_on_due() marks model-generation triggers."""

    def test_model_generation_includes_slimming_required(self):
        from watchdog.mdreview_cadence import act_on_due

        captured_bodies = []

        def fake_gh_runner(argv):
            if "comment" in argv:
                # Find --body arg
                for i, a in enumerate(argv):
                    if a == "--body" and i + 1 < len(argv):
                        captured_bodies.append(argv[i + 1])
            return ("", 0)

        audit_data = {
            "boxes": [{
                "host": "test",
                "memory": {"R": [], "P": [], "S_flag_count": 0},
                "dedup_pairs": [],
                "slimming": {
                    "candidates": [{"path": "x", "category": "hook-enforced",
                                    "reason": "test", "verdict_hint": "convert"}],
                    "context_snapshot": {},
                },
            }],
            "skipped": [],
            "failed": [],
        }
        result = act_on_due(123, "model-generation", audit_data,
                            gh_runner=fake_gh_runner)
        self.assertTrue(result)
        self.assertTrue(len(captured_bodies) > 0, "should post a comment")
        body = captured_bodies[0]
        self.assertIn("slimming_required: true", body)

    def test_30d_does_not_include_slimming_required(self):
        from watchdog.mdreview_cadence import act_on_due

        captured_bodies = []

        def fake_gh_runner(argv):
            if "comment" in argv:
                for i, a in enumerate(argv):
                    if a == "--body" and i + 1 < len(argv):
                        captured_bodies.append(argv[i + 1])
            return ("", 0)

        audit_data = {
            "boxes": [{
                "host": "test",
                "memory": {"R": [], "P": [], "S_flag_count": 0},
                "dedup_pairs": [],
                "slimming": {
                    "candidates": [],
                    "context_snapshot": {},
                },
            }],
            "skipped": [],
            "failed": [],
        }
        result = act_on_due(123, "30d", audit_data,
                            gh_runner=fake_gh_runner)
        self.assertTrue(result)
        body = captured_bodies[0]
        self.assertNotIn("slimming_required", body)

    def test_slim_count_in_summary(self):
        """The per-box summary line includes slim=N."""
        from watchdog.mdreview_cadence import act_on_due

        captured_bodies = []

        def fake_gh_runner(argv):
            if "comment" in argv:
                for i, a in enumerate(argv):
                    if a == "--body" and i + 1 < len(argv):
                        captured_bodies.append(argv[i + 1])
            return ("", 0)

        audit_data = {
            "boxes": [{
                "host": "testbox",
                "memory": {"R": [1, 2], "P": [3], "S_flag_count": 0},
                "dedup_pairs": [1],
                "slimming": {
                    "candidates": [{"a": 1}, {"b": 2}, {"c": 3}],
                    "context_snapshot": {},
                },
            }],
            "skipped": [],
            "failed": [],
        }
        act_on_due(99, "30d", audit_data, gh_runner=fake_gh_runner)
        body = captured_bodies[0]
        self.assertIn("slim=3", body)


class TestAuditArtifactIncludesSlimming(unittest.TestCase):
    """The single-box audit output includes slimming data."""

    def test_single_box_has_slimming_key(self):
        """cmd_mdreview_audit's single-box path includes slimming."""
        import cli_mdreview_audit as mod

        # Mock the heavy parts
        with mock.patch.object(mod, "inventory_box",
                               return_value={"global_modules": {}}), \
             mock.patch.object(mod, "_scan_memory_all",
                               return_value={"R": [], "P": [],
                                             "S_flag_count": 0,
                                             "candidates": []}), \
             mock.patch.object(mod, "_collect_dedup_surfaces",
                               return_value={}), \
             mock.patch.object(mod, "dedup_candidates",
                               return_value=[]), \
             mock.patch.object(mod, "_compute_zero_caller_skills",
                               return_value=[]), \
             mock.patch.object(mod, "slimming_candidates",
                               return_value=[{"path": "test",
                                              "category": "hook-enforced",
                                              "reason": "r",
                                              "verdict_hint": "convert"}]), \
             mock.patch.object(mod, "context_snapshot",
                               return_value={"modules_resolved_bytes": 100,
                                             "module_count": 5,
                                             "skill_desc_chars": 50,
                                             "ceilings": {}}), \
             mock.patch("cli_context_baseline._load_registry",
                        return_value=({}, "ok")), \
             mock.patch("socket.gethostname", return_value="test"):
            import io
            captured = io.StringIO()
            args = type("A", (), {
                "fleet": False, "json_output": True,
                "project": None,
            })()
            with mock.patch("sys.stdout", captured):
                mod.cmd_mdreview_audit(args)
            output = captured.getvalue()
            data = json.loads(output)
            self.assertIn("slimming", data)
            self.assertIn("candidates", data["slimming"])
            self.assertIn("context_snapshot", data["slimming"])
            self.assertEqual(len(data["slimming"]["candidates"]), 1)


class TestSkillBodySlimmingStep(unittest.TestCase):
    """The /mdreview skill body includes the slimming step."""

    def test_skill_has_slimming_step(self):
        skill_path = REPO / "skills" / "mdreview" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("Step 5c", text)
        self.assertIn("SLIMMING PASS", text)

    def test_skill_slimming_mandatory_rule(self):
        skill_path = REPO / "skills" / "mdreview" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("Slimming pass is MANDATORY", text)
        self.assertIn("model-generation", text)

    def test_skill_measurability_rule(self):
        skill_path = REPO / "skills" / "mdreview" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("context-baseline bytes BEFORE/AFTER", text)

    def test_skill_artifact_description_includes_slimming(self):
        skill_path = REPO / "skills" / "mdreview" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("Slimming candidates", text)
        self.assertIn("Context snapshot", text)


class TestRealTreeSlimmingCandidates(unittest.TestCase):
    """Run slimming_candidates against the REAL repo layout — H1 fix proof."""

    def test_real_tree_finds_reference_growth(self):
        """The real .claude/rules-reference/internals-archive.md > 50 KB
        must appear as a reference-growth candidate."""
        from cli_mdreview_audit import slimming_candidates
        result = slimming_candidates({"global_modules": {}})
        ref_growth = [c for c in result
                      if c["category"] == "reference-growth"]
        archive_hit = any("internals-archive" in c["path"]
                          for c in ref_growth)
        self.assertTrue(archive_hit,
                        f"real tree should find internals-archive.md "
                        f"(>50 KB); got {ref_growth}")


class TestHookProsePairsExist(unittest.TestCase):
    """Pin test: every entry in _HOOK_PROSE_PAIRS names files that exist."""

    def test_all_hooks_exist(self):
        from cli_mdreview_audit import _HOOK_PROSE_PAIRS, REPO_DIR
        for hook_basename, _mod, _desc in _HOOK_PROSE_PAIRS:
            hook_path = REPO_DIR / "hooks" / hook_basename
            self.assertTrue(hook_path.exists(),
                            f"hook {hook_basename} does not exist "
                            f"at {hook_path}")

    def test_all_modules_exist(self):
        from cli_mdreview_audit import _HOOK_PROSE_PAIRS, REPO_DIR
        for _hook, mod_relpath, _desc in _HOOK_PROSE_PAIRS:
            mod_path = REPO_DIR / mod_relpath
            self.assertTrue(mod_path.exists(),
                            f"module {mod_relpath} does not exist "
                            f"at {mod_path}")


class TestStubAwareHint(unittest.TestCase):
    """Stubs (<= 1500 B) get 'review' not 'convert' (M2 fix)."""

    def test_small_module_gets_review_hint(self):
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            (hooks_dir / "block-test-skips.sh").write_text("#!/bin/bash\n")

            mod_path = str(
                (REPO / "modules/ci/test-strictness.md").resolve())
            # Small module (stub-like, <= 1500 B)
            inventory = {"global_modules": {mod_path: 800}}

            result = slimming_candidates(inventory, hooks_dir=hooks_dir)
            hook_hits = [c for c in result
                         if c["category"] == "hook-enforced"]
            self.assertTrue(len(hook_hits) > 0)
            self.assertEqual(hook_hits[0]["verdict_hint"], "review")

    def test_large_module_gets_convert_hint(self):
        from cli_mdreview_audit import slimming_candidates

        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir)
            (hooks_dir / "block-test-skips.sh").write_text("#!/bin/bash\n")

            mod_path = str(
                (REPO / "modules/ci/test-strictness.md").resolve())
            # Large module (> 1500 B)
            inventory = {"global_modules": {mod_path: 3000}}

            result = slimming_candidates(inventory, hooks_dir=hooks_dir)
            hook_hits = [c for c in result
                         if c["category"] == "hook-enforced"]
            self.assertTrue(len(hook_hits) > 0)
            self.assertEqual(hook_hits[0]["verdict_hint"], "convert")


if __name__ == "__main__":
    unittest.main()
