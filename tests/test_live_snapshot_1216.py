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
        (pdir / "fd").mkdir()
        for i, target in enumerate(e.get("fds", [])):
            os.symlink(target, pdir / "fd" / str(i))
    return proc


def _one_pass_reads(proc):
    """readlink attempts of ONE /proc pass: exe + cwd per pid (present or
    not) plus every fd link."""
    pids = [d for d in proc.iterdir() if d.name.isdigit()]
    return 2 * len(pids) + sum(len(list((d / "fd").iterdir())) for d in pids)


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
        self.assertEqual(sum(1 for v in by.values() if v is None), 39)


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
            ])
            snap = ls.live_links(proc)
            targets = [b + "/x/a", b + "/x/a/f.jsonl", b + "/x/a/other", b + "/x/ab",
                       b + "/x/abc", b + "/x", b + "/x/d", b + "/x/d/g.jsonl", b + "/y",
                       "/", "/usr/bin/python3", "/usr", "/usr/bin/python"]
            for t in targets:
                self.assertEqual(ls.in_live_use(snap, t), _target_in_live_use(t, proc_dir=proc), t)
            # non-vacuous: both verdicts occur
            self.assertTrue(ls.in_live_use(snap, b + "/x/ab"))
            self.assertTrue(ls.in_live_use(snap, b + "/x"))
            self.assertFalse(ls.in_live_use(snap, b + "/x/abc"))
            self.assertFalse(ls.in_live_use(snap, b + "/y"))

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

    def test_null_timer_runs_the_whole_rung(self):
        with TemporaryDirectory() as td:
            acts = [{"cls": "transcript", "path": "%s/t%d.jsonl" % (td, i), "bytes": 1,
                     "kind": "gzip", "reason": None} for i in range(5)]
            logs = self._drain(td, FakeClock(), [("transcript", lambda: list(acts))], None)
            self.assertEqual(sum(1 for ln in logs if " WOULD-GZIP " in ln), 5)


if __name__ == "__main__":
    unittest.main()
