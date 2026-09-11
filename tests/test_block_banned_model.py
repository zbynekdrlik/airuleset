"""#991 lock 4 — hooks/block-banned-model.sh (replaces block-unpinned-model-dispatch.sh).

Owner directive 2026-09-11 (verbatim): "nechavat stale viac harnessu na claude
cli ktore je k tomu optimalizovane" — a per-dispatch `model` param is now a
LEGITIMATE native choice (the working model picks the subagent model). airuleset
bans exactly ONE thing on the dispatch surface: Opus 5 (`claude-opus-5` + the
bare `opus`/`opusplan` alias that resolves onto it). Everything else — the bare
`sonnet`/`haiku` alias, `claude-opus-4-8`, a missing param — passes.

Ban-list source of truth: airuleset.BANNED_MODELS (a lock below asserts the
hook's ban pattern covers every id in it, so a BANNED_MODELS edit that forgets
the hook fails CI — the #495 one-source lesson).
"""
import json
import os
import subprocess
import unittest
from unittest import TestCase

import airuleset

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "block-banned-model.sh")


def _run(payload):
    r = subprocess.run(["bash", HOOK], input=json.dumps(payload),
                       capture_output=True, text=True)
    return r.returncode, r.stderr


class TestBlocksBanned(TestCase):
    def test_blocks_agent_opus_alias(self):
        rc, err = _run({"tool_name": "Agent", "tool_input": {"model": "opus"}})
        self.assertEqual(rc, 2, err)

    def test_blocks_agent_opus_alias_1m(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "opus[1m]"}})
        self.assertEqual(rc, 2, err)

    def test_blocks_agent_opus5_exact(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "claude-opus-5"}})
        self.assertEqual(rc, 2, err)

    def test_blocks_agent_opus5_dated(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "claude-opus-5-20260514"}})
        self.assertEqual(rc, 2, err)

    def test_blocks_workflow_opus5(self):
        rc, err = _run({"tool_name": "Workflow",
                        "tool_input": {"script": "opts.model: 'claude-opus-5'"}})
        self.assertEqual(rc, 2, err)

    def test_blocks_workflow_opus_alias(self):
        rc, err = _run({"tool_name": "Workflow",
                        "tool_input": {"script": "  model: opus\n"}})
        self.assertEqual(rc, 2, err)


class TestPassesAllowed(TestCase):
    def test_passes_agent_sonnet_alias(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "sonnet"}})
        self.assertEqual(rc, 0, err)

    def test_passes_agent_haiku_alias(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "haiku"}})
        self.assertEqual(rc, 0, err)

    def test_passes_agent_opus_4_8(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"model": "claude-opus-4-8"}})
        self.assertEqual(rc, 0, err)

    def test_passes_agent_missing_model(self):
        rc, err = _run({"tool_name": "Agent",
                        "tool_input": {"subagent_type": "Explore"}})
        self.assertEqual(rc, 0, err)

    def test_passes_workflow_sonnet(self):
        rc, err = _run({"tool_name": "Workflow",
                        "tool_input": {"script": "opts.model: 'claude-sonnet-5'"}})
        self.assertEqual(rc, 0, err)


class TestHookBanlistMatchesSource(TestCase):
    """The hook's ban pattern MUST cover every id in airuleset.BANNED_MODELS —
    one source of truth (#495 lesson)."""

    def test_every_banned_id_named_in_hook(self):
        text = open(HOOK).read()
        for banned in airuleset.BANNED_MODELS:
            self.assertIn(banned, text,
                          "block-banned-model.sh does not name BANNED_MODELS "
                          "entry %r" % banned)


class TestHookShellClean(TestCase):
    def test_hook_parses(self):
        r = subprocess.run(["bash", "-n", HOOK], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_hook_under_3kb(self):
        self.assertLessEqual(os.path.getsize(HOOK), 3072,
                             "block-banned-model.sh must be <= 3 KB")


if __name__ == "__main__":
    unittest.main()
