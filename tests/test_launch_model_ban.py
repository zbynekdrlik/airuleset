"""#495: the RUNTIME LAUNCH ARG must never be a banned Opus 5 model.

The 2026-08-15 incident: subdev sessions montalu5 + david were found running
`claude --model claude-opus-5[1m]`. Live forensics (ps argv + start times vs the
#440 ban commit `cd19ca6`, 2026-08-13 14:42): both were STALE long-running
processes launched by an OLDER launcher, before the fable-5 launcher reached
their accounts (montalu5 started 2026-08-12, david 2026-08-13 15:06). Once
`exec claude --model claude-opus-5[1m]` runs, that process keeps the arg in its
argv forever; a rewritten on-disk launcher only affects the NEXT launch, and
`-c` alone continues the prior transcript's model (only an explicit --model
forces it). The on-disk launcher + MANAGED_MODEL on all accounts now bake
claude-fable-5-1[1m]; montalu6 (started 2026-08-15) runs fable-5 correctly, so the
CURRENT code does not launch opus-5 -- the two offenders were pre-ban ghosts
self-healing at their next natural relaunch.

The CODE gap this file locks -- the RUNTIME LAUNCH ARG gap the ticket calls for
teeth on: nothing SEMANTICALLY forbade the actual `--model` launch arg (and the
settings.json `model`) from BECOMING a banned Opus 5 value in a future edit. The
#440 repo-wide grep-gate (tests/test_model_tiering.py) catches the literal
`claude-opus-5` substring and the DISPATCH syntax `model: "opus"` -- but a future
`MANAGED_MODEL = "opus"` (the bare alias that resolves to Opus 5 on the Anthropic
API) slips past BOTH (no `model:` colon, no `claude-opus-5` literal), and the
launcher would then bake `--model 'opus'`. This file locks the runtime launch
arg itself -- MANAGED_MODEL, the rendered launcher's `--model` value, and
apply_managed_settings_defaults()'s `model` -- against the BAN (not the specific
fable value, so it survives a legitimate managed-model bump). Opus 5 is BANNED
everywhere (modules/core/model-awareness.md).

Teeth: cmd_push's `unittest discover` gate (and CI) fails the merge on any banned
managed model before it can ever be deployed to a box. Proven RED->GREEN by
mutation (temporarily set MANAGED_MODEL to claude-opus-5[1m] / the bare `opus`
alias -> these tests fail; restore -> pass).
"""

import re
import unittest
from unittest import TestCase

import airuleset


# #871: the ban predicate is now the SHARED airuleset.is_banned_model (Opus 5
# AND Fable 5.1), reused by cli_config.apply_managed_settings_defaults's
# self-heal — no duplicated predicate (the #495 one-source lesson). This
# module's tests exercise that single function directly.
_is_banned_opus5 = airuleset.is_banned_model


class TestBannedPredicateItself(TestCase):
    """The predicate must catch every Opus-5 AND Fable-5.1 form AND clear
    every allowed model -- otherwise the launch-arg guards below are vacuous."""

    def test_catches_every_opus5_form(self):
        for banned in ("claude-opus-5[1m]", "claude-opus-5",
                       "claude-opus-5-20260501", "opus", "opus[1m]",
                       "OPUS", "'opus'", " opus ", "opus [1m]", "opusplan",
                       "opus-5", "opus-5.1",
                       # provider-prefixed / dated forms (Bedrock / Vertex):
                       "us.anthropic.claude-opus-5-20260514-v1:0",
                       "anthropic.claude-opus-5-v1:0"):
            self.assertTrue(airuleset.is_banned_model(banned),
                            "%r should be BANNED" % banned)

    def test_catches_every_fable_5_1_form(self):
        # #871 — Fable 5.1 joins the ban: any fable-5-1 id AND the bare
        # `fable` alias (the Agent `model` param floats it to LATEST = 5.1).
        for banned in ("claude-fable-5-1", "claude-fable-5-1[1m]",
                       "CLAUDE-FABLE-5-1", "'claude-fable-5-1'",
                       " claude-fable-5-1 ", "claude-fable-5-1-20260901",
                       "fable", "fable[1m]", "FABLE", "'fable'", " fable "):
            self.assertTrue(airuleset.is_banned_model(banned),
                            "%r should be BANNED (Fable 5.1 / bare alias)" % banned)

    def test_clears_every_allowed_model(self):
        # #871: exact-id allowlist semantics -- only the CURRENT MODEL_TIERS
        # ids (+ the Fable main [1m] form) clear the predicate. claude-opus-4-8
        # is the SUPERSEDED predecessor (renamed to claude-opus-4-6) and is
        # correctly BANNED now -- see test_superseded_opus_4_8_is_now_banned.
        for ok in ("claude-fable-5-1[1m]", "claude-fable-5-1",
                   "claude-opus-4-6[1m]", "claude-opus-4-6",
                   "claude-sonnet-5", "claude-haiku-4-5"):
            self.assertFalse(airuleset.is_banned_model(ok),
                             "%r must be ALLOWED" % ok)

    def test_superseded_opus_4_8_is_now_banned(self):
        # #871: the exact-id allowlist means a PREVIOUSLY-allowed id becomes
        # banned the moment MODEL_TIERS moves on -- claude-opus-4-8 (the
        # pre-#871 opus tier) is superseded by claude-opus-4-6 and must be
        # BANNED now, not silently still-allowed.
        for superseded in ("claude-opus-4-8", "claude-opus-4-8[1m]",
                            "claude-opus-4-7"):
            self.assertTrue(airuleset.is_banned_model(superseded),
                            "%r (superseded id) should be BANNED" % superseded)


class TestManagedModelNeverBanned(TestCase):
    """MANAGED_MODEL is the SINGLE source the launcher `--model` arg and the
    settings.json `model` both flow from (#495 root cause)."""

    def test_managed_model_not_a_banned_opus5_value(self):
        self.assertFalse(
            _is_banned_opus5(airuleset.MANAGED_MODEL),
            "MANAGED_MODEL=%r is a BANNED Opus 5 value (model-awareness.md)"
            % airuleset.MANAGED_MODEL)


class TestRuntimeLaunchArgNeverBanned(TestCase):
    """The actual `--model '<x>'` value the managed launcher passes to
    `claude` -- the runtime launch arg the #495 incident was about."""

    def _model_args(self):
        script = airuleset.render_claude_launch_script()
        vals = re.findall(r"--model '([^']*)'", script)
        self.assertTrue(vals, "no --model arg found in the rendered launcher")
        return vals

    def test_every_launcher_model_arg_is_ban_safe(self):
        for v in self._model_args():
            self.assertFalse(
                _is_banned_opus5(v),
                "launcher --model '%s' is a BANNED Opus 5 value" % v)

    def test_no_opus5_id_substring_anywhere_in_launcher(self):
        # Defense in depth: not even a stray/commented opus-5 id (case-insensitive,
        # so `Claude-Opus-5` is caught too). Non-emptiness guarded so a future
        # render returning "" can never pass this vacuously (adversarial-review F3).
        script = airuleset.render_claude_launch_script()
        self.assertTrue(script.strip(), "rendered launcher is empty")
        self.assertNotIn("opus-5", script.lower())


class TestSettingsDefaultModelNeverBanned(TestCase):
    """apply_managed_settings_defaults writes model = MANAGED_MODEL into
    settings.json -- the second runtime surface the ban must cover."""

    def test_settings_default_model_not_banned(self):
        model = airuleset.apply_managed_settings_defaults({}).get("model")
        # Non-emptiness guarded so a future edit dropping the `model` default
        # can't pass vacuously (_is_banned_opus5(None) is False) — mirrors the
        # launcher test's assertTrue(vals) (adversarial-review F2).
        self.assertIsNotNone(
            model, "apply_managed_settings_defaults dropped the model default")
        self.assertFalse(
            _is_banned_opus5(model),
            "settings.json model=%r is a BANNED Opus 5 value" % model)


class TestSettingsSelfHealsBannedModel(TestCase):
    """#871 -- apply_managed_settings_defaults' UNCONDITIONAL `model =
    MANAGED_MODEL` overwrite is the self-heal: a settings.json carrying a
    banned id (an owner `/model → Fable 5.1` Enter, a persisted
    `model_changed` float, a stale Opus 5) is rewritten to the allowed
    managed default on the next install/push. This LOCKS that heal (the
    behavior is unconditional, so this documents/guards it rather than
    introducing it)."""

    def _healed(self, incoming):
        import cli_config
        return cli_config.apply_managed_settings_defaults(
            {"model": incoming}).get("model")

    def test_heals_banned_fable_5_1(self):
        for bad in ("claude-fable-5-1[1m]", "claude-fable-5-1", "fable"):
            healed = self._healed(bad)
            self.assertEqual(healed, airuleset.MANAGED_MODEL)
            self.assertFalse(airuleset.is_banned_model(healed))

    def test_heals_banned_opus_5(self):
        for bad in ("claude-opus-5[1m]", "opus"):
            healed = self._healed(bad)
            self.assertEqual(healed, airuleset.MANAGED_MODEL)
            self.assertFalse(airuleset.is_banned_model(healed))


if __name__ == "__main__":
    unittest.main()
