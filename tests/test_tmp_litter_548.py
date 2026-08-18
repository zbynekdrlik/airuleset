"""/tmp litter root fix (#548) — per-run TMPDIR redirect (CORE) + push-gate
litter guard + fleet age-gated reaper.

Root cause (STEP-0, dev1 2026-08-18): 465k `/tmp/tmp*` (test-suite
`tempfile.mkdtemp` litter, ~459 call sites without cleanup — #385 class) +
136k `/tmp/airuleset-*` (hook session state, hardcoded /tmp, no fleet reaper).
ext4 htree ENOSPC + root inodes 45%.

This file covers all four points:
  - CORE:  the pytest `conftest._per_run_tempdir` context manager that redirects
           `tempfile.tempdir`/`TMPDIR` into one throwaway per-run dir removed
           wholesale on exit (catches every raw mkdtemp without editing it).
  - GUARD: `cli_remote._check_push_tmpdir_litter` — the push-gate anti-recidivism
           tripwire (the suite must not leak more than a calibrated cap into its
           redirected per-run TMPDIR).
  - REAPER: `cli_scratch_sweep.discover_stray_airuleset_state_candidates` /
           `sweep_airuleset_state` — the fleet age-gated `/tmp/airuleset-*` state
           reaper (>3d), plus the live-reaping install-step wiring.
"""

import inspect
import os
import sys
import tempfile
import time
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # tests/ — for `import conftest`

import airuleset            # noqa: E402
import cli_remote           # noqa: E402
import cli_scratch_sweep    # noqa: E402

DAY = 86400.0


# --------------------------------------------------------------------------- #
# CORE — the per-run TMPDIR redirect context manager (conftest).
# --------------------------------------------------------------------------- #

class TestPerRunTempdirRedirect(unittest.TestCase):
    def _conftest(self):
        import conftest
        return conftest

    def test_redirects_tempfile_and_env_then_restores_and_cleans_up(self):
        """The helper redirects `tempfile.tempdir` + `$TMPDIR` into ONE per-run
        dir so every raw `tempfile.mkdtemp` lands there, then restores the
        originals and removes the whole dir on exit. RED: the helper does not
        exist yet (AttributeError)."""
        conftest = self._conftest()
        with TemporaryDirectory() as base:
            orig_tempdir = tempfile.tempdir
            orig_env = os.environ.get("TMPDIR")
            with conftest._per_run_tempdir(base=base) as run_dir:
                self.assertTrue(run_dir.exists())
                self.assertEqual(tempfile.gettempdir(), str(run_dir))
                self.assertEqual(os.environ.get("TMPDIR"), str(run_dir))
                d = tempfile.mkdtemp()             # a RAW mkdtemp, no custom dir=
                self.assertTrue(d.startswith(str(run_dir)),
                                "raw mkdtemp must land inside the per-run dir")
            # restored + removed wholesale
            self.assertEqual(tempfile.tempdir, orig_tempdir)
            self.assertEqual(os.environ.get("TMPDIR"), orig_env)
            self.assertFalse(run_dir.exists(),
                             "the per-run dir (and everything leaked into it) is removed on exit")

    def test_per_run_dir_name_is_reaper_recognisable(self):
        """The per-run dir is named `airuleset-pytest-run-*` so that a KILLED
        pytest run's leaked dir is itself reaped by the #548 airuleset-* reaper
        (>3d) — the redirect narrows the leak to at most one self-healing dir."""
        conftest = self._conftest()
        with TemporaryDirectory() as base:
            with conftest._per_run_tempdir(base=base) as run_dir:
                self.assertTrue(run_dir.name.startswith("airuleset-pytest-run-"))

    def test_session_fixture_wraps_the_helper_at_session_scope(self):
        """Source-lock: the autouse session fixture (new — no session-scoped
        fixture existed before #548) wires the helper, so every pytest run gets
        the redirect."""
        conftest = self._conftest()
        src = inspect.getsource(conftest)
        self.assertIn('scope="session"', src)
        self.assertIn("_per_run_tempdir", src)
        self.assertIn("autouse=True", src)


# --------------------------------------------------------------------------- #
# GUARD — the push-gate litter tripwire.
# --------------------------------------------------------------------------- #

class TestPushTmpdirLitterGuard(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-548-guard-")
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)

    def test_clean_run_passes(self):
        ok, count = cli_remote._check_push_tmpdir_litter(self.d, cap=50)
        self.assertTrue(ok)
        self.assertEqual(count, 0)

    def test_leak_over_cap_fails_loudly(self):
        for i in range(60):
            (self.d / ("tmp%08d" % i)).mkdir()
        ok, count = cli_remote._check_push_tmpdir_litter(self.d, cap=50)
        self.assertFalse(ok, "a suite leaking > cap entries must trip the guard")
        self.assertEqual(count, 60)

    def test_at_cap_is_still_ok(self):
        for i in range(50):
            (self.d / ("tmp%08d" % i)).mkdir()
        ok, count = cli_remote._check_push_tmpdir_litter(self.d, cap=50)
        self.assertTrue(ok, "exactly at the cap is not yet a regression")

    def test_env_override_of_cap(self):
        for i in range(10):
            (self.d / ("tmp%08d" % i)).mkdir()
        with m.patch.dict(os.environ, {"AIRULESET_PUSH_TMPDIR_LITTER_CAP": "5"}):
            ok, count = cli_remote._check_push_tmpdir_litter(self.d)
            self.assertFalse(ok)
        with m.patch.dict(os.environ, {"AIRULESET_PUSH_TMPDIR_LITTER_CAP": "5000"}):
            ok, count = cli_remote._check_push_tmpdir_litter(self.d)
            self.assertTrue(ok)

    def test_missing_dir_never_raises(self):
        ok, count = cli_remote._check_push_tmpdir_litter(self.d / "gone", cap=50)
        self.assertTrue(ok)
        self.assertEqual(count, 0)

    def test_cmd_push_injects_tmpdir_and_wires_the_guard(self):
        """Source-lock (#271/#385 pattern): cmd_push must point the whole
        `unittest discover` subprocess at a per-run TMPDIR AND run the litter
        guard on it — the guard fails no test if silently deleted otherwise."""
        src = inspect.getsource(cli_remote.cmd_push)
        self.assertIn('"TMPDIR"', src)
        self.assertIn("env=test_env", src)
        self.assertIn("_check_push_tmpdir_litter", src)


# --------------------------------------------------------------------------- #
# REAPER — the fleet age-gated /tmp/airuleset-* state reaper (>3d).
# --------------------------------------------------------------------------- #

class TestAirulesetStateReaper(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-548-reap-")
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.uid = os.getuid()
        self.now = time.time()

    def _mkfile(self, name, age_days=10.0):
        p = self.d / name
        p.write_text("state")
        t = self.now - age_days * DAY
        os.utime(p, (t, t))
        return p

    def _mkdir(self, name, age_days=10.0):
        p = self.d / name
        p.mkdir()
        (p / "f").write_text("x")
        t = self.now - age_days * DAY
        os.utime(p / "f", (t, t))
        os.utime(p, (t, t))
        return p

    def test_aged_state_file_is_a_candidate(self):
        self._mkfile("airuleset-main-bash-run-abc123", age_days=5)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertEqual(disc["total_matched"], 1)
        self.assertIsNone(disc["examined"][0]["reason"])

    def test_aged_state_dir_is_a_candidate(self):
        self._mkdir("airuleset-scopegate-test-xyz", age_days=5)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertEqual(disc["total_matched"], 1)
        self.assertIsNone(disc["examined"][0]["reason"])

    def test_recent_state_is_kept(self):
        self._mkfile("airuleset-designgate-fresh", age_days=1)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertIn("too recent", disc["examined"][0]["reason"])

    def test_exec_permission_markers_are_excluded(self):
        """The exec-permission markers (main-exec-ok / fable-exec-ok) are job
        22's live-checked domain — never reaped here, so a live session's
        deliberately-granted exception is never revoked mid-work."""
        self._mkfile("airuleset-main-exec-ok-sid1", age_days=10)
        self._mkfile("airuleset-fable-exec-ok-sid2", age_days=10)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertEqual(disc["total_matched"], 0,
                         "exec-permission markers must not even be matched by this reaper")

    def test_non_airuleset_entry_is_ignored(self):
        self._mkdir("tmpabcd1234", age_days=10)     # #513's domain, not this reaper's
        self._mkfile("claude-user-active-x", age_days=10)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertEqual(disc["total_matched"], 0)

    def test_foreign_uid_never_touched(self):
        self._mkfile("airuleset-stop-block-foreign", age_days=10)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid + 99999, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertIn("another uid", disc["examined"][0]["reason"])

    def test_symlink_is_refused(self):
        target = self._mkfile("airuleset-real-target", age_days=10)
        link = self.d / "airuleset-link-decoy"
        link.symlink_to(target)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        row = next(r for r in disc["examined"] if r["path"].endswith("airuleset-link-decoy"))
        self.assertIn("symlink", row["reason"])

    def test_in_live_use_is_skipped(self):
        p = self._mkfile("airuleset-held-open", age_days=10)
        key = os.path.realpath(str(p))
        with m.patch.object(cli_scratch_sweep, "_scan_live_tmp_tops", return_value={key}):
            disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
                tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, proc_dir="/proc")
        self.assertIn("in live use", disc["examined"][0]["reason"])

    def test_total_lockout_skips_everything_failsafe(self):
        self._mkfile("airuleset-somestate-lock", age_days=10)
        disc = cli_scratch_sweep.discover_stray_airuleset_state_candidates(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3,
            proc_dir=str(self.d / "no-such-proc"))
        self.assertIn("in live use", disc["examined"][0]["reason"])

    def test_sweep_live_reaps_aged_keeps_recent(self):
        aged_f = self._mkfile("airuleset-poll-old", age_days=10)
        aged_d = self._mkdir("airuleset-scopegate-test-old", age_days=10)
        recent = self._mkfile("airuleset-poll-new", age_days=1)
        s = cli_scratch_sweep.sweep_airuleset_state(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, force=True,
            proc_dir="/proc", live=True, log_path=self.d / "log", state_path=self.d / "st")
        self.assertEqual(s["removed"], 2)
        self.assertFalse(aged_f.exists())
        self.assertFalse(aged_d.exists(), "aged state DIR reaped, not just files")
        self.assertTrue(recent.exists())

    def test_sweep_report_only_by_default_deletes_nothing(self):
        p = self._mkfile("airuleset-report-only", age_days=10)
        s = cli_scratch_sweep.sweep_airuleset_state(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, force=True,
            proc_dir="/proc", live=False, log_path=self.d / "log", state_path=self.d / "st")
        self.assertEqual(s["removed"], 0)
        self.assertEqual(s["reclaimable"], 1)
        self.assertTrue(p.exists())

    def test_dry_run_mutates_nothing(self):
        p = self._mkfile("airuleset-dryrun", age_days=10)
        st = self.d / "st"
        s = cli_scratch_sweep.sweep_airuleset_state(
            tmp_dir=self.d, uid=self.uid, now=self.now, min_age_days=3, force=True,
            proc_dir="/proc", live=True, dry_run=True, log_path=self.d / "log", state_path=st)
        self.assertEqual(s["removed"], 0)
        self.assertTrue(p.exists(), "dry_run must never delete")
        self.assertFalse(st.exists(), "dry_run must not advance cadence state")


class TestReaperWiring(unittest.TestCase):
    def test_reaper_symbols_reexported(self):
        for name in ("discover_stray_airuleset_state_candidates",
                     "sweep_airuleset_state", "_AIRULESET_STATE_RX",
                     "AIRULESET_STATE_EXCLUDE_PREFIXES"):
            self.assertTrue(hasattr(airuleset, name), name)

    def test_install_step_11b_reaps_live_both_shapes(self):
        """Source-lock: cmd_install's disk-hygiene step must actually REAP
        (live=True) both the tmp* litter (24h floor) and the airuleset-* state
        (3d floor) — the #548 sign-off flips #513's report-only default."""
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("sweep_airuleset_state", src)
        self.assertIn("live=True", src)


if __name__ == "__main__":
    unittest.main()
