"""#1216: the disk-guard transcript rung killed the whole watchdog on dev1/dev2.

`discover_old_transcript_candidates(include_subagents=True)` ran a FULL /proc
walk (`_target_in_live_use`) per candidate: 6441 old subagent transcripts on
dev1 took 462 s (13.5 M readlink calls), far past the unit's 90 s start
timeout, so every poll was SIGTERMed inside step `transcript` and nothing was
ever gzipped. And `execute_drain` checked the budget only BETWEEN rungs, so a
rung with thousands of gzips would blow the timeout on its own.

Locks: one /proc pass per discovery, the snapshot verdict equals the per-target
verdict, and a rung's actions stop at the poll budget with the rung itself
recorded as the resume point."""
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_scratch_sweep as sweep                       # noqa: E402
from cli_target_purge import _target_in_live_use        # noqa: E402
from watchdog import disk_guard as dg                   # noqa: E402
from watchdog import disk_guard_timing as dgt           # noqa: E402

NOW = 1786176246.0
DAY = 86400.0


def _fakeproc(root, entries):
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for e in entries:
        pdir = proc / e["pid"]
        pdir.mkdir()
        for name in ("exe", "cwd"):
            if e.get(name) is not None:
                os.symlink(e[name], pdir / name)
        if e.get("nofd"):
            continue
        (pdir / "fd").mkdir()
        for i, target in enumerate(e.get("fds", [])):
            os.symlink(target, pdir / "fd" / str(i))
    return proc


def _one_pass_reads(proc):
    """readlink attempts of ONE /proc pass: exe + cwd per pid (present or
    not) plus every fd link."""
    pids = [d for d in proc.iterdir() if d.name.isdigit()]
    return 2 * len(pids) + sum(len(list((d / "fd").iterdir())) for d in pids
                               if (d / "fd").is_dir())


class TestOneProcPass(unittest.TestCase):

    def test_discovery_reads_proc_once_not_once_per_candidate(self):
        with TemporaryDirectory() as td:
            root = Path(td).resolve()
            subs = root / "projects" / "-home-u-p" / "sess" / "subagents"
            subs.mkdir(parents=True)
            files = []
            for i in range(40):
                f = subs / ("agent-%02d.jsonl" % i)
                f.write_bytes(b"x" * 2048)
                os.utime(f, (NOW - 30 * DAY, NOW - 30 * DAY))
                files.append(f)
            main = subs.parent.parent      # the main leg shares the ONE checker
            for i, age in enumerate((40, 30, 20, 10, 1)):
                f = main / ("main-%d.jsonl" % i)
                f.write_bytes(b"y" * 2048)
                os.utime(f, (NOW - age * DAY, NOW - age * DAY))
            proc = _fakeproc(root, [
                {"pid": "11", "exe": "/usr/bin/python3", "cwd": str(root),
                 "fds": ["/dev/null", "socket:[1]", str(files[0])]},
                {"pid": "12", "cwd": "/", "fds": ["/etc/hosts", "pipe:[2]"]},
                {"pid": "13", "fds": ["/var/log/syslog"]},
            ])
            calls = []
            real = os.readlink

            def counting(path, *a, **k):
                if str(path).startswith(str(proc)):
                    calls.append(str(path))
                return real(path, *a, **k)

            with mock.patch("os.readlink", side_effect=counting):
                rows = sweep.discover_old_transcript_candidates(
                    projects_dir=root / "projects", now=NOW, min_age_days=7,
                    min_size_bytes=100, include_subagents=True, proc_dir=proc)
            one_pass = _one_pass_reads(proc)
        self.assertEqual(one_pass, 12)
        self.assertLessEqual(len(calls), one_pass,
                             "one /proc pass per discovery, not one per candidate")
        by = {Path(r["path"]).name: r["reason"] for r in rows}
        self.assertIn("in live use", by["agent-00.jsonl"])
        self.assertIsNone(by["agent-01.jsonl"])
        self.assertEqual(sum(1 for v in by.values() if v is None), 39 + 4)
        self.assertNotIn("main-4.jsonl", by, "newest main transcript is never a row")


class TestSnapshotVerdictEqualsPerTarget(unittest.TestCase):

    def test_same_verdict_for_every_target_shape(self):
        import cli_live_snapshot as ls
        with TemporaryDirectory() as td:
            root = Path(td).resolve()
            b = str(root)
            proc = _fakeproc(root, [
                {"pid": "1", "exe": "/usr/bin/python3", "cwd": b + "/x/a",
                 "fds": [b + "/x/a/f.jsonl", "socket:[123]", b + "/x/d/g.jsonl (deleted)"]},
                {"pid": "2", "fds": [b + "/x/ab/h"]},
                {"pid": "3", "cwd": "/"},
                {"pid": "4", "cwd": b + "/z/w", "nofd": True},
            ])
            (root / "lnk").symlink_to(root / "x" / "a")
            snap = ls.live_links(proc)
            targets = [b + "/x/a", b + "/x/a/f.jsonl", b + "/x/a/other", b + "/x/ab",
                       b + "/x/abc", b + "/x", b + "/x/d", b + "/x/d/g.jsonl", b + "/y",
                       "/", "/usr/bin/python3", "/usr", "/usr/bin/python",
                       b + "/lnk", b + "/z", b + "/z/w/q"]
            for t in targets:
                self.assertEqual(ls.in_live_use(snap, t), _target_in_live_use(t, proc_dir=proc), t)
            # non-vacuous: both verdicts occur
            self.assertTrue(ls.in_live_use(snap, b + "/x/ab"))
            self.assertTrue(ls.in_live_use(snap, b + "/x"))
            self.assertFalse(ls.in_live_use(snap, b + "/x/abc"))
            self.assertFalse(ls.in_live_use(snap, b + "/y"))
            self.assertTrue(ls.in_live_use(snap, b + "/lnk"), "realpath of a symlinked target")
            self.assertTrue(ls.in_live_use(snap, b + "/z"), "cwd of a pid with no fd dir")

    def test_unusable_proc_is_live_for_everything(self):
        import cli_live_snapshot as ls
        with TemporaryDirectory() as td:
            missing = Path(td) / "no-proc"
            self.assertIsNone(ls.live_links(missing))
            self.assertTrue(ls.in_live_use(None, td))
            self.assertTrue(_target_in_live_use(td, proc_dir=missing))

    def test_batch_checker_reads_proc_lazily_once(self):
        import cli_live_snapshot as ls
        with TemporaryDirectory() as td:
            root = Path(td).resolve()
            proc = _fakeproc(root, [{"pid": "5", "fds": [str(root / "held")]}])
            with mock.patch.object(ls, "live_links", wraps=ls.live_links) as spy:
                check = ls.batch_checker(proc)
                self.assertEqual(spy.call_count, 0)
                self.assertTrue(check(root / "held"))
                self.assertFalse(check(root / "free"))
                self.assertEqual(spy.call_count, 1)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestRungStopsAtTheBudget(unittest.TestCase):

    def _drain(self, home, clock, planners, timer):
        def act(_a):
            clock.t += 20.0
            return 1
        return dg.execute_drain(
            {"worst_pct": 92, "dim": "bytes", "level": "critical"}, home, planners,
            recheck_fn=lambda: 92, do_action_fn=act, geteuid_fn=lambda: 1000,
            log_path=None, now=NOW, dry_run=True, timer=timer)

    def test_actions_stop_at_the_budget_and_the_rung_resumes_next_poll(self):
        with TemporaryDirectory() as td:
            state = Path(td) / "guard"
            acts = [{"cls": "transcript", "path": "%s/t%d.jsonl" % (td, i), "bytes": 1,
                     "kind": "gzip", "reason": None} for i in range(5)]
            later = []
            planners = [("transcript", lambda: list(acts)),
                        ("journal", lambda: later.append("ran") or [])]
            clock = FakeClock()
            timer = dgt.PollTimer(clock_fn=clock, state_dir=state, now=NOW)
            logs = self._drain(td, clock, planners, timer)
            acted = [ln for ln in logs if " WOULD-GZIP " in ln]
            self.assertEqual(len(acted), 3, "20 s per action, 45 s budget: 3 run")
            self.assertEqual(later, [], "no further rung runs this poll")
            self.assertTrue(timer.cut_short)
            self.assertTrue(any("budget exceeded inside rung transcript" in ln
                                and "2 action(s) deferred" in ln for ln in logs), logs)
            clock2 = FakeClock()
            timer2 = dgt.PollTimer(clock_fn=clock2, state_dir=state, now=NOW + 60)
            self.assertEqual(timer2.resume_start("fs", ["x", "transcript", "journal"], []), 1)

    def _poll(self, home, clock, planners, now, act, dry_run=True, pre=0.0):
        timer = dgt.PollTimer(clock_fn=clock, state_dir=Path(home) / "guard", now=now)
        clock.t += pre
        logs = dg.execute_drain(
            {"worst_pct": 92, "dim": "bytes", "level": "critical"}, home, planners,
            recheck_fn=lambda: 92, do_action_fn=act, geteuid_fn=lambda: 1000,
            log_path=None, now=now, dry_run=dry_run, timer=timer)
        return timer, logs

    @staticmethod
    def _gz(td, name, nbytes):
        return {"cls": "transcript", "path": "%s/%s" % (td, name), "bytes": nbytes,
                "kind": "gzip", "reason": None}

    def test_a_cut_rung_is_never_recorded_as_low_yield(self):
        """Review 🔴: a rung cut after one small gzip per poll was recorded as
        low-yield, so the 4th poll skipped the transcript rung for 6 h."""
        with TemporaryDirectory() as td:
            for poll in range(5):
                clock = FakeClock()

                def act(_a, clock=clock):
                    clock.t += 50.0
                    return 300_000
                acts = [self._gz(td, "p%d-%d.jsonl" % (poll, i), 300_000) for i in range(3)]
                timer, logs = self._poll(td, clock, [("transcript", lambda acts=acts: acts)],
                                         NOW + poll * 60, act, dry_run=False)
                self.assertFalse(any("SKIP-LOW-YIELD" in ln for ln in logs), (poll, logs))
                self.assertEqual(sum(" GZIP " in ln for ln in logs), 1, (poll, logs))
                self.assertTrue(timer.cut_short)

    def test_a_gzip_that_cannot_finish_waits_for_a_fresh_poll(self):
        """Review 🟡: a gzip SIGTERMed mid-way leaves a temp file and is retried
        on every poll, so it is started only when it fits the time left."""
        with TemporaryDirectory() as td:
            done = []

            def act(a):
                done.append(Path(a["path"]).name)
                return a["bytes"]
            need = 40 * dgt.GZIP_BYTES_PER_S              # 40 s of gzip, 35 s left
            planners = [("transcript", lambda: [self._gz(td, "big.jsonl", need),
                                                self._gz(td, "small.jsonl", 1000)])]
            timer, logs = self._poll(td, FakeClock(), planners, NOW, act, pre=10.0)
            self.assertEqual(done, [], "the time left only shrinks: the rung stops there")
            self.assertTrue(any("big.jsonl" in ln and "next poll" in ln for ln in logs), logs)
            self.assertTrue(timer.cut_short)
            t2 = dgt.PollTimer(clock_fn=FakeClock(), state_dir=Path(td) / "guard", now=NOW + 60)
            self.assertEqual(t2.resume_start("fs", ["x", "transcript"], []), 1)

    def test_running_out_of_time_logs_one_skip_not_one_per_file(self):
        """Live dev1 19:06:09: with ~0 s left one poll logged 330 SKIP lines,
        one per remaining 2 MB transcript, instead of stopping the rung."""
        with TemporaryDirectory() as td:
            acts = [self._gz(td, "a%02d.jsonl" % i, 2_000_000) for i in range(50)]
            timer, logs = self._poll(td, FakeClock(), [("transcript", lambda: acts)],
                                     NOW, lambda a: 1, pre=44.95)
            self.assertEqual(sum("next poll (#1216)" in ln for ln in logs), 1, logs)
            self.assertTrue(timer.cut_short)

    def test_a_gzip_larger_than_any_poll_is_skipped_without_a_cut(self):
        with TemporaryDirectory() as td:
            done = []
            need = 60 * dgt.GZIP_BYTES_PER_S              # past the whole 45 s budget
            planners = [("transcript", lambda: [self._gz(td, "huge.jsonl", need),
                                                self._gz(td, "small.jsonl", 1000)])]
            timer, logs = self._poll(td, FakeClock(), planners, NOW,
                                     lambda a: done.append(Path(a["path"]).name) or 1)
            self.assertEqual(done, ["small.jsonl"])
            self.assertTrue(any("huge.jsonl" in ln and "budget" in ln for ln in logs), logs)
            self.assertFalse(timer.cut_short, "a never-fitting file must not cut every poll")

    def test_last_action_crossing_the_budget_defers_the_next_rung(self):
        with TemporaryDirectory() as td:
            clock, later = FakeClock(), []

            def act(_a):
                clock.t += 30.0
                return 1
            planners = [("transcript", lambda: [self._gz(td, "a", 1), self._gz(td, "b", 1)]),
                        ("journal", lambda: later.append("ran") or [])]
            timer, logs = self._poll(td, clock, planners, NOW, act)
            self.assertEqual(sum(" WOULD-GZIP " in ln for ln in logs), 2)
            self.assertEqual(later, [])
            self.assertFalse(any("inside rung" in ln for ln in logs), logs)
            t2 = dgt.PollTimer(clock_fn=FakeClock(), state_dir=Path(td) / "guard", now=NOW + 60)
            self.assertEqual(t2.resume_start("fs", ["transcript", "journal"], []), 1)

    def test_failed_actions_count_against_the_budget(self):
        with TemporaryDirectory() as td:
            clock, tried = FakeClock(), []

            def boom(a):
                tried.append(a["path"])
                clock.t += 50.0
                raise OSError("verify mismatch")
            planners = [("transcript", lambda: [self._gz(td, "t%d" % i, 1) for i in range(3)])]
            timer, logs = self._poll(td, clock, planners, NOW, boom)
            self.assertEqual(len(tried), 1)
            self.assertTrue(timer.cut_short)

    def test_only_skip_rows_left_do_not_cut_the_rung(self):
        with TemporaryDirectory() as td:
            clock = FakeClock()

            def act(_a):
                clock.t += 50.0
                return 1
            rows = [self._gz(td, "a", 1),
                    {"cls": "transcript", "path": td + "/b", "bytes": 1, "kind": "skip",
                     "reason": "too recent"},
                    {"cls": "transcript", "path": td + "/c", "bytes": 1, "kind": "report",
                     "reason": "report-only"}]
            timer, logs = self._poll(td, clock, [("transcript", lambda: rows)], NOW, act)
            self.assertTrue(any(" SKIP " in ln and td + "/b" in ln for ln in logs), logs)
            self.assertTrue(any(" REPORT " in ln for ln in logs), logs)
            self.assertFalse(any("inside rung" in ln for ln in logs), logs)
            self.assertFalse(timer.cut_short)

    def test_null_timer_runs_the_whole_rung(self):
        with TemporaryDirectory() as td:
            acts = [{"cls": "transcript", "path": "%s/t%d.jsonl" % (td, i), "bytes": 1,
                     "kind": "gzip", "reason": None} for i in range(5)]
            logs = self._drain(td, FakeClock(), [("transcript", lambda: list(acts))], None)
            self.assertEqual(sum(1 for ln in logs if " WOULD-GZIP " in ln), 5)


if __name__ == "__main__":
    unittest.main()
