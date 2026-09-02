"""#844 — the BOUNDED live-hold cap for the drained-boundary compact.

On a saturated `/autopilot-master` box the fleet is essentially never drained,
so `deliver_compact`'s live-tasks / live-bg-bash veto holds the boundary compact
undelivered forever (`compact_sweep` refreshes `ts` on every hold-extend), the
main's context climbs to 776K, and only CC's OWN overflow auto-compact fires
(mid-fleet, the #29193 hazard the veto tried to avoid). #844 bounds the hold:
after `COMPACT_LIVE_HOLD_CAP_S` (default 1800s, measured `now - hbts`, the
INHERITABLE held-boundary anchor) with the boundary still un-drained, deliver the
`/compact` anyway (`sent:live-hold-cap`) — the step-0 live experiment proved
forcing the compact at an idle prompt while a lane is live does NOT kill the
lane, its commit is durable, and its completion notification survives.

RED against the pre-#844 tree: `record_compact_request` never writes `hbts`,
`deliver_compact` never forces past the live-tasks veto, and the sweep never
surfaces the cap in `--dry-run` — so every FORCE / hbts / dry-run assertion below
fails until the fix lands.
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

# The 30-min age cap is the SAME 1800s as the live-hold cap, and it is checked
# BEFORE the live-tasks veto — so the force can only fire on a HELD request whose
# `ts` is still fresh (hold-extended each sweep in production). The two-sweep
# fixtures below replicate that: sweep 1 hold-extends `ts`, sweep 2 forces.
_MAX_AGE = compact.COMPACT_REQUEST_MAX_AGE_S


class TestLiveHoldCap844(unittest.TestCase):
    CWD = "/home/newlevel/devel/livehold844"
    SID = "sess-live-hold-844"

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

    def test_844_live_hold_cap_forces_delivery_past_live_tasks(self):
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        real_now = time.time()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        # A FRESH live lane (mtime = real now) -> `_live_bg_tasks_detail` vetoes.
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=real_now, agent_id="lane844")
        # Sweep 1 at T+1750 (< 1800 cap): held 1750 < cap -> skip:live-tasks
        # (hold-extend refreshes `ts` to T+1750, keeps the request alive under
        # the age cap), NO force yet.
        logs1, tmux1 = self._sweep(proj, T + 1750)
        self.assertTrue(any("skip:live-tasks" in ln for ln in logs1),
                        "sweep 1 must still veto below the cap: %r" % logs1)
        self.assertNotIn("/compact", tmux1.typed_texts(),
                         "no force below the cap")
        # Sweep 2 at T+1850: age from the refreshed ts (T+1750) is 100s < 1800
        # (not expired), but held from hbts (T) is 1850 >= 1800 cap -> FORCE.
        logs2, tmux2 = self._sweep(proj, T + 1850)
        self.assertIn("/compact", tmux2.typed_texts(),
                      "the held boundary must be FORCED past live-tasks at the "
                      "cap: %r" % logs2)
        self.assertTrue(any("sent:live-hold-cap" in ln for ln in logs2),
                        "the forced delivery must be journalled "
                        "sent:live-hold-cap: %r" % logs2)
        self.assertNotIn(self.SID, compact.load_compact_requests(self.reqp),
                         "a forced delivery is terminal — request cleared")

    def test_844_held_below_cap_still_skips_live_tasks(self):
        # Guard / mutation target: the force must NOT fire before the cap (a
        # premature force would compact mid-batch on every box). Passes on BOTH
        # the pre- and post-fix tree; a mutation lowering the cap to ~0 fails it.
        proj = self._dir()
        _write_marker_transcript(proj, self.CWD, self.SID)
        real_now = time.time()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        _write_subagent_transcript(proj, self.CWD, self.SID,
                                   mtime=real_now, agent_id="lane844b")
        logs, tmux = self._sweep(proj, T + 600)   # held 600 < cap 1800
        self.assertTrue(any("skip:live-tasks" in ln for ln in logs), logs)
        self.assertFalse(any("live-hold-cap" in ln for ln in logs),
                         "no force below the cap: %r" % logs)
        self.assertNotIn("/compact", tmux.typed_texts())

    def test_844_hbts_inherited_across_held_re_records(self):
        # The Fable-consult hole: a busy master re-recording a genuine
        # `## ✅ Work Complete` boundary every ~15min while lanes stay live would
        # reset a raw-bts anchor below the cap forever, so the force never fires.
        # hbts is INHERITED from the prior PENDING entry (an un-drained chain).
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        e1 = compact.load_compact_requests(self.reqp)[self.SID]
        self.assertEqual(e1["hbts"], int(T), "fresh chain -> hbts = now")
        # A genuine NEW boundary 900s later, STILL pending (never delivered):
        compact.record_compact_request(self.SID, self.CWD, now=T + 900,
                                       path=self.reqp, origin="self-callback")
        e2 = compact.load_compact_requests(self.reqp)[self.SID]
        self.assertEqual(e2["bts"], int(T + 900),
                         "bts is the per-record boundary (HOLD-log observability)")
        self.assertEqual(e2["hbts"], int(T),
                         "hbts INHERITED from the prior held chain — the clock "
                         "is NOT reset by a re-record")

    def test_844_hbts_resets_on_a_fresh_chain_after_clear(self):
        # A delivered/expired compact CLEARS the request (terminal), so the next
        # record starts a FRESH hbts — the chain resets exactly on an actual
        # delivery/clear, never on a mere re-record.
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        compact.clear_compact_request(self.SID, path=self.reqp)
        compact.record_compact_request(self.SID, self.CWD, now=T + 900,
                                       path=self.reqp, origin="self-callback")
        e = compact.load_compact_requests(self.reqp)[self.SID]
        self.assertEqual(e["hbts"], int(T + 900),
                         "a fresh chain after a clear resets hbts to now")

    def test_844_cap_surfaced_in_dry_run(self):
        proj = self._dir()
        T = 1_000_000.0
        compact.record_compact_request(self.SID, self.CWD, now=T,
                                       path=self.reqp, origin="self-callback")
        tmux = DeliverCompactFakeTmux(
            [("%9", "claude", self.CWD, "111")], CB_IDLE_CAP)
        logs = compact.compact_sweep(
            T + 100, run=tmux, dry_run=True, projects_dir=proj,
            requests_path=self.reqp, delivered_path=self.delp)
        self.assertTrue(any("live-hold cap=" in ln for ln in logs),
                        "the dry-run sweep must surface the canonical cap: %r"
                        % logs)


if __name__ == "__main__":
    unittest.main()
