"""#687 — cross-session (cross-USER) content dedup for the ✅ device ping.

Four david sessions (david1–4 — SEPARATE unix accounts on subdev) delivered the
IDENTICAL ✅ payload ("bounce #4736 vyriešený…") as four separate Discord
messages. ❓ questions have per-session dedup; ✅ reports go the SHELL send path
(`hooks/notify-discord-send.sh`, transport `kind=shell`) which never routed
through `notify.send()`, so nothing coalesced an identical fleet-wide ✅ across
sessions. Fix: `notify.content_dedup_claim` — a cross-user O_EXCL claim in a
shared sticky /tmp store, keyed on owner+project+normalized-text+time-bucket —
wired into the ✅ branch of the shell hook. The first sender claims (delivers);
an identical payload within the window is "dup" (suppressed + logged `deduped`,
never a silent drop, #135).

RED (`test_two_identical_check_marks_deliver_once`) drives the EXISTING send hook
twice — behavioral (on the pre-#687 tree BOTH deliver, no `deduped` line), never
a bare AttributeError (#181). The `deduped` log line is written SYNCHRONOUSLY in
the dedup gate (before the fire-and-forget curl), so the assertion is
deterministic without polling.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
SEND_HOOK = ROOT / "hooks" / "notify-discord-send.sh"


def _fake_curl_dir(counter):
    """A bin dir (prepended to PATH) whose `curl` appends one line to `counter`
    per invocation and answers the `-w '\\n%{http_code}'` shape with a 200."""
    d = Path(tempfile.mkdtemp(prefix="airuleset-cd687curl-"))
    fake = d / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "echo x >> %s\n"
        "printf '%%s\\n%%s' '{\"id\":\"999\"}' '200'\n" % counter)
    fake.chmod(0o755)
    return d


class _Harness(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-cd687-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        d = self.home / ".claude" / "channels" / "discord"
        d.mkdir(parents=True, exist_ok=True)
        (d / ".env").write_text(
            "DISCORD_BOT_TOKEN=xxtokenxx\n"
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")
        # Isolated shared content-dedup store (never the real box store).
        self.store = Path(tempfile.mkdtemp(prefix="airuleset-cd687store-"))
        self.addCleanup(shutil.rmtree, self.store, True)
        self.counter = self.home / "curlcount"
        self.counter.write_text("")
        self.curldir = _fake_curl_dir(str(self.counter))
        self.addCleanup(shutil.rmtree, self.curldir, True)

    @property
    def log(self):
        return self.home / ".claude" / "notify-delivery.log"

    def log_lines(self):
        if not self.log.exists():
            return []
        return [ln for ln in self.log.read_text().splitlines() if ln.strip()]

    def _send(self, emoji="✅", text="bounce 4736 vyriešený a odovzdaný gk",
              cwd=None):
        env = {**os.environ, "HOME": str(self.home),
               "ND_EMOJI": emoji, "ND_TEXT": text,
               "ND_CWD": str(cwd or ROOT),
               "AIRULESET_CONTENT_DEDUP_DIR": str(self.store),
               "PATH": str(self.curldir) + os.pathsep + os.environ.get("PATH", "")}
        env.pop("DISCORD_NOTIFY_DRYRUN", None)
        env.pop("ND_DRYRUN_FILE", None)
        return subprocess.run(["bash", str(SEND_HOOK)], input="",
                              capture_output=True, text=True, env=env)

    def _curl_count(self, want, timeout=4.0):
        # the ✅ path backgrounds curl → poll the counter until it stabilises.
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            n = len([x for x in self.counter.read_text().splitlines() if x])
            if n == last and n >= want:
                return n
            last = n
            time.sleep(0.1)
        return len([x for x in self.counter.read_text().splitlines() if x])


class TestContentDedup(_Harness):
    def test_two_identical_check_marks_deliver_once(self):
        self._send()          # session 1 — claims + delivers
        r2 = self._send()     # session 2 — identical payload → must dedup
        self.assertEqual(r2.returncode, 0)
        deduped = [ln for ln in self.log_lines() if "deduped" in ln]
        self.assertEqual(
            len(deduped), 1,
            "the 2nd identical ✅ must be deduped (one `deduped` log line): %r"
            % self.log_lines())
        self.assertEqual(self._curl_count(1), 1,
                         "only the FIRST identical ✅ must reach Discord")


if __name__ == "__main__":
    unittest.main()
