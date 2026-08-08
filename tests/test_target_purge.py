"""Tier-0 target/ retention — auto-purge stale build artefacts (#315).

Tier 0 (`no-local-builds.md`'s DEFAULT) bans HEAVY local builds but still
legitimately fills `target/` via the cheap checks it DOES allow (`cargo
check`/`clippy`/`test --no-run`) — and nothing ever purged it. The
`local-builds` skill's own purge rule is on-demand prose with no caller
anywhere in this repo, so growth was monotonic: songplayer 10.1G, spinbike
8.3G, camera-box 4.4G, ~23GB of dead weight on dev1 alone, "znova a znova"
(user, 2026-08-08).

This ONLY adds RETENTION (auto-purge of STALE Tier-0 target/ dirs) — it
does not clean up the current ~23GB (the user's own separate cleanup).

Tier resolution deliberately shells out to the REAL, shipped
`hooks/block-tier0-local-build.sh` for every test here (never mocked) —
#315's own design requirement is a SINGLE source of truth for tier
detection; a mock would silently let the Python side and the hook drift
apart with nothing to catch it (per test-strictness.md: no mocking real
internal code).
"""

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
# Fixture builders
# ---------------------------------------------------------------------------

def _mkrepo(root, name="proj", claude_md="# no marker\n", cargo=True, git=True):
    """A minimal repo: `.git` (dir — enough for `_checkout_roots`'s own
    check), optionally a root `Cargo.toml`, optionally a `CLAUDE.md`.
    `claude_md=None` omits the file entirely (the "unmanaged" case)."""
    r = root / name
    r.mkdir(parents=True, exist_ok=True)
    if git:
        (r / ".git").mkdir()
    if cargo:
        (r / "Cargo.toml").write_text('[package]\nname = "x"\n')
    if claude_md is not None:
        (r / "CLAUDE.md").write_text(claude_md)
    return r


def _mktarget(repo, rel="target", age_days=None, now=NOW, n_files=1, size=4096):
    """`<repo>/<rel>/` with `n_files` inside, mtime `age_days` old (a fresh
    file — `age_days=None` — is stamped at `now` itself, i.e. 0 days old)."""
    t = repo / rel
    t.mkdir(parents=True, exist_ok=True)
    mtime = now if age_days is None else now - age_days * DAY
    for i in range(n_files):
        f = t / ("f%d.bin" % i)
        f.write_bytes(b"x" * size)
        os.utime(f, (mtime, mtime))
    return t


def _mkfakeproc(root, entries):
    """A fake `/proc`-shaped tree: `entries` is a list of dicts, each
    `{"pid": "1234", "exe": <target str or None>, "cwd": <target str or
    None>, "fds": [<target str>, ...]}`. Symlinks are DANGLING on purpose —
    `os.readlink` never needs the target to actually exist."""
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


# ---------------------------------------------------------------------------
# discover_target_purge_candidates
# ---------------------------------------------------------------------------

class TestDiscoverTargetPurgeCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-target-purge-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_finds_workspace_root_target(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo)
        found = airuleset.discover_target_purge_candidates(home=self.root)
        self.assertEqual([(t.resolve(), r.resolve()) for t, r in found],
                          [((repo / "target").resolve(), repo.resolve())])

    def test_finds_member_crate_target_too(self):
        """A cargo workspace member with its OWN independent target/ (the
        ticket's own example: sp-ui/target) — both must be discovered."""
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, "target")
        member = repo / "sp-ui"
        member.mkdir()
        (member / "Cargo.toml").write_text('[package]\nname = "sp-ui"\n')
        _mktarget(member, "target")
        found = airuleset.discover_target_purge_candidates(home=self.root)
        targets = sorted(str(t) for t, _ in found)
        self.assertEqual(targets, sorted([str(repo / "target"),
                                          str(member / "target")]))

    def test_ignores_target_with_no_sibling_cargo_toml(self):
        """A `target`-named dir that is NOT a cargo build artefact (no
        Cargo.toml at that level) is never even discovered as a candidate —
        #315 requirement 4: purge ONLY inside git repos WITH a Cargo.toml."""
        repo = _mkrepo(self.root, "notrust", cargo=False)
        _mktarget(repo)
        found = airuleset.discover_target_purge_candidates(home=self.root)
        self.assertEqual(found, [])

    def test_ignores_non_git_directory(self):
        """A Cargo.toml + target/ pair with NO `.git` anywhere above it is
        never discovered — must be inside a real checkout."""
        plain = self.root / "scratch"
        plain.mkdir()
        (plain / "Cargo.toml").write_text('[package]\nname = "x"\n')
        _mktarget(plain)
        found = airuleset.discover_target_purge_candidates(home=self.root)
        self.assertEqual(found, [])

    def test_never_descends_into_target_for_discovery(self):
        """No nested-target scanning — a huge target/ tree is not walked
        looking for MORE targets/Cargo.toml pairs inside it."""
        repo = _mkrepo(self.root, "proj")
        t = _mktarget(repo)
        # A decoy Cargo.toml + target/ pair planted INSIDE the real target/
        # tree must never be surfaced as its own candidate.
        decoy = t / "deps" / "vendor"
        decoy.mkdir(parents=True)
        (decoy / "Cargo.toml").write_text("bogus\n")
        (decoy / "target").mkdir()
        found = airuleset.discover_target_purge_candidates(home=self.root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0].resolve(), (repo / "target").resolve())


# ---------------------------------------------------------------------------
# _tier0_via_hook — reuses the REAL, shipped hook (never mocked)
# ---------------------------------------------------------------------------

class TestTier0ViaHook(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-tier-hook-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_no_marker_is_tier0(self):
        repo = _mkrepo(self.root, "proj", claude_md="# nothing special\n")
        self.assertTrue(airuleset._tier0_via_hook(str(repo)))

    def test_allowed_marker_is_not_tier0(self):
        repo = _mkrepo(self.root, "proj",
                       claude_md="<!-- airuleset:local-builds=allowed -->\n")
        self.assertFalse(airuleset._tier0_via_hook(str(repo)))

    def test_fast_iterate_marker_is_not_tier0(self):
        repo = _mkrepo(self.root, "proj",
                       claude_md="<!-- airuleset:local-builds=fast-iterate -->\n")
        self.assertFalse(airuleset._tier0_via_hook(str(repo)))

    def test_unmanaged_no_claude_md_is_not_tier0(self):
        """No CLAUDE.md reachable at all -> the hook's own "not enforced"
        branch (exit 0) -> never treated as a Tier-0 purge target."""
        repo = _mkrepo(self.root, "proj", claude_md=None)
        self.assertFalse(airuleset._tier0_via_hook(str(repo)))

    def test_missing_hook_file_never_claims_tier0(self):
        """A hook_path that doesn't exist must never silently claim Tier 0
        (which would purge with NO real gate behind it) — fails toward
        'not tier 0', i.e. toward never touching anything."""
        repo = _mkrepo(self.root, "proj", claude_md="# nothing\n")
        self.assertFalse(airuleset._tier0_via_hook(
            str(repo), hook_path=self.root / "does-not-exist.sh"))


# ---------------------------------------------------------------------------
# _target_in_live_use — the mechanical hot-swap/event guard
# ---------------------------------------------------------------------------

class TestTargetInLiveUse(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-live-use-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.target = self.root / "repo" / "target"
        self.target.mkdir(parents=True)

    def test_no_process_using_it_is_not_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "111", "exe": "/usr/bin/bash", "fds": ["/dev/null"]},
        ])
        self.assertFalse(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_exe_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "222", "exe": str(self.target / "release" / "app")},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_open_fd_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "333", "exe": "/usr/bin/bash",
             "fds": [str(self.target / "release" / "app.log")]},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_process_cwd_inside_target_is_in_use(self):
        proc = _mkfakeproc(self.root, [
            {"pid": "444", "exe": "/usr/bin/bash", "cwd": str(self.target / "debug")},
        ])
        self.assertTrue(airuleset._target_in_live_use(self.target, proc_dir=proc))

    def test_undeterminable_no_proc_dir_defaults_to_in_use(self):
        """No /proc-shaped dir at all -> can't determine -> SKIP (never
        purge on an unmeasurable answer) — approval-scope.md's mechanical
        substitute for asking the user "is there an event right now"."""
        self.assertTrue(airuleset._target_in_live_use(
            self.target, proc_dir=self.root / "no-such-proc"))

    def test_unrelated_target_dir_paths_are_not_confused(self):
        """A process using a DIFFERENT, similarly-named target dir (e.g.
        target-old/) must not false-positive via a bare string prefix
        match on the un-separated path."""
        sibling = self.root / "repo" / "target-old"
        sibling.mkdir()
        proc = _mkfakeproc(self.root, [
            {"pid": "555", "exe": str(sibling / "release" / "app")},
        ])
        self.assertFalse(airuleset._target_in_live_use(self.target, proc_dir=proc))


# ---------------------------------------------------------------------------
# purge_stale_tier0_targets — the full integration
# ---------------------------------------------------------------------------

class TestPurgeStaleTier0Targets(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-purge-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log_path = self.root / "logs" / "target-purge.log"
        self.state_path = self.root / "state" / "target-purge-state.json"
        self.empty_proc = self.root / "empty-proc"
        self.empty_proc.mkdir()

    def _purge(self, **kw):
        kw.setdefault("home", self.root)
        kw.setdefault("now", NOW)
        kw.setdefault("log_path", self.log_path)
        kw.setdefault("state_path", self.state_path)
        kw.setdefault("proc_dir", self.empty_proc)   # nothing "in use" by default
        kw.setdefault("force", True)                 # bypass cadence unless testing it
        return airuleset.purge_stale_tier0_targets(**kw)

    # --- stale ⇒ purge ------------------------------------------------
    def test_stale_tier0_target_is_purged(self):
        repo = _mkrepo(self.root, "songplayer")
        t = _mktarget(repo, age_days=45)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["purged"])
        self.assertFalse(t.exists(), "stale target/ must actually be deleted")

    def test_empty_target_dir_is_treated_as_infinitely_stale(self):
        repo = _mkrepo(self.root, "proj")
        t = repo / "target"
        t.mkdir()          # genuinely empty, no files at all
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertTrue(results[0]["purged"])
        self.assertFalse(t.exists())

    # --- fresh ⇒ keep ---------------------------------------------------
    def test_fresh_target_is_kept(self):
        repo = _mkrepo(self.root, "songplayer")
        t = _mktarget(repo, age_days=1)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["purged"])
        self.assertTrue(t.exists(), "fresh target/ must be left alone")

    def test_age_threshold_is_configurable(self):
        repo = _mkrepo(self.root, "songplayer")
        t = _mktarget(repo, age_days=2)
        results = self._purge(max_age_days=1, dry_run=False)
        self.assertTrue(results[0]["purged"])
        self.assertFalse(t.exists())

    # --- tier != 0 ⇒ keep ------------------------------------------------
    def test_tier1_allowed_marker_is_never_touched(self):
        repo = _mkrepo(self.root, "gpu-project",
                       claude_md="<!-- airuleset:local-builds=allowed -->\n")
        t = _mktarget(repo, age_days=999)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertFalse(results[0]["purged"])
        self.assertTrue(t.exists(), "Tier 1 (=allowed) must NEVER be purged")

    def test_tier2_fast_iterate_marker_is_never_touched(self):
        repo = _mkrepo(self.root, "restreamer",
                       claude_md="<!-- airuleset:local-builds=fast-iterate -->\n")
        t = _mktarget(repo, age_days=999)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertFalse(results[0]["purged"])
        self.assertTrue(t.exists(), "Tier 2 (=fast-iterate) must NEVER be purged")

    def test_unmanaged_repo_with_no_claude_md_is_never_touched(self):
        repo = _mkrepo(self.root, "scratch-experiment", claude_md=None)
        t = _mktarget(repo, age_days=999)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertFalse(results[0]["purged"])
        self.assertTrue(t.exists())

    # --- hotswap marker/process ⇒ skip -----------------------------------
    def test_live_process_using_target_is_skipped(self):
        repo = _mkrepo(self.root, "camera-box")
        t = _mktarget(repo, age_days=999)
        proc = _mkfakeproc(self.root, [
            {"pid": "1234", "exe": str(t / "release" / "camera-box")},
        ])
        results = self._purge(max_age_days=7, dry_run=False, proc_dir=proc)
        self.assertFalse(results[0]["purged"])
        self.assertIn("live use", results[0]["reason"])
        self.assertTrue(t.exists(), "a live hot-swap process must block the purge")

    def test_undeterminable_live_use_is_skipped_not_purged(self):
        """The mechanical substitute for 'never ask the user about an
        event' — an undeterminable answer fails toward SKIP."""
        repo = _mkrepo(self.root, "camera-box")
        t = _mktarget(repo, age_days=999)
        results = self._purge(max_age_days=7, dry_run=False,
                              proc_dir=self.root / "no-such-proc-dir")
        self.assertFalse(results[0]["purged"])
        self.assertTrue(t.exists())

    # --- non-cargo repo ⇒ skip -------------------------------------------
    def test_non_cargo_repo_is_never_scanned(self):
        repo = _mkrepo(self.root, "js-only", cargo=False)
        t = _mktarget(repo, age_days=999)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertEqual(results, [])
        self.assertTrue(t.exists())

    # --- dry-run never deletes --------------------------------------------
    def test_dry_run_reports_but_never_deletes(self):
        repo = _mkrepo(self.root, "songplayer")
        t = _mktarget(repo, age_days=45)
        results = self._purge(max_age_days=7, dry_run=True)
        self.assertTrue(results[0]["purged"])
        self.assertTrue(t.exists(), "dry-run must never actually delete")

    # --- symlink safety -----------------------------------------------
    #
    # Both tests below assert on the REASON string, not just "not purged" --
    # `shutil.rmtree` itself refuses to operate on a path that is a symlink
    # (raises "Cannot call rmtree on a symbolic link"), so a mutant that
    # REMOVES the explicit `target_dir.is_symlink()` guard would still leave
    # the files on disk (rmtree's own exception is caught by the per-
    # candidate try/except and reported as `reason: "error: ..."`) --
    # discovered live via mutation testing: a first-draft version of these
    # tests asserted only `stale_file.exists()` and passed unchanged even
    # with the guard deleted, for the WRONG reason. Asserting the exact
    # "symlink target/ -- never followed" reason is what actually proves
    # the explicit, intentional guard fired (fast, before wasting a
    # tier-hook subprocess call and a directory walk) rather than merely
    # riding rmtree's own incidental protection.
    def test_symlinked_target_dir_is_never_followed_or_deleted(self):
        repo = _mkrepo(self.root, "proj")
        real_elsewhere = self.root / "elsewhere" / "real-target"
        real_elsewhere.mkdir(parents=True)
        stale_file = real_elsewhere / "f.bin"
        stale_file.write_bytes(b"x")
        os.utime(stale_file, (NOW - 999 * DAY, NOW - 999 * DAY))
        (repo / "target").symlink_to(real_elsewhere)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertFalse(results[0]["purged"])
        self.assertIn("symlink target/", results[0]["reason"])
        self.assertTrue(real_elsewhere.exists())
        self.assertTrue(stale_file.exists(), "must never delete THROUGH a symlink")

    def test_symlink_pointing_inside_the_repo_is_also_refused(self):
        """A symlink whose target resolves INSIDE repo_root would pass the
        SEPARATE escape-check (`resolved.relative_to(repo_root)`) cleanly --
        only the explicit `is_symlink()` guard refuses it up front. Without
        that guard, `shutil.rmtree` would still ultimately refuse (see the
        class comment above), so this and the sibling test together prove
        the EXPLICIT guard is what actually fires, not an accident of
        rmtree's own behaviour."""
        repo = _mkrepo(self.root, "proj")
        real_inside = repo / "elsewhere-inside" / "real-target"
        real_inside.mkdir(parents=True)
        stale_file = real_inside / "f.bin"
        stale_file.write_bytes(b"x")
        os.utime(stale_file, (NOW - 999 * DAY, NOW - 999 * DAY))
        (repo / "target").symlink_to(real_inside)
        results = self._purge(max_age_days=7, dry_run=False)
        self.assertFalse(results[0]["purged"])
        self.assertIn("symlink target/", results[0]["reason"])
        self.assertTrue(stale_file.exists(), "must never delete THROUGH a symlink, even one resolving inside the repo")

    def test_candidate_resolving_outside_repo_root_is_refused(self):
        """A target/ reached only via a symlinked ANCESTOR (not itself a
        symlink) whose resolved path escapes the repo root — defense in
        depth beyond the direct-symlink check above. Exercised via the
        `candidates=` injection since normal discovery (followlinks=False)
        can never surface this shape on its own."""
        repo = _mkrepo(self.root, "proj")
        outside = self.root / "outside"
        stale_target = outside / "target"
        stale_target.mkdir(parents=True)
        stale_file = stale_target / "f.bin"
        stale_file.write_bytes(b"x")
        os.utime(stale_file, (NOW - 999 * DAY, NOW - 999 * DAY))
        (repo / "escape").symlink_to(outside)
        candidate_target = repo / "escape" / "target"
        results = self._purge(
            max_age_days=7, dry_run=False,
            candidates=[(candidate_target, repo)])
        self.assertFalse(results[0]["purged"])
        self.assertIn("escapes", results[0]["reason"])
        self.assertTrue(stale_file.exists())

    # --- logging ------------------------------------------------------
    def test_purge_is_logged_with_size_and_reason(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=45, n_files=3, size=4096)
        self._purge(max_age_days=7, dry_run=False)
        log_text = self.log_path.read_text()
        self.assertIn("PURGED", log_text)
        self.assertIn(str(repo / "target"), log_text)
        self.assertIn("size=", log_text)

    def test_skip_is_also_logged(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=1)
        self._purge(max_age_days=7, dry_run=False)
        log_text = self.log_path.read_text()
        self.assertIn("SKIP", log_text)
        self.assertIn("fresh", log_text)

    def test_dry_run_log_line_says_would_purge_not_purged(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=45)
        self._purge(max_age_days=7, dry_run=True)
        log_text = self.log_path.read_text()
        self.assertIn("DRYRUN-WOULD-PURGE", log_text)
        self.assertNotIn("\nPURGED", "\n" + log_text)

    # --- cadence gate (automatic install/push wiring only) ----------------
    def test_second_call_within_a_day_is_a_noop_without_force(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=45)
        first = self._purge(max_age_days=7, dry_run=False, force=False)
        self.assertTrue(first[0]["purged"])
        # A second stale repo appears; the cadence gate must still say "no"
        # regardless of what it would find, because it hasn't been a day.
        _mktarget(_mkrepo(self.root, "spinbike"), age_days=45)
        second = self._purge(max_age_days=7, dry_run=False, force=False,
                             now=NOW + 3600)   # one hour later
        self.assertEqual(second, [])

    def test_force_always_bypasses_cadence(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=45)
        self._purge(max_age_days=7, dry_run=False, force=True)
        t2 = _mktarget(_mkrepo(self.root, "spinbike"), age_days=45)
        second = self._purge(max_age_days=7, dry_run=False, force=True,
                             now=NOW + 3600)
        self.assertEqual(len(second), 1)
        self.assertFalse(t2.exists())

    def test_dry_run_always_bypasses_cadence_too(self):
        repo = _mkrepo(self.root, "songplayer")
        _mktarget(repo, age_days=45)
        self._purge(max_age_days=7, dry_run=False, force=True)
        _mktarget(_mkrepo(self.root, "spinbike"), age_days=45)
        second = self._purge(max_age_days=7, dry_run=True, force=False,
                             now=NOW + 3600)
        self.assertEqual(len(second), 1)


# ---------------------------------------------------------------------------
# CLI wiring — `airuleset.py purge-targets`
# ---------------------------------------------------------------------------

class TestCmdPurgeTargetsWiring(unittest.TestCase):
    def test_subcommand_registered(self):
        self.assertIn("purge-targets", airuleset.SUBCOMMANDS)
        self.assertIs(airuleset.SUBCOMMANDS["purge-targets"], airuleset.cmd_purge_targets)

    def test_forwards_dry_run_and_max_age_days_and_forces(self):
        ns = SimpleNamespace(dry_run=True, max_age_days=3)
        with m.patch.object(airuleset, "purge_stale_tier0_targets") as p:
            p.return_value = [{"target": "/x/target", "repo": "/x", "purged": True,
                               "reason": "stale (99.0d >= 3d), 1.0MB", "size": 1048576}]
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.cmd_purge_targets(ns)
        p.assert_called_once()
        kwargs = p.call_args.kwargs
        self.assertEqual(kwargs.get("max_age_days"), 3)
        self.assertTrue(kwargs.get("dry_run"))
        self.assertTrue(kwargs.get("force"),
                        "a manual CLI invocation must always bypass the cadence gate")
        self.assertIn("WOULD PURGE", out.getvalue())

    def test_real_run_reports_purged_not_would_purge(self):
        ns = SimpleNamespace(dry_run=False, max_age_days=None)
        with m.patch.object(airuleset, "purge_stale_tier0_targets") as p:
            p.return_value = [{"target": "/x/target", "repo": "/x", "purged": True,
                               "reason": "stale", "size": 2048}]
            out = StringIO()
            with m.patch("sys.stdout", out):
                airuleset.cmd_purge_targets(ns)
        text = out.getvalue()
        self.assertIn("PURGED", text)
        self.assertNotIn("WOULD PURGE", text)


if __name__ == "__main__":
    unittest.main()
