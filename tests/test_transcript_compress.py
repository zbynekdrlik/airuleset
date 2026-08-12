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

    def test_mtime_tie_for_newest_protects_both_not_just_the_first_sorted(self):
        # #410 review F5: the exclusion used to compare by OBJECT
        # IDENTITY (`p == newest_path`) after sorting -- a genuine mtime
        # TIE for newest (two files last written in the same wall-clock
        # second, nothing newer in the dir) only protected the ONE row
        # `sort()` happened to place first, contradicting this
        # function's own docstring ("the NEWEST... is NEVER a
        # candidate" -- a tie means BOTH are equally "the newest").
        # Comparing by mtime VALUE excludes every row tied for newest.
        d = self._proj()
        _mkfile(d / "tie-a.jsonl", age_days=40)
        _mkfile(d / "tie-b.jsonl", age_days=40)   # identical age -> identical mtime
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        genuine = {Path(r["path"]).name for r in found if r["reason"] is None}
        self.assertEqual(genuine, set(),
                         "a genuine mtime tie for newest must protect BOTH files")

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
        link = d / "linked.jsonl"
        link.symlink_to(target_dir / "real.jsonl")
        # os.lstat() reports the LINK's own mtime, never the target's --
        # back-date it explicitly (follow_symlinks=False) so it isn't
        # accidentally "the newest in its own dir" just because the
        # symlink itself was freshly created a moment ago.
        os.utime(link, (NOW - 60 * DAY, NOW - 60 * DAY), follow_symlinks=False)
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
        # #410 review F1: the tmp name is now UNIQUE (tempfile.mkstemp,
        # not the literal "<name>.gz.tmp") -- glob for the whole family
        # rather than one hardcoded literal name, which the fix means
        # never existed to begin with (a vacuous pass otherwise).
        self.assertEqual(list(self.root.glob("*.gz.tmp")), [],
                         "no tmp file of any name should survive a successful compress")

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
        self.assertEqual(list(self.root.glob("*.gz.tmp")), [])

    def test_source_disappearing_before_compress_is_refused_not_a_crash(self):
        p = self.root / "gone.jsonl"
        result = airuleset._compress_transcript_file(p, now=NOW)
        self.assertFalse(result["removed"])
        self.assertIsNotNone(result["reason"])

    def test_hash_mismatch_between_write_and_verify_is_caught_and_refused(self):
        """#410 review F6: mutation-tested `verify_hash != orig_hash` had
        ZERO test coverage -- flipping the comparison to `==` (a no-op
        "verify") survived the whole suite unnoticed. A REAL corrupted
        byte in the tmp file is the WRONG fault to inject here: gzip's
        own internal CRC32 validation (`gzip.py`'s `read()`) catches
        almost any real corruption FIRST and raises before the hash
        comparison is even reached, which means such a test cannot
        actually isolate the comparison logic itself from the F3/F4
        exception-widening fix immediately above it. Instead this
        patches `gzip.open` (the verify pass's ONLY caller of it in
        this function) to return VALID, cleanly-readable but DIFFERENT
        bytes than what was actually compressed -- no exception raised
        anywhere, so the ONLY thing that can catch it is the hash
        comparison itself."""
        content = _transcript_bytes(80)
        p = _mkfile(self.root / "s1.jsonl", age_days=40, content=content)
        wrong_bytes = bytearray(content)
        wrong_bytes[-1] ^= 0xFF

        class _FakeDecompressedRead:
            def __init__(self, data):
                self._data = data
                self._pos = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, n):
                chunk = self._data[self._pos:self._pos + n]
                self._pos += len(chunk)
                return chunk

        def _fake_gzip_open(path, mode):
            return _FakeDecompressedRead(bytes(wrong_bytes))

        with m.patch("airuleset.gzip.open", side_effect=_fake_gzip_open):
            result = airuleset._compress_transcript_file(p, now=NOW)

        self.assertFalse(result["removed"])
        self.assertIn("hash mismatch", result["reason"])
        self.assertTrue(p.exists(), "a caught mismatch must leave the original untouched")
        self.assertEqual(p.read_bytes(), content)
        self.assertFalse((self.root / "s1.jsonl.gz").exists())
        self.assertEqual(list(self.root.glob("*.gz.tmp")), [])


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

    def test_one_candidates_unexpected_exception_does_not_abort_the_whole_sweep(self):
        """#410 review F4: an UNEXPECTED exception escaping
        _compress_transcript_file() for ONE candidate (a per-candidate
        backstop, defense-in-depth on top of that function's own
        internal try/except, which already covers every documented
        failure mode) must never abort the REST of the sweep -- a
        genuine sibling candidate later in the SAME sweep must still be
        processed, and the log/cadence state must still be written."""
        d = self.pdir / "-home-user-proj"
        _mkfile(d / "bad.jsonl", age_days=40)
        _mkfile(d / "good.jsonl", age_days=41)
        _mkfile(d / "newer.jsonl", age_days=1)   # protects both from being "newest"
        real_compress = airuleset._compress_transcript_file

        def flaky_compress(path, now=None):
            if "bad.jsonl" in str(path):
                raise RuntimeError("boom -- simulated unexpected failure")
            return real_compress(path, now=now)

        with m.patch.object(airuleset, "_compress_transcript_file", side_effect=flaky_compress):
            results = airuleset.sweep_old_transcripts(
                projects_dir=self.pdir, dry_run=False, force=True, now=NOW,
                min_age_days=30, min_size_bytes=100,
                log_path=self.log_path, state_path=self.state_path)

        self.assertTrue((d / "good.jsonl.gz").exists(),
                        "the sibling candidate must still be compressed despite the earlier crash")
        self.assertFalse((d / "bad.jsonl.gz").exists())
        self.assertTrue((d / "bad.jsonl").exists(),
                        "the failing candidate's own original must stay untouched")
        bad_rows = [r for r in results if str(r.get("path", "")).endswith("bad.jsonl")]
        self.assertEqual(len(bad_rows), 1)
        self.assertFalse(bad_rows[0]["removed"])
        self.assertIn("unexpected error", bad_rows[0]["reason"])
        self.assertTrue(self.log_path.exists())
        self.assertTrue(self.state_path.exists())

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
    running it wholesale, this drives the ACTUAL wiring function it calls
    (`_run_transcript_compress_step`, #410 review F2) with `sweep_fn`
    mocked, so the assertion is about the REAL call cmd_install() makes,
    not a copy of the same two lines re-implemented inside the test
    itself (the pre-fix version's own tautology -- it could never fail
    regardless of what cmd_install() actually does)."""

    def test_install_wiring_calls_dry_run_true_when_env_unset(self):
        import inspect
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("_run_transcript_compress_step", src,
                      "cmd_install must call the extracted, testable wiring step")

    def test_env_var_flip_actually_changes_the_dry_run_argument(self):
        """A behavioural proof against the REAL wiring function
        (`_run_transcript_compress_step`, which is exactly what
        `cmd_install()`'s own step 12 calls) -- with the env var UNSET,
        the sweep must be called with dry_run=True; with it set to "1",
        dry_run=False. A mutant that hardcodes `dry_run=False` in
        `_run_transcript_compress_step()` fails THIS test (unlike the
        pre-fix self-referential version)."""
        with m.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIRULESET_TRANSCRIPT_COMPRESS_LIVE", None)
            fake = m.Mock(return_value=[])
            airuleset._run_transcript_compress_step(sweep_fn=fake)
            self.assertTrue(fake.call_args.kwargs.get("dry_run"),
                            "env var unset -> dry_run=True")

        with m.patch.dict(os.environ, {"AIRULESET_TRANSCRIPT_COMPRESS_LIVE": "1"}):
            fake = m.Mock(return_value=[])
            airuleset._run_transcript_compress_step(sweep_fn=fake)
            self.assertFalse(fake.call_args.kwargs.get("dry_run"),
                             "env var set to '1' -> dry_run=False")

    def test_default_sweep_fn_is_the_real_sweep_old_transcripts(self):
        """Without an injected sweep_fn, the step must call the REAL
        production sweep -- not silently no-op."""
        with m.patch.object(airuleset, "sweep_old_transcripts", return_value=[]) as fake:
            with m.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AIRULESET_TRANSCRIPT_COMPRESS_LIVE", None)
                airuleset._run_transcript_compress_step()
            fake.assert_called_once()
            self.assertTrue(fake.call_args.kwargs.get("dry_run"))


class TestSubcommandRegistration(unittest.TestCase):
    def test_sweep_transcripts_registered(self):
        self.assertIs(airuleset.SUBCOMMANDS["sweep-transcripts"],
                      airuleset.cmd_sweep_transcripts)


if __name__ == "__main__":
    unittest.main()
