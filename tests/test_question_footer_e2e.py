"""#161 Part 1 — the `Q N` statusline segment, proven end to end, not assumed.

The chain has three links: (1) a confirmed `❓` POST (NEEDS YOU or ASKED)
records an entry in `~/.claude/discord-questions.json` via
`notify.record_question` (`hooks/notify-discord-send.sh`'s 2xx branch,
called from `hooks/notify-discord-pending.sh`'s `send_q()`); (2) the entry is
dropped on the right events (answered, later human prompt, 24h TTL) — that
half is covered by `tests/test_discord_reply.py` and
`tests/test_question_prune.py`; (3) `statusbar.questions_segment` renders it.

Before this file, ONLY the SOURCE TEXT of the wiring was asserted
(`TestSendPathRecordsQuestion` in `tests/test_airuleset.py` — `assertIn`
over the hook's own bytes, never executed). This file runs the REAL Stop
hook as a real subprocess, with a faked `curl` standing in for Discord (never
touches the network), against a real temp `HOME`, and asserts the FULL
chain: the hook runs -> `discord-questions.json` gets a real entry ->
`statusbar.questions_segment` renders it non-empty. Nothing here needed a
code change — every link in this chain was already correct; this is the
missing proof, not a fix.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PENDING_HOOK = ROOT / "hooks" / "notify-discord-pending.sh"


def _path_with_fake_curl(http_code="200", msg_id="999000111"):
    """A bin dir with a fake `curl` standing in for Discord's REST API —
    prepended to the real PATH so `jq`/`python3`/everything else the hook
    needs stays resolvable. Mirrors `tests/test_notify_delivery_log.py`'s
    own `_path_with_fake_curl` (kept local + self-contained here rather
    than imported, since that module is not a shared test-utility home)."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-q-"))
    fake = d / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '%s'\n" % (msg_id, http_code))
    fake.chmod(0o755)
    return str(d), str(d) + os.pathsep + os.environ.get("PATH", "")


class QuestionFooterEndToEnd(unittest.TestCase):
    """Every test writes into an isolated tmp `HOME` — never the real one
    (the live api-watchdog + this developer's own real Discord config share
    this box)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-q-e2e-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-q-e2e-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")

    def _fire(self, sid, msg, msg_id="999000111"):
        curl_dir, path = _path_with_fake_curl(msg_id=msg_id)
        self.addCleanup(shutil.rmtree, curl_dir, True)
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": str(self.cwd)})
        env = {**os.environ, "HOME": str(self.home), "PATH": path,
              "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        return subprocess.run(["bash", str(PENDING_HOOK)], input=payload,
                              text=True, capture_output=True, env=env)

    def _questions_map(self):
        p = self.home / ".claude" / "discord-questions.json"
        if not p.exists():
            return {}
        return json.loads(p.read_text())

    def test_needs_you_confirmed_send_records_the_question(self):
        r = self._fire("sid-e2e-needs-you",
                       "**Otázka — projekt demo:** kontext.\n"
                       "• Áno (odporúčam) — pokračuje\n"
                       "❓ NEEDS YOU: pokračovať?",
                       msg_id="777001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        qmap = self._questions_map()
        self.assertIn("777001", qmap, qmap)
        self.assertEqual(qmap["777001"]["session"], "sid-e2e-needs-you")
        self.assertEqual(qmap["777001"]["cwd"], str(self.cwd))

    def test_asked_confirmed_send_ALSO_records_the_question(self):
        # ask-and-continue: the turn ends ⏳ WORKING, not ❓ — the ping still
        # fires IMMEDIATELY (message-status-marker.md) and must be recorded
        # exactly like a NEEDS YOU block.
        r = self._fire("sid-e2e-asked",
                       "❓ ASKED: ktorý dizajn?\n\n"
                       "⏳ WORKING: medzitým pokračujem na inom tickete",
                       msg_id="777002")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        qmap = self._questions_map()
        self.assertIn("777002", qmap, qmap)
        self.assertEqual(qmap["777002"]["session"], "sid-e2e-asked")

    def test_the_recorded_question_then_renders_in_the_footer(self):
        # The FULL chain, not just the write: after a real confirmed ❓ POST,
        # statusbar.questions_segment (the shim's own `Q N` badge) must
        # render it non-empty for this exact project — the missing half of
        # the proof #161 asked for.
        self._fire("sid-e2e-footer",
                  "❓ NEEDS YOU: nasadiť teraz?", msg_id="777003")
        seg = statusbar.questions_segment(str(self.cwd), home=self.home)
        self.assertIn("Q 1", seg, seg)

    def test_a_different_project_shows_up_as_elsewhere(self):
        self._fire("sid-e2e-inde", "❓ NEEDS YOU: nasadiť teraz?",
                  msg_id="777004")
        other_cwd = tempfile.mkdtemp(prefix="airuleset-q-e2e-other-")
        try:
            seg = statusbar.questions_segment(other_cwd, home=self.home)
            self.assertIn("inde 1", seg, seg)
        finally:
            shutil.rmtree(other_cwd, ignore_errors=True)

    def test_no_delivery_no_footer_entry(self):
        # a DENIED (non-2xx) send must NEVER record a question — the map
        # only ever holds a CONFIRMED delivery (review finding, 2026-07-04:
        # a transient failure must stay retryable, never silently "pinged").
        curl_dir, path = _path_with_fake_curl(http_code="500",
                                              msg_id="777005")
        self.addCleanup(shutil.rmtree, curl_dir, True)
        payload = json.dumps({"session_id": "sid-e2e-fail",
                              "last_assistant_message":
                              "❓ NEEDS YOU: nasadiť teraz?",
                              "cwd": str(self.cwd)})
        env = {**os.environ, "HOME": str(self.home), "PATH": path,
              "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "",
              "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        subprocess.run(["bash", str(PENDING_HOOK)], input=payload, text=True,
                       capture_output=True, env=env)
        self.assertEqual(self._questions_map(), {})
        seg = statusbar.questions_segment(str(self.cwd), home=self.home)
        self.assertEqual(seg, "")


if __name__ == "__main__":
    unittest.main()
