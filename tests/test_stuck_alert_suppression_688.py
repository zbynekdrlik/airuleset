"""#688 — the #662 `stuckalert:` frozen-goal owner alarm is spam; suppress it via #546.

Owner report (2026-08-25): a watchdog "⛔ odoo-erp-david1 — /goal slučka ZAMRZLA:
8× po sebe … 0 workerov … pozri sa na ňu prosím" ping is no-value spam. #662 added
an un-suppressed `stuckalert:` Discord alarm for a persistent STRUCTURAL stuck
(armed /goal + 0 workers + backlog + idle over threshold); the day before, #676
ruled the SIBLING `oauthblock:` alarm spam and suppressed it while explicitly
KEEPING `stuckalert:` un-suppressed. #688 now overturns exactly that: the
structural `stuck` verdict is a HEURISTIC that fires on many states that do NOT
need a human (transient idle, a session the owner turned off, a #676-normal
oauth-revoke), so it cannot clear the "genuinely needs a human" bar. Remove the
PING, keep the machine channel: add `stuckalert:` to the EXISTING #546 owner-
suppression list (`send()` gate), so a stuckalert send POSTs nothing, returns
"suppressed", and leaves an explicit `suppressed` delivery-log line (never a
silent drop, #486/#134). The `continue` auto-recovery never routes through
`send()`, so suppression at the chokepoint leaves it untouched; `acctblock:`
(a genuine account-block that needs a human) stays un-suppressed.

RED against the pre-#688 tree (where `stuckalert:` is NOT in
`SUPPRESSED_ALERT_PREFIXES`).
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
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-stuck688-"))
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


# The live stuckalert keys goal_lane_sweep emits
# (`dedup_key="stuckalert:%s:%d"` in watchdog/goal.py::_lane_stuck_owner_alert).
STUCKALERT_KEYS = [
    "stuckalert:sid:1000000",
    "stuckalert:sess-662:1000480",
]


class TestStuckalertIsSuppressed(_HomeIsolated):
    def test_classifier_recognises_stuckalert_as_suppressed(self):
        for k in STUCKALERT_KEYS:
            self.assertIsNotNone(
                notify._suppressed_alert_class(k),
                "%r must be a #546/#688 owner-suppressed class" % k)

    def test_stuckalert_send_posts_nothing_and_returns_suppressed(self):
        self._write_env()          # fully configured — a normal key WOULD post
        for k in STUCKALERT_KEYS:
            self.posts.clear()
            r = notify.send("⛔ /goal slučka ZAMRZLA", dedup_key=k)
            self.assertEqual(r, "suppressed", "%r should be suppressed" % k)
            self.assertEqual(self.posts, [], "%r must POST nothing" % k)

    def test_suppression_is_a_logged_decision_not_silent(self):
        # #486/#134: a suppressed send leaves an explicit delivery-log line — it
        # is a logged DECISION, never a silent drop.
        self._write_env()
        notify.send("body", dedup_key="stuckalert:sid:1")
        lines = [ln for ln in self.log_lines() if "suppressed" in ln]
        self.assertTrue(lines, "a suppressed stuckalert send must be LOGGED")
        self.assertIn("stuckalert", lines[-1], "the log line names the key")

    def test_dry_run_suppressed_mutates_nothing(self):
        self._write_env()
        r = notify.send("body", dedup_key="stuckalert:s:2", dry_run=True)
        self.assertEqual(r, "suppressed")
        self.assertEqual(self.log_lines(), [],
                         "dry-run must not write to the delivery log")

    def test_prefix_boundary_no_false_match(self):
        # boundary-matched on ':'/'-' — a same-letters-but-different-namespace
        # key must NOT be swept in.
        self.assertIsNone(notify._suppressed_alert_class("stuckalertother:1"))
        self.assertIsNone(notify._suppressed_alert_class("stuck:1"))


class TestOtherAlarmsUntouched(_HomeIsolated):
    """#688 is scoped to stuckalert — acctblock (needs a human) stays un-suppressed."""

    def test_acctblock_still_delivers(self):
        self.assertIsNone(notify._suppressed_alert_class("acctblock:s:9"))
        self._write_env()
        r = notify.send("body", dedup_key="acctblock:s:9")
        self.assertEqual(r, "sent")

    def test_apierr_still_suppressed(self):
        # the pre-existing #546 class is unaffected by #688.
        self.assertIsNotNone(notify._suppressed_alert_class("apierr-giveup:k:h:1"))


if __name__ == "__main__":
    unittest.main()
