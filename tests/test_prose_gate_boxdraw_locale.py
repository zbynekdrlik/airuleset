"""The prose gate's box-drawing detector must read characters, not bytes.

A project system account (dasio, 9.11.2026) runs its sessions in the C locale.
There `grep -E` reads a multibyte bracket class byte by byte, and every byte of
the em dash (E2 80 94) sits inside the box-drawing class. The /autopilot /goal
template has many em dashes and the word "footer", so the hook hard-blocked the
message the skill requires the session to print.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from _hook_state_cleanup import MODULE_HOOK_HOME, sweep_session_files  # noqa: E402

HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"
MOCKUP_REASON = "box-drawing"


def _run(text, locale):
    sid = f"test-boxdraw-locale-{uuid.uuid4().hex[:10]}"
    env = {**os.environ, "HOME": MODULE_HOOK_HOME, "LC_ALL": locale, "LANG": locale}
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                       text=True, encoding="utf-8", env=env)
    sweep_session_files(sid)
    return p.stdout + p.stderr


DASHES = ("The loop works the backlog — one unit at a time — and lets the footer U "
          "carry the question — never a buried one.\n\n✅ DONE: goal printed")
MOCKUP = ("Where should the version label go? Here's the header layout:\n"
          "┌─────────────┐\n│ header  logo │\n└─────────────┘")


class BoxDrawLocale(TestCase):
    def test_em_dashes_are_not_box_drawing_in_the_c_locale(self):
        self.assertNotIn(MOCKUP_REASON, _run(DASHES, "C"))

    def test_em_dashes_are_not_box_drawing_in_utf8(self):
        self.assertNotIn(MOCKUP_REASON, _run(DASHES, "C.UTF-8"))

    def test_a_real_mockup_is_still_blocked_in_the_c_locale(self):
        self.assertIn(MOCKUP_REASON, _run(MOCKUP, "C"))


if __name__ == "__main__":
    main()
