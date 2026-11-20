"""#1387 — the root `refresh` of the installed fleetbackup ACL script.

Runs under stub runuser/getent/passwd/sudo/chage/pkill/stat (never root work,
never a real account) with scripted read-back answers; the account-side
`apply` runs for real on temp files, and every record stream the read-back
receives is saved and asserted.
"""
import datetime
import fcntl
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _fleetbackup_1387_helpers import (  # noqa: E402
    ME, KEYLINE, Box, acl, entry, make_layout)

PRE0 = "leak=0 miss=0 readable=0 private=0"
CLEAN = "leak=0 miss=0 readable=4 private=1"
NONE = "leak=0 miss=0 readable=0 private=0"
LEAK1 = "leak=1 miss=0 readable=0 private=0"


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

    def own_account_box(self):
        """An account that OWNS its home and is not root: the test user, or
        under root (CI) `nobody`, with `daemon` as fleetbackup."""
        if os.geteuid() != 0:
            return self.box()
        box = Box(self.td, fb="daemon", accounts=("nobody",))
        h = box.home("nobody")
        make_layout(h)
        subprocess.run(["chown", "-R", "nobody:nogroup", h], check=True)
        return box, h

    def foreign_owned(self, path):
        """Owned by a non-root user that is not fleetbackup: the test user, or
        under root (CI) `daemon`."""
        if os.geteuid() == 0:
            subprocess.run(["chown", "-R", "daemon:daemon", path], check=True)

    def key(self, box):
        with open(box.keys) as f:
            return f.read()

    def assert_live_key(self, box):
        content = self.key(box)
        m = re.match(r'expiry-time="(\d{12})Z",(.*)\n\Z', content)
        self.assertIsNotNone(m, content)
        self.assertEqual(m.group(2), KEYLINE)
        until = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(
            tzinfo=datetime.timezone.utc)
        left = until - datetime.datetime.now(datetime.timezone.utc)
        self.assertTrue(datetime.timedelta(hours=2, minutes=55) < left
                        < datetime.timedelta(hours=3, minutes=5), left)
        self.assertEqual(stat.S_IMODE(os.stat(box.keys).st_mode), 0o644)

    def assert_withdrawn(self, box, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(box.expiry(), "2")          # our own value, never the admin's 1
        with open(box.mark) as f:
            self.assertEqual(f.read(), "2\n")
        self.assertFalse(os.path.exists(box.keys))
        self.assertIn("pkill -KILL -u %s" % box.fb, box.calls())
        self.assertIn("login WITHDRAWN", r.stderr)

    def assert_not_withdrawn(self, box):
        self.assertFalse(any(c.startswith("chage -E 2") for c in box.calls()))
        self.assert_live_key(box)

    # ------------------------------------------------------------ the clean path
    def test_clean_refresh_applies_reads_back_and_writes_an_expiring_key(self):
        box, h = self.box()
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_live_key(box)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:--x")
        calls = box.calls()
        self.assertTrue(any(c.startswith("%s env -i " % ME) and " apply nobody %s grant" % h in c
                            for c in calls), calls)
        self.assertEqual(sum(c.startswith("nobody ") and c.endswith(" check") for c in calls), 3)
        self.assertIn("readable=4 private=1", r.stdout)

    def test_the_read_back_is_asked_about_exactly_the_right_paths(self):
        box, h = self.box()
        other = box.home("otherdir", stream=False)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        j = lambda *p: os.path.join(h, *p)  # noqa: E731
        pre, post, foreign = box.checked(0), box.checked(1), box.checked(2)
        siblings = {("T", h), ("T", j(".claude")), ("C", j("devel")), ("C", j(".private")),
                    ("C", j(".bashrc")), ("C", j(".claude", "projects")),
                    ("C", j(".claude", "settings.json")),
                    ("C", j(".claude", "airuleset-box-class"))}
        self.assertLessEqual(siblings, pre)
        self.assertEqual({k for k, _p in pre}, {"T", "C"})
        tree = {("D", j("uploads")), ("D", j("uploads", "sub")), ("F", j("uploads", "x.txt")),
                ("F", j("uploads", "p.txt")), ("D", j(".claude", "work-products")),
                ("F", j(".claude", "work-products", "a", "w.md"))}
        self.assertLessEqual(siblings | tree, post)
        self.assertEqual(foreign, {("C", other)})

    def test_account_side_runs_never_hold_the_locks(self):
        box, _h = self.box()
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse([c for c in box.calls() if c.startswith("FD")], box.calls())

    def test_account_side_output_is_prefixed(self):
        box, _h = self.box()
        subprocess.run(["setfacl", "-m", "u:daemon:rwx", os.path.join(_h, "uploads", "sub")], check=True)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = [ln for ln in r.stderr.splitlines() if "tree not granted" in ln]
        self.assertTrue(lines, r.stderr)
        for ln in lines:
            self.assertTrue(ln.startswith("    [%s] " % ME), ln)

    def test_children_open_before_the_refresh_are_reported(self):
        box, _h = self.box()
        box.answers("leak=2 miss=0 readable=0 private=0", CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2 child(ren) were reachable before this refresh", r.stderr)

    def test_missing_files_fail_but_keep_the_key(self):
        box, _h = self.box()
        box.answers(PRE0, "leak=0 miss=3 readable=1 private=0", NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assert_live_key(box)

    def test_account_side_output_cannot_carry_a_terminal_escape(self):
        box, _h = self.box()
        box.extra_env["FBT_ESC_APPLY"] = "1"
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("\x1b", r.stderr)
        self.assertIn("    [%s] ?[2Jforged" % ME, r.stderr)

    def test_account_side_output_is_size_capped(self):
        box, _h = self.box(output_cap_kib=64)
        box.extra_env["FBT_SPAM_APPLY"] = "1"
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout[-500:] + r.stderr[-2000:])
        self.assertIn("wrote too much output", r.stderr)
        self.assertIn("TR_RC=153", box.calls())        # SIGXFSZ: the file stopped at the cap
        self.assertLess(len(r.stderr), 40 * 1024)        # and at most 32 KiB of it is shown
        self.assert_live_key(box)

    def test_over_the_budget_homes_are_closed_by_root_not_granted(self):
        box, h = self.box(budget_s=0)
        box.answers(NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("over the refresh budget", r.stderr)
        self.assertFalse(any(" apply " in c for c in box.calls()))
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")
        self.assertEqual(box.checked(0), {("C", h)})
        self.assert_live_key(box)

    def test_a_check_past_the_budget_still_gets_its_floor(self):
        """Only root can stop fleetbackup's processes: a check past the budget
        keeps a 30 s floor, so a slow check never reads as `unverifiable`."""
        box, h = self.box(budget_s=0)
        box.extra_env["FBT_CHECK_DELAY"] = "3"
        box.answers(NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")
        self.assert_not_withdrawn(box)

    def test_an_incomplete_record_stream_counts_as_open(self):
        box, h = self.box()
        box.extra_env["FBT_FAIL_FIND"] = "/uploads"
        box.answers(PRE0, CLEAN, NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("did not run", r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")

    def test_a_key_that_cannot_be_written_fails_the_refresh(self):
        """A directory planted as authorized_keys: `mv -f` would move the key
        INTO it and report success."""
        box, _h = self.box()
        os.makedirs(os.path.join(box.keys, "x"))
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(os.listdir(box.keys), ["x"])
        self.assertNotIn("key valid until", r.stdout)

    def test_a_tree_that_is_not_a_plain_dir_fails(self):
        box, h = self.box()
        os.rename(os.path.join(h, "uploads"), os.path.join(self.td, "up"))
        os.symlink(os.path.join(self.td, "up"), os.path.join(h, "uploads"))
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is not a plain directory", r.stderr)

    # -------------------------------------------- a leak, and who may cause what
    def test_a_resolved_leak_closes_the_home_and_keeps_the_key(self):
        box, h = self.box()
        box.answers(PRE0, "leak=2 miss=0 readable=4 private=0", NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")
        self.assertEqual(box.checked(2), {("C", h)})
        self.assertIn("root-setfacl -P -n -m u:nobody:--- -- %s" % h, box.calls())
        # round 3: no account-side run in the leak path — nothing a tenant can stall or refuse
        self.assertFalse([c for c in box.calls() if c.endswith(" hold")], box.calls())
        self.assert_not_withdrawn(box)

    def test_data_its_owner_reopened_fails_only_that_account(self):
        box, _h = self.box()
        box.answers(PRE0, "leak=1 miss=0 readable=4 private=0", LEAK1, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("its own doing; failed for", r.stderr)
        self.assert_not_withdrawn(box)

    def test_a_tenant_that_blocks_root_on_its_own_home_fails_alone(self):
        """Rounds 2-3 🔴: a tenant that fills its xattr space (root's deny fails)
        or flips its ACL between root's deny and the read-back — with OUR grant
        on its home — exposes only its OWN data and never withdraws the login."""
        box, h = self.own_account_box()
        box.extra_env["FBT_FAIL_SETFACL_LAST"] = h
        box.answers(PRE0, "leak=1 miss=0 readable=4 private=0", LEAK1, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("root could not close", r.stderr)
        self.assertIn("its own doing; failed for", r.stderr)
        self.assertIn("--x", entry(h, "user:%s:" % box.fb))     # our grant was there
        self.assert_not_withdrawn(box)

    def test_a_home_level_fault_never_withdraws_the_login(self):
        """Round 4 🔴: every per-path attribution of a withdrawal was
        tenant-triggerable (a FUSE mount root cannot stat, a child of another
        account with full xattrs, an ACL flip). Every inode that carries a
        grant of ours is the account's own, so a home that root cannot close
        fails that account — whatever else failed with it."""
        for close_answer in (LEAK1, "", "garbage"):
            with self.subTest(close_answer=close_answer), tempfile.TemporaryDirectory() as td:
                box = Box(td, fb="daemon", accounts=("nobody",))
                h = box.home("nobody")
                make_layout(h)
                box.extra_env["FBT_FAIL_SETFACL"] = "/devel"      # a child root cannot close
                box.extra_env["FBT_FAIL_SETFACL_LAST"] = h         # and the home itself
                box.answers(PRE0, LEAK1, close_answer, NONE)
                r = box.run()
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("HELD", r.stderr)
                self.assertIn("failed for nobody only", r.stderr)
                self.assertNotIn("UNRESOLVED", r.stderr)
                self.assert_not_withdrawn(box)

    def test_a_read_back_that_did_not_run_counts_as_open(self):
        box, h = self.box()
        box.answers(PRE0, "", NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("did not run", r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")

    def test_an_unparseable_read_back_counts_as_open(self):
        box, h = self.box()
        box.answers(PRE0, "leak=x miss=0 readable=1 private=0", NONE, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")

    def test_a_stalled_account_side_run_times_out_and_the_refresh_goes_on(self):
        box, _h = self.box(account_timeout_s=2)
        box.extra_env["FBT_STALL_APPLY"] = "1"
        box.answers(PRE0, CLEAN, NONE)
        t0 = time.monotonic()
        r = box.run()
        self.assertLess(time.monotonic() - t0, 25)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("timed out after 2s", r.stderr)
        self.assert_live_key(box)

    # ------------------------------------------------- identity and withdrawal
    def test_an_unlocked_password_withdraws_and_touches_no_home(self):
        box, h = self.box(pw_status="P")
        with open(box.keys, "w") as f:      # a live key from an earlier clean refresh
            f.write(KEYLINE + "\n")
        r = box.run()
        self.assert_withdrawn(box, r)
        self.assertFalse(any(c.startswith(("nobody ", ME + " ")) for c in box.calls()))
        self.assertIsNone(entry(h, "user:nobody:"))

    def test_sudo_rights_withdraw(self):
        box, _h = self.box(sudo_out="User nobody may run the following commands")
        self.assert_withdrawn(box, box.run())

    def test_a_supplementary_group_withdraws(self):
        box, _h = self.box()
        box.extra_env["FBT_ID_G"] = "1 27"
        self.assert_withdrawn(box, box.run())

    def test_a_foreign_primary_group_withdraws(self):
        box, _h = self.box()
        box.extra_env["FBT_ID_GN"] = "users"
        self.assert_withdrawn(box, box.run())

    def test_an_unmanaged_second_key_file_withdraws_and_is_removed(self):
        box, _h = self.box()
        k2 = os.path.join(box.ssh, "authorized_keys2")
        with open(k2, "w") as f:
            f.write("ssh-ed25519 AAAAother x\n")
        self.assert_withdrawn(box, box.run())
        self.assertFalse(os.path.exists(k2))

    def test_an_unsafe_key_dir_withdraws_by_expiry(self):
        box, _h = self.box()
        with open(box.keys, "w") as f:
            f.write(KEYLINE + "\n")
        os.chmod(box.ssh, 0o775)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(box.expiry(), "2")
        self.assertIn("login WITHDRAWN", r.stderr)
        self.assertEqual(self.key(box), KEYLINE + "\n")   # an unsafe dir is never written to

    def test_a_withdrawal_that_cannot_be_proven_says_so(self):
        box, _h = self.box(pw_status="P")
        with open(box.keys, "w") as f:
            f.write(KEYLINE + "\n")
        os.chmod(box.ssh, 0o775)
        box.extra_env["FBT_FAIL_CHAGE"] = "1"
        r = box.run()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("WITHDRAWAL FAILED", r.stderr)
        self.assertNotIn("login WITHDRAWN", r.stderr)

    def test_a_missing_tool_withdraws(self):
        for tool in ("setfacl", "getfacl", "runuser", "timeout", "python3"):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as td:
                box = Box(td)
                make_layout(box.home())
                sysbin = os.path.join(td, "sysbin")
                os.makedirs(sysbin)
                for d in (box.bindir, "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
                    for name in os.listdir(d):
                        if name == tool or os.path.exists(os.path.join(sysbin, name)):
                            continue
                        os.symlink(os.path.join(d, name), os.path.join(sysbin, name))
                env = box.env()
                env["PATH"] = sysbin
                r = subprocess.run(["bash", box.script], env=env, capture_output=True, text=True,
                                   timeout=60)
                self.assertIn("%s missing" % tool, r.stderr)
                self.assert_withdrawn(box, r)

    def test_a_missing_chage_cannot_withdraw_by_expiry_and_says_so(self):
        """chage is the expiry tool: without it the key file alone is withdrawn."""
        with tempfile.TemporaryDirectory() as td:
            box = Box(td)
            make_layout(box.home())
            with open(box.keys, "w") as f:
                f.write(KEYLINE + "\n")
            os.chmod(box.ssh, 0o775)                    # and the key cannot be removed
            sysbin = os.path.join(td, "sysbin")
            os.makedirs(sysbin)
            for d in (box.bindir, "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
                for name in os.listdir(d):
                    if name == "chage" or os.path.exists(os.path.join(sysbin, name)):
                        continue
                    os.symlink(os.path.join(d, name), os.path.join(sysbin, name))
            env = box.env()
            env["PATH"] = sysbin
            r = subprocess.run(["bash", box.script], env=env, capture_output=True, text=True,
                               timeout=60)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertIn("chage missing", r.stderr)
            self.assertIn("WITHDRAWAL FAILED", r.stderr)

    def test_a_clean_refresh_lifts_only_its_own_expiry(self):
        box, _h = self.box(expiry="2")
        with open(box.mark, "w") as f:         # our earlier withdrawal
            f.write("2\n")
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(box.expiry(), "")
        self.assertFalse(os.path.exists(box.mark))
        self.assertNotIn("set by someone else", r.stderr)
        self.assert_live_key(box)
        # an admin's lock (the documented `usermod --expiredate 1`, or a date),
        # with no marker of ours, or with a stale one the admin's change outlived;
        # an expired one fails the refresh LOUD (the login stays blocked)
        for expiry, marker, expired in (("1", False, True), ("20500", False, True), ("1", True, True),
                                        ("2", False, True), ("99999", False, False)):
            with self.subTest(expiry=expiry, marker=marker), tempfile.TemporaryDirectory() as td:
                box2 = Box(td, expiry=expiry)
                make_layout(box2.home())
                if marker:
                    with open(box2.mark, "w") as f:
                        f.write("2\n")
                box2.answers(PRE0, CLEAN, NONE)
                r = box2.run()
                self.assertEqual(box2.expiry(), expiry)
                self.assertIn("set by someone else", r.stderr)
                self.assertFalse(os.path.exists(box2.mark), "a stale marker is forgotten")
                self.assertEqual(r.returncode, 1 if expired else 0, r.stderr)
                self.assertEqual("EXPIRED" in r.stderr, expired, r.stderr)

    def test_the_withdrawal_marker_is_written_before_the_expiry(self):
        """Round 4 🟡: chage first + marker second left our own `2` unmarked
        (a crash, a failed write), and every later refresh reported success
        while the login stayed blocked."""
        box, _h = self.box(pw_status="P")
        r = box.run()
        self.assert_withdrawn(box, r)
        self.assertIn("chage -E 2 nobody marker=yes", box.calls())

    def test_a_withdrawal_without_a_marker_says_so_and_stays_loud(self):
        box, _h = self.box(pw_status="P")
        os.chmod(box.fbhome, 0o775)                 # the marker's dir is not safe: no marker
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no withdrawal marker could be written", r.stderr)
        self.assertEqual(box.expiry(), "2")
        self.assertFalse(os.path.exists(box.mark))
        os.chmod(box.fbhome, 0o755)
        with tempfile.TemporaryDirectory() as td:
            box2 = Box(td, expiry="2")              # the next refresh, everything else clean
            make_layout(box2.home())
            box2.answers(PRE0, CLEAN, NONE)
            r = box2.run()
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("EXPIRED", r.stderr)

    def test_a_withdrawal_over_an_admin_expiry_never_marks_it_ours(self):
        box, _h = self.box(pw_status="P", expiry="1")
        r = box.run()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("login WITHDRAWN", r.stderr)
        self.assertEqual(box.expiry(), "1")
        self.assertFalse(os.path.exists(box.mark))
        self.assertFalse(any(c.startswith("chage") for c in box.calls()), box.calls())

    def test_a_key_with_another_mode_is_rewritten(self):
        box, _h = self.box()
        box.answers(PRE0, CLEAN, NONE)
        self.assertEqual(box.run().returncode, 0)
        os.chmod(box.keys, 0o600)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_live_key(box)

    def test_a_foreign_fleetbackup_account_is_left_alone(self):
        box, h = self.box(fb_sig_home="/home/fleetbackup")
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped", r.stdout)
        self.assertEqual(box.calls(), [])
        self.assertFalse(os.path.exists(box.keys))
        self.assertIsNone(entry(h, "user:nobody:"))

    # ------------------------------------------------------------------- the lock
    def test_a_busy_refresh_lock_touches_nothing(self):
        box, h = self.box()
        with open(box.lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = box.run()
        self.assertEqual(r.returncode, 75, r.stdout + r.stderr)
        self.assertEqual(box.calls(), [])
        self.assertIsNone(entry(h, "user:nobody:"))

    def test_the_refresh_lock_is_root_only(self):
        box, _h = self.box()
        old = os.umask(0o022)
        try:
            open(box.lock, "w").close()        # a lock file others could open
        finally:
            os.umask(old)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(stat.S_IMODE(os.stat(box.lock).st_mode), 0o600)

    # ------------------------------------------- non-stream and refused homes
    def test_every_non_stream_entry_gets_the_deny_once(self):
        box, _h = self.box()
        open_dir = box.home("otherdir", stream=False)
        os.chmod(open_dir, 0o755)
        stale = box.home("former", stream=False)     # left the stream set, kept its --x
        os.chmod(stale, 0o750)
        subprocess.run(["setfacl", "-m", "u:nobody:--x", stale], check=True)
        dflt = box.home("dflt", stream=False)        # a DEFAULT deny alone closes nothing
        os.chmod(dflt, 0o755)
        subprocess.run(["setfacl", "-d", "-m", "u:nobody:---", dflt], check=True)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for d in (open_dir, stale, dflt):
            self.assertIn("user:nobody:---", acl(d), d)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("closed ", r.stdout)

    def test_a_non_stream_entry_its_owner_reopens_fails_without_withdrawal(self):
        box, _h = self.box()
        self.foreign_owned(box.home("otherdir", stream=False))
        box.answers(PRE0, CLEAN, LEAK1)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assert_not_withdrawn(box)

    def test_a_non_stream_entry_open_only_by_its_owners_choice_fails_alone(self):
        box, _h = self.own_account_box()
        other = box.home("otherdir", stream=False)
        if os.geteuid() == 0:
            subprocess.run(["chown", "-R", "nobody:nogroup", other], check=True)
        box.extra_env["FBT_FAIL_SETFACL_LAST"] = other
        box.answers(PRE0, CLEAN, LEAK1)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("still reachable", r.stderr)
        self.assert_not_withdrawn(box)

    def test_a_non_stream_entry_that_cannot_be_closed_fails_even_unreachable(self):
        box, _h = self.box()
        other = box.home("otherdir", stream=False)
        self.foreign_owned(other)
        box.extra_env["FBT_FAIL_SETFACL_LAST"] = other
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("could not close", r.stderr)
        self.assert_not_withdrawn(box)

    def test_a_non_stream_entry_that_stays_reachable_never_withdraws(self):
        box, _h = self.box()
        other = box.home("otherdir", stream=False)
        subprocess.run(["setfacl", "-m", "u:nobody:r-x", other], check=True)
        box.extra_env["FBT_FAIL_SETFACL_LAST"] = other
        box.answers(PRE0, CLEAN, LEAK1)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("still reachable", r.stderr)
        self.assert_not_withdrawn(box)
        self.assertEqual(len(os.listdir(box.checks)), 3)    # one read-back of /home, no second

    def test_a_child_the_account_does_not_own_is_closed_by_root(self):
        # the stream account is `nobody` (the files are the test user's, so
        # every child is "not owned by the account"); fleetbackup = daemon
        box = Box(self.td, fb="daemon", accounts=("nobody",))
        h = box.home("nobody")
        make_layout(h)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        denied = " ".join(c for c in box.calls() if c.startswith("root-setfacl "))
        for rel in ("devel", ".bashrc", ".claude/settings.json"):
            self.assertEqual(entry(os.path.join(h, rel), "user:daemon:"), "user:daemon:---", rel)
            self.assertIn(" %s" % os.path.join(h, rel), denied, rel)
        self.assertEqual(entry(h, "user:daemon:"), "user:daemon:--x")
        # root only ever denies, batched: no other root-side setfacl ran
        root_calls = [c for c in box.calls() if c.startswith("root-setfacl ")]
        self.assertTrue(all(c.startswith("root-setfacl -P -n -m u:daemon:--- -- ") for c in root_calls),
                        root_calls)

    def test_root_closes_many_foreign_children_in_a_few_calls(self):
        """Round 4 🟡: one root setfacl per child let a second account (or
        hardlinks) stall the refresh past its budget."""
        box = Box(self.td, fb="daemon", accounts=("nobody",))
        h = box.home("nobody")
        make_layout(h)
        for i in range(3000):
            open(os.path.join(h, "f%04d" % i), "w").close()
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = [c for c in box.calls() if c.startswith("root-setfacl ")]
        self.assertLess(len(calls), 10, len(calls))
        self.assertEqual(entry(os.path.join(h, "f2999"), "user:daemon:"), "user:daemon:---")

    def test_a_child_root_cannot_close_holds_the_home(self):
        box = Box(self.td, fb="daemon", accounts=("nobody",))
        h = box.home("nobody")
        make_layout(h)
        box.extra_env["FBT_FAIL_SETFACL"] = "/devel"
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("HELD", r.stderr)
        self.assertEqual(entry(h, "user:daemon:"), "user:daemon:---")

    def test_a_home_that_belongs_to_no_such_account_is_treated_as_foreign(self):
        box, _h = self.box()
        ghost = box.home("ghost")
        os.chmod(ghost, 0o755)
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is not the home of an account named ghost", r.stderr)
        self.assertFalse(any(" apply nobody %s " % ghost in c for c in box.calls()))
        self.assertEqual(entry(ghost, "user:nobody:"), "user:nobody:---")

    def test_a_symlinked_home_is_not_granted(self):
        box, _h = self.box()
        real = os.path.join(self.td, "real-lnk")
        os.makedirs(os.path.join(real, ".claude"))
        with open(os.path.join(real, ".claude", "airuleset-box-class"), "w") as f:
            f.write("shared-stream\n")
        os.symlink(real, os.path.join(box.root, "lnk"))
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("is a symlink", r.stderr)
        self.assertIsNone(entry(real, "user:nobody:"))

    def test_a_symlinked_marker_is_not_a_stream(self):
        box, _h = self.box()
        other = box.home("linked", stream=False)
        target = os.path.join(self.td, "marker")
        with open(target, "w") as f:
            f.write("shared-stream\n")
        os.symlink(target, os.path.join(other, ".claude", "airuleset-box-class"))
        box.answers(PRE0, CLEAN, NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(any(" apply nobody %s " % other in c for c in box.calls()))
        self.assertEqual(entry(other, "user:nobody:"), "user:nobody:---")

    def test_a_paused_account_is_never_granted(self):
        box, h = self.box(paused=(ME,))
        box.answers(NONE)
        r = box.run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("paused", r.stdout)
        self.assertFalse(any(" apply " in c for c in box.calls()))
        self.assertEqual(entry(h, "user:nobody:"), "user:nobody:---")
        self.assertIsNone(entry(os.path.join(h, "uploads"), "user:nobody:"))

    def test_a_fifo_marker_cannot_hang_the_refresh(self):
        box, _h = self.box()
        other = box.home("fifo-acct", stream=False)
        os.mkfifo(os.path.join(other, ".claude", "airuleset-box-class"))
        box.answers(PRE0, CLEAN, NONE)
        t0 = time.monotonic()
        r = box.run()
        self.assertLess(time.monotonic() - t0, 60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(entry(other, "user:nobody:"), "user:nobody:---")


if __name__ == "__main__":
    unittest.main()
