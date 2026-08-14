"""#467 — the ask-and-continue (❓ ASKED body + ⏳ WORKING terminal) question
must ping + register EXACTLY like a terminal ❓ NEEDS YOU, and every suppressed
delivery must leave a durable delivery-log line.

Live incident (dev1, 2026-08-14 06:40, session 2d02a127): a fully template-
compliant ask-and-continue question about #453 never reached Discord and never
entered `discord-questions.json`, and `notify-delivery.log` carried NOTHING —
a silent, undiagnosable loss.

Root cause: `hooks/notify-discord-pending.sh`'s `send_q()` block-file settle
check scanned the OVER-BROAD glob `/tmp/airuleset-*-block-<sid>`, which matches
the retry/block markers of ALL eight stop gates. In a busy autopilot batch an
ask-and-continue ⏳ WORKING turn routinely trips `stop-check-working-liveness.sh`
(nothing live in `background_tasks` once the dispatched worker returned), which
leaves a fresh `airuleset-working-liveness-block-<sid>` marker. `send_q` read
that UNRELATED block as "this question draft was rejected, the rewrite will
re-deliver" and dropped the ping — silently, with no delivery-log line.

Fix: scope the settle check to the ONE gate whose block is genuinely about the
question (`airuleset-question-quality-block-<sid>`), and log every suppression.
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

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"

# The incident's exact shape: full **Otázka** block + an ❓ ASKED body line +
# a ⏳ WORKING terminal line (ask-and-continue about #453).
ASKED_MSG = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Pri tikete #453 je otvorené jedno rozhodnutie o smerovaní notifikácií a "
    "potrebujem tvoj výber, nech to nastavím správne.\n"
    "\n"
    "- Presmerovať cez STREAM_NOTIFY_OWNER (odporúčam) — nasadí sa pri pushi\n"
    "- Nechať mirror v lokálnom .env — treba ručne na tom boxe\n"
    "\n"
    "❓ ASKED: ktorý spôsob smerovania mám pre #453 použiť?\n"
    "\n"
    "⏳ WORKING: medzitým pokračujem na ostatných tiketoch dávky")

NEEDS_YOU_MSG = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Pri tikete #453 je otvorené jedno rozhodnutie o smerovaní notifikácií.\n"
    "\n"
    "- Presmerovať cez STREAM_NOTIFY_OWNER (odporúčam) — nasadí sa pri pushi\n"
    "- Nechať mirror v lokálnom .env — treba ručne na tom boxe\n"
    "\n"
    "❓ NEEDS YOU: ktorý spôsob smerovania mám pre #453 použiť?")


def _fake_curl_bin(http="200", mid="453999"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-467-"))
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '%s'\n" % (mid, http))
    (d / "curl").chmod(0o755)
    return d


class AskAndContinueDelivery(unittest.TestCase):
    """Each test uses an isolated tmp HOME and a fresh, counter-suffixed
    session id (the /tmp per-session markers the hook writes are cleaned up
    per the same established pattern as tests/test_question_footer_e2e.py)."""

    _n = 0

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-467-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-467-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")

    def _sid(self, label):
        AskAndContinueDelivery._n += 1
        sid = "t467-%s-%d-%d" % (label, os.getpid(), AskAndContinueDelivery._n)
        for pre in ("lastq", "pending", "cardchk"):
            p = "/tmp/claude-discord-%s-%s" % (pre, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        for gate in ("working-liveness", "status-marker", "question-quality",
                     "prose", "untracked-work"):
            p = "/tmp/airuleset-%s-block-%s" % (gate, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        return sid

    def _plant_marker(self, gate, sid):
        p = "/tmp/airuleset-%s-block-%s" % (gate, sid)
        Path(p).write_text("1")            # fresh mtime = now
        return p

    def _fire(self, sid, msg, mid="453999"):
        cbin = _fake_curl_bin(mid=mid)
        self.addCleanup(shutil.rmtree, cbin, True)
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(cbin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": str(self.cwd)})
        return subprocess.run(["bash", str(PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    def _qmap(self):
        p = self.home / ".claude" / "discord-questions.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _dlog(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""

    # --- the incident: an UNRELATED gate's block must not eat the question ---

    def test_ask_and_continue_delivers_despite_working_liveness_block(self):
        # THE incident: a fresh working-liveness block marker (the gate that
        # fires on a ⏳ WORKING turn) must NOT suppress the question ping.
        sid = self._sid("liveness")
        self._plant_marker("working-liveness", sid)
        r = self._fire(sid, ASKED_MSG, mid="453001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("453001", self._qmap(), self._qmap())
        self.assertEqual(self._qmap()["453001"]["session"], sid)
        self.assertTrue(any("sent" in ln for ln in self._dlog().splitlines()),
                        self._dlog())

    def test_ask_and_continue_delivers_despite_status_marker_block(self):
        # A second orthogonal gate — same guarantee (defense in depth).
        sid = self._sid("statusm")
        self._plant_marker("status-marker", sid)
        r = self._fire(sid, ASKED_MSG, mid="453002")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("453002", self._qmap(), self._qmap())

    def test_needs_you_delivers_despite_unrelated_block(self):
        # The terminal ❓ NEEDS YOU path is served by the SAME send_q — an
        # unrelated block no longer eats it either. (The camera-box double-ping
        # guard is preserved; only the OVER-broad suppression is narrowed.)
        sid = self._sid("needs")
        self._plant_marker("status-marker", sid)
        r = self._fire(sid, NEEDS_YOU_MSG, mid="453003")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("453003", self._qmap(), self._qmap())

    # --- preserved guarantee: a question-quality block STILL suppresses -----

    def test_question_quality_block_still_suppresses_and_logs(self):
        # The camera-box no-ping-for-a-rejected-draft guarantee (2026-07-05):
        # when the question-quality gate is actively rewriting a MALFORMED
        # draft, the ping IS suppressed — but now with a durable delivery-log
        # line naming the reason, never silently (#467).
        sid = self._sid("qq")
        self._plant_marker("question-quality", sid)
        r = self._fire(sid, ASKED_MSG, mid="453004")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("453004", self._qmap(),
                         "a question-quality-blocked draft must still be suppressed")
        self.assertTrue(
            any("question-quality-rewrite" in ln for ln in self._dlog().splitlines()),
            "the suppression must leave a delivery-log line: " + repr(self._dlog()))

    # --- dedup unchanged, now diagnosable ----------------------------------

    def test_verbatim_repeat_is_silent_and_logged(self):
        # A /goal re-poke repeats the SAME question verbatim: the FIRST fire
        # delivers + writes LASTQ; the SECOND (byte-identical) is deduped — no
        # second POST (map still has ONLY the first entry) — and now leaves a
        # 'verbatim-repeat-dedup' delivery-log line instead of vanishing.
        sid = self._sid("verbatim")
        r1 = self._fire(sid, ASKED_MSG, mid="453005")
        self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
        self.assertIn("453005", self._qmap(), self._qmap())
        r2 = self._fire(sid, ASKED_MSG, mid="453006")   # verbatim repeat
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertNotIn("453006", self._qmap(),
                         "a verbatim repeat must not POST again")
        self.assertTrue(
            any("verbatim-repeat-dedup" in ln for ln in self._dlog().splitlines()),
            "the dedup suppression must leave a delivery-log line: "
            + repr(self._dlog()))


if __name__ == "__main__":
    unittest.main()
