"""#486 G3 -- unit tests for the pure one-glance predicate (`watchdog/
one_glance.py`) and the session-status reaper (`watchdog/session_status.py`).

The predicate reads STRUCTURED facts only; these tests exercise every verdict
branch (with mutation teeth on the two that matter for safety -- STUCK is the
only actionable one, and OVER-reading a dead worker as live must not suppress
it) plus the `evaluate` resolver's composition of the three injected readers
and its render<->structured divergence annotation.
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

    def test_awaiting_user_wins_over_stuck(self):
        # A ❓-blocked session is NEVER stuck, even armed + 0 workers + backlog +
        # idle. (Mutation teeth: dropping the needs_you branch would read stuck.)
        self.assertEqual(og.one_glance_verdict(**_facts(marker="needs_you")),
                         "awaiting-user")

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
    def test_cheap_verdicts_resolvable_from_heartbeat_alone(self):
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

    def test_none_means_needs_the_expensive_readers(self):
        # armed + not awaiting-user -> can't decide without workers/backlog.
        self.assertIsNone(og.heartbeat_only_verdict("stale", True, "working"))


class TestIsInformative(unittest.TestCase):
    def _g(self, verdict):
        return og.OneGlance(verdict, 0, 43, True, "working", "stale", True,
                            1387, "")

    def test_every_structured_armed_verdict_is_informative(self):
        for v in ("stuck", "working", "warming", "no-backlog", "awaiting-user"):
            self.assertTrue(og.is_informative(self._g(v), False), v)

    def test_not_armed_agreeing_with_the_footer_is_silenced(self):
        # heartbeat says not-armed AND the footer also read not-armed -> a plain
        # interactive session, pure noise, the pre-G3 render path silenced it too.
        self.assertFalse(og.is_informative(self._g("not-armed"), False))

    def test_not_armed_disagreeing_with_the_footer_is_informative(self):
        # footer read armed while the heartbeat says not-armed -> a real
        # divergence worth journalling.
        self.assertTrue(og.is_informative(self._g("not-armed"), True))

    def test_missing_heartbeat_is_never_silenced(self):
        # no-heartbeat / armed-unknown could hide a genuinely-armed session, so
        # they are NEVER suppressed (the class #486 must not go blind on).
        self.assertTrue(og.is_informative(self._g("no-heartbeat"), False))
        self.assertTrue(og.is_informative(self._g("armed-unknown"), False))


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

    def test_composes_three_readers_and_flags_the_486_divergence(self):
        rs, clw, cbc = self._readers(
            self._hb("stale", True, "working", 1387), 0, 43)
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, False, "loc-A",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "stuck")
        self.assertEqual(g.live_workers, 0)
        self.assertEqual(g.backlog, 43)
        self.assertTrue(line.startswith("one-glance loc-A -> stuck ("), line)
        self.assertIn("render=not-armed", line)
        self.assertIn("structured state is armed", line)

    def test_no_divergence_note_when_render_agrees_armed(self):
        rs, clw, cbc = self._readers(
            self._hb("fresh", True, "working", 30), 3, 43)
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, True, "loc-B",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "working")
        self.assertIn("render=armed", line)
        self.assertNotIn("structured state is armed", line)

    def test_idle_signal_is_the_heartbeat_staleness(self):
        # stale_after_s == idle_threshold_s, so a `stale` heartbeat IS
        # idle-over-threshold and a `fresh` one is not.
        rs, clw, cbc = self._readers(
            self._hb("fresh", True, "working", 100), 0, 43)
        g, _line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, True, "loc-C",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertFalse(g.idle_over_threshold)
        self.assertEqual(g.verdict, "warming")   # fresh -> not idle -> warming

    def test_cheap_verdict_never_calls_the_expensive_readers(self):
        # THE cost fix (review 🟡): a heartbeat-only verdict (here not-armed)
        # must NOT resolve count_live_workers (a disk scan) or the backlog cache
        # (a gh subprocess on a miss). Record calls and assert zero.
        calls = {"workers": 0, "backlog": 0}

        def clw(*a, **k):
            calls["workers"] += 1
            return 0, []

        def cbc(*a, **k):
            calls["backlog"] += 1
            return 43
        g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, False, "loc-D",
            read_status=lambda **kw: self._hb("fresh", False, "working", 30),
            count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertEqual(g.verdict, "not-armed")
        self.assertEqual(calls, {"workers": 0, "backlog": 0})
        # honest: readers never consulted -> n/a, not a misleading "0".
        self.assertIn("workers=n/a backlog=n/a", line)

    def test_divergence_note_only_for_render_positively_not_armed(self):
        # render=None (undeterminable footer) is NOT the #486 silent case
        # (#475 logs skip:armed-undeterminable), so no "SILENTLY" suffix.
        rs, clw, cbc = self._readers(
            self._hb("stale", True, "working", 1387), 0, 43)
        _g, line = og.evaluate(
            1000, "sid1", "/x", "/proj", {}, lambda c: 43, None, "loc-E",
            read_status=rs, count_live_workers=clw, cached_backlog_count=cbc,
            idle_threshold_s=900, freshness_s=900)
        self.assertIn("render=undeterminable", line)
        self.assertNotIn("SILENTLY", line)


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
