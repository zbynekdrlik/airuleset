"""#1058 — Playwright browsers-path rework (area review of #1048).

Items covered (see the ticket's design comment, Approach 1):
  1. resolver is CLASS-AGNOSTIC: any box reuses /opt/ms-playwright when it holds
     the pinned build, else the per-user cache (restores the #950 one-shared-copy
     architecture the moment root refreshes /opt on subdev);
  2. old-build CLEANUP in the per-user cache after a pinned install (never /opt,
     never a build a live process is using — the #1030 liveness check);
  3. the bashrc STREAM_ENV export FOLLOWS the resolver marker file;
  4. the push post-check RETRIES ONCE on an npm cache race (rc 1 + `npm error`),
     immediate FAIL still for exit 127 and a second failure;
  5. a dep-edge lock (CI-only, AIRULESET_NET_TESTS) that @playwright/mcp@<MCP>'s
     `playwright` dependency == PLAYWRIGHT_PW_VERSION.

RED-first: these encode the NEW behaviour and fail against the pre-#1058 code.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_playwright_mcp as p


# --------------------------------------------------------------------------- #
# Item 1 — class-agnostic resolver
# --------------------------------------------------------------------------- #
class TestResolverClassAgnostic1058(unittest.TestCase):
    def test_shared_stream_reuses_opt_when_it_holds_the_pinned_build(self):
        home = Path("/home/montalu1")
        self.assertEqual(
            p.resolve_playwright_browsers_path("shared-stream", True, home=home),
            p.OPT_MS_PLAYWRIGHT,
            "a shared-stream box must reuse the refreshed /opt (the #950 shared copy)")

    def test_shared_stream_uses_per_user_when_opt_is_mismatched(self):
        home = Path("/home/montalu1")
        self.assertEqual(
            p.resolve_playwright_browsers_path("shared-stream", False, home=home),
            home / ".cache" / "ms-playwright",
            "a mismatched/absent /opt must fall back to the per-user cache (#2420 safety)")

    def test_every_class_follows_the_same_build_match_rule(self):
        for bc in ("shared-stream", "workstation", "controller", "gatekeeper", None):
            home = Path("/home/x")
            self.assertEqual(
                p.resolve_playwright_browsers_path(bc, True, home=home),
                p.OPT_MS_PLAYWRIGHT, "class %r must reuse a pinned /opt" % bc)
            self.assertEqual(
                p.resolve_playwright_browsers_path(bc, False, home=home),
                home / ".cache" / "ms-playwright",
                "class %r must fall back to per-user on a mismatched /opt" % bc)


# --------------------------------------------------------------------------- #
# Item 2 — old-build cleanup in the per-user cache
# --------------------------------------------------------------------------- #
class TestOldBuildCleanup1058(unittest.TestCase):
    def _per_user_cache(self, *extra_dirs, pinned_complete=True):
        """A fake per-user cache. By default the COMPLETE pinned chromium pair
        (both halves + INSTALLATION_COMPLETE) is created so the survivor guard
        (#1058 review A 🟡-3) lets cleanup run; pass pinned_complete=False to test
        the guard. `extra_dirs` are the OLD/other build dirs to seed."""
        b = p.PLAYWRIGHT_CHROMIUM_BUILD
        d = Path(tempfile.mkdtemp()) / ".cache" / "ms-playwright"
        d.mkdir(parents=True)
        if pinned_complete:
            for half in ("chromium-" + b, "chromium_headless_shell-" + b):
                (d / half).mkdir()
                (d / half / "INSTALLATION_COMPLETE").write_text("")
        for name in extra_dirs:
            e = d / name
            if not e.exists():
                e.mkdir()
        return d

    def _names(self, d):
        return sorted(x.name for x in d.iterdir())

    def test_removes_superseded_chromium_pair_keeps_the_pinned_pair(self):
        b = p.PLAYWRIGHT_CHROMIUM_BUILD
        cache = self._per_user_cache("chromium-1234", "chromium_headless_shell-1234")
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        names = self._names(cache)
        self.assertIn("chromium-" + b, names)
        self.assertIn("chromium_headless_shell-" + b, names)
        self.assertNotIn("chromium-1234", names)
        self.assertNotIn("chromium_headless_shell-1234", names)

    def test_keeps_a_build_a_live_process_is_using(self):
        cache = self._per_user_cache("chromium-1234")
        p._cleanup_old_builds(cache, live_check=lambda d: d.name == "chromium-1234")
        self.assertIn("chromium-1234", self._names(cache),
                      "a build in live use must never be removed (#1030 liveness)")

    def test_ffmpeg_is_left_alone(self):
        # #1058 review B 🟡-2: cleanup never touches ffmpeg (airuleset pins no
        # ffmpeg revision; a keep-highest rule is a rollback hazard). The age-gated
        # disk-guard #892 timer reaps stale ffmpeg instead.
        cache = self._per_user_cache("ffmpeg-1010", "ffmpeg-1011")
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        names = self._names(cache)
        self.assertIn("ffmpeg-1010", names)
        self.assertIn("ffmpeg-1011", names)

    def test_no_removal_when_pinned_build_absent(self):
        # #1058 review A 🟡-3 survivor guard: with no pinned build present, cleanup
        # must NOT reap (a destructive rmtree can never leave the box browserless).
        cache = self._per_user_cache("chromium-1234", pinned_complete=False)
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        self.assertIn("chromium-1234", self._names(cache),
                      "cleanup must not reap when the pinned build is absent")

    def test_no_removal_when_pinned_build_incomplete(self):
        # pinned chromium half present + complete, but the headless-shell half
        # absent -> _has_pinned_chromium_build False -> survivor guard blocks reap.
        b = p.PLAYWRIGHT_CHROMIUM_BUILD
        cache = self._per_user_cache("chromium-1234", pinned_complete=False)
        (cache / ("chromium-" + b)).mkdir()
        (cache / ("chromium-" + b) / "INSTALLATION_COMPLETE").write_text("")
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        self.assertIn("chromium-1234", self._names(cache),
                      "an incomplete pinned build must block the reap")

    def test_never_touches_the_root_owned_opt(self):
        # /opt/ms-playwright is NOT a per-user cache -> cleanup is a no-op there
        # (the guard keys on the _is_per_user_cache shape, before the survivor
        # guard is even reached).
        opt = Path(tempfile.mkdtemp()) / "ms-playwright"
        opt.mkdir(parents=True)
        (opt / "chromium-1234").mkdir()
        p._cleanup_old_builds(opt, live_check=lambda d: False)
        self.assertIn("chromium-1234", self._names(opt),
                      "cleanup must never delete inside /opt")

    def test_skips_a_symlink_never_attempts_to_remove_it(self):
        # #1058 review B 🟡-1 (teeth): a symlink named like an old build must be
        # SKIPPED — never followed, never rmtree'd. Without the is_symlink() guard,
        # shutil.rmtree(<symlink>) raises and logs "could not remove chromium-1235"
        # to stderr, so asserting that name is ABSENT from stderr is the teeth; the
        # real superseded dir being removed is the discriminator that cleanup ran.
        import io
        external = Path(tempfile.mkdtemp()) / "external-chromium"
        external.mkdir()
        (external / "payload").write_text("keep me")
        cache = self._per_user_cache("chromium-1234")
        (cache / "chromium-1235").symlink_to(external)
        err = io.StringIO()
        with mock.patch("sys.stderr", err), mock.patch("sys.stdout", io.StringIO()):
            p._cleanup_old_builds(cache, live_check=lambda d: False)
        names = self._names(cache)
        self.assertNotIn("chromium-1234", names)          # real superseded removed
        self.assertIn("chromium-1235", names)             # symlink left in place
        self.assertNotIn("chromium-1235", err.getvalue(),
                         "cleanup must not even ATTEMPT to remove the symlink")
        self.assertTrue((external / "payload").exists(),  # never followed
                        "the symlink target must be untouched")

    def test_leaves_unrelated_families_alone(self):
        cache = self._per_user_cache("firefox-1400", "webkit-2000")
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        names = self._names(cache)
        self.assertIn("firefox-1400", names)
        self.assertIn("webkit-2000", names)

    def test_idempotent(self):
        cache = self._per_user_cache("chromium-1234")
        p._cleanup_old_builds(cache, live_check=lambda d: False)
        p._cleanup_old_builds(cache, live_check=lambda d: False)  # must not raise
        self.assertNotIn("chromium-1234", self._names(cache))

    def _install_env(self):
        # a per-user cache + patched seams so ensure_playwright_browsers runs its
        # install/already-installed branch with no real npx/browser.
        return Path(tempfile.mkdtemp()) / ".cache" / "ms-playwright"

    def test_ensure_runs_cleanup_after_a_successful_install(self):
        cache = self._install_env()
        with mock.patch.object(p, "_cleanup_old_builds") as cln, \
                mock.patch.object(p, "_playwright_pinned_build_installed", return_value=False), \
                mock.patch.object(p, "_heal_system_libs"), \
                mock.patch("shutil.which", return_value="/usr/bin/npx"), \
                mock.patch("subprocess.run",
                           return_value=mock.Mock(returncode=0, stdout="", stderr="")), \
                mock.patch("sys.stdout", __import__("io").StringIO()):
            p.ensure_playwright_browsers(cache_dir=cache, sleep=lambda *_: None,
                                         sudo_ok=lambda: False, probe_rc=lambda *_: None)
        cln.assert_called_once()

    def test_ensure_runs_cleanup_when_the_build_is_already_present(self):
        cache = self._install_env()
        cache.mkdir(parents=True)
        with mock.patch.object(p, "_cleanup_old_builds") as cln, \
                mock.patch.object(p, "_playwright_pinned_build_installed", return_value=True), \
                mock.patch.object(p, "_heal_system_libs"), \
                mock.patch("shutil.which", return_value="/usr/bin/npx"), \
                mock.patch("sys.stdout", __import__("io").StringIO()):
            p.ensure_playwright_browsers(cache_dir=cache, sleep=lambda *_: None,
                                         sudo_ok=lambda: False, probe_rc=lambda *_: None)
        cln.assert_called_once()


# --------------------------------------------------------------------------- #
# Item 3 — bashrc STREAM_ENV export follows the marker
# --------------------------------------------------------------------------- #
class TestStreamEnvFollowsMarker1058(unittest.TestCase):
    def _export_line(self):
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        lines = [ln for ln in STREAM_ENV_BASHRC_BLOCK.splitlines()
                 if "PLAYWRIGHT_BROWSERS_PATH=" in ln]
        self.assertEqual(len(lines), 1, "exactly one browsers-path export")
        return lines[0]

    def test_export_reads_the_resolver_marker_file(self):
        ln = self._export_line()
        self.assertIn("airuleset-playwright-browsers-path", ln,
                      "the shell must follow the resolver's marker, not hardcode the cache")

    def test_export_keeps_the_per_user_fallback(self):
        ln = self._export_line()
        self.assertIn(".cache/ms-playwright", ln,
                      "an unprovisioned box (no marker yet) must still fall back to the per-user cache")

    def test_export_never_hardcodes_opt(self):
        self.assertNotIn("/opt/ms-playwright", self._export_line())

    def test_block_is_valid_bash(self):
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        r = subprocess.run(["bash", "-n", "-c", STREAM_ENV_BASHRC_BLOCK],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_export_resolves_to_the_marker_value_at_runtime(self):
        # drive the actual export in a real bash with a fake HOME holding the marker
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        home = Path(tempfile.mkdtemp())
        (home / ".claude").mkdir()
        (home / ".claude" / "airuleset-playwright-browsers-path").write_text("/opt/ms-playwright\n")
        env = dict(os.environ)
        env["HOME"] = str(home)
        r = subprocess.run(
            ["bash", "-c", STREAM_ENV_BASHRC_BLOCK + '\nprintf "%s" "$PLAYWRIGHT_BROWSERS_PATH"'],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout, "/opt/ms-playwright", r.stderr)

    def test_export_falls_back_when_marker_absent_at_runtime(self):
        from cli_bashrc_appliers import STREAM_ENV_BASHRC_BLOCK
        home = Path(tempfile.mkdtemp())
        env = dict(os.environ)
        env["HOME"] = str(home)
        r = subprocess.run(
            ["bash", "-c", STREAM_ENV_BASHRC_BLOCK + '\nprintf "%s" "$PLAYWRIGHT_BROWSERS_PATH"'],
            capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout, str(home / ".cache" / "ms-playwright"), r.stderr)


# --------------------------------------------------------------------------- #
# Item 4 — push post-check retries once on an npm cache race
# --------------------------------------------------------------------------- #
class TestPostcheckNpmRetry1058(unittest.TestCase):
    def _frag(self):
        import cli_remote
        return cli_remote._playwright_chromium_postcheck()

    def test_fragment_mentions_the_npm_race_retry(self):
        f = self._frag()
        self.assertIn("npm error", f)                 # the race signature it keys on
        self.assertIn("sleep", f)                      # the pause before retry

    def _setup_box(self):
        home = Path(tempfile.mkdtemp())
        (home / ".claude").mkdir()
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".claude" / "airuleset-playwright-browsers-path").write_text("/tmp/bp\n")
        return home

    def _fake_npx(self, home, script):
        binp = home / ".local" / "bin" / "npx"
        binp.write_text("#!/bin/sh\n" + script + "\n")
        binp.chmod(0o755)

    def _run(self, home):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = str(home / ".local" / "bin") + os.pathsep + env.get("PATH", "")
        env["AIRULESET_PW_POSTCHECK_RETRY_SLEEP"] = "0"   # no real 5 s wait in tests
        return subprocess.run(["bash", "-c", self._frag()],
                              capture_output=True, text=True, timeout=60, env=env)

    def test_npm_race_passes_on_the_retry(self):
        home = self._setup_box()
        # 1st call: npm error rc 1; 2nd call: success
        self._fake_npx(home,
                       'C="$HOME/.npx-n"; n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C";\n'
                       'if [ "$n" = 1 ]; then echo "npm error code EEXIST: Remove the existing file" >&2; exit 1; fi;\n'
                       'exit 0')
        r = self._run(home)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)

    def test_second_npm_error_still_fails_the_target(self):
        home = self._setup_box()
        self._fake_npx(home, 'echo "npm error code EEXIST: Remove the existing file" >&2; exit 1')
        r = self._run(home)
        self.assertEqual(r.returncode, 88, r.stderr)

    def test_exit_127_never_retries_fails_immediately(self):
        home = self._setup_box()
        # count calls: exit 127 must fail on the FIRST call (no retry)
        self._fake_npx(home,
                       'C="$HOME/.npx-n"; n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C";\n'
                       'echo "error while loading shared libraries: libatk" >&2; exit 127')
        r = self._run(home)
        self.assertEqual(r.returncode, 88, r.stderr)
        self.assertIn("missing system shared libraries", r.stderr)
        self.assertEqual((home / ".npx-n").read_text().strip(), "1",
                         "exit 127 must not retry (npx called exactly once)")

    def test_generic_rc1_without_npm_error_does_not_retry(self):
        home = self._setup_box()
        self._fake_npx(home,
                       'C="$HOME/.npx-n"; n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C";\n'
                       'echo "Executable doesnt exist at chromium_headless_shell" >&2; exit 1')
        r = self._run(home)
        self.assertEqual(r.returncode, 88, r.stderr)
        self.assertIn("did not render", r.stderr)
        self.assertEqual((home / ".npx-n").read_text().strip(), "1",
                         "a non-npm rc 1 must not retry (npx called exactly once)")

    # --- #1085: npm's EEXIST cache race exits -2 = shell rc 254, NOT 1 --------
    def test_npm_race_rc254_passes_on_the_retry(self):
        # The real failure #1058 item 4 was written for: npm signals the EEXIST
        # cache collision with process exit -2, which the shell reports as 254.
        # The retry MUST still fire (keying on the `npm error` signature, not on
        # a guessed rc). 1st call: rc 254 + npm error; 2nd call: success.
        home = self._setup_box()
        self._fake_npx(home,
                       'C="$HOME/.npx-n"; n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C";\n'
                       'if [ "$n" = 1 ]; then '
                       'echo "npm error Remove the existing file and try again" >&2; exit 254; fi;\n'
                       'exit 0')
        r = self._run(home)
        self.assertEqual(r.returncode, 0, r.stderr + "\n" + r.stdout)
        self.assertEqual((home / ".npx-n").read_text().strip(), "2",
                         "an rc-254 npm race must retry once (npx called twice)")

    def test_exit_127_with_npm_error_never_retries(self):
        # exit 127 (the ELF loader could not find shared libraries) is a hard
        # fault even when the stderr happens to carry `npm error` — RC=127 is
        # excluded from the retry and the message names the 127 class, never the
        # npm race.
        home = self._setup_box()
        self._fake_npx(home,
                       'C="$HOME/.npx-n"; n=$(cat "$C" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$C";\n'
                       'echo "npm error while loading shared libraries: libatk" >&2; exit 127')
        r = self._run(home)
        self.assertEqual(r.returncode, 88, r.stderr)
        self.assertIn("exited 127", r.stderr)
        self.assertEqual((home / ".npx-n").read_text().strip(), "1",
                         "exit 127 must not retry even with an npm error (npx called once)")

    def test_two_rc254_failures_report_the_npm_race(self):
        # A persisting npm cache race (two rc-254 failures) FAILs the target with
        # the npm-race message and the #1085 tag — never "chrome-channel /
        # version drift", which blamed a transient on a browser fault.
        home = self._setup_box()
        self._fake_npx(home,
                       'echo "npm error Remove the existing file and try again" >&2; exit 254')
        r = self._run(home)
        self.assertEqual(r.returncode, 88, r.stderr)
        self.assertIn("npm cache race", r.stderr)
        self.assertIn("#1085", r.stderr)
        self.assertNotIn("did not render", r.stderr)


# --------------------------------------------------------------------------- #
# Item 5 — dep-edge lock (CI-only, network) — injectable fetcher so the FAIL
# branch is exercised offline (supervisor review: a registry error under the var
# must FAIL the lock, not skip it — a skip-in-disguise silently removes the lock).
# --------------------------------------------------------------------------- #
def _fetch_registry(url, timeout=20):
    """The REAL registry fetch (used under AIRULESET_NET_TESTS). Returns the
    parsed JSON dict or raises (a network/HTTP error)."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _dep_edge_check(testcase, mcp_version, pinned, *, fetch):
    """Resolve @playwright/mcp@<mcp_version>'s `playwright` dependency via `fetch`
    (a url->dict callable) and assert it equals `pinned`. A network/HTTP error is
    a FAILURE, never a skip: under AIRULESET_NET_TESTS the lock must catch a
    registry outage loudly (rerun once for a transient), never silently pass. A
    drift (wrong resolved dep) FAILS with the trio detail."""
    url = "https://registry.npmjs.org/@playwright/mcp/" + mcp_version
    try:
        data = fetch(url)
    except Exception as e:   # noqa: BLE001 — any fetch error FAILS (never skip)
        testcase.fail(
            "dep-edge lock: npm registry unreachable at %s: %s — under "
            "AIRULESET_NET_TESTS a registry error FAILS the gate (rerun once to "
            "rule out a transient); it must NEVER silently skip the lock" % (url, e))
    dep = (data.get("dependencies") or {}).get("playwright")
    testcase.assertEqual(
        dep, pinned,
        "@playwright/mcp@%s depends on playwright %r but the pin is %r — the trio drifted"
        % (mcp_version, dep, pinned))


class TestDepEdgeLock1058(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AIRULESET_NET_TESTS") == "1",
                         "network test — set AIRULESET_NET_TESTS=1 (CI sets it)")
    def test_mcp_pkg_depends_on_the_pinned_playwright(self):
        # under the var, the REAL registry resolve; a network/HTTP error FAILS
        # (supervisor review: skipping under the var is a skip-in-disguise).
        _dep_edge_check(self, p.PLAYWRIGHT_MCP_VERSION, p.PLAYWRIGHT_PW_VERSION,
                        fetch=_fetch_registry)

    def test_network_error_fails_under_the_var_never_skips(self):
        # HERMETIC (no var needed): a registry outage must FAIL the lock, not skip
        # it — otherwise a CI registry outage silently removes the lock while the
        # gate stays green. Inject a raising fetcher; the lock must raise an
        # AssertionError (fail), NEVER a SkipTest. A plain
        # assertRaises(AssertionError) does NOT catch unittest.SkipTest, so a
        # fail->skip regression (the exact skip-in-disguise this locks against)
        # would degrade THIS test to a silent SKIP (green in CI) — review F1. So
        # catch SkipTest explicitly and fail on it.
        def boom(url, timeout=20):
            raise urllib.error.URLError("simulated npm registry outage")
        failed_loudly = False
        try:
            _dep_edge_check(self, p.PLAYWRIGHT_MCP_VERSION, p.PLAYWRIGHT_PW_VERSION,
                            fetch=boom)
        except unittest.SkipTest:
            self.fail("regressed to a skip-in-disguise — a fetch error must FAIL, not skip")
        except AssertionError:
            failed_loudly = True  # correct: the lock FAILED loudly on the fetch error
        self.assertTrue(
            failed_loudly,
            "expected the dep-edge lock to FAIL (AssertionError) on a fetch error")

    def test_dep_drift_fails(self):
        # HERMETIC: a WRONG resolved dependency must FAIL the lock.
        def wrong(url, timeout=20):
            return {"dependencies": {"playwright": "9.9.9-wrong"}}
        with self.assertRaises(AssertionError):
            _dep_edge_check(self, p.PLAYWRIGHT_MCP_VERSION, p.PLAYWRIGHT_PW_VERSION,
                            fetch=wrong)

    def test_dep_match_passes(self):
        # HERMETIC: the correct resolved dependency passes.
        def ok(url, timeout=20):
            return {"dependencies": {"playwright": p.PLAYWRIGHT_PW_VERSION}}
        _dep_edge_check(self, p.PLAYWRIGHT_MCP_VERSION, p.PLAYWRIGHT_PW_VERSION,
                        fetch=ok)


# --------------------------------------------------------------------------- #
# Item 1 / review A 🟡-1 — the settings.json env (cli_config) is the 5th surface
# and must follow the SAME class-agnostic resolver, never hardcode /opt on a
# merely-present (mismatched) /opt.
# --------------------------------------------------------------------------- #
class TestConfigEnvResolver1058(unittest.TestCase):
    def _apply(self):
        import airuleset
        return airuleset.apply_managed_settings_defaults({})

    def test_shared_stream_env_is_opt_when_opt_holds_the_pinned_build(self):
        with mock.patch("watchdog.reaper.default_box_class", return_value="shared-stream"), \
                mock.patch.object(p, "_opt_has_pinned_build", return_value=True):
            out = self._apply()
        self.assertEqual(out["env"]["PLAYWRIGHT_BROWSERS_PATH"], str(p.OPT_MS_PLAYWRIGHT))

    def test_shared_stream_env_is_per_user_when_opt_mismatched(self):
        # the #2420 condition: /opt present but NOT the pinned build -> per-user,
        # exactly what the marker/bashrc/server resolve to (no 5th-surface drift).
        with mock.patch("watchdog.reaper.default_box_class", return_value="shared-stream"), \
                mock.patch.object(p, "_opt_has_pinned_build", return_value=False):
            out = self._apply()
        self.assertEqual(out["env"]["PLAYWRIGHT_BROWSERS_PATH"],
                         str(Path.home() / ".cache" / "ms-playwright"))

    def test_non_shared_stream_leaves_the_key_absent(self):
        with mock.patch("watchdog.reaper.default_box_class", return_value="workstation"):
            out = self._apply()
        self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", out.get("env", {}))


# --------------------------------------------------------------------------- #
# #1058 rework-2 — the account-side reap (design items 2, 4b-e)
# --------------------------------------------------------------------------- #
class TestLiveEnvPointsAt1058(unittest.TestCase):
    """#1058 rework-2 item 4(e): `_live_env_points_at` scans /proc/*/environ of
    the current user's processes for `PLAYWRIGHT_BROWSERS_PATH=<path>`, skipping
    unreadable/foreign pids (never fatal), matching the path EXACTLY (never a
    prefix). This is the idle-MCP-server case the fd/cwd `_target_in_live_use`
    scan misses (a server that will launch chromium later has the env but no open
    fd inside the cache yet)."""

    def _proc(self, table):
        """A fake /proc tree. `table` maps pid(str) -> environ bytes|None
        (None => an unreadable environ, e.g. a foreign-uid pid)."""
        proc = Path(tempfile.mkdtemp()) / "proc"
        proc.mkdir()
        for pid, environ in table.items():
            d = proc / pid
            d.mkdir()
            if environ is not None:
                (d / "environ").write_bytes(environ)
        # a non-digit entry must be ignored
        (proc / "self").mkdir()
        return proc

    def _env(self, *pairs):
        return b"\x00".join(k.encode() + b"=" + v.encode() for k, v in pairs) + b"\x00"

    def test_matches_a_pid_pointing_at_the_exact_path(self):
        per_user = "/home/u/.cache/ms-playwright"
        proc = self._proc({
            "100": self._env(("PLAYWRIGHT_BROWSERS_PATH", per_user), ("X", "y")),
            "200": self._env(("HOME", "/home/u")),
        })
        self.assertEqual(p._live_env_points_at(per_user, proc_dir=proc), [100])

    def test_exact_path_only_never_a_prefix(self):
        per_user = "/home/u/.cache/ms-playwright"
        proc = self._proc({
            # a DIFFERENT path that merely has per_user as a prefix must NOT match
            "300": self._env(("PLAYWRIGHT_BROWSERS_PATH", per_user + "-old")),
        })
        self.assertEqual(p._live_env_points_at(per_user, proc_dir=proc), [])

    def test_skips_an_unreadable_pid_never_fatal(self):
        per_user = "/home/u/.cache/ms-playwright"
        proc = self._proc({
            "400": None,  # unreadable environ (foreign uid) — skipped, not fatal
            "500": self._env(("PLAYWRIGHT_BROWSERS_PATH", per_user)),
        })
        self.assertEqual(p._live_env_points_at(per_user, proc_dir=proc), [500])

    def test_total_proc_failure_is_never_fatal(self):
        # a missing /proc returns [] (the reap's live_check gate is the fail-safe)
        missing = Path(tempfile.mkdtemp()) / "no-such-proc"
        self.assertEqual(p._live_env_points_at("/home/u/.cache/ms-playwright",
                                               proc_dir=missing), [])


class TestReadBrowsersPathMarker1058(unittest.TestCase):
    """#1058 rework-2: `_read_browsers_path_marker` returns the marker's Path, or
    None on absent/empty/unreadable/corrupt — never fatal (review A 🟡-1 / B 🟡-5)."""

    def _with_marker(self, contents):
        """Run the reader with the marker constant pointed at a temp file whose
        bytes are `contents` (None = no file at all)."""
        marker = Path(tempfile.mkdtemp()) / ".claude" / "airuleset-playwright-browsers-path"
        marker.parent.mkdir(parents=True)
        if contents is not None:
            marker.write_bytes(contents)
        with mock.patch.object(p, "PLAYWRIGHT_BROWSERS_PATH_MARKER", marker):
            return p._read_browsers_path_marker()

    def test_reads_the_path(self):
        self.assertEqual(self._with_marker(b"/opt/ms-playwright\n"),
                         Path("/opt/ms-playwright"))

    def test_absent_marker_is_none(self):
        self.assertIsNone(self._with_marker(None))

    def test_empty_marker_is_none(self):
        self.assertIsNone(self._with_marker(b"   \n"))

    def test_non_utf8_marker_is_none_never_fatal(self):
        # a corrupt (non-UTF-8) marker must degrade to None, not raise
        # UnicodeDecodeError out of the reader (review A 🟡-1).
        self.assertIsNone(self._with_marker(b"\xff\xfe/opt"))


class TestPerUserReapRework2_1058(unittest.TestCase):
    """#1058 rework-2 item 2/4: the ACCOUNT reaps its own per-user chromium pair
    ONLY when (i) the marker ALREADY pointed at /opt before this install, (S) /opt
    genuinely holds the complete pinned build (survivor guard), (ii) no live
    process of this user carries PLAYWRIGHT_BROWSERS_PATH=<per-user> in its
    environ, and (iii) the fd/cwd liveness check is clear. Removes ONLY the
    chromium pair (chromium-* / chromium_headless_shell-*), never ffmpeg, never
    /opt, never a symlink.

    setUp patches `_opt_has_pinned_build` -> True by default (the survivor guard
    (S) must PASS for the other gates to be exercised hermetically — a test box
    has no real /opt); the survivor-guard test overrides it to False."""

    def setUp(self):
        patcher = mock.patch.object(p, "_opt_has_pinned_build", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _per_user_cache(self, *dirs):
        d = Path(tempfile.mkdtemp()) / ".cache" / "ms-playwright"
        d.mkdir(parents=True)
        for name in dirs:
            (d / name).mkdir()
        return d

    def _names(self, d):
        return sorted(x.name for x in d.iterdir())

    def test_reaps_the_chromium_pair_when_all_gates_clear(self):
        # marker already /opt + no live env + not in use -> reap ONLY the chromium
        # pair, leave ffmpeg + unrelated families alone.
        cache = self._per_user_cache(
            "chromium-1244", "chromium_headless_shell-1244",
            "ffmpeg-1011", "firefox-1400")
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
            live_env=lambda path: [], live_check=lambda d: False)
        self.assertTrue(reaped)
        names = self._names(cache)
        self.assertNotIn("chromium-1244", names)
        self.assertNotIn("chromium_headless_shell-1244", names)
        self.assertIn("ffmpeg-1011", names, "ffmpeg is never reaped here (B 🟡-2)")
        self.assertIn("firefox-1400", names, "unrelated families are left alone")

    def test_refuses_when_a_live_server_env_points_at_the_per_user_cache(self):
        # (ii): an IDLE MCP server whose environ still points at the per-user path
        # (the david3/david4 case fuser/fd scans miss) blocks the reap.
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
            live_env=lambda path: [2188677], live_check=lambda d: False)
        self.assertFalse(reaped)
        self.assertIn("chromium-1244", self._names(cache),
                      "a live env pid must block the reap")

    def test_refuses_on_the_first_marker_flip(self):
        # (i): the marker was still per-user before this install (the first push
        # that flips it to /opt) -> keep, so every env surface has been /opt for at
        # least one full cycle before we delete the copy they might still use.
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        prev = cache  # previous marker == the per-user cache, NOT /opt
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=prev,
            live_env=lambda path: [], live_check=lambda d: False)
        self.assertFalse(reaped)
        self.assertIn("chromium-1244", self._names(cache),
                      "the reap must wait one cycle after the marker flips to /opt")

    def test_refuses_when_the_fd_cwd_liveness_check_is_dirty(self):
        # (iii): a process with an open fd / cwd inside the cache blocks the reap.
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
            live_env=lambda path: [], live_check=lambda d: True)
        self.assertFalse(reaped)
        self.assertIn("chromium-1244", self._names(cache))

    def test_never_touches_the_root_owned_opt(self):
        # Toothed for the `_is_per_user_cache` guard SPECIFICALLY (review B 🟡-3):
        # a /opt-shaped dir that EXISTS with a chromium pair, so `_is_per_user_
        # cache` is the ONLY thing preventing a reap (with the survivor guard
        # forced True via setUp, a mutation deleting `_is_per_user_cache` would
        # fall through and delete the pair -> RED). An env-independent proof, never
        # the real /opt.
        opt = Path(tempfile.mkdtemp()) / "opt" / "ms-playwright"
        opt.mkdir(parents=True)
        (opt / "chromium-1244").mkdir()
        (opt / "chromium_headless_shell-1244").mkdir()
        reaped = p._reap_per_user_copy_if_safe(
            opt, previous_marker=p.OPT_MS_PLAYWRIGHT,
            live_env=lambda path: [], live_check=lambda d: False)
        self.assertFalse(reaped, "/opt-shaped path is root's — never reaped")
        self.assertIn("chromium-1244", self._names(opt),
                      "the _is_per_user_cache guard must block the reap")

    def test_refuses_when_opt_lacks_the_complete_pinned_build(self):
        # Survivor guard (S), parity with _cleanup_old_builds (review A 🟡-3): even
        # with marker=/opt + no live env + not in use, if /opt does NOT hold the
        # complete pinned build the reap KEEPS the per-user pair (the only chromium
        # then) — a caller reaching here without a complete /opt can never leave
        # the box browserless.
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        with mock.patch.object(p, "_opt_has_pinned_build", return_value=False):
            reaped = p._reap_per_user_copy_if_safe(
                cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
                live_env=lambda path: [], live_check=lambda d: False)
        self.assertFalse(reaped)
        self.assertIn("chromium-1244", self._names(cache),
                      "an incomplete /opt must block the reap (survivor guard)")

    def test_never_removes_a_symlink(self):
        cache = self._per_user_cache("chromium_headless_shell-1244")
        # a chromium-<pinned> that is actually a SYMLINK must be left untouched
        target = Path(tempfile.mkdtemp())
        (cache / "chromium-1244").symlink_to(target)
        # Toothed (review B 🟡-4): spy on shutil.rmtree and assert it is NEVER
        # CALLED with the symlink path — proving the symlink is EXCLUDED from the
        # reap set, not merely that rmtree happened to refuse it.
        real_rmtree = p.shutil.rmtree
        calls = []

        def spy(path, *a, **k):
            calls.append(Path(path))
            return real_rmtree(path, *a, **k)

        with mock.patch.object(p.shutil, "rmtree", side_effect=spy):
            reaped = p._reap_per_user_copy_if_safe(
                cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
                live_env=lambda path: [], live_check=lambda d: False)
        # the real headless-shell dir is reaped; the symlink stays + its target
        # is never followed/removed, and rmtree was never even ATTEMPTED on it
        self.assertTrue(reaped, "the real (non-symlink) dir is still reaped")
        self.assertNotIn(cache / "chromium-1244", calls,
                         "rmtree must never be attempted on the symlink")
        self.assertIn(cache / "chromium_headless_shell-1244", calls,
                      "the real dir is reaped")
        self.assertTrue((cache / "chromium-1244").is_symlink(),
                        "a symlink is never removed")
        self.assertTrue(target.is_dir(), "a symlink target is never followed")

    def test_idempotent_no_pair_present(self):
        cache = self._per_user_cache("ffmpeg-1011")
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
            live_env=lambda path: [], live_check=lambda d: False)
        self.assertFalse(reaped, "nothing to reap -> False, no crash")

    def test_keeps_on_previous_marker_none(self):
        # gate (i): an absent/unreadable marker reads as None -> None != /opt ->
        # KEEP (the conservative direction — a marker problem never reaps).
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        reaped = p._reap_per_user_copy_if_safe(
            cache, previous_marker=None,
            live_env=lambda path: [], live_check=lambda d: False)
        self.assertFalse(reaped)
        self.assertIn("chromium-1244", self._names(cache))

    def test_rmtree_failure_is_non_fatal(self):
        # best-effort: a rmtree OSError on one dir is logged, never raised; the
        # other dir is still reaped.
        cache = self._per_user_cache("chromium-1244", "chromium_headless_shell-1244")
        real_rmtree = p.shutil.rmtree

        def flaky(path, *a, **k):
            if Path(path).name == "chromium-1244":
                raise OSError("boom")
            return real_rmtree(path, *a, **k)

        with mock.patch.object(p.shutil, "rmtree", side_effect=flaky):
            reaped = p._reap_per_user_copy_if_safe(
                cache, previous_marker=p.OPT_MS_PLAYWRIGHT,
                live_env=lambda path: [], live_check=lambda d: False)
        names = self._names(cache)
        self.assertTrue(reaped, "the dir that could be removed was reaped")
        self.assertIn("chromium-1244", names, "the failed dir remains, no crash")
        self.assertNotIn("chromium_headless_shell-1244", names)

    def test_provision_reaps_after_reconcile_when_resolved_is_opt(self):
        # End-to-end wiring: provision reads the PREVIOUS marker (/opt), resolves
        # to /opt, and reaps the per-user copy after writing the new marker/env.
        home = Path(tempfile.mkdtemp())
        cache = home / ".cache" / "ms-playwright"
        cache.mkdir(parents=True)
        (cache / "chromium-1244").mkdir()
        (cache / "chromium_headless_shell-1244").mkdir()
        marker = home / ".claude" / "airuleset-playwright-browsers-path"
        marker.parent.mkdir(parents=True)
        marker.write_text(str(p.OPT_MS_PLAYWRIGHT) + "\n")  # previous marker = /opt
        with mock.patch.object(p, "PLAYWRIGHT_BROWSER_CACHE", cache), \
             mock.patch.object(p, "PLAYWRIGHT_BROWSERS_PATH_MARKER", marker), \
             mock.patch.object(p, "ensure_playwright_browsers"), \
             mock.patch.object(p, "resolved_browsers_path", return_value=p.OPT_MS_PLAYWRIGHT), \
             mock.patch("cli_playwright_mcp._target_in_live_use", return_value=False), \
             mock.patch.object(p, "_live_env_points_at", return_value=[]), \
             mock.patch.object(p, "reconcile_playwright_mcp_file", return_value=True) as rec:
            ok = p.provision_playwright_mcp(box_class="shared-stream")
        self.assertTrue(ok)
        rec.assert_called_once()
        names = sorted(x.name for x in cache.iterdir())
        self.assertNotIn("chromium-1244", names,
                         "provision must reap the per-user copy once /opt is in use")
        self.assertNotIn("chromium_headless_shell-1244", names)

    def test_provision_does_not_reap_when_resolved_is_per_user(self):
        # /opt is NOT the resolved path (e.g. /opt still incomplete) -> no reap,
        # the account keeps its own copy (the resolver chose it).
        home = Path(tempfile.mkdtemp())
        cache = home / ".cache" / "ms-playwright"
        cache.mkdir(parents=True)
        (cache / "chromium-1244").mkdir()
        (cache / "chromium_headless_shell-1244").mkdir()
        marker = home / ".claude" / "airuleset-playwright-browsers-path"
        marker.parent.mkdir(parents=True)
        marker.write_text(str(cache) + "\n")
        with mock.patch.object(p, "PLAYWRIGHT_BROWSER_CACHE", cache), \
             mock.patch.object(p, "PLAYWRIGHT_BROWSERS_PATH_MARKER", marker), \
             mock.patch.object(p, "ensure_playwright_browsers"), \
             mock.patch.object(p, "resolved_browsers_path", return_value=cache), \
             mock.patch.object(p, "reconcile_playwright_mcp_file", return_value=True):
            ok = p.provision_playwright_mcp(box_class="shared-stream")
        self.assertTrue(ok)
        names = sorted(x.name for x in cache.iterdir())
        self.assertIn("chromium-1244", names,
                      "no reap when the resolver still chose the per-user cache")


if __name__ == "__main__":
    unittest.main()
