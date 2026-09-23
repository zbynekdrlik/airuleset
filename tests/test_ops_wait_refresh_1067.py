"""#1067 slice 1c (a) — the DETACHED ops-wait refresher.

The job-20 ops-wait fetch used to run the `--ops-wait` derivation as a BLOCKING
subprocess (timeout=35) inside the 90 s sweep, so a slow derivation (58 s live on
montalu1) timed out every stale-cache sweep and job 20 logged `w:?`. This suite
locks the replacement: `watchdog.ops_wait_refresh.fetch_or_refresh` NEVER blocks
— it reads a per-repo atomic cache file and, when stale AND no refresher child is
alive, spawns EXACTLY ONE detached child (via an injected spawn seam), returning
the last good result / None / the timeout sentinel; the child writes the parsed
members back atomically and preserves the prior good set on a failure/timeout.

RED against the pre-1c tree: `_watchdog_ops_wait_fetch` ran a subprocess and had
no file cache / refresher at all, so every one of these (stale→one detached
spawn, alive→no spawn, cache-file→served-next-call, dead-lock→reclaimed) fails.
"""
import json
import os
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watchdog import ops_wait_refresh as owref  # noqa: E402

NOW = 1_000_000
CWD = "/repo/a"
SENTINEL = object()   # stands in for ops_wait_recheck.FETCH_TIMEOUT


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self._home = self._td.name
        self._env = m.patch.dict(os.environ, {"HOME": self._home})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def _write_cache(self, obj, cwd=CWD):
        with open(owref.cache_path(cwd), "w") as f:
            json.dump(obj, f)

    def _read_cache(self, cwd=CWD):
        with open(owref.cache_path(cwd)) as f:
            return json.load(f)


class FetchOrRefresh(_HomeCase):
    def test_cold_cache_spawns_exactly_one_and_returns_none(self):
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertIsNone(out)                 # no result yet -> undetermined
        self.assertEqual(spy.call_count, 1)    # exactly one detached spawn
        self.assertEqual(spy.call_args[0][0], CWD)

    def test_no_spawn_while_a_child_is_alive(self):
        self._write_cache({"ts": NOW - 40 * 60, "members": [{"number": 7}]})
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: True)
        self.assertEqual(spy.call_count, 0)                # no second spawn
        self.assertEqual(out, [{"number": 7}])             # last good served

    def test_stale_cache_spawns_and_serves_last_good(self):
        self._write_cache({"ts": NOW - 40 * 60, "members": [{"number": 7}]})
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "core-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(out, [{"number": 7}])   # non-blocking: last good, not None

    def test_fresh_cache_no_spawn(self):
        self._write_cache({"ts": NOW - 60, "members": [{"number": 9}]})
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertEqual(spy.call_count, 0)
        self.assertEqual(out, [{"number": 9}])

    def test_cache_file_written_by_child_is_returned_next_call(self):
        spy = m.Mock()
        # first call: cold -> spawn, None
        self.assertIsNone(owref.fetch_or_refresh(
            CWD, "slice-quals", SENTINEL, now=NOW, spawn_fn=spy,
            alive_fn=lambda c: False))
        # child completes and writes the file
        self._write_cache({"ts": NOW + 5, "members": [{"number": 11}]})
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW + 6,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertEqual(out, [{"number": 11}])
        self.assertEqual(spy.call_count, 1)    # fresh now -> still just the one

    def test_cold_timeout_returns_sentinel(self):
        self._write_cache({"ts": NOW - 120, "timeout": True})   # no prior members
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertIs(out, SENTINEL)
        self.assertEqual(spy.call_count, 1)    # failed entry re-checks after fail-TTL

    def test_error_entry_still_serves_last_good_members(self):
        self._write_cache({"ts": NOW, "error": True, "members": [{"number": 3}]})
        spy = m.Mock()
        out = owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                     spawn_fn=spy, alive_fn=lambda c: False)
        self.assertEqual(out, [{"number": 3}])


class SpawnDueAndServe(_HomeCase):
    def test_spawn_due_success_uses_full_ttl(self):
        self.assertFalse(owref._spawn_due({"ts": NOW, "members": []},
                                          NOW + owref.REFRESH_TTL_S - 1))
        self.assertTrue(owref._spawn_due({"ts": NOW, "members": []},
                                         NOW + owref.REFRESH_TTL_S))

    def test_spawn_due_failed_entry_uses_fail_ttl(self):
        self.assertFalse(owref._spawn_due({"ts": NOW, "error": True},
                                          NOW + owref.REFRESH_FAIL_TTL_S - 1))
        self.assertTrue(owref._spawn_due({"ts": NOW, "timeout": True},
                                         NOW + owref.REFRESH_FAIL_TTL_S))

    def test_spawn_due_missing_or_typeless(self):
        self.assertTrue(owref._spawn_due(None, NOW))
        self.assertTrue(owref._spawn_due({"ts": "bad"}, NOW))


class RefresherAlive(_HomeCase):
    def _write_pid(self, obj, cwd=CWD):
        with open(owref.pid_path(cwd), "w") as f:
            json.dump(obj, f)

    def test_alive_for_our_own_live_pid(self):
        self._write_pid({"pid": os.getpid(), "ts": NOW})
        self.assertTrue(owref.refresher_alive(CWD, now=NOW))

    def test_dead_pid_reclaims_the_lock(self):
        self._write_pid({"pid": 2 ** 31 - 1, "ts": NOW})   # no /proc entry
        self.assertFalse(owref.refresher_alive(CWD, now=NOW))
        self.assertFalse(os.path.exists(owref.pid_path(CWD)))  # reclaimed

    def test_too_old_record_allows_a_fresh_spawn(self):
        self._write_pid({"pid": os.getpid(),
                         "ts": NOW - owref.CHILD_MAX_AGE_S - 10})
        self.assertFalse(owref.refresher_alive(CWD, now=NOW))

    def test_missing_pidfile_not_alive(self):
        self.assertFalse(owref.refresher_alive(CWD, now=NOW))

    def test_malformed_pidfile_reclaimed(self):
        self._write_pid({"pid": "nope", "ts": NOW})
        self.assertFalse(owref.refresher_alive(CWD, now=NOW))
        self.assertFalse(os.path.exists(owref.pid_path(CWD)))


class RunRefreshChild(_HomeCase):
    def test_ok_writes_members_and_clears_pidfile(self):
        tsv = ("41\t2026-01-01T00:00:00Z\taction-only\tops-wait\tt\n"
               "43\t2026-01-01T00:00:00Z\taction-only\tops-wait stale!\tt\n")
        owref.run_refresh_child(CWD, "slice-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (0, tsv))
        entry = self._read_cache()
        self.assertEqual([mm["number"] for mm in entry["members"]], [41, 43])
        self.assertTrue(entry["members"][1]["stale"])
        self.assertFalse(os.path.exists(owref.pid_path(CWD)))

    def test_timeout_preserves_prior_members(self):
        self._write_prior([{"number": 5}])

        def _raise(*a):
            raise owref._Timeout()

        owref.run_refresh_child(CWD, "slice-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=_raise)
        entry = self._read_cache()
        self.assertTrue(entry.get("timeout"))
        self.assertEqual(entry["members"], [{"number": 5}])   # prior preserved

    def test_error_preserves_prior_members(self):
        self._write_prior([{"number": 6}])
        owref.run_refresh_child(CWD, "slice-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (1, ""))
        entry = self._read_cache()
        self.assertTrue(entry.get("error"))
        self.assertEqual(entry["members"], [{"number": 6}])

    def test_malformed_output_is_error_not_a_partial_set(self):
        owref.run_refresh_child(CWD, "slice-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (0, "not-an-int-line\n"))
        entry = self._read_cache()
        self.assertTrue(entry.get("error"))
        self.assertNotIn("members", entry)   # no prior -> no members key

    def _write_prior(self, members, cwd=CWD):
        with open(owref.cache_path(cwd), "w") as f:
            json.dump({"ts": NOW - 10, "members": members}, f)


class ParseMembers(unittest.TestCase):
    def test_summary_line_skipped_not_malformed(self):
        out = ("41\t2026-08-01T00:00:00Z\timplement\tops-wait\tklient\n"
               "# W-summary: total=1 oldest=#41\n")
        members = owref.parse_members(out)
        self.assertEqual([mm["number"] for mm in members], [41])

    def test_malformed_line_yields_none(self):
        self.assertIsNone(owref.parse_members("41\tx\ty\tz\tt\ngarbage\n"))

    def test_empty_is_empty_list(self):
        self.assertEqual(owref.parse_members(""), [])


class NonBlocking(_HomeCase):
    def test_default_path_never_runs_a_subprocess(self):
        # The spawn seam is the ONLY place a child is created; fetch_or_refresh
        # itself must do no subprocess.run (the whole point of slice 1c (a)).
        with m.patch("subprocess.run") as run, \
                m.patch("subprocess.Popen") as popen:
            owref.fetch_or_refresh(CWD, "slice-quals", SENTINEL, now=NOW,
                                   spawn_fn=m.Mock(), alive_fn=lambda c: False)
            run.assert_not_called()
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
