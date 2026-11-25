"""#1397 — a peer channel message is not an owner prompt.

camera-box (24.–25.11.2026) re-printed one full owner question 12 times
overnight: every peer message (`<channel source="plugin:peer:peer" ...>`) fired
UserPromptSubmit, `clear-question-dedup.sh` classified it as HUMAN, deleted the
delivered-question key and stamped the user-present marker, so the repeat block
in `stop-check-question-quality.sh` never fired.

A peer message must behave like a task-notification: dedup kept, no presence
stamp, a logged auto skip. Only the peer plugin is matched: another channel
plugin (for example a chat bridge the owner types into) stays human.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import _hook_state_cleanup as hsc  # noqa: E402
import machine_prompts as mp  # noqa: E402

CLEAR = ROOT / "hooks" / "clear-question-dedup.sh"

PEER_PROMPT = (
    '<channel source="plugin:peer:peer" from="dasio">\n'
    "dasio: status 01:23Z. First two runs done. New estimate for \"rig free\": "
    "about 02:40Z.\n</channel>"
)
OTHER_CHANNEL_PROMPT = (
    '<channel source="plugin:discord:discord" chat_id="1">\n'
    "1\n</channel>"
)


class PeerPromptIsNotOwner(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1397-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    def _paths(self, sid):
        return (Path("/tmp/claude-discord-lastq-%s" % sid),
                Path("/tmp/claude-lastq-refs-%s" % sid),
                Path("/tmp/claude-user-active-%s" % sid))

    def _run(self, sid, prompt):
        lastq, refs, _ = self._paths(sid)
        lastq.write_text("Mám do release testu pridať rozbeh (1) alebo nie (2)?")
        refs.write_text("#1551")
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(CLEAR)],
                           input=json.dumps({"session_id": sid, "prompt": prompt}),
                           text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_peer_message_keeps_the_delivered_question(self):
        sid = hsc.new_hook_sid(self, "t1397")
        self._run(sid, PEER_PROMPT)
        lastq, refs, active = self._paths(sid)
        self.assertTrue(lastq.exists(), "peer message wiped LASTQ (#1397)")
        self.assertTrue(refs.exists(), "peer message wiped the refs key (#1397)")
        self.assertFalse(active.exists(), "peer message stamped user-present")

    def test_peer_skip_is_logged(self):
        sid = hsc.new_hook_sid(self, "t1397")
        self._run(sid, PEER_PROMPT)
        log = (self.home / ".claude" / "notify-delivery.log").read_text()
        self.assertIn("reason=auto:peer-channel", log)

    def test_other_channel_plugin_stays_human(self):
        sid = hsc.new_hook_sid(self, "t1397")
        self._run(sid, OTHER_CHANNEL_PROMPT)
        lastq, _, active = self._paths(sid)
        self.assertFalse(lastq.exists(), "a non-peer channel must stay human")
        self.assertTrue(active.exists())
        active.unlink()

    def test_machine_prompt_catalog_knows_peer_messages(self):
        self.assertTrue(PEER_PROMPT.startswith(mp.MACHINE_PROMPT_PREFIXES))
        self.assertFalse(OTHER_CHANNEL_PROMPT.startswith(mp.MACHINE_PROMPT_PREFIXES))


if __name__ == "__main__":
    unittest.main()
