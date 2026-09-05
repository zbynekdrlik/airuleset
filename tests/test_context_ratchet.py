"""tests/test_context_ratchet.py — context-baseline + skill-usage (#857).

RED/GREEN tests per the design's 9-item test list. Hermetic: patched HOME,
fake skills dir, fixture jsonl. Never touches the real home or projects.
"""

import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

# The real transcript record structure for Skill tool_use:
# {"type": "assistant", "message": {"role": "assistant", "content": [
#   {"type": "tool_use", "name": "Skill", "input": {"skill": "<name>"}}
# ]}}
# Pinned by the test below — the input key is "skill" (NOT "name", "id", etc.)

def _recent_ts():
    """A timestamp guaranteed to be within the last 24h for test fixtures."""
    import datetime
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return now.isoformat()


FIXTURE_SKILL_RECORD = json.dumps({
    "type": "assistant",
    "timestamp": _recent_ts(),
    "message": {
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "toolu_test",
            "name": "Skill",
            "input": {"skill": "playbook-review", "args": ""},
        }],
    },
})

FIXTURE_SLASH_RECORD = json.dumps({
    "type": "user",
    "timestamp": _recent_ts(),
    "message": {
        "role": "user",
        "content": "<command-name>review</command-name>",
    },
})

FIXTURE_OLD_SKILL_RECORD = json.dumps({
    "type": "assistant",
    "timestamp": "2026-06-01T01:00:00Z",
    "message": {
        "role": "assistant",
        "content": [{
            "type": "tool_use",
            "id": "toolu_old",
            "name": "Skill",
            "input": {"skill": "ci-monitor"},
        }],
    },
})


class TestBytesToTokens(unittest.TestCase):
    """tokens = bytes // 4."""

    def test_exact(self):
        import cli_context_baseline as cb
        self.assertEqual(cb.bytes_to_tokens(329116), 82279)

    def test_zero(self):
        import cli_context_baseline as cb
        self.assertEqual(cb.bytes_to_tokens(0), 0)


class TestImportResolution(unittest.TestCase):
    """Recursive @ resolution: nested summed, missing flagged,
    self-import terminates."""

    def test_nested_imports_summed(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            child = root / "child.md"
            child.write_text("child content here", encoding="utf-8")
            parent = root / "parent.md"
            parent.write_text(f"@{child}\nparent text", encoding="utf-8")

            files, missing = cb.resolve_imports_recursive(parent)
            self.assertEqual(len(missing), 0)
            # Both parent and child counted
            self.assertEqual(len(files), 2)
            total = sum(files.values())
            self.assertGreater(total, 0)

    def test_missing_import_flagged(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent = root / "parent.md"
            parent.write_text("@/nonexistent/file.md\ntext",
                              encoding="utf-8")

            files, missing = cb.resolve_imports_recursive(parent)
            self.assertEqual(len(missing), 1)
            self.assertIn("/nonexistent/file.md", missing[0])

    def test_self_import_terminates(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self_ref = root / "self.md"
            self_ref.write_text(f"@{self_ref}\ncontent", encoding="utf-8")

            files, missing = cb.resolve_imports_recursive(self_ref)
            # Should not infinite loop; file counted once
            self.assertEqual(len(files), 1)
            self.assertEqual(len(missing), 0)

    def test_depth_cap(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create a chain deeper than MAX_IMPORT_DEPTH
            files_list = []
            for i in range(cb.MAX_IMPORT_DEPTH + 3):
                f = root / f"level{i}.md"
                files_list.append(f)
            # Write chain: each imports the next
            for i, f in enumerate(files_list[:-1]):
                f.write_text(f"@{files_list[i + 1]}\nlevel {i}",
                             encoding="utf-8")
            files_list[-1].write_text("leaf", encoding="utf-8")

            result, missing = cb.resolve_imports_recursive(files_list[0])
            # Should stop at depth cap, not include all
            self.assertLess(len(result),
                            cb.MAX_IMPORT_DEPTH + 3)


class TestPathsFrontmatter(unittest.TestCase):
    """paths: rule excluded / bare rule included."""

    def test_paths_rule_excluded(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            rules = proj / ".claude" / "rules"
            rules.mkdir(parents=True)
            # File WITH paths: frontmatter
            (rules / "scoped.md").write_text(
                "---\npaths:\n  - src/**\n---\nContent",
                encoding="utf-8")
            # File WITHOUT paths: frontmatter
            (rules / "always-on.md").write_text(
                "---\ndescription: always\n---\nContent",
                encoding="utf-8")
            # File with no frontmatter at all
            (rules / "bare.md").write_text(
                "Just content", encoding="utf-8")

            result = cb.always_on_rule_files(proj)
            names = [r.name for r in result]
            self.assertIn("always-on.md", names)
            self.assertIn("bare.md", names)
            self.assertNotIn("scoped.md", names)


class TestSkillDescription(unittest.TestCase):
    """description extraction incl. multi-line + malformed -> 0 flagged."""

    def test_single_line(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "test-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                '---\ndescription: "A test skill"\n---\nBody',
                encoding="utf-8")
            result = cb.measure_skills(Path(td))
            self.assertEqual(result["test-skill"]["chars"],
                             len("A test skill"))
            self.assertFalse(result["test-skill"]["malformed"])

    def test_multi_line(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "ml-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(textwrap.dedent("""\
                ---
                description:
                  This is a multi-line
                  description value
                ---
                Body"""), encoding="utf-8")
            result = cb.measure_skills(Path(td))
            self.assertGreater(result["ml-skill"]["chars"], 0)
            self.assertFalse(result["ml-skill"]["malformed"])

    def test_malformed(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            skill = Path(td) / "bad-skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "No frontmatter at all", encoding="utf-8")
            result = cb.measure_skills(Path(td))
            self.assertEqual(result["bad-skill"]["chars"], 0)
            self.assertTrue(result["bad-skill"]["malformed"])


class TestMemoryKey(unittest.TestCase):
    """MEMORY.md key derivation + project sums."""

    def test_key_derivation(self):
        import cli_context_baseline as cb
        key = cb._memory_key("/home/user/devel/project")
        self.assertEqual(key, "-home-user-devel-project")


class TestJsonSchema(unittest.TestCase):
    """Full schema keys present in measure_box output."""

    def test_schema_keys(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            claude_dir = home / ".claude"
            claude_dir.mkdir()
            claude_md = claude_dir / "CLAUDE.md"
            claude_md.write_text("# Test\n", encoding="utf-8")

            with patch.object(cb, "CLAUDE_DIR", claude_dir), \
                 patch.object(cb, "CLAUDE_MD", claude_md):
                data = cb.measure_box()

            self.assertEqual(data["schema"], 1)
            self.assertIn("host", data)
            self.assertIn("date", data)
            self.assertIn("global", data)
            g = data["global"]
            self.assertIn("resolved_bytes", g)
            self.assertIn("tokens", g)
            self.assertIn("modules", g)
            self.assertIn("missing", g)
            self.assertEqual(g["tokens"], g["resolved_bytes"] // 4)
            self.assertIn("skills", data)
            self.assertIn("mcp", data)
            self.assertTrue(data["mcp"]["estimate"])


class TestRatchet(unittest.TestCase):
    """Ratchet: over-ceiling fails, --update lowers only,
    raise refused without --allow-raise, accepted with it."""

    def test_over_ceiling_fails(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            ratchet_path = Path(td) / "context_ratchet.json"
            ratchet_path.write_text(json.dumps({
                "ceilings": {
                    "modules_resolved_bytes": 100,
                    "skill_desc_chars": 10,
                    "module_count": 1,
                }
            }), encoding="utf-8")

            with patch.object(cb, "CONTEXT_RATCHET_PATH", ratchet_path):
                # Current values will be much higher than 100/10/1
                ok, details = cb.check_ratchet()
                self.assertFalse(ok)
                over_count = sum(1 for d in details if d.startswith("OVER"))
                self.assertGreater(over_count, 0)

    def test_update_lowers_only(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            ratchet_path = Path(td) / "context_ratchet.json"
            # Set ceilings much higher than current
            ratchet_path.write_text(json.dumps({
                "ceilings": {
                    "modules_resolved_bytes": 999999999,
                    "skill_desc_chars": 999999,
                    "module_count": 9999,
                }
            }), encoding="utf-8")

            with patch.object(cb, "CONTEXT_RATCHET_PATH", ratchet_path):
                updated, details = cb.update_ratchet()
                self.assertTrue(updated)
                # All should be lowered
                lowered = sum(1 for d in details if "lowered" in d)
                self.assertEqual(lowered, 3)

                # Read back and verify lowered
                data = json.loads(ratchet_path.read_text())
                self.assertLess(data["ceilings"]["modules_resolved_bytes"],
                                999999999)

    def test_raise_refused_without_flag(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            ratchet_path = Path(td) / "context_ratchet.json"
            ratchet_path.write_text(json.dumps({
                "ceilings": {
                    "modules_resolved_bytes": 1,
                    "skill_desc_chars": 1,
                    "module_count": 1,
                }
            }), encoding="utf-8")

            with patch.object(cb, "CONTEXT_RATCHET_PATH", ratchet_path):
                updated, details = cb.update_ratchet()
                refused = sum(1 for d in details if "REFUSED" in d)
                self.assertEqual(refused, 3)
                # Values NOT changed
                data = json.loads(ratchet_path.read_text())
                self.assertEqual(
                    data["ceilings"]["modules_resolved_bytes"], 1)

    def test_raise_accepted_with_flag(self):
        import cli_context_baseline as cb
        with tempfile.TemporaryDirectory() as td:
            ratchet_path = Path(td) / "context_ratchet.json"
            ratchet_path.write_text(json.dumps({
                "ceilings": {
                    "modules_resolved_bytes": 1,
                    "skill_desc_chars": 1,
                    "module_count": 1,
                }
            }), encoding="utf-8")

            with patch.object(cb, "CONTEXT_RATCHET_PATH", ratchet_path):
                updated, details = cb.update_ratchet(
                    allow_raise="test reason")
                self.assertTrue(updated)
                raised = sum(1 for d in details if "RAISED" in d)
                self.assertEqual(raised, 3)
                data = json.loads(ratchet_path.read_text())
                self.assertGreater(
                    data["ceilings"]["modules_resolved_bytes"], 1)


class TestFleet(unittest.TestCase):
    """Fleet with injected runner: one attempt, failed host recorded
    not retried, paused host absent."""

    def test_injected_runner(self):
        import cli_context_baseline as cb
        call_count = {"n": 0}

        def fake_runner(host):
            call_count["n"] += 1
            name = host.get("name", "test")
            if name == "fail-host":
                return ("", 1)
            return (json.dumps({
                "schema": 1,
                "host": name,
                "date": "2026-09-01",
                "global": {"resolved_bytes": 100, "tokens": 25,
                            "modules": 1, "missing": []},
                "skills": {"count": 0, "desc_chars": 0, "per_skill": {}},
                "projects": [],
                "mcp": {"estimate_bytes": 0, "servers": 0, "estimate": True},
            }), 0)

        fake_hosts = [
            {"name": "good-host", "addr": "1.2.3.4"},
            {"name": "fail-host", "addr": "5.6.7.8"},
        ]

        with patch("cli_remote._deployable_hosts", return_value=fake_hosts):
            data = cb.run_fleet(runner=fake_runner)

        self.assertEqual(data["schema"], 1)
        self.assertEqual(len(data["boxes"]), 1)
        self.assertEqual(data["boxes"][0]["host"], "good-host")
        self.assertEqual(len(data["failed"]), 1)
        self.assertEqual(data["failed"][0]["host"], "fail-host")
        # Each host called exactly once
        self.assertEqual(call_count["n"], 2)


class TestSeedConsistency(unittest.TestCase):
    """Committed ceilings >= current repo measurement."""

    def test_committed_ceilings_cover_current(self):
        import cli_context_baseline as cb
        ratchet = cb.load_ratchet()
        ceilings = ratchet.get("ceilings", {})
        if not ceilings:
            self.skipTest("no ceilings committed yet")

        current = cb._measure_repo_ceilings()
        for key, ceiling in ceilings.items():
            actual = current.get(key, 0)
            self.assertLessEqual(
                actual, ceiling,
                f"{key}: actual {actual} > ceiling {ceiling}")


class TestSkillUsageScan(unittest.TestCase):
    """skill-usage: Skill tool_use counted, slash counted, old-mtime
    file skipped, out-of-window ts excluded, distinct-session count."""

    def test_skill_input_key_is_skill(self):
        """PIN: the Skill tool_use input key is 'skill' (from a real
        captured transcript record)."""
        rec = json.loads(FIXTURE_SKILL_RECORD)
        content = rec["message"]["content"]
        skill_item = [c for c in content
                      if c.get("name") == "Skill"][0]
        self.assertIn("skill", skill_item["input"])
        self.assertEqual(skill_item["input"]["skill"], "playbook-review")

    def test_skill_counted(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            jf = proj / "session1.jsonl"
            jf.write_text(FIXTURE_SKILL_RECORD + "\n", encoding="utf-8")
            # Touch to make mtime recent
            os.utime(jf, None)

            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            self.assertIn("playbook-review", data["skills"])
            self.assertEqual(data["skills"]["playbook-review"]["calls"], 1)
            self.assertEqual(
                data["skills"]["playbook-review"]["sessions"], 1)

    def test_slash_counted(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            jf = proj / "session2.jsonl"
            jf.write_text(FIXTURE_SLASH_RECORD + "\n", encoding="utf-8")
            os.utime(jf, None)

            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            self.assertIn("review", data["slash"])
            self.assertEqual(data["slash"]["review"]["calls"], 1)

    def test_old_mtime_skipped(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            jf = proj / "old-session.jsonl"
            jf.write_text(FIXTURE_SKILL_RECORD + "\n", encoding="utf-8")
            # Set mtime to 100 days ago
            old_time = os.path.getmtime(str(jf)) - 100 * 86400
            os.utime(jf, (old_time, old_time))

            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            self.assertEqual(data["scanned_files"], 0)
            self.assertEqual(len(data["skills"]), 0)

    def test_out_of_window_ts_excluded(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            jf = proj / "session3.jsonl"
            # File mtime is recent but record timestamp is old
            jf.write_text(FIXTURE_OLD_SKILL_RECORD + "\n",
                          encoding="utf-8")
            os.utime(jf, None)

            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            # File is scanned (recent mtime) but record excluded (old ts)
            self.assertEqual(data["scanned_files"], 1)
            self.assertEqual(len(data["skills"]), 0)

    def test_distinct_session_count(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            # Same skill in two different session files
            jf1 = proj / "session-a.jsonl"
            jf1.write_text(FIXTURE_SKILL_RECORD + "\n", encoding="utf-8")
            jf2 = proj / "session-b.jsonl"
            jf2.write_text(FIXTURE_SKILL_RECORD + "\n", encoding="utf-8")
            os.utime(jf1, None)
            os.utime(jf2, None)

            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            self.assertEqual(
                data["skills"]["playbook-review"]["calls"], 2)
            self.assertEqual(
                data["skills"]["playbook-review"]["sessions"], 2)


class TestSkillUsageSchema(unittest.TestCase):
    """Full skill-usage --json schema keys."""

    def test_schema_keys(self):
        import cli_skill_usage as su
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "projects" / "-test-proj"
            proj.mkdir(parents=True)
            data = su.scan_usage(days=1, projects_dir=Path(td) / "projects")
            self.assertEqual(data["schema"], 1)
            self.assertIn("host", data)
            self.assertIn("days", data)
            self.assertIn("window_start", data)
            self.assertIn("scanned_files", data)
            self.assertIn("skills", data)
            self.assertIn("slash", data)


class TestPushSummary(unittest.TestCase):
    """Push summary line format."""

    def test_summary_format(self):
        import cli_context_baseline as cb
        line = cb.push_summary_line()
        self.assertTrue(line.startswith("context-baseline:"))
        self.assertIn("tok", line)


if __name__ == "__main__":
    unittest.main()
