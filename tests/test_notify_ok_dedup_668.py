"""#668 — the idle ✅ device ping: dedup identical content + a reliable project
label.

Two coupled defects reported live (david1/2@subdev, ticket odoo-erp 5023):

  (b) The SAME `✅ unknown — hotovo` message reached David FOUR TIMES. The ✅
      path has NO content dedup — only the ❓ path (`send_q`/`LASTQ`) does — so
      a session that re-reports the identical `✅ DONE` across several Stop/idle
      cycles (a /goal-loop re-poke of a stream that handed off its last ticket)
      pings once per cycle. This mirrors the ❓ 9× "rovnaká otázka" restreamer
      spam that LASTQ dedup fixed.

  (a) The label read "unknown". The Stop hook that RECORDS the ✅ has a reliable
      cwd but discards it; the idle hook re-derives the project from its OWN
      (possibly empty) event cwd, and the send path decorates an unresolved
      project with a meaningless "unknown" and sends it anyway.

These bash-hook integration tests drive the real hooks with an isolated HOME and
a fresh uuid session id (the /tmp per-session markers cleaned up per the same
pattern as tests/test_notify_ask_and_continue_delivery.py).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
IDLE = ROOT / "hooks" / "notify-discord.sh"
SEND = ROOT / "hooks" / "notify-discord-send.sh"
STOP = ROOT / "hooks" / "notify-discord-pending.sh"
CLEAR = ROOT / "hooks" / "clear-question-dedup.sh"


def _fake_curl_bin(http="200"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-668-"))
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"999\"}' '%s'\n" % http)
    (d / "curl").chmod(0o755)
    return d


class _Base(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-668-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    def _sid(self):
        sid = "t668-" + uuid.uuid4().hex
        for pre in ("pending", "pending-cwd", "lastok", "cardchk"):
            p = "/tmp/claude-discord-%s-%s" % (pre, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        self.addCleanup(
            lambda s=sid: os.path.exists("/tmp/claude-user-active-%s" % s)
            and os.remove("/tmp/claude-user-active-%s" % s))
        return sid

    def _pending(self, sid):
        return "/tmp/claude-discord-pending-%s" % sid

    def _dlog(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""


class OkDedup(_Base):
    """(b) — an identical ✅ delivers EXACTLY ONCE per distinct content, and
    re-pings only after a real user prompt (UserPromptSubmit clears LASTOK)."""

    def _fire_idle(self, sid, dryrun_file, cwd):
        env = {**os.environ, "HOME": str(self.home),
               "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(dryrun_file),
               "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": ""}
        payload = json.dumps({"session_id": sid, "cwd": str(cwd)})
        return subprocess.run(["bash", str(IDLE)], input=payload, text=True,
                              capture_output=True, env=env)

    def _deliveries(self, dryrun_file):
        if not Path(dryrun_file).exists():
            return 0
        return Path(dryrun_file).read_text().count("**✅")

    def test_identical_ok_delivers_exactly_once(self):
        sid = self._sid()
        out = self.home / "dry.txt"
        # cycle 1 — a fresh ✅ pending is delivered
        Path(self._pending(sid)).write_text("✅ hotovo")
        r = self._fire_idle(sid, out, self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._deliveries(out), 1)
        # cycle 2 — the SAME ✅ re-recorded (a re-poke), NO user input between:
        # exactly-once means it must NOT deliver again.
        Path(self._pending(sid)).write_text("✅ hotovo")
        self._fire_idle(sid, out, self.home)
        self.assertEqual(self._deliveries(out), 1,
                         "an identical ✅ must deliver exactly once")

    def test_a_user_prompt_reopens_an_identical_ok(self):
        sid = self._sid()
        out = self.home / "dry.txt"
        Path(self._pending(sid)).write_text("✅ hotovo")
        self._fire_idle(sid, out, self.home)
        self.assertEqual(self._deliveries(out), 1)
        # the user typed → clear-question-dedup.sh clears the ✅ dedup too
        env = {**os.environ, "HOME": str(self.home)}
        subprocess.run(["bash", str(CLEAR)], input=json.dumps({"session_id": sid}),
                       text=True, capture_output=True, env=env)
        Path(self._pending(sid)).write_text("✅ hotovo")
        self._fire_idle(sid, out, self.home)
        self.assertEqual(self._deliveries(out), 2,
                         "a fresh ✅ after the user spoke must re-ping")

    def test_a_different_ok_still_delivers(self):
        sid = self._sid()
        out = self.home / "dry.txt"
        Path(self._pending(sid)).write_text("✅ hotovo")
        self._fire_idle(sid, out, self.home)
        Path(self._pending(sid)).write_text("✅ nasadené v1.2.3")
        self._fire_idle(sid, out, self.home)
        self.assertEqual(self._deliveries(out), 2,
                         "a DIFFERENT ✅ is a distinct completion — must deliver")


class ProjectLabelNoUnknown(_Base):
    """(a) — an unresolvable project never ships the decorative "unknown"; the
    non-delivery reason is logged LOUD instead."""

    def test_dry_run_never_labels_the_ping_unknown(self):
        out = self.home / "dry.txt"
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "✅",
               "ND_TEXT": "hotovo", "ND_CWD": "",
               "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(out),
               "AIRULESET_NOTIFY_OWNER": ""}
        r = subprocess.run(["bash", str(SEND)], input="", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        content = out.read_text()
        self.assertIn("**✅", content)          # still delivered
        self.assertIn("hotovo", content)
        self.assertNotIn("unknown", content,
                         "an unresolved project must not decorate the ping")

    def test_an_unresolved_project_is_logged_loud_and_still_sent(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")
        cbin = _fake_curl_bin("200")
        self.addCleanup(shutil.rmtree, cbin, True)
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "✅",
               "ND_TEXT": "hotovo", "ND_CWD": "",
               "PATH": str(cbin) + os.pathsep + os.environ["PATH"],
               "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        r = subprocess.run(["bash", str(SEND)], input="", text=True,
                           capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = self._dlog()
        self.assertIn("unresolved-project", log, log)


class PendingCarriesCwd(_Base):
    """(a) end-to-end — the Stop hook records the ✅ WITH its reliable cwd, so
    the idle delivery resolves the real project even when the idle event cwd is
    empty. Before the fix the label collapsed to "unknown"."""

    def test_recorded_cwd_resolves_the_project_when_idle_cwd_is_empty(self):
        sid = self._sid()
        out = self.home / "dry.txt"
        # 1) Stop hook records a ✅ DONE with a real repo cwd (airuleset ROOT).
        stop_env = {**os.environ, "HOME": str(self.home), "TMUX_PANE": "",
                    "AIRULESET_NOTIFY_OWNER": "", "ND_BLOCK_SETTLE": "0"}
        stop_env.pop("DISCORD_NOTIFY_DRYRUN", None)
        stop_env.pop("ND_DRYRUN_FILE", None)
        stop_payload = json.dumps({
            "session_id": sid, "cwd": str(ROOT),
            "last_assistant_message": "✅ DONE: hotovo"})
        rs = subprocess.run(["bash", str(STOP)], input=stop_payload, text=True,
                            capture_output=True, env=stop_env)
        self.assertEqual(rs.returncode, 0, rs.stdout + rs.stderr)
        self.assertTrue(os.path.exists(self._pending(sid)),
                        "the ✅ must be recorded to a pending file")
        # 2) idle delivers with an EMPTY event cwd — the recorded ROOT cwd must
        #    still resolve the project name (never "unknown").
        idle_env = {**os.environ, "HOME": str(self.home),
                    "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(out),
                    "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": ""}
        idle_payload = json.dumps({"session_id": sid, "cwd": ""})
        ri = subprocess.run(["bash", str(IDLE)], input=idle_payload, text=True,
                            capture_output=True, env=idle_env)
        self.assertEqual(ri.returncode, 0, ri.stdout + ri.stderr)
        content = out.read_text()
        self.assertIn("airuleset", content,
                      "the recorded cwd must resolve the real project label")
        self.assertNotIn("unknown", content)


if __name__ == "__main__":
    unittest.main()
