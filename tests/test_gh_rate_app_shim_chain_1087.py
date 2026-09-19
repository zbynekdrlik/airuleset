"""#1087 L1b — the stream-box App-shim CHAIN + header-sourced budget.

On the 12 App-token stream boxes ~/.local/bin/gh is the odoo-erp App shim, which
`cmd_install` used to SKIP (#1051 no-wrap). L1b CHAINS our rate-guard wrapper
over it (move the App shim to gh-app-shim, wrapper at gh, depth-2 re-resolve of
the real binary) so accounting + the zero-budget line go live there; and because
an installation token's `gh api rate_limit` LIES, the budget is recorded from
`X-RateLimit-*` response headers (gates.ghread) + observed 403s, never a probe.

Shared fixtures/helpers are imported from test_cli_gh_rate (the App-shim install
+ chain-termination cases live there, next to the #1051 tests they supersede).
"""
import json
import os
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))       # repo root (cli_gh_rate)
sys.path.insert(0, _HERE)                         # tests/ (shared test helpers)

import cli_gh_rate  # noqa: E402
from test_cli_gh_rate import _FakeRun, _rate_json, TestEnsureWrapper  # noqa: E402,F401

_shq = cli_gh_rate._shq   # the wrapper's shell-escape helper (reused by fixtures)


class _AppShimBox(unittest.TestCase):
    """#1087 L1b: a fake HOME whose gh chain is the App-shim chain (our wrapper
    at gh, the app shim at gh-app-shim). Patches all four path functions +
    gh_rate_dir/status_path so read_status/record_headers_reading act on a tmp."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, ".local", "bin")
        self.rate = os.path.join(self.tmp, ".claude", "gh-rate")
        os.makedirs(self.bin)
        os.makedirs(self.rate)
        self._orig = {
            "shim_path": cli_gh_rate.shim_path,
            "upstream_path": cli_gh_rate.upstream_path,
            "app_shim_path": cli_gh_rate.app_shim_path,
            "gh_rate_dir": cli_gh_rate.gh_rate_dir,
            "status_path": cli_gh_rate.status_path,
        }
        cli_gh_rate.shim_path = lambda: os.path.join(self.bin, "gh")
        cli_gh_rate.upstream_path = lambda: os.path.join(self.bin, "gh-upstream")
        cli_gh_rate.app_shim_path = lambda: os.path.join(self.bin, "gh-app-shim")
        cli_gh_rate.gh_rate_dir = lambda: self.rate
        cli_gh_rate.status_path = lambda: os.path.join(self.rate, "status.json")

    def tearDown(self):
        for name, fn in self._orig.items():
            setattr(cli_gh_rate, name, fn)

    def _make_app_shim_at(self, path):
        # a minimal app-token shim carrying the recognisable header + export.
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/usr/bin/env bash\n"
                     "# gh-app-gh-shim.sh (odoo-erp issue 3281 / 3282)\n"
                     'export GH_TOKEN="$(cat "$HOME/.local/bin/gh-app-token"'
                     ' 2>/dev/null || echo)"\nexec /usr/bin/gh "$@"\n')
        os.chmod(path, 0o755)


class TestAppShimBudgetProbe(_AppShimBox):
    def test_is_app_shim_box_true_when_shim_at_gh(self):
        # pre-chain: the app shim still sits at ~/.local/bin/gh.
        self._make_app_shim_at(cli_gh_rate.shim_path())
        self.assertTrue(cli_gh_rate.is_app_shim_box())

    def test_is_app_shim_box_true_when_chained(self):
        self._make_app_shim_at(cli_gh_rate.app_shim_path())
        self.assertTrue(cli_gh_rate.is_app_shim_box())

    def test_is_app_shim_box_false_on_plain_box(self):
        # a real gh binary at gh, no app shim anywhere.
        with open(cli_gh_rate.shim_path(), "wb") as fh:
            fh.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8)
        os.chmod(cli_gh_rate.shim_path(), 0o755)
        self.assertFalse(cli_gh_rate.is_app_shim_box())

    def test_read_status_skips_fetch_on_app_shim_box(self):
        # #1087 L1b findings item 1: /rate_limit lies for installation tokens,
        # so read_status must NOT call gh api rate_limit on an app-shim box.
        self._make_app_shim_at(cli_gh_rate.app_shim_path())
        # seed a header-sourced reading (as ghread would have).
        cli_gh_rate.record_headers_reading("core", remaining=42, limit=5000,
                                            reset=1_800_000_000, now=1000.0)

        def _boom(*a, **k):
            raise AssertionError("gh api rate_limit must not be called on an "
                                 "app-shim box")

        status = cli_gh_rate.read_status(now=2000.0, run=_boom, force=True)
        # returns the header-sourced reading, marks the probe fresh.
        self.assertEqual(status["resources"]["core"]["remaining"], 42)
        self.assertEqual(status["resources"]["core"]["source"], "headers")
        self.assertAlmostEqual(status["fetched_at"], 2000.0, delta=1)

    def test_read_status_still_fetches_on_plain_box(self):
        # a plain box (real gh binary, no app shim) keeps the /rate_limit probe.
        with open(cli_gh_rate.shim_path(), "wb") as fh:
            fh.write(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8)
        os.chmod(cli_gh_rate.shim_path(), 0o755)
        fake = _FakeRun(_rate_json(4000, 5000, 4000, 5000))
        status = cli_gh_rate.read_status(now=1000.0, run=fake,
                                         real_gh="/usr/bin/gh", force=True)
        self.assertGreater(fake.calls, 0)          # it DID probe
        self.assertEqual(status["resources"]["core"]["source"], "rest")

    def test_budget_probe_skip_journals_once_per_hour(self):
        self._make_app_shim_at(cli_gh_rate.app_shim_path())
        for _ in range(5):
            cli_gh_rate.read_status(now=1000.0, run=lambda *a, **k: None,
                                    force=True)
        log = os.path.join(self.rate, "gh-rate.log")
        lines = [ln for ln in open(log).read().splitlines()
                 if "installation token" in ln] if os.path.exists(log) else []
        self.assertEqual(len(lines), 1, "skip note must journal once, not per call")


class TestHeaderSourcedBudget(_AppShimBox):
    def test_record_headers_reading_updates_status(self):
        cli_gh_rate.record_headers_reading("core", remaining=3653, limit=5000,
                                           reset=1_800_000_000, now=1000.0)
        with open(cli_gh_rate.status_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        blk = data["resources"]["core"]
        self.assertEqual(blk["remaining"], 3653)
        self.assertEqual(blk["limit"], 5000)
        self.assertEqual(blk["reset"], 1_800_000_000)
        self.assertEqual(blk["source"], "headers")

    def test_record_headers_preserves_a_prior_limit(self):
        # a 200 gave the limit; a later 403-observed write has none -> keep it.
        cli_gh_rate.record_headers_reading("core", remaining=10, limit=5000,
                                           reset=1_800_000_000, now=1000.0)
        cli_gh_rate.record_headers_reading("core", remaining=0, limit=None,
                                           reset=1_800_000_500, now=1100.0)
        blk = json.load(open(cli_gh_rate.status_path()))["resources"]["core"]
        self.assertEqual(blk["remaining"], 0)
        self.assertEqual(blk["limit"], 5000)       # preserved from the 200

    def test_record_headers_preserves_other_resources(self):
        cli_gh_rate.record_headers_reading("graphql", remaining=100, limit=5000,
                                           reset=1_800_000_000, now=1000.0)
        cli_gh_rate.record_headers_reading("core", remaining=200, limit=5000,
                                           reset=1_800_000_000, now=1001.0)
        res = json.load(open(cli_gh_rate.status_path()))["resources"]
        self.assertIn("graphql", res)
        self.assertIn("core", res)

    def test_zero_budget_line_from_headers_status(self):
        # #1087 L1b acceptance: the honest zero-budget line fires from a
        # headers-sourced status at remaining == 0.
        cli_gh_rate.record_headers_reading("core", remaining=0, limit=5000,
                                           reset=1_800_000_000, now=1000.0)
        status = cli_gh_rate._load_cache()
        line = cli_gh_rate.zero_budget_line(["api", "repos/o/r/issues"],
                                            env={}, status=status)
        self.assertIn("budget exhausted", line)

    def test_headers_zero_sets_throttle_marker(self):
        # remaining == 0 -> the throttle marker is present so the shim runs the
        # zero-budget line for a human call.
        cli_gh_rate.record_headers_reading("core", remaining=0, limit=5000,
                                           reset=1_800_000_000, now=1000.0)
        self.assertTrue(os.path.exists(cli_gh_rate.throttle_marker_path()))

    def test_observe_exhausted_writes_zero_and_reset(self):
        # #1087 L1b: the wrapper-observed 403/429 path writes remaining=0.
        cli_gh_rate._observe_exhausted_main(["--observe-exhausted"])
        blk = json.load(open(cli_gh_rate.status_path()))["resources"]["core"]
        self.assertEqual(blk["remaining"], 0)
        self.assertEqual(blk["source"], "headers")
        # reset defaults to a future epoch (now + 3600) when unparsed.
        self.assertGreater(blk["reset"], time.time())

    def test_observe_exhausted_honours_explicit_reset(self):
        cli_gh_rate._observe_exhausted_main(
            ["--observe-exhausted", "--reset", "1900000000"])
        blk = json.load(open(cli_gh_rate.status_path()))["resources"]["core"]
        self.assertEqual(blk["remaining"], 0)
        self.assertEqual(blk["reset"], 1900000000)


class TestDiagDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_gd = cli_gh_rate.gh_rate_dir
        cli_gh_rate.gh_rate_dir = lambda: self.tmp

    def tearDown(self):
        cli_gh_rate.gh_rate_dir = self._orig_gd

    def _lines(self):
        p = cli_gh_rate.journal_path()
        return open(p).read().splitlines() if os.path.exists(p) else []

    def test_identical_failures_journal_once_per_hour(self):
        # #1087 L1b item 3: the 949-lines/day DIAG spam collapses to once/hour.
        exc = RuntimeError("rc=4")
        base = 1_800_000_000.0            # some fixed hour
        for i in range(20):
            cli_gh_rate._diag("fetch-rate-limit", exc, now=base + i)   # same hour
        hits = [ln for ln in self._lines() if "fetch-rate-limit" in ln]
        self.assertEqual(len(hits), 1, "same-hour identical failures -> one line")

    def test_new_hour_flushes_count_and_writes_again(self):
        exc = RuntimeError("rc=4")
        base = 1_800_000_000.0
        for i in range(3):
            cli_gh_rate._diag("fetch-rate-limit", exc, now=base + i)     # hour H
        cli_gh_rate._diag("fetch-rate-limit", exc, now=base + 3700)      # hour H+1
        hits = [ln for ln in self._lines() if "fetch-rate-limit" in ln]
        # H's first line + H's flush (x3) + H+1's first line.
        joined = "\n".join(hits)
        self.assertIn("×3", joined)
        self.assertGreaterEqual(len(hits), 2)

    def test_distinct_failures_are_not_deduped_together(self):
        base = 1_800_000_000.0
        cli_gh_rate._diag("fetch-rate-limit", RuntimeError("rc=4"), now=base)
        cli_gh_rate._diag("cache-write", OSError("nope"), now=base + 1)
        lines = self._lines()
        self.assertTrue(any("fetch-rate-limit" in ln for ln in lines))
        self.assertTrue(any("cache-write" in ln for ln in lines))


class TestObserveDepthChain(unittest.TestCase):
    """#1087 L1b: prove the OBSERVE wrapper, when re-entered at depth > 1 (the
    App-shim re-entry), execs the REAL gh binary directly (loop-free)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, ".local", "bin")
        os.makedirs(self.bin)
        self._orig_shim = cli_gh_rate.shim_path
        self._orig_up = cli_gh_rate.upstream_path
        self._orig_app = cli_gh_rate.app_shim_path
        cli_gh_rate.shim_path = lambda: os.path.join(self.bin, "gh")
        cli_gh_rate.upstream_path = lambda: os.path.join(self.bin, "gh-upstream")
        cli_gh_rate.app_shim_path = lambda: os.path.join(self.bin, "gh-app-shim")
        self._orig_path = os.environ.get("PATH", "")

    def tearDown(self):
        cli_gh_rate.shim_path = self._orig_shim
        cli_gh_rate.upstream_path = self._orig_up
        cli_gh_rate.app_shim_path = self._orig_app
        os.environ["PATH"] = self._orig_path

    _make_sys_gh = TestEnsureWrapper._make_sys_gh

    def _run_gh(self, gh_path, env, hopfile=None, timeout=10):
        import subprocess
        run_env = {**os.environ, **env}
        if "PATH" in env:
            run_env["PATH"] = env["PATH"] + os.pathsep + self._orig_path
        if hopfile:
            run_env["HOPFILE"] = hopfile
        return subprocess.run([gh_path, "--version"], capture_output=True,
                              text=True, timeout=timeout, env=run_env)

    def test_observe_wrapper_reresolves_real_binary_at_depth2(self):
        # simulate the App-shim re-entry: AIRULESET_GH_SHIM_DEPTH already 1 in
        # env; an OBSERVE wrapper must skip its baked (app-shim) upstream and
        # exec the real gh on PATH directly.
        sysgh = os.path.join(self.tmp, "sysbin", "gh")
        os.makedirs(os.path.dirname(sysgh))
        self._make_sys_gh(sysgh)
        shim = cli_gh_rate.shim_path()
        # bake the wrapper's upstream at a self-referential app-shim path so that
        # if depth>1 did NOT re-resolve, it would loop; observe=True must instead
        # go straight to the real binary.
        cli_gh_rate._write_wrapper_file(
            shim, os.path.join(self.bin, "gh-app-shim"), "/usr/bin/python3",
            "/repo/cli_gh_rate.py", upstream=os.path.join(self.bin, "gh-app-shim"),
            observe=True)
        env = {"PATH": self.bin + os.pathsep + os.path.join(self.tmp, "sysbin"),
               "AIRULESET_GH_SHIM_DEPTH": "1"}   # already one hop deep
        r = self._run_gh(shim, env=env, timeout=8)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("gh version", r.stdout)



class TestObserveRunCapture(unittest.TestCase):
    """#1087 L1b: prove the OBSERVE run-and-capture path end-to-end in a real
    shell — it re-emits stderr unchanged, propagates the upstream's exit code,
    and fires the --observe-exhausted recorder ONLY on an exhaustion line."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bin = os.path.join(self.tmp, ".local", "bin")
        os.makedirs(self.bin)
        self._orig_shim = cli_gh_rate.shim_path
        self._orig_up = cli_gh_rate.upstream_path
        self._orig_app = cli_gh_rate.app_shim_path
        cli_gh_rate.shim_path = lambda: os.path.join(self.bin, "gh")
        cli_gh_rate.upstream_path = lambda: os.path.join(self.bin, "gh-upstream")
        cli_gh_rate.app_shim_path = lambda: os.path.join(self.bin, "gh-app-shim")

    def tearDown(self):
        cli_gh_rate.shim_path = self._orig_shim
        cli_gh_rate.upstream_path = self._orig_up
        cli_gh_rate.app_shim_path = self._orig_app

    def _fake_upstream(self, stderr_text, rc):
        # a fake "gh" (real gh binary stand-in) that writes stdout + a given
        # stderr and exits with rc — the wrapper's observe path runs THIS.
        p = os.path.join(self.bin, "gh-upstream")
        body = ("#!/usr/bin/env bash\n"
                'echo "gh version 2.40.0 (test)"\n'
                "printf '%s\\n' " + _shq(stderr_text) + " >&2\n"
                "exit " + str(rc) + "\n")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(p, 0o755)
        return p

    def _fake_recorder_module(self):
        # a stand-in cli_gh_rate module: --observe-exhausted touches a sentinel
        # so the test can prove the wrapper detected the exhaustion + fired it,
        # without depending on the real status cache / HOME.
        mod = os.path.join(self.tmp, "recorder.py")
        sentinel = os.path.join(self.tmp, "observed")
        with open(mod, "w", encoding="utf-8") as fh:
            fh.write("import sys, pathlib\n"
                     "if len(sys.argv) > 1 and sys.argv[1] == '--observe-exhausted':\n"
                     "    pathlib.Path(%r).write_text('1')\n" % sentinel)
        return mod, sentinel

    def _run(self, argv_stderr, rc):
        up = self._fake_upstream(argv_stderr, rc)
        mod, sentinel = self._fake_recorder_module()
        shim = cli_gh_rate.shim_path()
        cli_gh_rate._write_wrapper_file(shim, up, sys.executable, mod,
                                        upstream=up, observe=True)
        import subprocess
        r = subprocess.run([shim, "issue", "list"], capture_output=True,
                           text=True, timeout=10)
        # the recorder is backgrounded — give it a bounded moment to land.
        for _ in range(50):
            if os.path.exists(sentinel):
                break
            time.sleep(0.05)
        return r, os.path.exists(sentinel)

    def test_403_stderr_fires_the_recorder_and_propagates_rc(self):
        r, observed = self._run(
            "API rate limit exceeded for installation ID 152232225 (HTTP 403)", 1)
        self.assertEqual(r.returncode, 1, "the upstream's exit code must survive")
        self.assertIn("rate limit exceeded", r.stderr)     # re-emitted unchanged
        self.assertIn("gh version", r.stdout)              # stdout passes through
        self.assertTrue(observed, "a 403 line must fire --observe-exhausted")

    def test_clean_call_does_not_fire_the_recorder(self):
        r, observed = self._run("", 0)
        self.assertEqual(r.returncode, 0)
        self.assertIn("gh version", r.stdout)
        self.assertFalse(observed, "a clean call must NOT fire --observe-exhausted")

    def test_nonzero_without_ratelimit_does_not_fire(self):
        # a plain failure (not a budget exhaustion) propagates rc but records
        # nothing — no false remaining=0.
        r, observed = self._run("error: some unrelated failure", 2)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(observed)


if __name__ == "__main__":
    unittest.main()
