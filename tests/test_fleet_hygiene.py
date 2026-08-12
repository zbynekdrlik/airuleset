"""Fleet hygiene — old Claude CLI binaries + aging claude scratch/tmp (#355).

The worktree sweep (#345/#348) is scoped strictly to `.claude/worktrees/`
git worktrees; the Tier-0 target/ purge (#315) is scoped to cargo build
artefacts. Neither one touches `~/.local/share/claude/versions/` (every
native CLI auto-update leaves the OLD versioned binary, ~280-300MB, behind
forever) or `/tmp/claude-<uid>/` (every session's own scratchpad tree,
accumulating unboundedly). This is the SAME sweep shape (discovery
separated from destruction, own log+state file, cadence-gated, real
tempdir fixtures — no filesystem mocks) applied to those two artifact
classes.
"""

import json
import os
import sys
import unittest
import unittest.mock as m
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402

NOW = 1786176246.0          # fixed; never time.time() (repo convention)
DAY = 86400.0


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------

def _mkfakeproc(root, entries):
    """A fake `/proc`-shaped tree, identical shape to test_target_purge.py's
    own helper (this repo has no tests/__init__.py, so the small helper is
    duplicated rather than cross-imported — established convention)."""
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    for e in entries:
        pdir = proc / e["pid"]
        pdir.mkdir()
        if e.get("exe") is not None:
            os.symlink(e["exe"], pdir / "exe")
        if e.get("cwd") is not None:
            os.symlink(e["cwd"], pdir / "cwd")
        fdd = pdir / "fd"
        fdd.mkdir()
        for i, target in enumerate(e.get("fds", [])):
            os.symlink(target, fdd / str(i))
    return proc


def _mk_versions(root, names_and_ages, now=NOW):
    """`<root>/.local/share/claude/versions/<name>` for each (name, age_days)
    pair — a real FILE, mtime stamped `age_days` old (None = fresh, i.e. 0
    days old). Matches the real installer's own layout (#355 STEP 0: flat
    dir of version-named FILES, never a subdir-per-version)."""
    vdir = root / ".local" / "share" / "claude" / "versions"
    vdir.mkdir(parents=True, exist_ok=True)
    for name, age_days in names_and_ages:
        f = vdir / name
        f.write_bytes(b"x" * 4096)
        os.chmod(f, 0o755)   # real installer files are -rwxr-xr-x -- shutil.which needs +x
        mtime = now if age_days is None else now - age_days * DAY
        os.utime(f, (mtime, mtime))
    return vdir


def _mk_current_env(root, vdir, current_name):
    """A fake `~/.local/bin/claude -> <vdir>/<current_name>` symlink + the
    env dict that resolves it via `shutil.which` (`PATH` pointing at the
    fake bin dir) — exactly `_resolve_current_cli_version`'s own real-world
    mechanism, never guessed at."""
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    link = bindir / "claude"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(vdir / current_name)
    return {"PATH": str(bindir)}


# ---------------------------------------------------------------------------
# _cli_version_key / _resolve_current_cli_version
# ---------------------------------------------------------------------------

class TestCliVersionKey(unittest.TestCase):
    def test_parses_dotted_decimal(self):
        self.assertEqual(airuleset._cli_version_key("2.1.226"), (2, 1, 226))
        self.assertEqual(airuleset._cli_version_key("10.0"), (10, 0))

    def test_refuses_non_version_names(self):
        # A REAL Claude CLI version file is always dotted (X.Y.Z) -- a bare
        # integer ("10", no dot at all) is deliberately refused too:
        # fail-safe-skip-on-uncertainty, never assume an unusual shape is
        # "probably" a version.
        for bad in ("latest", "2.1.226-beta", "v2.1.226", "", "2.1.", ".226",
                    "2..226", "2.1.226/", "../2.1.226", "10"):
            self.assertIsNone(airuleset._cli_version_key(bad),
                              "expected None for %r" % bad)


class TestResolveCurrentCliVersion(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-cliver-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_resolves_real_symlink_target(self):
        vdir = _mk_versions(self.root, [("2.1.226", None)])
        env = _mk_current_env(self.root, vdir, "2.1.226")
        current = airuleset._resolve_current_cli_version(vdir, env=env)
        self.assertEqual(current, str((vdir / "2.1.226").resolve()))

    def test_none_when_claude_not_on_path(self):
        vdir = _mk_versions(self.root, [("2.1.226", None)])
        empty_bin = self.root / "emptybin"
        empty_bin.mkdir()
        current = airuleset._resolve_current_cli_version(
            vdir, env={"PATH": str(empty_bin)})
        self.assertIsNone(current)

    def test_none_when_resolved_binary_is_outside_versions_dir(self):
        """An unexpected install method (a system package, a different
        layout) — never guessed at, refused."""
        vdir = _mk_versions(self.root, [("2.1.226", None)])
        outside_bin = self.root / "outside" / "bin"
        outside_bin.mkdir(parents=True)
        real_bin = outside_bin / "claude"
        real_bin.write_bytes(b"#!/bin/sh\n")
        os.chmod(real_bin, 0o755)
        current = airuleset._resolve_current_cli_version(
            vdir, env={"PATH": str(outside_bin)})
        self.assertIsNone(current)


# ---------------------------------------------------------------------------
# discover_cli_version_candidates
# ---------------------------------------------------------------------------

class TestDiscoverCliVersionCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-cliver-discover-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _by_name(self, found, version):
        for r in found:
            if r.get("version") == version:
                return r
        self.fail("no row for version %r in %r" % (version, found))

    def test_keeps_current_and_previous_only(self):
        vdir = _mk_versions(self.root, [
            ("2.1.223", 10), ("2.1.224", 8), ("2.1.225", 5), ("2.1.226", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.226")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        cur = self._by_name(found, "2.1.226")
        prev = self._by_name(found, "2.1.225")
        old1 = self._by_name(found, "2.1.224")
        old2 = self._by_name(found, "2.1.223")
        self.assertIsNotNone(cur["reason"])
        self.assertIn("current", cur["reason"])
        self.assertIsNotNone(prev["reason"])
        self.assertIn("rollback", prev["reason"])
        self.assertIsNone(old1["reason"], "2.1.224 must be a genuine candidate")
        self.assertIsNone(old2["reason"], "2.1.223 must be a genuine candidate")

    def test_current_not_the_newest_keeps_the_real_rollback_not_the_newer_build(self):
        """A manual downgrade -- current (2.1.224) is NOT the newest entry
        present (2.1.225, downloaded but never symlinked). The kept
        "rollback" must be the version BELOW current (2.1.223, what was
        active before the update TO 224) -- the newer, undeployed 2.1.225
        gets no special protection at all and is a genuine candidate."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 30), ("2.1.224", 1), ("2.1.225", 30)])
        env = _mk_current_env(self.root, vdir, "2.1.224")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        cur = self._by_name(found, "2.1.224")
        self.assertIsNotNone(cur["reason"])
        self.assertIn("current", cur["reason"])
        rollback = self._by_name(found, "2.1.223")
        self.assertIsNotNone(rollback["reason"])
        self.assertIn("rollback", rollback["reason"])
        newer_undeployed = self._by_name(found, "2.1.225")
        self.assertIsNone(newer_undeployed["reason"])

    def test_current_is_the_oldest_entry_nothing_extra_is_kept(self):
        """When current is ALREADY the lowest-ranked version present, there
        is no "version below it" -- no extra rollback slot exists, and
        every OTHER (newer, unused) entry is a genuine candidate."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 1), ("2.1.224", 30), ("2.1.225", 30)])
        env = _mk_current_env(self.root, vdir, "2.1.223")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        cur = self._by_name(found, "2.1.223")
        self.assertIsNotNone(cur["reason"])
        self.assertIn("current", cur["reason"])
        self.assertIsNone(self._by_name(found, "2.1.224")["reason"])
        self.assertIsNone(self._by_name(found, "2.1.225")["reason"])

    def test_no_other_versions_present_is_fine(self):
        vdir = _mk_versions(self.root, [("2.1.226", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.226")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        self.assertEqual(len(found), 1)
        self.assertIn("current", found[0]["reason"])

    def test_unparseable_name_skipped_individually(self):
        vdir = _mk_versions(self.root, [("2.1.226", 0), ("2.1.225", 5)])
        (vdir / "latest").write_bytes(b"x")
        env = _mk_current_env(self.root, vdir, "2.1.226")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        bad = self._by_name(found, "latest")
        self.assertIsNotNone(bad["reason"])
        self.assertIn("does not parse", bad["reason"])

    def test_symlink_entry_skipped_individually_never_followed(self):
        vdir = _mk_versions(self.root, [("2.1.226", 0), ("2.1.225", 5)])
        # A version-shaped NAME that is a symlink -- unexpected layout.
        (vdir / "2.1.999").symlink_to(vdir / "2.1.226")
        env = _mk_current_env(self.root, vdir, "2.1.226")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        bad = self._by_name(found, "2.1.999")
        self.assertIsNotNone(bad["reason"])
        self.assertIn("not a plain regular file", bad["reason"])

    def test_directory_entry_skipped_individually(self):
        vdir = _mk_versions(self.root, [("2.1.226", 0), ("2.1.225", 5)])
        (vdir / "2.1.998").mkdir()
        env = _mk_current_env(self.root, vdir, "2.1.226")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        bad = self._by_name(found, "2.1.998")
        self.assertIsNotNone(bad["reason"])
        self.assertIn("not a plain regular file", bad["reason"])

    def test_versions_dir_missing_returns_empty_not_an_error(self):
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=self.root / "nope", now=NOW)
        self.assertEqual(found, [])

    def test_current_unresolvable_refuses_the_whole_box(self):
        vdir = _mk_versions(self.root, [
            ("2.1.223", 30), ("2.1.224", 20), ("2.1.225", 10)])
        empty_bin = self.root / "emptybin"
        empty_bin.mkdir()
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2,
            env={"PATH": str(empty_bin)})
        errors = [r for r in found if r.get("path") is None]
        self.assertEqual(len(errors), 1)
        self.assertIn("refusing the whole sweep", errors[0]["reason"])
        # Nothing anywhere in the result set may be a genuine (reason=None)
        # candidate when the whole box was refused.
        self.assertTrue(all(r.get("reason") for r in found))

    def test_current_resolves_but_matches_no_discovered_entry_refuses(self):
        """`which`/`realpath` resolve successfully (the file genuinely
        exists, is executable, and lives inside versions_dir) but its own
        NAME is unparseable as a dotted-decimal version -- so it never
        enters the `parsed` list at all, and current_idx can't be found.
        Never guess which entry is "probably" current -- refuse the whole
        sweep instead."""
        vdir = _mk_versions(self.root, [("2.1.225", 10), ("2.1.224", 20)])
        staging = vdir / "staging-build"
        staging.write_bytes(b"x" * 4096)
        os.chmod(staging, 0o755)
        env = _mk_current_env(self.root, vdir, "staging-build")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        errors = [r for r in found if r.get("path") is None]
        self.assertEqual(len(errors), 1)
        self.assertIn("does not match any discovered", errors[0]["reason"])

    def test_too_recent_version_below_previous_is_kept(self):
        """A version ranked below current+previous but genuinely fresh
        (e.g. mid-update-race) must NOT be reclaimed yet -- the age floor
        poistka."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 0.1), ("2.1.224", 5), ("2.1.225", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.225")
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2, env=env)
        fresh = self._by_name(found, "2.1.223")
        self.assertIsNotNone(fresh["reason"])
        self.assertIn("too recent", fresh["reason"])

    def test_min_age_days_env_override_is_actually_read(self):
        """#355 adversarial-review finding 2 (MINOR): the constant's own
        comment advertises AIRULESET_CLI_VERSION_MIN_AGE_DAYS -- it must
        genuinely change the outcome, not be a silently no-op knob."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 1), ("2.1.224", 5), ("2.1.225", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.225")
        with m.patch.dict(os.environ, {"AIRULESET_CLI_VERSION_MIN_AGE_DAYS": "0.5"}):
            found = airuleset.discover_cli_version_candidates(
                home=self.root, versions_dir=vdir, now=NOW, min_age_days=None, env=env)
        row = self._by_name(found, "2.1.223")
        self.assertIsNone(row["reason"], "env override to 0.5d must admit a 1-day-old version")

    def test_explicit_min_age_days_beats_the_env_override(self):
        """#355 round-2 adversarial-review finding F4: an EXPLICIT
        `min_age_days=` call argument must win over the env var, never the
        reverse."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 1), ("2.1.224", 5), ("2.1.225", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.225")
        with m.patch.dict(os.environ, {"AIRULESET_CLI_VERSION_MIN_AGE_DAYS": "0.1"}):
            found = airuleset.discover_cli_version_candidates(
                home=self.root, versions_dir=vdir, now=NOW, min_age_days=7, env=env)
        row = self._by_name(found, "2.1.223")
        self.assertIsNotNone(row["reason"], "explicit min_age_days=7 must NOT be overridden by env=0.1")
        self.assertIn("too recent", row["reason"])

    def test_nan_env_override_is_refused_never_disables_the_age_floor(self):
        """#355 round-2 adversarial-review finding F3: "nan" parses as a
        valid float, and `age_days < nan` is False for every value --
        silently disabling the ENTIRE age floor. Must fall back to the
        default instead."""
        vdir = _mk_versions(self.root, [
            ("2.1.223", 1), ("2.1.224", 5), ("2.1.225", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.225")
        with m.patch.dict(os.environ, {"AIRULESET_CLI_VERSION_MIN_AGE_DAYS": "nan"}):
            found = airuleset.discover_cli_version_candidates(
                home=self.root, versions_dir=vdir, now=NOW, min_age_days=None, env=env)
        row = self._by_name(found, "2.1.223")
        self.assertIsNotNone(row["reason"], "a 'nan' env override must NEVER disable the age floor")
        self.assertIn("too recent", row["reason"])

    def test_live_process_guard_skips_a_running_old_version(self):
        # current=2.1.226, rollback (kept)=2.1.225 -- 2.1.223/2.1.224 are
        # the two genuinely-below-rollback candidates this test targets.
        vdir = _mk_versions(self.root, [
            ("2.1.223", 30), ("2.1.224", 20), ("2.1.225", 5), ("2.1.226", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.226")
        proc = _mkfakeproc(self.root, [
            {"pid": "111", "exe": str((vdir / "2.1.223").resolve())}])
        found = airuleset.discover_cli_version_candidates(
            home=self.root, versions_dir=vdir, now=NOW, min_age_days=2,
            env=env, proc_dir=proc)
        busy = self._by_name(found, "2.1.223")
        self.assertIsNotNone(busy["reason"])
        self.assertIn("in live use", busy["reason"])
        # 2.1.224 (not running) stays a genuine candidate.
        free = self._by_name(found, "2.1.224")
        self.assertIsNone(free["reason"])


# ---------------------------------------------------------------------------
# sweep_stale_cli_versions
# ---------------------------------------------------------------------------

class TestSweepStaleCliVersions(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-cliver-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log_path = self.root / "log.log"
        self.state_path = self.root / "state.json"

    def _setup_layout(self):
        vdir = _mk_versions(self.root, [
            ("2.1.223", 30), ("2.1.224", 20), ("2.1.225", 5), ("2.1.226", 0)])
        env = _mk_current_env(self.root, vdir, "2.1.226")
        return vdir, env

    def test_dry_run_deletes_nothing_but_reports(self):
        vdir, env = self._setup_layout()
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertTrue((vdir / "2.1.223").exists())
        self.assertTrue((vdir / "2.1.224").exists())
        would = [r for r in results if str(r.get("reason", "")).startswith("would remove")]
        self.assertEqual({r["version"] for r in would}, {"2.1.223", "2.1.224"})

    def test_force_actually_deletes_stale_only(self):
        vdir, env = self._setup_layout()
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertFalse((vdir / "2.1.223").exists())
        self.assertFalse((vdir / "2.1.224").exists())
        self.assertTrue((vdir / "2.1.225").exists(), "rollback version must survive")
        self.assertTrue((vdir / "2.1.226").exists(), "current version must survive")
        removed = [r for r in results if r.get("removed")]
        self.assertEqual({r["version"] for r in removed}, {"2.1.223", "2.1.224"})
        self.assertTrue(self.log_path.exists())
        self.assertTrue(self.state_path.exists())

    def test_current_never_deleted_even_under_force(self):
        """Redundant, explicit assertion of the ONE non-negotiable
        invariant -- even if every other guard had a bug, this must hold."""
        vdir, env = self._setup_layout()
        airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertTrue((vdir / "2.1.226").exists())

    def test_cadence_gate_skips_without_force(self):
        vdir, env = self._setup_layout()
        self.state_path.write_text(json.dumps({"last_run": NOW - 3600}))
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=False, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertEqual(results, [])
        self.assertTrue((vdir / "2.1.223").exists())

    def test_cadence_gate_allows_after_interval(self):
        vdir, env = self._setup_layout()
        self.state_path.write_text(json.dumps(
            {"last_run": NOW - airuleset.CLI_VERSION_MIN_INTERVAL_S - 1}))
        airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=False, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertFalse((vdir / "2.1.223").exists())

    def test_dry_run_never_writes_cadence_state(self):
        """#355 adversarial-review finding 3a: a manual --dry-run must
        NEVER consume the next 24h of the automatic install-time cadence
        gate -- mutating the write guard to unconditional stays green
        without this lock."""
        vdir, env = self._setup_layout()
        self.assertFalse(self.state_path.exists())
        airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertFalse(self.state_path.exists(),
                         "dry-run must never write the cadence-gate state file")

    def test_delete_time_toctou_recheck_refuses_a_newly_symlinked_path(self):
        """#355 adversarial-review finding 3b: the delete-time re-check
        must actually run -- inject a candidate whose path was replaced by
        a symlink between discovery and delete."""
        vdir, env = self._setup_layout()
        target = self.root / "elsewhere.bin"
        target.write_bytes(b"y")
        stale_path = vdir / "2.1.223"
        stale_path.unlink()
        stale_path.symlink_to(target)
        candidates = [{"path": str(stale_path), "version": "2.1.223", "reason": None}]
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            candidates=candidates, env=env)
        self.assertFalse(results[0]["removed"])
        self.assertIn("no longer a plain regular file", results[0]["reason"])
        self.assertTrue(stale_path.is_symlink(), "the symlink must survive untouched")

    def test_delete_time_toctou_recheck_refuses_a_now_live_path(self):
        vdir, env = self._setup_layout()
        target_path = vdir / "2.1.223"
        proc = _mkfakeproc(self.root, [
            {"pid": "333", "exe": str(target_path.resolve())}])
        candidates = [{"path": str(target_path), "version": "2.1.223", "reason": None}]
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            candidates=candidates, env=env, proc_dir=proc)
        self.assertFalse(results[0]["removed"])
        self.assertIn("in live use", results[0]["reason"])
        self.assertTrue(target_path.exists())

    def test_future_dated_cadence_stamp_does_not_wedge_the_gate_forever(self):
        """#355 adversarial-review finding 3c."""
        vdir, env = self._setup_layout()
        self.state_path.write_text(json.dumps({"last_run": NOW + 999999}))
        results = airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=False, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        self.assertNotEqual(results, [], "a future-dated stamp must be clamped, not honoured")
        self.assertFalse((vdir / "2.1.223").exists())

    def test_log_lines_written_for_every_row(self):
        vdir, env = self._setup_layout()
        airuleset.sweep_stale_cli_versions(
            home=self.root, versions_dir=vdir, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            min_age_days=2, env=env)
        text = self.log_path.read_text()
        self.assertIn("REMOVED", text)
        self.assertIn("2.1.223", text)
        self.assertIn("2.1.226", text)   # current row is logged too (SKIP)


class TestCliVersionCLICommand(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-cliver-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_dry_run_reports_and_prints_log_path(self):
        vdir = _mk_versions(self.root, [("2.1.223", 30), ("2.1.226", 0)])
        with m.patch.object(airuleset, "sweep_stale_cli_versions") as fake_sweep:
            fake_sweep.return_value = [
                {"path": str(vdir / "2.1.223"), "version": "2.1.223",
                 "reason": "would remove (dry-run)", "size": 4096, "removed": False},
                {"path": str(vdir / "2.1.226"), "version": "2.1.226",
                 "reason": "current version -- never deleted", "removed": False},
            ]
            args = SimpleNamespace(dry_run=True, min_age_days=None)
            buf = StringIO()
            with m.patch("sys.stdout", buf):
                airuleset.cmd_sweep_cli_versions(args)
            out = buf.getvalue()
        self.assertIn("WOULD REMOVE", out)
        self.assertIn("2.1.223", out)
        self.assertIn("skip", out)
        self.assertIn(str(airuleset.CLI_VERSION_LOG_PATH), out)
        fake_sweep.assert_called_once()
        self.assertTrue(fake_sweep.call_args.kwargs.get("force"))


# ---------------------------------------------------------------------------
# _claude_scratch_root / discover_claude_scratch_candidates
# ---------------------------------------------------------------------------

class TestClaudeScratchRoot(unittest.TestCase):
    def test_builds_uid_named_path(self):
        root = airuleset._claude_scratch_root(tmp_dir="/tmp", uid=1234)
        self.assertEqual(str(root), "/tmp/claude-1234")


class TestDiscoverClaudeScratchCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-scratch-discover-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.uid = os.getuid()

    def _mk_root(self, uid=None):
        r = self.root / ("claude-%d" % (uid if uid is not None else self.uid))
        r.mkdir(parents=True, exist_ok=True)
        return r

    def _mkfile(self, path, age_days, now=NOW):
        path.write_bytes(b"x" * 2048)
        mtime = now - age_days * DAY
        os.utime(path, (mtime, mtime))

    def test_finds_old_loose_file_and_old_session_dir(self):
        r = self._mk_root()
        self._mkfile(r / "copy.txt", age_days=10)
        sess = r / "-home-newlevel-devel-airuleset" / "abc123" / "scratchpad"
        sess.mkdir(parents=True)
        self._mkfile(sess / "notes.md", age_days=9)
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        names = {Path(r_["path"]).name: r_ for r_ in found}
        self.assertIsNone(names["copy.txt"]["reason"])
        self.assertIsNone(names["-home-newlevel-devel-airuleset"]["reason"])

    def test_young_entries_kept_not_swept(self):
        r = self._mk_root()
        self._mkfile(r / "fresh.txt", age_days=1)
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = found[0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("too recent", row["reason"])

    def test_freshly_created_empty_session_dir_is_never_reclaimable(self):
        """#355 adversarial-review finding 1 (MAJOR, live-confirmed on
        dev1): the harness pre-creates <cwd>/<session>/scratchpad EMPTY at
        session start, before a live session has written anything. An
        empty tree must NEVER read as "infinitely stale" (unlike #315's
        target/ purge, where an empty target/ genuinely has zero bytes to
        protect) -- it must fall back to the directory's OWN mtime, so a
        tree created SECONDS ago stays protected by the age floor exactly
        like a non-empty one would."""
        r = self._mk_root()
        empty_tree = r / "some-cwd" / "sessionid" / "scratchpad"
        empty_tree.mkdir(parents=True)
        # Stamp every level FRESH (age 0) -- a session that just started.
        for d in (r / "some-cwd", r / "some-cwd" / "sessionid", empty_tree):
            os.utime(d, (NOW, NOW))
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "some-cwd"][0]
        self.assertIsNotNone(row["reason"], "an empty, seconds-old tree must NOT be a genuine candidate")
        self.assertIn("too recent", row["reason"])

    def test_old_empty_tree_still_reclaimable(self):
        """The fix must not make an empty tree PERMANENTLY unreclaimable --
        an old, empty, abandoned tree (its own dir mtime old) is still a
        genuine candidate."""
        r = self._mk_root()
        empty_tree = r / "old-cwd" / "sessionid" / "scratchpad"
        empty_tree.mkdir(parents=True)
        old_stamp = NOW - 30 * DAY
        for d in (r / "old-cwd", r / "old-cwd" / "sessionid", empty_tree):
            os.utime(d, (old_stamp, old_stamp))
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "old-cwd"][0]
        self.assertIsNone(row["reason"])

    def test_min_age_days_env_override_is_actually_read(self):
        """#355 adversarial-review finding 2 (MINOR): the constant's own
        comment advertises AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS -- it must
        genuinely change the outcome, not be a silently no-op knob."""
        r = self._mk_root()
        self._mkfile(r / "f.txt", age_days=3)
        with m.patch.dict(os.environ, {"AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS": "1"}):
            found = airuleset.discover_claude_scratch_candidates(
                tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=None)
        row = found[0]
        self.assertIsNone(row["reason"], "env override to 1 day must admit a 3-day-old file")

    def test_explicit_min_age_days_beats_the_env_override(self):
        """#355 round-2 adversarial-review finding F4."""
        r = self._mk_root()
        self._mkfile(r / "f.txt", age_days=3)
        with m.patch.dict(os.environ, {"AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS": "0.1"}):
            found = airuleset.discover_claude_scratch_candidates(
                tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = found[0]
        self.assertIsNotNone(row["reason"], "explicit min_age_days=7 must NOT be overridden by env=0.1")
        self.assertIn("too recent", row["reason"])

    def test_nan_env_override_is_refused_never_disables_the_age_floor(self):
        """#355 round-2 adversarial-review finding F3."""
        r = self._mk_root()
        self._mkfile(r / "f.txt", age_days=3)
        with m.patch.dict(os.environ, {"AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS": "nan"}):
            found = airuleset.discover_claude_scratch_candidates(
                tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=None)
        row = found[0]
        self.assertIsNotNone(row["reason"], "a 'nan' env override must NEVER disable the age floor")
        self.assertIn("too recent", row["reason"])

    def test_newest_file_anywhere_inside_protects_a_live_session_dir(self):
        """A dir whose TOP entry mtime looks old but has a FRESH file deep
        inside must never be judged stale -- the 'never scratch of a live
        session' mtime poistka."""
        r = self._mk_root()
        d = r / "some-cwd" / "sessionid" / "scratchpad"
        d.mkdir(parents=True)
        old_file = d / "old.txt"
        self._mkfile(old_file, age_days=30)
        fresh_file = d / "still-being-written.txt"
        self._mkfile(fresh_file, age_days=0)
        # Backdate the DIRECTORY's own mtime so a naive "check the top
        # entry's own mtime" implementation would misjudge it stale.
        old_stamp = NOW - 30 * DAY
        os.utime(r / "some-cwd", (old_stamp, old_stamp))
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "some-cwd"][0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("too recent", row["reason"])

    def test_symlink_entry_never_followed_never_deleted_through(self):
        r = self._mk_root()
        target = self.root / "elsewhere"
        target.mkdir()
        self._mkfile(target / "f.txt", age_days=30)
        (r / "linked").symlink_to(target)
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "linked"][0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("symlink", row["reason"])

    def test_live_process_guard_skips_old_but_in_use_dir(self):
        r = self._mk_root()
        d = r / "in-use-dir"
        d.mkdir()
        self._mkfile(d / "f.txt", age_days=30)
        proc = _mkfakeproc(self.root, [
            {"pid": "222", "cwd": str(d.resolve())}])
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7, proc_dir=proc)
        row = [r_ for r_ in found if Path(r_["path"]).name == "in-use-dir"][0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("in live use", row["reason"])

    def test_root_missing_returns_empty(self):
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=999999, now=NOW)
        self.assertEqual(found, [])

    def test_wrong_owner_never_touched_even_if_name_matches(self):
        """The double-check: NAME matching `claude-<uid>` alone is not
        enough -- st_uid must ALSO match. Simulated by asking for a uid
        the real directory does NOT actually belong to."""
        r = self._mk_root(uid=42424242)   # claude-42424242, owned by ME
        self._mkfile(r / "f.txt", age_days=30)
        # Real ownership is os.getuid(), never 42424242 -- so passing THAT
        # uid must find nothing, even though the NAME matches perfectly.
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=42424242, now=NOW, min_age_days=7)
        self.assertEqual(found, [])

    def test_a_differently_named_sibling_directory_is_never_even_listed(self):
        """Live on the real box: /tmp/claude-286 sits right next to
        /tmp/claude-1000, SAME owner, unrelated number -- proves this
        sweep must resolve its OWN root directly by uid, never scan
        siblings."""
        mine = self._mk_root(uid=self.uid)
        self._mkfile(mine / "mine.txt", age_days=30)
        foreign_name_root = self.root / "claude-999999"
        foreign_name_root.mkdir()
        self._mkfile(foreign_name_root / "not-mine.txt", age_days=30)
        found = airuleset.discover_claude_scratch_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        paths = [r_["path"] for r_ in found]
        self.assertTrue(all("claude-999999" not in p for p in paths))
        self.assertTrue(any("mine.txt" in p for p in paths))


# ---------------------------------------------------------------------------
# discover_stray_worktree_tmp_candidates (#380)
# ---------------------------------------------------------------------------

class TestDiscoverStrayWorktreeTmpCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-wttmp-discover-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.uid = os.getuid()

    def _mkfile(self, path, age_days, now=NOW):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 2048)
        mtime = now - age_days * DAY
        os.utime(path, (mtime, mtime))

    def test_finds_old_top_level_wt_entry(self):
        self._mkfile(self.root / "wt-abc123", age_days=30)
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "wt-abc123"][0]
        self.assertIsNone(row["reason"])

    def test_young_entry_kept_not_swept(self):
        self._mkfile(self.root / "wt-fresh", age_days=1)
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = found[0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("too recent", row["reason"])

    def test_symlink_entry_never_followed_never_deleted_through(self):
        target = self.root / "elsewhere"
        target.mkdir()
        self._mkfile(target / "f.txt", age_days=30)
        (self.root / "wt-linked").symlink_to(target)
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        row = [r_ for r_ in found if Path(r_["path"]).name == "wt-linked"][0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("symlink", row["reason"])

    def test_live_process_guard_skips_old_but_in_use_dir(self):
        d = self.root / "wt-inuse"
        self._mkfile(d / "f.txt", age_days=30)
        proc = _mkfakeproc(self.root, [
            {"pid": "333", "cwd": str(d.resolve())}])
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7, proc_dir=proc)
        row = [r_ for r_ in found if Path(r_["path"]).name == "wt-inuse"][0]
        self.assertIsNotNone(row["reason"])
        self.assertIn("in live use", row["reason"])

    def test_a_non_wt_prefixed_sibling_is_never_even_listed(self):
        """`/tmp/claude-<uid>` (#355's own scope) sits right next to a
        real `wt-*` entry -- this sweep must never touch anything outside
        its own `wt-*` glob."""
        self._mkfile(self.root / "wt-real", age_days=30)
        self._mkfile(self.root / ("claude-%d" % self.uid) / "unrelated.txt", age_days=30)
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=7)
        names = [Path(r_["path"]).name for r_ in found]
        self.assertEqual(names, ["wt-real"])

    def test_foreign_owned_wt_entry_is_never_even_classified(self):
        """A `wt-*` entry belonging to a DIFFERENT uid must never be
        listed, let alone touched -- these boxes host foreign uids by
        design on a shared /tmp."""
        p = self.root / "wt-foreign"
        self._mkfile(p, age_days=30)
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root, uid=self.uid + 987654, now=NOW, min_age_days=7)
        self.assertEqual(found, [])

    def test_root_missing_returns_empty(self):
        found = airuleset.discover_stray_worktree_tmp_candidates(
            tmp_dir=self.root / "does-not-exist", uid=self.uid, now=NOW)
        self.assertEqual(found, [])

    def test_min_age_days_env_override_is_actually_read(self):
        self._mkfile(self.root / "wt-x", age_days=3)
        with m.patch.dict(os.environ, {"AIRULESET_CLAUDE_SCRATCH_MIN_AGE_DAYS": "1"}):
            found = airuleset.discover_stray_worktree_tmp_candidates(
                tmp_dir=self.root, uid=self.uid, now=NOW, min_age_days=None)
        row = found[0]
        self.assertIsNone(row["reason"], "env override to 1 day must admit a 3-day-old file")


# ---------------------------------------------------------------------------
# sweep_claude_scratch
# ---------------------------------------------------------------------------

class TestSweepClaudeScratch(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-scratch-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.uid = os.getuid()
        self.log_path = self.root / "log.log"
        self.state_path = self.root / "state.json"

    def _mkfile(self, path, age_days, now=NOW):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 2048)
        mtime = now - age_days * DAY
        os.utime(path, (mtime, mtime))

    def _setup(self):
        r = self.root / ("claude-%d" % self.uid)
        r.mkdir(parents=True)
        self._mkfile(r / "old.txt", age_days=30)
        d = r / "old-cwd" / "sessid" / "scratchpad"
        self._mkfile(d / "old-notes.md", age_days=30)
        self._mkfile(r / "fresh.txt", age_days=1)
        return r

    def test_dry_run_deletes_nothing(self):
        r = self._setup()
        airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertTrue((r / "old.txt").exists())
        self.assertTrue((r / "old-cwd").exists())

    def test_force_deletes_stale_leaves_fresh(self):
        r = self._setup()
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertFalse((r / "old.txt").exists())
        self.assertFalse((r / "old-cwd").exists())
        self.assertTrue((r / "fresh.txt").exists())
        removed = [x for x in results if x.get("removed")]
        self.assertEqual(len(removed), 2)
        self.assertTrue(self.log_path.exists())
        self.assertTrue(self.state_path.exists())

    def test_cadence_gate_skips_without_force(self):
        r = self._setup()
        self.state_path.write_text(json.dumps({"last_run": NOW - 3600}))
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=False, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertEqual(results, [])
        self.assertTrue((r / "old.txt").exists())

    def test_never_touches_a_differently_named_sibling(self):
        self._setup()
        foreign = self.root / "claude-999999"
        foreign.mkdir()
        self._mkfile(foreign / "not-mine.txt", age_days=30)
        airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertTrue((foreign / "not-mine.txt").exists())

    def test_a_real_sweep_also_reclaims_stray_top_level_wt_entries(self):
        """#380 -- when `candidates` is not injected, `sweep_claude_scratch`
        must combine BOTH #355's own `/tmp/claude-<uid>/*` discovery AND
        the new top-level `/tmp/wt-*` discovery into one sweep -- the same
        cadence gate, same log, same state file, no new mechanism."""
        r = self._setup()
        wt = self.root / "wt-stray"
        self._mkfile(wt, age_days=30)
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertFalse(wt.exists(), "the stray wt-* entry must also be reclaimed")
        self.assertFalse((r / "old.txt").exists())
        removed_names = {Path(x["path"]).name for x in results if x.get("removed")}
        self.assertIn("wt-stray", removed_names)
        self.assertIn("old.txt", removed_names)

    def test_dry_run_never_writes_cadence_state(self):
        """#355 adversarial-review finding 3a."""
        self._setup()
        self.assertFalse(self.state_path.exists())
        airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertFalse(self.state_path.exists(),
                         "dry-run must never write the cadence-gate state file")

    def test_delete_time_toctou_recheck_refuses_a_newly_symlinked_path(self):
        """#355 adversarial-review finding 3b."""
        r = self._setup()
        target = self.root / "elsewhere-dir"
        target.mkdir()
        stale_path = r / "old.txt"
        stale_path.unlink()
        stale_path.symlink_to(target)
        candidates = [{"path": str(stale_path), "reason": None}]
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            candidates=candidates)
        self.assertFalse(results[0]["removed"])
        self.assertIn("symlink", results[0]["reason"])
        self.assertTrue(stale_path.is_symlink())

    def test_delete_time_toctou_recheck_refuses_a_now_live_path(self):
        """#355 adversarial-review finding 3b."""
        r = self._setup()
        old_dir = r / "old-cwd"
        proc = _mkfakeproc(self.root, [
            {"pid": "444", "cwd": str(old_dir.resolve())}])
        candidates = [{"path": str(old_dir), "reason": None}]
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            candidates=candidates, proc_dir=proc)
        self.assertFalse(results[0]["removed"])
        self.assertIn("in live use", results[0]["reason"])
        self.assertTrue(old_dir.exists())

    def test_future_dated_cadence_stamp_does_not_wedge_the_gate_forever(self):
        """#355 adversarial-review finding 3c."""
        r = self._setup()
        self.state_path.write_text(json.dumps({"last_run": NOW + 999999}))
        results = airuleset.sweep_claude_scratch(
            tmp_dir=self.root, uid=self.uid, dry_run=False, force=False, now=NOW,
            log_path=self.log_path, state_path=self.state_path, min_age_days=7)
        self.assertNotEqual(results, [], "a future-dated stamp must be clamped, not honoured")
        self.assertFalse((r / "old.txt").exists())


class TestClaudeScratchCLICommand(unittest.TestCase):
    def test_dry_run_reports_and_prints_log_path(self):
        with m.patch.object(airuleset, "sweep_claude_scratch") as fake_sweep:
            fake_sweep.return_value = [
                {"path": "/tmp/claude-1000/old.txt",
                 "reason": "would remove (dry-run)", "size": 2048, "removed": False},
                {"path": "/tmp/claude-1000/fresh.txt",
                 "reason": "too recent (1.0d < 7d)", "removed": False},
            ]
            args = SimpleNamespace(dry_run=True, min_age_days=None)
            buf = StringIO()
            with m.patch("sys.stdout", buf):
                airuleset.cmd_sweep_claude_scratch(args)
            out = buf.getvalue()
        self.assertIn("WOULD REMOVE", out)
        self.assertIn("old.txt", out)
        self.assertIn("skip", out)
        self.assertIn(str(airuleset.CLAUDE_SCRATCH_LOG_PATH), out)
        fake_sweep.assert_called_once()
        self.assertTrue(fake_sweep.call_args.kwargs.get("force"))


# ---------------------------------------------------------------------------
# CLI subcommand registration
# ---------------------------------------------------------------------------

class TestSubcommandRegistration(unittest.TestCase):
    def test_both_new_subcommands_registered(self):
        self.assertIs(airuleset.SUBCOMMANDS["sweep-cli-versions"],
                      airuleset.cmd_sweep_cli_versions)
        self.assertIs(airuleset.SUBCOMMANDS["sweep-claude-scratch"],
                      airuleset.cmd_sweep_claude_scratch)


if __name__ == "__main__":
    unittest.main()
