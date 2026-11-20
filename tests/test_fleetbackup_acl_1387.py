"""#1387 — the installed fleetbackup ACL script, executed for real.

`apply` runs REAL setfacl/getfacl on temp files the test user owns (with
`nobody` standing in for fleetbackup); `check` is exercised with owner-
permission proxies (the test user IS the prober, so a mode the owner cannot
read stands for "fleetbackup cannot read"); the root `refresh` runs under
stub runuser/getent/passwd/sudo with scripted read-back answers.
"""
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fleetbackup_1387_helpers import (  # noqa: E402
    ME, KEYLINE, Box, acl, entry, make_layout)


class TestApply(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)
        self.box = Box(self.td)
        self.h = self.box.home()
        make_layout(self.h)

    def apply(self, mode="grant"):
        return self.box.run("apply", "nobody", self.h, mode)

    def p(self, rel):
        return os.path.join(self.h, rel)

    def test_every_other_child_gets_a_named_deny_with_its_mask_kept(self):
        before = {rel: os.lstat(self.p(rel)).st_mode for rel in
                  ("devel", ".private", ".bashrc", ".claude/projects", ".claude/settings.json")}
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel, mode in before.items():
            self.assertEqual(entry(self.p(rel), "user:nobody:"), "user:nobody:---", rel)
            self.assertEqual(os.lstat(self.p(rel)).st_mode, mode, rel)

    def test_a_symlink_child_is_never_followed(self):
        outside = os.path.join(self.td, "outside")
        os.makedirs(outside)
        os.symlink(outside, self.p("ext"))
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(entry(outside, "user:nobody:"))

    def test_home_and_claude_are_traverse_only_without_default_acl(self):
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        for d in (self.h, self.p(".claude")):
            self.assertEqual(entry(d, "user:nobody:"), "user:nobody:--x", d)
            self.assertFalse(any(ln.startswith("default:") for ln in acl(d)), d)

    def test_tree_dirs_are_readable_with_a_closed_default(self):
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("uploads", "uploads/sub", ".claude/work-products", ".claude/work-products/a"):
            lines = acl(self.p(rel))
            self.assertIn("user:nobody:r-x", lines, rel)
            self.assertIn("group::---", lines, rel)
            for want in ("default:user:nobody:r-x", "default:group::---", "default:other::---"):
                self.assertIn(want, lines, rel)

    def test_tree_files_keep_their_mask_so_0600_stays_closed(self):
        modes = {rel: os.stat(self.p(rel)).st_mode for rel in ("uploads/x.txt", "uploads/p.txt")}
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("user:nobody:r--", acl(self.p("uploads/x.txt")))
        self.assertEqual(entry(self.p("uploads/p.txt"), "user:nobody:"),
                         "user:nobody:r--\t#effective:---")
        for rel, mode in modes.items():
            self.assertEqual(os.stat(self.p(rel)).st_mode, mode, rel)

    def test_new_files_follow_the_default_whatever_the_umask(self):
        self.apply()
        old = os.umask(0o002)
        try:
            for name, mode in (("open.txt", 0o666), ("priv.txt", 0o600)):
                os.close(os.open(self.p("uploads/" + name), os.O_CREAT | os.O_WRONLY, mode))
        finally:
            os.umask(old)
        self.assertIn("user:nobody:r-x\t#effective:r--", acl(self.p("uploads/open.txt")))
        self.assertIn("other::---", acl(self.p("uploads/open.txt")))
        self.assertIn("user:nobody:r-x\t#effective:---", acl(self.p("uploads/priv.txt")))

    def test_a_group_the_owner_closed_is_never_reopened(self):
        sub = self.p("uploads/sub")
        os.chmod(sub, 0o770)
        subprocess.run(["setfacl", "-m", "u:nobody:---", sub], check=True)
        os.chmod(sub, 0o700)   # extended ACL: group stays rwx, the mask closes it
        claude = self.p(".claude")
        os.chmod(claude, 0o770)
        subprocess.run(["setfacl", "-m", "u:nobody:---", claude], check=True)
        os.chmod(claude, 0o700)
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("group::---", acl(sub))
        self.assertIn("group::---", acl(claude))
        self.assertIn("mask::--x", acl(claude))

    def test_a_foreign_named_entry_refuses_the_tree(self):
        subprocess.run(["setfacl", "-m", "u:daemon:rwx", self.p("uploads/sub")], check=True)
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tree not granted", r.stderr)
        self.assertIsNone(entry(self.p("uploads"), "user:nobody:"))
        self.assertEqual(entry(self.p(".claude/work-products"), "user:nobody:"), "user:nobody:r-x")

    def test_a_foreign_named_entry_on_claude_keeps_it_closed(self):
        subprocess.run(["setfacl", "-m", "u:daemon:r-x", self.p(".claude")], check=True)
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertIsNone(entry(self.p(".claude"), "user:nobody:"))
        self.assertIsNone(entry(self.p(".claude/work-products"), "user:nobody:"))

    def test_hold_closes_the_home_and_opens_nothing(self):
        r = self.apply("hold")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:---")
        self.assertIsNone(entry(self.p("uploads"), "user:nobody:"))
        self.assertEqual(entry(self.p("devel"), "user:nobody:"), "user:nobody:---")

    def test_a_symlinked_tree_is_not_granted(self):
        outside = os.path.join(self.td, "elsewhere")
        os.makedirs(outside)
        os.rename(self.p("uploads"), os.path.join(self.td, "old-uploads"))
        os.symlink(outside, self.p("uploads"))
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(entry(outside, "user:nobody:"))

    def test_second_run_writes_nothing(self):
        self.apply()
        paths = [self.p(r) for r in ("uploads/x.txt", "devel", ".private", "uploads/sub")]
        ctimes = [os.stat(p).st_ctime_ns for p in paths]
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([os.stat(p).st_ctime_ns for p in paths], ctimes)

    def test_special_files_close_too(self):
        os.mkfifo(self.p("fifo"))
        s = socket.socket(socket.AF_UNIX)
        self.addCleanup(s.close)
        s.bind(self.p("sock"))
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel in ("fifo", "sock"):
            self.assertEqual(entry(self.p(rel), "user:nobody:"), "user:nobody:---", rel)


class TestCheck(unittest.TestCase):
    """The read-back as fleetbackup, with the test user as the prober."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)
        self.box = Box(self.td)

    def node(self, name, mode, kind="f"):
        p = os.path.join(self.td, name)
        if kind == "d":
            os.mkdir(p)
        else:
            with open(p, "w") as f:
                f.write("x")
        os.chmod(p, mode)
        self.addCleanup(os.chmod, p, 0o700)
        return p

    def check(self, *records):
        data = "".join("%s\0%s\0%s\0" % r for r in records)
        r = self.box.run("check", stdin=data)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip(), r.stderr

    def test_a_closed_child_is_no_leak_and_an_open_one_is(self):
        closed_f, closed_d = self.node("cf", 0o000), self.node("cd", 0o000, "d")
        open_f = self.node("of", 0o644)
        out, err = self.check(("C", "-", closed_f), ("C", "-", closed_d), ("C", "-", open_f))
        self.assertEqual(out, "leak=1 miss=0 readable=0 private=0")
        self.assertIn("LEAK reachable", err)

    def test_traverse_or_write_alone_is_a_leak_too(self):
        """A child fleetbackup can only ENTER still exposes every file below it
        by name; any access bit at all is a leak."""
        x_only, w_only = self.node("xd", 0o100, "d"), self.node("wf", 0o200)
        out, _ = self.check(("C", "-", x_only), ("C", "-", w_only))
        self.assertEqual(out, "leak=2 miss=0 readable=0 private=0")

    def test_a_listable_home_is_a_leak_a_traverse_only_one_is_not(self):
        listable, trav = self.node("l", 0o700, "d"), self.node("t", 0o100, "d")
        out, _ = self.check(("T", "-", listable), ("T", "-", trav))
        self.assertEqual(out, "leak=1 miss=0 readable=0 private=0")

    def test_tree_entries_are_readable_private_or_missing(self):
        d_ok, d_bad = self.node("d1", 0o755, "d"), self.node("d2", 0o300, "d")
        f_ok, f_priv, f_miss = self.node("f1", 0o644), self.node("f2", 0o000), self.node("f3", 0o040)
        out, err = self.check(("D", "755", d_ok), ("D", "300", d_bad), ("F", "644", f_ok),
                              ("F", "0", f_priv), ("F", "40", f_miss))
        self.assertEqual(out, "leak=0 miss=2 readable=2 private=1")
        self.assertIn("MISS file", err)

    def test_bad_records_never_count_as_readable(self):
        f_ok = self.node("f1", 0o644)
        out, _ = self.check(("F", "9z", f_ok), ("Q", "-", f_ok))
        self.assertEqual(out, "leak=0 miss=2 readable=0 private=0")

    def test_odd_names_survive_the_record_stream(self):
        odd = self.node("a b\nc", 0o644)
        out, _ = self.check(("C", "-", odd))
        self.assertEqual(out, "leak=1 miss=0 readable=0 private=0")


class TestRecords(unittest.TestCase):
    def test_records_cover_siblings_and_trees_but_no_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            box = Box(td)
            h = box.home()
            make_layout(h)
            r = box.run("records", h)
            self.assertEqual(r.returncode, 0, r.stderr)
            f = r.stdout.split("\0")[:-1]
            recs = {(f[i], os.path.relpath(f[i + 2], h)) for i in range(0, len(f), 3)}
        self.assertIn(("T", "."), recs)
        self.assertIn(("T", ".claude"), recs)
        for rel in ("devel", ".private", ".bashrc", ".claude/projects",
                    ".claude/settings.json", ".claude/airuleset-box-class"):
            self.assertIn(("C", rel), recs)
        for rel in ("uploads", "uploads/sub", ".claude/work-products"):
            self.assertIn(("D", rel), recs)
        for rel in ("uploads/x.txt", "uploads/p.txt", ".claude/work-products/a/w.md"):
            self.assertIn(("F", rel), recs)
        names = {rel for _k, rel in recs}
        self.assertNotIn("link", names)
        self.assertNotIn(("C", "uploads"), recs)
        self.assertNotIn(("C", ".claude/work-products"), recs)


CLEAN = "leak=0 miss=0 readable=4 private=1"


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)

    def box(self, **kw):
        box = Box(self.td, **kw)
        h = box.home()
        make_layout(h)
        return box, h

    def key(self, box):
        with open(box.keys) as f:
            return f.read()

    def test_clean_refresh_applies_reads_back_and_writes_the_key(self):
        box, h = self.box()
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.key(box), KEYLINE + "\n")
        self.assertEqual(stat.S_IMODE(os.stat(box.keys).st_mode), 0o644)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:--x")
        calls = box.calls()
        self.assertTrue(any(c.startswith("%s env -i " % ME) and " apply nobody %s grant" % h in c
                            for c in calls), calls)
        self.assertEqual(sum(c.startswith("nobody ") and c.endswith(" check") for c in calls), 2)
        self.assertIn("readable=4 private=1", r.stdout)

    def test_a_resolved_leak_closes_the_home_and_keeps_the_key(self):
        box, h = self.box()
        box.answers("leak=2 miss=0 readable=4 private=0", "leak=0 miss=0 readable=0 private=0",
                    "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")
        self.assertEqual(self.key(box), KEYLINE + "\n")

    def test_an_unresolved_leak_withdraws_the_key(self):
        box, h = self.box()
        box.answers("leak=1 miss=0 readable=4 private=0", "leak=1 miss=0 readable=0 private=0",
                    "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("ssh-ed25519", self.key(box))
        self.assertIn("UNRESOLVED", r.stderr)

    def test_a_read_back_that_did_not_run_counts_as_open(self):
        box, h = self.box()
        box.answers("", "leak=0 miss=0 readable=0 private=0", "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("did not run", r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")

    def test_missing_files_fail_but_keep_the_key(self):
        box, _h = self.box()
        box.answers("leak=0 miss=3 readable=1 private=0", "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.key(box), KEYLINE + "\n")

    def test_an_unlocked_password_withdraws_and_touches_no_home(self):
        box, h = self.box(pw_status="P")
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("ssh-ed25519", self.key(box))
        self.assertEqual(box.calls(), [])
        self.assertIsNone(entry(h, "user:nobody:"))

    def test_sudo_rights_withdraw(self):
        box, _h = self.box(sudo_out="User nobody may run the following commands")
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("ssh-ed25519", self.key(box))

    def test_a_foreign_fleetbackup_account_is_left_alone(self):
        box, h = self.box(fb_sig_home="/home/fleetbackup")
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped", r.stdout)
        self.assertEqual(box.calls(), [])
        self.assertFalse(os.path.exists(box.keys))
        self.assertIsNone(entry(h, "user:nobody:"))

    def test_an_open_non_stream_home_is_closed_once(self):
        box, _h = self.box()
        other = box.home("otherdir", stream=False)
        os.chmod(other, 0o755)
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(entry(other, "user:nobody:"), "user:nobody:---")
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        before = sum("acl-deny" in c for c in box.calls())
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sum("acl-deny" in c for c in box.calls()), before)

    def test_a_reachable_non_stream_entry_withdraws_the_key(self):
        box, _h = self.box()
        box.answers(CLEAN, "leak=1 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("ssh-ed25519", self.key(box))

    def test_an_unsafe_key_dir_is_never_written(self):
        box, _h = self.box()
        os.chmod(box.ssh, 0o775)
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(box.keys))

    def test_a_child_the_account_cannot_close_holds_the_home(self):
        # the stream account is `nobody` (the files are the test user's, so
        # every open child is "not owned by the account"); fleetbackup = daemon
        box = Box(self.td, fb="daemon", accounts=("nobody",))
        h = box.home("nobody")
        make_layout(h)
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("HELD", r.stderr)
        self.assertEqual(entry(h, "user:daemon:"), "user:daemon:---")
        self.assertIsNone(entry(os.path.join(h, "uploads"), "user:daemon:"))

    def test_a_home_that_belongs_to_no_such_account_is_treated_as_foreign(self):
        box, _h = self.box()
        ghost = box.home("ghost")
        os.chmod(ghost, 0o755)
        box.answers(CLEAN, "leak=0 miss=0 readable=0 private=0")
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is not the home of an account named ghost", r.stderr)
        self.assertFalse(any(" apply nobody %s " % ghost in c for c in box.calls()))
        # not managed, but open: closed like any other non-stream entry
        self.assertEqual(entry(ghost, "user:nobody:"), "user:nobody:---")


if __name__ == "__main__":
    unittest.main()
