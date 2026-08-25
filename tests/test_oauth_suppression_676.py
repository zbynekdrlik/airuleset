"""#676 — the #662 `oauthblock:` owner alarm is spam; suppress it via #546.

Owner ruling (2026-08-24, verbatim): "ten oauth token revoked moze byt
switchovanim subscriptions ... mna to nezaujima, zbytocne dalsi spam, je to
normalne spravanie sposobene claudy projektom a jeho watchermi!!!". #662 added
an un-suppressed `oauthblock:` Discord alarm for a persistent 401 OAuth-revoke;
the owner rejected it after deployment — a token revoke is NORMAL subscription-
switching behaviour, not an incident. Remove the PING, keep the machine channel:
add `oauthblock:` to the EXISTING #546 owner-suppression list (`send()` gate),
so an oauthblock send POSTs nothing, returns "suppressed", and leaves an
explicit `suppressed` delivery-log line (never a silent drop, #486/#134). The
`continue` auto-resume never routes through `send()`, so suppression at the
chokepoint leaves it untouched.

These lock BOTH halves of the ruling (RED against the pre-#676 tree, where
`oauthblock:` is NOT yet in `SUPPRESSED_ALERT_PREFIXES`):
  (a) an `oauthblock:` send is SUPPRESSED — recognised by the classifier, POSTs
      nothing, returns "suppressed", and is a LOGGED decision (not a silent
      drop);
  (b) `stuckalert:` (the STRUCTURAL-stuck escape valve, #662) is UNTOUCHED —
      still un-suppressed and still delivers (the owner objected ONLY to the
      oauth class).
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402


class _HomeIsolated(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-oauth676-"))
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
            "DISCORD_NOTIFICATION_CHANNEL_ID=123456789\n")


# The live oauthblock keys watchdog job 1 emits (STEP-0 grep of
# `dedup_key="oauthblock:` in watchdog/__init__.py:1566).
OAUTHBLOCK_KEYS = [
    "oauthblock:sid:hash:pane",
    "oauthblock:9a8b:deadbeef:%7",
]


class TestOAuthblockIsSuppressed(_HomeIsolated):
    def test_classifier_recognises_oauthblock_as_suppressed(self):
        for k in OAUTHBLOCK_KEYS:
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be a #546/#676 owner-suppressed class" % k)

    def test_oauthblock_send_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        for k in OAUTHBLOCK_KEYS:
            self.posts.clear()
            r = notify.send("⛔ /login potrebný", dedup_key=k)
            self.assertEqual(r, "suppressed", "%r should be suppressed" % k)
            self.assertEqual(self.posts, [], "%r must POST nothing" % k)

    def test_suppression_is_a_logged_decision_not_silent(self):
        # #486/#134: a suppressed send leaves an explicit delivery-log line — it
        # is a logged DECISION, never a silent drop.
        self._write_env()
        notify.send("body", dedup_key="oauthblock:sid:hash:1")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed oauthblock send must be LOGGED")
        self.assertIn("oauthblock", lines[-1], "the log line names the key")
        self.assertIn("oauth", lines[-1].lower(),
                      "the reason must name the oauth class, not a bare drop")

    def test_return_message_id_shape_is_respected(self):
        self._write_env()
        r = notify.send("body", dedup_key="oauthblock:s:h:1",
                        return_message_id=True)
        self.assertEqual(r, ("suppressed", None))

    def test_dry_run_suppressed_mutates_nothing(self):
        self._write_env()
        r = notify.send("body", dedup_key="oauthblock:s:h:2", dry_run=True)
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.log_lines(), [],
                         "dry-run must not write to the delivery log")

    def test_prefix_boundary_no_false_match(self):
        # boundary-matched on ':'/'-' — a same-letters-but-different-namespace
        # key must NOT be swept in.
        self.assertIsNone(notify._suppressed_alert_class("oauthblockother:1"))
        self.assertIsNone(notify._suppressed_alert_class("oauth:1"))


class TestStuckalertUntouched(_HomeIsolated):
    """#676 was scoped to oauth ALONE; #688 (owner ruling 2026-08-25) then
    OVERTURNED the "stuckalert stays un-suppressed" scoping — the structural
    frozen-goal alarm is spam too, suppressed the same way. See
    test_stuck_alert_suppression_688.py for the full #688 lock; these two are the
    #676 assertions inverted (justified in-place per the deliberate-invariant-
    overturn convention)."""

    def test_stuckalert_is_now_suppressed_688(self):
        self.assertEqual(
            notify._suppressed_alert_class("stuckalert:sid:123"),
            "structural-stuck (#688)",
            "#688: the structural-stuck alarm is owner-ruled spam — now suppressed")

    def test_stuckalert_no_longer_delivers_688(self):
        self._write_env()
        r = notify.send("⛔ /goal ZAMRZLA", dedup_key="stuckalert:s:1")
        self.assertEqual(r, "suppressed", "#688: stuckalert must NOT POST")
        self.assertEqual(self.posts, [], "#688: stuckalert must reach nothing")

    def test_acctblock_still_delivers(self):
        # the genuine one-shot account-block alarm (needs a human) is ALSO
        # untouched — #676 is scoped to oauthblock alone.
        self.assertIsNone(notify._suppressed_alert_class("acctblock:s:9"))
        self._write_env()
        r = notify.send("body", dedup_key="acctblock:s:9")
        self.assertEqual(r, "sent")


if __name__ == "__main__":
    unittest.main()
