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
