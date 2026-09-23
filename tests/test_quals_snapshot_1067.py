"""#1067 slice 1d — ONE detached refresher computes ALL watchdog quals facts.

After slice 1c only the ops-wait fetch was off the sweep path; the backlog
(`--count`, 17.7 s live on montalu1) and dispatchable (`--count-dispatchable`,
19.7 s) fetches still ran the full derivation as BLOCKING subprocesses inside
`goal_lane_sweep` (30 s of a 39 s sweep). This suite locks the replacement:

* `core-quals|slice-quals --snapshot-json` prints `{open_count, i_members,
  dispatchable_count, dispatchable_reason, ops_wait_members}` from ONE
  `_partition_workable` pass, byte-for-byte consistent with the separate
  `--count` / `--count-dispatchable` / `--ops-wait` outputs (parity, #367).
* the detached child (`watchdog.ops_wait_refresh`) runs that ONE command and
  writes ONE versioned per-repo snapshot, preserving prior good fields on a
  failure.
* `airuleset._watchdog_backlog_fetch` / `_watchdog_dispatchable_fetch` /
  `_watchdog_ops_wait_fetch` are non-blocking snapshot readers: a stale
  snapshot makes ZERO blocking quals subprocess calls and spawns the refresher
  ONCE (single-flight across the three readers in one sweep); a missing
  snapshot reads backlog as None (unmeasurable), never 0.

RED against the pre-1d tree: `--snapshot-json`, `read_snapshot`,
`backlog_count`, `dispatchable` and the v2 snapshot do not exist, and both
fetches shell a blocking `subprocess.run`.
"""
import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock as mk
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_quals_cmd  # noqa: E402
from watchdog import ops_wait_refresh as owref  # noqa: E402

NOW = 1_000_000
CWD = "/repo/a"


def _snap(open_count=3, i_members=(11, 12, 13), dc=2, dr=None,
          members=None, ts=NOW - 10, v=None):
    return {"v": owref.SNAPSHOT_VERSION if v is None else v,
            "ts": ts, "members_ts": ts,
            "open_count": open_count, "i_members": list(i_members),
            "dispatchable_count": dc, "dispatchable_reason": dr,
            "members": [{"number": 41}] if members is None else members}


class _HomeCase(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self._env = mk.patch.dict(os.environ, {"HOME": self._td.name})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._td.cleanup()

    def _write(self, obj, cwd=CWD):
        with open(owref.cache_path(cwd), "w") as f:
            json.dump(obj, f)

    def _read(self, cwd=CWD):
        with open(owref.cache_path(cwd)) as f:
            return json.load(f)


class SnapshotReaders(_HomeCase):
    """Fresh snapshot -> each reader returns its OWN field, no spawn."""

    def _kw(self, spy):
        return dict(now=NOW, spawn_fn=spy, alive_fn=lambda c: False)

    def test_fresh_snapshot_serves_every_field_without_spawning(self):
        self._write(_snap(open_count=7, dc=0, dr="dep-wait",
                          members=[{"number": 41, "stale": True}]))
        spy = mk.Mock()
        self.assertEqual(owref.backlog_count(CWD, "slice-quals", **self._kw(spy)), 7)
        self.assertEqual(owref.dispatchable(CWD, "slice-quals", **self._kw(spy)),
                         [{"count": 0, "reason": "dep-wait"}])
        self.assertEqual(
            owref.fetch_or_refresh(CWD, "slice-quals", object(), **self._kw(spy)),
            [{"number": 41, "stale": True}])
        spy.assert_not_called()

    def test_zero_backlog_from_a_good_snapshot_is_zero(self):
        # a real, refusal-guarded 0 from the CLI IS trusted (it passed #181)
        self._write(_snap(open_count=0, i_members=()))
        self.assertEqual(owref.backlog_count(CWD, "core-quals", now=NOW,
                                             spawn_fn=mk.Mock(),
                                             alive_fn=lambda c: True), 0)

    def test_unmeasurable_dispatchable_keeps_its_reason(self):
        self._write(_snap(dc=None, dr="meta read failed"))
        self.assertEqual(
            owref.dispatchable(CWD, "core-quals", now=NOW, spawn_fn=mk.Mock(),
                               alive_fn=lambda c: True),
            [{"count": None, "reason": "meta read failed"}])

    def test_reasonless_unmeasurable_dispatchable_is_none(self):
        self._write(_snap(dc=None, dr=None))
        self.assertIsNone(owref.dispatchable(
            CWD, "core-quals", now=NOW, spawn_fn=mk.Mock(),
            alive_fn=lambda c: True))

    def test_no_snapshot_backlog_is_none_never_zero_and_spawns_once(self):
        spy = mk.Mock()
        self.assertIsNone(owref.backlog_count(CWD, "slice-quals", now=NOW,
                                              spawn_fn=spy,
                                              alive_fn=lambda c: False))
        self.assertEqual(spy.call_count, 1)

    def test_error_only_entry_backlog_is_none(self):
        self._write({"ts": NOW - 5, "error": True})
        self.assertIsNone(owref.backlog_count(CWD, "slice-quals", now=NOW,
                                              spawn_fn=mk.Mock(),
                                              alive_fn=lambda c: True))

    def test_legacy_members_only_entry_is_due_and_has_no_backlog(self):
        # a slice-1c cache (members only, no v) must be upgraded at once, not
        # held for a full TTL with backlog unreadable.
        self._write({"ts": NOW - 5, "members": [], "members_ts": NOW - 5})
        spy = mk.Mock()
        self.assertIsNone(owref.backlog_count(CWD, "slice-quals", now=NOW,
                                              spawn_fn=spy,
                                              alive_fn=lambda c: False))
        self.assertEqual(spy.call_count, 1)

    def test_snapshot_past_max_serve_age_goes_honest(self):
        old = NOW - owref.MAX_SERVE_AGE_S - 1
        entry = _snap(open_count=9)
        entry.update({"ts": NOW - 5, "error": True, "members_ts": old})
        self._write(entry)
        kw = dict(now=NOW, spawn_fn=mk.Mock(), alive_fn=lambda c: True)
        self.assertIsNone(owref.backlog_count(CWD, "core-quals", **kw))
        self.assertIsNone(owref.dispatchable(CWD, "core-quals", **kw))

    def test_snapshot_ttl_is_the_short_consumer_window(self):
        # ONE snapshot TTL, the shortest the consumers need (~5 min).
        self.assertLessEqual(owref.REFRESH_TTL_S, 5 * 60)
        self.assertTrue(owref._spawn_due(_snap(ts=NOW - owref.REFRESH_TTL_S),
                                         NOW))
        self.assertFalse(owref._spawn_due(_snap(ts=NOW - 10), NOW))

    def test_three_readers_in_one_sweep_spawn_exactly_once(self):
        # single-flight: the pidfile is not yet written by a just-launched
        # child, so the in-process guard must stop readers 2 and 3 re-spawning.
        spy = mk.Mock()
        kw = dict(now=NOW, spawn_fn=spy, alive_fn=lambda c: False)
        owref.backlog_count(CWD, "core-quals", **kw)
        owref.dispatchable(CWD, "core-quals", **kw)
        owref.fetch_or_refresh(CWD, "core-quals", object(), **kw)
        self.assertEqual(spy.call_count, 1)


class RefreshChildSnapshot(_HomeCase):
    def test_ok_writes_the_whole_snapshot(self):
        out = json.dumps({"open_count": 2, "i_members": [11, 12],
                          "dispatchable_count": 1,
                          "dispatchable_reason": None,
                          "ops_wait_members": [{"number": 41, "stale": False}]})
        owref.run_refresh_child(CWD, "core-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (0, out))
        e = self._read()
        self.assertEqual(e["v"], owref.SNAPSHOT_VERSION)
        self.assertEqual(e["open_count"], 2)
        self.assertEqual(e["i_members"], [11, 12])
        self.assertEqual(e["dispatchable_count"], 1)
        self.assertEqual(e["members"], [{"number": 41, "stale": False}])
        self.assertEqual(e["members_ts"], e["ts"])
        self.assertFalse(os.path.exists(owref.pid_path(CWD)))

    def test_failure_preserves_every_prior_good_field(self):
        self._write(_snap(open_count=5, dc=3, ts=NOW - 100))
        owref.run_refresh_child(CWD, "core-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (1, ""))
        e = self._read()
        self.assertTrue(e["error"])
        self.assertEqual(e["open_count"], 5)
        self.assertEqual(e["dispatchable_count"], 3)
        self.assertEqual(e["members_ts"], NOW - 100)   # age carried forward

    def test_bool_open_count_is_rejected_as_malformed(self):
        bad = json.dumps({"open_count": True, "i_members": [],
                          "dispatchable_count": 0, "dispatchable_reason": None,
                          "ops_wait_members": []})
        owref.run_refresh_child(CWD, "core-quals", owref.cache_path(CWD),
                                owref.pid_path(CWD), "/x/airuleset.py",
                                run_fn=lambda *a: (0, bad))
        e = self._read()
        self.assertTrue(e["error"])
        self.assertNotIn("open_count", e)

    def test_default_run_invokes_snapshot_json(self):
        with mk.patch("subprocess.run") as run:
            run.return_value = mk.Mock(returncode=0, stdout="{}")
            owref._default_run("/t", "slice-quals", "/x/airuleset.py")
        argv = run.call_args[0][0]
        self.assertEqual(argv[-2:], ["slice-quals", "--snapshot-json"])
        self.assertEqual(run.call_args[1]["timeout"], owref.CHILD_TIMEOUT_S)


class WatchdogFetchesNeverBlock(_HomeCase):
    """The three airuleset fetches, driven through job 20's per-pane body
    (`goal_lane_occupancy_nudge`, the `goal_lane_sweep` loop body) with a
    STALE snapshot: ZERO blocking subprocess calls, the spawn seam once."""

    def test_stale_snapshot_makes_zero_blocking_quals_calls(self):
        from tempfile import TemporaryDirectory as _TD
        import watchdog as wd
        from watchdog import goal
        from _goal_arm_helpers import (DeliverGoalFakeTmux, GOAL_ARMED_CAP,
                                       _encode, _write_marker_transcript)
        self._write(_snap(ts=NOW - owref.REFRESH_TTL_S - 1, open_count=37,
                          dc=4))
        spawn = mk.Mock()
        d = _TD()
        self.addCleanup(d.cleanup)
        proj = Path(d.name)
        cwd, sid = CWD, "sess-1067-1d"
        _write_marker_transcript(proj, cwd, sid)
        tpath = proj / _encode(cwd) / (sid + ".jsonl")
        tmux = DeliverGoalFakeTmux([("%9", "claude", cwd, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=tpath)

        def _no_blocking(*a, **k):
            raise AssertionError("blocking subprocess on the sweep path: %r"
                                 % (a[:1],))

        with mk.patch.object(airuleset, "_repo_root", return_value=cwd), \
                mk.patch.object(airuleset, "resolve_authority",
                                return_value="full"), \
                mk.patch("statusbar.obligation_count",
                         return_value=(None, None)), \
                mk.patch("statusbar._spawn_refresh"), \
                mk.patch.object(owref, "spawn_refresher", spawn), \
                mk.patch.object(owref, "refresher_alive",
                                lambda c, now=None: False), \
                mk.patch.object(wd, "count_live_workers",
                                return_value=(0, [])), \
                mk.patch("subprocess.run", side_effect=_no_blocking), \
                mk.patch("subprocess.Popen", side_effect=_no_blocking):
            logs, _owns = goal.goal_lane_occupancy_nudge(
                NOW, tmux, {}, sid, cwd, "111", GOAL_ARMED_CAP, tpath,
                NOW - 100, "loc", None, False, None, proj,
                backlog_fetch=airuleset._watchdog_backlog_fetch, state={},
                sleep_fn=lambda s: None,
                dispatchable_fetch=airuleset._watchdog_dispatchable_fetch)
            airuleset._watchdog_ops_wait_fetch(cwd)
        self.assertEqual(spawn.call_count, 1, logs)
        self.assertEqual(spawn.call_args[0][1], "core-quals")
        # the stale-but-served snapshot fed the decision (backlog 37, 4 cands)
        self.assertFalse(any("skip:dispatchable-unknown" in ln for ln in logs),
                         logs)

    def test_backlog_fetch_without_snapshot_is_none(self):
        with mk.patch.object(airuleset, "_repo_root", return_value=CWD), \
                mk.patch.object(airuleset, "resolve_authority",
                                return_value="branch-merge"), \
                mk.patch("statusbar.obligation_count",
                         return_value=(None, None)), \
                mk.patch("statusbar._spawn_refresh"), \
                mk.patch.object(owref, "spawn_refresher") as spawn, \
                mk.patch.object(owref, "refresher_alive",
                                lambda c, now=None: False), \
                mk.patch("subprocess.run") as run:
            self.assertIsNone(airuleset._watchdog_backlog_fetch(CWD))
            self.assertIsNone(airuleset._watchdog_dispatchable_fetch(CWD))
        run.assert_not_called()
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(spawn.call_args[0][1], "slice-quals")


# ---- --snapshot-json parity with the four separate CLI outputs -------------

_ROWS = {
    11: {"number": 11, "title": "core a", "labels": [],
         "createdAt": "2026-07-01T00:00:00Z"},
    12: {"number": 12, "title": "core b (dep-wait)", "labels": [],
         "createdAt": "2026-07-02T00:00:00Z"},
    41: {"number": 41, "title": "parked", "labels": [{"name": "ops-wait"}],
         "createdAt": "2026-07-03T00:00:00Z"},
    # the gk-handoff! tag comes from the patched flag sets (a real
    # needs-gatekeeper label would route 43 to I, #943 precedence)
    43: {"number": 43, "title": "parked gk", "labels": [{"name": "ops-wait"}],
         "createdAt": "2026-07-04T00:00:00Z"},
}
_FLAGS = ({41}, set(), {43}, set(), set(), set(), set(), set(), set())
_ARGS = dict(count=False, list=False, waiting=False, ops_wait=False,
             audit=False, dep_wait=False, count_dispatchable=False,
             list_dispatchable=False, snapshot_json=False, bounces=False,
             unhandled=False, task_hygiene=False, extra=None, role=None)


class SnapshotJsonParity(unittest.TestCase):
    def _run(self, cmd, dep_ok=True, **flag):
        args = dict(_ARGS)
        args.update(flag)
        out = io.StringIO()
        dep = (({12: ["o/r#99"]}, "o/r", True) if dep_ok
               else ({}, "o/r", False))
        union = mk.Mock(return_value=(dict(_ROWS), False))
        with mk.patch.object(airuleset, "_repo_root", return_value="/r"), \
                mk.patch.object(airuleset, "resolve_authority",
                                return_value="full"), \
                mk.patch.object(airuleset, "_obligation_quals",
                                return_value=["q"]), \
                mk.patch.object(airuleset, "_union_open_issues", union), \
                mk.patch.object(cli_quals_cmd, "_merged_unreleased",
                                return_value=frozenset()), \
                mk.patch.object(cli_quals_cmd, "_dep_wait_map_for",
                                return_value=dep), \
                mk.patch.object(cli_quals_cmd, "_ops_wait_flag_sets",
                                return_value=_FLAGS), \
                contextlib.redirect_stdout(out):
            cmd(mk.Mock(**args))
        return out.getvalue(), union.call_count

    def test_core_quals_snapshot_matches_the_separate_outputs(self):
        cmd = cli_quals_cmd.cmd_core_quals
        snap_out, n_derivations = self._run(cmd, snapshot_json=True)
        snap = json.loads(snap_out)
        self.assertEqual(n_derivations, 1)                   # ONE derivation
        count_out, _ = self._run(cmd, count=True)
        self.assertEqual(snap["open_count"], int(count_out.strip()))
        self.assertEqual(snap["i_members"], [11, 12])
        disp_out, _ = self._run(cmd, count_dispatchable=True)
        lines = disp_out.split()
        self.assertEqual(snap["dispatchable_count"], int(lines[0]))
        self.assertIsNone(snap["dispatchable_reason"])
        ow_out, _ = self._run(cmd, ops_wait=True)
        self.assertEqual(snap["ops_wait_members"], owref.parse_members(ow_out))
        by = {m["number"]: m for m in snap["ops_wait_members"]}
        self.assertTrue(by[41]["stale"])
        self.assertTrue(by[43]["gk_handoff"])

    def test_unmeasurable_dispatchable_parity(self):
        cmd = cli_quals_cmd.cmd_core_quals
        snap = json.loads(self._run(cmd, dep_ok=False, snapshot_json=True)[0])
        disp_out, _ = self._run(cmd, dep_ok=False, count_dispatchable=True)
        self.assertEqual(disp_out.strip(),
                         "unmeasurable:" + snap["dispatchable_reason"])
        self.assertIsNone(snap["dispatchable_count"])

    def test_slice_quals_snapshot_matches_count(self):
        def _run(**flag):
            args = dict(_ARGS)
            args.update(flag)
            out = io.StringIO()
            handed = {n: False for n in _ROWS}
            with mk.patch.object(airuleset, "_repo_root", return_value="/r"), \
                    mk.patch.object(airuleset, "resolve_authority",
                                    return_value="branch-merge"), \
                    mk.patch.object(airuleset, "_current_user",
                                    return_value="montalu1"), \
                    mk.patch.object(airuleset, "_slice_quals",
                                    return_value=["label:stream:montalu1"]), \
                    mk.patch.object(airuleset, "_repo_slug",
                                    return_value="o/r"), \
                    mk.patch.object(airuleset, "_slice_mine_and_handed",
                                    return_value=(dict(_ROWS), handed, False)), \
                    mk.patch.object(cli_quals_cmd, "_merged_unreleased",
                                    return_value=frozenset()), \
                    mk.patch.object(cli_quals_cmd, "_dep_wait_map_for",
                                    return_value=({}, "o/r", True)), \
                    mk.patch.object(cli_quals_cmd, "_ops_wait_flag_sets",
                                    return_value=_FLAGS), \
                    contextlib.redirect_stdout(out):
                cli_quals_cmd.cmd_slice_quals(mk.Mock(**args))
            return out.getvalue()
        snap = json.loads(_run(snapshot_json=True))
        self.assertEqual(snap["open_count"], int(_run(count=True).strip()))
        self.assertEqual(snap["dispatchable_count"],
                         int(_run(count_dispatchable=True).split()[0]))
        self.assertEqual(snap["ops_wait_members"],
                         owref.parse_members(_run(ops_wait=True)))

    def test_a_failed_query_prints_no_snapshot(self):
        # the refuse contract: a gh failure exits non-zero, never a JSON 0.
        args = dict(_ARGS, snapshot_json=True)
        out = io.StringIO()
        with mk.patch.object(airuleset, "_repo_root", return_value="/r"), \
                mk.patch.object(airuleset, "resolve_authority",
                                return_value="full"), \
                mk.patch.object(airuleset, "_obligation_quals",
                                return_value=["q"]), \
                mk.patch.object(airuleset, "_union_open_issues",
                                return_value=({}, True)), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                cli_quals_cmd.cmd_core_quals(mk.Mock(**args))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_unparseable_ops_wait_listing_refuses_a_partial_snapshot(self):
        import cli_quals_snapshot as qs
        out = io.StringIO()
        with mk.patch.object(cli_quals_cmd, "_emit_ops_wait",
                             lambda *a: print("garbage-row")), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                qs.emit_snapshot_json({}, {}, "/r", ["q"], None)
        self.assertNotEqual(cm.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_parse_snapshot_rejects_a_reasonless_unmeasurable_count(self):
        base = {"open_count": 1, "i_members": [1], "ops_wait_members": [],
                "dispatchable_count": None, "dispatchable_reason": None}
        self.assertIsNone(owref.parse_snapshot(json.dumps(base)))
        base["dispatchable_reason"] = "meta read failed"
        self.assertEqual(owref.parse_snapshot(json.dumps(base)), base)
        self.assertIsNone(owref.parse_snapshot("not json"))

    def test_cli_parser_accepts_snapshot_json(self):
        # the shared core-quals/slice-quals flag helper carries the new mode
        import argparse
        p = argparse.ArgumentParser()
        airuleset._add_dispatch_flags(p)
        self.assertIs(p.parse_args(["--snapshot-json"]).snapshot_json, True)
        self.assertIs(p.parse_args([]).snapshot_json, False)


if __name__ == "__main__":
    unittest.main()
