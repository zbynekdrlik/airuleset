"""Tests for cli_gh_rate — the fleet-wide gh rate-guard (#1040).

RED-first: the incident (odoo-erp issue 7308, 15.9.2026) was the owner-token
GraphQL 5000/h budget exhausted by N independent pollers with no shared budget
— one blind hour. These tests pin the shared-budget reading, the exponential
backoff (0 above 20 %, exponential below, capped), the once-per-episode alert
latch (two consecutive exhausted readings → one alert; reset above 50 %), the
gh-call classifier (a WRITE / human action is never throttled), and the
fail-open contract (any gh/parse error → no throttle).
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli_gh_rate  # noqa: E402


def _rate_json(core_remaining, core_limit, gql_remaining, gql_limit,
               core_reset=1_800_000_000, gql_reset=1_800_000_000):
    """A minimal `gh api rate_limit` response body (the shape gh returns)."""
    return json.dumps({
        "resources": {
            "core": {"limit": core_limit, "remaining": core_remaining,
                     "reset": core_reset},
            "graphql": {"limit": gql_limit, "remaining": gql_remaining,
                        "reset": gql_reset},
        }
    })


class _FakeRun:
    """A subprocess.run stand-in returning a canned rate_limit body, counting
    calls so cache-hit behaviour is observable."""

    def __init__(self, body, returncode=0, stderr=""):
        self.body = body
        self.returncode = returncode
        self.stderr = stderr
        self.calls = 0

    def __call__(self, argv, **kwargs):
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.returncode = self.returncode
        r.stdout = self.body
        r.stderr = self.stderr
        return r


class TestFetchAndPct(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = cli_gh_rate.status_path
        self._orig_gd = cli_gh_rate.gh_rate_dir
        cli_gh_rate.gh_rate_dir = lambda: self.tmp
        cli_gh_rate.status_path = lambda: os.path.join(self.tmp, "status.json")

    def tearDown(self):
        cli_gh_rate.status_path = self._orig
        cli_gh_rate.gh_rate_dir = self._orig_gd

    def test_fetch_parses_both_resources(self):
        run = _FakeRun(_rate_json(4000, 5000, 500, 5000))
        st = cli_gh_rate.read_status(now=1000.0, run=run, real_gh="/usr/bin/gh")
        self.assertEqual(st["resources"]["core"]["remaining"], 4000)
        self.assertEqual(st["resources"]["graphql"]["remaining"], 500)
        self.assertEqual(st["resources"]["graphql"]["limit"], 5000)

    def test_remaining_pct(self):
        run = _FakeRun(_rate_json(4000, 5000, 500, 5000))
        st = cli_gh_rate.read_status(now=1000.0, run=run, real_gh="/usr/bin/gh")
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("core", st), 80.0)
        self.assertAlmostEqual(cli_gh_rate.remaining_pct("graphql", st), 10.0)

    def test_cache_within_ttl_does_not_refetch(self):
        run = _FakeRun(_rate_json(4000, 5000, 4000, 5000))
        cli_gh_rate.read_status(now=1000.0, run=run, real_gh="/usr/bin/gh")
        self.assertEqual(run.calls, 1)
        # A second read 30s later (< 60s TTL) must reuse the cache.
        cli_gh_rate.read_status(now=1030.0, run=run, real_gh="/usr/bin/gh")
        self.assertEqual(run.calls, 1)
        # After the TTL it refetches.
        cli_gh_rate.read_status(now=1000.0 + cli_gh_rate.CACHE_TTL_S + 1,
                                run=run, real_gh="/usr/bin/gh")
        self.assertEqual(run.calls, 2)

    def test_gh_error_fails_open(self):
        run = _FakeRun("", returncode=1, stderr="boom")
        st = cli_gh_rate.read_status(now=1000.0, run=run, real_gh="/usr/bin/gh")
        # No usable reading -> remaining_pct None -> callers treat as plenty.
        self.assertIsNone(cli_gh_rate.remaining_pct("graphql", st))
        self.assertEqual(cli_gh_rate.backoff_seconds("graphql", st), 0)

    def test_no_real_gh_fails_open(self):
        run = _FakeRun(_rate_json(1, 5000, 1, 5000))
        st = cli_gh_rate.read_status(now=1000.0, run=run, real_gh=None)
        self.assertEqual(run.calls, 0)  # never even tried to call gh
        self.assertEqual(cli_gh_rate.backoff_seconds("graphql", st), 0)


class TestBackoff(unittest.TestCase):
    def _st(self, gql_remaining):
        return {"resources": {"graphql": {"remaining": gql_remaining,
                                          "limit": 5000, "reset": 0}}}

    def test_zero_above_threshold(self):
        for pct in (100, 50, 25, 21, 20):
            st = self._st(int(5000 * pct / 100))
            self.assertEqual(cli_gh_rate.backoff_seconds("graphql", st), 0,
                             "pct=%s must not back off" % pct)

    def test_exponential_below_threshold(self):
        b19 = cli_gh_rate.backoff_seconds("graphql", self._st(int(5000 * 0.19)))
        b14 = cli_gh_rate.backoff_seconds("graphql", self._st(int(5000 * 0.14)))
        b9 = cli_gh_rate.backoff_seconds("graphql", self._st(int(5000 * 0.09)))
        b1 = cli_gh_rate.backoff_seconds("graphql", self._st(int(5000 * 0.01)))
        self.assertGreater(b19, 0)
        self.assertGreater(b14, b19)
        self.assertGreater(b9, b14)
        self.assertGreaterEqual(b1, b9)

    def test_capped(self):
        b0 = cli_gh_rate.backoff_seconds("graphql", self._st(0))
        self.assertLessEqual(b0, cli_gh_rate.BACKOFF_CAP_S)
        self.assertEqual(b0, cli_gh_rate.BACKOFF_CAP_S)

    def test_unknown_resource_zero(self):
        self.assertEqual(cli_gh_rate.backoff_seconds("core", self._st(1)), 0)


class TestAlertLatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = cli_gh_rate.status_path
        self._orig_gd = cli_gh_rate.gh_rate_dir
        cli_gh_rate.gh_rate_dir = lambda: self.tmp
        cli_gh_rate.status_path = lambda: os.path.join(self.tmp, "status.json")

    def tearDown(self):
        cli_gh_rate.status_path = self._orig
        cli_gh_rate.gh_rate_dir = self._orig_gd

    def _read(self, now, gql_remaining):
        run = _FakeRun(_rate_json(4000, 5000, gql_remaining, 5000))
        return cli_gh_rate.read_status(now=now, run=run, real_gh="/usr/bin/gh",
                                       force=True)

    def test_two_consecutive_low_one_alert(self):
        st1 = self._read(1000.0, 500)      # 10% low, 1st
        self.assertEqual(cli_gh_rate.pending_alerts(st1), [])
        st2 = self._read(1100.0, 400)      # 8% low, 2nd consecutive -> ALERT
        self.assertIn("graphql", cli_gh_rate.pending_alerts(st2))
        st3 = self._read(1200.0, 300)      # 6% low, 3rd -> latched, NO new alert
        self.assertEqual(cli_gh_rate.pending_alerts(st3), [])

    def test_latch_resets_above_50(self):
        self._read(1000.0, 500)
        st2 = self._read(1100.0, 400)
        self.assertIn("graphql", cli_gh_rate.pending_alerts(st2))
        self._read(1200.0, 3000)           # 60% -> recovered, latch reset
        # A fresh episode alerts again after two more consecutive lows.
        self._read(1300.0, 500)
        st5 = self._read(1400.0, 400)
        self.assertIn("graphql", cli_gh_rate.pending_alerts(st5))

    def test_non_consecutive_low_does_not_alert(self):
        self._read(1000.0, 500)            # low
        self._read(1100.0, 2000)           # 40% -> not low (consecutive reset)
        st3 = self._read(1200.0, 500)      # low again, only 1 consecutive
        self.assertEqual(cli_gh_rate.pending_alerts(st3), [])

    def test_pending_alerts_not_persisted_to_cache(self):
        # #1040 review-1 MINOR-3: a fresh-cache read must NOT re-surface an
        # already-fired alert. The transient _pending_alerts is stripped before
        # the cache is written.
        self._read(1000.0, 500)
        self._read(1100.0, 400)            # 2nd consecutive -> alert fired + persisted
        import json
        with open(cli_gh_rate.status_path(), encoding="utf-8") as fh:
            persisted = json.load(fh)
        self.assertNotIn("_pending_alerts", persisted)
        # A cache-only read within TTL yields NO new pending alert.
        cached = cli_gh_rate.read_status(now=1130.0, run=_FakeRun(_rate_json(
            4000, 5000, 400, 5000)), real_gh="/usr/bin/gh")
        self.assertEqual(cli_gh_rate.pending_alerts(cached), [])


class TestClassifyCall(unittest.TestCase):
    def test_poll_shapes(self):
        for argv in (
            ["run", "view", "123"],
            ["run", "list"],
            ["pr", "checks", "5"],
            ["pr", "view", "5", "--json", "state"],
            ["issue", "view", "41", "--json", "labels"],
            ["issue", "list", "--state", "open"],
        ):
            is_poll, _res = cli_gh_rate.classify_call(argv)
            self.assertTrue(is_poll, "%r should be a poll" % argv)

    def test_write_shapes_never_poll(self):
        for argv in (
            ["issue", "comment", "41", "--body", "hi"],
            ["issue", "edit", "41", "--add-label", "x"],
            ["issue", "reopen", "41"],
            ["issue", "close", "41"],
            ["issue", "create", "--title", "x"],
            ["pr", "merge", "5"],
            ["api", "-X", "POST", "/repos/x/y/issues"],
            ["api", "--method", "PUT", "/x"],
        ):
            is_poll, res = cli_gh_rate.classify_call(argv)
            self.assertFalse(is_poll, "%r must NEVER be throttled" % argv)
            self.assertIsNone(res)

    def test_rate_limit_endpoint_is_not_a_poll(self):
        # The free refresh endpoint must never itself be throttled.
        is_poll, _ = cli_gh_rate.classify_call(["api", "rate_limit"])
        self.assertFalse(is_poll)

    def test_resource_detection(self):
        _, res = cli_gh_rate.classify_call(["issue", "list", "--json", "number"])
        self.assertEqual(res, "graphql")
        _, res = cli_gh_rate.classify_call(["run", "view", "1"])
        self.assertEqual(res, "core")

    def test_api_graphql_read_with_field_is_a_poll(self):
        # `-f query=...` is the NORMAL read idiom for `api graphql` (#1040
        # review-1 MAJOR-2) — must be a graphql poll, not misread as a write.
        for argv in (
            ["api", "graphql", "-f", "query=query { viewer { login } }"],
            ["api", "graphql", "-f", "query={ viewer { login } }"],
            ["api", "graphql", "--paginate", "-f", "query=query Q { x }"],
        ):
            is_poll, res = cli_gh_rate.classify_call(argv)
            self.assertTrue(is_poll, "%r is a graphql READ poll" % argv)
            self.assertEqual(res, "graphql")

    def test_api_graphql_mutation_is_never_a_poll(self):
        for argv in (
            ["api", "graphql", "-f", "query=mutation { addComment(x:1){id} }"],
            ["api", "graphql", "-f", "query=mutation Foo { y }"],
            ["api", "graphql", "-X", "POST", "-f", "query=mutation { z }"],
        ):
            is_poll, res = cli_gh_rate.classify_call(argv)
            self.assertFalse(is_poll, "%r is a mutation (write) — never throttle" % argv)
            self.assertIsNone(res)

    def test_rest_api_field_is_a_write(self):
        # A REST `api` with a field flag implies a POST body -> a write.
        is_poll, _ = cli_gh_rate.classify_call(
            ["api", "/repos/x/y/issues", "-f", "title=hi"])
        self.assertFalse(is_poll)

    def test_leading_repo_flag_before_subcommand_still_a_poll(self):
        # `gh -R o/r issue view 5` — the -R value must not be mistaken for the
        # subcommand (#1040 review-2 MINOR-4).
        for argv in (
            ["-R", "owner/repo", "issue", "view", "5", "--json", "labels"],
            ["--repo", "owner/repo", "issue", "list"],
            ["-Rowner/repo", "pr", "checks", "5"],
            ["--repo=owner/repo", "run", "list"],
        ):
            is_poll, _ = cli_gh_rate.classify_call(argv)
            self.assertTrue(is_poll, "%r should still classify as a poll" % argv)

    def test_graphql_query_from_file_is_never_throttled(self):
        # A file-loaded query hides its text — could be a mutation, so fail SAFE
        # to a write / no-throttle (#1040 review-2 MINOR-3).
        for argv in (
            ["api", "graphql", "-F", "query=@q.graphql"],
            ["api", "graphql", "-F", "query=@mutation.graphql"],
        ):
            is_poll, res = cli_gh_rate.classify_call(argv)
            self.assertFalse(is_poll, "%r hides its query -> never throttle" % argv)
            self.assertIsNone(res)


class TestWrapperBackoff(unittest.TestCase):
    def _st(self, gql_remaining, core_remaining=5000):
        return {"resources": {
            "graphql": {"remaining": gql_remaining, "limit": 5000, "reset": 0},
            "core": {"remaining": core_remaining, "limit": 5000, "reset": 0},
        }}

    def test_poller_low_sleeps(self):
        secs = cli_gh_rate.wrapper_backoff(["issue", "list", "--json", "n"],
                                           status=self._st(200))  # graphql 4%
        self.assertGreater(secs, 0)

    def test_poller_healthy_no_sleep(self):
        secs = cli_gh_rate.wrapper_backoff(["issue", "list", "--json", "n"],
                                           status=self._st(4000))
        self.assertEqual(secs, 0)

    def test_write_never_sleeps_even_when_exhausted(self):
        secs = cli_gh_rate.wrapper_backoff(["issue", "comment", "41", "--body", "x"],
                                           status=self._st(0, core_remaining=0))
        self.assertEqual(secs, 0)

    def test_error_status_fails_open(self):
        secs = cli_gh_rate.wrapper_backoff(["issue", "list", "--json", "n"],
                                           status={"resources": {}})
        self.assertEqual(secs, 0)

    def test_either_resource_low_backs_off_a_poll(self):
        # core low but graphql fine — a background poll still slows.
        secs = cli_gh_rate.wrapper_backoff(["run", "view", "1"],
                                           status=self._st(5000, core_remaining=100))
        self.assertGreater(secs, 0)


class TestStatusRow(unittest.TestCase):
    def test_status_row_format(self):
        st = {"resources": {
            "core": {"remaining": 600, "limit": 5000, "reset": 0},
            "graphql": {"remaining": 150, "limit": 5000, "reset": 0},
        }}
        row = cli_gh_rate.status_row(st)
        self.assertIn("gh-rate", row)
        self.assertIn("core", row)
        self.assertIn("graphql", row)
        self.assertIn("12%", row)   # core 600/5000
        self.assertIn("3%", row)    # graphql 150/5000

    def test_status_row_none_when_unknown(self):
        self.assertIsNone(cli_gh_rate.status_row({"resources": {}}))


class TestWrapperScript(unittest.TestCase):
    def test_script_has_the_safety_guards(self):
        s = cli_gh_rate.wrapper_script("/usr/bin/gh", "/usr/bin/python3",
                                       "/repo/cli_gh_rate.py")
        self.assertIn(cli_gh_rate.WRAPPER_SENTINEL, s)
        self.assertIn("AIRULESET_GH_RATE_INTERNAL", s)  # refresh never recurses
        self.assertIn("AIRULESET_GH_POLLER", s)         # only pollers throttle
        self.assertIn("exec", s)                        # transparent delegation
        self.assertIn("/usr/bin/gh", s)                 # baked real gh
        self.assertIn("--wrapper-backoff", s)
        self.assertIn("throttle-active", s)             # cheap healthy fast-path


class TestThrottleMarker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_sp = cli_gh_rate.status_path
        self._orig_gd = cli_gh_rate.gh_rate_dir
        cli_gh_rate.gh_rate_dir = lambda: self.tmp
        cli_gh_rate.status_path = lambda: os.path.join(self.tmp, "status.json")

    def tearDown(self):
        cli_gh_rate.status_path = self._orig_sp
        cli_gh_rate.gh_rate_dir = self._orig_gd

    def test_marker_present_when_low_absent_when_healthy(self):
        marker = cli_gh_rate.throttle_marker_path()
        # low graphql -> marker created
        low = _FakeRun(_rate_json(4000, 5000, 200, 5000))
        cli_gh_rate.read_status(now=1000.0, run=low, real_gh="/usr/bin/gh",
                                force=True)
        self.assertTrue(os.path.exists(marker))
        # healthy reading -> marker removed
        ok = _FakeRun(_rate_json(4000, 5000, 4000, 5000))
        cli_gh_rate.read_status(now=2000.0, run=ok, real_gh="/usr/bin/gh",
                                force=True)
        self.assertFalse(os.path.exists(marker))


class TestEnsureWrapper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, ".local", "bin")
        os.makedirs(self.bin)
        self._orig_shim = cli_gh_rate.shim_path
        self._orig_up = cli_gh_rate.upstream_path
        cli_gh_rate.shim_path = lambda: os.path.join(self.bin, "gh")
        cli_gh_rate.upstream_path = lambda: os.path.join(self.bin, "gh-upstream")
        self._orig_path = os.environ.get("PATH", "")

    def tearDown(self):
        cli_gh_rate.shim_path = self._orig_shim
        cli_gh_rate.upstream_path = self._orig_up
        os.environ["PATH"] = self._orig_path

    def _make_real_gh(self, path, body="#!/bin/sh\necho REAL-GH \"$@\"\n"):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)

    def test_wrap_real_gh_in_place(self):
        # gh lives AT the shim path (this fleet's layout).
        self._make_real_gh(cli_gh_rate.shim_path())
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertEqual(status, "wrapped-in-place")
        # The shim now carries the sentinel; the real gh is at gh-upstream.
        self.assertTrue(cli_gh_rate._is_our_wrapper(cli_gh_rate.shim_path()))
        self.assertFalse(cli_gh_rate._is_our_wrapper(cli_gh_rate.upstream_path()))
        with open(cli_gh_rate.upstream_path(), encoding="utf-8") as fh:
            self.assertIn("REAL-GH", fh.read())
        self.assertTrue(os.access(cli_gh_rate.upstream_path(), os.X_OK))

    def test_idempotent_after_wrap(self):
        self._make_real_gh(cli_gh_rate.shim_path())
        cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py", verbose=False)
        up_mtime = os.path.getmtime(cli_gh_rate.upstream_path())
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertEqual(status, "already installed (-> %s)"
                         % cli_gh_rate.upstream_path())
        # Upstream was NOT re-copied on the idempotent run.
        self.assertEqual(up_mtime, os.path.getmtime(cli_gh_rate.upstream_path()))

    def test_no_gh_anywhere_is_noop(self):
        os.environ["PATH"] = self.bin      # empty bin, no gh, no shim
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertEqual(status, "skip: no gh on this box")
        self.assertFalse(os.path.exists(cli_gh_rate.shim_path()))

    def test_real_gh_path_prefers_upstream(self):
        self._make_real_gh(cli_gh_rate.upstream_path())
        self.assertEqual(cli_gh_rate.real_gh_path(), cli_gh_rate.upstream_path())

    def test_real_gh_path_skips_our_shim(self):
        # A shim at the shim path (our sentinel) plus a real gh elsewhere on PATH.
        cli_gh_rate._write_wrapper_file(cli_gh_rate.shim_path(),
                                        "/nonexistent/gh", "/usr/bin/python3",
                                        "/repo/cli_gh_rate.py")
        other = os.path.join(self.tmp, "otherbin")
        os.makedirs(other)
        self._make_real_gh(os.path.join(other, "gh"))
        os.environ["PATH"] = self.bin + os.pathsep + other
        resolved = cli_gh_rate.real_gh_path()
        self.assertEqual(resolved, os.path.join(other, "gh"))


class TestHoldComposition(unittest.TestCase):
    """The #1041 min_budget guard composes with the rate backoff: a gh-poller
    watchdog job is held (never run late) when the rate warrants a backoff."""

    def test_hold_when_backoff_positive(self):
        self.assertTrue(cli_gh_rate.should_hold_gh_poller(backoff=30))
        self.assertTrue(cli_gh_rate.should_hold_gh_poller(backoff=1))

    def test_no_hold_when_healthy(self):
        self.assertFalse(cli_gh_rate.should_hold_gh_poller(backoff=0))


class TestWiring(unittest.TestCase):
    """#1040 review-1 MINOR-4: guard the three airuleset.py integration points +
    the run_once param, so a future edit can't silently drop the wiring."""

    def test_gh_rate_subcommand_registered(self):
        import airuleset
        self.assertIn("gh-rate", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["gh-rate"], airuleset.cmd_gh_rate)

    def test_run_once_accepts_gh_rate_fetch(self):
        import inspect
        import watchdog
        self.assertIn("gh_rate_fetch",
                      inspect.signature(watchdog.run_once).parameters)

    def test_cmd_install_calls_ensure_gh_rate_wrapper(self):
        import inspect
        import airuleset
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("ensure_gh_rate_wrapper", src)

    def test_cmd_watchdog_sets_poller_env(self):
        import inspect
        import airuleset
        src = inspect.getsource(airuleset.cmd_watchdog)
        self.assertIn("AIRULESET_GH_POLLER", src)
        self.assertIn("gh_rate_fetch", src)

    def test_cmd_status_prints_gh_rate_row(self):
        import inspect
        import airuleset
        src = inspect.getsource(airuleset.cmd_status)
        self.assertIn("status_row_cached", src)


if __name__ == "__main__":
    unittest.main()
