"""Behaviour tests for #965 disk-guard additions: android-build-intermediates,
npx-cache, scratch-worktrees, drain-log honesty, and rung skip-after-3-low-yield.

Each test uses a synthetic HOME under a tempdir — no real disk reads, no sudo,
no real git. All seams injectable.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ======================================================================= #
# 1. android-build-intermediates rung
# ======================================================================= #

class TestAndroidBuildIntermediates(unittest.TestCase):
    """The rung discovers android build dirs under $HOME/devel/**/node_modules
    and $HOME/.gradle on a shared-stream box, with NO age gate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, relpath, size_bytes=1024):
        """Create a file at relpath under self.home with given size."""
        p = Path(self.home) / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size_bytes)
        return str(p)

    def test_discovers_android_build_dir(self):
        """android/build inside node_modules is discovered as reclaimable."""
        from watchdog.disk_guard import discover_android_build_intermediates
        self._mk("devel/proj/.claude/worktrees/agent-x/frontline/node_modules/"
                 "react-native/android/build/intermediates/something.so", 4096)
        rows = discover_android_build_intermediates(
            home=self.home, dir_stats_fn=lambda p: (9000000, 0))
        self.assertTrue(len(rows) > 0,
                        "should discover android/build dir in node_modules")
        self.assertEqual(rows[0]["cls"], "android-build")
        self.assertEqual(rows[0]["kind"], "delete")

    def test_discovers_gradle_caches(self):
        """~/.gradle/caches is discovered."""
        from watchdog.disk_guard import discover_android_build_intermediates
        self._mk(".gradle/caches/transforms-3/foo.jar", 2048)
        rows = discover_android_build_intermediates(
            home=self.home, dir_stats_fn=lambda p: (500000, 0))
        gradle_rows = [r for r in rows if ".gradle/caches" in r["path"]]
        self.assertTrue(len(gradle_rows) > 0,
                        "should discover .gradle/caches")

    def test_discovers_gradle_daemon(self):
        """~/.gradle/daemon is discovered."""
        from watchdog.disk_guard import discover_android_build_intermediates
        self._mk(".gradle/daemon/8.0/daemon-pid.log", 1024)
        rows = discover_android_build_intermediates(
            home=self.home, dir_stats_fn=lambda p: (100_000, 0))
        daemon_rows = [r for r in rows if ".gradle/daemon" in r["path"]]
        self.assertTrue(len(daemon_rows) > 0,
                        "should discover .gradle/daemon")

    def test_discovers_android_cxx_dir(self):
        """android/.cxx inside node_modules is discovered."""
        from watchdog.disk_guard import discover_android_build_intermediates
        self._mk("devel/proj/node_modules/react-native/android/.cxx/cmake/debug/x86_64/libfoo.so",
                 4096)
        rows = discover_android_build_intermediates(
            home=self.home, dir_stats_fn=lambda p: (5000000, 0))
        cxx_rows = [r for r in rows if ".cxx" in r["path"]]
        self.assertTrue(len(cxx_rows) > 0,
                        "should discover android/.cxx dir")

    def test_no_age_gate(self):
        """All dirs are discovered regardless of mtime — no age gate on shared-stream."""
        from watchdog.disk_guard import discover_android_build_intermediates
        build_path = self._mk("devel/proj/node_modules/mod/android/build/out.so", 4096)
        build_dir = str(Path(build_path).parent.parent)  # android/build
        os.utime(build_dir, (time.time(), time.time()))
        rows = discover_android_build_intermediates(
            home=self.home, dir_stats_fn=lambda p: (1000, 0))
        self.assertTrue(len(rows) > 0,
                        "fresh dirs should still be discovered (no age gate)")

    def test_empty_home_returns_empty(self):
        """No devel dir -> empty result."""
        from watchdog.disk_guard import discover_android_build_intermediates
        rows = discover_android_build_intermediates(home=self.home)
        self.assertEqual(rows, [])

    def test_rung_in_prevention_planners(self):
        """android-build rung appears in _prevention_planners."""
        from watchdog.disk_guard import _prevention_planners
        now = time.time()
        planners = _prevention_planners(self.home, now)
        labels = [label for label, _ in planners]
        self.assertIn("android-build", labels,
                      "android-build must be in prevention planners")

    def test_android_build_in_reclaimable_classes(self):
        """android-build class is in RECLAIMABLE_CLASSES."""
        from watchdog.disk_guard import RECLAIMABLE_CLASSES
        self.assertIn("android-build", RECLAIMABLE_CLASSES)


# ======================================================================= #
# 2. npx-cache rung
# ======================================================================= #

class TestNpxCache(unittest.TestCase):
    """discover_npm_uv_cache now also discovers ~/.npm/_npx."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_discovers_npx_cache(self):
        """~/.npm/_npx is discovered as a delete action."""
        from watchdog.disk_guard import discover_npm_uv_cache
        npx_dir = Path(self.home) / ".npm" / "_npx"
        npx_dir.mkdir(parents=True)
        (npx_dir / "some-pkg").mkdir()
        (npx_dir / "some-pkg" / "index.js").write_text("x" * 1024)
        rows = discover_npm_uv_cache(home=self.home)
        npx_rows = [r for r in rows if "_npx" in r["path"]]
        self.assertTrue(len(npx_rows) > 0,
                        "should discover ~/.npm/_npx cache")
        self.assertEqual(npx_rows[0]["kind"], "delete")
        self.assertEqual(npx_rows[0]["cls"], "npm-uv-cache")

    def test_still_discovers_cacache(self):
        """~/.npm/_cacache is still discovered (existing behaviour preserved)."""
        from watchdog.disk_guard import discover_npm_uv_cache
        cacache_dir = Path(self.home) / ".npm" / "_cacache"
        cacache_dir.mkdir(parents=True)
        (cacache_dir / "content-v2").mkdir()
        (cacache_dir / "content-v2" / "abc").write_text("data")
        rows = discover_npm_uv_cache(home=self.home)
        cacache_rows = [r for r in rows if "_cacache" in r["path"]]
        self.assertTrue(len(cacache_rows) > 0,
                        "should still discover _cacache")


# ======================================================================= #
# 3. scratch-worktrees rung
# ======================================================================= #

class TestScratchWorktrees(unittest.TestCase):
    """scratch-worktrees discovers git worktrees under scratchpad wt* dirs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp
        self.uid = os.getuid()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_scratch_wt(self, name="wt1234", age_s=8000, make_git=True):
        """Create a simulated scratch worktree dir."""
        scratch = Path(self.tmp) / ("claude-%d" % self.uid) / "sessid" / "scratchpad"
        wt = scratch / name
        wt.mkdir(parents=True)
        if make_git:
            (wt / ".git").write_text("gitdir: /fake/repo/.git/worktrees/" + name)
        old = time.time() - age_s
        os.utime(str(wt), (old, old))
        return str(wt)

    def test_discovers_scratch_worktree(self):
        """A stale unlocked scratch worktree is discovered."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wt5971", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: (200_000_000, 0),
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 0,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertTrue(len(reclaimable) > 0,
                        "stale scratch worktree should be reclaimable")
        self.assertEqual(reclaimable[0]["cls"], "scratch-worktree")

    def test_skips_too_recent(self):
        """A worktree younger than 2h is skipped."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wt1234", age_s=3600)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: 100_000,
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 0,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0)

    def test_skips_dirty(self):
        """A dirty worktree is kept."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wt9999", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "M modified.py\n",
            dir_stats_fn=lambda p: 100_000,
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 0,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0)

    def test_skips_locked(self):
        """A locked worktree is kept."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wtlocked", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: 100_000,
            locked_fn=lambda wt_path, name: True,
            ahead_fn=lambda wt_path: 0,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0)

    def test_skips_ahead(self):
        """A worktree with commits ahead is kept."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wtahead", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: 100_000,
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 3,
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0)

    def test_skips_main_dev_branch(self):
        """A worktree on main/dev branch is never removed."""
        from watchdog.disk_guard import discover_scratch_worktrees
        self._mk_scratch_wt("wtmain", age_s=8000)
        rows = discover_scratch_worktrees(
            tmp_dir=self.tmp, uid=self.uid, now=time.time(),
            git_run_fn=lambda cmd, **kw: "",
            dir_stats_fn=lambda p: 100_000,
            locked_fn=lambda wt_path, name: False,
            ahead_fn=lambda wt_path: 0,
            branch_fn=lambda wt_path: "main",
        )
        reclaimable = [r for r in rows if r.get("kind") == "scratch-worktree-remove"]
        self.assertEqual(len(reclaimable), 0)

    def test_scratch_worktree_in_reclaimable_classes(self):
        """scratch-worktree is in RECLAIMABLE_CLASSES."""
        from watchdog.disk_guard import RECLAIMABLE_CLASSES
        self.assertIn("scratch-worktree", RECLAIMABLE_CLASSES)


# ======================================================================= #
# 4. Reaper Job 38 — extended process matching
# ======================================================================= #

class TestReaperExtendedMatching(unittest.TestCase):
    """_heavy_build_kind matches gradle/cmake/ninja/kotlinc by comm/exe."""

    def test_gradle_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(_heavy_build_kind("gradle assembleRelease"), "gradle")

    def test_gradlew_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(_heavy_build_kind("/home/david1/devel/proj/gradlew build"), "gradlew")

    def test_cmake_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(_heavy_build_kind("cmake --build . --target all"), "cmake")

    def test_ninja_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(_heavy_build_kind("ninja -C build"), "ninja")

    def test_kotlinc_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(
            _heavy_build_kind("kotlinc src/main.kt -include-runtime -d app.jar"),
            "kotlinc")

    def test_kotlinc_jvm_by_comm(self):
        from watchdog.reaper import _heavy_build_kind
        self.assertEqual(_heavy_build_kind("kotlinc-jvm src/main.kt"), "kotlinc")

    def test_finding_jdk_dirs(self):
        """discover_jdk_toolchain_findings returns findings for JDK dirs."""
        from watchdog.reaper import discover_jdk_toolchain_findings
        home = tempfile.mkdtemp()
        try:
            jdk = Path(home) / "tools" / "jdk17"
            jdk.mkdir(parents=True)
            (jdk / "bin").mkdir(parents=True)
            (jdk / "bin" / "java").write_text("fake")
            findings = discover_jdk_toolchain_findings(home=home)
            self.assertTrue(len(findings) > 0,
                            "should find JDK under ~/tools/jdk*")
            self.assertIn("jdk", findings[0].lower())
        finally:
            shutil.rmtree(home, ignore_errors=True)


# ======================================================================= #
# 5. Hook — indirect launches
# ======================================================================= #

class TestHookIndirectLaunches(unittest.TestCase):
    """block-heavy-build-toolchain.sh blocks indirect build launches."""

    def _run_hook(self, cmd, box_class="shared-stream"):
        import subprocess
        hook = REPO / "hooks" / "block-heavy-build-toolchain.sh"
        payload = json.dumps({"tool_input": {"command": cmd}})
        with tempfile.TemporaryDirectory() as home:
            claude = os.path.join(home, ".claude")
            os.makedirs(claude)
            if box_class is not None:
                with open(os.path.join(claude, "airuleset-box-class"), "w") as fh:
                    fh.write(box_class + "\n")
            env = {**os.environ, "HOME": home}
            return subprocess.run(
                ["bash", str(hook)], input=payload, capture_output=True,
                text=True, env=env)

    def test_blocks_npx_expo_run_android(self):
        r = self._run_hook("npx expo run:android")
        self.assertEqual(r.returncode, 2,
                         "should block: npx expo run:android\nstderr=%s" % r.stderr)

    def test_blocks_npx_expo_prebuild(self):
        r = self._run_hook("npx expo prebuild")
        self.assertEqual(r.returncode, 2,
                         "should block: npx expo prebuild\nstderr=%s" % r.stderr)

    def test_blocks_react_native_run_android(self):
        r = self._run_hook("npx react-native run-android")
        self.assertEqual(r.returncode, 2,
                         "should block: npx react-native run-android\nstderr=%s" % r.stderr)

    def test_blocks_npm_run_android(self):
        r = self._run_hook("npm run android")
        self.assertEqual(r.returncode, 2,
                         "should block: npm run android\nstderr=%s" % r.stderr)

    def test_blocks_eas_build_local(self):
        r = self._run_hook("eas build --local --platform android")
        self.assertEqual(r.returncode, 2,
                         "should block: eas build --local\nstderr=%s" % r.stderr)

    def test_allows_npm_test(self):
        r = self._run_hook("npm test")
        self.assertEqual(r.returncode, 0,
                         "npm test should pass\nstderr=%s" % r.stderr)

    def test_allows_npx_playwright_test(self):
        r = self._run_hook("npx playwright test")
        self.assertEqual(r.returncode, 0,
                         "npx playwright test should pass\nstderr=%s" % r.stderr)

    def test_allows_npm_install(self):
        r = self._run_hook("npm install")
        self.assertEqual(r.returncode, 0,
                         "npm install should pass\nstderr=%s" % r.stderr)

    def test_allows_npm_run_test(self):
        r = self._run_hook("npm run test")
        self.assertEqual(r.returncode, 0,
                         "npm run test should pass\nstderr=%s" % r.stderr)

    def test_allows_npx_jest(self):
        r = self._run_hook("npx jest --runInBand")
        self.assertEqual(r.returncode, 0,
                         "npx jest should pass\nstderr=%s" % r.stderr)


# ======================================================================= #
# 7. Drain log honesty — skip-after-3-low-yield
# ======================================================================= #

class TestDrainSkipAfterLowYield(unittest.TestCase):
    """A rung that frees < 1 MiB three times in a row is skipped for 6h."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rung_skipped_after_3_low_yields(self):
        """After 3 consecutive low-yield (<1 MiB) runs, a rung is skipped."""
        from watchdog.disk_guard import (
            _should_skip_low_yield_rung, _record_rung_yield,
            LOW_YIELD_SKIP_COUNT)
        now = time.time()
        rung = "npm-uv-cache"
        for i in range(LOW_YIELD_SKIP_COUNT):
            self.assertFalse(
                _should_skip_low_yield_rung(self.home, rung, now),
                "should not skip before %d low yields" % LOW_YIELD_SKIP_COUNT)
            _record_rung_yield(self.home, rung, freed=500_000, now=now + i)
        self.assertTrue(
            _should_skip_low_yield_rung(self.home, rung, now + LOW_YIELD_SKIP_COUNT),
            "should skip after 3 consecutive low yields")

    def test_rung_not_skipped_after_high_yield(self):
        """A high-yield run resets the counter."""
        from watchdog.disk_guard import (
            _should_skip_low_yield_rung, _record_rung_yield)
        now = time.time()
        rung = "npm-uv-cache"
        _record_rung_yield(self.home, rung, freed=500_000, now=now)
        _record_rung_yield(self.home, rung, freed=500_000, now=now + 1)
        _record_rung_yield(self.home, rung, freed=5_000_000, now=now + 2)
        self.assertFalse(
            _should_skip_low_yield_rung(self.home, rung, now + 3),
            "high yield should reset the counter")

    def test_skip_expires_after_6h(self):
        """The skip expires after 6 hours."""
        from watchdog.disk_guard import (
            _should_skip_low_yield_rung, _record_rung_yield,
            LOW_YIELD_SKIP_COUNT, LOW_YIELD_SKIP_S)
        now = time.time()
        rung = "npm-uv-cache"
        for i in range(LOW_YIELD_SKIP_COUNT):
            _record_rung_yield(self.home, rung, freed=500_000, now=now + i)
        self.assertTrue(
            _should_skip_low_yield_rung(self.home, rung, now + LOW_YIELD_SKIP_COUNT))
        # last_ts is at now+2 (3rd record), so we need now+2+SKIP_S+1 to be past the window
        self.assertFalse(
            _should_skip_low_yield_rung(self.home, rung, now + LOW_YIELD_SKIP_COUNT + LOW_YIELD_SKIP_S + 1),
            "skip should expire after 6h")


if __name__ == "__main__":
    unittest.main()
