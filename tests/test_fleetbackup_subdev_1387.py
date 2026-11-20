"""#1387 — subdev's read-only fleetbackup login: the key line, the forced-
command wrapper, the push leg, and its wiring into the root apply + the hourly
quota-ceiling unit.

The push leg is executed against stub useradd/getent/passwd/usermod/install/systemctl
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


PULL = "rsync --server --sender -vlogDtpre.iLsfxCIvu"


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

    def assert_refused(self, command):
        r, log = self._run(command)
        self.assertEqual(r.returncode, 1, command)
        self.assertEqual(log, "", command)
        self.assertIn("refused", r.stderr)

    def test_a_pull_of_either_tree_reaches_rrsync_read_only(self):
        for path in ("david3/uploads/", "david3/uploads", "david3/uploads/sub/x.pdf",
                     "david3/.claude/work-products/", "u1/uploads/ u2/.claude/work-products/a"):
            r, log = self._run("%s --numeric-ids . %s" % (PULL, path))
            self.assertEqual(r.returncode, 0, (path, r.stderr))
            self.assertEqual(log, "rrsync -ro /home\n", path)

    def test_the_refused_options_are_exactly_these(self):
        self.assertEqual(set(fbm.FLEETBACKUP_REFUSED_OPTIONS),
                         {"--files-from", "--copy-unsafe-links", "--copy-links",
                          "--copy-dirlinks", "--keep-dirlinks"})
        for opt in ("--files-from=-", "--files-from", "--copy-unsafe-links", "--copy-links",
                    "--copy-dirlinks", "--keep-dirlinks"):
            self.assert_refused("%s %s . u/uploads/" % (PULL, opt))

    def test_only_paths_inside_a_tree(self):
        for path in ("u/devel/", "u/.claude/", "u/.claude/projects/x", "u/", "u", "/",
                     "/etc/passwd", "u/uploads/../devel", "u/uploads/..", "../etc",
                     "U-Pper/uploads/", "u/uploadsX/", "u/.claude/work-productsX",
                     "uploads", "uploads/x", "a.b/uploads/", "./uploads/", ".x/uploads/"):
            self.assert_refused("%s . %s" % (PULL, path))

    def test_a_byte_only_rrsync_splits_on_is_refused(self):
        """Round 3 🔴: rrsync splits on every Python \\s and honours a
        backslash, sh splits on space, tab and newline only. A VT between two
        words was one allowed option to the wrapper and `--files-from=-` to
        rrsync, which then served /etc through a planted symlink."""
        for sep in ("\x0b", "\x0c", "\r", "\x1c", "\x1d", "\x1e", "\x1f", "\t", "\n",
                    "\x85", "\xa0", "\u2003"):
            self.assert_refused("%s%s--files-from=- . u/uploads/" % (PULL, sep))
            self.assert_refused("%s . u/uploads/%s/etc/passwd" % (PULL, sep))

    def test_no_quote_glob_or_escape_character_passes(self):
        for path in ("u/uploads/a\\ b", "u/uploads/*", "u/uploads/?", "u/uploads/[a]",
                     '"u/uploads/x"', "'u/uploads/x'", "u/uploads/~x", "u/uploads/$HOME",
                     "u/uploads/x;id", "u/uploads/x|id", "u/uploads/{a,b}"):
            self.assert_refused("%s . %s" % (PULL, path))

    def test_only_a_read_only_pull(self):
        for command in ("rsync --server -vlogDtpre.iLsfxCIvu . u/uploads/",   # a push
                        "scp -f u/uploads/x", "", "rsync --server --sender -v u/uploads/",
                        "rsync --server --sender -v .", "id"):
            self.assert_refused(command)

    def test_the_client_text_is_never_evaluated(self):
        with tempfile.TemporaryDirectory() as td:
            canary = os.path.join(td, "canary")
            self.assert_refused('%s $(touch %s) `touch %s` . u/uploads/' % (PULL, canary, canary))
            self.assert_refused('%s . u/uploads/$(touch %s)*' % (PULL, canary))
            self.assertFalse(os.path.exists(canary))


class TestRrsyncWrapperLive(unittest.TestCase):
    """The real rsync client, a fake ssh that sets SSH_ORIGINAL_COMMAND the way
    sshd does, the rendered wrapper and the real /usr/bin/rrsync."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        td = self.td = self._td.name
        self.addCleanup(self._td.cleanup)
        self.root = os.path.join(td, "home")
        os.makedirs(os.path.join(self.root, "alice", "uploads"))
        os.makedirs(os.path.join(self.root, "alice", "devel"))
        with open(os.path.join(self.root, "alice", "uploads", "a.txt"), "w") as f:
            f.write("backup me\n")
        with open(os.path.join(self.root, "alice", "devel", "code.py"), "w") as f:
            f.write("not me\n")
        secret_dir = os.path.join(td, "outside")
        os.makedirs(secret_dir)
        with open(os.path.join(secret_dir, "secret"), "w") as f:
            f.write("SECRET\n")
        os.symlink(secret_dir, os.path.join(self.root, "alice", "uploads", "etclink"))
        w = os.path.join(td, "wrapper")
        with open(w, "w") as f:
            f.write(fbm.render_fleetbackup_rrsync_wrapper(rrsync=fbm.RRSYNC_PATH, root=self.root))
        self.ssh = os.path.join(td, "fakessh")
        write_exe(self.ssh, 'shift\nSSH_ORIGINAL_COMMAND="$*" exec /bin/sh %s\n' % w)

    def pull(self, *args):
        dest = tempfile.mkdtemp(dir=self.td)
        r = subprocess.run(["rsync", "-a", "-e", self.ssh, *args, dest + "/"],
                           capture_output=True, text=True, timeout=60)
        found = {}
        for d, _dirs, files in os.walk(dest):
            for name in files:
                with open(os.path.join(d, name)) as f:
                    found[os.path.relpath(os.path.join(d, name), dest)] = f.read()
        return r, found

    def test_a_tree_pull_works_and_keeps_symlinks_as_links(self):
        r, found = self.pull("x:alice/uploads/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(found, {"a.txt": "backup me\n"})

    def test_a_planted_symlink_never_serves_its_target(self):
        r, found = self.pull("--copy-unsafe-links", "x:alice/uploads/")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("SECRET\n", found.values())
        lst = os.path.join(self.td, "list")
        with open(lst, "w") as f:
            f.write("etclink/secret\n")
        r, found = self.pull("--files-from=%s" % lst, "x:alice/uploads/")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("SECRET\n", found.values())

    def test_nothing_outside_the_trees(self):
        for src in ("x:alice/devel/", "x:alice/", "x:/", "x:alice/uploads/etclink/"):
            r, found = self.pull(src)
            self.assertNotEqual(r.returncode, 0, src)
            self.assertNotIn("not me\n", found.values(), src)
            self.assertNotIn("SECRET\n", found.values(), src)


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
        deny-only fb_root_deny (-n: the mask is never recalculated, `---`:
        nothing can open, whatever path it lands on)."""
        account_side = {"fb_traverse", "fb_tree", "fb_close", "fb_close_kids", "fb_apply"}
        call = re.compile(r"\bsetfacl\s+-")
        self.assertGreater(len(self.fn), 25)
        for name, body in self.fn.items():
            if name not in account_side | {"fb_root_deny"}:
                self.assertIsNone(call.search(body), name)
        self.assertEqual(self.fn["fb_root_deny"].strip().splitlines()[-1].strip(),
                         'setfacl -P -n -m "u:$FB:---" -- "$1"')

    def test_every_other_uid_runs_timed_with_no_root_environment(self):
        runs = re.findall(r"[^\n]*runuser -u[^\n]*", self.s)
        self.assertEqual(len(runs), 2, runs)
        for line in runs:
            self.assertRegex(line, r"timeout -k 10 \"\$t\" runuser -u ")
            self.assertIn("-- env -i PATH=", line)
        self.assertIn('runuser -u "$u"', self.fn["fb_run_account"])
        self.assertIn('t=$(fb_cut "$FB_ACCOUNT_TIMEOUT" 1)', self.fn["fb_run_account"])
        self.assertIn('runuser -u "$FB"', self.fn["fb_as_fb"])
        self.assertIn('t=$(fb_cut "$1" 30)', self.fn["fb_as_fb"])
        calls = re.findall(r"fb_as_fb [^\n]*", self.s)
        self.assertEqual(len(calls), 5, calls)
        for c in calls:
            self.assertRegex(c, r'^fb_as_fb "\$FB_(CHECK_TIMEOUT|QUICK_CHECK)" check\b', c)

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
        self.assertIn('setfacl -P -n -d -m "u:$fb:---,g::---,o::---" -- "$h"', self.fn["fb_apply"])
        self.assertIn("d:u:$1:r-x,d:g::---,d:o::---", self.fn["fb_tree"])

    def test_files_never_get_their_mask_recalculated(self):
        self.assertIn('-type f -exec setfacl -P -n -m "u:$1:r--" -- {} +', self.fn["fb_tree"])
        self.assertNotIn("--restore", self.s)    # round 3: it rewrote the whole ACL
        self.assertIn('setfacl -P -n -m "u:$fb:---"', self.fn["fb_close"])
        self.assertIn('setfacl -P -n -m "u:$1:---"', self.fn["fb_close_kids"])

    def test_symlinks_are_never_walked(self):
        for name in ("fb_home_kids", "fb_claude_kids"):
            self.assertIn("! -type l", self.fn[name], name)
        self.assertNotIn("find -L", self.s)

    def test_account_side_output_cannot_forge_a_line_or_hold_a_pipe(self):
        run = self.fn["fb_run_account"]
        self.assertIn('>"$out" 2>&1', run)
        self.assertIn('ulimit -f "$FB_OUTPUT_CAP_KIB"', run)
        self.assertIn('head -c 32768 -- "$out" | head -n 200 | fb_prefix "$u"', run)
        self.assertIn("s/[^[:print:]]/?/g", self.fn["fb_prefix"])
        self.assertNotIn("fb_as ", self.fn["fb_account"])

    def test_the_refresh_lock_is_root_only(self):
        lock = self.fn["fb_lock"]
        self.assertLess(lock.index("umask 077"), lock.index('exec 8>>"$FB_LOCK"'))
        self.assertIn('"$FB_KEY_UID 600"', lock)

    def test_the_login_is_restored_only_after_every_check(self):
        refresh = self.fn["fb_refresh"]
        restore = refresh.index("fb_restore")
        for before in ("fb_identity", "fb_account", "fb_foreign_homes", "withdrawn"):
            self.assertLess(refresh.index(before), restore, before)


def _push_box(td, passwd_line=None, pw="L", rrsync=True, wrapper=True, systemctl_rc=0,
              aquota=True):
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
    write_exe(os.path.join(bindir, "systemctl"),
              'echo "systemctl $*" >> %s; exit %d' % (log, systemctl_rc))
    acl = os.path.join(td, "acl.sh")
    write_exe(acl, 'echo "acl-run $*" >> %s' % log)
    aq = os.path.join(td, "aquota.user")
    if aquota:
        open(aq, "w").close()
    rr = os.path.join(td, "rrsync")
    if rrsync:
        write_exe(rr, "exit 0")
    wr = os.path.join(td, "wrapper")
    if wrapper:
        write_exe(wr, "exit 0")
    block = fbm.render_fleetbackup_push_block(acl_script=acl, wrapper=wr, user="fbt",
                                              home=home, rrsync=rr, aquota=aq)
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
    def test_fresh_box_creates_the_system_account_then_queues_the_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, passwd_line="")
            r = _run(script, env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            log = _log(p)
            self.assertIn("useradd --system --home-dir %s --no-create-home --shell /bin/sh "
                          "--comment %s fbt" % (p["home"], fbm.FLEETBACKUP_GECOS), log)
            self.assertIn("install -d -o root -g root -m 0755 %s" % p["home"], log)
            self.assertIn("install -d -o root -g root -m 0755 %s/.ssh" % p["home"], log)
            self.assertEqual(log[-1], "systemctl start --no-block %s" % fbm.FLEETBACKUP_REFRESH_UNIT)
            self.assertFalse(any(ln.startswith("acl-run") for ln in log), "the push never waits")
            self.assertIn("queued in %s" % fbm.FLEETBACKUP_REFRESH_UNIT, r.stdout)

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
            self.assertFalse(any(ln.startswith(("useradd", "usermod", "install", "systemctl"))
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
            self.assertFalse(any(ln.startswith("systemctl") for ln in _log(p)))

    def test_no_quota_means_no_refresh_and_fails_loud(self):
        """The hourly unit carries the refresh and needs /aquota.user."""
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, aquota=False)
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("quota is not provisioned", r.stderr)
            self.assertFalse(any(ln.startswith("systemctl") for ln in _log(p)))
            self.assertTrue(os.path.exists(p["after"]), "a later guard step was skipped")

    def test_a_missing_installed_script_changes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, passwd_line="")
            os.unlink(os.path.join(td, "acl.sh"))
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("nothing changed", r.stderr)
            self.assertEqual(_log(p), [])

    def test_a_refresh_that_cannot_be_queued_never_skips_a_later_step(self):
        with tempfile.TemporaryDirectory() as td:
            script, env, p = _push_box(td, systemctl_rc=5)
            r = _run(script, env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("could not queue", r.stderr)
            self.assertIn("FLEETBACKUP FAILED rc=1", r.stderr)
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
        self.assertIn("systemctl start --no-block airuleset-quota-ceiling.service ", leg)
        self.assertEqual(fbm.FLEETBACKUP_REFRESH_UNIT, os.path.basename(crgq.QUOTA_CEILING_SERVICE_PATH))
        self.assertNotIn("/bin/bash %s" % fbm.FLEETBACKUP_ACL_SCRIPT_PATH, leg)
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

    def test_a_busy_hourly_refresh_is_loud_but_not_a_failure(self):
        with tempfile.TemporaryDirectory() as td:
            acl = os.path.join(td, "acl.sh")
            write_exe(acl, "exit %d" % fbm.FLEETBACKUP_LOCK_BUSY_RC)
            body = fbm.render_fleetbackup_hourly_call(acl) + \
                'if [ "$fb_rc" -ne 0 ]; then exit 1; fi\nexit 0\n'
            r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("refresh lock busy", r.stderr)

    def test_the_ceiling_unit_has_time_for_the_refresh(self):
        svc = crg.render_quota_ceiling_service()
        line = [ln for ln in svc.splitlines() if ln.startswith("TimeoutStartSec=")][0]
        self.assertGreaterEqual(int(line.split("=", 1)[1]),
                                crgq.QUOTA_LOCK_WAIT_S + fbm.FLEETBACKUP_REFRESH_BUDGET_S)


class TestQuotaLockIsRootOnly(unittest.TestCase):
    """#1387 review: any local user could flock a world-readable quota lock
    and stall every quota writer (and, before the release, the refresh)."""

    def _no_chmod_env(self, td):
        """A PATH whose chmod does nothing: what is left is the umask."""
        bindir = os.path.join(td, "bin")
        os.makedirs(bindir)
        write_exe(os.path.join(bindir, "chmod"), "exit 0")
        return dict(os.environ, PATH=bindir + ":" + os.environ.get("PATH", ""))

    def _run_preamble(self, td, existing_mode=None, env=None):
        lock = os.path.join(td, "quota.lock")
        aq = os.path.join(td, "aquota.user")
        open(aq, "w").close()
        if existing_mode is not None:
            open(lock, "w").close()
            os.chmod(lock, existing_mode)
        s = crgq._render_quota_script_preamble("t", "h", "w") + "exit 0\n"
        s = s.replace(crgq.QUOTA_LOCK_PATH, lock).replace("/aquota.user", aq)
        r = subprocess.run(["bash", "-c", "umask 022\n" + s], capture_output=True, text=True,
                           timeout=30, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return lock

    def test_the_lock_is_born_root_only_not_only_chmod_ed_later(self):
        with tempfile.TemporaryDirectory() as td:
            lock = self._run_preamble(td, env=self._no_chmod_env(td))
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, "quota.lock")
            s = ("set -euo pipefail\numask 022\nquota_fail=0\n"
                 + crgq._render_quota_apply_lock_block().replace(crgq.QUOTA_LOCK_PATH, lock) + "\n")
            r = subprocess.run(["bash", "-c", s], capture_output=True, text=True, timeout=30,
                               env=self._no_chmod_env(td))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)

    def test_the_script_preamble_creates_and_keeps_it_0600(self):
        for existing in (None, 0o644):
            with tempfile.TemporaryDirectory() as td:
                lock = self._run_preamble(td, existing)
                self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600, existing)

    def test_the_push_apply_lock_block_too(self):
        with tempfile.TemporaryDirectory() as td:
            lock = os.path.join(td, "quota.lock")
            open(lock, "w").close()
            os.chmod(lock, 0o644)
            s = ("set -euo pipefail\numask 022\nquota_fail=0\n"
                 + crgq._render_quota_apply_lock_block().replace(crgq.QUOTA_LOCK_PATH, lock)
                 + '\n[ "$quota_fail" -eq 0 ]\n[ "$(umask)" = 0022 ]\n')
            r = subprocess.run(["bash", "-c", s], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)

    def test_the_quota_units_run_under_umask_0077(self):
        for svc in (crg.render_quota_refresh_service(), crg.render_quota_ceiling_service()):
            self.assertIn("UMask=0077\n", svc)


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
