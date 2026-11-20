"""#1387 — subdev's read-only fleetbackup login: the key line, the forced-
command wrapper, the push leg, and its wiring into the root apply + the hourly
quota-ceiling unit.

The push leg is executed against stub useradd/getent/passwd/usermod/install
(never root, never a real account); the installed ACL script is a stub here
(it has its own suite, test_fleetbackup_acl_1387.py).
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cli_controller_fleetbackup as cfb  # noqa: E402
import cli_resource_guards as crg  # noqa: E402
import cli_resource_guards_fleetbackup as fbm  # noqa: E402
import cli_resource_guards_quota as crgq  # noqa: E402
from _fleetbackup_1387_helpers import write_exe  # noqa: E402

BLOB = "AAAAC3NzaC1lZDI1NTE5AAAAIMzhgslnCx56OLBPtqRStknHDCCDlpWvLoNQkXVB5B3l"


class TestKeyAndAccount(unittest.TestCase):
    def test_the_key_line_is_dev1_only_restricted_and_forced(self):
        self.assertEqual(
            fbm.authorized_key_line(),
            'from="100.104.8.125",restrict,command="/bin/sh '
            '/usr/local/lib/airuleset/fleetbackup-rrsync" '
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
        s = fbm.render_fleetbackup_acl_script(paused=("simap1",))
        self.assertIn("FB=fleetbackup\n", s)
        self.assertIn("FB_SIG=/var/lib/fleetbackup:/bin/sh\n", s)
        self.assertIn("FB_SSH=/var/lib/fleetbackup/.ssh\n", s)
        self.assertIn("FB_KEY_UID=0\n", s)
        self.assertIn("FB_ROOT=/home\n", s)
        self.assertIn("FB_KEY_TTL_H=3\n", s)
        self.assertIn("FB_LOCK=/run/airuleset-fleetbackup.lock\n", s)
        self.assertIn("FB_SKIP=simap1\n", s)
        self.assertIn("FB_KEY=" + fbm.shlex.quote(fbm.authorized_key_line()) + "\n", s)
        self.assertNotIn("@@", s)

    def test_paused_accounts_come_from_the_fleet_config(self):
        import cli_disk_guard_root
        self.assertEqual(tuple(fbm._paused_accounts()), tuple(cli_disk_guard_root._paused_accounts()))


class TestRrsyncWrapper(unittest.TestCase):
    def _run(self, command):
        with tempfile.TemporaryDirectory() as td:
            log = os.path.join(td, "log")
            rr = os.path.join(td, "rrsync")
            write_exe(rr, 'echo "rrsync $*" >> %s' % log)
            w = os.path.join(td, "w.sh")
            with open(w, "w") as f:
                f.write(fbm.render_fleetbackup_rrsync_wrapper(rrsync=rr, root="/home"))
            env = dict(os.environ, SSH_ORIGINAL_COMMAND=command)
            r = subprocess.run(["/bin/sh", w], env=env, capture_output=True, text=True)
            return r, (open(log).read() if os.path.exists(log) else "")

    def test_a_plain_pull_reaches_rrsync_read_only(self):
        r, log = self._run("rsync --server --sender -vlogDtpre.iLsfxCIvu . david3/uploads/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log, "rrsync -ro /home\n")

    def test_symlink_following_options_never_reach_rrsync(self):
        for opt in fbm.FLEETBACKUP_REFUSED_OPTIONS:
            r, log = self._run("rsync --server --sender -vlogDtpre.iLsfxCIvu %s . u/uploads/" % opt)
            self.assertEqual(r.returncode, 1, opt)
            self.assertEqual(log, "", opt)
            self.assertIn("refused", r.stderr)

    def test_the_client_text_is_never_evaluated(self):
        r, log = self._run('rsync --server --sender $(touch /nonexistent/x) `id` ; id . u/')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(log, "rrsync -ro /home\n")


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
        self.s = fbm.render_fleetbackup_acl_script(paused=())
        self.fn = _functions(self.s)

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n"], input=self.s, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_root_side_never_grants(self):
        """Every GRANT runs as the account; the only root setfacl is the
        deny-only fb_root_deny (-P: a swapped symlink is skipped, -n: the mask
        is never recalculated, `---`: nothing can open)."""
        account_side = {"fb_traverse", "fb_tree", "fb_close", "fb_close_kids", "fb_apply"}
        call = re.compile(r"\bsetfacl\s+-")
        self.assertGreater(len(self.fn), 25)
        for name, body in self.fn.items():
            if name not in account_side | {"fb_root_deny"}:
                self.assertIsNone(call.search(body), name)
        self.assertEqual(self.fn["fb_root_deny"].strip().splitlines()[-1].strip(),
                         'setfacl -P -n -m "u:$FB:---" -- "$1"')
        self.assertIn('runuser -u "$u" -- env -i', self.fn["fb_as"])

    def test_every_setfacl_is_physical(self):
        calls = re.findall(r"\bsetfacl( -[A-Za-z]+)+", self.s)
        self.assertGreaterEqual(len(calls), 8)
        for m in re.finditer(r"\bsetfacl((?: -[A-Za-z]+)+)", self.s):
            self.assertIn("-P", m.group(1).split(), m.group(0))

    def test_every_find_tolerates_a_vanishing_file(self):
        finds = re.findall(r"\bfind \"[^\"]+\"[^\n]*", self.s)
        self.assertGreaterEqual(len(finds), 8)
        for f in finds:
            self.assertIn("-ignore_readdir_race", f)

    def test_trees_never_cross_a_filesystem(self):
        for name in ("fb_tree", "fb_tree_records"):
            for f in re.findall(r"\bfind [^\n]*", self.fn[name]):
                self.assertIn("-xdev", f, name)

    def test_default_acls(self):
        self.assertIn('setfacl -P -d -m "u:$fb:---,g::---,o::---" -- "$h"', self.fn["fb_apply"])
        self.assertIn("d:u:$1:r-x,d:g::---,d:o::---", self.fn["fb_tree"])

    def test_files_never_get_their_mask_recalculated(self):
        self.assertIn('-type f -exec setfacl -P -n -m "u:$1:r--"', self.fn["fb_tree"])
        self.assertIn('setfacl -P -n -m "u:$fb:---"', self.fn["fb_close"])
        self.assertIn('setfacl -P -n -m "u:$1:---"', self.fn["fb_close_kids"])

    def test_symlinks_are_never_walked(self):
        for name in ("fb_home_kids", "fb_claude_kids"):
            self.assertIn("! -type l", self.fn[name], name)
        self.assertNotIn("find -L", self.s)

    def test_account_side_output_cannot_forge_a_line(self):
        for line in self.fn["fb_account"].splitlines():
            if "fb_as \"$u\" apply" in line:
                self.assertIn("| fb_prefix \"$u\"", line)

    def test_the_login_is_restored_only_after_every_check(self):
        refresh = self.fn["fb_refresh"]
        restore = refresh.index("fb_restore")
        for before in ("fb_identity", "fb_account", "fb_foreign_homes", "withdrawn"):
            self.assertLess(refresh.index(before), restore, before)


def _push_box(td, passwd_line=None, pw="L", rrsync=True, wrapper=True, acl_rc=0):
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
    wr = os.path.join(td, "wrapper")
    if wrapper:
        write_exe(wr, "exit 0")
    block = fbm.render_fleetbackup_push_block(acl_script=acl, wrapper=wr, user="fbt",
                                              home=home, rrsync=rr)
    script = os.path.join(td, "push.sh")
    with open(script, "w") as f:
        f.write("set -euo pipefail\n" + block + "\n"
                'echo after > %s\n' % os.path.join(td, "after")
                + fbm.FLEETBACKUP_DEFERRED_EXIT + "\n")
    env = dict(os.environ)
    env["PATH"] = bindir + ":" + env.get("PATH", "")
    return script, env, {"log": log, "home": home, "after": os.path.join(td, "after")}


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

    def test_missing_rrsync_or_wrapper_changes_nothing(self):
        for kw in ({"rrsync": False}, {"wrapper": False}):
            with tempfile.TemporaryDirectory() as td:
                script, env, p = _push_box(td, passwd_line="", **kw)
                r = _run(script, env)
                self.assertEqual(r.returncode, 1, (kw, r.stdout + r.stderr))
                self.assertIn("nothing changed", r.stderr)
                self.assertEqual(_log(p), [])

    def test_a_symlinked_home_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td)
            os.symlink(td, p["home"])
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertFalse(any(ln.startswith("acl-run") for ln in _log(p)))

    def test_a_busy_refresh_lock_is_loud_and_deferred(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, acl_rc=fbm.FLEETBACKUP_LOCK_BUSY_RC)
            r = _run(script, env)
            self.assertEqual(r.returncode, fbm.FLEETBACKUP_LOCK_BUSY_RC, r.stdout + r.stderr)
            self.assertIn("refresh lock busy", r.stderr)
            self.assertTrue(os.path.exists(p["after"]), "a later guard step was skipped")

    def test_a_failed_refresh_never_skips_a_later_step(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, acl_rc=2)
            r = _run(script, env)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("FLEETBACKUP FAILED rc=2", r.stderr)
            self.assertTrue(os.path.exists(p["after"]), "a later guard step was skipped")


class TestWiring(unittest.TestCase):
    def test_the_script_and_wrapper_are_installed_before_daemon_reload(self):
        files = dict(crg.guard_files())
        self.assertEqual(files[fbm.FLEETBACKUP_ACL_SCRIPT_PATH], fbm.render_fleetbackup_acl_script())
        self.assertEqual(files[fbm.FLEETBACKUP_RRSYNC_WRAPPER_PATH],
                         fbm.render_fleetbackup_rrsync_wrapper())
        s = crg.build_apply_script()
        for path in (fbm.FLEETBACKUP_ACL_SCRIPT_PATH, fbm.FLEETBACKUP_RRSYNC_WRAPPER_PATH):
            self.assertLess(s.index("_install %s" % path), s.index("systemctl daemon-reload"))

    def test_push_leg_runs_after_quota_and_exits_last(self):
        s = crg.build_apply_script()
        leg = fbm.render_fleetbackup_push_block()
        self.assertEqual(s.count(leg), 1)
        self.assertIn("/bin/bash %s\n" % fbm.FLEETBACKUP_ACL_SCRIPT_PATH, leg)
        self.assertNotIn(crg.QUOTA_LOCK_PATH, leg)
        self.assertLess(s.index("per-user ext4 disk quota"), s.index(leg))
        self.assertLess(s.index(leg), s.index("shared Playwright browsers"))
        self.assertTrue(s.rstrip("\n").endswith(fbm.FLEETBACKUP_DEFERRED_EXIT))

    def test_hourly_ceiling_releases_the_quota_lock_then_refreshes(self):
        s = crg.render_quota_ceiling_script()
        call = "/bin/bash %s || fb_rc=$?" % fbm.FLEETBACKUP_ACL_SCRIPT_PATH
        self.assertIn(call, s)
        self.assertLess(s.index("per-user usage-aware quota limits"), s.index("exec 9>&-"))
        self.assertLess(s.index("exec 9>&-"), s.index(call))
        self.assertIn('[ "$fb_rc" -ne 0 ]; then exit 1', s)

    def test_the_refresh_never_runs_under_the_quota_lock(self):
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, "quota.lock")
            acl = os.path.join(td, "acl.sh")
            write_exe(acl, "flock -n %s true" % lock)   # free only if the caller let go
            body = ("set -euo pipefail\nexec 9>>%s\nflock 9\n" % lock
                    + fbm.render_fleetbackup_hourly_call(acl)
                    + 'if [ "$fb_rc" -ne 0 ]; then exit 1; fi\nexit 0\n')
            r = subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)

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

    def test_the_ceiling_unit_has_time_for_the_refresh(self):
        svc = crg.render_quota_ceiling_service()
        line = [ln for ln in svc.splitlines() if ln.startswith("TimeoutStartSec=")][0]
        self.assertGreaterEqual(int(line.split("=", 1)[1]),
                                crgq.QUOTA_LOCK_WAIT_S + fbm.FLEETBACKUP_REFRESH_BUDGET_S)


class TestMarkerRead(unittest.TestCase):
    """Root reads a marker the ACCOUNT owns: a FIFO must never hang a loop."""

    def _is_stream(self, path):
        t0 = time.monotonic()
        r = subprocess.run(["bash", "-c", crgq._render_is_stream_test('"$1"'), "_", path],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0, time.monotonic() - t0

    def test_only_a_plain_marker_file_counts(self):
        with tempfile.TemporaryDirectory() as td:
            plain = os.path.join(td, "plain")
            with open(plain, "w") as f:
                f.write("shared-stream\n")
            fifo = os.path.join(td, "fifo")
            os.mkfifo(fifo)
            link = os.path.join(td, "link")
            os.symlink(plain, link)
            self.assertEqual(self._is_stream(plain)[0], True)
            ok, took = self._is_stream(fifo)
            self.assertFalse(ok)
            self.assertLess(took, 10)
            self.assertFalse(self._is_stream(link)[0])

    def test_every_root_loop_uses_the_safe_read(self):
        s = crg.build_apply_script()
        self.assertNotIn('grep -q "shared-stream" "$', s)
        self.assertEqual(s.count(crgq._render_is_stream_test('"$bcf"')), 1 + 2 * 3)


if __name__ == "__main__":
    unittest.main()
