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

import notify                                             # noqa: E402

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

    def test_question_ping_is_never_deduped(self):
        # #687 gate is ✅-only — the ❓ flow (its own per-session dedup) must be
        # untouched: two identical ❓ payloads must NOT produce a `deduped` line.
        self._send(emoji="❓", text="rovnaká otázka")
        self._send(emoji="❓", text="rovnaká otázka")
        self.assertEqual(
            [ln for ln in self.log_lines() if "deduped" in ln], [],
            "the ❓ flow must never be content-deduped by #687")


class TestContentDedupClaim(unittest.TestCase):
    """The pure claim function — cross-user semantics, fail-open, no false dups."""

    def setUp(self):
        self.store = Path(tempfile.mkdtemp(prefix="airuleset-cdclaim-"))
        self.addCleanup(shutil.rmtree, self.store, True)
        import notify
        self.notify = notify

    def _claim(self, text="hotovo", owner="david", project="odoo-erp",
               now=1_000_000.0, window_s=120):
        return self.notify.content_dedup_claim(
            text, owner=owner, project=project, now=now, window_s=window_s,
            store_dir=str(self.store))

    def test_first_claims_second_dups(self):
        self.assertEqual(self._claim(), "claim")
        self.assertEqual(self._claim(), "dup")

    def test_different_text_both_claim(self):
        self.assertEqual(self._claim(text="a"), "claim")
        self.assertEqual(self._claim(text="b"), "claim")

    def test_different_owner_both_claim(self):
        self.assertEqual(self._claim(owner="david"), "claim")
        self.assertEqual(self._claim(owner="montalu"), "claim")

    def test_different_project_both_claim(self):
        self.assertEqual(self._claim(project="odoo-erp"), "claim")
        self.assertEqual(self._claim(project="restreamer"), "claim")

    def test_next_window_claims_again(self):
        self.assertEqual(self._claim(now=1_000_000.0), "claim")
        self.assertEqual(self._claim(now=1_000_000.0 + 500), "claim")

    def test_boundary_straddling_claims_dedup(self):
        # #832: two identical payloads 1 s apart that STRADDLE a 120-s bucket
        # edge (int(now // 120) increments between them) must still dedup — the
        # pre-fix code keyed the bucket into the claim FILENAME, so the two
        # sends landed in ADJACENT files and BOTH delivered. Injected clock
        # (a future bucket boundary), never a wall-clock sleep, so it is
        # deterministic regardless of when the suite runs.
        edge = 20_000_000 * 120        # exactly on a bucket boundary (~2046)
        self.assertEqual(self._claim(now=edge - 0.5), "claim")
        self.assertEqual(self._claim(now=edge + 0.5), "dup")

    def test_adjacent_bucket_beyond_window_both_claim(self):
        # #832 control: two identical payloads in ADJACENT buckets but MORE than
        # window_s apart must BOTH claim — the sliding window is BOUNDED at
        # window_s, not silently widened toward 2×window_s (the rejected
        # existence-only probe would over-suppress here). This also proves the
        # marker's mtime is stamped with the injected `now` (else the far-future
        # claim time vs the real file-creation mtime would misjudge the age).
        edge = 20_000_000 * 120
        self.assertEqual(self._claim(now=edge - 0.5), "claim")
        self.assertEqual(self._claim(now=edge + 119.9), "claim")

    def test_whitespace_and_case_normalized_to_same_key(self):
        self.assertEqual(self._claim(text="Bounce  4736   done"), "claim")
        self.assertEqual(self._claim(text="bounce 4736 done"), "dup")

    def test_fail_open_on_unusable_store(self):
        # a store path that cannot be created (a FILE where the dir should be) →
        # fail OPEN (deliver), never a spurious "dup".
        blocker = self.store / "afile"
        blocker.write_text("x")
        r = self.notify.content_dedup_claim(
            "t", owner="d", project="p", now=1.0, window_s=120,
            store_dir=str(blocker / "sub"))   # can't mkdir under a file
        self.assertEqual(r, "claim", "an unusable store must fail OPEN (deliver)")

    def test_store_is_world_writable_and_sticky(self):
        self._claim()
        mode = os.stat(self.store).st_mode & 0o7777
        self.assertEqual(mode & 0o1000, 0o1000, "store must be sticky")
        self.assertEqual(mode & 0o0002, 0o0002, "store must be world-writable")


class TestDedupKeyUsesUnqualifiedRepo(unittest.TestCase):
    """#687 review 🔴: the dedup key's project component MUST be the UNQUALIFIED
    origin repo name, never the stream-qualified `$PROJECT` label — else the four
    david1–4 accounts (odoo-erp-david2 vs -david3) never share a key and the
    cross-account incident this fixes is uncoalescable. Content-lock on the shell
    gate (the collision is cross-UNIX-USER, not cheaply reproducible in-process).
    Owner is already shared (resolve_owner redirects david1–4 → david)."""

    def _dedup_gate(self):
        src = SEND_HOOK.read_text()
        i = src.index("--content-dedup-claim")
        start = src.rfind("if [ \"$EMOJI\" = \"✅\" ]", 0, i)
        return src[start:i + 400]

    def test_dedup_gate_keys_on_the_unqualified_repo_name(self):
        gate = self._dedup_gate()
        self.assertIn("--repo-name", gate,
                      "the dedup gate must resolve the UNQUALIFIED repo name")
        self.assertIn('--project "$DEDUP_REPO"', gate,
                      "content-dedup-claim must be given the unqualified repo, "
                      "not the stream-qualified $PROJECT label")
        self.assertNotIn('--project "$PROJECT"', gate,
                         "must NOT pass the stream-qualified $PROJECT to the claim")

    def test_content_dedup_claim_coalesces_same_repo_regardless_of_caller(self):
        # Two calls for the SAME (redirected owner, unqualified repo, text) —
        # what david1-4 produce once the gate passes the unqualified name —
        # MUST coalesce; a stream-qualified project would NOT (the differing-
        # project control proves exactly why the gate must pass the unqualified
        # name).
        store = Path(tempfile.mkdtemp(prefix="airuleset-cd687u-"))
        self.addCleanup(shutil.rmtree, store, True)

        def claim(project):
            return notify.content_dedup_claim(
                "bounce done", owner="david", project=project,
                now=1_000_000.0, window_s=120, store_dir=str(store))
        self.assertEqual((claim("odoo-erp"), claim("odoo-erp")), ("claim", "dup"),
                         "same unqualified repo + owner + text must coalesce")
        self.assertEqual((claim("odoo-erp-david2"), claim("odoo-erp-david3")),
                         ("claim", "claim"),
                         "differing stream-qualified projects do NOT coalesce")


if __name__ == "__main__":
    unittest.main()
