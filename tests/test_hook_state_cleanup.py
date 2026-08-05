"""Unit tests for tests/_hook_state_cleanup.py (#202)."""

import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402


class TestSweepSessionFiles(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_removes_every_file_containing_the_sid(self):
        sid = "sweep-%s" % uuid.uuid4().hex[:10]
        a = Path(self.tmp.name) / ("airuleset-stop-block-%s" % sid)
        b = Path(self.tmp.name) / ("airuleset-untracked-work-block-%s" % sid)
        a.write_text("1\n")
        b.write_text("1\n")
        sweep_session_files(sid, tmp_dir=self.tmp.name)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists())

    def test_leaves_unrelated_files_alone(self):
        sid = "sweep-%s" % uuid.uuid4().hex[:10]
        mine = Path(self.tmp.name) / ("airuleset-stop-block-%s" % sid)
        other = Path(self.tmp.name) / "airuleset-stop-block-completely-unrelated"
        mine.write_text("1\n")
        other.write_text("1\n")
        sweep_session_files(sid, tmp_dir=self.tmp.name)
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())

    def test_is_safe_to_call_when_nothing_matches(self):
        sweep_session_files("no-such-sid-at-all", tmp_dir=self.tmp.name)

    def test_is_safe_to_call_twice(self):
        sid = "sweep-%s" % uuid.uuid4().hex[:10]
        f = Path(self.tmp.name) / ("airuleset-stop-block-%s" % sid)
        f.write_text("1\n")
        sweep_session_files(sid, tmp_dir=self.tmp.name)
        sweep_session_files(sid, tmp_dir=self.tmp.name)   # no crash on re-sweep
        self.assertFalse(f.exists())

    def test_empty_sid_is_a_no_op_not_a_wipe(self):
        # A blank sid globbed as "**" would match EVERY file in the dir —
        # the caller must never be able to accidentally wipe a whole /tmp.
        stray = Path(self.tmp.name) / "totally-unrelated-file"
        stray.write_text("keep me\n")
        sweep_session_files("", tmp_dir=self.tmp.name)
        sweep_session_files(None, tmp_dir=self.tmp.name)
        self.assertTrue(stray.exists())

    def test_never_removes_a_directory(self):
        sid = "sweep-%s" % uuid.uuid4().hex[:10]
        d = Path(self.tmp.name) / ("airuleset-stop-block-%s-dir" % sid)
        d.mkdir()
        sweep_session_files(sid, tmp_dir=self.tmp.name)
        self.assertTrue(d.exists())


if __name__ == "__main__":
    main()
