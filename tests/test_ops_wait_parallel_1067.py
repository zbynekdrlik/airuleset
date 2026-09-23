"""#1067 slice 1c (b) — bounded-parallel per-issue reads with sequential parity.

The two per-issue gh-read loops in `cli_quals.py` (the `_slice_mine_and_handed`
timeline walk and the `ops_wait_ages_fn` >100-comment fallback) now run through a
bounded `cli_parallel.run_parallel(max_workers=6)` instead of one sequential call
at a time. This suite proves:

  * `run_parallel` maps correctly, isolates a per-call failure, and stays bounded;
  * `_handed_from_timelines` produces the SAME `handed` as a sequential fold on
    identical fixtures, folds deterministically in number order, and isolates a
    failing / unusable per-issue read;
  * `ops_wait_ages_fn` returns byte-identical `_ages(n)` results to the lazy path,
    prefetches only the fallback (non-prefetch) members, bounds the prefetch to
    the same cap the flag-set consumers query, and isolates a failing call;
  * `spawn_refresher` builds the detached `-m watchdog.ops_wait_refresh` argv.
"""
import threading
import types
import unittest
import unittest.mock as m
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402
import cli_parallel  # noqa: E402
from watchdog import ops_wait_refresh as owref  # noqa: E402

ROOT = "/repo"
SLUG = "o/r"


class RunParallel(unittest.TestCase):
    def test_maps_every_item(self):
        self.assertEqual(cli_parallel.run_parallel([1, 2, 3], lambda n: n * 10),
                         {1: 10, 2: 20, 3: 30})

    def test_empty_makes_no_calls(self):
        calls = []
        self.assertEqual(cli_parallel.run_parallel(
            [], lambda n: calls.append(n)), {})
        self.assertEqual(calls, [])

    def test_a_failing_call_is_isolated(self):
        def fn(n):
            if n == 2:
                raise RuntimeError("boom")
            return n * 10
        out = cli_parallel.run_parallel([1, 2, 3], fn)
        self.assertEqual(out, {1: 10, 3: 30})   # 2 omitted, 1 & 3 survive

    def test_pool_is_bounded_by_max_workers(self):
        live = {"n": 0, "peak": 0}
        lock = threading.Lock()
        gate = threading.Event()

        def fn(n):
            with lock:
                live["n"] += 1
                live["peak"] = max(live["peak"], live["n"])
            gate.wait(0.5)
            with lock:
                live["n"] -= 1
            return n
        t = threading.Thread(target=lambda: cli_parallel.run_parallel(
            range(20), fn, max_workers=3))
        t.start()
        # let the pool saturate, then release
        threading.Event().wait(0.15)
        peak = live["peak"]
        gate.set()
        t.join()
        self.assertLessEqual(peak, 3)


def _seq_handed_from_timelines(walk, bounce, root, slug, verdict_of, released):
    """A straight sequential reference implementation of the timeline fold."""
    handed = {}
    for n in walk:
        if n in released:
            handed[n] = "released"
    for n in walk:
        if n in released:
            continue
        res = verdict_of(n)
        if res is None:
            continue
        verdict, saw_gk = res
        if verdict and (n not in bounce or saw_gk):
            handed[n] = True
    return handed


class HandedFromTimelinesParity(unittest.TestCase):
    def _run(self, walk, bounce, verdict_map, released):
        with m.patch.object(cli_quals, "_released_stream_numbers",
                            return_value=set(released)), \
                m.patch.object(cli_quals, "_timeline_verdict",
                               side_effect=lambda n, r, s: verdict_map.get(n)), \
                m.patch.object(airuleset, "_current_user", return_value="me"):
            handed = {}
            cli_quals._handed_from_timelines(walk, bounce, handed, ROOT, SLUG)
        return handed

    def test_parallel_equals_sequential(self):
        walk = [50, 47, 43, 41, 39]          # the real walk order (reverse-sorted)
        bounce = {43}
        # 41 handed via comment; 43 (bounce) needs a gk comment -> saw_gk True;
        # 47 verdict True but bounce? no -> handed; 39 verdict False -> not; 50 None.
        verdict_map = {50: None, 47: (True, False), 43: (True, True),
                       41: (True, False), 39: (False, False)}
        released = {50}
        got = self._run(walk, bounce, verdict_map, released)
        exp = _seq_handed_from_timelines(
            walk, bounce, ROOT, SLUG, verdict_map.get, released)
        self.assertEqual(got, exp)
        self.assertEqual(got, {50: "released", 47: True, 43: True, 41: True})

    def test_bounce_without_gk_comment_not_upgraded(self):
        walk = [43]
        got = self._run(walk, {43}, {43: (True, False)}, set())
        self.assertEqual(got, {})              # bounce + no gk comment -> not handed

    def test_deterministic_across_runs(self):
        walk = [90, 80, 70, 60, 50, 40, 30, 20, 10]
        vm = {n: (True, False) for n in walk}
        a = self._run(walk, set(), vm, set())
        b = self._run(walk, set(), vm, set())
        self.assertEqual(a, b)
        self.assertEqual(set(a), set(walk))

    def test_unusable_read_is_isolated(self):
        walk = [7, 8, 9]
        got = self._run(walk, set(), {7: (True, False), 8: None,
                                      9: (True, False)}, set())
        self.assertEqual(got, {7: True, 9: True})   # 8 (None) omitted


class TimelineVerdictReadShape(unittest.TestCase):
    def test_non_list_json_is_none(self):
        # a bare int / non-list JSON is never a real answer -> None (skip),
        # exactly as the old inline walk's `if not isinstance(events, list)`.
        with m.patch.object(airuleset, "_gh_out", return_value="123"):
            self.assertIsNone(cli_quals._timeline_verdict(1, ROOT, SLUG))

    def test_parse_error_is_neutral_verdict(self):
        # unparseable output -> events=[] -> (False, False) = no upgrade, matching
        # the old walk's `except: events = []` then empty-loop path.
        with m.patch.object(airuleset, "_gh_out", return_value="not json"):
            self.assertEqual(cli_quals._timeline_verdict(1, ROOT, SLUG),
                             (False, False))

    def test_empty_list_is_neutral_verdict(self):
        with m.patch.object(airuleset, "_gh_out", return_value="[]"):
            self.assertEqual(cli_quals._timeline_verdict(1, ROOT, SLUG),
                             (False, False))


class OpsWaitAgesFnParity(unittest.TestCase):
    def _ages_of(self, n):
        return {"own": n * 100, "any": n * 100, "own_cited": None,
                "own_oldest": None, "own_final_reminder": None}

    def _recorder(self):
        # a thread-safe call recorder (Mock.call_count RMW is not lock-guarded,
        # and the fallback runs on a thread pool — assert on the guarded list).
        calls, lock = [], threading.Lock()

        def rec(n, *a, **k):
            with lock:
                calls.append(n)
            return self._ages_of(n)
        return calls, rec

    def test_fallback_members_parallel_prefetched_identical_to_lazy(self):
        members = [10, 20, 30]
        calls, rec = self._recorder()
        with m.patch.object(airuleset, "_stream_self_login", return_value="me"), \
                m.patch.object(airuleset, "_ops_wait_prefetch_comments",
                               return_value={}), \
                m.patch.object(airuleset, "_issue_comment_ages", side_effect=rec):
            ages_fn = cli_quals.ops_wait_ages_fn(members, ROOT, ["label:x"])
            # every fallback member prefetched exactly once during construction
            self.assertEqual(sorted(calls), [10, 20, 30])
            for n in members:
                self.assertEqual(ages_fn(n), self._ages_of(n))
            # reads are cached -> no extra call after construction
            self.assertEqual(sorted(calls), [10, 20, 30])

    def test_prefetch_hit_bypasses_the_per_issue_fallback(self):
        members = [10, 20]
        with m.patch.object(airuleset, "_stream_self_login", return_value="me"), \
                m.patch.object(airuleset, "_ops_wait_prefetch_comments",
                               return_value={20: [{"c": 1}]}), \
                m.patch.object(airuleset, "_ages_from_comments",
                               side_effect=lambda raw, sl: {"prefetched": True}), \
                m.patch.object(airuleset, "_issue_comment_ages",
                               side_effect=lambda n, *a, **k: self._ages_of(n)) as ica:
            ages_fn = cli_quals.ops_wait_ages_fn(members, ROOT, ["label:x"])
            self.assertEqual([c[0][0] for c in ica.call_args_list], [10])  # only 10
            self.assertEqual(ages_fn(20), {"prefetched": True})           # from prefetch
            self.assertEqual(ages_fn(10), self._ages_of(10))              # from fallback

    def test_prefetch_bounded_to_stale_max_fetches(self):
        members = list(range(1, 41))   # 40 > OPS_WAIT_STALE_MAX_FETCHES (25)
        calls, rec = self._recorder()
        with m.patch.object(airuleset, "_stream_self_login", return_value="me"), \
                m.patch.object(airuleset, "_ops_wait_prefetch_comments",
                               return_value={}), \
                m.patch.object(airuleset, "_issue_comment_ages", side_effect=rec):
            cli_quals.ops_wait_ages_fn(members, ROOT, ["label:x"])
            self.assertEqual(len(calls), cli_quals.OPS_WAIT_STALE_MAX_FETCHES)
            self.assertEqual(sorted(calls), list(range(1, 26)))   # the lowest 25

    def test_a_failing_fallback_call_is_isolated(self):
        members = [10, 20, 30]

        def _side(n, *a, **k):
            if n == 20:
                raise RuntimeError("gh boom")
            return self._ages_of(n)
        with m.patch.object(airuleset, "_stream_self_login", return_value="me"), \
                m.patch.object(airuleset, "_ops_wait_prefetch_comments",
                               return_value={}), \
                m.patch.object(airuleset, "_issue_comment_ages",
                               side_effect=_side):
            ages_fn = cli_quals.ops_wait_ages_fn(members, ROOT, ["label:x"])
            # the non-failing members still resolve (isolated failure)
            self.assertEqual(ages_fn(10), self._ages_of(10))
            self.assertEqual(ages_fn(30), self._ages_of(30))
            # the failing member behaves exactly as the lazy path: it re-raises
            with self.assertRaises(RuntimeError):
                ages_fn(20)

    def test_empty_member_set_makes_no_fallback_calls(self):
        with m.patch.object(airuleset, "_stream_self_login", return_value="me"), \
                m.patch.object(airuleset, "_ops_wait_prefetch_comments",
                               return_value={}), \
                m.patch.object(airuleset, "_issue_comment_ages") as ica:
            cli_quals.ops_wait_ages_fn([], ROOT, ["label:x"])
            ica.assert_not_called()


class SpawnRefresher(unittest.TestCase):
    """#1067 slice 1c REVIEW: the primary spawn is a transient `systemd-run
    --user` unit (own cgroup, survives the KillMode=control-group oneshot exit);
    a logged Popen fallback covers a non-systemd box."""

    def _patch_paths(self):
        return (m.patch.object(owref, "cache_path", return_value="/c/x.json"),
                m.patch.object(owref, "pid_path", return_value="/c/x.pid"))

    def test_primary_path_is_a_systemd_run_user_unit(self):
        calls = {}

        def fake_run(argv, **kw):
            calls["argv"] = argv
            calls["env"] = kw.get("env")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        popen = m.Mock()
        cp, pp = self._patch_paths()
        with cp, pp:
            owref.spawn_refresher("/repo/a", "core-quals", argv0="/root/airuleset.py",
                                  run_fn=fake_run, popen_fn=popen)
        argv = calls["argv"]
        self.assertEqual(argv[0], "systemd-run")
        self.assertIn("--user", argv)
        self.assertIn("--collect", argv)
        unit = argv[argv.index("--unit") + 1]
        self.assertTrue(unit.startswith("airuleset-opswait-"), unit)
        self.assertEqual(argv[argv.index("--working-directory") + 1], "/root")
        prop = argv[argv.index("--property") + 1]
        self.assertEqual(prop, "RuntimeMaxSec=%d" % (owref.CHILD_TIMEOUT_S + 30))
        # the child argv follows the `--` separator
        child = argv[argv.index("--") + 1:]
        self.assertIn("-m", child)
        self.assertIn("watchdog.ops_wait_refresh", child)
        self.assertEqual(child[child.index("--target") + 1], "/repo/a")
        self.assertEqual(child[child.index("--cmd") + 1], "core-quals")
        self.assertEqual(child[child.index("--cache") + 1], "/c/x.json")
        self.assertEqual(child[child.index("--pid") + 1], "/c/x.pid")
        self.assertEqual(child[child.index("--argv0") + 1], "/root/airuleset.py")
        # review F1: the derivation's env (PATH incl. the gh shim, HOME) is
        # forwarded INTO the unit via --setenv (a --user transient unit does NOT
        # inherit the caller's env), not merely handed to the systemd-run client.
        self.assertTrue(any(a.startswith("--setenv=PATH=") for a in argv), argv)
        self.assertTrue(any(a.startswith("--setenv=HOME=") for a in argv), argv)
        # the client env carries the user bus for systemd-run itself (issue 826)
        self.assertIsNotNone(calls["env"])
        self.assertIn("XDG_RUNTIME_DIR", calls["env"])
        popen.assert_not_called()   # no cgroup-bound fallback on the primary path

    def test_unit_setenv_forwards_derivation_env_only(self):
        src = {"PATH": "/home/s/.local/bin:/usr/bin", "HOME": "/home/s",
               "GH_TOKEN": "tok", "GITHUB_TOKEN": "g", "XDG_RUNTIME_DIR": "/run/user/9",
               "LANG": "en_US.UTF-8", "IRRELEVANT": "x", "PYTHONPATH": "/y"}
        args = owref._unit_setenv_args(src)
        got = dict(a[len("--setenv="):].split("=", 1) for a in args)
        # the derivation's env is forwarded (PATH incl. the ~/.local/bin gh shim)
        self.assertEqual(got["PATH"], "/home/s/.local/bin:/usr/bin")
        self.assertEqual(got["HOME"], "/home/s")
        self.assertEqual(got["GH_TOKEN"], "tok")
        self.assertEqual(got["GITHUB_TOKEN"], "g")
        self.assertEqual(got["LANG"], "en_US.UTF-8")
        # unrelated env is NOT forwarded
        self.assertNotIn("IRRELEVANT", got)
        self.assertNotIn("PYTHONPATH", got)

    def test_unit_already_exists_is_benign_single_flight(self):
        def fake_run(argv, **kw):
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="Unit airuleset-opswait-x.service already exists.")
        popen = m.Mock()
        cp, pp = self._patch_paths()
        with cp, pp:
            owref.spawn_refresher("/repo/a", "slice-quals", argv0="/root/airuleset.py",
                                  run_fn=fake_run, popen_fn=popen)
        popen.assert_not_called()   # already running -> no fallback, no double-run

    def test_systemd_run_absent_falls_back_to_popen_and_logs(self):
        def fake_run(argv, **kw):
            raise FileNotFoundError("systemd-run")
        popen = m.Mock()
        logs = []
        cp, pp = self._patch_paths()
        with cp, pp:
            owref.spawn_refresher("/repo/a", "slice-quals", argv0="/root/airuleset.py",
                                  run_fn=fake_run, popen_fn=popen,
                                  log_fn=logs.append)
        popen.assert_called_once()
        self.assertTrue(any("popen-fallback" in ln and "absent" in ln
                            for ln in logs), logs)
        # the fallback still runs the same -m child, detached
        argv = popen.call_args[0][0]
        self.assertIn("watchdog.ops_wait_refresh", argv)
        self.assertTrue(popen.call_args[1].get("start_new_session"))

    def test_systemd_run_error_falls_back_to_popen_and_logs(self):
        def fake_run(argv, **kw):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        popen = m.Mock()
        logs = []
        cp, pp = self._patch_paths()
        with cp, pp:
            owref.spawn_refresher("/repo/a", "slice-quals", argv0="/root/airuleset.py",
                                  run_fn=fake_run, popen_fn=popen,
                                  log_fn=logs.append)
        popen.assert_called_once()
        self.assertTrue(any("popen-fallback" in ln and "rc=1" in ln
                            for ln in logs), logs)


if __name__ == "__main__":
    unittest.main()
