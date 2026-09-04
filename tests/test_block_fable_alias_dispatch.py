"""#871 — hooks/block-fable-alias-dispatch.sh (PreToolUse: Agent + Workflow).

Blocks the bare `fable` model alias (it floats to the BANNED Fable 5.1) and any
`claude-fable-5-1` id, on both the Agent `model` param and Workflow `script`
text — pointing the caller at the pinned `fable-advisor` agent type / a Workflow
`opts.model: 'claude-fable-5'`. Fable 5.0 (`claude-fable-5`) and every other
model MUST pass.

Stdin contract: JSON payload on STDIN (.tool_input.model / .tool_input.script);
exit 2 = block, reason on STDERR (never STDOUT — invisible to the model).
"""
import json
import subprocess
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-fable-alias-dispatch.sh"


def run_hook(payload):
    return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True)


class TestAgentModelParam(TestCase):
    def test_blocks_bare_fable_alias(self):
        r = run_hook({"tool_input": {"subagent_type": "general-purpose",
                                     "model": "fable"}})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fable-advisor", r.stderr)
        self.assertIn("871", r.stderr)

    def test_blocks_fable_alias_case_and_quotes(self):
        for m in ("FABLE", "'fable'", " fable "):
            r = run_hook({"tool_input": {"model": m}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (m, r.stderr))

    def test_blocks_fable_5_1_id(self):
        for m in ("claude-fable-5-1", "claude-fable-5-1[1m]",
                  "CLAUDE-FABLE-5-1"):
            r = run_hook({"tool_input": {"model": m}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (m, r.stderr))

    def test_allows_no_model_param(self):
        # the fable-advisor pinned-agent dispatch shape (no model param).
        r = run_hook({"tool_input": {"subagent_type": "fable-advisor"}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_sonnet_haiku_opus(self):
        for m in ("sonnet", "haiku", "opus"):
            r = run_hook({"tool_input": {"model": m}})
            self.assertEqual(r.returncode, 0, "%r should pass: %s" % (m, r.stderr))

    def test_allows_pinned_fable_5_0_id(self):
        # never a real Agent param value, but must never be flagged as banned.
        r = run_hook({"tool_input": {"model": "claude-fable-5"}})
        self.assertEqual(r.returncode, 0, r.stderr)


class TestWorkflowScript(TestCase):
    def test_blocks_bare_fable_alias_in_script(self):
        for s in ("agent(x, {model: \"fable\"})",
                  "agent(x, {model: 'fable'})",
                  "opts.model: fable"):
            r = run_hook({"tool_input": {"script": s}})
            self.assertEqual(r.returncode, 2, "%r should block: %s" % (s, r.stderr))

    def test_blocks_fable_5_1_id_in_script(self):
        r = run_hook({"tool_input": {"script": "opts.model: 'claude-fable-5-1'"}})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_pinned_fable_5_0_in_script(self):
        r = run_hook({"tool_input": {"script": "agent(x, {model: 'claude-fable-5'})"}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_sonnet_opus_4_8_stage(self):
        for s in ("agent(x, {model: 'sonnet'})",
                  "agent(x, {model: 'claude-opus-4-8'})"):
            r = run_hook({"tool_input": {"script": s}})
            self.assertEqual(r.returncode, 0, "%r should pass: %s" % (s, r.stderr))


class TestFailOpenAndReasonOnStderr(TestCase):
    def test_empty_input_passes(self):
        r = subprocess.run(["bash", str(HOOK)], input="",
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_block_reason_on_stderr_not_stdout(self):
        r = run_hook({"tool_input": {"model": "fable"}})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("BLOCKED", r.stderr)


if __name__ == "__main__":
    main()
