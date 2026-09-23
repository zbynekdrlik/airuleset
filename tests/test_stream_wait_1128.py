"""#1128 — `airuleset.py stream-wait`: the ONE cheap idle waiter a sub-dev
stream loop keeps live instead of ending on an empty slice.

It polls the stream's OWN slice through the SAME search `slice-quals` runs
(`_slice_quals` quals unioned by `_union_open_issues` over
`AUTOPILOT_SKIP_EXCL` — never a parallel derivation), fingerprints each open
ticket as (number, updatedAt, labels) — `updatedAt` moves on every comment,
label change or reopen — and exits 0 on the FIRST fingerprint change, or at
`--max` as a heartbeat so a dead waiter is always replaced. A gh error is never
a change; a gh-rate backoff skips the poll. Everything here runs against a
fake fetch + fake clock: no gh, no network, no tmux.
"""

import io
import os
import subprocess
import sys
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
import cli_stream_wait as sw  # noqa: E402
from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def row(n, upd="2026-09-23T10:00:00Z", labels=("stream:david1",)):
    return {"number": n, "updatedAt": upd,
            "labels": [{"name": lb} for lb in labels]}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class ScriptedFetch:
    """Returns the scripted (rows, err) results in order; the LAST repeats."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        i = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[i]


def run_wait(results, interval=300, max_s=3600, backoff=lambda: 0):
    clock = Clock()
    fetch = ScriptedFetch(results)
    verdict, lines, stats = sw.wait_for_change(
        fetch, interval=interval, max_s=max_s, now_fn=clock.now,
        sleep_fn=clock.sleep, backoff_fn=backoff)
    return verdict, lines, stats, fetch, clock


class TestFingerprint(unittest.TestCase):
    def test_fingerprint_is_number_updated_and_sorted_labels(self):
        fp = sw.fingerprint({7: row(7, "T1", ("b", "a"))})
        self.assertEqual(fp, {7: ("T1", ("a", "b"))})

    def test_label_order_never_reads_as_a_change(self):
        a = sw.fingerprint({7: row(7, "T1", ("a", "b"))})
        b = sw.fingerprint({7: row(7, "T1", ("b", "a"))})
        self.assertEqual(a, b)

    def test_title_or_other_fields_are_not_part_of_the_fingerprint(self):
        r1 = dict(row(7, "T1"), title="old")
        r2 = dict(row(7, "T1"), title="new")
        self.assertEqual(sw.fingerprint({7: r1}), sw.fingerprint({7: r2}))


class TestWaitForChange(unittest.TestCase):
    def test_identical_fingerprint_stays_until_max_then_heartbeats(self):
        verdict, lines, stats, fetch, clock = run_wait(
            [({1: row(1)}, None)], interval=300, max_s=3600)
        self.assertEqual(verdict, "heartbeat")
        self.assertEqual(lines, [])
        # baseline at t=0, then a poll after every 300 s sleep up to 3600 s
        self.assertEqual(fetch.calls, 13)
        self.assertEqual(sum(clock.sleeps), 3600)
        self.assertTrue(all(0 < s <= 300 for s in clock.sleeps), clock.sleeps)

    def test_updated_at_change_exits_changed(self):
        verdict, lines, _s, fetch, clock = run_wait([
            ({1: row(1, "T1")}, None),
            ({1: row(1, "T1")}, None),
            ({1: row(1, "T2")}, None),
        ])
        self.assertEqual(verdict, "changed")
        self.assertEqual(fetch.calls, 3)
        self.assertEqual(sum(clock.sleeps), 600)
        self.assertTrue(any("#1" in ln and "updated" in ln for ln in lines), lines)

    def test_label_change_exits_changed_and_names_the_label(self):
        verdict, lines, *_ = run_wait([
            ({5: row(5, "T1", ("stream:david1", "ready-for-review"))}, None),
            ({5: row(5, "T1", ("stream:david1", "prio:bounce"))}, None),
        ])
        self.assertEqual(verdict, "changed")
        joined = " ".join(lines)
        self.assertIn("#5", joined)
        self.assertIn("+prio:bounce", joined)
        self.assertIn("-ready-for-review", joined)

    def test_new_ticket_exits_changed(self):
        verdict, lines, *_ = run_wait([
            ({1: row(1)}, None),
            ({1: row(1), 9: row(9)}, None),
        ])
        self.assertEqual(verdict, "changed")
        self.assertTrue(any("#9" in ln and "new" in ln for ln in lines), lines)

    def test_ticket_leaving_the_slice_exits_changed(self):
        verdict, lines, *_ = run_wait([
            ({1: row(1), 9: row(9)}, None),
            ({1: row(1)}, None),
        ])
        self.assertEqual(verdict, "changed")
        self.assertTrue(any("#9" in ln and "left" in ln for ln in lines), lines)

    def test_empty_slice_to_one_ticket_is_a_change(self):
        verdict, _lines, *_ = run_wait([({}, None), ({3: row(3)}, None)])
        self.assertEqual(verdict, "changed")

    def test_gh_error_is_not_a_change(self):
        verdict, lines, stats, fetch, _c = run_wait([
            ({1: row(1)}, None),
            (None, "gh query failed"),
            (None, "gh query failed"),
            ({1: row(1)}, None),
        ], max_s=1500)
        self.assertEqual(verdict, "heartbeat")
        self.assertEqual(lines, [])
        self.assertEqual(stats["errors"], 2)

    def test_gh_error_at_baseline_takes_the_baseline_from_the_first_success(self):
        verdict, _l, stats, *_ = run_wait([
            (None, "gh query failed"),
            ({1: row(1)}, None),
            ({1: row(1)}, None),
        ], max_s=900)
        self.assertEqual(verdict, "heartbeat")
        self.assertEqual(stats["errors"], 1)

    def test_a_change_after_an_error_is_still_detected(self):
        verdict, *_ = run_wait([
            ({1: row(1, "T1")}, None),
            (None, "gh query failed"),
            ({1: row(1, "T2")}, None),
        ])
        self.assertEqual(verdict, "changed")

    def test_rate_backoff_skips_the_poll_without_fetching(self):
        verdict, _l, stats, fetch, _c = run_wait(
            [({1: row(1)}, None)], interval=300, max_s=900,
            backoff=lambda: 30)
        self.assertEqual(verdict, "heartbeat")
        self.assertEqual(fetch.calls, 0, "no gh call while rate-limited")
        self.assertEqual(stats["holds"], 4)

    def test_max_below_interval_never_oversleeps(self):
        verdict, _l, _s, _f, clock = run_wait(
            [({1: row(1)}, None)], interval=300, max_s=120)
        self.assertEqual(verdict, "heartbeat")
        self.assertEqual(sum(clock.sleeps), 120)

    def test_invalid_bounds_are_refused(self):
        for interval, max_s in ((0, 10), (10, 0), (-1, 10)):
            with self.assertRaises(ValueError):
                sw.wait_for_change(lambda: ({}, None), interval=interval,
                                   max_s=max_s, now_fn=lambda: 0,
                                   sleep_fn=lambda s: None,
                                   backoff_fn=lambda: 0)


class TestFetchSliceReusesTheSliceQualsSearch(unittest.TestCase):
    def test_same_quals_same_base_extra_fields(self):
        seen = {}

        def fake_union(quals, base, cwd=None, repo=None, fields=None):
            seen.update(quals=quals, base=base, cwd=cwd, fields=fields)
            return {4: row(4)}, False

        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="fork-no-merge"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"), \
                unittest.mock.patch.object(airuleset, "_current_user",
                                           return_value="david1"), \
                unittest.mock.patch.object(airuleset, "_slice_quals",
                                           return_value=["label:stream:david1"]), \
                unittest.mock.patch.object(airuleset, "_union_open_issues",
                                           side_effect=fake_union):
            rows, err = sw.fetch_slice()
        self.assertIsNone(err)
        self.assertEqual(list(rows), [4])
        self.assertEqual(seen["quals"], ["label:stream:david1"])
        self.assertEqual(seen["base"], airuleset.AUTOPILOT_SKIP_EXCL)
        self.assertEqual(seen["cwd"], "/r")
        self.assertEqual(tuple(seen["fields"]), sw.FIELDS)
        self.assertIn("updatedAt", sw.FIELDS)

    def test_a_failed_union_is_an_error_not_an_empty_slice(self):
        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="branch-merge"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"), \
                unittest.mock.patch.object(airuleset, "_current_user",
                                           return_value="montalu1"), \
                unittest.mock.patch.object(airuleset, "_slice_quals",
                                           return_value=["label:stream:montalu1"]), \
                unittest.mock.patch.object(airuleset, "_union_open_issues",
                                           return_value=({}, True)):
            rows, err = sw.fetch_slice()
        self.assertIsNone(rows)
        self.assertTrue(err)

    def test_an_unresolvable_identity_is_an_error_not_a_crash(self):
        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="fork-no-merge"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"), \
                unittest.mock.patch.object(airuleset, "_current_user",
                                           return_value="david1"), \
                unittest.mock.patch.object(
                    airuleset, "_slice_quals",
                    side_effect=airuleset.SliceUnresolved("gh api user failed")):
            rows, err = sw.fetch_slice()
        self.assertIsNone(rows)
        self.assertIn("gh api user failed", err)

    def test_a_full_authority_box_is_refused(self):
        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="full"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"):
            with self.assertRaises(sw.NotStreamBox):
                sw.fetch_slice()


class TestUnionOpenIssuesFieldsParameter(unittest.TestCase):
    """`_union_open_issues(fields=...)` — the default row shape is unchanged;
    an explicit field list reaches BOTH the snapshot and the GraphQL path."""

    def test_graphql_path_requests_the_given_fields(self):
        calls = []

        def fake_gh_out(*args, **kw):
            calls.append(args)
            return '[{"number": 3, "updatedAt": "T9", "labels": []}]'

        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_QUALS_NO_SNAPSHOT": "1"}), \
                unittest.mock.patch.object(airuleset, "_gh_out",
                                           side_effect=fake_gh_out):
            rows, failed = airuleset._union_open_issues(
                ["label:stream:x"], "-label:autopilot-skip", cwd="/r",
                fields=("number", "updatedAt", "labels"))
        self.assertFalse(failed)
        self.assertEqual(rows[3]["updatedAt"], "T9")
        json_arg = calls[0][calls[0].index("--json") + 1]
        self.assertEqual(json_arg, "number,updatedAt,labels")

    def test_graphql_default_fields_are_unchanged(self):
        calls = []

        def fake_gh_out(*args, **kw):
            calls.append(args)
            return "[]"

        with unittest.mock.patch.dict(os.environ,
                                      {"AIRULESET_QUALS_NO_SNAPSHOT": "1"}), \
                unittest.mock.patch.object(airuleset, "_gh_out",
                                           side_effect=fake_gh_out):
            airuleset._union_open_issues(["label:stream:x"], "b", cwd="/r")
        json_arg = calls[0][calls[0].index("--json") + 1]
        self.assertEqual(json_arg, "number,title,createdAt,labels")

    def _snapshot_union(self, **kw):
        snap = [{"number": 8, "title": "t", "createdAt": "C", "updatedAt": "U8",
                 "labels": [{"name": "stream:x"}], "assignees": [],
                 "authorLogin": "a"}]
        from gates import ghread
        env = dict(os.environ)
        env.pop("AIRULESET_QUALS_NO_SNAPSHOT", None)
        with unittest.mock.patch.dict(os.environ, env, clear=True), \
                unittest.mock.patch.object(ghread, "canonical_slug",
                                           return_value="o/r"), \
                unittest.mock.patch.object(ghread, "list_open_issues_cached",
                                           return_value=(snap, None)):
            return airuleset._union_open_issues(["label:stream:x"], "", cwd="/r",
                                                **kw)

    def test_snapshot_path_carries_updated_at_when_asked(self):
        rows, failed = self._snapshot_union(
            fields=("number", "updatedAt", "labels"))
        self.assertFalse(failed)
        self.assertEqual(rows[8], {"number": 8, "updatedAt": "U8",
                                   "labels": [{"name": "stream:x"}]})

    def test_snapshot_default_row_shape_is_unchanged(self):
        rows, _failed = self._snapshot_union()
        self.assertEqual(set(rows[8]), {"number", "title", "createdAt", "labels"})


class TestCli(unittest.TestCase):
    def test_registered_subcommand(self):
        self.assertIn("stream-wait", airuleset.SUBCOMMANDS)

    def test_help_lists_interval_and_max_with_defaults(self):
        r = subprocess.run([sys.executable, "airuleset.py", "stream-wait",
                            "--help"], capture_output=True, text=True,
                           timeout=30, cwd=str(ROOT),
                           env=hermetic_hook_env(self))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--interval", r.stdout)
        self.assertIn("--max", r.stdout)
        self.assertEqual(sw.DEFAULT_INTERVAL_S, 300)
        self.assertEqual(sw.DEFAULT_MAX_S, 3600)

    def _stream_box(self):
        for target, value in (("resolve_authority", "fork-no-merge"),
                              ("_repo_root", "/r")):
            patcher = unittest.mock.patch.object(airuleset, target,
                                                 return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _args(self, **kw):
        ns = unittest.mock.Mock(spec=["interval", "max"])
        ns.interval = kw.get("interval", 300)
        ns.max = kw.get("max", 3600)
        return ns

    def test_changed_prints_and_exits_zero(self):
        self._stream_box()
        out = io.StringIO()
        with unittest.mock.patch.object(
                sw, "wait_for_change",
                return_value=("changed", ["#5 updated (+prio:bounce)"],
                              {"polls": 2, "errors": 0, "holds": 0})), \
                redirect_stdout(out):
            rc = sw.run(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("CHANGED", out.getvalue())
        self.assertIn("#5 updated", out.getvalue())

    def test_heartbeat_prints_and_exits_zero(self):
        self._stream_box()
        out = io.StringIO()
        with unittest.mock.patch.object(
                sw, "wait_for_change",
                return_value=("heartbeat", [],
                              {"polls": 13, "errors": 0, "holds": 0})), \
                redirect_stdout(out):
            rc = sw.run(self._args())
        self.assertEqual(rc, 0)
        self.assertIn("HEARTBEAT", out.getvalue())

    def test_full_authority_box_exits_two(self):
        err = io.StringIO()
        waited = []
        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="full"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"), \
                unittest.mock.patch.object(
                    sw, "wait_for_change",
                    side_effect=lambda *a, **k: waited.append(1)), \
                redirect_stderr(err):
            rc = sw.run(self._args())
        self.assertEqual(rc, 2)
        self.assertEqual(waited, [], "a full box never starts the wait loop")
        self.assertIn("stream-wait", err.getvalue())

    def test_invalid_bounds_exit_two(self):
        err = io.StringIO()
        with unittest.mock.patch.object(airuleset, "resolve_authority",
                                        return_value="fork-no-merge"), \
                unittest.mock.patch.object(airuleset, "_repo_root",
                                           return_value="/r"), \
                redirect_stderr(err):
            rc = sw.run(self._args(interval=0))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
