"""#1292 — production pages reach the owner's phone.

Owner decision 8.11.2026 (camera-box#1495, answer "1"): only production-
stopping outages reach the owner's phone, at most one message per 10 minutes
per check while the outage lasts; diagnostic alarms stay in the log.

A `page:<check>[:<anything>]` dedup key passes the zbynek owner policy, and
`notify.send()` rewrites it to `page:<check>:<epoch // 600>`, so the cap holds
whatever bucket the caller sends. A malformed `page:` key stays suppressed.
"""

import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import notify                                             # noqa: E402
from test_owner_policy_1137 import _Home                  # noqa: E402

# A 600 s bucket boundary near the real clock: `_prune_dedup` ages markers by
# their real mtime against `time.time()`, so a far-off fake clock prunes them.
T0 = float(int(time.time()) // 600 * 600)


class PagePolicy(_Home):

    def send_at(self, t, key, owner="zbynek"):
        with m.patch.object(notify.time, "time", return_value=t):
            return notify.send("program audio missing", owner=owner,
                               dedup_key=key)

    def test_page_key_reaches_zbynek(self):
        self.assertEqual(self.send_at(T0, "page:stream-audio"), "sent")
        self.assertEqual(len(self.posts), 1)

    def test_one_per_ten_minutes_per_check_whatever_the_caller_bucket(self):
        self.assertEqual(self.send_at(T0, "page:stream-audio:1"), "sent")
        # a new caller bucket inside the same 10 minutes does not re-send
        self.assertNotEqual(self.send_at(T0 + 60, "page:stream-audio:2"), "sent")
        self.assertNotEqual(self.send_at(T0 + 599, "page:stream-audio"), "sent")
        self.assertEqual(len(self.posts), 1)
        # the next 10 minute window sends again while the outage lasts
        self.assertEqual(self.send_at(T0 + 600, "page:stream-audio:3"), "sent")
        self.assertEqual(len(self.posts), 2)

    def test_checks_are_capped_separately(self):
        self.assertEqual(self.send_at(T0, "page:stream-audio"), "sent")
        self.assertEqual(self.send_at(T0 + 5, "page:dante-clock"), "sent")
        self.assertEqual(len(self.posts), 2)

    def test_canonical_key_is_logged(self):
        self.send_at(T0, "page:stream-audio:whatever")
        self.assertIn("page:stream-audio:%d" % int(T0 // 600), self.log())

    def test_malformed_page_keys_stay_suppressed(self):
        for key in ("page:", "page::5", "page:Bad Name:1", "page:-x:1",
                    "page:" + "a" * 65, "xpage:stream:1"):
            with self.subTest(key=key):
                self.assertEqual(self.send_at(T0, key), "suppressed")
        self.assertEqual(self.posts, [])

    def test_diagnostic_keys_stay_suppressed(self):
        for key in ("measurement-audio-stream-2990206", "dante-clock-mbc-2990225"):
            with self.subTest(key=key):
                self.assertEqual(self.send_at(T0, key), "suppressed")
        self.assertEqual(self.posts, [])

    def test_existing_alarm_keys_are_not_rewritten(self):
        self.assertEqual(self.send_at(T0, "healthz:dev1:down:5"), "sent")
        self.assertIn("healthz:dev1:down:5", self.log())

    def test_david_page_also_capped(self):
        self.assertEqual(self.send_at(T0, "page:x", owner="david"), "sent")
        self.assertNotEqual(self.send_at(T0 + 1, "page:x:9", owner="david"), "sent")
        self.assertEqual(len(self.posts), 1)

    def test_failed_page_is_retried_inside_the_window(self):
        with m.patch.object(notify, "_post_discord", lambda *a, **k: None):
            self.assertNotEqual(self.send_at(T0, "page:stream-audio"), "sent")
        self.assertEqual(self.send_at(T0 + 60, "page:stream-audio"), "sent")
        self.assertEqual(self.send_at(T0 + 120, "page:stream-audio"), "dedup")
        self.assertEqual(len(self.posts), 1)

    def test_check_name_is_case_insensitive(self):
        self.assertEqual(self.send_at(T0, "page:StreamAudio"), "sent")
        self.assertEqual(self.send_at(T0 + 5, "page:streamaudio"), "dedup")
        self.assertIn("page:streamaudio:%d" % int(T0 // 600), self.log())

    def _seed(self, status):
        key = notify.page_dedup_key("page:stream-audio", now=T0)
        notify._dedup_claim(key)
        notify._dedup_mark_status(key, status)
        return key

    def test_dry_run_keeps_a_failed_marker(self):
        key = self._seed("error")
        with m.patch.object(notify.time, "time", return_value=T0 + 5):
            notify.send("x", owner="zbynek", dedup_key="page:stream-audio",
                        dry_run=True)
        self.assertTrue(Path(notify._dedup_path(key)).exists())

    def test_suppressed_or_sent_marker_is_never_released(self):
        for status in ("suppressed", "sent"):
            with self.subTest(status=status):
                key = self._seed(status)
                self.assertEqual(self.send_at(T0 + 5, "page:stream-audio"), "dedup")
                notify._dedup_release(key)
        self.assertEqual(self.posts, [])

    def test_release_spares_a_claim_taken_after_the_check(self):
        key = self._seed("error")
        real = notify.marker_delivered

        def racing(k):
            seen = real(k)                 # reads "error" ...
            notify._dedup_release(k)       # ... then another sender re-claims
            notify._dedup_claim(k)
            return seen
        with m.patch.object(notify, "marker_delivered", racing):
            notify._release_failed_page(key)
        self.assertTrue(Path(notify._dedup_path(key)).exists())


if __name__ == "__main__":
    unittest.main()
