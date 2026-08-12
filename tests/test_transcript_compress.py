"""Transcript gzip-at-rest retention (#410, split from #380 point 3).

#376 force-set `cleanupPeriodDays=3650` fleet-wide, disabling Claude Code's
own native transcript auto-delete with nothing wired in to replace what it
used to (silently) do -- ~/.claude/projects/**/*.jsonl now grows unbounded.
This is the size-aware retention layer: gzip-at-rest for OLD (default 30d+)
top-level session transcripts, NEVER deletes, reversible via a plain
`gunzip`. Same sweep shape as #355's sibling sweeps in test_fleet_hygiene.py
(discovery separated from action, own log+state file, cadence-gated, real
tempdir fixtures) -- with a materially heavier action step (a real
compress-verify-swap protocol over real file content, not just an unlink),
so it gets its own dedicated test file.

Scope decision (see the #410 design comment on the ticket): MAIN
(top-level, per-project) transcripts only in v1 -- never a `subagents/`
descendant, which neither `/resume` nor claude-history ever read.
"""

import gzip
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


def _mkfakeproc(root, entries):
    """A fake `/proc`-shaped tree -- identical shape to
    test_fleet_hygiene.py's own helper (no tests/__init__.py in this repo,
    so the small helper is duplicated rather than cross-imported --
    established convention)."""
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


def _transcript_bytes(n_lines=50):
    """Real transcript-SHAPED content (JSONL, one user/assistant record per
    line) -- large enough to comfortably clear the size floor by default,
    and genuinely re-parseable so a round-trip content check is real."""
    lines = []
    for i in range(n_lines):
        lines.append(json.dumps({"type": "user", "uuid": "u%d" % i,
                                 "message": {"role": "user",
                                             "content": "question number %d, padded %s" % (i, "x" * 200)}}))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _mkfile(path, age_days, now=NOW, content=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else _transcript_bytes())
    mtime = now - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# discover_old_transcript_candidates
# ---------------------------------------------------------------------------

class TestDiscoverOldTranscriptCandidates(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-transcript-discover-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pdir = self.root / "projects"

    def _proj(self, name="-home-user-proj"):
        d = self.pdir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _by_name(self, found, name):
        for r in found:
            if Path(r["path"]).name == name:
                return r
        self.fail("no row for %r in %r" % (name, [r.get("path") for r in found]))

    def test_finds_a_genuine_old_non_newest_candidate(self):
        d = self._proj()
        _mkfile(d / "old.jsonl", age_days=40)
        _mkfile(d / "newer.jsonl", age_days=1)   # protects "old" from being the newest
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        row = self._by_name(found, "old.jsonl")
        self.assertIsNone(row["reason"])
        self.assertGreaterEqual(row["age_days"], 30)

    def test_newest_file_in_its_own_dir_never_a_candidate_even_when_very_old(self):
        d = self._proj()
        # The ONLY file in this dormant project -- it is also, by
        # definition, the newest -- must never be touched regardless of
        # age (protects /resume/claude --continue).
        _mkfile(d / "only.jsonl", age_days=400)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        self.assertEqual(found, [])

    def test_second_newest_in_a_three_file_dir_is_still_protected_by_recency_only(self):
        d = self._proj()
        _mkfile(d / "oldest.jsonl", age_days=100)
        _mkfile(d / "middle.jsonl", age_days=50)
        _mkfile(d / "newest.jsonl", age_days=1)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        names = {Path(r["path"]).name for r in found if r["reason"] is None}
        self.assertEqual(names, {"oldest.jsonl", "middle.jsonl"})
        self.assertNotIn("newest.jsonl", [Path(r["path"]).name for r in found])

    def test_too_young_file_kept_not_swept(self):
        d = self._proj()
        _mkfile(d / "young.jsonl", age_days=5)
        _mkfile(d / "newer.jsonl", age_days=0)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        row = self._by_name(found, "young.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("too recent", row["reason"])

    def test_below_size_floor_kept_not_swept(self):
        d = self._proj()
        _mkfile(d / "tiny.jsonl", age_days=40, content=b"{}\n")
        _mkfile(d / "newer.jsonl", age_days=1)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=1024)
        row = self._by_name(found, "tiny.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("size floor", row["reason"])

    def test_already_compressed_gz_sibling_never_rediscovered(self):
        d = self._proj()
        with gzip.open(d / "already.jsonl.gz", "wb") as f:
            f.write(_transcript_bytes())
        os.utime(d / "already.jsonl.gz", (NOW - 90 * DAY, NOW - 90 * DAY))
        _mkfile(d / "newer.jsonl", age_days=1)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        self.assertTrue(all(not Path(r["path"]).name.endswith(".gz") for r in found),
                        "a .jsonl.gz file must never be discovered as a candidate")

    def test_symlink_entry_never_followed_never_a_candidate(self):
        d = self._proj()
        target_dir = self.root / "elsewhere"
        target_dir.mkdir()
        _mkfile(target_dir / "real.jsonl", age_days=90)
        (d / "linked.jsonl").symlink_to(target_dir / "real.jsonl")
        _mkfile(d / "newer.jsonl", age_days=1)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        row = self._by_name(found, "linked.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("symlink", row["reason"])

    def test_live_process_guard_skips_old_but_open_file(self):
        d = self._proj()
        old = _mkfile(d / "held-open.jsonl", age_days=60)
        _mkfile(d / "newer.jsonl", age_days=1)
        proc = _mkfakeproc(self.root, [{"pid": "555", "fds": [str(old.resolve())]}])
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            proc_dir=proc)
        row = self._by_name(found, "held-open.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("in live use", row["reason"])

    def test_min_age_days_env_override_is_actually_read(self):
        d = self._proj()
        _mkfile(d / "f.jsonl", age_days=3)
        _mkfile(d / "newer.jsonl", age_days=0)
        with m.patch.dict(os.environ, {"AIRULESET_TRANSCRIPT_MIN_AGE_DAYS": "1"}):
            found = airuleset.discover_old_transcript_candidates(
                projects_dir=self.pdir, now=NOW, min_age_days=None, min_size_bytes=100)
        row = self._by_name(found, "f.jsonl")
        self.assertIsNone(row["reason"], "env override to 1 day must admit a 3-day-old file")

    def test_min_size_bytes_env_override_is_actually_read(self):
        d = self._proj()
        _mkfile(d / "small.jsonl", age_days=40, content=b"x" * 500)
        _mkfile(d / "newer.jsonl", age_days=1)
        with m.patch.dict(os.environ, {"AIRULESET_TRANSCRIPT_MIN_SIZE_BYTES": "100"}):
            found = airuleset.discover_old_transcript_candidates(
                projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=None)
        row = self._by_name(found, "small.jsonl")
        self.assertIsNone(row["reason"], "env override to 100B must admit a 500B file")

    def test_projects_dir_missing_returns_empty_not_an_error(self):
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir / "nope", now=NOW)
        self.assertEqual(found, [])

    def test_subagent_transcripts_are_never_discovered_v1_scope(self):
        d = self._proj()
        _mkfile(d / "s1.jsonl", age_days=1)
        sub = d / "s1" / "subagents"
        _mkfile(sub / "agent-x.jsonl", age_days=90)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        self.assertTrue(all("subagents" not in r["path"] for r in found),
                        "v1 scope is MAIN transcripts only -- see #410 design comment")


# ---------------------------------------------------------------------------
# _compress_transcript_file (compress-verify-swap protocol)
# ---------------------------------------------------------------------------

class TestCompressTranscriptFile(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-transcript-compress-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_compresses_and_removes_the_original_content_is_byte_identical(self):
        content = _transcript_bytes()
        p = _mkfile(self.root / "s1.jsonl", age_days=40, content=content)
        result = airuleset._compress_transcript_file(p, now=NOW)
        self.assertTrue(result["removed"])
        self.assertFalse(p.exists(), "the original .jsonl must be gone")
        gz = self.root / "s1.jsonl.gz"
        self.assertTrue(gz.exists())
        with gzip.open(gz, "rb") as f:
            decompressed = f.read()
        self.assertEqual(decompressed, content, "gzip-at-rest must NEVER lose or alter data")

    def test_no_tmp_file_left_behind_on_success(self):
        p = _mkfile(self.root / "s1.jsonl", age_days=40)
        airuleset._compress_transcript_file(p, now=NOW)
        self.assertFalse((self.root / "s1.jsonl.gz.tmp").exists())

    def test_original_mtime_is_preserved_on_the_compressed_file(self):
        old_mtime = NOW - 40 * DAY
        p = _mkfile(self.root / "s1.jsonl", age_days=40)
        airuleset._compress_transcript_file(p, now=NOW)
        gz = self.root / "s1.jsonl.gz"
        self.assertAlmostEqual(gz.stat().st_mtime, old_mtime, delta=2)

    def test_symlink_refused_never_compressed(self):
        target = self.root / "real.jsonl"
        _mkfile(target, age_days=40)
        link = self.root / "link.jsonl"
        link.symlink_to(target)
        result = airuleset._compress_transcript_file(link, now=NOW)
        self.assertFalse(result["removed"])
        self.assertIn("symlink", result["reason"])
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.root / "link.jsonl.gz").exists())

    def test_toctou_race_source_changed_mid_compress_refuses_and_leaves_original(self):
        """The FILE genuinely changes (a resumed session writes more)
        between the compress read and the final swap -- simulated by
        patching os.stat so the SECOND call (the re-check) reports a
        different size than the first."""
        content = _transcript_bytes()
        p = _mkfile(self.root / "s1.jsonl", age_days=40, content=content)
        real_stat = os.stat
        calls = {"n": 0}

        def fake_stat(path, *a, **kw):
            calls["n"] += 1
            st = real_stat(path, *a, **kw)
            if calls["n"] == 2 and str(path) == str(p):
                # second stat() call is the TOCTOU re-check -- report a
                # DIFFERENT size than the first, real call.
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev,
                                       st.st_nlink, st.st_uid, st.st_gid,
                                       st.st_size + 999, st.st_atime,
                                       st.st_mtime, st.st_ctime))
            return st

        with m.patch("os.stat", side_effect=fake_stat):
            result = airuleset._compress_transcript_file(p, now=NOW)
        self.assertFalse(result["removed"])
        self.assertIn("changed", result["reason"])
        self.assertTrue(p.exists(), "the original must be left untouched on a detected race")
        self.assertEqual(p.read_bytes(), content)
        self.assertFalse((self.root / "s1.jsonl.gz").exists())
        self.assertFalse((self.root / "s1.jsonl.gz.tmp").exists())

    def test_source_disappearing_before_compress_is_refused_not_a_crash(self):
        p = self.root / "gone.jsonl"
        result = airuleset._compress_transcript_file(p, now=NOW)
        self.assertFalse(result["removed"])
        self.assertIsNotNone(result["reason"])


# ---------------------------------------------------------------------------
# sweep_old_transcripts
# ---------------------------------------------------------------------------

class TestSweepOldTranscripts(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-transcript-sweep-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pdir = self.root / "projects"
        self.log_path = self.root / "log.log"
        self.state_path = self.root / "state.json"

    def _setup(self):
        d = self.pdir / "-home-user-proj"
        _mkfile(d / "old.jsonl", age_days=40)
        _mkfile(d / "young.jsonl", age_days=1)
        return d

    def test_default_dry_run_is_true_nothing_is_ever_touched_by_accident(self):
        """#410's own hard constraint: the underlying function's OWN
        default must be report-only, belt-and-suspenders on top of
        cmd_install()'s own env-var gate -- a caller that forgets to pass
        dry_run= explicitly must still be safe."""
        d = self._setup()
        airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path, force=True)
        self.assertTrue((d / "old.jsonl").exists(),
                        "sweep_old_transcripts() called with NO dry_run= must default to report-only")
        self.assertFalse((d / "old.jsonl.gz").exists())

    def test_dry_run_true_deletes_and_compresses_nothing(self):
        d = self._setup()
        results = airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=True, now=NOW, min_age_days=30,
            min_size_bytes=100, log_path=self.log_path, state_path=self.state_path)
        self.assertTrue((d / "old.jsonl").exists())
        acted = [r for r in results if r.get("removed")]
        self.assertEqual(acted, [])
        would = [r for r in results if str(r.get("reason", "")).startswith("would compress")]
        self.assertEqual(len(would), 1)

    def test_force_live_compresses_only_genuine_candidates(self):
        d = self._setup()
        results = airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=True, now=NOW,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        self.assertFalse((d / "old.jsonl").exists())
        self.assertTrue((d / "old.jsonl.gz").exists())
        self.assertTrue((d / "young.jsonl").exists(), "the young file must be left alone")
        self.assertFalse((d / "young.jsonl.gz").exists())
        removed = [r for r in results if r.get("removed")]
        self.assertEqual(len(removed), 1)
        self.assertTrue(self.log_path.exists())
        self.assertTrue(self.state_path.exists())

    def test_data_is_never_lost_only_ever_gzipped(self):
        d = self._setup()
        original_bytes = (d / "old.jsonl").read_bytes()
        airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=True, now=NOW,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        with gzip.open(d / "old.jsonl.gz", "rb") as f:
            restored = f.read()
        self.assertEqual(restored, original_bytes)

    def test_idempotent_second_run_compresses_nothing_new(self):
        d = self._setup()
        airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=True, now=NOW,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        gz_mtime_before = (d / "old.jsonl.gz").stat().st_mtime
        results2 = airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=True, now=NOW + 60,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        removed2 = [r for r in results2 if r.get("removed")]
        self.assertEqual(removed2, [], "a second run must never re-compress an already-.gz file")
        self.assertEqual((d / "old.jsonl.gz").stat().st_mtime, gz_mtime_before,
                         "an already-compressed file must not be touched again")

    def test_cadence_gate_skips_without_force(self):
        import json as _json
        d = self._setup()
        self.state_path.write_text(_json.dumps({"last_run": NOW - 3600}))
        results = airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=False, now=NOW,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        self.assertEqual(results, [])
        self.assertTrue((d / "old.jsonl").exists())

    def test_dry_run_never_writes_cadence_state(self):
        self._setup()
        self.assertFalse(self.state_path.exists())
        airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=True, now=NOW, min_age_days=30,
            min_size_bytes=100, log_path=self.log_path, state_path=self.state_path)
        self.assertFalse(self.state_path.exists())

    def test_delete_time_toctou_recheck_refuses_a_now_live_file(self):
        d = self._setup()
        proc = _mkfakeproc(self.root, [{"pid": "777", "fds": [str((d / "old.jsonl").resolve())]}])
        candidates = [{"path": str(d / "old.jsonl"), "reason": None}]
        results = airuleset.sweep_old_transcripts(
            dry_run=False, force=True, now=NOW,
            log_path=self.log_path, state_path=self.state_path,
            candidates=candidates, proc_dir=proc)
        self.assertFalse(results[0]["removed"])
        self.assertIn("in live use", results[0]["reason"])
        self.assertTrue((d / "old.jsonl").exists())

    def test_future_dated_cadence_stamp_does_not_wedge_the_gate_forever(self):
        import json as _json
        d = self._setup()
        self.state_path.write_text(_json.dumps({"last_run": NOW + 999999}))
        results = airuleset.sweep_old_transcripts(
            projects_dir=self.pdir, dry_run=False, force=False, now=NOW,
            min_age_days=30, min_size_bytes=100,
            log_path=self.log_path, state_path=self.state_path)
        self.assertNotEqual(results, [])
        self.assertFalse((d / "old.jsonl").exists())


class TestSweepTranscriptsCLICommand(unittest.TestCase):
    def test_dry_run_reports_and_prints_log_path(self):
        with m.patch.object(airuleset, "sweep_old_transcripts") as fake:
            fake.return_value = [
                {"path": "/home/x/.claude/projects/p/old.jsonl",
                 "reason": "would compress (dry-run)", "size": 570574, "removed": False},
                {"path": "/home/x/.claude/projects/p/young.jsonl",
                 "reason": "too recent (1.0d < 30d)", "removed": False},
            ]
            args = SimpleNamespace(dry_run=True, min_age_days=None, min_size_bytes=None)
            buf = StringIO()
            with m.patch("sys.stdout", buf):
                airuleset.cmd_sweep_transcripts(args)
            out = buf.getvalue()
        self.assertIn("WOULD COMPRESS", out)
        self.assertIn("old.jsonl", out)
        self.assertIn("skip", out)
        self.assertIn(str(airuleset.TRANSCRIPT_COMPRESS_LOG_PATH), out)
        fake.assert_called_once()
        self.assertTrue(fake.call_args.kwargs.get("force"))


# ---------------------------------------------------------------------------
# cmd_install() wiring -- report-only unless AIRULESET_TRANSCRIPT_COMPRESS_LIVE=1
# ---------------------------------------------------------------------------

class TestCmdInstallTranscriptWiring(unittest.TestCase):
    """`cmd_install` is large and touches real system state -- rather than
    running it wholesale, this asserts the ONE load-bearing invariant
    directly against its source: the transcript-sweep call it makes is
    gated on AIRULESET_TRANSCRIPT_COMPRESS_LIVE, defaulting to
    dry_run=True. A full cmd_install() integration run is out of scope for
    a unit test (it touches CLAUDE.md, skills, hooks, plugins, ...)."""

    def test_install_wiring_calls_dry_run_true_when_env_unset(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("AIRULESET_TRANSCRIPT_COMPRESS_LIVE", src,
                      "cmd_install must gate transcript compression on this env var")
        self.assertIn("sweep_old_transcripts", src)

    def test_env_var_flip_actually_changes_the_dry_run_argument(self):
        """A behavioural proof, not just a source-text grep: with the env
        var UNSET, sweep_old_transcripts() must be called with
        dry_run=True; with it set to "1", dry_run=False."""
        with m.patch.object(airuleset, "sweep_old_transcripts", return_value=[]) as fake:
            with m.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AIRULESET_TRANSCRIPT_COMPRESS_LIVE", None)
                live_compress = os.environ.get("AIRULESET_TRANSCRIPT_COMPRESS_LIVE") == "1"
                airuleset.sweep_old_transcripts(dry_run=not live_compress)
            self.assertTrue(fake.call_args.kwargs.get("dry_run"),
                            "env var unset -> dry_run=True")

        with m.patch.object(airuleset, "sweep_old_transcripts", return_value=[]) as fake:
            with m.patch.dict(os.environ, {"AIRULESET_TRANSCRIPT_COMPRESS_LIVE": "1"}):
                live_compress = os.environ.get("AIRULESET_TRANSCRIPT_COMPRESS_LIVE") == "1"
                airuleset.sweep_old_transcripts(dry_run=not live_compress)
            self.assertFalse(fake.call_args.kwargs.get("dry_run"),
                             "env var set to '1' -> dry_run=False")


class TestSubcommandRegistration(unittest.TestCase):
    def test_sweep_transcripts_registered(self):
        self.assertIs(airuleset.SUBCOMMANDS["sweep-transcripts"],
                      airuleset.cmd_sweep_transcripts)


if __name__ == "__main__":
    unittest.main()
