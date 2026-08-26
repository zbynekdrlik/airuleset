"""#710 — owner-scoped Discord QUESTION-ping suppression for zbynek + marek.

Owner directive (2026-08-26, verbatim): "Otazky do claude-zbynek-1 uz chcem
prestat uplne dostavat lebo u mam webterm a paticku s U. To mi vyhovuje viacej a
viem to rovno tu prebrat. Otazky stale mozu chodit do claude-david, no mne ani
marekovi (ak mu chodia) uz nie."

So a ❓ QUESTION ping to owners **zbynek** and **marek** is SUPPRESSED at BOTH
Discord transports — the interactive Stop-hook shell path
(`notify-discord-pending.sh` -> `notify-discord-send.sh::emit_one`) AND the
watchdog Python `notify.send(kind="questions")` re-ask path — each leaving an
explicit `suppressed` delivery-log line (never a silent drop, the #546/#704
machine-channel pattern). Owner **david** (and david1-4 -> david) keeps FULL
question delivery. Only DISCORD DELIVERY changes: the session ❓ marker
discipline, the `discord-questions.json` map, `needs-answer` tracking, and the
footer `U N` partition are all UNTOUCHED.

Job 7 (Discord reply -> asking session) is not modified: for david the question
is still RECORDED in the map (so job 7 routes his replies exactly as before);
for zbynek/marek nothing is recorded (the POST is suppressed before the record
step), so job 7 simply has no entry to match — its existing empty-match path, a
no-op, never an error. These locks assert that record-level contract directly
(the map has david's entry, none for zbynek/marek) rather than re-driving job 7
(whose own mechanics are already covered by tests/test_discord_reply.py and are
byte-for-byte unchanged by this ticket).
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

import notify                                             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "hooks" / "notify-discord-pending.sh"


# --------------------------------------------------------------------------- #
# 1. the pure predicate
# --------------------------------------------------------------------------- #
class TestQuestionPingOffPredicate(unittest.TestCase):
    def test_off_owners(self):
        self.assertTrue(notify.question_ping_off("zbynek"))
        self.assertTrue(notify.question_ping_off("marek"))
        # case / whitespace normalized
        self.assertTrue(notify.question_ping_off("  ZBYNEK "))
        self.assertTrue(notify.question_ping_off("Marek"))

    def test_david_stays_on(self):
        self.assertFalse(notify.question_ping_off("david"))
        # david1-4 redirect to owner `david` -> still ON
        self.assertFalse(notify.question_ping_off("david1"))
        self.assertFalse(notify.question_ping_off("david2"))

    def test_stream_personas_routing_into_claude_zbynek_are_off(self):
        # montalu5/montalu1/simap1 route to claude-zbynek via STREAM_NOTIFY_OWNER,
        # so their question pings land in zbynek's thread -> suppressed too.
        self.assertTrue(notify.question_ping_off("montalu5"))
        self.assertTrue(notify.question_ping_off("montalu1"))
        self.assertTrue(notify.question_ping_off("simap1"))

    def test_empty_and_unknown_never_off(self):
        self.assertFalse(notify.question_ping_off(""))
        self.assertFalse(notify.question_ping_off(None))
        self.assertFalse(notify.question_ping_off("someoneelse"))

    def test_owners_off_set_is_exactly_zbynek_and_marek(self):
        self.assertEqual(set(notify.QUESTION_PING_OWNERS_OFF), {"zbynek", "marek"})


# --------------------------------------------------------------------------- #
# 2. the Python send() chokepoint (watchdog re-ask transport)
# --------------------------------------------------------------------------- #
class _HomeIsolated(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-q710-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        # A suppressed send must NEVER touch the network — a spy proves it.
        self._orig_post = notify._post_discord
        self.posts = []
        notify._post_discord = lambda *a, **k: self.posts.append((a, k)) or "999"
        self.addCleanup(lambda: setattr(notify, "_post_discord", self._orig_post))
        # isolate the keyless-auto-dedup / delivery-log state off the real home
        self._orig_cdir = notify._claude_dir
        notify._claude_dir = lambda: str(self.home / ".claude")
        self.addCleanup(lambda: setattr(notify, "_claude_dir", self._orig_cdir))

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]

    def _write_env(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=770099\n"
            "DISCORD_NOTIFICATION_CHANNEL_MAREK_Q=880099\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID_Q=990099\n")


class TestSendSuppressesQuestionsForOffOwners(_HomeIsolated):
    def test_zbynek_question_send_suppressed(self):
        self._write_env()
        r = notify.send("otázka?", owner="zbynek", kind="questions")
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.posts, [], "a zbynek question must POST nothing")

    def test_marek_question_send_suppressed(self):
        self._write_env()
        r = notify.send("otázka?", owner="marek", kind="questions")
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.posts, [], "a marek question must POST nothing")

    def test_david_question_send_still_sends(self):
        self._write_env()
        r = notify.send("otázka?", owner="david", kind="questions")
        self.assertEqual(r, "sent", "david keeps full question delivery")
        self.assertEqual(len(self.posts), 1, "a david question must still POST")

    def test_suppression_is_a_logged_decision_not_silent(self):
        self._write_env()
        notify.send("otázka?", owner="zbynek", kind="questions")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed question must leave a delivery-log line")
        self.assertIn("#710", lines[-1])

    def test_default_kind_to_zbynek_is_NOT_suppressed(self):
        # ONLY questions are owner-scoped off; a ✅ / any other notification to
        # zbynek is unaffected (still POSTs on the normal thread).
        self._write_env()
        r = notify.send("hotovo", owner="zbynek", kind="default")
        self.assertEqual(r, "sent")
        self.assertEqual(len(self.posts), 1)

    def test_return_message_id_shape_is_respected(self):
        self._write_env()
        r = notify.send("q?", owner="marek", kind="questions", return_message_id=True)
        self.assertEqual(r, ("suppressed", None))

    def test_dry_run_suppressed_mutates_nothing(self):
        self._write_env()
        r = notify.send("q?", owner="zbynek", kind="questions", dry_run=True)
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.log_lines(), [],
                         "dry-run must not write to the delivery log")


class TestWatchdogRepingHonorsSuppression(_HomeIsolated):
    """The watchdog daily re-ask (`reping_stale_questions`) calls
    `send_fn(kind="questions")` == notify.send in production — so a zbynek/marek
    re-ask is suppressed at the same chokepoint, and the entry is KEPT (never
    dropped, so session-side tracking is unchanged)."""

    def _qmap(self, owner):
        p = self.home / ".claude" / "discord-questions.json"
        # ts far in the past so it is due for a re-ask.
        p.write_text(json.dumps({
            "111": {"block": "**Otázka — projekt X:** …\n❓ NEEDS YOU: čo?",
                    "session": "sid-1", "cwd": "/tmp/x", "ts": 1}}))
        return str(p)

    def test_zbynek_reping_suppressed_and_entry_kept(self):
        import unittest.mock as m
        import watchdog as wd
        import watchdog.questions as wq
        self._write_env()
        path = self._qmap("zbynek")
        # Neutralise the 00:00-05:59 sleep-window deferral so the re-ask actually
        # runs regardless of the wall clock (the documented #457 clock-flake).
        with m.patch.object(wd, "_in_sleep_window", lambda *a, **k: False):
            logs = wq.reping_stale_questions(
                now=10 ** 9, send_fn=notify.send, path=path,
                account_owner="zbynek")
        self.assertEqual(self.posts, [], "a zbynek re-ask must POST nothing")
        self.assertTrue(any("suppressed" in ln for ln in logs)
                        or any("suppressed" in ln for ln in self.log_lines()),
                        (logs, self.log_lines()))
        # entry KEPT (not dropped) — session-side tracking unchanged
        left = json.loads(Path(path).read_text())
        self.assertIn("111", left, "the question entry must stay tracked")


# --------------------------------------------------------------------------- #
# 3. the interactive Stop-hook shell transport (notify-discord-pending.sh)
# --------------------------------------------------------------------------- #
def _fake_curl_bin(http="200", mid="710999"):
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-710-"))
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"%s\"}' '%s'\n" % (mid, http))
    (d / "curl").chmod(0o755)
    return d


_NEEDS_YOU = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Pri tikete #710 potrebujem tvoj výber.\n"
    "\n"
    "- Možnosť A (odporúčam)\n"
    "- Možnosť B\n"
    "\n"
    "❓ NEEDS YOU: ktorú možnosť mám použiť?")


class InteractiveQuestionSuppression(unittest.TestCase):
    _n = 0

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-710-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.cwd = Path(tempfile.mkdtemp(prefix="airuleset-710-cwd-"))
        self.addCleanup(shutil.rmtree, self.cwd, True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123\n"
            "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=77799\n"
            "DISCORD_NOTIFICATION_CHANNEL_MAREK_Q=88899\n"
            "DISCORD_NOTIFICATION_CHANNEL_DAVID_Q=99977\n")

    def _sid(self, label):
        InteractiveQuestionSuppression._n += 1
        sid = "t710-%s-%d-%d" % (label, os.getpid(),
                                 InteractiveQuestionSuppression._n)
        for pre in ("lastq", "pending", "pending-cwd", "cardchk"):
            p = "/tmp/claude-discord-%s-%s" % (pre, sid)
            self.addCleanup(lambda p=p: os.path.exists(p) and os.remove(p))
        return sid

    def _fire(self, sid, owner, msg, mid="710999"):
        cbin = _fake_curl_bin(mid=mid)
        self.addCleanup(shutil.rmtree, cbin, True)
        env = {**os.environ, "HOME": str(self.home),
               "PATH": str(cbin) + os.pathsep + os.environ["PATH"],
               "ND_BLOCK_SETTLE": "0", "TMUX_PANE": "",
               "AIRULESET_NOTIFY_OWNER": owner}
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

    def test_zbynek_interactive_question_suppressed(self):
        sid = self._sid("zbynek")
        r = self._fire(sid, "zbynek", _NEEDS_YOU, mid="710001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # never POSTed -> never recorded in the question map (nothing for job 7)
        self.assertNotIn("710001", self._qmap(), self._qmap())
        # suppression is a durable, logged decision, not a silent drop
        dlog = self._dlog()
        self.assertTrue(any("suppressed" in ln and "710" in ln
                            for ln in dlog.splitlines()),
                        "expected a #710 suppressed delivery-log line: " + repr(dlog))
        self.assertFalse(any(ln.split()[1:2] == ["sent"]
                             for ln in dlog.splitlines() if len(ln.split()) > 1),
                         "a suppressed question must not log a 'sent' line: " + repr(dlog))

    def test_marek_interactive_question_suppressed(self):
        sid = self._sid("marek")
        r = self._fire(sid, "marek", _NEEDS_YOU, mid="710002")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("710002", self._qmap(), self._qmap())
        self.assertTrue(any("suppressed" in ln for ln in self._dlog().splitlines()),
                        repr(self._dlog()))

    def test_david_interactive_question_still_delivers_and_records(self):
        # david keeps full delivery: POSTed (fake curl 200) AND recorded in the
        # question map, so watchdog job 7 can route his reply back exactly as
        # before (the "job 7 david roundtrip unbroken" contract, at the record
        # level job 7 consumes).
        sid = self._sid("david")
        r = self._fire(sid, "david", _NEEDS_YOU, mid="710003")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("710003", self._qmap(), self._qmap())
        self.assertEqual(self._qmap()["710003"]["session"], sid)
        self.assertTrue(any(len(ln.split()) > 1 and ln.split()[1] == "sent"
                            for ln in self._dlog().splitlines()),
                        repr(self._dlog()))


if __name__ == "__main__":
    unittest.main()
