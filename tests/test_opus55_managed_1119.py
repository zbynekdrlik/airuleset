"""#1119 — Opus 5.5 (`claude-opus-5-5`) replaces Fable 5.1 as the managed MAIN
model AND Opus 4.8 as the fleet subagent default (owner directive 2026-09-23,
verbatim: "Najprv chcem aby sa zacal nahradzat fable 5.1 za opus 5.5 a to aj v
hlavnom agentovi main a aj ako nahrada opus 4.8").

Approach 1 (design comment #1119): add `"opus5": "claude-opus-5-5"` as the MAIN
tier at the single-source lineup, repoint MANAGED_MODEL + the subagent-default
self-heal reader to it, keep Fable 5.1 and Opus 4.8 as ALLOWED (non-default)
dispatch ids, and leave the Opus-5 ban intact. These are the RED-first locks the
design's Acceptance section names.
"""
import json
import os
import subprocess
import sys
import unittest
from unittest import TestCase

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airuleset  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestManagedMainIsOpus55(TestCase):
    def test_managed_model_is_opus_5_5_with_1m_suffix(self):
        # Opus 5.5 supports the 1M variant; the `[1m]` tag keeps the 1M window
        # exactly as the Fable id did (real `lastModelUsage` id shape).
        self.assertEqual(airuleset.MANAGED_MODEL, "claude-opus-5-5[1m]")

    def test_main_tier_id_present(self):
        self.assertIn("claude-opus-5-5", set(airuleset.MODEL_TIERS.values()))

    def test_fable_and_opus48_stay_allowed_ids(self):
        # No longer the defaults, but still legitimate explicit-dispatch ids.
        vals = set(airuleset.MODEL_TIERS.values())
        self.assertIn("claude-fable-5-1", vals)
        self.assertIn("claude-opus-4-8", vals)
        self.assertTrue(airuleset.is_allowed_model("claude-fable-5-1"))
        self.assertTrue(airuleset.is_allowed_model("claude-opus-4-8"))


class TestSelfHeal(TestCase):
    def _settings(self, base=None):
        import cli_config
        return cli_config.apply_managed_settings_defaults(base or {})

    def test_model_healed_to_opus_5_5_1m(self):
        self.assertEqual(self._settings()["model"], "claude-opus-5-5[1m]")

    def test_subagent_default_is_opus_5_5(self):
        env = self._settings()["env"]
        self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-opus-5-5")

    def test_heals_a_contaminated_fable_and_48_input(self):
        # A box a prior session left on the OLD managed lineup self-heals to the
        # new one on the next install/push.
        out = self._settings({"model": "claude-fable-5-1[1m]",
                              "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "claude-opus-4-8"}})
        self.assertEqual(out["model"], "claude-opus-5-5[1m]")
        self.assertEqual(out["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-opus-5-5")


class TestLauncherModel(TestCase):
    def test_launcher_bakes_opus_5_5(self):
        import cli_claude_scripts
        rendered = cli_claude_scripts.render_claude_launch_script()
        self.assertIn("--model 'claude-opus-5-5[1m]'", rendered)
        self.assertNotIn("{{MANAGED_MODEL}}", rendered)


class TestBanLock(TestCase):
    """Opus 5.5 is NOT banned (Python predicate AND the dispatch hook); Opus 5
    and the bare `opus` alias stay banned."""

    HOOK = os.path.join(REPO, "hooks", "block-banned-model.sh")

    def _hook(self, model):
        r = subprocess.run(["bash", self.HOOK],
                           input=json.dumps({"tool_name": "Agent",
                                             "tool_input": {"model": model}}),
                           capture_output=True, text=True,
                           env=hermetic_hook_env(self))
        return r.returncode

    def test_python_allows_opus_5_5(self):
        self.assertFalse(airuleset.is_banned_model("claude-opus-5-5"))
        self.assertFalse(airuleset.is_banned_model("claude-opus-5-5[1m]"))

    def test_python_still_bans_opus_5_and_alias(self):
        self.assertTrue(airuleset.is_banned_model("claude-opus-5"))
        self.assertTrue(airuleset.is_banned_model("opus"))

    def test_audit_predicate_does_not_flag_opus_5_5(self):
        self.assertFalse(airuleset.is_banned_model_for_audit("claude-opus-5-5"))
        self.assertFalse(
            airuleset.is_banned_model_for_audit("claude-opus-5-5-20260901"))
        self.assertTrue(airuleset.is_banned_model_for_audit("claude-opus-5"))

    def test_hook_passes_opus_5_5(self):
        self.assertEqual(self._hook("claude-opus-5-5"), 0)
        self.assertEqual(self._hook("claude-opus-5-5[1m]"), 0)

    def test_hook_blocks_opus_5_and_alias(self):
        self.assertEqual(self._hook("claude-opus-5"), 2)
        self.assertEqual(self._hook("opus"), 2)


class TestDesignGateOpus55(TestCase):
    """The #1061 dispatch gate accepts a `<main-model> main` design stamp for the
    new managed main (claude-opus-5-5), while still accepting a Fable stamp
    through the transition and refusing a worker/other-model stamp."""

    def _payload(self, prompt):
        return json.dumps({"tool_name": "Agent", "cwd": "/repo",
                           "tool_input": {"subagent_type": "autopilot-worker",
                                          "prompt": prompt}})

    def _ev(self, bodies):
        from gates import designdispatch as dd
        # No fable_id injected -> production accepted set (managed main + Fable).
        return dd.evaluate(
            self._payload("Work issue #1119 in repo"),
            fetch=lambda s, n, c: bodies,
            resolve_slug=lambda cwd: "owner/repo",
            is_pr=lambda n, s, c: (False, None))[0]

    def test_accepts_opus_5_5_main(self):
        self.assertEqual(self._ev(["Design-by: main claude-opus-5-5"]), "allow")

    def test_accepts_opus_5_5_main_1m(self):
        self.assertEqual(self._ev(["Design-by: main claude-opus-5-5[1m]"]),
                         "allow")

    def test_still_accepts_fable_main_transition(self):
        self.assertEqual(self._ev(["Design-by: main claude-fable-5-1"]), "allow")

    def test_blocks_worker_stamp(self):
        self.assertEqual(self._ev(["Design-by: worker claude-opus-5-5"]),
                         "block")


class TestBurnTier(TestCase):
    def test_opus_5_5_maps_to_opus_family_price_key(self):
        import burn
        self.assertEqual(burn.tier("claude-opus-5-5[1m]"), "opus")
        self.assertEqual(burn.tier("claude-opus-5-5"), "opus")


if __name__ == "__main__":
    unittest.main()
