"""#855 RECURRENCE (po v0.1.140) — a SECOND `/compact` 215 s after the first,
on an already-compacted boundary.

ONE `## ✅ Work Complete` boundary produces TWO `compact-request` records: the
proactive `compact-request --self` and the #411 Stop-hook backstop
`compact-request --record --origin self-callback`. The backstop record is created
AFTER the first record's delivery, so it survives it (1-pending-per-sid overwrite),
and the #855 v0.1.140 120 s `recently-compacted` veto only DEFERRED it — once past
the floor, with the 30-min cooldown superseded for the `self-callback` drained-
boundary origin (#805), the duplicate delivered a 2nd `/compact` onto a boundary CC
had already compacted.

Fix: a delivered `/compact` CONSUMES every pending record for the SAME boundary —
an early terminal `already-compacted` gate CLEARS a duplicate self-callback record
when EITHER a compaction has been OBSERVED in the transcript (`isCompactSummary`)
newer than the newest `## ✅ Work Complete` heading, OR the record's original
boundary `bts` is not newer than the last delivered ts. The defensive-sent branch
also clears the stale `compact-queued.json` marker.
"""

import sys
import json
import time
import unittest
import unittest.mock as m
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd  # noqa: E402
from watchdog import compact  # noqa: E402

CB_IDLE = "● Predošlá práca hotová.\n❯ \n  ctx ███░  caveman:lite\n"
CB_BUSY = ("● Baking…\n✳ Baking… (2m 30s · ↓ 4.1k tokens · esc to interrupt)\n"
           "  ctx ███░  caveman:lite\n")

CWD = "/home/newlevel/devel/dc855r"
SID = "sess-dc855r"


def _isolate(tc):
    d = TemporaryDirectory()
    tc.addCleanup(d.cleanup)
    reqp = Path(d.name) / "compact-requests.json"
    delp = Path(d.name) / "compact-delivered.json"
    syncp = Path(d.name) / "compact-sync.log"
    queuedp = Path(d.name) / "compact-queued.json"
    for name, path in (("compact_requests_path", reqp),
                       ("compact_delivered_path", delp),
                       ("compact_sync_log_path", syncp),
                       ("compact_queued_path", queuedp)):
        p = m.patch.object(compact, name, return_value=path)
        p.start()
        tc.addCleanup(p.stop)
    return reqp, delp, syncp, queuedp


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _transcript(proj, cwd, sid, entries):
    d = Path(proj) / wd.encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return p


def _report(ts):
    return {"type": "assistant", "timestamp": _iso(ts),
            "message": {"content": "## ✅ Work Complete\nnasadené v1\n"}}


def _compaction(ts):
    return {"type": "user", "timestamp": _iso(ts), "isCompactSummary": True,
            "message": {"content": "This session is being continued…"}}


class _StaticRun:
    """Serves ONE pane and a static capture; records every send-keys argv."""

    def __init__(self, cwd, capture):
        self.cwd = cwd
        self.capture = capture
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "%9\tclaude\t" + self.cwd + "\t111"
        if "display-message" in j:
            return "0" if argv[-1] == "#{pane_in_mode}" else "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            return ""
        if "capture-pane" in j:
            return self.capture
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class _SubmitThenBusyRun:
    """BARE (idle) until the `/compact` Enter lands, then renders `busy` — the
    residual t0->t+1s queue race `_compact_post_send_classify` classifies queued."""

    def __init__(self, cwd, busy):
        self.cwd = cwd
        self.busy = busy
        self.submitted = False
        self.sent = []

    def __call__(self, argv, timeout=8):
        j = " ".join(argv)
        if "list-panes" in j:
            return "%9\tclaude\t" + self.cwd + "\t111"
        if "display-message" in j:
            return "0" if argv[-1] == "#{pane_in_mode}" else "sess:0.0"
        if "send-keys" in j:
            self.sent.append(argv)
            if argv[-1] == "Enter":
                self.submitted = True
            return ""
        if "capture-pane" in j:
            return self.busy if self.submitted else CB_IDLE
        return ""

    def typed(self):
        return [a[-1] for a in self.sent if "-l" in a]


class TestBoundaryAlreadyCompactedHelper(unittest.TestCase):
    """The structured "boundary already compacted" reader — a compaction observed
    newer than the newest `## ✅ Work Complete` heading proves a duplicate."""

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_compaction_newer_than_report_is_already_compacted(self):
        proj = self._dir()
        t = 2_000_000.0
        _transcript(proj, CWD, SID, [_report(t), _compaction(t + 5)])
        self.assertTrue(
            compact._compact_boundary_already_compacted(CWD, SID, projects_dir=proj))

    def test_report_newer_than_compaction_is_not(self):
        proj = self._dir()
        t = 2_000_000.0
        _transcript(proj, CWD, SID, [_compaction(t), _report(t + 5)])
        self.assertFalse(
            compact._compact_boundary_already_compacted(CWD, SID, projects_dir=proj))

    def test_no_compaction_observed_is_not(self):
        proj = self._dir()
        t = 2_000_000.0
        _transcript(proj, CWD, SID, [_report(t)])
        self.assertFalse(
            compact._compact_boundary_already_compacted(CWD, SID, projects_dir=proj))

    def test_missing_transcript_is_not(self):
        proj = self._dir()
        self.assertFalse(
            compact._compact_boundary_already_compacted(CWD, SID, projects_dir=proj))


class TestAlreadyCompactedIsTerminal(unittest.TestCase):
    def test_already_compacted_is_a_terminal_word(self):
        # A consumed duplicate must be CLEARED by the caller, not left pending.
        self.assertIn("already-compacted", compact._COMPACT_TERMINAL_WORDS)

    def test_already_compacted_is_not_hold_extend(self):
        # A duplicate is discarded, never a held boundary refreshing ts.
        self.assertNotIn("already-compacted", compact._COMPACT_HOLD_EXTEND_WORDS)


class TestRecurrenceConsumesDuplicate(unittest.TestCase):
    """The 215 s recurrence: a self-callback backstop record for an
    already-compacted boundary is CONSUMED, never delivered — even PAST the 120 s
    veto window, even with the 30-min cooldown superseded for self-callback."""

    def setUp(self):
        self.reqp, self.delp, self.syncp, self.queuedp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_second_record_past_veto_is_consumed_not_delivered(self):
        proj = self._dir()
        now = 3_000_000.0
        # ticket A's report, then CC's compaction — both BEFORE the 2nd record.
        _transcript(proj, CWD, SID, [_report(now - 230), _compaction(now - 220)])
        # SEND #1 delivered 215 s ago (past the 120 s recently-compacted floor).
        compact.mark_compact_delivery_ts(SID, now=now - 215, path=self.delp)
        # the #411 backstop's duplicate record, created AFTER SEND #1.
        compact.record_compact_request(SID, CWD, now=now - 210, path=self.reqp,
                                       origin="self-callback")
        run = _StaticRun(CWD, CB_IDLE)
        logs = compact.compact_sweep(now, run=run, projects_dir=proj,
                                     requests_path=self.reqp,
                                     delivered_path=self.delp)
        self.assertEqual(run.typed(), [],
                         "NO 2nd /compact onto an already-compacted boundary")
        self.assertNotIn(SID, compact.load_compact_requests(self.reqp),
                         "the duplicate record must be CLEARED, not left pending")
        self.assertTrue(any("already-compacted" in ln for ln in logs), logs)

    def test_genuine_new_boundary_after_compaction_is_not_consumed(self):
        # Gate safety: a report NEWER than the last compaction is a fresh boundary
        # awaiting its own compact — it must NOT be consumed as a duplicate.
        proj = self._dir()
        now = 3_000_000.0
        _transcript(proj, CWD, SID,
                    [_compaction(now - 300), _report(now - 40)])
        compact.mark_compact_delivery_ts(SID, now=now - 30, path=self.delp)
        compact.record_compact_request(SID, CWD, now=now - 20, path=self.reqp,
                                       origin="self-callback")
        run = _StaticRun(CWD, CB_IDLE)
        logs = compact.compact_sweep(now, run=run, projects_dir=proj,
                                     requests_path=self.reqp,
                                     delivered_path=self.delp)
        # within 120 s of the prior delivery -> the veto DEFERS it (kept pending);
        # it is NEVER consumed as an already-compacted duplicate.
        self.assertFalse(any("already-compacted" in ln for ln in logs), logs)
        self.assertIn(SID, compact.load_compact_requests(self.reqp),
                      "a genuine new boundary must stay pending (defer), not clear")

    def test_genuine_boundary_past_veto_delivers(self):
        # Past the 120 s veto, a genuine new boundary (report NEWER than the last
        # compaction) with an older compaction present must DELIVER — the transcript
        # gate must not over-consume a real boundary once the floor lifts.
        proj = self._dir()
        now = 3_000_000.0
        _transcript(proj, CWD, SID,
                    [_compaction(now - 400), _report(now - 40)])
        compact.mark_compact_delivery_ts(SID, now=now - 200, path=self.delp)
        compact.record_compact_request(SID, CWD, now=now - 20, path=self.reqp,
                                       origin="self-callback")
        run = _StaticRun(CWD, CB_IDLE)
        logs = compact.compact_sweep(now, run=run, projects_dir=proj,
                                     requests_path=self.reqp,
                                     delivered_path=self.delp)
        self.assertEqual(run.typed(), ["/compact"],
                         "a genuine boundary past the veto must be delivered")
        self.assertTrue(any("-> sent" in ln for ln in logs), logs)


class TestSelfSyncAttemptSafety(unittest.TestCase):
    """The transcript "compaction-observed" signal must NOT consume a FRESH record
    on `--self`'s own mid-turn sync attempt (`from_sweep=False`): production runs
    `compact-request --self` as the LAST tool call BEFORE the report is written, so
    at that moment the transcript's newest report is the PREVIOUS cycle's (older than
    the previous compaction) — consuming it would wrongly clear the genuine record.
    The timestamp belt (`bts<=delivered`) still runs on the sync path."""

    def setUp(self):
        self.reqp, self.delp, self.syncp, self.queuedp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_sync_attempt_does_not_consume_a_fresh_record(self):
        proj = self._dir()
        now = 5_000_000.0
        # the PREVIOUS cycle's report + its compaction; the CURRENT report is not
        # written yet (the --self ordering). The prior compact is the last delivery.
        _transcript(proj, CWD, SID,
                    [_report(now - 300), _compaction(now - 290)])
        compact.mark_compact_delivery_ts(SID, now=now - 290, path=self.delp)
        run = _StaticRun(CWD, CB_BUSY)   # mid-turn — the --self attempt runs busy
        word = compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=now, request_bts=int(now),
            from_sweep=False)
        self.assertNotEqual(word, "already-compacted",
                            "the fresh --self record must not be consumed mid-turn")
        self.assertEqual(word, "skip:turn-running")

    def test_sweep_consumes_the_same_state_once_the_report_lands(self):
        # The COMPLEMENT: on the periodic sweep (from_sweep=True), the boundary's own
        # report IS in the transcript (newer than any compaction) -> not consumed;
        # only a genuinely-already-compacted state (report older than compaction) is.
        proj = self._dir()
        now = 5_000_000.0
        _transcript(proj, CWD, SID,
                    [_report(now - 300), _compaction(now - 290)])
        self.assertTrue(compact._compact_duplicate_consume_reason(
            SID, CWD, self.delp, int(now - 250), projects_dir=proj,
            from_sweep=True) == "compaction-observed")
        self.assertEqual(compact._compact_duplicate_consume_reason(
            SID, CWD, self.delp, int(now - 250), projects_dir=proj,
            from_sweep=False), "",
            "the same state is NOT consumed on the sync path")


class TestLockNoStaleDuplicateSurvives(unittest.TestCase):
    """After a delivery, no pending record whose ORIGINAL boundary `bts` is not
    newer than the delivered ts may survive for that sid (the timestamp belt)."""

    def setUp(self):
        self.reqp, self.delp, self.syncp, self.queuedp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_record_bts_le_delivered_is_cleared_no_send(self):
        proj = self._dir()
        now = 4_000_000.0
        # a minimal transcript (no compaction observed) isolates the TIMESTAMP belt.
        _transcript(proj, CWD, SID, [_report(now - 40)])
        compact.mark_compact_delivery_ts(SID, now=now - 30, path=self.delp)
        # a duplicate record whose boundary predates the delivery.
        compact.record_compact_request(SID, CWD, now=now - 40, path=self.reqp,
                                       origin="self-callback")
        run = _StaticRun(CWD, CB_IDLE)
        compact.compact_sweep(now, run=run, projects_dir=proj,
                              requests_path=self.reqp, delivered_path=self.delp)
        self.assertEqual(run.typed(), [], "no 2nd /compact for a stale duplicate")
        self.assertNotIn(SID, compact.load_compact_requests(self.reqp),
                         "a record with bts <= delivered ts must not survive")


class TestDefensiveClearsQueued(unittest.TestCase):
    """The QUEUED-DEFENSIVE (treat-as-sent) branch clears a stale
    `compact-queued.json` marker — the store the recurrence found stale."""

    def setUp(self):
        self.reqp, self.delp, self.syncp, self.queuedp = _isolate(self)
        p = m.patch("time.sleep", lambda *a, **k: None)
        p.start()
        self.addCleanup(p.stop)

    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def test_defensive_sent_clears_stale_queued_marker(self):
        proj = self._dir()
        _transcript(proj, CWD, SID, [_report(time.time() - 10)])
        # a stale queued marker left by an earlier incident.
        compact.mark_compact_queued_ts(SID, now=time.time() - 9000,
                                       path=self.queuedp)
        self.assertIsNotNone(compact.compact_queued_since(SID, path=self.queuedp))
        run = _SubmitThenBusyRun(CWD, CB_BUSY)
        word = compact.deliver_compact(
            SID, CWD, origin="self-callback", run=run, projects_dir=proj,
            delivered_path=self.delp, now=time.time())
        self.assertEqual(word, "sent")
        self.assertIsNone(compact.compact_queued_since(SID, path=self.queuedp),
                          "the defensive-sent branch must clear the stale marker")


if __name__ == "__main__":
    unittest.main()
