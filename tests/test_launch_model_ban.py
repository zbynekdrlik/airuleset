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
claude-fable-5[1m]; montalu6 (started 2026-08-15) runs fable-5 correctly, so the
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


def _is_banned_opus5(value):
    """True iff `value` is a BANNED Opus 5 model: any `opus-5` model id (the
    bare `claude-opus-5*` ids AND provider-prefixed / dated variants like
    `us.anthropic.claude-opus-5-20260514-v1:0`), OR the bare `opus`/`opusplan`
    aliases (both route to Opus 5 on the Anthropic API). The `opus-5` substring
    check is false-positive-safe: no ALLOWED model contains it -- `claude-fable-
    5[1m]` (managed default), `claude-opus-4-8[1m]` (gate-CLOSED fallback tier,
    which shares only the `opus-4` fragment), `claude-sonnet-4-5`, etc. The bare
    alias is checked by exact set membership, never substring, so `opus` inside
    `claude-opus-4-8` is NOT flagged (adversarial-review F1, #495)."""
    v = (value or "").strip().strip("'\"").strip().lower()
    v = re.sub(r"\s*\[\d+m\]$", "", v).strip()   # drop a trailing [Nm] context tag
    if v in ("opus", "opusplan"):                # bare Opus-5-routing aliases
        return True
    if "opus-5" in v:                            # any opus-5 id (incl. prefixed/dated)
        return True
    return False


class TestBannedPredicateItself(TestCase):
    """The predicate must catch every Opus-5 form AND clear every allowed
    model -- otherwise the launch-arg guards below are vacuous."""

    def test_catches_every_opus5_form(self):
        for banned in ("claude-opus-5[1m]", "claude-opus-5",
                       "claude-opus-5-20260501", "opus", "opus[1m]",
                       "OPUS", "'opus'", " opus ", "opus [1m]", "opusplan",
                       "opus-5", "opus-5.1",
                       # provider-prefixed / dated forms (Bedrock / Vertex):
                       "us.anthropic.claude-opus-5-20260514-v1:0",
                       "anthropic.claude-opus-5-v1:0"):
            self.assertTrue(_is_banned_opus5(banned),
                            "%r should be BANNED" % banned)

    def test_clears_every_allowed_model(self):
        for ok in ("claude-fable-5[1m]", "claude-fable-5",
                   "claude-opus-4-8[1m]", "claude-opus-4-8",
                   "claude-sonnet-4-5", "claude-haiku-4-5"):
            self.assertFalse(_is_banned_opus5(ok),
                             "%r must be ALLOWED" % ok)


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


if __name__ == "__main__":
    unittest.main()
