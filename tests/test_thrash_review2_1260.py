"""#1260 verification review — CC's recoverable "after shrinking fallback" thrash is
NOT held (the next message retries the bigger model), the thrash notice follows the
#704 rule (a session-stopped state never pings the owner's phone), and a watchdog
goal re-arm never types into a held session.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import notify  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import goal  # noqa: E402
from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux, GOAL_IDLE_CAP, _encode, _isolate_goal_state)
from test_autocompact_thrash_1260 import THRASH_ENTRY, _FakeTmux  # noqa: E402

SID = "5e55abc0-51d0-4a5e-9f1e-00000000abcd"
# The CC 2.1.306 text for a thrash that followed a model fallback (zs(), afterShrinkingFallback).
FALLBACK_TEXT = (
    "Autocompact is thrashing: the context refilled to the limit within 3 turns of the "
    "previous compact, 3 times in a row. The likely cause: while working on this response, "
    "Claude Code fell back from Opus 5.5 to Opus 4.8 and runs that model with a 200K-token "
    "context window on your provider, instead of 1M. Your next message tries Opus 5.5 first. "
    "To avoid this, use a fallback model with a 1M context window, or use /clear to start fresh.")
FALLBACK_ENTRY = {"type": "assistant", "isApiErrorMessage": True,
                  "apiError": "autocompact_thrashing", "error": "invalid_request",
                  "message": {"role": "assistant", "model": "<synthetic>",
                              "content": [{"type": "text", "text": FALLBACK_TEXT}]}}


def _write(path, entries, age_s, now):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    os.utime(path, (now - age_s, now - age_s))
    return path


class FallbackThrashIsResumable(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.projects = Path(self.tmp) / "projects"
        orig = wd.usage._USAGE_CACHE_PATH
        wd.usage._USAGE_CACHE_PATH = str(Path(self.tmp) / "usage-cache.json")
        self.addCleanup(setattr, wd.usage, "_USAGE_CACHE_PATH", orig)

        def _typing(pid, text, run=None, tpath=None, **_kw):
            run(["tmux", "send-keys", "-t", pid, "-l", "--", text])
            return True
        p = m.patch.object(wd, "send_verified", _typing)
        p.start()
        self.addCleanup(p.stop)

    def test_not_held_and_job_one_resumes_it(self):
        from watchdog import thrash
        now = 1_000_000
        cwd = "/devel/fellback"
        tpath = _write(self.projects / wd.encode_project_dir(cwd) / (SID + ".jsonl"),
                       [FALLBACK_ENTRY], 600, now)
        self.assertFalse(thrash.held(str(tpath)))
        fake = _FakeTmux("%5\tclaude\t" + cwd + "\n")
        wd.run_once(now=now, run=fake, send_fn=lambda *a, **k: "sent",
                    projects_dir=self.projects, state_path=str(Path(self.tmp) / "s.json"),
                    grace=300, interval=300, max_nudges=3)
        self.assertEqual(fake.continues_sent(), 1, "the next message retries the bigger model")


class ThrashNoticeIsMachineChannel(TestCase):
    def test_the_thrash_class_is_suppressed_at_send(self):
        self.assertTrue(notify._suppressed_alert_class("thrash:5e55:abc:1000000"))


class GoalRearmSkipsAHeldSession(TestCase):
    CWD = "/home/newlevel/devel/thrashgoal"
    TEXT = "/goal STOP CONDITIONS test payload"

    def setUp(self):
        _isolate_goal_state(self)

    def test_dark_rearm_is_not_typed_into_a_held_pane(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        import time
        _write(proj / _encode(self.CWD) / (SID + ".jsonl"), [THRASH_ENTRY], 120, time.time())
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")], GOAL_IDLE_CAP)
        with m.patch.object(wd, "_goal_autoarm_recent_human_activity", return_value=(False, "")):
            word = goal.deliver_goal(SID, self.CWD, self.TEXT, "full", run=tmux,
                                     projects_dir=proj, sleep_fn=lambda s: None,
                                     origin="dark-rearm")
        self.assertEqual(word, "skip:thrash-held")
        self.assertEqual(tmux.sent, [])
