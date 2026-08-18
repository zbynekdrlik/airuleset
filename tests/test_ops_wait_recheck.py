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
    def test_undetermined_members_skip_and_never_mutate(self):
        # None (fetch failed/refused) -> skip, rec returned UNCHANGED, no nudge.
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 5 * DAY}
        action, out, reason = owr._recheck_decision(rec, None, NOW, CAD)
        self.assertEqual(action, "skip")
        self.assertEqual(reason, "undetermined")
        self.assertIs(out, rec)

    def test_empty_members_clear(self):
        action, out, reason = owr._recheck_decision(
            {"first_seen": NOW - 5 * DAY}, [], NOW, CAD)
        self.assertEqual(action, "clear")
        self.assertIsNone(out)

    def test_first_sight_seeds_first_seen_and_waits(self):
        # A never-seen W set: seed first_seen=now, WAIT (grace — never nag a
        # session about a ticket it just parked).
        action, out, reason = owr._recheck_decision({}, [43, 41], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(reason, "grace")
        self.assertEqual(out["first_seen"], NOW)
        self.assertIsNone(out["last_nudge"])
        self.assertEqual(out["sig"], "41,43")   # numeric sort

    def test_past_cadence_from_first_seen_nudges(self):
        # first_seen older than the cadence, never nudged -> DUE.
        rec = {"first_seen": NOW - 2 * CAD}
        action, out, reason = owr._recheck_decision(rec, [41], NOW, CAD)
        self.assertEqual(action, "nudge")
        self.assertEqual(reason, "due")
        # a "nudge" verdict is an INTENT — last_nudge is preserved (the caller
        # advances it only on a confirmed submit), never set here.
        self.assertIsNone(out["last_nudge"])
        self.assertEqual(out["first_seen"], NOW - 2 * CAD)

    def test_within_reping_window_waits(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - CAD // 2}
        action, out, _ = owr._recheck_decision(rec, [41], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(out["last_nudge"], NOW - CAD // 2)

    def test_past_reping_window_nudges(self):
        rec = {"first_seen": NOW - 5 * DAY, "last_nudge": NOW - 2 * CAD}
        action, out, _ = owr._recheck_decision(rec, [41], NOW, CAD)
        self.assertEqual(action, "nudge")

    def test_malformed_rec_is_seeded_fresh(self):
        action, out, _ = owr._recheck_decision(None, [7], NOW, CAD)
        self.assertEqual(action, "wait")
        self.assertEqual(out["first_seen"], NOW)


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

    def _run(self, wrecs, fetch, tmux, *, dry_run=False, handled=None, state=None):
        return owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            dry_run, handled, ops_wait_fetch=fetch,
            state=state if state is not None else {},
            sleep_fn=lambda *a, **k: None, cadence=CAD)


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
        typed = " ".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)
        self.assertIn("#41", typed)
        self.assertIn("#43", typed)
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

    def _armed_sweep(self, state, *, ops_wait_fetch, dry_run=False, handled=None):
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
        with m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch.object(wd, "_owner_disabled", return_value=False):
            # backlog 0 -> the lane-occupancy nudge skips (no-backlog), so this
            # sweep's ONLY possible keystroke is the ops-wait re-check under test
            # (clean isolation from the sibling lane nudge; the `handled`
            # coordination when the lane nudge DOES fire is locked by
            # TestOrchestrator.test_due_but_already_handled_defers).
            goal.goal_lane_sweep(
                NOW, run=tmux, projects_dir=proj, state=state, dry_run=dry_run,
                handled=handled, backlog_fetch=lambda cwd: 0,
                ops_wait_fetch=ops_wait_fetch, sleep_fn=lambda *a, **k: None)
        return sid, tmux

    def test_parked_w_past_cadence_is_nudged(self):
        # Pre-seed an OLD first_seen so the W set is past the cadence -> DUE.
        state = {"ops_wait_recheck": {
            "sess-547-lane": {"first_seen": NOW - 5 * DAY, "last_nudge": None}}}
        sid, tmux = self._armed_sweep(state, ops_wait_fetch=lambda cwd: [41, 43])
        typed = " ".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed,
                      "an armed pane with W parked past cadence must be nudged "
                      "(RED before goal_lane_sweep wires goal_ops_wait_recheck)")
        self.assertIn("#41", typed)
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
        text = (_REPO / "modules/core/statusline-vocabulary.md").read_text(
            encoding="utf-8")
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
