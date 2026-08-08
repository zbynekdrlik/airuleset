"""Regression lock for `stop-check-prose-violations.sh`'s ASCII-art /
box-drawing layout-mockup ban (lines ~432-454), relocated out of
`test_ask_before_assuming_dropped_rows.py` — #316 restored the
"visual companion" row that test used to live under back into
`ask-before-assuming.md`'s pre-answered table (its Slovak coverage did
not survive audit), so it is no longer a "dropped row" and the test no
longer belongs in that file. The ASCII-art hard-block itself is a
SEPARATE, still-active mechanism (a structural box-drawing-character
detector, independent of whether the visual-companion QUESTION row is
present in the table) and stays regression-locked here.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from _hook_state_cleanup import sweep_session_files  # noqa: E402

HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"


def _run_stop_prose(text):
    sid = f"test-ascii-art-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True)
    sweep_session_files(sid)
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestAsciiArtLayoutMockupIsBlocked(TestCase):
    def test_ascii_art_layout_mockup_blocked(self):
        msg = (
            "Where should the version label go? Here's the header layout:\n"
            "┌─────────────┐\n"
            "│ header  logo │\n"
            "└─────────────┘"
        )
        self.assertTrue(_run_stop_prose(msg))


if __name__ == "__main__":
    main()
