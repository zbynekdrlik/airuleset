"""#546 — owner-directed suppression of the automated ALERT ping classes.

Owner directive (2026-08-18, verbatim): airuleset's ONLY job on an api-error is
the silent `continue` auto-resume; pinging the owner is counterproductive, and
limit/subscription monitoring is another project's concern. So the api-error,
5h/usage LIMIT, and token-BURN alert classes must no longer POST to Discord —
their signal moves to the machine channel (the watchdog journal + an explicit
`suppressed` line in `notify-delivery.log`, the #546 audience split). The
`continue` auto-resume (send_verified / deliver_with_stash / tmux) never routes
through `send()`, so suppression at the `send()` chokepoint leaves it untouched.

These locks:
  1. `_suppressed_alert_class()` maps the suppressed dedup-key prefixes (#546's
     five + #704's ten state/stall classes) to a human label, and NOTHING else
     (❓ `waiting:`, ✅ `done:`, run-cards `<repo>#<n>`, bounce/gkreq,
     `acctblock:` genuine-alarm). NOTE: `busypane:` was preserved here under
     #546 (job-4, distinct from api-error) but is now #704-suppressed — see
     PRESERVED_KEYS below and tests/test_state_stall_suppression_704.py.
  2. `send()` with a suppressed key POSTs NOTHING, returns "suppressed", and
     logs an explicit `suppressed` decision (not a silent drop, #486/#134).
  3. a non-suppressed key is unaffected (still reaches the real send path).
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
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-suppress546-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        # Any suppressed send must NEVER touch the network — a spy proves it.
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


# The exact ten live keys that the five suppressed classes emit (STEP-0 grep of
# every `dedup_key=` across watchdog/ + airuleset.py), each with its owning job.
SUPPRESSED_KEYS = [
    "apierr:sid:hash:123",            # job 1 first-stall
    "apierr-busypane:key:456",        # job 1 busy-pane
    "apierr-giveup:key:h:789",        # job 1 give-up
    "apierr-stashabort:key:111",      # job 1 stash-abort
    "sesslimit:key:222",              # job 6 5h-limit
    "sesslimit-giveup:key:333",       # job 6 give-up
    "sesslimit-resume:key:444",       # job 6 resume
    "usage:bucket:90",                # usage.py weekly-limit %
    "burn-alert:496411",              # job 19 hourly token-burn
    "fleet-burn-budget:496411",       # job 16 fleet budget
]

# Keys that MUST keep sending — the readable channel the owner wants to keep.
PRESERVED_KEYS = [
    "waiting:key:555",                # ❓ question ping
    "done:sid",                       # ✅ done
    "camera-box#42",                  # per-ticket run-card
    "bounce:repo:9",                  # cross-stream bounce (directive: unchanged)
    "gkreq:repo:8",                   # gatekeeper request (unchanged)
    # `busypane:` was preserved here under #546 (job-4 working-stall, distinct
    # from the api-error class) — #704 (2026-08-25 owner ruling) REVERSES that:
    # a "visí na ⏳ WORKING, zaseknuto" verdict is a session-stall heuristic and
    # is now #546-suppressed. Its suppression is locked in
    # tests/test_state_stall_suppression_704.py; removed from PRESERVED here so
    # this control matches the current denylist (invert-with-justification, #688).
    "acctblock:sid:888",             # genuine one-shot alarm (deliberately kept)
    "conformance-hb:box:1",           # fleet-death alarm (job 35)
]


class TestSuppressedAlertClassHelper(_HomeIsolated):
    def test_each_suppressed_key_maps_to_a_label(self):
        for k in SUPPRESSED_KEYS:
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be recognized as a suppressed alert class" % k)

    def test_preserved_keys_are_not_suppressed(self):
        for k in PRESERVED_KEYS:
            self.assertIsNone(
                notify._suppressed_alert_class(k),
                "%r must NOT be suppressed (readable-channel class)" % k)

    def test_keyless_send_is_never_suppressed(self):
        self.assertIsNone(notify._suppressed_alert_class(None))
        self.assertIsNone(notify._suppressed_alert_class(""))

    def test_prefix_boundary_no_false_match(self):
        # a key that merely STARTS with the letters but is a different namespace
        # must not be swept in (boundary-matched on ":" / "-").
        self.assertIsNone(notify._suppressed_alert_class("usageother:1"))
        self.assertIsNone(notify._suppressed_alert_class("apierrornot:2"))


class TestSendSuppressesAlertClasses(_HomeIsolated):
    def test_suppressed_key_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        for k in SUPPRESSED_KEYS:
            self.posts.clear()
            r = notify.send("body", dedup_key=k)
            self.assertEqual(r, "suppressed", "%r should be suppressed" % k)
            self.assertEqual(self.posts, [], "%r must POST nothing" % k)

    def test_suppression_is_a_logged_decision_not_silent(self):
        self._write_env()
        notify.send("body", dedup_key="apierr:sid:hash:1")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed send must leave a delivery-log line")
        self.assertIn("apierr", lines[-1])

    def test_return_message_id_shape_is_respected(self):
        self._write_env()
        r = notify.send("body", dedup_key="burn-alert:5", return_message_id=True)
        self.assertEqual(r, ("suppressed", None))

    def test_dry_run_suppressed_mutates_nothing(self):
        self._write_env()
        r = notify.send("body", dedup_key="sesslimit:key:2", dry_run=True)
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.log_lines(), [],
                         "dry-run must not write to the delivery log")

    def test_preserved_key_still_reaches_the_send_path(self):
        # No .env → a NON-suppressed key resolves to no-config (it reached the
        # real path); a suppressed one short-circuits to "suppressed" first.
        self.assertEqual(notify.send("body", dedup_key="waiting:k:1"), "no-config")
        self.assertEqual(notify.send("body", dedup_key="camera-box#42"), "no-config")
        self.assertEqual(notify.send("body", dedup_key=None), "no-config")
        self.assertEqual(notify.send("body", dedup_key="acctblock:s:9"), "no-config")

    def test_preserved_key_posts_when_configured(self):
        self._write_env()
        r = notify.send("body", dedup_key="waiting:k:2")
        self.assertEqual(r, "sent")
        self.assertEqual(len(self.posts), 1, "a ❓ ping must still POST")


if __name__ == "__main__":
    unittest.main()
