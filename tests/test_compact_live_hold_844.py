"""#848 — compact at EVERY Work Complete, delivered over LIVE lanes.

Reverses #844 (and the #723/#724 batch doctrine it half-fixed). #844 BOUNDED
`deliver_compact`'s live-tasks / live-bg-bash veto with a 30-min `hbts` cap; the
STEP-0 live experiment (re-run for #848 on CC 2.1.258, dev1 2026-09-02) proved a
`/compact` typed at an idle prompt with worktree lanes + a bg-bash waiter + an
armed `/goal` all live does NOT break the task registry, so #848 removes the two
live-task vetoes OUTRIGHT (no cap) — a boundary compact delivers even with lanes
live, as plain `sent`/`queued`. The residual lost-notification case is covered by
#844's retained LANE-RETURN gate + lane-reconcile rider (a SEPARATE, kept net).

This file was `test_compact_live_hold_844.py` (the BOUNDED-cap lock); it is
REWRITTEN, never deleted (the #723 lesson: flip a doctrine test, keep its teeth).
Every assertion below is RED against the pre-#848 tree: `deliver_compact` still
vetoes a live lane (skip:live-tasks), still writes/reads `hbts`, and still owns
`_compact_live_bg_bash` + the `COMPACT_LIVE_HOLD_CAP_S` cap machinery — so each
"delivers anyway / no cap / no hbts / helper gone" assertion fails until the fix.
"""

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import compact                                       # noqa: E402

# Reuse the exact fixtures the main compact suite already models (idle-pane
# capture, a live sibling-lane transcript, isolated state files).
from test_compact import (                                         # noqa: E402
    _isolate_compact_state,
    _write_marker_transcript,
    _write_subagent_transcript,
    DeliverCompactFakeTmux,
    CB_IDLE_CAP,
)


class TestCompactOverLanes848(unittest.TestCase):
    CWD = "/home/newlevel/devel/livehold848"
    SID = "sess-live-hold-848"

    def setUp(self):
        self.reqp, self.delp, self.syncp = _isolate_compact_state(self)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def _sweep(self, proj, now):
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        logs = compact.compact_sweep(
            now, run=tmux, projects_dir=proj, requests_path=self.reqp,
            delivered_path=self.delp)
        return logs, tmux

    def test_1084_sweep_never_delivers_over_a_live_lane(self):
        # #1084: compact_sweep is REMOVED — it never delivers, over a live lane or
        # not; it early-returns the removed line. (The #848 "delivers over live
        # lanes" behaviour is moot: there is no machine compact at all.)
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        real_now = time.time()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=real_now, agent_id="lane848")
        logs, tmux = self._sweep(proj, T + 1750)
        self.assertEqual(tmux.typed_texts(), [], "no /compact ever (#1084)")
        self.assertTrue(any("machine compacts removed" in ln for ln in logs), logs)
        # the sweep never reads/consumes the request
        self.assertIn(self.SID, compact.load_compact_requests(self.reqp))

    def test_1084_sweep_never_delivers_over_a_young_boundary(self):
        # #1084: same, for a young boundary + a live lane — no delivery, no read.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        real_now = time.time()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=real_now, agent_id="lane848b")
        logs, tmux = self._sweep(proj, T + 600)
        self.assertEqual(tmux.typed_texts(), [], "no /compact ever (#1084)")
        self.assertTrue(any("machine compacts removed" in ln for ln in logs), logs)

    def test_848_record_writes_no_hbts_keeps_ts_and_bts(self):
        # The #844 cap anchor `hbts` (and its across-record inheritance) is gone;
        # `ts` (refreshable age-cap anchor) and `bts` (HOLD-log observability)
        # stay exactly as before.
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        e = compact.load_compact_requests(self.reqp)[self.SID]
        self.assertNotIn("hbts", e, "#848 removes the live-hold cap anchor")
        self.assertEqual(e["ts"], int(T), "ts (age-cap anchor) preserved")
        self.assertEqual(e["bts"], int(T), "bts (boundary observability) preserved")

    def test_848_legacy_hbts_rec_is_tolerated_on_re_record(self):
        # An on-disk request written by the pre-#848 tree still carries `hbts`;
        # a re-record must ignore it, never KeyError, and not resurrect the key.
        T = 1_000_000.0
        d = compact.load_compact_requests(self.reqp)
        d[self.SID] = {"cwd": self.CWD, "ts": int(T), "bts": int(T),
                       "hbts": int(T - 5000), "origin": "self-callback"}
        compact._save_compact_requests(d, self.reqp)
        ok = compact.record_compact_request(self.SID, self.CWD, now=T + 900,
                                            path=self.reqp, origin="self-callback")
        self.assertTrue(ok, "a legacy-hbts rec re-records cleanly")
        e = compact.load_compact_requests(self.reqp)[self.SID]
        self.assertNotIn("hbts", e, "re-record drops the legacy cap anchor")
        self.assertEqual(e["bts"], int(T + 900),
                         "bts is the fresh per-record boundary")

    def test_848_cap_machinery_is_gone(self):
        # The #844 cap constant, its helpers, and the dead live-bg-bash veto
        # helper are all removed (net subtraction, #486).
        for name in ("COMPACT_LIVE_HOLD_CAP_S", "_compact_live_hold_cap",
                     "_compact_live_hold_reached", "_compact_live_bg_bash"):
            self.assertFalse(hasattr(compact, name),
                             "#848 removes %s" % name)

    def test_848_no_cap_language_in_dry_run(self):
        proj = self._dir()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        logs = compact.compact_sweep(
            T + 100, run=tmux, dry_run=True, projects_dir=proj,
            requests_path=self.reqp, delivered_path=self.delp)
        self.assertFalse(any("live-hold cap" in ln for ln in logs),
                         "the dry-run sweep no longer surfaces a cap: %r" % logs)


if __name__ == "__main__":
    unittest.main()
