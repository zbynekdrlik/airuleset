"""#538 — surface each inline subagent's resolved MODEL (+ effort) in the
Claude Code agent strip via the NATIVE `subagentStatusLine` mechanism.

Root cause the module fixes: CC's default agent-strip row is
`name · description · token count` — no model — and airuleset never wired
the native override. `subagentStatusLine` (CC v2.1.205+) delivers a per-task
resolved `model` (+ `effort`, v2.1.214+) on stdin; a small render script
emits `{"id","content"}` lines that replace each row body with one that
leads with the model badge.

These lock the render module's contract (RED before the module exists,
GREEN after) and the settings-reconcile wiring. The module is deliberately
fail-safe: any bad/partial input renders as EMPTY output so Claude Code
keeps its own default rows rather than a broken strip.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subagent_statusline as ss  # noqa: E402


def _plain(s):
    """Strip ANSI SGR + OSC-8 so assertions test the VISIBLE text."""
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = re.sub(r"\x1b\]8;[^\x07]*\x07", "", s)
    return s


class TestShortModel(unittest.TestCase):
    def test_fleet_ids_map_to_family_and_version(self):
        cases = {
            "claude-opus-4-8": "opus-4.8",
            "claude-sonnet-5": "sonnet-5",
            "claude-fable-5-1": "fable-5",
            "claude-haiku-4-5": "haiku-4.5",
            "claude-opus-5": "opus-5",
        }
        for mid, want in cases.items():
            self.assertEqual(ss.short_model(mid), want, mid)

    def test_context_window_suffix_is_stripped(self):
        self.assertEqual(ss.short_model("claude-opus-4-8[1m]"), "opus-4.8")
        self.assertEqual(ss.short_model("claude-fable-5-1[1m]"), "fable-5")

    def test_missing_or_bad_model_is_empty(self):
        for bad in (None, "", "   ", 123, {}):
            self.assertEqual(ss.short_model(bad), "")

    def test_unknown_id_degrades_not_blank(self):
        # an id we don't hardcode must still render SOMETHING, never crash
        out = ss.short_model("claude-newmodel-9-2")
        self.assertTrue(out)
        self.assertIn("newmodel", out)


class TestRenderRow(unittest.TestCase):
    def _task(self, **kw):
        base = {
            "id": "t1", "name": "autopilot-worker", "type": "autopilot-worker",
            "status": "running", "description": "Work issue #538",
            "label": "Reading foo.py", "model": "claude-opus-4-8",
            "effort": "xhigh", "tokenCount": 541000,
        }
        base.update(kw)
        return base

    def test_row_leads_with_model_badge(self):
        row = ss.render_row(self._task(), 120)
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "t1")
        content = _plain(row["content"])
        self.assertIn("opus-4.8", content)
        self.assertIn("autopilot-worker", content)

    def test_effort_appears_when_present(self):
        content = _plain(ss.render_row(self._task(effort="xhigh"), 120)["content"])
        self.assertIn("xhigh", content)

    def test_effort_absent_is_omitted_gracefully(self):
        t = self._task()
        del t["effort"]
        content = _plain(ss.render_row(t, 120)["content"])
        self.assertIn("opus-4.8", content)
        # no stray trailing separator artifact from a missing effort
        self.assertNotIn("opus-4.8·ANSI", content)

    def test_token_count_rendered_compact(self):
        content = _plain(ss.render_row(self._task(tokenCount=541000), 120)["content"])
        self.assertIn("541k", content)
        content_m = _plain(ss.render_row(self._task(tokenCount=2_300_000), 120)["content"])
        self.assertIn("2.3M", content_m)

    def test_no_id_is_skipped(self):
        t = self._task()
        del t["id"]
        self.assertIsNone(ss.render_row(t, 120))

    def test_unresolved_model_keeps_default_row(self):
        # model absent (older CC / not yet resolved) -> None => CC keeps its
        # own default row rather than a WORSE model-less override.
        t = self._task()
        del t["model"]
        self.assertIsNone(ss.render_row(t, 120))

    def test_width_is_respected(self):
        long_label = "Reading " + ("x" * 400)
        content = _plain(ss.render_row(self._task(label=long_label), 60)["content"])
        # visible width fits the given column budget
        self.assertLessEqual(len(content), 60)
        # the important lead (badge + name) survives truncation
        self.assertIn("opus-4.8", content)
        self.assertIn("autopilot-worker", content)

    def test_lead_exceeding_budget_is_hard_clamped(self):
        # review #538 (🔵): the last-resort clamp — badge+name lead alone
        # exceeds `columns`, no activity — must still keep visible width <= budget.
        t = self._task(name="a" * 200, label="", description="", status="")
        content = _plain(ss.render_row(t, 20)["content"])
        self.assertLessEqual(len(content), 20)

    def test_control_chars_never_break_the_row(self):
        # review #538 (🔵): a label/name with a raw newline or ESC must not
        # produce a multi-line / cursor-moving row (CC renders content as-is).
        content = ss.render_row(self._task(label="Reading\nfoo\x1b[2Jbar"), 120)["content"]
        # strip only OUR trailing colour resets; the DATA newline/ESC must be gone
        body = content.replace("\x1b[0m", "").replace("\x1b[2m", "")
        body = re.sub(r"\x1b\[38;5;\d+m", "", body)
        self.assertNotIn("\n", body)
        self.assertNotIn("\x1b[2J", body)
        self.assertIn("Reading foo", _plain(content))

    def test_control_chars_in_name_are_sanitised(self):
        content = _plain(ss.render_row(self._task(name="bad\nname"), 120)["content"])
        self.assertNotIn("\n", content)
        self.assertIn("bad name", content)


class TestRender(unittest.TestCase):
    def test_emits_one_json_line_per_overridable_task(self):
        payload = {
            "columns": 120,
            "tasks": [
                {"id": "a", "name": "autopilot-worker", "model": "claude-opus-4-8",
                 "label": "Editing", "tokenCount": 10000},
                {"id": "b", "name": "general-purpose", "model": "claude-sonnet-5",
                 "label": "Grepping", "tokenCount": 2000},
            ],
        }
        out = ss.render(payload)
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        objs = [json.loads(ln) for ln in lines]
        self.assertEqual({o["id"] for o in objs}, {"a", "b"})
        self.assertIn("opus-4.8", _plain(objs[0]["content"]))
        self.assertIn("sonnet-5", _plain(objs[1]["content"]))

    def test_accepts_json_string_payload(self):
        payload = json.dumps({"columns": 100, "tasks": [
            {"id": "a", "name": "w", "model": "claude-fable-5-1", "tokenCount": 5}]})
        out = ss.render(payload)
        self.assertIn("fable-5", _plain(out))
        json.loads(out.strip())  # each line is valid JSON

    def test_unresolved_tasks_produce_no_line(self):
        payload = {"columns": 100, "tasks": [
            {"id": "a", "name": "w", "tokenCount": 5}]}  # no model
        self.assertEqual(ss.render(payload).strip(), "")

    def test_failsafe_on_bad_input_returns_empty(self):
        for bad in (None, "", "not json", "{", 42, [], {"tasks": "nope"},
                    {"no": "tasks"}):
            self.assertEqual(ss.render(bad), "")


class TestReconcileSettings(unittest.TestCase):
    CMD = 'bash "/home/u/.claude/airuleset-subagent-statusline.sh"'

    def test_sets_the_key(self):
        out = ss.reconcile_settings({}, self.CMD)
        self.assertEqual(out["subagentStatusLine"],
                         {"type": "command", "command": self.CMD})

    def test_preserves_other_keys(self):
        src = {"statusLine": {"type": "command", "command": "x"}, "env": {"A": "1"}}
        out = ss.reconcile_settings(src, self.CMD)
        self.assertEqual(out["statusLine"], {"type": "command", "command": "x"})
        self.assertEqual(out["env"], {"A": "1"})

    def test_idempotent(self):
        once = ss.reconcile_settings({}, self.CMD)
        twice = ss.reconcile_settings(once, self.CMD)
        self.assertEqual(once, twice)

    def test_does_not_mutate_input(self):
        src = {"env": {"A": "1"}}
        ss.reconcile_settings(src, self.CMD)
        self.assertNotIn("subagentStatusLine", src)


class TestSetup(unittest.TestCase):
    """The install step: writes the managed shim (executable) + reconciles
    the setting into settings.json, fail-safe and idempotent."""

    def _dirs(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="ss538-")
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        claude = Path(d) / ".claude"
        claude.mkdir()
        return d, str(claude), str(claude / "settings.json")

    def test_writes_executable_shim_with_repo_dir_substituted(self):
        repo, claude, settings = self._dirs()
        ok = ss.setup(repo, claude, settings)
        self.assertTrue(ok)
        shim = Path(ss.shim_dest(claude))
        self.assertTrue(shim.is_file())
        self.assertTrue(shim.stat().st_mode & 0o111)      # executable
        body = shim.read_text()
        self.assertIn(str(repo), body)
        self.assertNotIn("{{REPO_DIR}}", body)

    def test_wires_setting_and_preserves_existing(self):
        repo, claude, settings = self._dirs()
        Path(settings).write_text(json.dumps(
            {"statusLine": {"type": "command", "command": "keep"}}))
        ss.setup(repo, claude, settings)
        d = json.loads(Path(settings).read_text())
        self.assertEqual(d["subagentStatusLine"]["command"], ss.command_for(claude))
        self.assertEqual(d["statusLine"], {"type": "command", "command": "keep"})

    def test_idempotent_second_run_reports_already_correct(self):
        repo, claude, settings = self._dirs()
        ss.setup(repo, claude, settings)
        before = Path(settings).read_text()
        ss.setup(repo, claude, settings)
        self.assertEqual(before, Path(settings).read_text())

    def test_invalid_settings_json_is_non_fatal(self):
        repo, claude, settings = self._dirs()
        Path(settings).write_text("{ not json")
        # must not raise; returns False (couldn't reconcile), shim still written
        self.assertFalse(ss.setup(repo, claude, settings))
        self.assertTrue(Path(ss.shim_dest(claude)).is_file())


if __name__ == "__main__":
    unittest.main()
