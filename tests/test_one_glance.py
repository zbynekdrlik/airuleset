"""#486 G3+G6 -- unit tests for the pure one-glance predicate (`watchdog/
one_glance.py`) and the session-status reaper (`watchdog/session_status.py`).

The predicate reads STRUCTURED facts only; these tests exercise every verdict
branch (with mutation teeth on the two that matter for safety -- STUCK is the
only actionable one, and OVER-reading a dead worker as live must not suppress
it), the G6 `resolve_goal_armed` precedence chain (goal_mark tail-proof FIRST,
heartbeat fallback), and the `evaluate` resolver's composition of the injected
readers with the `src` annotation that replaced the render-footer read.
"""

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import one_glance as og
from watchdog import session_status as ss


def _facts(**over):
    base = dict(heartbeat_state="stale", goal_armed=True, marker="working",
                idle_over_threshold=True, live_workers=0, backlog=43)
    base.update(over)
    return base


class TestResolveGoalArmed(unittest.TestCase):
    """#486 G6 -- the authoritative armed signal precedence chain: goal_mark
    (tail-proof) FIRST, heartbeat only as fallback, heartbeat NEVER vetoes
    goal_mark."""

    def _mark(self, state):
        return {"off": 0, "mark": {"state": state, "ts": 1000}} if state else None

    def test_goal_mark_set_wins_over_a_heartbeat_that_lies_not_armed(self):
        # THE #486 crux: a day-old arm reads goal_armed=False from the 4 MB tail,
        # but goal_mark saw the `Goal set:` and persists it. goal_mark wins.
        armed, src = og.resolve_goal_armed(self._mark("set"), False)
        self.assertIs(armed, True)
        self.assertEqual(src, "goal_mark")

    def test_goal_mark_cleared_wins_over_a_heartbeat_that_says_armed(self):
        # The incremental scan saw the clear; it is strictly fresher than the
        # heartbeat's own single-shot tail scan.
        armed, src = og.resolve_goal_armed(self._mark("cleared"), True)
        self.assertIs(armed, False)
        self.assertEqual(src, "goal_mark")

    def test_heartbeat_true_is_the_fallback_when_goal_mark_absent(self):
        for entry in (None, {"off": 0, "mark": None}, {}, "garbage"):
            armed, src = og.resolve_goal_armed(entry, True)
            self.assertIs(armed, True, entry)
            self.assertEqual(src, "heartbeat", entry)

    def test_heartbeat_false_is_the_tail_labelled_fallback(self):
        armed, src = og.resolve_goal_armed(None, False)
        self.assertIs(armed, False)
        self.assertEqual(src, "heartbeat-tail")

    def test_both_unknown_is_none_fail_closed(self):
        # Neither signal can confirm -> None -> the gate fails CLOSED (skip, but
        # logged), never a guessed keystroke into a maybe-not-armed session.
        armed, src = og.resolve_goal_armed(None, None)
        self.assertIsNone(armed)
        self.assertEqual(src, "unknown")


class TestOneGlanceVerdict(unittest.TestCase):
    def test_stuck_is_armed_zero_workers_backlog_idle_not_awaiting(self):
        self.assertEqual(og.one_glance_verdict(**_facts()), "stuck")

    def test_no_heartbeat_when_absent_or_corrupt(self):
        self.assertEqual(og.one_glance_verdict(**_facts(heartbeat_state="absent",
                                                        goal_armed=None)),
                         "no-heartbeat")
        self.assertEqual(og.one_glance_verdict(**_facts(heartbeat_state="corrupt",
                                                        goal_armed=None)),
                         "no-heartbeat")

    def test_armed_unknown_when_goal_armed_is_none(self):
        self.assertEqual(og.one_glance_verdict(**_facts(goal_armed=None)),
                         "armed-unknown")

    def test_not_armed_when_goal_armed_false(self):
        self.assertEqual(og.one_glance_verdict(**_facts(goal_armed=False)),
                         "not-armed")
        # G6: an armed=False is DEFINITE regardless of whether a heartbeat file
        # exists (goal_mark can clear a heartbeatless session) -> still not-armed,
        # never the heartbeat-absent label. Mutation teeth: the pre-G6 absent-
        # first order returned "no-heartbeat" here.
        self.assertEqual(og.one_glance_verdict(**_facts(heartbeat_state="absent",
                                                        goal_armed=False)),
                         "not-armed")

    def test_awaiting_user_wins_over_stuck(self):
        # A ❓-blocked session is NEVER stuck, even armed + 0 workers + backlog +
        # idle. (Mutation teeth: dropping the needs_you branch would read stuck.)
        self.assertEqual(og.one_glance_verdict(**_facts(marker="needs_you")),
                         "awaiting-user")

    def test_armed_via_goal_mark_without_a_heartbeat_still_a_candidate(self):
        # goal_mark can arm a heartbeatless session -> armed=True + absent hb must
        # PROCEED to the worker/backlog readers (return a candidate verdict), NOT
        # short-circuit to "no-heartbeat". Mutation teeth: the pre-G6 absent-first
        # order returned "no-heartbeat" and never reached `stuck`.
        self.assertEqual(og.one_glance_verdict(**_facts(heartbeat_state="absent")),
                         "stuck")

    def test_working_when_a_live_worker_is_counted(self):
        # OVER-reading a dead worker as live is the DANGEROUS direction (it
        # suppresses the recovery nudge) -- but a genuine live worker IS
        # `working`, never `stuck`. G2's count is what excludes dead workers.
        self.assertEqual(og.one_glance_verdict(**_facts(live_workers=1)),
                         "working")

    def test_no_backlog_when_zero_or_unmeasurable(self):
        self.assertEqual(og.one_glance_verdict(**_facts(backlog=0)), "no-backlog")
        # None = unmeasurable -> never guessed as work-to-do -> never stuck.
        self.assertEqual(og.one_glance_verdict(**_facts(backlog=None)),
                         "no-backlog")

    def test_warming_when_recently_active(self):
        # idle NOT over threshold -> recently active -> debounce, not stuck.
        self.assertEqual(og.one_glance_verdict(**_facts(idle_over_threshold=False)),
                         "warming")

    def test_unmeasurable_worker_count_never_reads_stuck(self):
        # DEFENSIVE symmetry with the backlog guard: a non-int (unmeasurable)
        # worker count can't confirm workers==0, so it must never assert stuck
        # (the dangerous direction). Mutation teeth: dropping the guard reads
        # `stuck` here (backlog>0, idle over) instead of the safe `warming`.
        self.assertEqual(og.one_glance_verdict(**_facts(live_workers=None)),
                         "warming")


class TestHeartbeatOnlyVerdict(unittest.TestCase):
    def test_cheap_verdicts_resolvable_from_the_armed_signal_alone(self):
        self.assertEqual(og.heartbeat_only_verdict("absent", None, None),
                         "no-heartbeat")
        self.assertEqual(og.heartbeat_only_verdict("corrupt", None, None),
                         "no-heartbeat")
        self.assertEqual(og.heartbeat_only_verdict("fresh", None, "working"),
                         "armed-unknown")
        self.assertEqual(og.heartbeat_only_verdict("fresh", False, "working"),
                         "not-armed")
        self.assertEqual(og.heartbeat_only_verdict("stale", True, "needs_you"),
                         "awaiting-user")

    def test_definite_armed_takes_precedence_over_heartbeat_state(self):
        # G6 reorder: armed True/False is DEFINITE regardless of heartbeat_state
        # (goal_mark can arm/clear a heartbeatless session).
        self.assertEqual(og.heartbeat_only_verdict("absent", False, None),
                         "not-armed")
        self.assertIsNone(og.heartbeat_only_verdict("absent", True, "working"))

    def test_none_means_needs_the_expensive_readers(self):
        # armed + not awaiting-user -> can't decide without workers/backlog.
        self.assertIsNone(og.heartbeat_only_verdict("stale", True, "working"))


class TestIsInformative(unittest.TestCase):
    def _g(self, verdict):
        return og.OneGlance(verdict, 0, 43, True, "working", "stale", True,
                            1387, "goal_mark", "")

    def test_every_structured_armed_verdict_is_informative(self):
        for v in ("stuck", "working", "warming", "no-backlog", "awaiting-user"):
            self.assertTrue(og.is_informative(self._g(v)), v)

    def test_not_armed_is_silenced_as_pure_noise(self):
        # A definite not-armed pane is a plain interactive session -> the
        # per-sweep noise the pre-G6 render path also silenced.
        self.assertFalse(og.is_informative(self._g("not-armed")))

    def test_missing_or_unknown_armed_is_never_silenced(self):
        # no-heartbeat / armed-unknown could hide a genuinely-armed session, so
        # they are NEVER suppressed (the class #486 must not go blind on).
        self.assertTrue(og.is_informative(self._g("no-heartbeat")))
        self.assertTrue(og.is_informative(self._g("armed-unknown")))


class TestEvaluate(unittest.TestCase):
    def _readers(self, hb, workers, backlog):
        def _rs(**kw):
            return hb

        def _clw(*a, **k):
            return workers, []

        def _cbc(*a, **k):
            return backlog
        return _rs, _clw, _cbc

    def _hb(self, state, goal_armed, marker, age_s):
        return ss.SessionStatus(state, age_s, {}, "sid1", "main", marker,
                                goal_armed, "/x", None, None, "stop", None)

    def _mark(self, state):
        return {"off": 0, "mark": {"state": state, "ts": 1000}}

    def test_composes_readers_and_annotates_the_armed_source(self):
        # THE #486 case end to end: the heartbeat LIES not-armed (4 MB tail) but
        # goal_mark says set -> the verdict is `stuck` and the line names
        # src=goal_mark, NOT the heartbeat's False.
        rs, clw, cbc = self._readers(
            self._hb("stale", False, "working", 1387), 0, 43)
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, self._mark("set"),
            "loc-A",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "stuck")
        self.assertEqual(g.live_workers, 0)
        self.assertEqual(g.backlog, 43)
        self.assertEqual(g.goal_armed, True)
        self.assertEqual(g.src, "goal_mark")
        self.assertTrue(line.startswith("one-glance loc-A -> stuck ("), line)
        self.assertIn("armed=yes src=goal_mark", line)

    def test_heartbeat_fallback_annotated_when_goal_mark_absent(self):
        rs, clw, cbc = self._readers(
            self._hb("fresh", True, "working", 30), 3, 43)
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, None, "loc-B",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "working")
        self.assertIn("armed=yes src=heartbeat", line)

    def test_idle_signal_is_the_heartbeat_staleness(self):
        # stale_after_s == idle_threshold_s, so a `stale` heartbeat IS
        # idle-over-threshold and a `fresh` one is not.
        rs, clw, cbc = self._readers(
            self._hb("fresh", True, "working", 100), 0, 43)
        g, _line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, None, "loc-C",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertFalse(g.idle_over_threshold)
        self.assertEqual(g.verdict, "warming")   # fresh -> not idle -> warming

    def test_cheap_verdict_never_calls_the_expensive_readers(self):
        # THE cost fix (review 🟡): a cheap verdict (here not-armed via the
        # heartbeat fallback) must NOT resolve count_live_workers (a disk scan)
        # or the backlog cache (a gh subprocess on a miss). Record calls, assert 0.
        calls = {"workers": 0, "backlog": 0}

        def clw(*a, **k):
            calls["workers"] += 1
            return 0, []

        def cbc(*a, **k):
            calls["backlog"] += 1
            return 43
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, None, "loc-D",
            read_status=lambda **kw: self._hb("fresh", False, "working", 30),
            count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "not-armed")
        self.assertEqual(calls, {"workers": 0, "backlog": 0})
        # honest: readers never consulted -> n/a, not a misleading "0".
        self.assertIn("workers=n/a backlog=n/a", line)


class TestReapStaleStatus(unittest.TestCase):
    def _dir(self):
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return Path(d.name)

    def _file(self, base, name, age_s, now):
        p = base / name
        p.write_text("{}", encoding="utf-8")
        old = now - age_s
        os.utime(p, (old, old))
        return p

    def test_reaps_only_files_older_than_ttl(self):
        base = self._dir()
        now = time.time()
        old = self._file(base, "dead.json", ss.SESSION_STATUS_TTL_S + 3600, now)
        young = self._file(base, "alive.json", 60, now)
        logs = ss.reap_stale_status(now=now, base_dir=str(base))
        self.assertFalse(old.exists(), "a >TTL file must be reaped")
        self.assertTrue(young.exists(), "a young file must NEVER be reaped")
        self.assertTrue(any("reaped 1" in ln for ln in logs), logs)

    def test_no_dir_is_a_silent_noop(self):
        missing = self._dir() / "does-not-exist"
        self.assertEqual(ss.reap_stale_status(now=time.time(),
                                              base_dir=str(missing)), [])

    def test_nothing_to_reap_returns_empty_list(self):
        base = self._dir()
        now = time.time()
        self._file(base, "alive.json", 60, now)
        self.assertEqual(ss.reap_stale_status(now=now, base_dir=str(base)), [])

    def test_a_subdir_is_never_removed(self):
        base = self._dir()
        now = time.time()
        sub = base / "subdir"
        sub.mkdir()
        old = now - (ss.SESSION_STATUS_TTL_S + 3600)
        os.utime(sub, (old, old))
        logs = ss.reap_stale_status(now=now, base_dir=str(base))
        self.assertTrue(sub.exists(), "a directory must never be unlinked")
        self.assertEqual(logs, [])

    def test_never_raises_and_warns_on_a_bad_entry(self):
        base = self._dir()
        now = time.time()
        warned = []
        # a broken symlink -> stat() raises -> warned + skipped, never fatal
        (base / "broken.json").symlink_to(base / "nowhere-target")
        logs = ss.reap_stale_status(now=now, base_dir=str(base),
                                    on_warn=warned.append)
        self.assertEqual(logs, [])
        self.assertTrue(any("stat failed" in w for w in warned), warned)


if __name__ == "__main__":
    unittest.main()
