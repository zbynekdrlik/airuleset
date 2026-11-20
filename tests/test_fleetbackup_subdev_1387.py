"""#1387 — subdev's read-only fleetbackup login: the key line, the push leg,
and its wiring into the root apply + the hourly quota-ceiling unit.

The push leg is executed against stub useradd/getent/passwd/usermod/install
(never root, never a real account); the installed ACL script is a stub here
(it has its own suite, test_fleetbackup_acl_1387.py).
"""
import fcntl
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli_controller_fleetbackup as cfb  # noqa: E402
import cli_resource_guards as crg  # noqa: E402
import cli_resource_guards_fleetbackup as fbm  # noqa: E402
from _fleetbackup_1387_helpers import write_exe  # noqa: E402

BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAIMzhgslnCx56OLBPtqRStknHDCCDlpWvLoNQkXVB5B3l"


class TestKeyAndAccount(unittest.TestCase):
    def test_the_key_line_is_exactly_the_decided_one(self):
        self.assertEqual(
            fbm.authorized_key_line(),
            'from="100.104.8.125",restrict,command="/usr/bin/rrsync -ro /home" '
            "ssh-ed25519 %s fleet-backup-manager@dev1" % BLOB)

    def test_one_copy_of_the_manager_key(self):
        self.assertIn(cfb.MANAGER_KEY, cfb.FLEETBACKUP["authorized_key"])
        self.assertIn(cfb.MANAGER_KEY, fbm.authorized_key_line())

    def test_account_shape(self):
        self.assertEqual(fbm.FLEETBACKUP_USER, "fleetbackup")
        self.assertEqual(fbm.FLEETBACKUP_HOME, "/var/lib/fleetbackup")
        self.assertEqual(fbm.FLEETBACKUP_SHELL, "/bin/sh")
        self.assertFalse(fbm.FLEETBACKUP_HOME.startswith(fbm.RRSYNC_ROOT + "/"),
                         "the account's own home must stay outside the rrsync root")

    def test_installed_script_carries_the_production_values(self):
        s = fbm.render_fleetbackup_acl_script()
        self.assertIn("FB=fleetbackup\n", s)
        self.assertIn("FB_SIG=/var/lib/fleetbackup:/bin/sh\n", s)
        self.assertIn("FB_SSH=/var/lib/fleetbackup/.ssh\n", s)
        self.assertIn("FB_KEY_UID=0\n", s)
        self.assertIn("FB_ROOT=/home\n", s)
        self.assertIn("FB_KEY=" + fbm.shlex.quote(fbm.authorized_key_line()) + "\n", s)
        self.assertNotIn("@@", s)


def _functions(script):
    """name -> body of every top-level `name() {` ... `}` function."""
    out, name, body = {}, None, []
    for ln in script.splitlines():
        m = re.match(r"^([a-z_]+)\(\) \{", ln)
        if m and ln.rstrip().endswith("}"):     # a one-line function
            out[m.group(1)] = ln
            continue
        if m:
            name, body = m.group(1), []
            continue
        if name and ln == "}":
            out[name] = "\n".join(body)
            name = None
        elif name:
            body.append(ln)
    return out


class TestScriptInvariants(unittest.TestCase):
    def setUp(self):
        self.s = fbm.render_fleetbackup_acl_script()
        self.fn = _functions(self.s)

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n"], input=self.s, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_root_side_never_runs_setfacl_itself(self):
        """Decided: every setfacl runs AS the account. Only the account-side
        functions (reached through `runuser -u <account>`) may call it."""
        account_side = {"fb_traverse", "fb_tree", "fb_apply"}
        call = re.compile(r"\bsetfacl\s+-")
        self.assertGreater(len(self.fn), 15)
        for name, body in self.fn.items():
            if name not in account_side:
                self.assertIsNone(call.search(body), name)
        for name in account_side:
            self.assertIsNotNone(call.search(self.fn[name]), name)
        dispatch = self.s[self.s.index('case "${1:-refresh}"'):]
        self.assertIn('acl-deny) cd /; setfacl -n -m "u:$2:---"', dispatch)
        self.assertIn('runuser -u "$u" -- env -i', self.fn["fb_as"])

    def test_default_acl_only_on_tree_dirs(self):
        for name, body in self.fn.items():
            if name != "fb_tree":
                self.assertNotIn("d:u:", body, name)
        self.assertIn("d:u:$1:r-x,d:g::---,d:o::---", self.fn["fb_tree"])

    def test_files_never_get_their_mask_recalculated(self):
        self.assertIn('-type f -exec setfacl -n -m "u:$1:r--"', self.fn["fb_tree"])
        closes = re.findall(r'setfacl (-\S+ )*-m "u:\$fb:---"', self.fn["fb_apply"])
        self.assertEqual(len(closes), 3)
        self.assertNotIn('setfacl -m "u:$fb:---"', self.fn["fb_apply"])

    def test_symlinks_are_never_walked(self):
        self.assertIn("-xdev", self.fn["fb_tree_records"])
        for name in ("fb_apply", "fb_records", "fb_unclosable"):
            self.assertIn("! -type l", self.fn[name], name)
        self.assertNotIn("find -L", self.s)

    def test_the_key_is_written_only_by_the_refresh_after_its_checks(self):
        refresh = self.fn["fb_refresh"]
        self.assertLess(refresh.index("fb_identity"), refresh.index('fb_key_write "$FB_KEY"'))
        self.assertLess(refresh.index("fb_foreign_homes"), refresh.index('fb_key_write "$FB_KEY"'))
        self.assertLess(refresh.index("unresolved"), refresh.index('fb_key_write "$FB_KEY"'))


def _push_box(td, passwd_line=None, pw="L", rrsync=True, acl_rc=0):
    """Stubs for the push leg; returns (script_path, env, paths)."""
    bindir = os.path.join(td, "bin")
    os.makedirs(bindir)
    log = os.path.join(td, "log")
    pwfile = os.path.join(td, "passwd")
    home = os.path.join(td, "fbhome")
    sig_line = "fbt:x:999:999::%s:/bin/sh" % home
    with open(pwfile, "w") as f:
        f.write((passwd_line if passwd_line is not None else sig_line) + "\n"
                if passwd_line != "" else "")
    write_exe(os.path.join(bindir, "getent"),
              'grep "^$2:" %s || exit 2' % pwfile)
    write_exe(os.path.join(bindir, "useradd"),
              'echo "useradd $*" >> %s; echo %r >> %s' % (log, sig_line, pwfile))
    write_exe(os.path.join(bindir, "passwd"), 'echo "$2 %s 01/01/2026"' % pw)
    write_exe(os.path.join(bindir, "usermod"), 'echo "usermod $*" >> %s' % log)
    write_exe(os.path.join(bindir, "install"),
              'echo "install $*" >> %s; mkdir -p "${@: -1}"' % log)
    acl = os.path.join(td, "acl.sh")
    write_exe(acl, 'echo "acl-run $*" >> %s; exit %d' % (log, acl_rc))
    rr = os.path.join(td, "rrsync")
    if rrsync:
        write_exe(rr, "exit 0")
    lock = os.path.join(td, "lock")
    block = fbm.render_fleetbackup_push_block(lock, lock_wait_s=1, acl_script=acl,
                                              user="fbt", home=home, rrsync=rr)
    script = os.path.join(td, "push.sh")
    with open(script, "w") as f:
        f.write("set -euo pipefail\n" + block + "\n"
                'echo after > %s\n' % os.path.join(td, "after")
                + fbm.FLEETBACKUP_DEFERRED_EXIT + "\n")
    env = dict(os.environ)
    env["PATH"] = bindir + ":" + env.get("PATH", "")
    return script, env, {"log": log, "home": home, "lock": lock, "after": os.path.join(td, "after")}


def _run(script, env):
    return subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=60)


def _log(p):
    return open(p["log"]).read().splitlines() if os.path.exists(p["log"]) else []


class TestPushLeg(unittest.TestCase):
    def test_fresh_box_creates_the_system_account_then_refreshes(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, passwd_line="")
            r = _run(script, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            log = _log(p)
            self.assertIn("useradd --system --home-dir %s --no-create-home --shell /bin/sh "
                          "--comment %s fbt" % (p["home"], fbm.FLEETBACKUP_GECOS), log)
            self.assertIn("install -d -o root -g root -m 0755 %s" % p["home"], log)
            self.assertIn("install -d -o root -g root -m 0755 %s/.ssh" % p["home"], log)
            self.assertEqual(log[-1], "acl-run ")

    def test_existing_account_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td)
            r = _run(script, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(any(ln.startswith("useradd") for ln in _log(p)))

    def test_a_foreign_account_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, passwd_line="fbt:x:1003:1003::/home/fbt:/bin/bash")
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("not airuleset's", r.stderr)
            self.assertFalse(any(ln.startswith(("useradd", "usermod", "install", "acl-run"))
                                 for ln in _log(p)))

    def test_an_unlocked_password_is_locked(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, pw="P")
            r = _run(script, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("usermod -L fbt", _log(p))

    def test_missing_rrsync_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, passwd_line="", rrsync=False)
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("rrsync missing", r.stderr)
            self.assertEqual(_log(p), [])

    def test_a_symlinked_home_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td)
            os.symlink(td, p["home"])
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertFalse(any(ln.startswith("acl-run") for ln in _log(p)))

    def test_a_busy_quota_lock_is_loud_and_deferred(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td)
            with open(p["lock"], "w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                r = _run(script, env)
            self.assertEqual(r.returncode, fbm.FLEETBACKUP_LOCK_BUSY_RC, r.stdout + r.stderr)
            self.assertIn("quota lock busy", r.stderr)
            self.assertTrue(os.path.exists(p["after"]), "a later guard step was skipped")

    def test_a_failed_refresh_never_skips_a_later_step(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, acl_rc=2)
            r = _run(script, env)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("FLEETBACKUP FAILED rc=2", r.stderr)
            self.assertTrue(os.path.exists(p["after"]), "a later guard step was skipped")


class TestWiring(unittest.TestCase):
    def test_the_acl_script_is_installed_before_daemon_reload(self):
        files = dict(crg.guard_files())
        self.assertEqual(files[fbm.FLEETBACKUP_ACL_SCRIPT_PATH], fbm.render_fleetbackup_acl_script())
        s = crg.build_apply_script()
        self.assertLess(s.index("_install %s" % fbm.FLEETBACKUP_ACL_SCRIPT_PATH),
                        s.index("systemctl daemon-reload"))

    def test_push_leg_runs_after_quota_under_its_lock_and_exits_last(self):
        s = crg.build_apply_script()
        leg = fbm.render_fleetbackup_push_block(crg.QUOTA_LOCK_PATH)
        self.assertEqual(s.count(leg), 1)
        self.assertIn("flock -o -E 75 -w 60 %s /bin/bash %s"
                      % (crg.QUOTA_LOCK_PATH, fbm.FLEETBACKUP_ACL_SCRIPT_PATH), leg)
        self.assertLess(s.index("per-user ext4 disk quota"), s.index(leg))
        self.assertLess(s.index(leg), s.index("shared Playwright browsers"))
        self.assertTrue(s.rstrip("\n").endswith(fbm.FLEETBACKUP_DEFERRED_EXIT))

    def test_hourly_ceiling_runs_the_refresh_under_the_lock(self):
        s = crg.render_quota_ceiling_script()
        call = "/bin/bash %s 9>&- || fb_rc=$?" % fbm.FLEETBACKUP_ACL_SCRIPT_PATH
        self.assertIn(call, s)
        self.assertLess(s.index("flock -w"), s.index(call))
        self.assertLess(s.index("per-user usage-aware quota limits"), s.index(call))
        self.assertIn('[ "$fb_rc" -ne 0 ]; then exit 1', s)

    def test_hourly_refresh_failure_fails_the_unit(self):
        with tempfile.TemporaryDirectory() as td:
            acl = os.path.join(td, "acl.sh")
            write_exe(acl, "exit 2")
            body = fbm.render_fleetbackup_hourly_call(acl) + \
                'if [ "$fb_rc" -ne 0 ]; then exit 1; fi\nexit 0\n'
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1, r.stderr)
            os.unlink(acl)
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("missing", r.stderr)


if __name__ == "__main__":
    unittest.main()
