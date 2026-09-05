"""#547 — mechanical W/ops-wait re-check nudge.

The W (`ops-wait`) bucket's re-entry was PROSE-ONLY: nothing re-read the
`--ops-wait` members, the /goal evaluator reads only the transcript, and
`_watchdog_backlog_fetch` runs only `--count` — so an armed loop parking on W had
no trigger to re-check the external state (montalu5: 13 tickets, replies/release
arrived long ago, the loop stayed blind).

REGRESSION (`TestLaneSweepWiring`, RED against the pre-wiring tree): driving
`goal_lane_sweep` over an armed pane whose repo has W members parked past the
cadence produces NO re-check nudge at all. GREEN (once the sweep calls
`goal_ops_wait_recheck`): the nudge is delivered into the session and the per-sid
state advances. The pure decider, the reaper, and the orchestrator are locked
directly too.
"""

import json
import os
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from watchdog import goal
from watchdog import ops_wait_recheck as owr
from watchdog import session_status as ss

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

NOW = 1_000_000
DAY = 24 * 3600
CAD = 1000   # a small test cadence


# --------------------------------------------------------------------------- #
# 1. Pure decider — the safety-critical verdict logic.
# --------------------------------------------------------------------------- #

class TestRecheckDecision(unittest.TestCase):
    # NOTE (#552): the decider signature is now `(rec, i_count, w_members, now,
    # cadence)` — the W-only tests below pin i_count=0 (determined-empty I) so
    # they lock the SAME W behaviour #547 shipped; the #552 I-direction is
    # covered by TestRecheckDecisionIDirection below.
    def test_undetermined_members_skip_and_never_mutate(self):
        # W=None (fetch failed/refused) + I determined-0 -> skip, rec returned
        # UNCHANGED, no nudge (an undetermined half never clears/nudges).
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 5 * DAY}
        action, out, reason = owr._recheck_decision(rec, 0, None, NOW, CAD)
        self.assertEqual(action, "skip")
        self.assertEqual(reason, "undetermined")
        self.assertIs(out, rec)

    def test_empty_members_clear(self):
        # BOTH halves determined-empty (I==0 AND W==[]) -> partition drained.
        action, out, reason = owr._recheck_decision(
            {"first_seen": NOW - 5 * DAY}, 0, [], NOW, CAD)
        self.assertEqual(action, "clear")
        self.assertIsNone(out)

    def test_first_sight_seeds_first_seen_and_waits(self):
        # A never-seen W set (I==0): seed first_seen=now, WAIT (grace — never nag
        # a session about a partition it just arrived at).
        action, out, reason = owr._recheck_decision({}, 0, [43, 41], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(reason, "grace")
        self.assertEqual(out["first_seen"], NOW)
        self.assertIsNone(out["last_nudge"])
        # #594: per-ticket W first-seen map — each new member seeded to now
        self.assertEqual(out["w_seen"], {"41": NOW, "43": NOW})
        self.assertEqual(out["sig"], "i:0|w:41,43")   # partition sig, numeric sort

    def test_past_cadence_from_first_seen_nudges(self):
        # first_seen older than the cadence, never nudged -> DUE.
        rec = {"first_seen": NOW - 2 * CAD}
        action, out, reason = owr._recheck_decision(rec, 0, [41], NOW, CAD)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "due")
        # a "nudge" verdict is an INTENT — last_nudge is preserved (the caller
        # advances it only on a confirmed submit), never set here.
        self.assertIsNone(out["last_nudge"])
        self.assertEqual(out["first_seen"], NOW - 2 * CAD)

    def test_within_reping_window_waits(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - CAD // 2}
        action, out, _ = owr._recheck_decision(rec, 0, [41], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(out["last_nudge"], NOW - CAD // 2)

    def test_past_reping_window_nudges(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 2 * CAD}
        action, out, _ = owr._recheck_decision(rec, 0, [41], NOW, CAD)
        self.assertEqual(action, "nudge")

    def test_malformed_rec_is_seeded_fresh(self):
        action, out, _ = owr._recheck_decision(None, 0, [7], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(out["first_seen"], NOW)


class TestRecheckDecisionIDirection(unittest.TestCase):
    """#552 — the folded-in I→W/U direction: `I > 0` alone is something to audit,
    even when W is empty; the partition only `clear`s when BOTH halves are
    determined-empty; an undetermined half never clears/nudges."""

    def test_i_positive_w_empty_past_cadence_nudges(self):
        # The montalu3 shape: I inflated, W empty, past cadence -> nudge (the
        # pre-#552 tree would have `clear`ed here and never nudged).
        rec = {"first_seen": NOW - 2 * CAD}
        action, out, reason = owr._recheck_decision(rec, 5, [], NOW, CAD)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "due")
        # W empty -> no per-ticket W map; sig names the I half.
        self.assertIsNone(out["w_seen"])
        self.assertEqual(out["sig"], "i:5|w:")

    def test_i_positive_w_empty_first_sight_waits(self):
        action, out, _ = owr._recheck_decision({}, 5, [], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(out["first_seen"], NOW)

    def test_i_and_w_both_present_nudges_and_seeds_w_anchor(self):
        rec = {"first_seen": NOW - 2 * CAD}
        action, out, _ = owr._recheck_decision(rec, 3, [41, 43], NOW, CAD)
        self.assertEqual(action, "nudge")
        # #594: both members first-seen this sweep -> seeded to now
        self.assertEqual(out["w_seen"], {"41": NOW, "43": NOW})
        self.assertEqual(out["sig"], "i:3|w:41,43")

    def test_w_park_anchor_preserved_per_ticket_and_new_member_fresh(self):
        # #594: a continuously-parked member keeps its ORIGINAL per-ticket
        # anchor (truthful age), while a member NEWLY appearing in W is seeded
        # fresh to `now` — so the new one reads ~0h even alongside an old one,
        # the exact bug the single partition-level anchor caused.
        rec = {"first_seen": NOW - 5 * DAY, "w_seen": {"41": NOW - 3 * CAD},
               "last_nudge": None}
        action, out, _ = owr._recheck_decision(rec, 2, [41, 43], NOW, CAD)
        self.assertEqual(action, "nudge")
        self.assertEqual(out["w_seen"]["41"], NOW - 3 * CAD)   # preserved
        self.assertEqual(out["w_seen"]["43"], NOW)             # fresh member

    def test_w_seen_drops_a_member_that_left_w(self):
        # A member no longer in the W set is dropped from `w_seen` (so a
        # re-added ticket later gets a FRESH anchor from its re-entry).
        rec = {"first_seen": NOW - 5 * DAY,
               "w_seen": {"41": NOW - 3 * CAD, "43": NOW - 2 * CAD},
               "last_nudge": None}
        action, out, _ = owr._recheck_decision(rec, 2, [41], NOW, CAD)
        self.assertEqual(out["w_seen"], {"41": NOW - 3 * CAD})

    def test_w_park_anchor_dropped_when_w_empties(self):
        # W drains to [] but I still >0 -> keep auditing (I), drop the W map.
        rec = {"first_seen": NOW - 5 * DAY, "w_seen": {"41": NOW - 3 * CAD}}
        action, out, _ = owr._recheck_decision(rec, 2, [], NOW, CAD)
        self.assertIn(action, ("nudge", "wait"))
        self.assertIsNone(out["w_seen"])

    def test_i_zero_w_empty_clears(self):
        action, out, _ = owr._recheck_decision(
            {"first_seen": NOW - 5 * DAY}, 0, [], NOW, CAD)
        self.assertEqual(action, "clear")
        self.assertIsNone(out)

    def test_i_undetermined_w_empty_skips_not_clears(self):
        # I None (awaiting-user pane) + W determined-empty -> cannot confirm the
        # partition is drained, so SKIP (leave the rec), never clear/nudge.
        rec = {"first_seen": NOW - 5 * DAY}
        action, out, reason = owr._recheck_decision(rec, None, [], NOW, CAD)
        self.assertEqual(action, "skip")
        self.assertEqual(reason, "undetermined")
        self.assertIs(out, rec)

    def test_both_undetermined_skips(self):
        rec = {"first_seen": NOW - 5 * DAY}
        action, out, _ = owr._recheck_decision(rec, None, None, NOW, CAD)
        self.assertEqual(action, "skip")
        self.assertIs(out, rec)

    def test_i_positive_w_undetermined_nudges_i_only(self):
        # I>0 is a POSITIVE signal even when the W fetch is undetermined -> nudge
        # the I direction (the W clause is simply absent from the text).
        rec = {"first_seen": NOW - 2 * CAD}
        action, out, _ = owr._recheck_decision(rec, 4, None, NOW, CAD)
        self.assertEqual(action, "nudge")
        self.assertEqual(out["sig"], "i:4|w:?")


class _CountingFetch:
    """A fetch that records how many times it was actually invoked — the teeth
    for the #547-review cache fix (a raw per-sweep fetch would call it N times)."""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self, cwd):
        self.calls += 1
        return self.result


class TestCachedOpsWait(unittest.TestCase):
    def test_second_read_within_ttl_hits_cache_no_refetch(self):
        # THE fix's teeth: two reads inside the TTL -> ONE fetch. A raw
        # (uncached) fetch would call it twice — the per-sweep regression.
        f = _CountingFetch([41, 43])
        state = {}
        m1 = owr._cached_ops_wait("/r", f, state, NOW)
        m2 = owr._cached_ops_wait("/r", f, state, NOW + 60)   # next sweep
        self.assertEqual(m1, [41, 43])
        self.assertEqual(m2, [41, 43])
        self.assertEqual(f.calls, 1, "second read within TTL must hit the cache")

    def test_read_past_ttl_refetches(self):
        f = _CountingFetch([41])
        state = {}
        owr._cached_ops_wait("/r", f, state, NOW, ttl=100)
        owr._cached_ops_wait("/r", f, state, NOW + 200, ttl=100)   # past TTL
        self.assertEqual(f.calls, 2)

    def test_none_failure_cached_only_for_fail_ttl(self):
        f = _CountingFetch(None)
        state = {}
        owr._cached_ops_wait("/r", f, state, NOW, fail_ttl=30)
        # within fail_ttl -> cached None, no refetch
        owr._cached_ops_wait("/r", f, state, NOW + 10, fail_ttl=30)
        self.assertEqual(f.calls, 1)
        # past fail_ttl -> refetch (a transient gh hiccup re-checks soon)
        owr._cached_ops_wait("/r", f, state, NOW + 40, fail_ttl=30)
        self.assertEqual(f.calls, 2)

    def test_wired_none_returns_none_no_cache_write(self):
        state = {}
        self.assertIsNone(owr._cached_ops_wait("/r", None, state, NOW))
        self.assertNotIn("ops_wait_cache", state)

    def test_fetch_exception_is_none_and_cached(self):
        def boom(cwd):
            raise RuntimeError("gh down")
        state = {}
        self.assertIsNone(owr._cached_ops_wait("/r", boom, state, NOW))
        self.assertIsNone(state["ops_wait_cache"]["/r"]["members"])

    def test_malformed_entry_reads_as_expired(self):
        f = _CountingFetch([7])
        state = {"ops_wait_cache": {"/r": {"ts": "notanumber", "members": [1]}}}
        m = owr._cached_ops_wait("/r", f, state, NOW)
        self.assertEqual(m, [7])   # refetched, not the malformed [1]
        self.assertEqual(f.calls, 1)

    def test_per_cwd_keyed(self):
        f = _CountingFetch([1])
        state = {}
        owr._cached_ops_wait("/a", f, state, NOW)
        owr._cached_ops_wait("/b", f, state, NOW)   # different repo -> own fetch
        self.assertEqual(f.calls, 2)


class TestHelpers(unittest.TestCase):
    def test_fmt_age_hours_then_days(self):
        self.assertEqual(owr._fmt_age(3 * 3600), "~3h")
        self.assertEqual(owr._fmt_age(47 * 3600), "~47h")
        self.assertEqual(owr._fmt_age(3 * DAY), "~3d")

    def test_sig_numeric_sorted(self):
        self.assertEqual(owr._sig([43, 7, 41]), "7,41,43")

    def test_members_line(self):
        self.assertEqual(owr._members_line([43, 41]), "#41 #43")

    def test_cadence_floored(self):
        with m.patch.dict(os.environ,
                          {"AIRULESET_OPS_WAIT_RECHECK_CADENCE_S": "60"}):
            self.assertEqual(owr._cadence(), owr.OPS_WAIT_RECHECK_MIN_S)
        with m.patch.dict(os.environ,
                          {"AIRULESET_OPS_WAIT_RECHECK_CADENCE_S": "999999"}):
            self.assertEqual(owr._cadence(), 999999)


# --------------------------------------------------------------------------- #
# 2. Orphan reaper (#531) — mirror of _prune_goal_lane_orphans.
# --------------------------------------------------------------------------- #

class TestPruneOpsWaitOrphans(unittest.TestCase):
    def test_aged_not_visited_reaped(self):
        recs = {"gone": {"lts": NOW - 3 * DAY, "first_seen": NOW - 4 * DAY}}
        owr._prune_ops_wait_orphans(recs, visited_sids=set(), now=NOW)
        self.assertEqual(recs, {})

    def test_visited_never_reaped_even_stale(self):
        recs = {"live": {"lts": NOW - 3 * DAY}}
        owr._prune_ops_wait_orphans(recs, visited_sids={"live"}, now=NOW)
        self.assertIn("live", recs)

    def test_recent_not_visited_kept(self):
        recs = {"recent": {"lts": NOW - 60}}
        owr._prune_ops_wait_orphans(recs, visited_sids=set(), now=NOW)
        self.assertIn("recent", recs)

    def test_malformed_not_visited_reaped(self):
        recs = {"nodict": "junk", "nots": {"first_seen": 1}, "badts": {"lts": "x"}}
        owr._prune_ops_wait_orphans(recs, visited_sids=set(), now=NOW)
        self.assertEqual(recs, {})

    def test_future_lts_kept(self):
        recs = {"skew": {"lts": NOW + 5000}}
        owr._prune_ops_wait_orphans(recs, visited_sids=set(), now=NOW)
        self.assertIn("skew", recs)

    def test_non_dict_never_raises(self):
        owr._prune_ops_wait_orphans(None, visited_sids=set(), now=NOW)
        owr._prune_ops_wait_orphans([1, 2], visited_sids=set(), now=NOW)

    def test_json_round_trip(self):
        state = {"w": {"gone": {"lts": NOW - 3 * DAY},
                       "recent": {"lts": NOW - 60}}}
        recs = json.loads(json.dumps(state))["w"]
        owr._prune_ops_wait_orphans(recs, visited_sids=set(), now=NOW)
        self.assertNotIn("gone", recs)
        self.assertIn("recent", recs)


# --------------------------------------------------------------------------- #
# 3. Orchestrator — the fetch/decide/deliver path, direct.
# --------------------------------------------------------------------------- #

class _OrchBase(unittest.TestCase):
    CWD = "/home/newlevel/devel/wrecheck"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-547-orch")
        self.sid = self.tpath.stem

    def _tmux(self, **kw):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath, **kw)

    def _run(self, wrecs, fetch, tmux, *, dry_run=False, handled=None, state=None,
             i_count=0):
        # i_count defaults to 0 (determined-empty I) so the pre-#552 W-only tests
        # below lock the SAME behaviour; the #552 I direction passes i_count>0.
        return owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            dry_run, handled, ops_wait_fetch=fetch,
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=i_count)


class TestOrchestrator(_OrchBase):
    def test_fetch_error_is_undetermined_no_state_change(self):
        # A raising fetch is absorbed by `_cached_ops_wait` (-> None), so the
        # orchestrator sees undetermined -> skip, with NO nudge and NO state
        # write. (The orchestrator's own `skip:fetch-error` branch remains a
        # defensive backstop for a `_cached_ops_wait` raise.)
        wrecs = {}

        def boom(cwd):
            raise RuntimeError("gh down")

        logs = self._run(wrecs, boom, self._tmux())
        self.assertTrue(any("skip:" in ln for ln in logs))
        self.assertFalse(any("nudge" in ln for ln in logs))
        self.assertEqual(wrecs, {})

    def test_none_members_undetermined(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(wrecs, lambda cwd: None, self._tmux())
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        # state unchanged (skip returns the rec as-is, no write)
        self.assertEqual(wrecs[self.sid], {"first_seen": NOW - 5 * DAY})

    def test_empty_members_clear_pops_rec(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(wrecs, lambda cwd: [], self._tmux())
        self.assertTrue(any("clear" in ln for ln in logs))
        self.assertNotIn(self.sid, wrecs)

    def test_first_sight_waits_and_seeds(self):
        wrecs = {}
        logs = self._run(wrecs, lambda cwd: [41, 43], self._tmux())
        self.assertTrue(any("-> wait" in ln for ln in logs))
        self.assertEqual(wrecs[self.sid]["first_seen"], NOW)
        self.assertEqual(wrecs[self.sid]["lts"], NOW)
        self.assertIsNone(wrecs[self.sid]["last_nudge"])

    def test_due_dry_run_would_nudge_no_mutation(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(wrecs, lambda cwd: [41], tmux, dry_run=True)
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])           # nothing typed
        self.assertIsNone(wrecs[self.sid]["last_nudge"])   # no mutation
        self.assertNotIn("lts", wrecs[self.sid])

    def test_due_real_delivery_types_and_advances(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = set()
        state = {}
        logs = self._run(wrecs, lambda cwd: [41, 43], tmux,
                         handled=handled, state=state)
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("W=2", typed)        # #714 compact COUNT, not #41 #43
        self.assertEqual(wrecs[self.sid]["last_nudge"], NOW)
        self.assertIn(self.sid, handled)   # claims the pane vs other jobs

    def test_due_but_already_handled_defers(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = {self.sid}
        logs = self._run(wrecs, lambda cwd: [41], tmux, handled=handled)
        self.assertTrue(any("skip:already-handled" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(wrecs[self.sid]["last_nudge"])

    def test_swallowed_submit_does_not_advance_last_nudge(self):
        # A swallowed Enter -> send_verified returns False -> last_nudge NOT
        # advanced (retry next sweep), the pane NOT claimed in handled.
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux(enters_swallowed=5)
        handled = set()
        logs = self._run(wrecs, lambda cwd: [41], tmux, handled=handled,
                         state={})
        self.assertTrue(any("submit-unverified" in ln for ln in logs))
        self.assertIsNone(wrecs[self.sid]["last_nudge"])
        self.assertNotIn(self.sid, handled)


class TestBusyPaneRefire594(_OrchBase):
    """#594 root cause A — a nudge DELIVERED once (box cleared, message
    submitted/queued into an actively-cycling armed `/goal` loop) whose
    transcript confirmation RACED must NOT re-deliver on every later sweep."""

    def _tmux_unconfirmed(self):
        # transcript_path=None models the busy-pane / cycling-armed-loop case:
        # the Enter SUBMITS (clears the box — delivered/queued) but writes NO
        # `user` turn to the transcript in the window, so `_await_submit_confirmed`
        # fails and `send_verified` returns False despite the delivery.
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=None)

    def test_delivered_unconfirmed_holds_dedup_across_sweeps(self):
        # RED before the fix: 3 sweeps -> 3 deliveries (last_nudge never advances
        # because it was recorded ONLY on a transcript-CONFIRMED submit). GREEN:
        # ONE delivery, last_nudge advanced, the busy-pane retry spam stopped.
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        state = {}
        tmux = self._tmux_unconfirmed()
        for _ in range(3):
            owr.goal_ops_wait_recheck(
                NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
                False, set(), ops_wait_fetch=lambda cwd: [41], state=state,
                sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=0)
        deliveries = [t for t in tmux.typed_texts() if "stuck-check" in t]
        self.assertEqual(len(deliveries), 1,
                         "a delivered-but-unconfirmed nudge must NOT re-deliver "
                         "across sweeps (busy-pane retry spam, #594): got %d"
                         % len(deliveries))
        self.assertEqual(wrecs[self.sid]["last_nudge"], NOW)

    def test_genuine_swallow_still_retries(self):
        # The #594 fix must NOT regress the genuine-swallow retry: a swallowed
        # Enter (text stuck -> corrective -> undone, NEVER delivered) leaves
        # last_nudge unadvanced so the nudge retries next sweep.
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux(enters_swallowed=99)
        logs = self._run(wrecs, lambda cwd: [41], tmux, handled=set(), state={})
        self.assertTrue(any("submit-unverified" in ln for ln in logs))
        self.assertIsNone(wrecs[self.sid]["last_nudge"])


class TestParkedAgeSummary714(_OrchBase):
    """#714 SUPERSEDES #594's per-member W-park age rendering: the compact nudge
    is a COUNT, not a per-member enumeration, so it renders NO per-member ages at
    all (the actionable freshness signal is the `stale!` tag in `slice-quals
    --ops-wait`, which the session runs itself). The decider still tracks `w_seen`
    per-ticket (its tests unchanged) — the nudge just no longer prints it."""

    def test_nudge_is_a_count_with_no_per_member_age(self):
        wrecs = {self.sid: {"first_seen": NOW - DAY, "last_nudge": None}}
        tmux = self._tmux()
        self._run(wrecs, lambda cwd: [3076], tmux, handled=set(), state={})
        typed = "".join(tmux.typed_texts())
        self.assertIn("W=1", typed)          # a compact count
        self.assertNotIn("#3076", typed)     # no member enumeration
        self.assertNotIn("~0h", typed)       # no per-member age rendered
        self.assertNotIn("~24h", typed)


class TestNudgeText(unittest.TestCase):
    """#552 — the composed partition-audit text: the I clause when I>0, the W
    clause (names + truthful park age) when W non-empty, both when both, and a
    clean degrade to #547's W-only nudge when I==0."""

    def test_i_only_carries_reaudit_clause_no_w(self):
        t = owr._nudge_text(3, [], NOW, None)
        self.assertIn("stuck-check:", t)
        self.assertIn("re-audituj", t)            # the I->W/U instruction
        self.assertIn("#526/#539", t)
        self.assertNotIn("parknuté `ops-wait`", t)   # no W clause when W empty

    def test_w_only_is_a_compact_count(self):
        # #714: W-only nudge is the W trigger + COUNT, no I clause, no member
        # enumeration, no per-member ages.
        t = owr._nudge_text(0, [41, 43], NOW)
        self.assertIn("stuck-check:", t)
        self.assertNotIn("re-audituj", t)         # no I clause when I==0
        self.assertIn("W=2", t)                   # the count
        self.assertNotIn("#41", t)                # no member enumeration
        self.assertNotIn("#43", t)

    def test_both_directions_ride_one_ping(self):
        t = owr._nudge_text(2, [41], NOW)
        self.assertIn("re-audituj", t)            # I clause
        self.assertIn("W=1", t)                   # W count
        self.assertIn("supervisor", t)            # label-change ownership note

    def test_w_count_scales_not_enumerates(self):
        # #714: any number of W members renders a COUNT, never per-member `#N`.
        t = owr._nudge_text(0, [7, 9, 3498, 4990], NOW)
        self.assertIn("W=4", t)
        for n in ("#7", "#9", "#3498", "#4990"):
            self.assertNotIn(n, t)

    def test_nudge_never_exceeds_cap(self):
        # every shape stays bounded (#714).
        t = owr._nudge_text(41, list(range(3498, 3498 + 53)), NOW)
        self.assertLessEqual(len(t), owr.NUDGE_MAX_CHARS)


class TestOrchestratorIDirection(_OrchBase):
    """#552 — the orchestrator's I direction end-to-end (fetch/decide/deliver)."""

    def test_i_positive_w_empty_due_delivers_reaudit_nudge(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        handled = set()
        logs = self._run(wrecs, lambda cwd: [], tmux, handled=handled,
                         state={}, i_count=4)
        self.assertTrue(any("ops-wait-recheck nudge" in ln for ln in logs))
        # chunks CONCATENATED (send_verified splits mid-word) — see the wiring
        # test's note; a " "-join would inject a chunk-boundary space.
        typed = "".join(tmux.typed_texts())
        self.assertIn("re-audituj", typed)
        self.assertEqual(wrecs[self.sid]["last_nudge"], NOW)
        self.assertIn(self.sid, handled)

    def test_i_zero_w_empty_clears(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(wrecs, lambda cwd: [], self._tmux(), i_count=0)
        self.assertTrue(any("clear" in ln for ln in logs))
        self.assertNotIn(self.sid, wrecs)

    def test_i_undetermined_w_empty_skips_leaves_rec(self):
        # An awaiting-user pane (i_count None) with no W parked -> skip, rec kept.
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY}}
        logs = self._run(wrecs, lambda cwd: [], self._tmux(), i_count=None)
        self.assertTrue(any("skip:undetermined" in ln for ln in logs))
        self.assertIn(self.sid, wrecs)

    def test_i_positive_w_empty_dry_run_no_mutation(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        logs = self._run(wrecs, lambda cwd: [], tmux, dry_run=True, i_count=4)
        self.assertTrue(any("WOULD-NUDGE" in ln for ln in logs))
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(wrecs[self.sid]["last_nudge"])


# --------------------------------------------------------------------------- #
# 4. Integration regression — the wiring into goal_lane_sweep.
# --------------------------------------------------------------------------- #

class TestLaneSweepWiring(unittest.TestCase):
    """RED against the pre-wiring tree: `goal_lane_sweep` produces NO re-check
    nudge for an armed pane with W members parked past the cadence. GREEN once
    the sweep calls `goal_ops_wait_recheck`."""

    CWD = "/home/newlevel/devel/wrechecklane"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)

    def _heartbeat(self, sid):
        pth = ss.status_path(sid)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(
            {"schema": 1, "sid": sid, "kind": "main", "last_turn": "stop",
             "ts": NOW, "cwd": self.CWD, "marker": "working",
             "goal_armed": True}), encoding="utf-8")

    def _armed_sweep(self, state, *, ops_wait_fetch, dry_run=False, handled=None,
                     backlog=0, authority="full"):
        proj = Path(self._proj.name)
        tpath = _write_marker_transcript(proj, self.CWD, "sess-547-lane")
        sid = tpath.stem
        old = NOW - goal.GOAL_LANE_IDLE_S - 500
        os.utime(tpath, (old, old))
        self._heartbeat(sid)
        gmarks = state.setdefault("goal_mark", {})
        gmarks[sid] = {"off": 0, "mark": {"state": "set", "ts": NOW}}
        tmux = DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)
        with m.patch("airuleset.resolve_authority", return_value=authority), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            # backlog 0 (the DEFAULT) -> the lane-occupancy nudge skips
            # (no-backlog), so this sweep's ONLY possible keystroke is the
            # partition-audit re-check under test (clean isolation from the
            # sibling lane nudge; the `handled` coordination when the lane nudge
            # DOES fire is locked by
            # TestOrchestrator.test_due_but_already_handled_defers). #618: the
            # lane nudge now runs on reduced-authority boxes too, so a caller
            # exercising the #552 I-direction (backlog>0) must ALSO seed the
            # lane cooldown (state["goal_lane"][sid] = {"llast": now-1}) so the
            # lane nudge hits its hourly-cap skip WITHOUT a keystroke or a
            # `handled` claim, isolating the partition-audit I clause.
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=dry_run,
                handled=handled, backlog_fetch=lambda cwd: backlog,
                ops_wait_fetch=ops_wait_fetch, sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_parked_w_past_cadence_is_nudged(self):
        # Pre-seed an OLD first_seen so the W set is past the cadence -> DUE.
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [41, 43])
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an armed pane with W parked past cadence must be nudged "
                      "(RED before goal_lane_sweep wires goal_ops_wait_recheck)")
        self.assertIn("W=2", typed)        # #714 compact count
        self.assertEqual(state["ops_wait_recheck"][sid]["last_nudge"], NOW)

    def test_i_only_partition_past_cadence_is_nudged(self):
        # #552 I->W/U freshness: an armed pane with I>0 but W EMPTY, past the
        # cadence, must be nudged to re-audit its I list against the #526/#539
        # parking shapes. RED on the pre-#552 tree (W==[] -> `clear` -> no
        # keystroke; the I count is never consulted). GREEN once goal_lane_sweep
        # passes glance.backlog and goal_ops_wait_recheck folds i_count into the
        # partition-audit decider. #618: the lane nudge now runs on
        # reduced-authority boxes too, and with backlog>0 + 0 workers it WOULD
        # fire and claim `handled` (deferring this recheck) — so seed the lane
        # cooldown (llast=now-1) so the lane nudge hits its hourly-cap skip with
        # no keystroke + no `handled` claim, isolating the recheck's I clause
        # (a realistic scenario: the lane nudge already fired within the hour).
        state = {"ops_wait_recheck": {
                     "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}},
                 "goal_lane": {"sess-547-lane": {"llast": NOW - 1}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [],
                                      backlog=3, authority="branch-merge")
        # send_verified chunks the payload across several `send-keys -l` calls
        # (splitting mid-word), so the faithful landed text is the chunks
        # CONCATENATED, never " "-joined (which injects a chunk-boundary space).
        typed = "".join(tmux.typed_texts())
        self.assertIn("re-audituj", typed,
                      "an armed pane with I>0 (W empty) past cadence must be "
                      "nudged to re-audit I->W/U (RED before #552 folds i_count "
                      "into the partition-audit decider)")
        self.assertIn("stuck-check:", typed)
        # #618 isolation teeth: the sibling lane-occupancy nudge must NOT have
        # fired this sweep (else it would have claimed `handled` and deferred
        # the recheck) — its delivered text is `lane-check:`.
        self.assertNotIn("lane-check:", typed)
        self.assertEqual(state["ops_wait_recheck"][sid]["last_nudge"], NOW)

    def test_no_w_members_clears_state(self):
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [])
        self.assertNotIn(sid, state["ops_wait_recheck"])

    def test_undetermined_fetch_never_nudges(self):
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: None)
        self.assertEqual(tmux.typed_texts(), [])

    def test_no_ops_wait_fetch_is_a_noop(self):
        # An unwired box (ops_wait_fetch None) never touches ops_wait state.
        state = {}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=None)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertNotIn("ops_wait_recheck", state)

    def test_gone_session_orphan_is_reaped(self):
        # A parked W rec whose session is not a live candidate this sweep + aged
        # -> reaped by the sweep's orphan prune.
        state = {"ops_wait_recheck": {
            "orphan-547": {"first_seen": NOW - 5 * DAY, "lts": NOW - 3 * DAY},
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [41])
        self.assertNotIn("orphan-547", state["ops_wait_recheck"])
        self.assertIn(sid, state["ops_wait_recheck"])

    def test_dry_run_mutates_no_state(self):
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [41],
                                      dry_run=True)
        self.assertEqual(tmux.typed_texts(), [])
        self.assertIsNone(state["ops_wait_recheck"][sid]["last_nudge"])

    def test_fetch_is_cached_across_sweeps(self):
        # #547 review: the --ops-wait fetch must be per-repo-TTL-cached, not
        # per-sweep. Two sweeps sharing state (within the TTL) -> ONE fetch.
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        f = _CountingFetch([41])
        self._armed_sweep(state, ops_wait_fetch=f)
        self._armed_sweep(state, ops_wait_fetch=f)
        self.assertEqual(f.calls, 1,
                         "the --ops-wait fetch must be cached across sweeps, "
                         "never one gh subprocess per sweep per pane")


# --------------------------------------------------------------------------- #
# 5. Doctrine content-lock (#498/#500 teeth) — the W-bucket re-entry surfaces
#    must keep pointing at the mechanism, not silently drift back to prose-only.
# --------------------------------------------------------------------------- #

_REPO = Path(__file__).resolve().parent.parent
# #859 batch 3: the W-bullet deep mechanism prose moved out of the always-on
# statusline-vocabulary module into this on-demand companion.
DEEP2 = _REPO / "skills" / "statusline-vocabulary-deep" / "DEEP-2.md"


def _line_with(text, finder):
    """The single physical line carrying `finder` (an UNIQUE operative token),
    or "" — per-line teeth for a one-line surface (#500)."""
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


def _norm_window(text, start_token, end_marker="\n- **"):
    """A whitespace-collapsed window from `start_token` to the next `end_marker`
    (or EOF) — the #500 shape for content that WRAPS across indented physical
    lines (a markdown list-item continuation)."""
    i = text.index(start_token)
    j = text.find(end_marker, i)
    return " ".join(text[i:(j if j > 0 else len(text))].split())


class TestDoctrineContentLock(unittest.TestCase):
    # UNIQUE finders (the #547 operative sentence), NEVER a token also under
    # assertion, so the finder never begs the question (#498).
    STATUS_FINDER = "W re-entry is MECHANICAL (#547)"
    SKILL_FINDER = "W re-check is now MECHANICAL (#547)"
    # Co-tokens the operative statement MUST carry — the mechanism + the
    # supervisor-only-clears invariant. A partial revert dropping the mechanism
    # sentence drops these together.
    TOKENS = ("job 20", "--ops-wait", "stuck-check:", "never auto-unlabels")

    def test_statusline_vocabulary_W_bullet_points_at_mechanism(self):
        text = DEEP2.read_text(encoding="utf-8")
        line = _line_with(text, self.STATUS_FINDER)
        self.assertTrue(line, "the W bullet must carry the #547 mechanism sentence")
        for tok in self.TOKENS:
            self.assertIn(tok, line,
                          "W bullet lost the operative token %r (#547 drift)" % tok)

    def test_autopilot_skill_W_paragraph_points_at_mechanism(self):
        text = (_REPO / "skills/autopilot/SKILL.md").read_text(encoding="utf-8")
        win = _norm_window(text, self.SKILL_FINDER)
        for tok in self.TOKENS:
            self.assertIn(tok, win,
                          "autopilot SKILL W paragraph lost %r (#547 drift)" % tok)

    # --- #552 I→W/U freshness sibling (same surfaces, same shape as #547) ---
    # The finder is UNIQUE to the #552 sentence; the co-tokens are UNIQUE to it on
    # the SAME physical statusline line (`partition-audit`/`re-audit`/`OPPOSITE
    # direction` each occur ONCE there — verified), so a partial revert dropping
    # ONLY the #552 sentence (leaving #547 intact) genuinely fails the lock, never
    # begs the question via a token #547 also carries (`job 20`/`supervisor` recur).
    STATUS_FINDER_552 = "I→W/U freshness audit is MECHANICAL (#552)"
    SKILL_FINDER_552 = "I→W/U freshness audit is MECHANICAL (#552)"
    TOKENS_552 = ("partition-audit", "re-audit", "OPPOSITE direction")

    def test_statusline_vocabulary_carries_I_direction_mechanism(self):
        text = DEEP2.read_text(encoding="utf-8")
        line = _line_with(text, self.STATUS_FINDER_552)
        self.assertTrue(line, "the W bullet must carry the #552 I→W/U mechanism "
                              "sentence")
        for tok in self.TOKENS_552:
            self.assertIn(tok, line,
                          "W bullet lost the #552 operative token %r (drift)" % tok)

    def test_autopilot_skill_carries_I_direction_mechanism(self):
        text = (_REPO / "skills/autopilot/SKILL.md").read_text(encoding="utf-8")
        win = _norm_window(text, self.SKILL_FINDER_552)
        for tok in self.TOKENS_552:
            self.assertIn(tok, win,
                          "autopilot SKILL lost the #552 token %r (drift)" % tok)


class TestPhase2DeferralDecisionLock(unittest.TestCase):
    """#550 — the source-aware probe (phase 2) was EVALUATED and DEFERRED. The
    decision + its named REOPEN triggers must live durably in THIS module's
    docstring (the first thing a future engineer reads on touching the file),
    not only in a GitHub comment that ages out of view — the #486 'explicit
    decision log' direction. A partial revert dropping the decision, a load-
    bearing reason, or the triggers fails this lock, so a future session cannot
    silently re-open the same evaluated-and-rejected work."""

    # Tokens UNIQUE to the phase-2 DECISION, never one the phase-1 core docstring
    # above already carries (so the finder never begs the question, #498):
    # the verdict, the two load-bearing reasons, the already-tunable latency knob,
    # and the machine-readable REOPEN trigger the probe would first require.
    TOKENS = ("EVALUATED (#550)", "DEFERRED", "REOPEN",
              "AIRULESET_OPS_WAIT_RECHECK_CADENCE_S", "fail2ban",
              "waiting-on:")

    def test_module_docstring_records_phase2_deferral(self):
        doc = " ".join((owr.__doc__ or "").split())
        for tok in self.TOKENS:
            self.assertIn(
                tok, doc,
                "ops_wait_recheck docstring lost the #550 phase-2 deferral "
                "marker %r (decision-log drift)" % tok)


if __name__ == "__main__":
    unittest.main()
