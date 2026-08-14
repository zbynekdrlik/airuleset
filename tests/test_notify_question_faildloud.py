"""#466 — every ❓ turn MUST leave a delivery-log line (fail-loud) + each line
must identify WHICH question (per-question hash). Residual after #467.

Live incident (gk box, 2026-08-14 ~10:33, session f219f0e3): a template-
compliant `❓ NEEDS YOU:` APK-install question (odoo-erp #3488) never reached
Discord, never entered `discord-questions.json`, and `notify-delivery.log`
carried NOTHING for that turn — a silent, undiagnosable loss.

Root cause the residual closes: `hooks/notify-discord-pending.sh` runs all of
#467's fail-loud logging INSIDE `send_q()`, but `send_q()` is only reached from
two dispatch branches (`❓ ASKED` on a body line, or `❓` as the LAST non-empty
line). Any ❓-carrying turn that misses both — a `❓ NEEDS YOU` block whose
marker is NOT the last line (a trailing note, or a wrapped long URL leaving a
tail fragment), or the `❓ … vlož … /goal` arm-skip — falls to a branch that
writes zero trace. This suite pins: EVERY ❓ turn leaves a log line, and every
`kind=pending` / `sent` line carries a `qhash=` field.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"

# A `❓ NEEDS YOU` block whose marker is NOT the last non-empty line — the
# incident shape (long URL with `&`, multiline numbered list, a trailing note).
TRAILING_MSG = (
    "**Otázka — projekt odoo-erp (ERP pre firmu):** Pri APK builde #3488 "
    "treba rozhodnut postup instalacie.\n"
    "\n"
    "- Cez USB debug (odporucam)\n"
    "- Cez odkaz https://example.com/apk?a=1&b=2&c=3\n"
    "\n"
    "❓ NEEDS YOU: ktory sposob instalacie APK mam pouzit?\n"
    "\n"
    "Poznamka: build je pripraveny na dev2.")

# Marker IS the last non-empty line — the happy path (reaches send_q → POST).
NEEDS_YOU_MSG = (
    "**Otázka — projekt airuleset (nastroj na spravu Claude Code pravidiel):** "
    "Pri tikete #453 je otvorene jedno rozhodnutie o smerovani notifikacii.\n"
    "\n"
    "- Presmerovat cez STREAM_NOTIFY_OWNER (odporucam)\n"
    "- Nechat mirror v lokalnom .env\n"
    "\n"
    "❓ NEEDS YOU: ktory sposob smerovania mam pre #453 pouzit?")

# The /goal arm-skip shape (❓ … vlož … /goal) — a machine question, never a
# phone ping, but STILL a ❓ turn that must leave a trace.
ARM_MSG = (
    "**Otázka — projekt airuleset:** backlog je pripraveny.\n"
    "\n"
    "❓ NEEDS YOU: vloz tento /goal prikaz do terminalu nech sa spusti autopilot")


def _fake_curl_bin(mid="466999"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-466-"))
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '200'\n" % mid)
    (d / "curl").chmod(0o755)
    return d


class QuestionFailLoud(unittest.TestCase):
    _n = 0

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-466-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-466-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")

    def _sid(self, label):
        QuestionFailLoud._n += 1
        sid = "t466-%s-%d-%d" % (label, os.getpid(), QuestionFailLoud._n)
        for pre in ("lastq", "pending", "cardchk"):
            p = "/tmp/claude-discord-%s-%s" % (pre, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        return sid

    def _fire(self, sid, msg, mid="466999"):
        cbin = _fake_curl_bin(mid=mid)
        self.addCleanup(shutil.rmtree, cbin, True)
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(cbin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "",
               "AIRULESET_NOTIFY_OWNER": ""}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        payload = json.dumps({"session_id": sid, "last_assistant_message": msg,
                              "cwd": str(self.cwd)})
        return subprocess.run(["bash", str(PENDING)], input=payload, text=True,
                              capture_output=True, env=env)

    def _dlog_lines(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text().splitlines() if p.exists() else []

    # --- the incident: a ❓ turn that misses send_q must NOT vanish ----------

    def test_needs_you_with_trailing_line_leaves_a_log_line(self):
        # THE incident class: `❓ NEEDS YOU` present but not the last non-empty
        # line → old dispatch fell to `else` and wrote nothing. Must now leave
        # a durable, reason-bearing delivery-log line.
        sid = self._sid("trailing")
        r = self._fire(sid, TRAILING_MSG, mid="466001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = self._dlog_lines()
        self.assertTrue(
            any("unhandled" in ln for ln in lines),
            "a ❓-carrying turn that misses send_q must leave a fail-loud "
            "delivery-log line, never vanish: " + repr(lines))

    def test_arm_question_skip_leaves_a_log_line(self):
        # The `❓ … vlož … /goal` machine-question skip: no phone ping (correct),
        # but STILL a ❓ turn — it must leave a trace instead of exiting silent.
        sid = self._sid("arm")
        r = self._fire(sid, ARM_MSG, mid="466002")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = self._dlog_lines()
        self.assertTrue(
            any("arm-question" in ln for ln in lines),
            "the arm-question skip must leave a delivery-log line: "
            + repr(lines))

    def test_large_message_with_early_marker_still_logs(self):
        # The backstop grep must survive a payload larger than the 64 KiB pipe
        # buffer with the ❓ marker EARLY: under `set -o pipefail`, a
        # `printf … | grep -q` quits at the early match while printf is still
        # writing, so printf takes SIGPIPE, the pipe returns 141, the `if` goes
        # false, and the `unhandled` line is silently never written — the exact
        # silence class this fix exists to close (#190/#192/#194). A here-string
        # has no concurrent writer, so it must log regardless of size.
        sid = self._sid("large")
        msg = (
            "**Otázka — projekt odoo-erp (ERP pre firmu):** rozhodni postup.\n"
            "\n"
            "❓ NEEDS YOU: ktory sposob instalacie APK mam pouzit?\n"
            "\n"
            + ("Poznamka k buildu, riadok. " * 4000))   # ~112 KB trailing note
        self.assertGreater(len(msg.encode()), 65536)     # past the pipe buffer
        r = self._fire(sid, msg, mid="466007")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(
            any("unhandled" in ln for ln in self._dlog_lines()),
            "a large ❓-carrying turn whose marker is not the last line must "
            "still leave a fail-loud line — the backstop grep must not lose the "
            "SIGPIPE race: " + repr(self._dlog_lines()))

    # --- per-question identification (req c) --------------------------------

    def test_pending_lines_carry_qhash(self):
        # (Coverage note: the reword-in-place / edit-fallthrough / not-delivered /
        # arm-question _pending_log calls all pass the SAME proven-non-empty $QH
        # and are log-only; the edit-SUCCESS path is not directly exercisable in
        # this bash harness because --edit-question uses urllib, not the faked
        # curl — the dedup line below is the reachable proxy for the qhash field.)
        # A verbatim re-poke logs a `verbatim-repeat-dedup` pending line — it
        # (like every kind=pending line) must carry a per-question `qhash=` so
        # two DIFFERENT questions are distinguishable in the log (the
        # per-project `❓:<project>` key never could).
        sid = self._sid("qhash")
        self._fire(sid, NEEDS_YOU_MSG, mid="466003")     # first ask → LASTQ set
        self._fire(sid, NEEDS_YOU_MSG, mid="466004")     # verbatim repeat → dedup
        dedup = [ln for ln in self._dlog_lines()
                 if "verbatim-repeat-dedup" in ln]
        self.assertTrue(dedup, "expected a dedup line: "
                        + repr(self._dlog_lines()))
        self.assertTrue(
            all(re.search(r"qhash=\S", ln) for ln in dedup),
            "every kind=pending line must carry a non-empty qhash=: "
            + repr(dedup))

    def test_sent_line_carries_qhash(self):
        # The POST path's `sent` line (send hook) must also carry the qhash, so
        # even a delivered question is per-question identifiable in the log.
        sid = self._sid("sentq")
        r = self._fire(sid, NEEDS_YOU_MSG, mid="466005")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sent = [ln for ln in self._dlog_lines() if " sent " in (" " + ln + " ")]
        self.assertTrue(sent, "expected a sent line: "
                        + repr(self._dlog_lines()))
        self.assertTrue(
            all(re.search(r"qhash=\S", ln) for ln in sent),
            "the sent delivery-log line must carry a non-empty qhash=: "
            + repr(sent))


if __name__ == "__main__":
    unittest.main()
