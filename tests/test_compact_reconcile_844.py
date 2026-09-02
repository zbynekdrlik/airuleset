"""#844 — the post-compact lane RECONCILE rider (`watchdog/lane_reconcile.py`).

After a compaction (the transcript's newest `isCompactSummary` epoch), the main
session may have lost lane-completion notifications (the #29193 hazard the #844
forced compact accepts as safe, backed by durable state). This job-20 keystroke
rider — keyed on OBSERVED compaction (NOT the delivery ts: a queued compact
drains later), full-authority only, under the shared `nudge_gate`, importing NO
notify — lists worktree branches ahead of the integration branch carrying a
LANE-RETURN comment via an injected `reconcile_fetch` seam and delivers ONE
`lane-reconcile:` nudge listing them. A gh/git error → None → no nudge (the
doctrine `git worktree list` net covers the crashed-lane case).

RED against the pre-#844 tree: `watchdog/lane_reconcile.py` does not exist.
"""

import json
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd                                     # noqa: E402
from watchdog import lane_reconcile                       # noqa: E402
from watchdog import compact as wd_compact                # noqa: E402


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


class _FakeTmux:
    """A minimal `run` seam. `send_verified` is patched separately (below), so
    this only has to answer capture/display calls the rider makes directly."""

    def __init__(self):
        self.texts = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "capture-pane" in j:
            return "● Hotovo.\n❯ \n  ctx ███░\n"
        if "display-message" in j:
            return "sess:0.0"
        return ""

    def typed(self):
        return self.texts


class ReconcileRider844(unittest.TestCase):
    CWD = "/home/newlevel/devel/reconcile844"
    SID = "sess-reconcile-844"

    def setUp(self):
        # Isolate the compact-requests store the rider's #741 latch reads.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.creqp = Path(d.name) / "compact-requests.json"
        p = m.patch.object(wd_compact, "compact_requests_path",
                           return_value=self.creqp)
        p.start()
        self.addCleanup(p.stop)
        self.tdir = TemporaryDirectory()
        self.addCleanup(self.tdir.cleanup)

    def _tpath(self, compaction_epoch=None):
        """A transcript whose tail carries an `isCompactSummary` user entry at
        `compaction_epoch` (None = no compaction ever)."""
        p = Path(self.tdir.name) / (self.SID + ".jsonl")
        lines = [{"type": "assistant", "message": {"id": "m1", "content": "hi"}}]
        if compaction_epoch is not None:
            lines.append({"type": "user", "isCompactSummary": True,
                          "timestamp": _iso(compaction_epoch),
                          "message": {"content": "compact summary"}})
        p.write_text("\n".join(json.dumps(e) for e in lines) + "\n")
        return p

    def _run(self, now, tpath, reconcile_fetch, state=None, dry_run=False,
             handled=None, authority="full"):
        state = state if state is not None else {}
        lrecs = state.setdefault("lane_reconcile", {})
        tmux = _FakeTmux()

        def _fake_send_verified(pane_id, text, run=None, tpath=None,
                                sleep_fn=None, logs=None, out=None):
            tmux.texts.append(text)
            return True   # transcript-confirmed submit

        with m.patch("airuleset.resolve_authority", return_value=authority), \
                m.patch.object(wd, "send_verified", _fake_send_verified), \
                m.patch.object(wd, "_janitor_mark_watch", lambda *a, **k: None), \
                m.patch.object(wd, "_janitor_clear_watch", lambda *a, **k: None):
            logs = lane_reconcile.goal_lane_reconcile_recheck(
                now, tmux, lrecs, self.SID, self.CWD, "%9", tpath,
                "reconcile844", dry_run, handled if handled is not None else set(),
                reconcile_fetch=reconcile_fetch, state=state)
        return logs, tmux, state

    def test_844_branch_ahead_with_lane_return_gets_one_nudge(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)   # fresh compaction
        fetch = lambda cwd: [("worktree-agent-abc", 700, "fix the money gate"),
                             ("worktree-agent-def", 701, "add sms lane")]
        logs, tmux, state = self._run(now, tpath, fetch)
        typed = tmux.typed()
        self.assertTrue(typed, "a reconcile nudge must be typed: %r" % logs)
        blob = " ".join(typed)
        self.assertIn("lane-reconcile", blob)
        # ALL returned branches are listed (multi-lane burst — not just one).
        self.assertIn("worktree-agent-abc", blob)
        self.assertIn("worktree-agent-def", blob)
        self.assertIn("#700", blob)
        self.assertIn("#701", blob)

    def test_844_gh_error_none_yields_no_nudge(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)
        fetch = lambda cwd: None    # a gh/git error
        logs, tmux, state = self._run(now, tpath, fetch)
        self.assertEqual(tmux.typed(), [],
                         "a fetch error must NOT nudge: %r" % logs)

    def test_844_no_compaction_no_nudge(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=None)   # never compacted
        fetch = lambda cwd: [("worktree-agent-abc", 700, "x")]
        logs, tmux, state = self._run(now, tpath, fetch)
        self.assertEqual(tmux.typed(), [],
                         "no observed compaction -> no reconcile: %r" % logs)

    def test_844_deduped_per_compaction(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)
        fetch = lambda cwd: [("worktree-agent-abc", 700, "x")]
        state = {}
        logs1, tmux1, state = self._run(now, tpath, fetch, state=state)
        self.assertTrue(tmux1.typed(), "first reconcile nudges: %r" % logs1)
        # Same compaction, next sweep -> already reconciled, no second nudge.
        logs2, tmux2, state = self._run(now + 60, tpath, fetch, state=state)
        self.assertEqual(tmux2.typed(), [],
                         "the SAME compaction must not re-nudge: %r" % logs2)

    def test_844_not_full_authority_skips(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)
        fetch = lambda cwd: [("worktree-agent-abc", 700, "x")]
        logs, tmux, state = self._run(now, tpath, fetch, authority="fork-no-merge")
        self.assertEqual(tmux.typed(), [],
                         "a reduced-authority box never reconciles lanes: %r"
                         % logs)

    def test_844_pending_compact_holds_the_nudge(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)
        fetch = lambda cwd: [("worktree-agent-abc", 700, "x")]
        # A NEW compact is pending for this sid -> the #741 latch HOLDS the nudge.
        wd_compact.record_compact_request(self.SID, self.CWD, now=now,
                                          path=self.creqp, origin="self-callback")
        logs, tmux, state = self._run(now, tpath, fetch)
        self.assertEqual(tmux.typed(), [],
                         "a pending /compact must hold the reconcile nudge: %r"
                         % logs)

    def test_844_dry_run_types_nothing(self):
        now = time.time()
        tpath = self._tpath(compaction_epoch=now - 60)
        fetch = lambda cwd: [("worktree-agent-abc", 700, "x")]
        logs, tmux, state = self._run(now, tpath, fetch, dry_run=True)
        self.assertEqual(tmux.typed(), [],
                         "dry-run sends nothing: %r" % logs)


if __name__ == "__main__":
    unittest.main()
