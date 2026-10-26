"""Bundle B9 fix round, sixth adversarial review of the issue 1257 owner-stop
latch: the stall episodes a pause ends (all five keys, never in a dry run),
a paused pane that shares its cwd with a working one, the latch journal when
a session moves between a pane that owns it and none, and the issue 1256
tmux server scan on /proc bytes that are not UTF-8.
"""
import json
import os
import sys
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchdog as wd  # noqa: E402
from watchdog import owner_stop  # noqa: E402
from test_owner_stop_review5_1257 import _lane  # noqa: E402

EPISODE_KEYS = ("soa", "soalert", "soa_ts", "gks_ts", "gks_alert")


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts))


def _u(text, ts):
    return {"type": "user", "timestamp": _iso(ts),
            "message": {"role": "user", "content": text}}


def _a(text, ts):
    return {"type": "assistant", "timestamp": _iso(ts),
            "message": {"content": [{"type": "text", "text": text}]}}


class _Stuck(unittest.TestCase):
    """The issue 662 stuck-alert sweep harness (one armed, stuck pane %9 on
    session `sess-662`)."""

    def setUp(self):
        import test_oauth_stuck_alert_662 as sa
        self.goal = sa.goal
        self.h = sa.SweepStuckOwnerAlert("run")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.addCleanup(owner_stop.reset)

    def _dir(self, kw):
        return Path(kw["projects_dir"]) / wd.encode_project_dir(self.h.CWD)

    def _own_pause(self, now, kw):
        t = self._dir(kw) / "sess-662.jsonl"
        if "pauza" not in t.read_text(encoding="utf-8"):
            aged = os.path.getmtime(t)
            with open(t, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(_u("pauza", now - 600)) + "\n")
            os.utime(t, (aged, aged))
        owner_stop.refresh(kw["state"], [("%9", self.h.CWD)], kw["projects_dir"], now)


class AnOwnPauseEndsEveryStallEpisode(_Stuck):
    """Review 6, finding 4: removing the dry-run guard of the episode reset,
    or dropping `soalert` / `gks_alert` from it, left every test green (the
    old test seeded three of the five keys and never ran a dry sweep).
    `run_once` saves state even in a dry run, so a dry pop would persist."""

    def _swept(self, dry_run):
        state = {"goal_lane": {"sess-662": {k: 1 for k in EPISODE_KEYS}}}
        with _lane(self, self.goal, self.h.CWD, self._own_pause):
            _s, state, sid = self.h._run(self.h._stuck(1), state=state,
                                         dry_run=dry_run)
        return state["goal_lane"][sid]

    def test_a_live_sweep_ends_all_five(self):
        rec = self._swept(False)
        self.assertEqual([k for k in EPISODE_KEYS if k in rec], [], rec)

    def test_a_dry_run_leaves_them(self):
        rec = self._swept(True)
        self.assertEqual([k for k in EPISODE_KEYS if k in rec], list(EPISODE_KEYS), rec)


if __name__ == "__main__":
    unittest.main()
