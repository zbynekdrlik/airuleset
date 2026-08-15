"""Unit tests for tests/_hook_state_cleanup.py (#202, #494)."""

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import (  # noqa: E402
    new_hook_sid, reclaim_stale_orphans, sweep_session_files)


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


class TestReclaimStaleOrphans(TestCase):
    """#494 — age-gated reclamation of killed-run litter. The load-bearing
    safety property is that a CONCURRENT live run's (young) file is NEVER
    touched while an hour-old kill-orphan is reclaimed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _mk(self, name, age_s=0):
        p = Path(self.tmp.name) / name
        p.write_text("1\n")
        if age_s:
            old = time.time() - age_s
            os.utime(p, (old, old))
        return p

    def test_reclaims_an_old_matching_orphan(self):
        p = self._mk("airuleset-runcard-gate-gate-deadbeef", age_s=7200)
        reclaim_stale_orphans("airuleset-runcard-gate-gate-*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertFalse(p.exists())

    def test_never_touches_a_young_live_file(self):
        # THE safety property: a concurrent live run's file (always young) must
        # survive, even though it matches the pattern.
        live = self._mk("airuleset-runcard-gate-gate-cafef00d", age_s=0)
        reclaim_stale_orphans("airuleset-runcard-gate-gate-*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertTrue(live.exists())

    def test_age_boundary_uses_the_now_parameter(self):
        # Deterministic, injected clock: a file exactly at the cutoff stays
        # (>= cutoff), one older goes.
        base = 1_000_000.0
        keep = Path(self.tmp.name) / "airuleset-x-block-keep"
        drop = Path(self.tmp.name) / "airuleset-x-block-drop"
        keep.write_text("1\n")
        drop.write_text("1\n")
        os.utime(keep, (base - 3600, base - 3600))   # exactly at the window
        os.utime(drop, (base - 3601, base - 3601))   # one second past it
        reclaim_stale_orphans("airuleset-x-block-*", tmp_dir=self.tmp.name,
                              max_age_s=3600, now=base)
        self.assertTrue(keep.exists())
        self.assertFalse(drop.exists())

    def test_leaves_an_unrelated_old_file_alone(self):
        mine = self._mk("airuleset-runcard-gate-gate-1", age_s=7200)
        other = self._mk("something-else-entirely", age_s=7200)
        reclaim_stale_orphans("airuleset-runcard-gate-gate-*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertFalse(mine.exists())
        self.assertTrue(other.exists())

    def test_a_bare_star_pattern_is_refused_not_a_wipe(self):
        # A shared /tmp helper must never be able to wipe the whole directory,
        # even given an accidental trivial pattern.
        old = self._mk("anything-at-all", age_s=7200)
        reclaim_stale_orphans("*", tmp_dir=self.tmp.name, max_age_s=3600)
        reclaim_stale_orphans("**", tmp_dir=self.tmp.name, max_age_s=3600)
        reclaim_stale_orphans("", tmp_dir=self.tmp.name, max_age_s=3600)
        reclaim_stale_orphans(None, tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertTrue(old.exists())

    def test_a_degenerate_glob_without_a_literal_anchor_is_refused(self):
        # #494 review MINOR — a pattern made only of glob metachars/ranges
        # (`?*`, `?`, `[a-z]*`) has no family anchor and would broadly match a
        # world-writable /tmp; it must be refused just like a bare "*".
        old = self._mk("zzz-some-old-unrelated-file", age_s=7200)
        # incl. round-2 review: a long ENUMERATED char-class interior
        # ("[abcdefghij]") must not count as a literal anchor.
        degenerates = ("?*", "?", "[a-z]*", "ab*", "[!x]*", "[abcdefghij]*",
                       "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*", "[a-z][a-z]*")
        for degenerate in degenerates:
            reclaim_stale_orphans(degenerate, tmp_dir=self.tmp.name,
                                  max_age_s=3600)
        self.assertTrue(old.exists(), "a degenerate pattern must reclaim nothing")

    def test_a_pattern_with_a_real_literal_anchor_is_accepted(self):
        # The complement: a specific-enough pattern (long literal run) still
        # works, so the guard cannot be satisfied by refusing everything —
        # including one carrying an incidental char-class AFTER a real anchor.
        a = self._mk("airuleset-runcard-gate-gate-old", age_s=7200)
        b = self._mk("airuleset9", age_s=7200)
        reclaim_stale_orphans("airuleset-runcard-gate-gate-*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        reclaim_stale_orphans("airuleset[0-9]*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertFalse(a.exists())
        self.assertFalse(b.exists(), "a real anchor before a [...] class works")

    def test_only_regular_files_never_a_dir_or_symlink(self):
        # Directories and symlinks matching the pattern are left untouched —
        # only the hooks' read-at-start regular-FILE state is the flake.
        d = Path(self.tmp.name) / "airuleset-designgate-designstop-somedir"
        d.mkdir()
        os.utime(d, (time.time() - 7200, time.time() - 7200))
        target = self._mk("real-target", age_s=7200)
        link = Path(self.tmp.name) / "airuleset-designgate-designstop-link"
        link.symlink_to(target)
        old = time.time() - 7200
        os.utime(link, (old, old), follow_symlinks=False)
        reclaim_stale_orphans("airuleset-designgate-designstop-*",
                              tmp_dir=self.tmp.name, max_age_s=3600)
        self.assertTrue(d.exists(), "a directory must never be removed")
        self.assertTrue(link.exists(), "a symlink must never be followed/removed")
        self.assertTrue(target.exists(), "the symlink's target must be untouched")


class _FakeCase:
    """Records addCleanup registrations so a test can invoke them by hand."""

    def __init__(self):
        self.cleanups = []

    def addCleanup(self, fn, *a, **kw):
        self.cleanups.append((fn, a, kw))

    def run_cleanups(self):
        for fn, a, kw in reversed(self.cleanups):
            fn(*a, **kw)


class TestNewHookSid(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_sid_is_prefixed_uuid_never_a_recyclable_pid(self):
        c = _FakeCase()
        sid = new_hook_sid(c, "gate", tmp_dir=self.tmp.name)
        self.assertTrue(sid.startswith("gate-"), sid)
        hexpart = sid[len("gate-"):]
        self.assertEqual(len(hexpart), 32)          # uuid4().hex
        int(hexpart, 16)                            # pure hex, no pid digits

    def test_two_sids_never_collide(self):
        c = _FakeCase()
        a = new_hook_sid(c, "gate", tmp_dir=self.tmp.name)
        b = new_hook_sid(c, "gate", tmp_dir=self.tmp.name)
        self.assertNotEqual(a, b)

    def test_registered_cleanup_removes_exactly_this_sids_files(self):
        c = _FakeCase()
        sid = new_hook_sid(c, "test-qq-reference", tmp_dir=self.tmp.name)
        # All THREE shapes the question-quality gate writes for one SID.
        mine = Path(self.tmp.name) / ("airuleset-question-quality-block-" + sid)
        lastq = Path(self.tmp.name) / ("claude-discord-lastq-" + sid)
        active = Path(self.tmp.name) / ("claude-user-active-" + sid)
        mine.write_text("1\n")
        lastq.write_text("1\n")
        active.write_text("1\n")
        other = Path(self.tmp.name) / "airuleset-question-quality-block-someoneelse"
        other.write_text("1\n")
        c.run_cleanups()
        self.assertFalse(mine.exists())
        self.assertFalse(lastq.exists())
        self.assertFalse(active.exists())
        self.assertTrue(other.exists(), "a concurrent sid's file must survive")

    def test_reclaims_an_old_orphan_of_the_family_at_mint_time(self):
        old = Path(self.tmp.name) / "airuleset-runcard-gate-gate-oldpid"
        old.write_text("1\n")
        aged = time.time() - 7200
        os.utime(old, (aged, aged))
        c = _FakeCase()
        new_hook_sid(c, "gate", ["airuleset-runcard-gate-gate-*"],
                     tmp_dir=self.tmp.name)
        self.assertFalse(old.exists(), "an hour-old family orphan is reclaimed")

    def test_mint_time_reclaim_spares_a_young_family_file(self):
        young = Path(self.tmp.name) / "airuleset-runcard-gate-gate-livepid"
        young.write_text("1\n")                      # freshly written = young
        c = _FakeCase()
        new_hook_sid(c, "gate", ["airuleset-runcard-gate-gate-*"],
                     tmp_dir=self.tmp.name)
        self.assertTrue(young.exists(),
                        "a concurrent live family file must never be reclaimed")


if __name__ == "__main__":
    main()
