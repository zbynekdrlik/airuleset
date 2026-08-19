"""#559 — a keyless send must be dedupable AND traceable.

`notify.send()`'s dedup gate is `if dedup_key and not _dedup_claim(dedup_key)`.
The `dedup_key and` short-circuit means a keyless send (`dedup_key=None`) NEVER
claims a marker, NEVER checks dedup, and logs as an untraceable `key=-`
(1871 lines / the single largest bucket on dev1, 546 measured 226/day). So a
runaway keyless caller is unthrottleable and nothing in the delivery log can
tell two keyless sends apart.

The fix auto-derives a content-hash dedup key (bounded to a short time window)
at the chokepoint when none is provided: identical content within the window
dedups, DISTINCT content always sends (distinct body -> distinct hash), the
same content re-sent after the window sends again (the time bucket keeps the
dedup window at minutes, not the 14-day marker TTL a bare content hash would
inherit), and the delivery log carries `key=auto:...` instead of `key=-`.
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import notify                                            # noqa: E402


class _HomeIsolated(unittest.TestCase):
    """Every test writes into `$HOME/.claude` — never the real one (the live
    api-watchdog runs this tree every 60s on this box)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-autodedup-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._env))
        # A real POST is stubbed truthy so send() reaches "sent" without network.
        orig = notify._post_discord
        notify._post_discord = lambda *a, **k: True
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))

    ENV = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}


class TestAutoDedupKeyHelper(_HomeIsolated):

    def test_key_is_prefixed_and_owner_folded(self):
        k = notify._auto_dedup_key("hello", "zbynek", now=1000.0)
        self.assertTrue(k.startswith("auto:"),
                        "an auto-derived key must be namespaced 'auto:' so it "
                        "is traceable and can never collide with a real caller key")
        self.assertIn("zbynek", k, "owner is folded in so two DIFFERENT owners' "
                                   "identical bodies never collide")

    def test_distinct_bodies_get_distinct_keys(self):
        a = notify._auto_dedup_key("body A", "o", now=1000.0)
        b = notify._auto_dedup_key("body B", "o", now=1000.0)
        self.assertNotEqual(a, b, "different content MUST hash to different keys "
                                  "so two legitimately-distinct pings both send")

    def test_identical_body_same_window_same_key(self):
        a = notify._auto_dedup_key("same", "o", now=1000.0)
        b = notify._auto_dedup_key("same", "o", now=1000.0 + notify.AUTO_DEDUP_WINDOW_S - 1)
        self.assertEqual(a, b, "identical content within one window must share a "
                              "key so a runaway repeat is throttled")

    def test_identical_body_next_window_different_key(self):
        a = notify._auto_dedup_key("same", "o", now=1000.0)
        b = notify._auto_dedup_key("same", "o", now=1000.0 + notify.AUTO_DEDUP_WINDOW_S)
        self.assertNotEqual(a, b, "the SAME content in the NEXT window must get a "
                                 "different key so a legitimate re-send is not "
                                 "swallowed by the 14-day marker TTL")

    def test_never_matches_a_suppressed_alert_class(self):
        k = notify._auto_dedup_key("anything", "o", now=1000.0)
        self.assertIsNone(notify._suppressed_alert_class(k),
                          "an auto key must never be mistaken for a #546 "
                          "suppressed alert class")


class TestSendDedupsKeylessCalls(_HomeIsolated):

    def test_second_identical_keyless_send_is_deduped(self):
        st1 = notify.send("identical body", env=self.ENV, owner="zbynek")
        st2 = notify.send("identical body", env=self.ENV, owner="zbynek")
        self.assertEqual(st1, "sent")
        self.assertEqual(
            st2, "dedup",
            "a SECOND keyless send of identical content within the window must "
            "be recognised as a duplicate — if it reads 'sent', keyless sends "
            "are still unthrottleable (the #559 bug)")

    def test_distinct_keyless_bodies_both_send(self):
        st1 = notify.send("first distinct body", env=self.ENV, owner="zbynek")
        st2 = notify.send("second distinct body", env=self.ENV, owner="zbynek")
        self.assertEqual(st1, "sent")
        self.assertEqual(st2, "sent",
                         "two legitimately-DISTINCT keyless pings must BOTH send "
                         "— auto-dedup must never drop a distinct message")

    def test_identical_keyless_send_after_window_sends_again(self):
        with mock.patch("notify.time.time", return_value=1000.0):
            st1 = notify.send("windowed body", env=self.ENV, owner="zbynek")
        with mock.patch("notify.time.time",
                        return_value=1000.0 + notify.AUTO_DEDUP_WINDOW_S + 5):
            st2 = notify.send("windowed body", env=self.ENV, owner="zbynek")
        self.assertEqual(st1, "sent")
        self.assertEqual(st2, "sent",
                         "identical content re-sent AFTER the window must send "
                         "again — the time bucket bounds the dedup window")

    def test_keyless_send_is_not_suppressed(self):
        st = notify.send("some keyless alert body", env=self.ENV, owner="zbynek")
        self.assertEqual(st, "sent",
                         "a keyless send is never a #546 suppressed alert class")

    def test_explicit_key_is_not_overridden(self):
        st = notify.send("body", env=self.ENV, owner="", dedup_key="explicit:key:1")
        self.assertEqual(st, "sent")
        self.assertTrue(
            notify.marker_delivered("explicit:key:1"),
            "an explicit dedup_key must still be used verbatim — auto-derive "
            "only fires when NO key was given")


class TestKeylessSendIsTraceableInTheLog(_HomeIsolated):

    def test_log_line_carries_auto_key_not_dash(self):
        notify.send("a traceable keyless body", env=self.ENV, owner="zbynek")
        log = Path(notify.delivery_log_path()).read_text(encoding="utf-8")
        self.assertIn("key=auto:", log,
                      "a keyless send must log a traceable key=auto:... line")
        self.assertNotIn(
            "kind=python key=- ", log,
            "no keyless python send may still log the untraceable key=- form "
            "that #559 exists to eliminate")


if __name__ == "__main__":
    unittest.main()
