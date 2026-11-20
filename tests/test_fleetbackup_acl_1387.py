"""#1387 — the installed fleetbackup ACL script, executed for real: the
account-side `apply`, the fleetbackup-side `check` and the root `records`.

`apply` runs REAL setfacl/getfacl on temp files the test user owns (with
`nobody` standing in for fleetbackup); `check` is exercised with owner-
permission proxies (the prober OWNS the probed files, so a mode the owner
cannot read stands for "fleetbackup cannot read"; under root, as in the CI
container, the files are handed to `nobody` and the check runs as nobody).
The root `refresh` has its own suite (test_fleetbackup_refresh_1387.py).
"""
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fleetbackup_1387_helpers import Box, acl, entry, make_layout  # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)


class TestApply(_Tmp):
    def setUp(self):
        super().setUp()
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

    def test_home_and_claude_are_traverse_only_and_close_every_new_child(self):
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        for d in (self.h, self.p(".claude")):
            lines = acl(d)
            self.assertIn("user:nobody:--x", lines, d)
            for want in ("default:user:nobody:---", "default:group::---", "default:other::---"):
                self.assertIn(want, lines, d)

    def test_a_default_named_entry_on_the_home_is_neither_widened_nor_blocking(self):
        """review round 2: traversal recalculates only the ACCESS mask, so a
        default named entry neither refuses it, nor may the default mask grow."""
        subprocess.run(["setfacl", "-d", "-m", "u:daemon:rwx,m::--x", self.h], check=True)
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = acl(self.h)
        self.assertIn("user:nobody:--x", lines)
        self.assertIn("default:mask::--x", lines)

    def test_a_file_renamed_over_a_name_is_born_closed(self):
        """Round 1 🔴: a temp file + rename (watchdog state, git config, shell
        history) is a NEW inode; it must not reopen the name."""
        self.apply()
        old = os.umask(0o022)
        try:
            for d in (self.h, self.p(".claude")):
                tmp = os.path.join(d, ".state.tmp")
                with open(tmp, "w") as f:
                    f.write("x")
                os.replace(tmp, os.path.join(d, "state.json"))
                os.mkdir(os.path.join(d, "newdir"))
        finally:
            os.umask(old)
        for d in (self.h, self.p(".claude")):
            for rel in ("state.json", "newdir"):
                lines = acl(os.path.join(d, rel))
                self.assertIn("user:nobody:---", lines, (d, rel))
                self.assertIn("other::---", lines, (d, rel))

    def test_a_file_that_arrived_closed_is_reported_and_its_own_mode_decides(self):
        """Rounds 2-3: a file born under the closed home default (or in a
        re-created tree) arrives with u:fb:--- and mask ---. It is reported, it
        gets r-- like any tree file, and its owner's `chmod g+r` includes it."""
        self.apply()
        old = os.umask(0o022)
        try:
            with open(self.p("report.txt"), "w") as f:     # born closed in the home
                f.write("x")
        finally:
            os.umask(old)
        os.rename(self.p("report.txt"), self.p("uploads/report.txt"))
        os.rename(self.p("devel/code.py"), self.p("uploads/code.py"))   # no fleetbackup entry
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("arrived closed", r.stderr)
        self.assertIn("1 file(s) in a backup tree arrived closed", r.stderr)
        self.assertEqual(entry(self.p("uploads/report.txt"), "user:nobody:"),
                         "user:nobody:r--\t#effective:---")
        self.assertIn("user:nobody:r--", acl(self.p("uploads/code.py")))
        self.assertIn("mask::r--", acl(self.p("uploads/code.py")))
        os.chmod(self.p("uploads/report.txt"), 0o640)   # the documented remedy
        self.assertEqual(entry(self.p("uploads/report.txt"), "user:nobody:"), "user:nobody:r--")

    def test_missing_trees_are_created_and_granted(self):
        with tempfile.TemporaryDirectory() as td:
            box = Box(td)
            h = box.home()
            r = box.run("apply", "nobody", h, "grant")
            self.assertEqual(r.returncode, 0, r.stderr)
            for rel in ("uploads", ".claude/work-products"):
                lines = acl(os.path.join(h, rel))
                self.assertIn("user:nobody:r-x", lines, rel)
                self.assertIn("default:user:nobody:r-x", lines, rel)
            self.assertIn("user:nobody:--x", acl(h))

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
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("user:nobody:r--", acl(self.p("uploads/open.txt")))
        self.assertIn("user:nobody:r--\t#effective:---", acl(self.p("uploads/priv.txt")))

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
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:--x")

    def test_a_foreign_named_entry_on_claude_closes_it(self):
        subprocess.run(["setfacl", "-m", "u:daemon:r-x", self.p(".claude")], check=True)
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(entry(self.p(".claude"), "user:nobody:"), "user:nobody:---")

    def test_claude_without_a_traversable_work_products_is_closed(self):
        os.rename(self.p(".claude/work-products"), os.path.join(self.td, "wp"))
        os.symlink(os.path.join(self.td, "wp"), self.p(".claude/work-products"))
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(entry(self.p(".claude"), "user:nobody:"), "user:nobody:---")
        self.assertIsNone(entry(os.path.join(self.td, "wp"), "user:nobody:"))
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:--x")

    def test_a_home_with_no_granted_tree_stays_closed(self):
        subprocess.run(["setfacl", "-m", "u:daemon:rwx", self.p("uploads")], check=True)
        os.rename(self.p(".claude/work-products"), os.path.join(self.td, "wp"))
        os.symlink(os.path.join(self.td, "wp"), self.p(".claude/work-products"))
        r = self.apply()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:---")

    def test_a_failed_close_holds_the_home(self):
        self.box.extra_env["FBT_FAIL_SETFACL"] = "/devel"
        r = self.apply()
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("HELD", r.stderr)
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:---")
        self.assertIsNone(entry(self.p("uploads"), "user:nobody:"))

    def test_a_vanished_file_costs_one_re_scan_not_a_hold(self):
        self.box.extra_env["FBT_FAIL_ONCE"] = "/devel"
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:--x")
        self.assertEqual(entry(self.p("devel"), "user:nobody:"), "user:nobody:---")

    def test_a_vanished_tree_file_costs_one_re_scan(self):
        self.box.extra_env["FBT_FAIL_ONCE"] = "/uploads/x.txt"
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("user:nobody:r--", acl(self.p("uploads/x.txt")))

    def test_hold_closes_the_home_and_claude_and_opens_nothing(self):
        r = self.apply("hold")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(entry(self.h, "user:nobody:"), "user:nobody:---")
        self.assertEqual(entry(self.p(".claude"), "user:nobody:"), "user:nobody:---")
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
        paths = [self.p(r) for r in ("uploads/x.txt", "devel", ".private", "uploads/sub", ".claude")]
        paths.append(self.h)
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
    """The read-back as fleetbackup, with the OWNER of the probed files as the
    prober (the test user; under root, `nobody` — root reads everything)."""

    def setUp(self):
        self.root_run = os.geteuid() == 0
        self._td = tempfile.TemporaryDirectory(dir="/tmp" if self.root_run else None)
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)
        box = Box(os.path.join(self.td, "box"))
        self.script = os.path.join(self.td, "check.sh")
        shutil.copy(box.script, self.script)
        if self.root_run:
            self.nobody = pwd.getpwnam("nobody")
            os.chmod(self.td, 0o755)
            os.chmod(self.script, 0o644)

    def node(self, name, mode, kind="f"):
        p = os.path.join(self.td, name)
        if kind == "d":
            os.mkdir(p)
        else:
            with open(p, "w") as f:
                f.write("x")
        if self.root_run:
            os.chown(p, self.nobody.pw_uid, self.nobody.pw_gid)
        os.chmod(p, mode)
        self.addCleanup(os.chmod, p, 0o700)
        return p

    def check(self, *records):
        data = "".join("%s\0%s\0%s\0" % r for r in records)
        argv = ["bash", self.script, "check"]
        if self.root_run:
            argv = ["runuser", "-u", "nobody", "--"] + argv
        r = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=60)
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

    def test_a_listable_or_writable_home_is_a_leak_a_traverse_only_one_is_not(self):
        listable, trav = self.node("l", 0o700, "d"), self.node("t", 0o100, "d")
        writable = self.node("w", 0o200, "d")
        out, _ = self.check(("T", "-", listable), ("T", "-", trav), ("T", "-", writable))
        self.assertEqual(out, "leak=2 miss=0 readable=0 private=0")

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

    def raw_check(self, data):
        argv = ["bash", self.script, "check"]
        if self.root_run:
            argv = ["runuser", "-u", "nobody", "--"] + argv
        r = subprocess.run(argv, input=data, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_a_cut_stream_is_a_miss_and_a_last_record_needs_no_nul(self):
        open_f = self.node("of", 0o644)
        self.assertEqual(self.raw_check("C\0-\0"), "leak=0 miss=1 readable=0 private=0")
        self.assertEqual(self.raw_check("C\0-\0%s" % open_f), "leak=1 miss=0 readable=0 private=0")

    def test_odd_names_survive_the_record_stream(self):
        odd = self.node("a b\nc", 0o644)
        out, _ = self.check(("C", "-", odd))
        self.assertEqual(out, "leak=1 miss=0 readable=0 private=0")


def _recs(box, h, *args):
    r = box.run("records", h, *args)
    assert r.returncode == 0, r.stderr
    f = r.stdout.split("\0")[:-1]
    return {(f[i], os.path.relpath(f[i + 2], h)) for i in range(0, len(f), 3)}


class TestRecords(_Tmp):
    def test_records_cover_siblings_and_trees_but_no_symlink(self):
        box = Box(self.td)
        h = box.home()
        make_layout(h)
        recs = _recs(box, h)
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

    def test_siblings_scope_has_no_tree_records(self):
        box = Box(self.td)
        h = box.home()
        make_layout(h)
        kinds = {k for k, _rel in _recs(box, h, "siblings")}
        self.assertEqual(kinds, {"T", "C"})

    def test_an_untraversed_claude_is_checked_as_a_closed_child(self):
        box = Box(self.td)
        h = box.home()
        make_layout(h)
        wp = os.path.join(h, ".claude", "work-products")
        os.rename(wp, os.path.join(self.td, "wp"))
        recs = _recs(box, h)
        self.assertIn(("C", ".claude"), recs)
        self.assertNotIn(("T", ".claude"), recs)


if __name__ == "__main__":
    unittest.main()
