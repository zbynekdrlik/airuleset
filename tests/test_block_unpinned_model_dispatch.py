"""#871 — hooks/block-unpinned-model-dispatch.sh (PreToolUse: Agent + Workflow).

The fleet lineup is an ALLOWLIST of EXACT ids (airuleset.MODEL_TIERS). A dispatch
NEVER carries a `model` alias param (an alias floats to the latest model — the
Fable 5.1 failure). The model choice is carried by a PINNED agent type / Workflow
`opts.model: '<exact id>'`.

  * Agent:    block ANY non-empty `model` param; allow a dispatch with none.
  * Workflow: block any `(opts.)model` value not on the exact-id allowlist;
              allow the exact ids.

Stdin contract: JSON on STDIN (.tool_input.model / .tool_input.script); exit 2 =
block, reason on STDERR (never STDOUT).
"""
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-unpinned-model-dispatch.sh"

sys.path.insert(0, str(ROOT))
import airuleset  # noqa: E402


def run_hook(payload):
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True)


class TestAgentModelParam(TestCase):
    def test_blocks_any_model_param(self):
        # every alias AND every exact id: the pinned agent type is the model,
        # so an Agent `model` param is never right.
        for m in ("fable", "opus", "sonnet", "haiku", "opusplan",
                  "claude-fable-5-1", "claude-opus-4-8",
                  "claude-fable-5", "claude-sonnet-5", "claude-opus-4-6"):
            r = run_hook({"tool_input": {"subagent_type": "general-purpose",
                                         "model": m}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (m, r.stderr))
            self.assertIn("871", r.stderr)

    def test_allows_dispatch_with_no_model_param(self):
        for st in ("fable-advisor", "sonnet-mechanical", "sonnet-implementer",
                   "autopilot-worker", "general-purpose", "Explore"):
            r = run_hook({"tool_input": {"subagent_type": st}})
            self.assertEqual(r.returncode, 0, "%r should pass: %s" % (st, r.stderr))

    def test_block_reason_on_stderr_not_stdout(self):
        r = run_hook({"tool_input": {"model": "fable"}})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("BLOCKED", r.stderr)
        self.assertIn("fable-advisor", r.stderr)


class TestWorkflowScript(TestCase):
    def test_blocks_bare_alias_in_script(self):
        for s in ("agent(x, {model: \"fable\"})",
                  "agent(x, {model: 'sonnet'})",
                  "opts.model: opus",
                  "agent(x, {model: 'haiku'})"):
            r = run_hook({"tool_input": {"script": s}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (s, r.stderr))

    def test_blocks_backtick_quoted_banned_value(self):
        # #871 adversarial review 🟡4: a template-literal value used only
        # `"`/`'` in the quote class, so a backtick-quoted value skipped
        # detection entirely (empty capture -> no violation found).
        r = run_hook({"tool_input": {"script": "agent(x, {model: `claude-opus-5`})"}})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_backtick_quoted_allowlisted_value(self):
        r = run_hook({"tool_input": {"script": "agent(x, {model: `claude-fable-5`})"}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_superseded_id_in_script(self):
        for s in ("opts.model: 'claude-fable-5-1'",
                  "opts.model: 'claude-opus-4-8'",
                  "opts.model: 'claude-opus-5'"):
            r = run_hook({"tool_input": {"script": s}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (s, r.stderr))

    def test_allows_exact_allowlisted_ids_in_script(self):
        for s in ("agent(x, {model: 'claude-fable-5'})",
                  "agent(x, {model: 'claude-sonnet-5'})",
                  "agent(x, {model: 'claude-opus-4-6'})",
                  "agent(x, {model: 'claude-haiku-4-5'})",
                  "agent(x, {model: 'claude-fable-5[1m]'})"):
            r = run_hook({"tool_input": {"script": s}})
            self.assertEqual(r.returncode, 0, "%r should pass: %s" % (s, r.stderr))

    def test_allows_script_with_no_model(self):
        r = run_hook({"tool_input": {"script": "agent(x, {effort: 'high'})"}})
        self.assertEqual(r.returncode, 0, r.stderr)


class TestWorkflowScriptPath(TestCase):
    """#871 adversarial review 🟡5: a Workflow invoked via `scriptPath` (the
    documented iterate pattern — persist the script, re-invoke by path) was
    never scanned at all — only `.tool_input.script` was read. The hook must
    read scriptPath's FILE content and scan it the same way, fail-open if
    the path is unreadable."""

    def _script_file(self, tmp_path_dir, content):
        import tempfile
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", dir=tmp_path_dir, delete=False)
        f.write(content)
        f.close()
        return f.name

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="airuleset-scriptpath-871-")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_blocks_banned_model_in_scriptpath_file(self):
        p = self._script_file(
            self._tmpdir, "agent(x, {model: 'claude-opus-5'})")
        r = run_hook({"tool_input": {"scriptPath": p}})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_allowlisted_model_in_scriptpath_file(self):
        p = self._script_file(
            self._tmpdir, "agent(x, {model: 'claude-fable-5'})")
        r = run_hook({"tool_input": {"scriptPath": p}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fails_open_on_unreadable_scriptpath(self):
        r = run_hook({"tool_input": {
            "scriptPath": "/tmp/airuleset-871-does-not-exist.js"}})
        self.assertEqual(r.returncode, 0, r.stderr)


class TestAllowlistMatchesModelTiers(TestCase):
    """The hook's embedded ALLOWLIST_RE must equal airuleset.MODEL_TIERS.values()
    — a MODEL_TIERS edit that forgets the hook fails here (the #495 one-source
    discipline)."""

    def test_hook_allowlist_equals_model_tiers(self):
        src = HOOK.read_text(encoding="utf-8")
        m = re.search(r"ALLOWLIST_RE='\^\(([^)]*)\)", src)
        self.assertIsNotNone(m, "ALLOWLIST_RE not found in the hook")
        hook_ids = set(m.group(1).split("|"))
        self.assertEqual(hook_ids, set(airuleset.MODEL_TIERS.values()))


class TestFailOpen(TestCase):
    def test_empty_input_passes(self):
        r = subprocess.run(["bash", str(HOOK)], input="",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
