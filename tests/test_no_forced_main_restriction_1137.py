"""#1137 (owner 23.9.2026): rules must not FORCE how work is split between the
main session and subagents. The main-agent write guard and the always-on
"keep main thin / delegate heavy reading" module are removed, not reworded;
subagents stay fully allowed and no counter-rule replaces them."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestNoForcedMainRestriction1137(unittest.TestCase):
    def test_settings_wire_no_main_write_guard(self):
        text = (ROOT / "settings" / "hooks.json").read_text()
        json.loads(text)
        for hook in ("block-main-implementation.sh",
                     "post-consume-main-exec-marker.sh"):
            self.assertNotIn(hook, text)
            self.assertFalse((ROOT / "hooks" / hook).exists(), hook)

    def test_universal_profile_imports_no_thin_main_module(self):
        prof = (ROOT / "profiles" / "universal.profile").read_text()
        self.assertNotIn("main-context-hygiene", prof)
        self.assertFalse(
            (ROOT / "modules" / "core" / "main-context-hygiene.md").exists())

    def test_no_module_points_at_the_removed_doctrine(self):
        for md in (ROOT / "modules").rglob("*.md"):
            text = md.read_text()
            self.assertNotIn("main-context-hygiene", text, md)
            self.assertNotIn("block-main-implementation", text, md)


if __name__ == "__main__":
    unittest.main()
