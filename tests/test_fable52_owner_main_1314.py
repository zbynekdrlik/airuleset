"""#1314 (owner 13.11.2026): the owner may run Fable 5.2 (`claude-fable-5-2`) as
a MAIN model in any project. The #1203 model gate blocked every tool call in
varos and watchdog Job 54 typed `/model claude-opus-5-5[1m]` back.

Fable 5.2 is an owner-SELECTABLE main, never a default: launch and restore
target stay MANAGED_MODEL. Only the exact id is carved out of the #1248 Fable
ban; every other Fable id and the bare `fable` alias stay banned.
"""
import json
import os
import subprocess
from unittest import TestCase

import airuleset
import model_fallback
import model_lineup
from _hook_state_cleanup import MODULE_HOOK_HOME  # noqa: E402  (#1046 hermetic HOME)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO, "hooks", "block-banned-model.sh")
FABLE52 = "claude-fable-5-2"
TRANSCRIPT = "/home/varos/.claude/projects/-home-varos-devel-varos-uctovnictvo/s.jsonl"


def _hook(model):
    r = subprocess.run(["bash", HOOK], capture_output=True, text=True,
                       input=json.dumps({"tool_name": "Agent", "tool_input": {"model": model}}),
                       env={**os.environ, "HOME": MODULE_HOOK_HOME})
    return r.returncode


class TestMainModels(TestCase):
    def test_every_project_may_run_fable52(self):
        for name in (None, "varos", "pz-server"):
            target, allowed = model_lineup.main_models(name)
            self.assertIn(FABLE52, allowed, name)
        self.assertIn(FABLE52, model_lineup.main_models_for_transcript(TRANSCRIPT)[1])

    def test_target_stays_managed(self):
        self.assertEqual(model_lineup.main_models(None)[0], model_lineup.MANAGED_MODEL)
        self.assertEqual(model_lineup.main_models("pz-server")[0], "claude-opus-4-6")

    def test_gate_verdict_allows_fable52_main(self):
        managed, allowed = model_lineup.main_models_for_transcript(TRANSCRIPT)
        for served in (FABLE52, FABLE52 + "[1m]"):
            st = {"model": served, "fallback": False}
            self.assertIsNone(model_fallback.verdict(st, managed, allowed=allowed), served)

    def test_gate_still_blocks_other_fable_and_fallback(self):
        managed, allowed = model_lineup.main_models_for_transcript(TRANSCRIPT)
        for served in ("claude-fable-5-1", "claude-opus-4-8"):
            v = model_fallback.verdict({"model": served, "fallback": False}, managed, allowed=allowed)
            self.assertIsNotNone(v, served)

    def test_not_a_fleet_dispatch_tier(self):
        self.assertNotIn(FABLE52, airuleset.MODEL_TIERS.values())


class TestBanCarveOut(TestCase):
    def test_audit_does_not_flag_fable52(self):
        for v in (FABLE52, FABLE52 + "[1m]", FABLE52 + "-20261101", "us.anthropic." + FABLE52):
            self.assertFalse(airuleset.is_banned_model_for_audit(v), v)

    def test_audit_still_flags_other_fable(self):
        for v in ("claude-fable-5-1", "claude-fable-5", "claude-fable-6", "claude-fable-5-21", "fable"):
            self.assertTrue(airuleset.is_banned_model_for_audit(v), v)

    def test_hook_allows_fable52_dispatch(self):
        for v in (FABLE52, FABLE52 + "[1m]", "CLAUDE-FABLE-5-2"):
            self.assertEqual(_hook(v), 0, v)

    def test_hook_still_blocks_other_fable(self):
        for v in ("fable", "claude-fable-5-1", "claude-fable-5", "claude-fable-6", "claude-fable-5-21"):
            self.assertEqual(_hook(v), 2, v)
