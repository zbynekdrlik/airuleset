"""#359 — `notify.send`'s dedup marker can fail OPEN on a long key.

`notify._dedup_path(key)` sanitises `key` into a filename under
`~/.claude/autopilot-notify-sent/`, and `_dedup_claim` opens it with
`O_CREAT | O_EXCL`. When the SANITISED key is long enough to exceed the
filesystem's `NAME_MAX` (~255 bytes), `os.open()` raises `OSError`
(`ENAMETOOLONG`), and `_dedup_claim`'s own deliberate
`except OSError: return True` fail-open path (a documented choice — "better
a possible double-send than dropping the user's requested message") means
the key NEVER ACTUALLY DEDUPES for that call: every send with an
over-length key claims fresh, silently defeating dedup.

The fix bounds the sanitised key's length in `_dedup_path` — the ONE choke
point `_dedup_claim`/`_dedup_mark_status`/`marker_delivered`/`_dedup_release`
all resolve through — by hashing the RAW key whenever the sanitised form
would exceed a safe threshold, well under the real NAME_MAX.
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
    """Every test here writes into `$HOME/.claude` — never the real one (the
    live api-watchdog executes this repo's working tree every 60s on this
    box, so an un-isolated test races production)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-dedupkey-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._env = dict(os.environ)
        os.environ["HOME"] = str(self.home)
        self.addCleanup(lambda: os.environ.clear() or os.environ.update(self._env))


def _long_key(prefix="gkreq:", n=70, start=1000):
    """A key shaped like the ticket's own reproduction (a space-joined list
    of ticket numbers) whose SANITISED form reliably exceeds the real
    filesystem NAME_MAX (~255 bytes) on every platform this runs on."""
    return prefix + " ".join(str(i) for i in range(start, start + n))


class TestLongKeyActuallyDedupes(_HomeIsolated):
    """The core reproduction: two identical claims of a long key must behave
    like two identical claims of a short key (first True, second False) —
    not both True."""

    def test_a_long_key_dedupes_across_two_claims(self):
        key = _long_key()
        r1 = notify._dedup_claim(key)
        r2 = notify._dedup_claim(key)
        self.assertTrue(r1, "the first claim of any key must succeed")
        self.assertFalse(
            r2, "the SECOND claim of the SAME long key must be refused — "
            "if this is True, the marker never reached disk and dedup "
            "silently never happens for this key")

    def test_a_long_key_marker_actually_reaches_disk(self):
        key = _long_key()
        notify._dedup_claim(key)
        self.assertTrue(
            os.path.exists(notify._dedup_path(key)),
            "a claimed key's marker file must exist on disk — an "
            "ENAMETOOLONG failure that silently drops the write is exactly "
            "the bug: the claim reports success but nothing is persisted")

    def test_two_distinct_long_keys_do_not_collide(self):
        key_a = _long_key(start=1000)
        key_b = _long_key(start=5000)
        self.assertNotEqual(notify._dedup_path(key_a), notify._dedup_path(key_b))
        self.assertTrue(notify._dedup_claim(key_a))
        self.assertTrue(notify._dedup_claim(key_b),
                        "a DIFFERENT long key must claim independently, "
                        "never be blocked by an unrelated key's marker")


class TestDedupPathStaysWithinAFilesystemSafeLength(_HomeIsolated):

    def test_a_long_keys_marker_filename_is_bounded(self):
        key = _long_key()
        path = notify._dedup_path(key)
        name_len = len(os.path.basename(path).encode("utf-8"))
        self.assertLessEqual(
            name_len, 255,
            "the marker filename must never exceed the real filesystem "
            "NAME_MAX (255 bytes on ext4/xfs/btrfs), or os.open() raises "
            "ENAMETOOLONG and _dedup_claim's fail-open swallows it")

    def test_a_short_keys_marker_path_is_unaffected(self):
        # Non-regression: the overwhelmingly common case (a repo#issue card
        # key, a short session-id-keyed watchdog key) must resolve to
        # EXACTLY the same path it always has — the fix only engages past
        # the length threshold.
        key = "airuleset#41"
        path = notify._dedup_path(key)
        self.assertEqual(os.path.basename(path), "airuleset#41")

    def test_the_boundary_is_exact_not_off_by_one(self):
        # A mutant swapping `>` for `>=` in _dedup_path survives every OTHER
        # test in this file (a fresh-context adversarial review found this,
        # #359) — nothing else pins the exact edge. A key whose SANITISED
        # form is exactly _DEDUP_NAME_MAX bytes must keep its literal name;
        # one byte longer must switch to the hashed form.
        at_limit = "a" * notify._DEDUP_NAME_MAX
        over_limit = "a" * (notify._DEDUP_NAME_MAX + 1)
        self.assertEqual(len(at_limit), notify._DEDUP_NAME_MAX)
        name_at = os.path.basename(notify._dedup_path(at_limit))
        name_over = os.path.basename(notify._dedup_path(over_limit))
        self.assertEqual(
            name_at, at_limit,
            "a key sanitising to EXACTLY the limit must keep its literal, "
            "un-hashed name")
        self.assertTrue(
            name_over.startswith("long-"),
            "a key ONE byte over the limit must already be hashed")


class TestSendEndToEndWithALongDedupKey(_HomeIsolated):
    """The real consumer-facing behaviour: `notify.send()` itself must
    dedupe a second identical call carrying a long `dedup_key` — not just
    the private `_dedup_claim` helper in isolation."""

    def setUp(self):
        super().setUp()
        orig = notify._post_discord
        notify._post_discord = lambda *a, **k: True
        self.addCleanup(lambda: setattr(notify, "_post_discord", orig))

    def test_send_dedupes_the_second_call_with_a_long_key(self):
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        key = _long_key(prefix="gkreq:someRepoName:")
        st1 = notify.send("body", env=env, owner="", dedup_key=key)
        st2 = notify.send("body", env=env, owner="", dedup_key=key)
        self.assertEqual(st1, "sent")
        self.assertEqual(
            st2, "dedup",
            "a SECOND send() with the identical long dedup_key must be "
            "recognised as a duplicate — if it reads 'sent' again, the "
            "long key never actually claimed and every retry re-posts")

    def test_marker_delivered_reads_true_for_a_long_key_after_a_real_send(self):
        env = {"DISCORD_BOT_TOKEN": "t", "DISCORD_NOTIFICATION_CHANNEL_ID": "c"}
        key = _long_key(prefix="gkreq:anotherRepo:")
        st = notify.send("body", env=env, owner="", dedup_key=key)
        self.assertEqual(st, "sent")
        self.assertTrue(notify.marker_delivered(key))


if __name__ == "__main__":
    unittest.main()
