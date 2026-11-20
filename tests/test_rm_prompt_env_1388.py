"""#1388: Claude Code 2.1.327 asks for explicit approval of every rm whose
target is built from $(...) or a variable (also inside bash -c), even under
--dangerously-skip-permissions, and no permission rule can allow it. Background
lanes clean temp dirs that way, so the owner had to press Enter every few
minutes. The managed settings env turns both prompts off (verified live: the
same rm is blocked without the vars and runs with them)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_config  # noqa: E402

KEYS = ("CLAUDE_CODE_DISABLE_SUBSTITUTION_RM_PROMPT",
        "CLAUDE_CODE_DISABLE_INLINE_SHELL_RM_PROMPT")


class RmPromptEnv1388(unittest.TestCase):
    def test_managed_env_disables_both_rm_prompts(self):
        out = cli_config.apply_managed_settings_defaults({"env": {"KEEP": "1"}})
        for key in KEYS:
            self.assertEqual(out["env"].get(key), "1", key)
        self.assertEqual(out["env"]["KEEP"], "1")

    def test_an_empty_settings_file_gets_them_too(self):
        out = cli_config.apply_managed_settings_defaults({})
        for key in KEYS:
            self.assertEqual(out["env"].get(key), "1", key)


if __name__ == "__main__":
    unittest.main()
