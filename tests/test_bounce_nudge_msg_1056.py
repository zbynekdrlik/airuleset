"""Job 8 bounce-nudge message upgrade (#1056 L1 (c) / #1057 item 1).

RED-first: `_bounce_nudge_message` renders `BOUNCE #N (gk HH:MM) unanswered Xh
— dispatch a lane now` from the gk-watch classifier, and falls back VERBATIM to
today's `watchdog.BOUNCE_NUDGE` when nothing resolves; `bounce_backstop`
threads a `watch_fn` and delivers the rich message. Cadence/dedup/persist and
the nudge kind (`bounce`) are untouched (locked by test_bounce_backstop.py).
"""
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import statusbar
import watchdog as wd
from watchdog import cross_stream as cs


def _bounce(issue, created_at_ts, age_h):
    return {"issue": issue, "state": "bounce-unanswered",
            "gk_latest": {"id": 999, "verdict": "BOUNCE",
                          "created_at": created_at_ts, "sha": "abc1234",
                          "ids": ["1"]},
            "age_seconds": age_h * 3600.0, "undispositioned_ids": ["1"]}


class BounceNudgeMessage(unittest.TestCase):
    def test_rich_line_when_gk_watch_resolves(self):
        ts = time.mktime((2026, 9, 17, 17, 23, 0, 0, 0, -1))
        msg = cs._bounce_nudge_message(
            [5613], "odoo-erp", "/root", now=ts + 9 * 3600,
            watch_fn=lambda iss, r: _bounce(iss, ts, 9))
        self.assertIn("BOUNCE #5613", msg)
        self.assertIn("17:23", msg)
        self.assertIn("unanswered 9h", msg)
        self.assertIn("dispatch a lane now", msg)

    def test_fallback_when_nothing_resolves(self):
        msg = cs._bounce_nudge_message(
            [5613, 6890], "odoo-erp", "/root", now=time.time(),
            watch_fn=lambda iss, r: {"issue": iss, "state": "unknown"})
        # verbatim today's text (contains the ticket numbers + the skill hint)
        self.assertEqual(msg, wd.BOUNCE_NUDGE % ("#5613 #6890", "odoo-erp"))

    def test_multiple_bounces_each_get_a_line(self):
        ts = time.mktime((2026, 9, 17, 18, 10, 0, 0, 0, -1))
        msg = cs._bounce_nudge_message(
            [5613, 6890], "odoo-erp", "/root", now=ts + 3600,
            watch_fn=lambda iss, r: _bounce(iss, ts, 1))
        self.assertIn("BOUNCE #5613", msg)
        self.assertIn("BOUNCE #6890", msg)


class BounceBackstopThreadsWatchFn(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.root = str(Path(tmp.name) / "devel" / "demo")
        Path(self.root).mkdir(parents=True)
        d = statusbar.cache_dir(self.home)
        d.mkdir(parents=True, exist_ok=True)
        (d / (statusbar.cwd_key(self.root) + ".json")).write_text(
            '{"open": 1, "name": "demo", "root": "%s", "ts": %d}'
            % (self.root, int(time.time())))

    def test_rich_message_delivered_to_idle_pane(self):
        captured = {}

        def fake_send(state, pid, root, msg, *a, **kw):
            captured["msg"] = msg
            return True

        ts = time.mktime((2026, 9, 17, 17, 23, 0, 0, 0, -1))
        now = ts + 9 * 3600
        with m.patch.object(wd, "list_claude_panes",
                            return_value=[("%1", self.root)]), \
             m.patch.object(wd, "capture_pane", return_value="❯ \n"), \
             m.patch.object(wd, "pane_in_mode", return_value=False), \
             m.patch.object(wd, "pane_at_idle_prompt", return_value=True), \
             m.patch.object(cs, "_safe_to_bounce_nudge", return_value=True), \
             m.patch.object(cs, "_send_bare_nudge_verified", side_effect=fake_send):
            cs.bounce_backstop(
                now, m.MagicMock(), {}, lambda *a, **k: "sent",
                home=self.home, gh_fetch=lambda root: [5613],
                cross_stream_repos={"demo"},
                watch_fn=lambda iss, r: _bounce(iss, ts, 9))
        self.assertIn("msg", captured)
        self.assertIn("BOUNCE #5613", captured["msg"])
        self.assertIn("dispatch a lane now", captured["msg"])


if __name__ == "__main__":
    unittest.main()
