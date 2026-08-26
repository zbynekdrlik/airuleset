"""#135 — a delivery that never happened must never be silent.

The failure: `emit_one()` in `hooks/notify-discord-send.sh` sets
`DELIVERY_FAILED=1` and returns with NO log line and nothing on stderr when
the bot token, the channel id, or `jq` is missing — and that is the path the
idle `✅` ping uses. The Python half is the same shape: `notify.send()`
returns `no-config` / `error` as a string nobody must consume, and
`_notify_run_card` prints it into a stdout that is `/dev/null` in the
detached spawn, inside a blanket `except Exception: pass`.

So nobody could tell "sent and Discord rejected it" from "never sent" from
"there was nothing to send" — which is exactly how #134 went unnoticed for
five days.

Three locks here:

  1. every non-delivery on EITHER path writes one line, with a reason, to one
     durable log (`~/.claude/notify-delivery.log`);
  2. the dedup marker records the DELIVERY OUTCOME, not merely the claim —
     `_dedup_claim` writes the marker BEFORE the POST, so marker presence
     alone proves nothing, and #134's gate/backstop/suppression all key on
     this;
  3. `_notify_run_card` stops swallowing: a genuine delivery failure reaches
     stderr and a non-zero exit, so the worker's own Bash call sees it.

Per #124: an ALLOWED (successful) path must also leave stderr EMPTY — a hook
that pollutes stderr while exiting 0 is otherwise invisible.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEND_HOOK = ROOT / "hooks" / "notify-discord-send.sh"
AIRULESET = ROOT / "airuleset.py"


class _HomeIsolated(unittest.TestCase):
    """Every test here writes into `$HOME/.claude` — never the real one (the
    live api-watchdog executes this repo's working tree every 60s on this
    box, so an un-isolated test races production)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-notifylog-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        # #687: isolate the shared ✅ content-dedup store PER TEST — these tests
        # send the IDENTICAL ✅ payload ("hotovo") across methods, which the new
        # cross-session dedup would coalesce under a shared store. Per-test dir
        # under self.home; works under BOTH pytest and `unittest discover` (the
        # push gate never reads conftest.py's own isolation fixture).
        self.cdedup = self.home / "content-dedup"

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]


def _path_with_fake_curl(http_code="200"):
    """A bin dir with a fake `curl` (prepended to the real PATH so `jq` and
    everything else stay resolvable) that always answers with a canned
    `<body>\\n<code>` — the exact shape both the confirm and the
    fire-and-forget branches parse via `curl -w '\\n%{http_code}'`. Used to
    prove #184's success logging without ever touching the real network."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-fakecurl-"))
    fake = d / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%%s\\n%%s' '{\"id\":\"999\"}' '%s'\n" % http_code)
    fake.chmod(0o755)
    return str(d) + os.pathsep + os.environ.get("PATH", "")


def _path_without(tool):
    """A single bin dir mirroring the real PATH minus one tool. Symlinks are
    used so every mirrored program keeps working; only `tool` is absent, so
    `command -v <tool>` genuinely fails while the rest of the hook runs."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-nopath-"))
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or not os.path.isdir(entry):
            continue
        for name in os.listdir(entry):
            if name == tool:
                continue
            dst = d / name
            if dst.exists() or dst.is_symlink():
                continue
            os.symlink(os.path.join(entry, name), dst)
    return d


# --------------------------------------------------------------------------- #
# 1. the SHELL path (#135's own report)
# --------------------------------------------------------------------------- #

class TestShellSendLogsEveryNonDelivery(_HomeIsolated):

    def _run(self, path=None, emoji="✅", text="hotovo"):
        env = {**os.environ, "HOME": str(self.home),
               "ND_EMOJI": emoji, "ND_TEXT": text, "ND_CWD": str(ROOT),
               "AIRULESET_CONTENT_DEDUP_DIR": str(self.cdedup)}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        if path is not None:
            env["PATH"] = path
        return subprocess.run(["bash", str(SEND_HOOK)], input="",
                              capture_output=True, text=True, env=env)

    def _write_env(self, token=True, channel=True):
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        lines = []
        if token:
            lines.append("DISCORD_BOT_TOKEN=xxtokenxx")
        if channel:
            lines.append("DISCORD_NOTIFICATION_CHANNEL_ID=123456789")
        (d / ".env").write_text("\n".join(lines) + "\n")

    def test_missing_token_is_logged_with_a_reason(self):
        self._write_env(token=False, channel=True)
        r = self._run()
        self.assertEqual(r.returncode, 0)
        lines = self.log_lines()
        self.assertTrue(lines, "a non-delivery must write one durable line")
        self.assertTrue(any("no-token" in ln for ln in lines), lines)

    def test_missing_channel_is_logged_with_a_reason(self):
        self._write_env(token=True, channel=False)
        r = self._run()
        self.assertEqual(r.returncode, 0)
        self.assertTrue(any("no-channel" in ln for ln in self.log_lines()),
                        self.log_lines())

    def test_missing_jq_is_logged_with_a_reason(self):
        self._write_env()
        d = _path_without("jq")
        self.addCleanup(shutil.rmtree, d, True)
        r = self._run(path=str(d))
        self.assertEqual(r.returncode, 0)
        self.assertTrue(any("no-jq" in ln for ln in self.log_lines()),
                        self.log_lines())

    def test_dry_run_is_not_a_failure_and_logs_nothing(self):
        self._write_env()
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "✅",
               "ND_TEXT": "hotovo", "ND_CWD": str(ROOT),
               "DISCORD_NOTIFY_DRYRUN": "1"}
        r = subprocess.run(["bash", str(SEND_HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), "",
                         "an allowed path must leave stderr empty (#124)")
        self.assertEqual(self.log_lines(), [],
                         "a dry-run delivered nothing and failed nothing")

    def test_log_line_carries_a_timestamp_and_the_kind(self):
        self._write_env(token=False)
        self._run()
        line = self.log_lines()[-1]
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T")
        self.assertIn("kind=", line)

    def test_a_confirmed_successful_delivery_is_ALSO_logged(self):
        # #184: the CONFIRM (❓) path only ever logged a NON-delivery — a
        # card that genuinely reached Discord left zero trace, so a healthy
        # box and a broken logger both showed an empty file.
        self._write_env(token=True, channel=True)
        d = _path_with_fake_curl("200")
        self.addCleanup(shutil.rmtree, d.split(os.pathsep)[0], True)
        # #710: pin a NON-suppressed owner so the ❓ delivery path actually runs
        # (a bare box resolves owner=zbynek, whose question delivery is now OFF;
        # empty owner is never suppressed and falls to the shared channel).
        env = {**os.environ, "HOME": str(self.home), "ND_EMOJI": "❓",
               "ND_TEXT": "otazka", "ND_CWD": str(ROOT), "ND_CONFIRM": "1",
               "AIRULESET_NOTIFY_OWNER": "", "PATH": d}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        r = subprocess.run(["bash", str(SEND_HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(any("sent" in ln for ln in self.log_lines()),
                        self.log_lines())

    def test_a_backgrounded_successful_delivery_is_ALSO_logged(self):
        # The idle ✅ path (no ND_CONFIRM) posts via a fire-and-forget
        # backgrounded curl — before this it NEVER checked its own result,
        # success or failure. It now captures the HTTP code and logs the
        # outcome from WITHIN the same background subshell, so the parent
        # script stays exactly as non-blocking as before; the log line lands
        # slightly after this process returns, so poll for it (bounded).
        self._write_env(token=True, channel=True)
        d = _path_with_fake_curl("200")
        self.addCleanup(shutil.rmtree, d.split(os.pathsep)[0], True)
        r = self._run(path=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        deadline = time.time() + 5
        lines = []
        while time.time() < deadline:
            lines = self.log_lines()
            if any("sent" in ln for ln in lines):
                break
            time.sleep(0.1)
        self.assertTrue(any("sent" in ln for ln in lines), lines)


# --------------------------------------------------------------------------- #
# 2. the marker records DELIVERY, not merely the claim
# --------------------------------------------------------------------------- #

class TestDedupMarkerRecordsDeliveryOutcome(_HomeIsolated):

    def setUp(self):
        super().setUp()
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._env))

    def _marker(self, key):
        return self.home / ".claude" / "autopilot-notify-sent" / key

    def _post(self, ok):
        orig = notify._post_discord
        notify._post_discord = lambda *a, **k: ok
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))

    def test_a_delivered_send_marks_the_marker_sent(self):
        self._post(True)
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        st = notify.send("body", env=env, owner="", dedup_key="repo#7")
        self.assertEqual(st, "sent")
        self.assertTrue(notify.marker_delivered("repo#7"),
                        "a delivered card's marker must READ as delivered")

    def test_a_failed_post_leaves_the_marker_not_delivered(self):
        self._post(False)
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        st = notify.send("body", env=env, owner="", dedup_key="repo#8")
        self.assertEqual(st, "error")
        self.assertTrue(self._marker("repo#8").exists(),
                        "the claim is kept so a timeout cannot double-send")
        self.assertFalse(notify.marker_delivered("repo#8"),
                         "a CLAIM is not a DELIVERY — this is the whole point")

    def test_a_failed_post_is_logged(self):
        self._post(False)
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        notify.send("body", env=env, owner="", dedup_key="repo#9")
        self.assertTrue(any("error" in ln and "repo#9" in ln
                            for ln in self.log_lines()), self.log_lines())

    def test_no_config_is_logged(self):
        notify.send("body", env={}, owner="", dedup_key="repo#10")
        self.assertTrue(any("no-config" in ln for ln in self.log_lines()),
                        self.log_lines())

    def test_a_successful_send_is_ALSO_logged(self):
        # #184: before this, log_delivery was called ONLY on a non-delivery
        # — so a card that genuinely reached Discord left zero trace, and
        # "zero lines, ever" was indistinguishable from "the logger is
        # broken". Every delivery ATTEMPT, success included, now writes one
        # line, so the file's own absence becomes a diagnosable state.
        self._post(True)
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        st = notify.send("body", env=env, owner="", dedup_key="repo#184a")
        self.assertEqual(st, "sent")
        self.assertTrue(any("sent" in ln and "repo#184a" in ln
                            for ln in self.log_lines()), self.log_lines())

    def test_a_dedup_skip_is_ALSO_logged(self):
        self._post(True)
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        notify.send("body", env=env, owner="", dedup_key="repo#184b")
        notify.send("body", env=env, owner="", dedup_key="repo#184b")
        self.assertTrue(any("dedup" in ln and "repo#184b" in ln
                            for ln in self.log_lines()), self.log_lines())

    def test_a_log_line_is_always_exactly_one_line(self):
        # Found live, not hypothesised: a test fake returned the whole
        # multi-line card BODY as its status, which landed verbatim in the
        # real log and made it ungreppable. Every field is squeezed + capped.
        notify.log_delivery("a\nb\nc", kind="k\nk", key="x\ny",
                            reason="r" * 500)
        self.assertEqual(len(self.log_lines()), 1, self.log_lines())
        self.assertLess(len(self.log_lines()[0]), 400)

    def test_marker_delivered_is_false_for_an_absent_marker(self):
        self.assertFalse(notify.marker_delivered("repo#404"))

    def test_legacy_markers_without_a_status_read_as_delivered(self):
        # markers written before this change carry only a timestamp. Reading
        # them as UNDELIVERED would make every pre-existing card look failed
        # and flood the user with false alerts on the first deploy.
        d = self.home / ".claude" / "autopilot-notify-sent"
        d.mkdir(parents=True, exist_ok=True)
        (d / "repo#11").write_text("1785242877.4970248")
        self.assertTrue(notify.marker_delivered("repo#11"))


# --------------------------------------------------------------------------- #
# 3. `_notify_run_card` stops swallowing
# --------------------------------------------------------------------------- #

class TestRunCardSurfacesFailure(_HomeIsolated):

    def _run_card(self, extra=()):
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(
            [sys.executable, str(AIRULESET), "notify", "--run-card",
             "--repo", "o/r", "--issue", "5", "--goal", "g",
             "--achieved", "a", *extra],
            capture_output=True, text=True, env=env)

    def test_a_card_that_cannot_be_delivered_exits_non_zero(self):
        # no .env in the isolated HOME -> `no-config` -> the worker must SEE it
        r = self._run_card()
        self.assertNotEqual(r.returncode, 0,
                            "a card that never reached Discord must not "
                            "report success")
        self.assertIn("no-config", (r.stderr + r.stdout))

    def test_the_failure_reaches_stderr(self):
        r = self._run_card()
        self.assertTrue(r.stderr.strip(),
                        "the reason must reach stderr, not a /dev/null stdout")

    def test_the_failure_is_logged_durably(self):
        self._run_card()
        self.assertTrue(any("run-card" in ln for ln in self.log_lines()),
                        self.log_lines())

    def test_dry_run_still_exits_zero_and_says_nothing_on_stderr(self):
        r = self._run_card(extra=("--dry-run",))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr.strip(), "")


class TestNoBlanketSwallowLeftInRunCard(unittest.TestCase):
    """The literal shape the ticket names. Matched as a STATEMENT PAIR inside
    `_notify_run_card`'s own source so this assertion cannot match its own
    prose (the repo's "a lock test will match its own prose" rule)."""

    def test_run_card_has_no_bare_except_pass(self):
        import inspect

        import airuleset
        src = inspect.getsource(airuleset._notify_run_card)
        rows = src.splitlines()
        offenders = [
            i + 1 for i, (a, b) in enumerate(zip(rows, rows[1:]))
            if re.match(r"^\s*except Exception\s*:\s*$", a)
            and re.match(r"^\s*pass\s*$", b)]
        self.assertEqual(offenders, [],
                         "a swallowed card is an invisible card (#135)")


if __name__ == "__main__":
    unittest.main()
