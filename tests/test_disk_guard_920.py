"""RED tests for #920 — disk-guard drain ladder new rungs.

Tests that the new rung classes (tmp-test, runner-diag, npm-uv-cache) exist
in the disk-guard, the prevention threshold fires at 70%, and top_consumers
includes the new classes. Each test is designed to FAIL before the fix.
"""

import os
import time

import watchdog.disk_guard as dg


# --------------------------------------------------------------------------- #
# 1. New reclaimable classes exist
# --------------------------------------------------------------------------- #
class TestNewReclaimableClasses:
    """The new rung classes must be in RECLAIMABLE_CLASSES."""

    def test_tmp_test_in_fence(self):
        assert "tmp-test" in dg.RECLAIMABLE_CLASSES

    def test_runner_diag_in_fence(self):
        assert "runner-diag" in dg.RECLAIMABLE_CLASSES

    def test_npm_uv_cache_in_fence(self):
        assert "npm-uv-cache" in dg.RECLAIMABLE_CLASSES


# --------------------------------------------------------------------------- #
# 2. Discovery functions exist and produce correct output
# --------------------------------------------------------------------------- #
class TestDiscoverStaleTmpTestDirs:
    """discover_stale_tmp_test_dirs must find jest_*/pytest-of-*/tmp* dirs."""

    def test_function_exists(self):
        assert hasattr(dg, "discover_stale_tmp_test_dirs")

    def test_finds_old_jest_dir(self, tmp_path):
        jest = tmp_path / "jest_abc123"
        jest.mkdir()
        os.utime(jest, (0, time.time() - 2 * 86400))
        rows = dg.discover_stale_tmp_test_dirs(
            tmp_dir=str(tmp_path), now=time.time(), min_age_days=1,
            uid=os.getuid())
        candidates = [r for r in rows if r.get("reason") is None]
        assert any("jest_abc123" in r["path"] for r in candidates)

    def test_finds_old_pytest_dir(self, tmp_path):
        pyt = tmp_path / "pytest-of-user"
        pyt.mkdir()
        os.utime(pyt, (0, time.time() - 2 * 86400))
        rows = dg.discover_stale_tmp_test_dirs(
            tmp_dir=str(tmp_path), now=time.time(), min_age_days=1,
            uid=os.getuid())
        candidates = [r for r in rows if r.get("reason") is None]
        assert any("pytest-of-user" in r["path"] for r in candidates)

    def test_skips_recent_dir(self, tmp_path):
        jest = tmp_path / "jest_new"
        jest.mkdir()
        # fresh — no backdate
        rows = dg.discover_stale_tmp_test_dirs(
            tmp_dir=str(tmp_path), now=time.time(), min_age_days=1,
            uid=os.getuid())
        candidates = [r for r in rows if r.get("reason") is None]
        assert not any("jest_new" in r["path"] for r in candidates)


class TestDiscoverRunnerDiag:
    """discover_runner_diag_logs must find _diag logs older than min_age_days."""

    def test_function_exists(self):
        assert hasattr(dg, "discover_runner_diag_logs")

    def test_finds_old_diag_dir(self, tmp_path):
        runner = tmp_path / "actions-runner"
        diag = runner / "_work" / "_diag"
        diag.mkdir(parents=True)
        logfile = diag / "Worker_20260901.log"
        logfile.write_text("log content")
        os.utime(logfile, (0, time.time() - 3 * 86400))
        rows = dg.discover_runner_diag_logs(
            runner_root=str(tmp_path), now=time.time(), min_age_days=2,
            pgrep_fn=lambda _p: "")
        candidates = [r for r in rows if r.get("reason") is None]
        assert len(candidates) > 0

    def test_skips_when_worker_live(self, tmp_path):
        runner = tmp_path / "actions-runner"
        diag = runner / "_work" / "_diag"
        diag.mkdir(parents=True)
        logfile = diag / "Worker_20260901.log"
        logfile.write_text("log content")
        os.utime(logfile, (0, time.time() - 3 * 86400))
        rows = dg.discover_runner_diag_logs(
            runner_root=str(tmp_path), now=time.time(), min_age_days=2,
            pgrep_fn=lambda _p: "12345\n")
        # When Runner.Worker is live, all should be skipped
        candidates = [r for r in rows if r.get("reason") is None]
        assert len(candidates) == 0


class TestDiscoverNpmUvCache:
    """discover_npm_uv_cache must find ~/.npm/_cacache."""

    def test_function_exists(self):
        assert hasattr(dg, "discover_npm_uv_cache")

    def test_finds_npm_cacache(self, tmp_path):
        cacache = tmp_path / ".npm" / "_cacache"
        cacache.mkdir(parents=True)
        (cacache / "some-cache-file").write_text("data")
        rows = dg.discover_npm_uv_cache(
            home=str(tmp_path), dir_stats_fn=lambda p: (1000, 5))
        candidates = [r for r in rows if r.get("reason") is None]
        assert any("_cacache" in r["path"] for r in candidates)


# --------------------------------------------------------------------------- #
# 3. Prevention threshold
# --------------------------------------------------------------------------- #
class TestPreventionThreshold:
    """A PREVENTION_PCT constant must exist at 70."""

    def test_prevention_pct_exists(self):
        assert hasattr(dg, "PREVENTION_PCT")
        assert dg.PREVENTION_PCT == 70


# --------------------------------------------------------------------------- #
# 4. New rungs in the ladder and top_consumers
# --------------------------------------------------------------------------- #
class TestNewRungsInLadder:
    """_default_planners must include the new rung labels."""

    def test_tmp_test_in_ladder(self):
        home = "/tmp/fake"
        now = time.time()
        planners = dg._default_planners(home, now)
        labels = [label for label, _fn in planners]
        assert "tmp-test" in labels

    def test_runner_diag_in_ladder(self):
        home = "/tmp/fake"
        now = time.time()
        planners = dg._default_planners(home, now)
        labels = [label for label, _fn in planners]
        assert "runner-diag" in labels

    def test_npm_uv_cache_in_ladder(self):
        home = "/tmp/fake"
        now = time.time()
        planners = dg._default_planners(home, now)
        labels = [label for label, _fn in planners]
        assert "npm-uv-cache" in labels
