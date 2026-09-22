"""Subagent-transcript leg of the gzip-at-rest discovery (#1117).

#410 shipped `discover_old_transcript_candidates` MAIN-only ("SCOPE (v1)"),
deferring `subagents/` until "real subagent-transcript disk pressure exists".
That pressure now exists: the disk-guard auto-filed #1117 at 95% on
odoo-gatekeeper, where subagent transcripts hold 4.36 GB (3.05 GB >7d) and the
drain ladder had NO rung that could reclaim them (`_plan_transcripts` calls the
same MAIN-only discovery). This adds an opt-in `include_subagents` leg, turned
on ONLY by the pressure caller (`watchdog/disk_guard._plan_transcripts`); the
install-step report-only sweep stays main-only (default False).

Every safety rule of the main leg is reused unchanged for the subagent leg
EXCEPT the newest-per-dir protection: each subagent file is its own agent and is
never `/resume`d, so there is nothing to protect. A subagent idle 7 days is
terminated.
"""

import gzip
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402
from watchdog import disk_guard as dg                     # noqa: E402

NOW = 1786176246.0          # fixed; never time.time() (repo convention)
DAY = 86400.0


def _mkfakeproc(root, entries):
    """A fake `/proc`-shaped tree (identical shape to
    test_transcript_compress.py's own helper)."""
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
    lines = []
    for i in range(n_lines):
        lines.append(json.dumps({"type": "user", "uuid": "u%d" % i,
                                 "message": {"role": "user",
                                             "content": "q %d %s" % (i, "x" * 200)}}))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _mkfile(path, age_days, now=NOW, content=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else _transcript_bytes())
    mtime = now - age_days * DAY
    os.utime(path, (mtime, mtime))
    return path


class TestSubagentLegDiscovery(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-subagent-discover-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pdir = self.root / "projects"

    def _proj(self, name="-home-user-proj"):
        d = self.pdir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sub(self, proj, sid="sess-1", agent="agent-x.jsonl"):
        return proj / sid / "subagents" / agent

    def _by_name(self, found, name):
        for r in found:
            if r.get("path") and Path(r["path"]).name == name:
                return r
        self.fail("no row for %r in %r" % (name, [r.get("path") for r in found]))

    def _names(self, found, genuine_only=False):
        return {Path(r["path"]).name for r in found
                if r.get("path") and (not genuine_only or r["reason"] is None)}

    # -- the core opt-in behaviour ------------------------------------------

    def test_old_subagent_is_candidate_only_with_include_subagents_true(self):
        d = self._proj()
        _mkfile(self._sub(d), age_days=40)
        with_flag = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        row = self._by_name(with_flag, "agent-x.jsonl")
        self.assertIsNone(row["reason"], "old subagent file should be a genuine candidate")
        self.assertGreaterEqual(row["age_days"], 30)
        self.assertIn("size", row)

    def test_default_call_never_sees_subagents(self):
        d = self._proj()
        _mkfile(self._sub(d), age_days=40)
        default = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        self.assertNotIn("agent-x.jsonl", self._names(default),
                         "the default (install-sweep) call must stay main-only")

    def test_default_call_output_unchanged_by_presence_of_subagents(self):
        # Characterization: a project's MAIN-leg result is byte-identical
        # whether or not a subagents/ dir exists alongside it.
        d = self._proj()
        _mkfile(d / "old.jsonl", age_days=40)
        _mkfile(d / "newer.jsonl", age_days=1)
        before = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        _mkfile(self._sub(d), age_days=40)   # add a subagent tree
        after = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100)
        self.assertEqual(before, after,
                         "adding a subagents/ tree must not change the default result")

    # -- no newest-per-dir protection for the subagent leg -------------------

    def test_every_old_subagent_is_a_candidate_no_newest_protection(self):
        # Two subagent files in the SAME subagents dir, both old -> BOTH are
        # candidates. The main leg would protect the newest; the subagent leg
        # must not (each file is its own never-resumed agent).
        d = self._proj()
        _mkfile(self._sub(d, agent="agent-a.jsonl"), age_days=40)
        _mkfile(self._sub(d, agent="agent-b.jsonl"), age_days=10)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=5, min_size_bytes=100,
            include_subagents=True)
        genuine = self._names(found, genuine_only=True)
        self.assertEqual(genuine, {"agent-a.jsonl", "agent-b.jsonl"},
                         "no newest-per-dir protection for subagent files")

    def test_nested_subagent_at_any_depth_is_walked(self):
        d = self._proj()
        deep = d / "sess-1" / "subagents" / "nested" / "agent-deep.jsonl"
        _mkfile(deep, age_days=40)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        row = self._by_name(found, "agent-deep.jsonl")
        self.assertIsNone(row["reason"])

    # -- reused safety rules -------------------------------------------------

    def test_live_fd_subagent_never_candidate(self):
        d = self._proj()
        sub = _mkfile(self._sub(d), age_days=60)
        proc = _mkfakeproc(self.root, [{"pid": "777", "fds": [str(sub.resolve())]}])
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True, proc_dir=proc)
        row = self._by_name(found, "agent-x.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("in live use", row["reason"])

    def test_symlink_subagent_file_never_followed(self):
        d = self._proj()
        target = self.root / "elsewhere" / "real.jsonl"
        _mkfile(target, age_days=90)
        link = self._sub(d)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        os.utime(link, (NOW - 60 * DAY, NOW - 60 * DAY), follow_symlinks=False)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        row = self._by_name(found, "agent-x.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("symlink", row["reason"])

    def test_symlinked_subagents_dir_never_descended(self):
        # A `subagents` dir that is itself a symlink to another directory must
        # never be walked into (never follow symlinked dirs).
        d = self._proj()
        real = self.root / "real-subagents"
        _mkfile(real / "agent-hidden.jsonl", age_days=90)
        sess = d / "sess-1"
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "subagents").symlink_to(real, target_is_directory=True)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        self.assertNotIn("agent-hidden.jsonl", self._names(found),
                         "a symlinked subagents dir must never be descended")

    def test_under_age_subagent_kept_not_swept(self):
        d = self._proj()
        _mkfile(self._sub(d), age_days=3)
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        row = self._by_name(found, "agent-x.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("too recent", row["reason"])

    def test_under_size_subagent_kept_not_swept(self):
        d = self._proj()
        _mkfile(self._sub(d), age_days=40, content=b"{}\n")
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=1024,
            include_subagents=True)
        row = self._by_name(found, "agent-x.jsonl")
        self.assertIsNotNone(row["reason"])
        self.assertIn("size floor", row["reason"])

    def test_gz_subagent_never_rediscovered(self):
        d = self._proj()
        gzp = self._sub(d, agent="agent-x.jsonl.gz")
        gzp.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(gzp, "wb") as f:
            f.write(_transcript_bytes())
        os.utime(gzp, (NOW - 90 * DAY, NOW - 90 * DAY))
        found = airuleset.discover_old_transcript_candidates(
            projects_dir=self.pdir, now=NOW, min_age_days=30, min_size_bytes=100,
            include_subagents=True)
        self.assertTrue(all(not (r.get("path") or "").endswith(".gz") for r in found),
                        "a .jsonl.gz subagent file must never be discovered")


class TestPlanTranscriptsPressurePath(unittest.TestCase):
    """`watchdog/disk_guard._plan_transcripts` (rung e of the drain ladder)
    must emit gzip actions for OLD subagent transcripts (#1117)."""

    def setUp(self):
        self._tmp = TemporaryDirectory(prefix="airuleset-plan-transcripts-")
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.proj = (self.home / ".claude" / "projects" / "-home-user-proj")

    def test_plan_transcripts_yields_gzip_action_for_old_subagent(self):
        sub = self.proj / "sess-1" / "subagents" / "agent-x.jsonl"
        _mkfile(sub, age_days=40, content=_transcript_bytes(700))  # > 100KB floor
        actions = dg._plan_transcripts(
            home=str(self.home), now=NOW, box_class_fn=lambda: "workstation")
        gzip_paths = [a["path"] for a in actions
                      if a.get("kind") == "gzip" and a.get("reason") is None]
        self.assertIn(str(sub), gzip_paths,
                      "the pressure drain must plan a gzip for the old subagent file")


if __name__ == "__main__":
    unittest.main()
