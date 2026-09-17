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


# --------------------------------------------------------------------------- #
# Item 5 — dep-edge lock (CI-only, network)
# --------------------------------------------------------------------------- #
class TestDepEdgeLock1058(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AIRULESET_NET_TESTS") == "1",
                         "network test — set AIRULESET_NET_TESTS=1 (CI sets it)")
    def test_mcp_pkg_depends_on_the_pinned_playwright(self):
        url = "https://registry.npmjs.org/@playwright/mcp/" + p.PLAYWRIGHT_MCP_VERSION
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:   # network/registry hiccup is not a drift — skip honestly
            self.skipTest("npm registry unreachable: %s" % e)
        deps = data.get("dependencies", {})
        self.assertEqual(
            deps.get("playwright"), p.PLAYWRIGHT_PW_VERSION,
            "@playwright/mcp@%s depends on playwright %r but the pin is %r — the trio drifted"
            % (p.PLAYWRIGHT_MCP_VERSION, deps.get("playwright"), p.PLAYWRIGHT_PW_VERSION))


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


if __name__ == "__main__":
    unittest.main()
