"""#369 — the WIRING layer: the four call-site families the ticket names
explicitly (run-cards, idle ✅/❓, gk-request/bounce backstop, api-error) must
actually PASS `project=` through to `notify.send()` / `--channel-id`, not just
have the underlying mechanism exist. bounce/gk-request backstop wiring is
covered directly in tests/test_bounce_backstop.py and tests/test_gk_request.py
(their own `send_fn` fakes already capture kwargs); this file covers the
remaining two: `_notify_run_card` and `notify --api-error`, plus the SHELL
hook wiring (`notify-discord-send.sh` / `notify-api-error.sh`).
"""

import json
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

import notify                                              # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AIRULESET = ROOT / "airuleset.py"
SEND_HOOK = ROOT / "hooks" / "notify-discord-send.sh"
API_ERROR_HOOK = ROOT / "hooks" / "notify-api-error.sh"


class _HomeIsolated(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-pwiring-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# 1. _notify_run_card routes via the stream-qualified bare repo name
# --------------------------------------------------------------------------- #

class TestRunCardPassesProject(unittest.TestCase):

    def _args(self, **over):
        base = dict(run_card=True, autopilot_done=False, mention_prefix=False,
                    repo_name=False, newest_card=False, backfill_digest=False,
                    provision_question_thread=False, provision_project_thread=False,
                    project_label=False, record_question=False,
                    edit_question=False, channel_id=False, owner=False,
                    mirror_owners=False, body=None, run=None,
                    repo="zbynekdrlik/odoo-erp", issue=5, pr=None,
                    achieved="a", result=None, goal="g", version="v1",
                    merge_sha=None, url=None, review="ok", handoff=False,
                    dedup_key=None, dry_run=False)
        base.update(over)
        return m.Mock(**base)

    def test_send_receives_the_stream_qualified_project(self):
        import airuleset
        captured = {}

        def fake_send(body, **k):
            captured.update(k)
            return "sent"

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "1"):
            with m.patch("notify.send", side_effect=fake_send):
                with m.patch("getpass.getuser", return_value="david2"):
                    airuleset.cmd_notify(self._args())
        self.assertEqual(captured.get("project"), "odoo-erp-david2")

    def test_record_card_message_uses_the_project_aware_channel(self):
        # The channel `record_card_message` stores must be the SAME one
        # send() actually posted to (#297/#298) -- resolved via the SAME
        # project label, or a later Discord reply/reaction on the card
        # would be looked up against the wrong channel.
        import airuleset
        seen_channel_calls = []

        def fake_channel(*a, **k):
            seen_channel_calls.append(k)
            return "777001"

        def fake_send(body, **k):
            return "sent", "555666777"

        recorded = {}

        def fake_record(message_id, channel, repo, issue, **k):
            recorded["channel"] = channel

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "1"):
            with m.patch("notify.send", side_effect=fake_send):
                with m.patch("notify.notification_channel",
                             side_effect=fake_channel):
                    with m.patch("notify.record_card_message",
                                 side_effect=fake_record):
                        with m.patch("getpass.getuser", return_value="david2"):
                            airuleset.cmd_notify(self._args())
        self.assertEqual(recorded["channel"], "777001")
        self.assertTrue(seen_channel_calls, "notification_channel was never called")
        self.assertEqual(seen_channel_calls[-1].get("project"), "odoo-erp-david2")

    def test_personal_box_project_is_unqualified(self):
        import airuleset
        captured = {}

        def fake_send(body, **k):
            captured.update(k)
            return "sent"

        with m.patch.object(airuleset, "_gh_out",
                            side_effect=lambda *a, **k: "T" if "view" in a else "1"):
            with m.patch("notify.send", side_effect=fake_send):
                with m.patch("getpass.getuser", return_value="newlevel"):
                    airuleset.cmd_notify(self._args(repo="zbynekdrlik/airuleset"))
        self.assertEqual(captured.get("project"), "airuleset")


# --------------------------------------------------------------------------- #
# 2. notify --api-error routes via its EXISTING --project argument
# --------------------------------------------------------------------------- #

class TestApiErrorPassesProject(unittest.TestCase):

    def _args(self, **over):
        base = dict(api_error=True, run_card=False, autopilot_done=False,
                    mention_prefix=False, repo_name=False, newest_card=False,
                    backfill_digest=False, provision_question_thread=False,
                    provision_project_thread=False, project_label=False,
                    record_question=False, edit_question=False,
                    channel_id=False, owner=False, mirror_owners=False,
                    body=None, run=None, dry_run=False, dedup_key=None,
                    session="s1", project="odoo-erp",
                    text="API Error: Server is temporarily limiting requests "
                        "· Rate limited")
        base.update(over)
        return m.Mock(**base)

    def test_real_error_routes_send_with_the_raw_project_arg(self):
        import airuleset
        captured = {}

        def fake_send(body, **k):
            captured.update(k)
            return "sent"

        with m.patch("notify.send", side_effect=fake_send):
            airuleset.cmd_notify(self._args())
        self.assertEqual(captured.get("project"), "odoo-erp")

    def test_normal_text_never_calls_send(self):
        import airuleset
        calls = []
        with m.patch("notify.send", side_effect=lambda *a, **k: calls.append(1)):
            airuleset.cmd_notify(self._args(text="I'll fix the rate limiter."))
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------- #
# 3. hooks/notify-discord-send.sh: project label computed via python,
#    passed to --channel-id ONLY on the default (non-question) kind.
# --------------------------------------------------------------------------- #

def _path_with_fake_curl_capturing_channel(log_path, http_code="200"):
    """A fake curl that also records which channel URL it posted to, so a
    subprocess-level test can prove WHICH thread a message actually landed
    in — the SAME shape as the existing `_path_with_fake_curl` fixture in
    tests/test_notify_delivery_log.py, extended to capture the URL."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl2-"))
    fake = d / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "for a in \"$@\"; do\n"
        "  case \"$a\" in\n"
        "    https://discord.com/*) echo \"$a\" >> '%s' ;;\n"
        "  esac\n"
        "done\n"
        "printf '%%s\\n%%s' '{\"id\":\"999\"}' '%s'\n" % (log_path, http_code))
    fake.chmod(0o755)
    return str(d) + os.pathsep + os.environ.get("PATH", "")


class TestSendHookProjectRouting(_HomeIsolated):

    def _write_env(self, project_channel=None, q_channel=None):
        # #369 review m7 (TRIGGERED): the project env KEY must be derived
        # via the SAME functions the hook itself calls
        # (`project_label_for`/`_owner_project_key`) rather than the
        # hardcoded literal "AIRULESET" — that literal is only correct
        # when the box's REAL unix user is newlevel/root (stream_qualified
        # leaves it unqualified); on a stream box (marek/david/...) running
        # this suite, `project_label_for(ROOT)` returns "airuleset-<user>"
        # and the hardcoded key would never match, silently falling
        # through to the shared channel and looking like project routing
        # was broken.
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["DISCORD_BOT_TOKEN=xxtokenxx",
                "DISCORD_NOTIFICATION_CHANNEL_ID=100"]
        label = notify.project_label_for(str(ROOT))
        if project_channel:
            lines.append("%s=%s" % (notify._owner_project_key("zbynek", label),
                                    project_channel))
        if q_channel:
            lines.append("DISCORD_NOTIFICATION_CHANNEL_ZBYNEK_Q=%s" % q_channel)
        (d / ".env").write_text("\n".join(lines) + "\n")

    def _run(self, emoji, cwd, curl_path):
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": emoji,
              "ND_TEXT": "hotovo", "ND_CWD": cwd,
              "AIRULESET_NOTIFY_OWNER": "zbynek", "PATH": curl_path}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        return subprocess.run(["bash", str(SEND_HOOK)], input="",
                              capture_output=True, text=True, env=env)

    def test_default_kind_routes_to_the_configured_project_thread(self):
        self._write_env(project_channel="333")
        urls_log = self.home / "urls.log"
        curl_path = _path_with_fake_curl_capturing_channel(str(urls_log))
        self.addCleanup(shutil.rmtree, curl_path.split(os.pathsep)[0], True)
        r = self._run("✅", str(ROOT), curl_path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        urls = urls_log.read_text() if urls_log.exists() else ""
        self.assertIn("/channels/333/messages", urls, urls)

    def test_questions_kind_never_reads_the_project_channel(self):
        # A ❓ ping must land in the QUESTIONS thread even though a project
        # thread is ALSO configured -- #369's own design decision.
        self._write_env(project_channel="333", q_channel="222")
        urls_log = self.home / "urls.log"
        curl_path = _path_with_fake_curl_capturing_channel(str(urls_log))
        self.addCleanup(shutil.rmtree, curl_path.split(os.pathsep)[0], True)
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "❓",
              "ND_TEXT": "otazka", "ND_CWD": str(ROOT), "ND_CONFIRM": "1",
              "AIRULESET_NOTIFY_OWNER": "zbynek", "PATH": curl_path}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        r = subprocess.run(["bash", str(SEND_HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        urls = urls_log.read_text() if urls_log.exists() else ""
        self.assertIn("/channels/222/messages", urls, urls)
        self.assertNotIn("/channels/333/messages", urls, urls)

    def test_project_label_falls_back_to_shared_when_unconfigured(self):
        self._write_env()   # no project thread configured
        urls_log = self.home / "urls.log"
        curl_path = _path_with_fake_curl_capturing_channel(str(urls_log))
        self.addCleanup(shutil.rmtree, curl_path.split(os.pathsep)[0], True)
        r = self._run("✅", str(ROOT), curl_path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        urls = urls_log.read_text() if urls_log.exists() else ""
        self.assertIn("/channels/100/messages", urls, urls)   # shared fallback


# --------------------------------------------------------------------------- #
# 4. hooks/notify-api-error.sh: project computed via python (canonical),
#    falling back to basename — never a checkout-dir-name mismatch with the
#    run-card / idle-ping label for the SAME repo.
# --------------------------------------------------------------------------- #

class TestApiErrorHookProjectLabel(unittest.TestCase):

    def test_hook_calls_the_project_label_cli_mode(self):
        src = API_ERROR_HOOK.read_text()
        self.assertIn("--project-label", src)

    def test_hook_still_falls_back_when_python_yields_nothing(self):
        # A basename-shaped fallback must survive even if the python call
        # fails/returns empty -- PROJECT must never end up unset.
        src = API_ERROR_HOOK.read_text()
        self.assertIn("basename", src)

    def _run_with_fake_python(self, cwd, log_path, project_label_ok=True):
        """A fake `python3` earlier on PATH: a `notify --project-label`
        invocation is faithfully DELEGATED to the REAL python3 (so the
        canonical value is genuine) unless `project_label_ok` is False (in
        which case it fails, forcing the hook's own basename fallback); a
        `notify --api-error` invocation is never actually run (no network,
        no real send) -- its full argv is logged instead, so the test can
        assert on the EXACT --project value the hook passed. Anything else
        delegates to the real python3 untouched (#369 review m6 -- the
        pre-existing tests only asserted the hook's SOURCE mentions
        `--project-label`/`basename`, which a mutant deleting the whole
        PROJECT-computation block still satisfied via the surrounding
        comments)."""
        real_python3 = shutil.which("python3")

        def q(s):
            # single-quote a value for literal bash embedding
            return "'" + str(s).replace("'", "'\\''") + "'"

        project_label_branch = (
            "exec %s \"$@\"" % q(real_python3) if project_label_ok
            else "exit 1")
        d = Path(tempfile.mkdtemp(prefix="airuleset-fakepy3-"))
        self.addCleanup(shutil.rmtree, d, True)
        fake = d / "python3"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if printf '%s\\n' \"$*\" | grep -q -- '--api-error'; then\n"
            "    printf '%s\\n' \"$*\" >> " + q(log_path) + "\n"
            "    exit 0\n"
            "fi\n"
            "if printf '%s\\n' \"$*\" | grep -q -- '--project-label'; then\n"
            "    " + project_label_branch + "\n"
            "fi\n"
            "exec " + q(real_python3) + " \"$@\"\n")
        fake.chmod(0o755)
        env = {**os.environ, "PATH": str(d) + os.pathsep + os.environ.get("PATH", "")}
        payload = json.dumps({
            "last_assistant_message": "API Error: Server is temporarily "
                                       "limiting requests · Rate limited",
            "session_id": "s1", "cwd": str(cwd)})
        return subprocess.run(["bash", str(API_ERROR_HOOK)], input=payload,
                              capture_output=True, text=True, env=env)

    def test_hook_passes_the_canonical_project_label_to_api_error(self):
        log = Path(tempfile.mkdtemp(prefix="airuleset-apierr-log-")) / "argv.log"
        self.addCleanup(shutil.rmtree, log.parent, True)
        expected = subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--project-label",
             "--cwd", str(ROOT)], capture_output=True, text=True).stdout.strip()
        self.assertTrue(expected)
        r = self._run_with_fake_python(ROOT, log)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the background `( ... ) &` job may still be finishing when the
        # hook itself has already exited -- poll briefly for its log line.
        deadline = time.time() + 5
        logged = ""
        while time.time() < deadline:
            if log.exists():
                logged = log.read_text()
                if logged.strip():
                    break
            time.sleep(0.1)
        self.assertIn("--project %s" % expected, logged, logged)

    def test_hook_falls_back_to_basename_when_project_label_fails(self):
        log = Path(tempfile.mkdtemp(prefix="airuleset-apierr-log2-")) / "argv.log"
        self.addCleanup(shutil.rmtree, log.parent, True)
        r = self._run_with_fake_python(ROOT, log, project_label_ok=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        deadline = time.time() + 5
        logged = ""
        while time.time() < deadline:
            if log.exists():
                logged = log.read_text()
                if logged.strip():
                    break
            time.sleep(0.1)
        # the fallback recipe (git-toplevel-basename) — computed the SAME
        # way the hook itself computes it, never hardcoded: in a plain
        # checkout that is "airuleset", but from inside a WORKTREE checkout
        # (as THIS test suite may itself be running) `git rev-parse
        # --show-toplevel` resolves to the WORKTREE's own root, so the
        # fallback's basename is the worktree dir name, not "airuleset" —
        # a genuine, real divergence from the canonical --project-label
        # value that PRE-DATES #369 (the fallback recipe was the OLD code's
        # ONLY mechanism, so it was always "wrong" in a worktree; #369
        # merely demoted it to a rarely-hit fallback, per review m4). What
        # this test asserts is narrower and still meaningful: the fallback
        # actually RAN and produced ITS OWN real value — never the literal
        # "unknown" the pre-fallback code used to collapse to.
        expected_fallback = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True).stdout.strip()
        expected_fallback = os.path.basename(expected_fallback.rstrip("/"))
        self.assertIn("--project %s" % expected_fallback, logged, logged)
        self.assertNotIn("--project unknown", logged, logged)


# --------------------------------------------------------------------------- #
# 5. notify --autopilot-done (#369 review M1) also passes project=
# --------------------------------------------------------------------------- #

class TestAutopilotDonePassesProject(unittest.TestCase):

    def _args(self, **over):
        base = dict(run_card=False, autopilot_done=True, mention_prefix=False,
                    repo_name=False, newest_card=False, backfill_digest=False,
                    provision_question_thread=False, provision_project_thread=False,
                    project_label=False, record_question=False,
                    edit_question=False, channel_id=False, owner=False,
                    mirror_owners=False, api_error=False, body=None, run=None,
                    repo="zbynekdrlik/odoo-erp", pr=None, tickets_json="[]",
                    version="v1", merge_sha=None, review="ok",
                    done=None, remaining=None, dedup_key=None, dry_run=False)
        base.update(over)
        return m.Mock(**base)

    def test_send_receives_the_stream_qualified_project(self):
        import airuleset
        captured = {}

        def fake_send(body, **k):
            captured.update(k)
            return "sent"

        with m.patch("notify.send", side_effect=fake_send):
            with m.patch("getpass.getuser", return_value="david2"):
                airuleset.cmd_notify(self._args())
        self.assertEqual(captured.get("project"), "odoo-erp-david2")

    def test_no_repo_means_no_project_guessed(self):
        import airuleset
        captured = {}

        def fake_send(body, **k):
            captured.update(k)
            return "sent"

        with m.patch("notify.send", side_effect=fake_send):
            airuleset.cmd_notify(self._args(repo=None))
        self.assertIsNone(captured.get("project"))


if __name__ == "__main__":
    unittest.main()
