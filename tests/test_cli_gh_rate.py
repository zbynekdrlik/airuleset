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

    def _make_real_gh(self, path, marker="REAL-GH"):
        # #1051: a REAL gh is an ELF binary, NOT a #!-script. The installer's
        # #1051 classifier keys on exactly that (ELF magic -> wrap in place;
        # a #!-wrapper script -> foreign, skip), so the fixture for the
        # "real gh binary" layout must carry the ELF magic bytes (all < 0x80,
        # so a later utf-8 text read still finds `marker`).
        with open(path, "wb") as fh:
            fh.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
                     + b"\n" + marker.encode("ascii") + b"\n")
        os.chmod(path, 0o755)

    def _make_app_shim(self, path):
        # #1051: replay the odoo-erp issue-888 APP-TOKEN shim `gh-app-gh-shim.sh`
        # — it mints the App token then execs the FIRST `gh` on PATH whose
        # realpath is not its OWN file. It does NOT skip our rate-guard marker
        # (that fix is odoo-erp issue 3281), so wrapping OUR shim over it and
        # copying it to gh-upstream is the exact #1051 exec-loop.
        body = (
            "#!/usr/bin/env bash\n"
            "# gh-app-gh-shim.sh  (odoo-erp issue 888 app-token shim)\n"
            '_self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"\n'
            '[ -n "$HOPFILE" ] && echo appshim >> "$HOPFILE"\n'
            'IFS=":" read -ra _parts <<< "$PATH"\n'
            'for _d in "${_parts[@]}"; do\n'
            '  [ -z "$_d" ] && continue\n'
            '  _cand="$_d/gh"\n'
            '  if [ -x "$_cand" ]; then\n'
            '    _rc="$(cd "$(dirname "$_cand")" && pwd)/$(basename "$_cand")"\n'
            '    [ "$_rc" = "$_self" ] && continue\n'
            '    exec "$_cand" "$@"\n'
            "  fi\n"
            "done\n"
            'echo "gh: no real gh found" >&2; exit 127\n'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)

    def _make_sys_gh(self, path):
        # An executable stand-in for the real system gh (echoes a version).
        body = ('#!/usr/bin/env bash\n'
                '[ -n "$HOPFILE" ] && echo sysgh >> "$HOPFILE"\n'
                'echo "gh version 2.40.0 (test)"\n')
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o755)

    def test_classify_local_gh_four_layouts(self):
        # #1051: the installer must classify ~/.local/bin/gh BEFORE touching it.
        gh = cli_gh_rate.shim_path()
        # (a) our own wrapper -> 'ours'
        cli_gh_rate._write_wrapper_file(gh, "/nonexistent/gh", "/usr/bin/python3",
                                        "/repo/cli_gh_rate.py")
        self.assertEqual(cli_gh_rate._classify_local_gh(gh), "ours")
        # (b) a real gh ELF binary -> 'binary'
        os.remove(gh)
        self._make_real_gh(gh)
        self.assertEqual(cli_gh_rate._classify_local_gh(gh), "binary")
        # (c) the issue-888 app-token shim (a #!-script that is not ours) -> 'foreign'
        os.remove(gh)
        self._make_app_shim(gh)
        self.assertEqual(cli_gh_rate._classify_local_gh(gh), "foreign")
        # (d) nothing there -> 'absent'
        os.remove(gh)
        self.assertEqual(cli_gh_rate._classify_local_gh(gh), "absent")

    def test_foreign_app_shim_is_skipped_not_wrapped(self):
        # #1051 RED: the app-token shim at ~/.local/bin/gh must NEVER be
        # copy-and-wrapped (that is the exec-loop). The installer SKIPS it.
        self._make_app_shim(cli_gh_rate.shim_path())
        before = open(cli_gh_rate.shim_path(), encoding="utf-8").read()
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertIn("skip", status)
        self.assertIn("foreign", status)
        # gh is STILL the app shim, untouched — never our wrapper.
        self.assertFalse(cli_gh_rate._is_our_wrapper(cli_gh_rate.shim_path()))
        self.assertEqual(open(cli_gh_rate.shim_path(), encoding="utf-8").read(),
                         before)
        # and NO gh-upstream copy of the app shim was made (the loop's other leg).
        self.assertFalse(os.path.exists(cli_gh_rate.upstream_path()))

    def test_foreign_app_shim_install_is_idempotent(self):
        # #1051 item 4: running the installer twice on the foreign layout both
        # skips and never mutates gh / creates gh-upstream.
        self._make_app_shim(cli_gh_rate.shim_path())
        s1 = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                verbose=False)
        s2 = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                verbose=False)
        self.assertIn("skip", s1)
        self.assertEqual(s1, s2)
        self.assertFalse(cli_gh_rate._is_our_wrapper(cli_gh_rate.shim_path()))
        self.assertFalse(os.path.exists(cli_gh_rate.upstream_path()))

    def test_loop_baked_in_state_is_self_healed(self):
        # #1051 review-1 MAJOR: a box already in the exec-loop state — our
        # (#1040) shim at ~/.local/bin/gh + a COPY of the app-token shim at
        # gh-upstream — must be UN-WRAPPED by the installer, not have the loop
        # re-baked (Case 1 used to blindly refresh pointing at the foreign
        # upstream). After the fix gh is the app shim again, our shim is gone,
        # and gh-upstream is gone (moved back).
        self._make_app_shim(cli_gh_rate.upstream_path())         # gh-upstream = app shim
        cli_gh_rate._write_wrapper_file(cli_gh_rate.shim_path(),
                                        cli_gh_rate.upstream_path(),
                                        "/usr/bin/python3", "/repo/cli_gh_rate.py")
        # sanity: this IS the loop-baked-in state before we run the installer.
        self.assertTrue(cli_gh_rate._is_our_wrapper(cli_gh_rate.shim_path()))
        self.assertEqual(cli_gh_rate._classify_local_gh(cli_gh_rate.upstream_path()),
                         "foreign")
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertIn("unwrapped", status)
        self.assertFalse(cli_gh_rate._is_our_wrapper(cli_gh_rate.shim_path()))
        self.assertIn("gh-app-gh-shim.sh",
                      open(cli_gh_rate.shim_path(), encoding="utf-8").read())
        self.assertFalse(os.path.exists(cli_gh_rate.upstream_path()))
        # idempotent: a SECOND run now sees the app shim at gh -> Case 2 skip.
        s2 = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                verbose=False)
        self.assertIn("skip", s2)
        self.assertFalse(os.path.exists(cli_gh_rate.upstream_path()))

    def test_foreign_skip_prints_a_loud_line(self):
        # #1051: the skip must be LOUD so a push operator sees why the box is
        # unthrottled (fail-open: no throttle there is acceptable, a hang is not).
        import contextlib
        import io
        self._make_app_shim(cli_gh_rate.shim_path())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                               verbose=True)
        out = buf.getvalue()
        self.assertIn("shim install skipped", out)
        self.assertIn("foreign wrapper", out)
        self.assertIn("odoo-erp issue 3281", out)

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

    def test_real_gh_path_skips_a_foreign_wrapper(self):
        # #1051 review-2 (finding #1): an app-token shim on PATH must NOT be
        # returned as the "real gh" — only a real binary is (else Cases 3/4 bake
        # our shim's REAL_GH at the app shim -> the our<->app exec-loop).
        appdir = os.path.join(self.tmp, "appbin")
        realdir = os.path.join(self.tmp, "realbin")
        os.makedirs(appdir)
        os.makedirs(realdir)
        self._make_app_shim(os.path.join(appdir, "gh"))    # foreign shim, FIRST on PATH
        self._make_real_gh(os.path.join(realdir, "gh"))    # real binary, later
        os.environ["PATH"] = appdir + os.pathsep + realdir
        self.assertEqual(cli_gh_rate.real_gh_path(),
                         os.path.join(realdir, "gh"))

    def test_case4_never_wraps_onto_a_foreign_shim(self):
        # #1051 review-2 (finding #1): with ~/.local/bin/gh absent and an app
        # shim earlier on PATH than a real gh, the installer must NOT bake our
        # shim's REAL_GH at the app shim (that re-creates the loop). It points at
        # the real binary (ELF), never the app shim.
        appdir = os.path.join(self.tmp, "appbin2")
        realdir = os.path.join(self.tmp, "realbin2")
        os.makedirs(appdir)
        os.makedirs(realdir)
        self._make_app_shim(os.path.join(appdir, "gh"))       # foreign, first on PATH
        self._make_real_gh(os.path.join(realdir, "gh"))       # real ELF binary, later
        # ~/.local/bin (self.bin) is empty -> Case 4.
        os.environ["PATH"] = self.bin + os.pathsep + appdir + os.pathsep + realdir
        status = cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                                    verbose=False)
        self.assertEqual(status, "installed")
        shim_text = open(cli_gh_rate.shim_path(), encoding="utf-8").read()
        self.assertIn(os.path.join(realdir, "gh"), shim_text)      # points at REAL gh
        self.assertNotIn(os.path.join(appdir, "gh"), shim_text)    # NEVER the app shim

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


class TestChainTermination(unittest.TestCase):
    """#1051: prove the runtime gh chain TERMINATES (never the exec-loop) for
    every layout, and that OUR shim carries a depth guard that aborts a
    self-referencing chain rather than looping."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, ".local", "bin")
        self.sys = os.path.join(self.tmp, "usrbin")
        os.makedirs(self.bin)
        os.makedirs(self.sys)
        self._orig_shim = cli_gh_rate.shim_path
        self._orig_up = cli_gh_rate.upstream_path
        cli_gh_rate.shim_path = lambda: os.path.join(self.bin, "gh")
        cli_gh_rate.upstream_path = lambda: os.path.join(self.bin, "gh-upstream")
        self._orig_path = os.environ.get("PATH", "")

    def tearDown(self):
        cli_gh_rate.shim_path = self._orig_shim
        cli_gh_rate.upstream_path = self._orig_up
        os.environ["PATH"] = self._orig_path

    # -- reuse the fixture builders from TestEnsureWrapper --------------------
    _make_app_shim = TestEnsureWrapper._make_app_shim
    _make_sys_gh = TestEnsureWrapper._make_sys_gh
    _make_real_gh = TestEnsureWrapper._make_real_gh

    def _run_gh(self, gh_path, env, hopfile=None, timeout=10):
        import subprocess
        run_env = {**os.environ, **env}
        # The controlled dirs come FIRST (so the scripts resolve `gh` to the
        # fixtures, taking the fake system gh before any real /usr/bin/gh), and
        # the real system PATH is appended so `bash` itself stays findable.
        if "PATH" in env:
            run_env["PATH"] = env["PATH"] + os.pathsep + self._orig_path
        if hopfile:
            run_env["HOPFILE"] = hopfile
        return subprocess.run([gh_path, "--version"],
                              capture_output=True, text=True,
                              timeout=timeout, env=run_env)

    def test_script_has_depth_guard(self):
        # #1051 item 2: the shim increments+exports AIRULESET_GH_SHIM_DEPTH and
        # aborts (not loops) at depth > 2.
        s = cli_gh_rate.wrapper_script("/usr/bin/gh", "/usr/bin/python3",
                                       "/repo/cli_gh_rate.py")
        self.assertIn("AIRULESET_GH_SHIM_DEPTH", s)
        self.assertRegex(s, r"AIRULESET_GH_SHIM_DEPTH.*>\s*2")

    def test_depth_guard_aborts_a_self_referencing_chain(self):
        # #1051 item 2: a shim whose REAL_GH points back at ITSELF must abort
        # via the depth guard within 1 s, never loop forever.
        import time
        shim = cli_gh_rate.shim_path()
        # bake REAL_GH = the shim itself -> a deliberate self-loop.
        cli_gh_rate._write_wrapper_file(shim, shim, "/usr/bin/python3",
                                        "/repo/cli_gh_rate.py")
        start = time.monotonic()
        try:
            r = self._run_gh(shim, env={}, timeout=5)
        except Exception as e:  # noqa: BLE001 — a TimeoutExpired == it looped
            self.fail("depth guard did not abort — the shim looped: %r" % e)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.5, "depth guard must abort fast, not spin")
        self.assertNotEqual(r.returncode, 0, "a looping chain must exit non-zero")
        self.assertIn("depth", r.stderr.lower())

    def test_depth_guard_fires_across_a_foreign_app_shim(self):
        # #1051 review-2 (finding #4): the REAL incident loop is
        # our-shim -> FOREIGN app shim -> (app shim resolves first non-self gh
        # on PATH = our shim) -> our-shim ... The depth guard's EXPORTED env var
        # must survive the app shim's own exec so it accumulates and aborts at
        # depth 3 (exit 89), never hanging. (test_loop_baked_in_state_is_self_
        # healed removes the loop before it runs, so this is the only test that
        # exercises the guard firing THROUGH a foreign shim.)
        appdir = os.path.join(self.tmp, "appdir")
        os.makedirs(appdir)
        self._make_app_shim(os.path.join(appdir, "gh"))
        # our shim at ~/.local/bin/gh, baked REAL_GH = the app shim.
        cli_gh_rate._write_wrapper_file(cli_gh_rate.shim_path(),
                                        os.path.join(appdir, "gh"),
                                        "/usr/bin/python3", "/repo/cli_gh_rate.py")
        import time
        start = time.monotonic()
        try:
            # PATH = our shim's dir only, so the app shim's "first non-self gh"
            # scan finds our shim -> the mutual loop.
            r = self._run_gh(cli_gh_rate.shim_path(), env={"PATH": self.bin},
                             timeout=6)
        except Exception as e:  # noqa: BLE001 — TimeoutExpired == it hung (guard failed)
            self.fail("depth guard did NOT fire across the app shim — it hung: %r"
                      % e)
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("depth", r.stderr.lower())

    def test_foreign_layout_chain_terminates_after_install(self):
        # #1051 item 1: on the stream-box layout (app-token shim at
        # ~/.local/bin/gh, system gh elsewhere) the installer SKIPS, so the live
        # chain is app-shim -> system-gh: it terminates in <= 3 exec hops and
        # NEVER loops back through our shim.
        self._make_app_shim(cli_gh_rate.shim_path())
        self._make_sys_gh(os.path.join(self.sys, "gh"))
        cli_gh_rate.ensure_gh_rate_wrapper(module="/repo/cli_gh_rate.py",
                                           verbose=False)
        hopfile = os.path.join(self.tmp, "hops")
        env = {"PATH": self.bin + os.pathsep + self.sys}
        try:
            r = self._run_gh(cli_gh_rate.shim_path(), env=env, hopfile=hopfile,
                             timeout=10)
        except Exception as e:  # noqa: BLE001 — TimeoutExpired == the exec-loop
            self.fail("gh chain did NOT terminate on the foreign layout "
                      "(exec-loop): %r" % e)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gh version", r.stdout)
        hops = open(hopfile).read().split() if os.path.exists(hopfile) else []
        self.assertLessEqual(len(hops), 3, "chain took > 3 hops: %r" % hops)
        self.assertIn("sysgh", hops, "the real system gh must be reached")

    def test_wrap_in_place_chain_terminates(self):
        # #1051 item 1: the wrap-in-place layout (a plain box with a real gh)
        # runs our shim -> the real gh and terminates (2 hops), no loop.
        sysgh = os.path.join(self.sys, "gh")
        self._make_sys_gh(sysgh)
        # our shim baked to point straight at the real gh.
        cli_gh_rate._write_wrapper_file(cli_gh_rate.shim_path(), sysgh,
                                        "/usr/bin/python3", "/repo/cli_gh_rate.py")
        try:
            r = self._run_gh(cli_gh_rate.shim_path(),
                             env={"PATH": self.bin + os.pathsep + self.sys},
                             timeout=10)
        except Exception as e:  # noqa: BLE001
            self.fail("wrap-in-place chain did not terminate: %r" % e)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gh version", r.stdout)


if __name__ == "__main__":
    unittest.main()
