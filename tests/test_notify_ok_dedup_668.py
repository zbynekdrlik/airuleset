"""#668 — the idle ✅ device ping: dedup identical content + a reliable project
label, on BOTH deliverers (the idle hook AND the watchdog job-5 backstop).

Two coupled defects reported live (david1/2@subdev, ticket odoo-erp 5023):

  (b) The SAME `✅ unknown — hotovo` message reached David FOUR TIMES. The ✅
      path had NO content dedup — only the ❓ path (`send_q`/`LASTQ`) did — so a
      session re-reporting the identical `✅ DONE` across several Stop/idle
      cycles (a /goal-loop re-poke of a stream that handed off its last ticket)
      pinged once per cycle. This mirrors the ❓ 9× "rovnaká otázka" spam LASTQ
      dedup fixed.

  (a) The label read "unknown". The Stop hook that RECORDS the ✅ has a reliable
      cwd but discarded it; the idle hook re-derived the project from its OWN
      (possibly empty) event cwd, and the send path decorated an unresolved
      project with a meaningless "unknown" and sent it anyway. The watchdog
      job-5 backstop (the ACTUAL deliverer where idle_prompt is unreliable, i.e.
      on the subdev boxes of the incident) had the SAME two bugs.

These bash-hook + watchdog integration tests drive the real hooks with an
isolated HOME and a uuid session id via the shared #494 helper.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hook_state_cleanup as hsc               # noqa: E402
import watchdog                                 # noqa: E402
from watchdog import sweep_jobs                 # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IDLE = ROOT / "hooks" / "notify-discord.sh"
SEND = ROOT / "hooks" / "notify-discord-send.sh"
STOP = ROOT / "hooks" / "notify-discord-pending.sh"
CLEAR = ROOT / "hooks" / "clear-question-dedup.sh"


def _fake_curl_bin(http="200", calls_file=None):
    """A bin dir whose fake `curl` answers `<body>\\n<code>` AND, when
    `calls_file` is given, records one invocation — so a test can PROVE a send
    actually reached the network layer, not merely that a log line was written
    (review finding: a total drop must not stay green under an anti-drop test)."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-668-"))
    rec = ('printf x >> %s 2>/dev/null || true\n' % calls_file) if calls_file else ""
    (d / "curl").write_text(
        "#!/usr/bin/env bash\n"
        + rec
        + "printf '%%s\\n%%s' '{\"id\":\"999\"}' '%s'\n" % http)
    (d / "curl").chmod(0o755)
    return d


class _Base(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-668-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    def _sid(self):
        # #494 shared helper — a uuid sid (never a recyclable pid) + a precise
        # `*<sid>*` teardown sweep, instead of hand-enumerating /tmp prefixes
        # (the #293 "a future new marker silently reopens the leak" trap).
        return hsc.new_hook_sid(self, "t668")

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
        Path(self._pending(sid)).write_text("✅ hotovo")
        r = self._fire_idle(sid, out, self.home)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._deliveries(out), 1)
        # the SAME ✅ re-recorded (a re-poke), NO user input between → once only
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

    def test_a_suppressed_duplicate_leaves_a_delivery_log_line(self):
        # A traceless dedup drop is the #134/#467 silence class — a real (not
        # dry-run) suppression must write one durable line.
        sid = self._sid()
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")
        cbin = _fake_curl_bin("200")
        self.addCleanup(shutil.rmtree, cbin, True)
        base = {**os.environ, "HOME": str(self.home), "TMUX_PANE": "",
                "AIRULESET_NOTIFY_OWNER": "",
                "PATH": str(cbin) + os.pathsep + os.environ["PATH"]}
        base.pop("DISCORD_NOTIFY_DRYRUN", None)
        base.pop("ND_DRYRUN_FILE", None)
        Path(self._pending(sid)).write_text("✅ hotovo")
        subprocess.run(["bash", str(IDLE)], input=json.dumps({"session_id": sid,
                       "cwd": str(self.home)}), text=True, capture_output=True,
                       env=base)             # cycle 1 delivers + writes LASTOK
        Path(self._pending(sid)).write_text("✅ hotovo")
        subprocess.run(["bash", str(IDLE)], input=json.dumps({"session_id": sid,
                       "cwd": str(self.home)}), text=True, capture_output=True,
                       env=base)             # cycle 2 suppressed
        self.assertIn("suppressed", self._dlog(), self._dlog())
        self.assertIn("duplicate-ok", self._dlog(), self._dlog())


class ProjectLabelNoUnknown(_Base):
    """(a) — an unresolvable project never ships the decorative "unknown"; the
    non-delivery reason is logged LOUD instead, and only outside dry-run."""

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

    def test_dry_run_with_an_unresolved_project_logs_nothing(self):
        # The dry-run-logs-nothing contract must hold even for the new
        # unresolved-project branch (an empty cwd → unresolved).
        out = self.home / "dry.txt"
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "✅",
               "ND_TEXT": "hotovo", "ND_CWD": "",
               "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(out),
               "AIRULESET_NOTIFY_OWNER": ""}
        subprocess.run(["bash", str(SEND)], input="", text=True,
                       capture_output=True, env=env)
        self.assertEqual(self._dlog(), "",
                         "dry-run must write no delivery-log line")

    def test_an_unresolved_project_is_logged_loud_and_still_sent(self):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtok\nDISCORD_NOTIFICATION_CHANNEL_ID=123\n")
        calls = self.home / "curl.calls"
        cbin = _fake_curl_bin("200", calls_file=str(calls))
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
        self.assertIn("unresolved-project", self._dlog(), self._dlog())
        self.assertTrue(calls.exists() and calls.read_text(),
                        "the ✅ must STILL be sent (curl invoked)")


class PendingCarriesCwd(_Base):
    """(a) end-to-end — the Stop hook records the ✅ WITH its reliable cwd, so
    the idle delivery resolves the real project even when the idle event cwd is
    empty. Before the fix the label collapsed to "unknown"."""

    def _repo_with_origin(self, name):
        r = Path(tempfile.mkdtemp(prefix="airuleset-668-repo-"))
        self.addCleanup(shutil.rmtree, r, True)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull}
        subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(r), "remote", "add", "origin",
                        "https://github.com/test/%s.git" % name],
                       check=True, env=env)
        return r

    def test_recorded_cwd_resolves_the_project_when_idle_cwd_is_empty(self):
        sid = self._sid()
        out = self.home / "dry.txt"
        repo = self._repo_with_origin("mytestproj")
        stop_env = {**os.environ, "HOME": str(self.home), "TMUX_PANE": "",
                    "AIRULESET_NOTIFY_OWNER": "", "ND_BLOCK_SETTLE": "0"}
        stop_env.pop("DISCORD_NOTIFY_DRYRUN", None)
        stop_env.pop("ND_DRYRUN_FILE", None)
        stop_payload = json.dumps({
            "session_id": sid, "cwd": str(repo),
            "last_assistant_message": "✅ DONE: hotovo"})
        rs = subprocess.run(["bash", str(STOP)], input=stop_payload, text=True,
                            capture_output=True, env=stop_env)
        self.assertEqual(rs.returncode, 0, rs.stdout + rs.stderr)
        self.assertTrue(os.path.exists(self._pending(sid)),
                        "the ✅ must be recorded to a pending file")
        idle_env = {**os.environ, "HOME": str(self.home),
                    "DISCORD_NOTIFY_DRYRUN": "1", "ND_DRYRUN_FILE": str(out),
                    "TMUX_PANE": "", "AIRULESET_NOTIFY_OWNER": ""}
        idle_payload = json.dumps({"session_id": sid, "cwd": ""})
        ri = subprocess.run(["bash", str(IDLE)], input=idle_payload, text=True,
                            capture_output=True, env=idle_env)
        self.assertEqual(ri.returncode, 0, ri.stdout + ri.stderr)
        content = out.read_text()
        self.assertIn("mytestproj", content,
                      "the recorded cwd must resolve the real project label")
        self.assertNotIn("unknown", content)


class Job5NoUnknown(unittest.TestCase):
    """watchdog.deliver_pending_done (job 5) — the reliable backstop, the ACTUAL
    deliverer on a subdev box where idle_prompt is unreliable — must ALSO resolve
    the real project (never "unknown") and record LASTOK so a cross-path re-poke
    dedups against the idle hook (#668)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="airuleset-668-j5-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.prefix = str(self.tmp / "claude-discord-pending-")
        self.projects = self.tmp / "projects"       # empty → no transcript
        self.projects.mkdir()
        self.sent = []

    def _send(self, msg, owner=None, dedup_key=None, dry_run=False):
        self.sent.append(msg)
        return "sent"

    def _repo(self, name):
        r = self.tmp / ("repo-" + name)
        r.mkdir()
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull}
        subprocess.run(["git", "-C", str(r), "init", "-q", "-b", "main"],
                       check=True, env=env)
        subprocess.run(["git", "-C", str(r), "remote", "add", "origin",
                        "https://github.com/test/%s.git" % name],
                       check=True, env=env)
        return r

    def _write_pending(self, sid, body="✅ hotovo", cwd=None):
        p = Path(self.prefix + sid)
        p.write_text(body)
        os.utime(p, (1000.0, 1000.0))               # old mtime → idle > grace
        if cwd is not None:
            Path(self.prefix + "cwd-" + sid).write_text(str(cwd))

    def _run(self, now=100000.0):
        return watchdog.deliver_pending_done(
            now, self._send, self.projects, done_grace=0, max_stale=10 ** 9,
            pending_prefix=self.prefix, bg_check=lambda cwd: False)

    def test_recorded_cwd_resolves_the_project_not_unknown(self):
        repo = self._repo("mytestproj")
        sid = "j5sid1"
        self._write_pending(sid, cwd=repo)
        self._run()
        self.assertEqual(len(self.sent), 1, self.sent)
        self.assertIn("mytestproj", self.sent[0])
        self.assertNotIn("unknown", self.sent[0])
        lastok = Path(sweep_jobs._lastok_path(sid, self.prefix))
        self.assertTrue(lastok.exists() and lastok.read_text(),
                        "job 5 must record LASTOK for cross-path dedup")

    def test_unresolvable_cwd_ships_no_unknown_label(self):
        sid = "j5sid2"
        self._write_pending(sid, cwd=None)          # no sibling, no transcript
        self._run()
        self.assertEqual(len(self.sent), 1, self.sent)
        self.assertNotIn("unknown", self.sent[0])
        self.assertIn("hotovo", self.sent[0])

    def test_lastok_matches_the_idle_hook_fingerprint(self):
        sid = "j5sid3"
        self._write_pending(sid, body="✅ nasadené v1.2.3", cwd=None)
        self._run()
        expect = hashlib.sha1("✅ nasadené v1.2.3".encode("utf-8")).hexdigest()[:16]
        lastok = Path(sweep_jobs._lastok_path(sid, self.prefix))
        self.assertEqual(lastok.read_text(), expect,
                         "job-5 LASTOK must equal the shell idle hook's own fp")


if __name__ == "__main__":
    unittest.main()
