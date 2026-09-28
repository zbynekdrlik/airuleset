"""#1173 — Sonnet 5.5 joins the fleet; subagent models are Claude Code's native
choice (no forced CLAUDE_CODE_SUBAGENT_MODEL).

Owner directive 2026-09-28 (verbatim): "a plus vysiel sonnet 5.5, chcem aby si
ho zakomponoval a nasadil, idealne keby jeho vyuzivanie bolo co najviacej
prirodzene tak ako to automaticky claude odporuca navrhuje, a nase zasahy pri
vybere modelu aby sa znizovali. Pravdaze nechem znizenie kvality vysledkov."

Approach 1 of the main-authored design (issuecomment-5877698121):
  1. MODEL_TIERS["sonnet"] = "claude-sonnet-5-5"; claude-sonnet-5 stays allowed
     under its own key.
  2. airuleset no longer renders CLAUDE_CODE_SUBAGENT_MODEL into settings.json
     nor exports it from a launcher; the install self-heal REMOVES a value
     airuleset itself wrote (an old managed default, or the #1062 L2 gateway
     write) and KEEPS + reports a hand-set foreign value.
  3. Quality floor: with the env unset, Claude Code resolves a subagent with no
     per-dispatch model and no `model:` frontmatter to the MAIN conversation's
     model (docs "Choose a model" order + the 2.1.283 resolver, evidence on the
     ticket), so the airuleset agents keep NO `model:` pin and inherit Opus 5.5.
  4. MANAGED_MODEL + BANNED_MODELS unchanged.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import unittest
from unittest import TestCase

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airuleset  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = "CLAUDE_CODE_SUBAGENT_MODEL"


def _apply(settings):
    """apply_managed_settings_defaults + its captured stderr."""
    import cli_config
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        out = cli_config.apply_managed_settings_defaults(settings)
    return out, err.getvalue()


class TestSonnet55Allowlisted(TestCase):
    def test_sonnet_tier_is_5_5(self):
        self.assertEqual(airuleset.MODEL_TIERS["sonnet"], "claude-sonnet-5-5")

    def test_both_sonnet_ids_allowed(self):
        self.assertTrue(airuleset.is_allowed_model("claude-sonnet-5-5"))
        self.assertTrue(airuleset.is_allowed_model("claude-sonnet-5-5[1m]"))
        # Older pinned dispatches keep working.
        self.assertTrue(airuleset.is_allowed_model("claude-sonnet-5"))

    def test_sonnet_5_5_not_banned_anywhere(self):
        self.assertFalse(airuleset.is_banned_model("claude-sonnet-5-5"))
        self.assertFalse(airuleset.is_banned_model_for_audit("claude-sonnet-5-5"))
        self.assertNotIn("claude-sonnet-5-5", airuleset.BANNED_MODELS)

    def test_dispatch_hook_passes_sonnet_5_5(self):
        hook = os.path.join(REPO, "hooks", "block-banned-model.sh")
        r = subprocess.run(["bash", hook],
                           input=json.dumps({"tool_name": "Agent",
                                             "tool_input": {"model": "claude-sonnet-5-5"}}),
                           capture_output=True, text=True,
                           env=hermetic_hook_env(self))
        self.assertEqual(r.returncode, 0, r.stderr)


class TestMainPinAndBanUnchanged(TestCase):
    """Step 4 lock: this ticket must not move the main pin or the ban list."""

    def test_managed_model_unchanged(self):
        self.assertEqual(airuleset.MANAGED_MODEL, "claude-opus-5-5[1m]")

    def test_ban_list_unchanged(self):
        self.assertEqual(airuleset.BANNED_MODELS,
                         frozenset({"claude-opus-5", "opus", "opusplan"}))


class TestNoForcedSubagentEnv(TestCase):
    def test_rendered_env_has_no_subagent_model(self):
        out, _ = _apply({})
        self.assertNotIn(KEY, out["env"])
        self.assertNotIn(KEY + "_FORCE", out["env"])

    def test_old_managed_opus_5_5_value_removed(self):
        out, err = _apply({"env": {KEY: "claude-opus-5-5"}})
        self.assertNotIn(KEY, out["env"])
        self.assertIn(KEY, err, "the removal is reported LOUDLY")

    def test_old_managed_opus_4_8_value_removed(self):
        # The #991 era wrote MODEL_TIERS["opus"] = claude-opus-4-8.
        out, _ = _apply({"env": {KEY: "claude-opus-4-8"}})
        self.assertNotIn(KEY, out["env"])

    def test_l2_gateway_write_removed(self):
        # #1062 L2 wrote marker["sub"] (a gateway alias) next to the gateway
        # keys into the SHARED settings.json, together with airuleset's own
        # managed apiKeyHelper path. That alias is airuleset's own write; left
        # behind it would point the MAIN window's subagents at a model the
        # Anthropic API does not serve.
        import cli_config
        out, _ = _apply({"apiKeyHelper": cli_config._L2_MANAGED_APIKEY_HELPER,
                         "env": {"ANTHROPIC_BASE_URL": "http://gw:4000",
                                 "ANTHROPIC_MODEL": "impl-main",
                                 KEY: "impl-sub"}})
        self.assertNotIn(KEY, out["env"])
        self.assertNotIn("ANTHROPIC_BASE_URL", out["env"])

    def test_hand_set_value_next_to_generic_env_keys_kept(self):
        # API_TIMEOUT_MS / ANTHROPIC_BASE_URL alone are keys an owner may set
        # for their own reasons; without airuleset's managed L2 apiKeyHelper
        # they prove nothing, so the owner's subagent model must survive.
        out, err = _apply({"env": {"API_TIMEOUT_MS": "600000",
                                   "ANTHROPIC_BASE_URL": "https://proxy.example",
                                   KEY: "claude-sonnet-5-5"}})
        self.assertEqual(out["env"][KEY], "claude-sonnet-5-5")
        self.assertIn("kept hand-set", err)

    def test_non_string_value_kept_without_crash(self):
        out, err = _apply({"env": {KEY: ["claude-opus-5-5"]}})
        self.assertEqual(out["env"][KEY], ["claude-opus-5-5"])
        self.assertIn("kept hand-set", err)

    def test_foreign_hand_set_value_kept_and_reported(self):
        out, err = _apply({"env": {KEY: "claude-sonnet-5-5"}})
        self.assertEqual(out["env"][KEY], "claude-sonnet-5-5")
        self.assertIn(KEY, err)
        self.assertIn("claude-sonnet-5-5", err)

    def test_absent_key_prints_nothing_about_it(self):
        _, err = _apply({})
        self.assertNotIn(KEY, err)

    def test_idempotent(self):
        first, _ = _apply({"env": {KEY: "claude-opus-5-5", "OTHER": "x"}})
        second, _ = _apply(first)
        self.assertEqual(first, second)
        self.assertEqual(second["env"]["OTHER"], "x")


class TestLaunchScriptsDoNotExport(TestCase):
    def test_main_launcher_no_export(self):
        import cli_claude_scripts as cs
        self.assertNotIn(KEY, cs.render_claude_launch_script())

    def test_impl_launcher_no_export(self):
        import cli_claude_scripts as cs
        rendered = cs.render_claude_impl_launch_script()
        self.assertNotRegex(rendered, r"(?m)^\s*export\s+" + KEY + r"\b")


class TestOwnAgentsInheritMain(TestCase):
    """Step 3 lock: the native default for an agent with no `model:` is the main
    conversation's model, so airuleset's own quality-critical agents carry NO
    model pin (a pin to anything weaker would silently drop a worker's
    quality). `model: inherit` would be equivalent and is also accepted."""

    def test_no_weaker_pin(self):
        for name in airuleset.AGENT_NAMES:
            path = os.path.join(REPO, "agents", name + ".md")
            text = open(path, encoding="utf-8").read()
            m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(m, "%s has frontmatter" % name)
            pin = re.search(r"(?m)^\s*model:\s*(\S+)", m.group(1))
            if pin:
                self.assertEqual(pin.group(1).strip("'\""), "inherit",
                                 "%s must inherit the main model" % name)


if __name__ == "__main__":
    unittest.main()
