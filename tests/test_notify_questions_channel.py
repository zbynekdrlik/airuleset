"""#330 — a ❓ question ping falls back to the owner's NORMAL Discord thread
whenever `DISCORD_NOTIFICATION_CHANNEL_<OWNER>_Q` is not configured on THIS
box's local, non-git `.env` (#296's own documented, deliberate design — never
lose a ping just because the questions thread isn't provisioned yet). That
fallback used to be COMPLETELY INDISTINGUISHABLE from a genuinely-configured
send: both wrote the identical `sent kind=shell key=❓:<project>` line to
notify-delivery.log, and nothing ever attempted to provision the missing
thread. Live-confirmed: the gatekeeper box has sent dozens of ❓ pings this
way, every one landing in the normal `claude-zbynek` thread, with zero trace
anywhere that it was a fallback rather than the real thing.

`notify.resolve_questions_channel()` wraps the existing, UNCHANGED
`notification_channel(kind="questions")` (the fast, already-configured path
stays byte-for-byte identical — zero new network calls, zero new log lines).
Only when the owner's `_Q` key is genuinely absent does it (1) write a LOUD,
distinguishable log line (`status="fallback"`) and (2) kick a GUARDED,
DETACHED background attempt to provision the thread — mirroring
`statusbar._spawn_refresh`'s own marker-mtime guard shape verbatim — so the
NEXT ❓ for that owner on that box self-heals with no manual per-box step.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                             # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AIRULESET = ROOT / "airuleset.py"


class _HomeIsolated(unittest.TestCase):
    """Every test here points `notify._claude_dir()` (== expanduser('~')/
    .claude, via $HOME) at a scratch dir — the live api-watchdog executes
    this repo's working tree every 60s on this box, so an un-isolated test
    races production state (real delivery log, real spawn-guard markers)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-qchan-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._env))

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]


class TestResolveQuestionsChannel(_HomeIsolated):

    def test_configured_q_thread_is_used_unchanged_no_log_no_spawn(self):
        # The fast, already-provisioned path (dev1's own real config shape)
        # must stay byte-for-byte the pre-fix behaviour: same return value,
        # NO fallback log line, spawn NEVER called.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111",
               "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q": "222"}
        calls = []
        chan = notify.resolve_questions_channel(
            env=env, owner="zbynek", spawn=lambda o: calls.append(o))
        self.assertEqual(chan, "222")
        self.assertEqual(calls, [], "a genuinely-configured owner must never spawn")
        self.assertEqual(self.log_lines(), [],
                         "a genuinely-configured owner must never log a fallback")

    def test_missing_q_thread_falls_back_to_the_normal_thread(self):
        # The RETURNED channel is unchanged from notification_channel's own
        # existing, deliberate fallback cascade -- this ticket does not touch
        # WHICH channel a fallback delivery uses, only whether it is silent.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        chan = notify.resolve_questions_channel(
            env=env, owner="zbynek", spawn=lambda o: None)
        self.assertEqual(chan, "111")

    def test_missing_q_thread_logs_the_fallback_loudly(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        notify.resolve_questions_channel(env=env, owner="zbynek",
                                         spawn=lambda o: None)
        lines = self.log_lines()
        self.assertTrue(lines, "a silent fallback is exactly the bug #330 reports")
        self.assertTrue(
            any("fallback" in ln and "zbynek" in ln
                and "q-thread-not-provisioned" in ln for ln in lines),
            lines)

    def test_missing_q_thread_spawns_a_provision_attempt(self):
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        calls = []
        notify.resolve_questions_channel(env=env, owner="zbynek",
                                         spawn=lambda o: calls.append(o))
        self.assertEqual(calls, ["zbynek"])

    def test_no_owner_never_logs_or_spawns(self):
        # notification_channel() itself falls back to the shared channel
        # when the owner can't be resolved at all -- there is no PER-OWNER
        # fact to log or self-heal in that case.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ID": "999"}
        calls = []
        chan = notify.resolve_questions_channel(
            env=env, owner="", spawn=lambda o: calls.append(o))
        self.assertEqual(chan, "999")
        self.assertEqual(calls, [])
        self.assertEqual(self.log_lines(), [])

    def test_default_spawn_is_the_real_provisioner(self):
        # Calling with NO spawn= at all must resolve the module-level
        # default -- proving the wiring, not just the injected-callable shape.
        env = {"DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "111"}
        with m.patch.object(notify, "_spawn_provision_question_thread") as fake:
            notify.resolve_questions_channel(env=env, owner="zbynek")
        fake.assert_called_once_with("zbynek")


class TestSpawnProvisionQuestionThreadGuard(_HomeIsolated):
    """Mirrors statusbar._spawn_refresh's own spawn-guard test verbatim (a
    burst of ❓ deliveries for the same not-yet-provisioned owner must spawn
    at most one background attempt per guard window)."""

    def test_spawn_guard_marker_throttles(self):
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_question_thread("zbynek")
            notify._spawn_provision_question_thread("zbynek")
        self.assertEqual(len(calls), 1,
                         "second spawn within the guard window must be skipped")

    def test_different_owners_are_not_throttled_by_each_other(self):
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_question_thread("zbynek")
            notify._spawn_provision_question_thread("marek")
        self.assertEqual(len(calls), 2)

    def test_spawn_calls_provision_question_thread_for_the_owner(self):
        calls = []
        with m.patch.object(notify.subprocess, "Popen",
                            lambda *a, **k: calls.append(a)):
            notify._spawn_provision_question_thread("zbynek")
        self.assertEqual(len(calls), 1)
        argv = calls[0][0]
        self.assertIn("--provision-question-thread", argv)
        self.assertIn("zbynek", argv)


class TestChannelIdCliWiresTheFallback(_HomeIsolated):
    """`airuleset.py notify --channel-id --kind questions` is the ONE real
    call site the ❓ delivery hook uses (hooks/notify-discord-send.sh's
    emit_one()) -- proves the WIRING end to end via subprocess, not just the
    function in isolation."""

    def _write_env(self, q=False):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["DISCORD_BOT_TOKEN=xxtokenxx",
                 "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK=111"]
        if q:
            lines.append("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=222")
        (d / ".env").write_text("\n".join(lines) + "\n")

    def _run(self):
        env = {**os.environ, "HOME": str(self.home),
               "AIRULESET_NOTIFY_OWNER": "zbynek"}
        return subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--channel-id",
             "--kind", "questions"],
            capture_output=True, text=True, env=env)

    def test_configured_prints_the_q_channel_no_fallback_log(self):
        self._write_env(q=True)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "222")
        self.assertEqual(self.log_lines(), [])

    def test_unconfigured_falls_back_and_logs(self):
        self._write_env(q=False)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "111")
        lines = self.log_lines()
        self.assertTrue(
            any("fallback" in ln and "q-thread-not-provisioned" in ln
                for ln in lines), lines)


if __name__ == "__main__":
    unittest.main()
