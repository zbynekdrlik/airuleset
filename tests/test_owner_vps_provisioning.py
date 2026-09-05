"""#659 — VPS-class owner-target provisioning: native user-space claude (never
root-npm) + NOPASSWD sudo for the owner user.

#669 (owner ROZHODNUTÉ, 2026-08-24): the #659 headless CLAUDE_CODE_OAUTH_TOKEN
delivery leg was REMOVED — login/auth ON a target is the PROJECT claudy's
responsibility, airuleset never touches auth (#537 machine-identity boundary).
`TestNoAuthTokenStep` locks that the owner_vps flow now carries NO auth/token
step at all.

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

    def test_install_script_validates_BEFORE_install(self):
        # #659 review YELLOW-3a: lock the ORDER, not just presence -- a mutant
        # that moves the `mv` before the `visudo -cf` check (landing an
        # UNVALIDATED file, a sudo-lockout risk) must fail. visudo must come
        # first, and the mv must sit in the visudo-success branch.
        script = cli_owner_vps._sudoers_install_script("/etc/sudoers.d/newlevel")
        self.assertLess(script.index("visudo -cf"), script.index("mv -f"))
        self.assertIn("if visudo -cf", script)
        # the candidate is cleaned up on any exit (no /etc/sudoers.d litter)
        self.assertIn("trap 'rm -f", script)


# --------------------------------------------------------------------------- #
# Gap 3 — headless OAuth token so first-run claude never shows the login dialog
# --------------------------------------------------------------------------- #
class TestDeliverSecretToHosts(unittest.TestCase):
    OWNER = {"name": "spinbike-vps", "host": "1.2.3.4", "user": "newlevel",
             "identity": "~/.ssh/spinbike_vps", "owner_vps": True}
    NOKEY = {"name": "nokey-vps", "host": "9.9.9.9", "user": "newlevel",
             "owner_vps": True}
    _TRANSIENT = "kex_exchange_identification: read: Connection reset by peer\n"

    def test_refuses_owner_secret_without_identity(self):
        # #659 review: an owner secret must NEVER ride the fleet-shared password.
        out = StringIO()
        calls, run = _rec()
        with m.patch("sys.stderr", out):
            failed = cli_remote._deliver_secret_to_hosts(
                [self.NOKEY], "tok", "cat > ~/x", "headless token", run,
                require_identity=True)
        self.assertEqual(failed, [("nokey-vps", "refused-no-identity")])
        self.assertEqual(calls, [])   # never opened an ssh connection
        self.assertIn("requires a pinned ssh identity", out.getvalue())

    def test_soniox_style_allows_no_identity(self):
        # require_identity=False (the subdev/soniox default) uses the sshpass
        # branch for a no-identity host.
        calls, run = _rec()
        failed = cli_remote._deliver_secret_to_hosts(
            [self.NOKEY], "v", "cat > ~/x", "soniox key", run,
            require_identity=False)
        self.assertEqual(failed, [])
        self.assertEqual(calls[0]["argv"][0], "sshpass")

    def test_transient_failure_is_retried_then_succeeds(self):
        seq = [(255, self._TRANSIENT), (0, "")]
        calls = []

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            rc, err = seq[min(len(calls) - 1, len(seq) - 1)]
            return m.Mock(returncode=rc, stdout="", stderr=err)
        with m.patch("time.sleep"):
            failed = cli_remote._deliver_secret_to_hosts(
                [self.OWNER], "tok", "cat > ~/x", "headless token", run,
                require_identity=True)
        self.assertEqual(failed, [])
        self.assertEqual(len(calls), 2)   # retried the transient failure

    def test_nontransient_failure_not_retried(self):
        calls = []

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=1, stdout="", stderr="cat: write error\n")
        with m.patch("sys.stderr", StringIO()):
            failed = cli_remote._deliver_secret_to_hosts(
                [self.OWNER], "tok", "cat > ~/x", "headless token", run,
                require_identity=True)
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(calls), 1)   # an ordinary write failure is NOT retried


# --------------------------------------------------------------------------- #
# Wiring — facade re-exports, cmd_install calls, deploy-loop env, launcher
# --------------------------------------------------------------------------- #
class TestWiring(unittest.TestCase):
    def test_facade_reexports(self):
        self.assertIs(airuleset.provision_owner_sudo,
                      cli_owner_vps.provision_owner_sudo)
        self.assertIs(airuleset.ensure_claude_native_userspace,
                      cli_binary_installers.ensure_claude_native_userspace)
        # cli_remote._deliver_secret_to_hosts: assertIs can fail under
        # unittest discover when import ordering causes cli_remote to be
        # loaded as a second module instance (issue 875 Pass B — intermittent,
        # depends on filesystem listing order + sys.path manipulation in test
        # modules + deferred `import airuleset` chains in watchdog).
        # Verify the facade contract: the re-exported name resolves to the
        # CURRENT cli_remote attribute (same __qualname__ + __module__),
        # proving the from-import captured the right function even if the
        # cli_remote module object in sys.modules was later replaced.
        a_fn = airuleset._deliver_secret_to_hosts
        c_fn = cli_remote._deliver_secret_to_hosts
        if a_fn is not c_fn:
            # Fallback: verify they share origin (same source function,
            # different module instance due to import-order instability).
            import warnings
            warnings.warn(
                "cli_remote module identity split under discover "
                "(issue 875) — fallback contract check used",
                stacklevel=1)
            self.assertEqual(
                a_fn.__qualname__, c_fn.__qualname__,
                "facade re-export qualname mismatch: "
                f"{a_fn.__qualname__!r} vs {c_fn.__qualname__!r}")
            self.assertEqual(
                a_fn.__module__, c_fn.__module__,
                "facade re-export module mismatch: "
                f"{a_fn.__module__!r} vs {c_fn.__module__!r}")
            # Y1 review finding: also check co_filename to distinguish a
            # duplicate module instance (same source file) from a
            # functools.wraps wrapper (different source file).
            self.assertEqual(
                a_fn.__code__.co_filename, c_fn.__code__.co_filename,
                "facade re-export source file mismatch: "
                f"{a_fn.__code__.co_filename!r} vs "
                f"{c_fn.__code__.co_filename!r}")

    def test_cmd_install_invokes_provision_owner_sudo(self):
        import inspect
        import re
        src = inspect.getsource(airuleset.cmd_install)
        self.assertTrue(re.search(r"(?m)^\s*provision_owner_sudo\(\)", src),
                        "cmd_install must actually CALL provision_owner_sudo()")
        self.assertIn("owner-sudo provisioning error (non-fatal)", src)

    def test_cmd_install_invokes_ensure_claude_native_userspace(self):
        import inspect
        import re
        src = inspect.getsource(airuleset.cmd_install)
        self.assertTrue(
            re.search(r"(?m)^\s*ensure_claude_native_userspace\(\)", src),
            "cmd_install must actually CALL ensure_claude_native_userspace()")

    def test_spinbike_flagged_owner_vps(self):
        sb = [h for h in airuleset.REMOTE_HOSTS if h.get("name") == "spinbike-vps"]
        self.assertEqual(len(sb), 1)
        self.assertTrue(sb[0].get("owner_vps"))

    def test_no_subdev_or_dev2_is_owner_vps(self):
        for h in airuleset.REMOTE_HOSTS:
            if h.get("name") != "spinbike-vps":
                self.assertFalse(h.get("owner_vps"),
                                 "%s must not be owner_vps" % h.get("name"))

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
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
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

    def test_deploy_self_heals_gh_credential_helper_before_pull(self):
        """simap1@subdev push v0.1.134: `git pull --ff-only` on the remote
        failed with "could not read Username for 'https://github.com'"
        because that account's global `credential.helper` had drifted to
        `store` while `gh` itself was still logged in. The remote deploy
        command must run `gh auth setup-git` (idempotent, best-effort — never
        fatal on a box with no gh) BEFORE `git pull --ff-only` so a drifted
        helper self-heals on every push instead of wedging the target."""
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")
        plain = {"name": "dev2", "host": "5.6.7.8", "user": "newlevel",
                 "repo_path": "~/devel/airuleset"}
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", [plain]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
            airuleset.cmd_push(args)
        deploy = [c for c in calls
                  if any("python3 airuleset.py install" in str(a) for a in c)]
        self.assertEqual(len(deploy), 1)
        remote_cmd = next(str(a) for a in deploy[0]
                           if "python3 airuleset.py install" in str(a))
        self.assertIn("gh auth setup-git >/dev/null 2>&1 || true", remote_cmd)
        self.assertIn("git pull --ff-only", remote_cmd)
        self.assertLess(remote_cmd.index("gh auth setup-git"),
                         remote_cmd.index("git pull --ff-only"),
                         "gh auth setup-git must run BEFORE git pull --ff-only")


class TestNoAuthTokenStep(unittest.TestCase):
    """#669 — the owner_vps flow contains NO auth/token step. Login/auth ON a
    target is the PROJECT claudy's responsibility; airuleset never touches auth
    (owner ROZHODNUTÉ #659, #537 machine-identity boundary)."""

    def test_headless_token_symbols_are_gone(self):
        for name in ("provision_owner_headless_token",
                     "_owner_headless_token_value",
                     "OWNER_HEADLESS_TOKEN_SOURCE",
                     "OWNER_HEADLESS_TOKEN_DELIVERED_NAME"):
            self.assertFalse(hasattr(airuleset, name),
                             "airuleset.%s must be removed" % name)
            self.assertFalse(hasattr(cli_remote, name),
                             "cli_remote.%s must be removed" % name)

    def test_deploy_flow_has_no_token_delivery_step(self):
        import inspect
        for fn in (airuleset.cmd_push, cli_remote._deploy_to_all_remotes):
            src = inspect.getsource(fn)
            self.assertNotIn("provision_owner_headless_token", src)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", src)

    def test_launcher_has_no_oauth_token_export(self):
        s = airuleset.CLAUDE_LAUNCH_SCRIPT_CONTENT
        # no ACTIVE token export, and no guard reading the delivered secret file
        self.assertNotIn("export CLAUDE_CODE_OAUTH_TOKEN", s)
        self.assertNotIn(".secrets/claude-code-oauth-token", s)

    def test_push_to_owner_vps_exits_zero_with_no_token_warning(self):
        # A push whose only target is an owner_vps host must complete with NO
        # token warning and NO nonzero exit — there is no owner-secret ssh
        # phase any more.
        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")
        owner = {"name": "spinbike-vps", "host": "1.2.3.4", "user": "newlevel",
                 "repo_path": "~/devel/airuleset", "identity": "~/.ssh/sb",
                 "owner_vps": True}
        out = StringIO()
        args = m.Mock()
        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", [owner]), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}), \
                m.patch("sys.stderr", out):
            airuleset.cmd_push(args)   # must NOT raise SystemExit(1)
        combined = out.getvalue()
        for banned in ("headless", "setup-token", "OAUTH TOKEN",
                       "CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertNotIn(banned, combined,
                             "owner_vps push must emit no token warning")


if __name__ == "__main__":
    unittest.main()
