"""#659 — VPS-class owner-target provisioning: native user-space claude (never
root-npm), NOPASSWD sudo for the owner user, and headless OAuth-token delivery
so first-run claude never shows the interactive login dialog.

Mirrors the offline / injected-`run` discipline of test_owner_key_provisioning
and test_soniox_provisioning: NO real ssh, NO real sudo, NO real network —
every subprocess is a recorder/stub.
"""
import os
import sys
import unittest
import unittest.mock as m
from io import StringIO
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import airuleset  # noqa: E402
import cli_binary_installers  # noqa: E402
import cli_owner_vps  # noqa: E402
import cli_remote  # noqa: E402


def _rec():
    """A recorder run(): captures argv (+ input=) and returns a rc=0 Mock."""
    calls = []

    def run(cmd, *a, **k):
        calls.append({"argv": list(cmd), "input": k.get("input")})
        return m.Mock(returncode=0, stdout="", stderr="")
    return calls, run


# --------------------------------------------------------------------------- #
# Gap 1 — native user-space claude, never a root-npm copy
# --------------------------------------------------------------------------- #
class TestSystemClaudeDetection(unittest.TestCase):
    def test_native_present_true_when_local_bin_resolves(self):
        with m.patch.object(cli_binary_installers.shutil, "which",
                             return_value="/home/u/.local/bin/claude"):
            self.assertTrue(cli_binary_installers._native_claude_present())

    def test_native_present_false_when_local_bin_empty(self):
        with m.patch.object(cli_binary_installers.shutil, "which",
                             return_value=None):
            self.assertFalse(cli_binary_installers._native_claude_present())

    def test_system_path_none_when_no_system_claude(self):
        with m.patch.object(cli_binary_installers.shutil, "which",
                             return_value=None):
            self.assertIsNone(cli_binary_installers._system_claude_path())

    def test_system_path_returns_non_home_copy(self):
        with m.patch.object(cli_binary_installers.shutil, "which",
                             return_value="/usr/bin/claude"), \
                m.patch.object(cli_binary_installers.Path, "home",
                               return_value=Path("/home/u")):
            self.assertEqual(cli_binary_installers._system_claude_path(),
                             "/usr/bin/claude")

    def test_system_path_none_when_copy_realpath_under_home(self):
        # a `claude` on the system PATH whose real target lives under $HOME is
        # NOT a system copy to remove.
        with m.patch.object(cli_binary_installers.shutil, "which",
                             return_value="/usr/bin/claude"), \
                m.patch.object(cli_binary_installers.Path, "home",
                               return_value=Path("/home/u")), \
                m.patch.object(cli_binary_installers.os.path, "realpath",
                               side_effect=lambda p: "/home/u/x" if p == "/usr/bin/claude" else "/home/u"):
            self.assertIsNone(cli_binary_installers._system_claude_path())


class TestEnsureClaudeNativeUserspace(unittest.TestCase):
    def test_no_op_when_no_system_copy(self):
        calls, run = _rec()
        with m.patch.object(cli_binary_installers, "_system_claude_path",
                             return_value=None):
            cli_binary_installers.ensure_claude_native_userspace(
                env={"PATH": "/x"}, run=run)
        self.assertEqual(calls, [])   # fully native / claude-less → nothing

    def test_installs_native_then_removes_system_copy(self):
        # only a root-npm copy present at first, native appears after install
        native = {"present": False}
        calls, run = _rec()
        with m.patch.object(cli_binary_installers, "_system_claude_path",
                             return_value="/usr/bin/claude"), \
                m.patch.object(cli_binary_installers, "_native_claude_present",
                               side_effect=lambda: native["present"]), \
                m.patch.object(cli_binary_installers.shutil, "which",
                               return_value="/usr/bin/sudo"):
            def run_effect(cmd, *a, **k):
                calls.append({"argv": list(cmd), "input": k.get("input")})
                # the native installer flips presence True
                if cmd[:2] == ["bash", "-c"]:
                    native["present"] = True
                return m.Mock(returncode=0, stdout="", stderr="")
            cli_binary_installers.ensure_claude_native_userspace(
                env={"PATH": "/x"}, run=run_effect)
        argvs = [c["argv"] for c in calls]
        # the official installer ran
        self.assertTrue(any(a[:2] == ["bash", "-c"] and "install.sh" in a[2]
                            for a in argvs))
        # and the system copy was removed via sudo -n rm -f <path>
        self.assertIn(["sudo", "-n", "rm", "-f", "/usr/bin/claude"], argvs)

    def test_removes_system_copy_when_native_already_present(self):
        calls, run = _rec()
        with m.patch.object(cli_binary_installers, "_system_claude_path",
                             return_value="/usr/local/bin/claude"), \
                m.patch.object(cli_binary_installers, "_native_claude_present",
                               return_value=True), \
                m.patch.object(cli_binary_installers.shutil, "which",
                               return_value="/usr/bin/sudo"):
            cli_binary_installers.ensure_claude_native_userspace(
                env={"PATH": "/x"}, run=run)
        argvs = [c["argv"] for c in calls]
        # native already present → NO installer, just the sudo probe + removal
        self.assertFalse(any(a[:2] == ["bash", "-c"] for a in argvs))
        self.assertIn(["sudo", "-n", "true"], argvs)
        self.assertIn(["sudo", "-n", "rm", "-f", "/usr/local/bin/claude"], argvs)

    def test_leaves_system_copy_when_no_passwordless_sudo(self):
        calls = []

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            # sudo -n true fails → no passwordless sudo
            rc = 1 if cmd[:3] == ["sudo", "-n", "true"] else 0
            return m.Mock(returncode=rc, stdout="", stderr="")
        with m.patch.object(cli_binary_installers, "_system_claude_path",
                             return_value="/usr/bin/claude"), \
                m.patch.object(cli_binary_installers, "_native_claude_present",
                               return_value=True), \
                m.patch.object(cli_binary_installers.shutil, "which",
                               return_value="/usr/bin/sudo"):
            cli_binary_installers.ensure_claude_native_userspace(
                env={"PATH": "/x"}, run=run)
        # NEVER an rm without passwordless sudo
        self.assertNotIn(["sudo", "-n", "rm", "-f", "/usr/bin/claude"], calls)

    def test_never_removes_when_native_install_fails(self):
        # only a system copy, and the native install never succeeds → keep it
        calls, run = _rec()
        with m.patch.object(cli_binary_installers, "_system_claude_path",
                             return_value="/usr/bin/claude"), \
                m.patch.object(cli_binary_installers, "_native_claude_present",
                               return_value=False), \
                m.patch("sys.stderr", StringIO()):
            cli_binary_installers.ensure_claude_native_userspace(
                env={"PATH": "/x"}, run=run)
        argvs = [c["argv"] for c in calls]
        self.assertNotIn(["sudo", "-n", "rm", "-f", "/usr/bin/claude"], argvs)


# --------------------------------------------------------------------------- #
# Gap 2 — NOPASSWD sudo for the owner user on an owner VPS
# --------------------------------------------------------------------------- #
class TestProvisionOwnerSudo(unittest.TestCase):
    def test_no_op_without_owner_vps_signal(self):
        calls, run = _rec()
        with m.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                cli_owner_vps.provision_owner_sudo(user="newlevel", run=run),
                "not-owner-vps")
        self.assertEqual(calls, [])

    def test_refuses_unsafe_username(self):
        calls, run = _rec()
        with m.patch("sys.stderr", StringIO()):
            r = cli_owner_vps.provision_owner_sudo(
                user="root; rm -rf /", run=run, require_signal=False)
        self.assertEqual(r, "unsafe-username")
        self.assertEqual(calls, [])

    def test_loud_when_no_passwordless_sudo(self):
        out = StringIO()

        def run(cmd, *a, **k):
            return m.Mock(returncode=1, stdout="", stderr="")  # sudo -n true fails
        with m.patch.object(cli_owner_vps.shutil, "which",
                             return_value="/usr/bin/sudo"), \
                m.patch("sys.stderr", out):
            r = cli_owner_vps.provision_owner_sudo(
                user="newlevel", run=run, require_signal=False)
        self.assertEqual(r, "no-passwordless-sudo")
        self.assertIn("one-time manual bootstrap", out.getvalue())

    def test_installs_validated_sudoers_for_owner_user(self):
        calls = []

        def run(cmd, *a, **k):
            calls.append({"argv": list(cmd), "input": k.get("input")})
            if cmd[:3] == ["sudo", "-n", "true"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            return m.Mock(returncode=0, stdout="AIRULESET_SUDOERS_INSTALLED\n",
                          stderr="")
        with m.patch.object(cli_owner_vps.shutil, "which",
                             return_value="/usr/bin/sudo"):
            r = cli_owner_vps.provision_owner_sudo(
                user="newlevel", run=run, require_signal=False)
        self.assertEqual(r, "provisioned")
        install = [c for c in calls if c["argv"][:4] == ["sudo", "-n", "sh", "-c"]]
        self.assertEqual(len(install), 1)
        script = install[0]["argv"][4]
        # visudo validation gates the install; dest is /etc/sudoers.d/<user>
        self.assertIn("visudo -cf", script)
        self.assertIn("/etc/sudoers.d/newlevel", script)
        # the NOPASSWD content for the SINGLE owner user is piped via stdin,
        # never argv; content names the user, never ALL users.
        self.assertEqual(install[0]["input"], "newlevel ALL=(ALL) NOPASSWD:ALL\n")

    def test_idempotent_unchanged(self):
        def run(cmd, *a, **k):
            if cmd[:3] == ["sudo", "-n", "true"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            return m.Mock(returncode=0, stdout="AIRULESET_SUDOERS_UNCHANGED\n",
                          stderr="")
        with m.patch.object(cli_owner_vps.shutil, "which",
                             return_value="/usr/bin/sudo"):
            r = cli_owner_vps.provision_owner_sudo(
                user="newlevel", run=run, require_signal=False)
        self.assertEqual(r, "unchanged")

    def test_signal_env_reader(self):
        self.assertTrue(cli_owner_vps._owner_vps_signalled({"AIRULESET_OWNER_VPS": "1"}))
        self.assertFalse(cli_owner_vps._owner_vps_signalled({}))
        self.assertFalse(cli_owner_vps._owner_vps_signalled({"AIRULESET_OWNER_VPS": "0"}))

    def test_install_script_never_names_ALL_users_and_is_atomic(self):
        script = cli_owner_vps._sudoers_install_script("/etc/sudoers.d/newlevel")
        # append-safe: dotted temp in the dir (sudo ignores dotted files) +
        # atomic mv; never a truncating write to the dest itself.
        self.assertIn("mktemp", script)
        self.assertIn("mv -f", script)
        self.assertIn("visudo -cf", script)


# --------------------------------------------------------------------------- #
# Gap 3 — headless OAuth token so first-run claude never shows the login dialog
# --------------------------------------------------------------------------- #
class TestHeadlessTokenValue(unittest.TestCase):
    def test_none_when_source_missing(self):
        self.assertIsNone(
            cli_remote._owner_headless_token_value(Path("/nope/absent")))

    def test_strips_and_returns_value(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False) as fh:
            fh.write("sk-oauth-abc123\n")
            p = fh.name
        try:
            self.assertEqual(
                cli_remote._owner_headless_token_value(Path(p)), "sk-oauth-abc123")
        finally:
            os.unlink(p)

    def test_none_when_empty(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".tok", delete=False) as fh:
            fh.write("   \n")
            p = fh.name
        try:
            self.assertIsNone(cli_remote._owner_headless_token_value(Path(p)))
        finally:
            os.unlink(p)


class TestProvisionOwnerHeadlessToken(unittest.TestCase):
    OWNER = {"name": "spinbike-vps", "host": "1.2.3.4", "user": "newlevel",
             "repo_path": "~/devel/airuleset", "identity": "~/.ssh/spinbike_vps",
             "owner_vps": True}
    SUBDEV = {"name": "montalu2@subdev", "host": "5.6.7.8", "user": "montalu2",
              "repo_path": "~/devel/airuleset"}

    def test_no_targets_returns_empty(self):
        calls, run = _rec()
        with m.patch.object(airuleset, "REMOTE_HOSTS", [self.SUBDEV]):
            self.assertEqual(
                cli_remote.provision_owner_headless_token(run=run), [])
        self.assertEqual(calls, [])   # source never even read

    def test_loud_fail_when_source_missing(self):
        out = StringIO()
        calls, run = _rec()
        with m.patch.object(airuleset, "REMOTE_HOSTS", [self.OWNER, self.SUBDEV]), \
                m.patch.object(cli_remote, "_owner_headless_token_value",
                               return_value=None), \
                m.patch("sys.stderr", out):
            failed = cli_remote.provision_owner_headless_token(run=run)
        self.assertEqual(failed, [("spinbike-vps", "headless-token-source-missing")])
        self.assertIn("setup-token", out.getvalue())
        self.assertEqual(calls, [])   # nothing delivered

    def test_delivers_only_to_owner_vps_via_stdin(self):
        calls, run = _rec()
        with m.patch.object(airuleset, "REMOTE_HOSTS", [self.OWNER, self.SUBDEV]), \
                m.patch.object(cli_remote, "_owner_headless_token_value",
                               return_value="sk-oauth-XYZ"):
            failed = cli_remote.provision_owner_headless_token(run=run)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 1)   # ONLY the owner_vps target
        argv = calls[0]["argv"]
        # value delivered via stdin, NEVER in argv
        self.assertEqual(calls[0]["input"], "sk-oauth-XYZ\n")
        self.assertNotIn("sk-oauth-XYZ", " ".join(argv))
        self.assertIn("newlevel@1.2.3.4", argv)
        # lands in ~/.secrets, mode 600
        remote_cmd = argv[-1]
        self.assertIn("~/.secrets/claude-code-oauth-token", remote_cmd)
        self.assertIn("chmod 600", remote_cmd)

    def test_skips_known_auth_failure(self):
        out = StringIO()
        calls, run = _rec()
        with m.patch.object(airuleset, "REMOTE_HOSTS", [self.OWNER]), \
                m.patch.object(cli_remote, "_owner_headless_token_value",
                               return_value="tok"), \
                m.patch("sys.stderr", out):
            failed = cli_remote.provision_owner_headless_token(
                run=run, skip_names={"spinbike-vps"})
        self.assertEqual(failed, [("spinbike-vps", "skipped-known-auth-failure")])
        self.assertEqual(calls, [])


# --------------------------------------------------------------------------- #
# Wiring — REMOTE_HOSTS flag, deploy-loop env signal, launcher guard
# --------------------------------------------------------------------------- #
class TestWiring(unittest.TestCase):
    def test_spinbike_flagged_owner_vps(self):
        sb = [h for h in airuleset.REMOTE_HOSTS if h.get("name") == "spinbike-vps"]
        self.assertEqual(len(sb), 1)
        self.assertTrue(sb[0].get("owner_vps"))

    def test_no_subdev_or_dev2_is_owner_vps(self):
        for h in airuleset.REMOTE_HOSTS:
            if h.get("name") != "spinbike-vps":
                self.assertFalse(h.get("owner_vps"),
                                 "%s must not be owner_vps" % h.get("name"))

    def test_launcher_guards_headless_token_export(self):
        s = airuleset.CLAUDE_LAUNCH_SCRIPT_CONTENT
        # export happens ONLY behind the file-exists guard (never unconditional)
        self.assertIn('[ -s "$HOME/.secrets/claude-code-oauth-token" ]', s)
        self.assertIn("export CLAUDE_CODE_OAUTH_TOKEN", s)
        guard_idx = s.index("claude-code-oauth-token")
        export_idx = s.index("export CLAUDE_CODE_OAUTH_TOKEN")
        self.assertLess(guard_idx, export_idx)

    def test_deploy_loop_sets_owner_vps_env_only_for_owner_vps_host(self):
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")
        owner = {"name": "spinbike-vps", "host": "1.2.3.4", "user": "newlevel",
                 "repo_path": "~/devel/airuleset", "identity": "~/.ssh/sb",
                 "owner_vps": True}
        plain = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                 "repo_path": "~/devel/airuleset"}
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", [owner, plain]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                m.patch.object(cli_remote, "provision_owner_headless_token",
                               return_value=[]):
            airuleset.cmd_push(args)
        deploy = [c for c in calls
                  if any("python3 airuleset.py install" in str(a) for a in c)]
        owner_cmds = [c for c in deploy if any("1.2.3.4" in str(a) for a in c)]
        plain_cmds = [c for c in deploy if any("5.6.7.8" in str(a) for a in c)]
        self.assertEqual(len(owner_cmds), 1)
        self.assertEqual(len(plain_cmds), 1)
        self.assertTrue(any("AIRULESET_OWNER_VPS=1 python3 airuleset.py install" in str(a)
                            for a in owner_cmds[0]))
        self.assertFalse(any("AIRULESET_OWNER_VPS=1" in str(a)
                             for a in plain_cmds[0]))


if __name__ == "__main__":
    unittest.main()
