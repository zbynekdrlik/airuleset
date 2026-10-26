"""#1236: the fleet-shared newlevel ssh password leaves the public source.

Every identity-less ssh airuleset builds (the push deploy loop, the deploy-leg
diagnostics, the secret-delivery prefix, the fleet burn/delegation collectors,
`context-baseline --fleet`, the webterm connect and U-read prefixes) now runs
`sshpass -f ~/.secrets/newlevel-ssh-password`: the value is in neither this repo
nor any argv. A missing, non-regular or group/other-readable file is a LOUD
error naming the file and how to create it, never a silent fallback.

Every test builds a FAKE password file in a temp HOME; the real value is never
written here. The source guard below forbids any literal sshpass password in a
tracked file without spelling one out.
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset                # noqa: E402
import cli_burn                 # noqa: E402
import cli_context_baseline     # noqa: E402
import cli_privileges           # noqa: E402
import cli_remote               # noqa: E402
import cli_ssh_password         # noqa: E402
import cli_webterm              # noqa: E402

# TEST-NET-1 addresses (RFC 5737, never routed) and every subprocess.run
# patched: no test here may open a real ssh (a fail2ban strike on a real box).
FAKE_VALUE = "fake-1236-test-value"
CREATE_HINT = "install -m 600 /dev/stdin ~/.secrets/newlevel-ssh-password"
NOKEY = {"name": "nokey-1236", "host": "192.0.2.10", "user": "u", "id": "nk",
         "repo_path": "~/devel/airuleset"}
KEYED = {"name": "keyed-1236", "host": "192.0.2.11", "user": "g",
         "repo_path": "~/devel/airuleset", "identity": "~/.secrets/k"}


class _FakeHome(unittest.TestCase):
    """A temp HOME; `make_file(mode)` writes the FAKE password file there."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        # the suite-wide FAKE file (conftest / push Pass B): resolve for real here
        os.environ.pop(cli_ssh_password.ENV_OVERRIDE, None)
        self.pw = self.home / ".secrets" / "newlevel-ssh-password"

    def make_file(self, mode=0o600):
        self.pw.parent.mkdir(mode=0o700, exist_ok=True)
        self.pw.write_text(FAKE_VALUE + "\n")
        os.chmod(self.pw, mode)
        return str(self.pw.parent.resolve() / self.pw.name)

    def assert_reads_file(self, argv, expected):
        # A failure never prints the argv: before #1236 it carried the value.
        self.assertTrue(argv[:3] == ["sshpass", "-f", expected]
                        and "-p" not in argv and FAKE_VALUE not in " ".join(argv),
                        "not an `sshpass -f <password file>` prefix")


class TestPasswordFile(_FakeHome):

    def test_present_0600_file_is_returned(self):
        expected = self.make_file()
        self.assertEqual(cli_ssh_password.password_file(), expected)

    def test_sshpass_argv_reads_the_file(self):
        expected = self.make_file()
        self.assert_reads_file(cli_ssh_password.sshpass_argv(), expected)

    def test_missing_file_is_a_loud_named_error(self):
        self.pw.parent.mkdir(mode=0o700)
        with self.assertRaises(cli_ssh_password.SshPasswordFileError) as cm:
            cli_ssh_password.password_file()
        msg = str(cm.exception)
        self.assertIsInstance(cm.exception, OSError)
        self.assertIn(str(self.pw.name), msg)
        self.assertIn("MISSING", msg)
        self.assertIn(CREATE_HINT, msg)

    def test_missing_secrets_dir_is_the_same_loud_error(self):
        with self.assertRaises(cli_ssh_password.SshPasswordFileError) as cm:
            cli_ssh_password.sshpass_argv()
        self.assertIn(CREATE_HINT, str(cm.exception))

    def test_group_readable_file_is_refused(self):
        self.make_file(mode=0o640)
        with self.assertRaises(cli_ssh_password.SshPasswordFileError) as cm:
            cli_ssh_password.password_file()
        self.assertIn("chmod 600", str(cm.exception))
        self.assertNotIn(FAKE_VALUE, str(cm.exception))

    def test_symlinked_file_is_refused(self):
        real = self.home / "elsewhere"
        real.write_text(FAKE_VALUE + "\n")
        os.chmod(real, 0o600)
        self.pw.parent.mkdir(mode=0o700)
        self.pw.symlink_to(real)
        with self.assertRaises(cli_ssh_password.SshPasswordFileError) as cm:
            cli_ssh_password.password_file()
        self.assertIn("not a regular file", str(cm.exception))


class TestOverrideSeam(_FakeHome):
    """The AIRULESET_SSH_PASSWORD_FILE test seam: honoured with the same
    checks, written by the push gate for Pass B, set by conftest for pytest."""

    def test_override_path_is_used_with_the_same_checks(self):
        fake = self.home / "fake-pw"
        with mock.patch.dict(os.environ, {cli_ssh_password.ENV_OVERRIDE: str(fake)}):
            with self.assertRaises(cli_ssh_password.SshPasswordFileError):
                cli_ssh_password.password_file()
            fake.write_text(FAKE_VALUE + "\n")
            os.chmod(fake, 0o600)
            self.assertEqual(cli_ssh_password.password_file(), str(fake))

    def test_suite_fake_file_is_0600(self):
        path = Path(cli_ssh_password.fake_file_for_suite(self.home))
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        with mock.patch.dict(os.environ, {cli_ssh_password.ENV_OVERRIDE: str(path)}):
            self.assertEqual(cli_ssh_password.password_file(), str(path))

    def test_both_runners_get_a_fake_file(self):
        import inspect
        self.assertIn("fake_file_for_suite(", inspect.getsource(cli_remote.cmd_push))
        conftest = (REPO / "tests" / "conftest.py").read_text()
        self.assertIn('"%s"' % cli_ssh_password.ENV_OVERRIDE, conftest)


class TestRemoteSshPrefix(_FakeHome):

    def test_identityless_prefix_reads_the_file(self):
        expected = self.make_file()
        prefix, reason = cli_remote._ssh_prefix(NOKEY, False)
        self.assertIsNone(reason)
        self.assert_reads_file(prefix, expected)
        # the #669 hostkey opts and the #341 one-prompt cap are unchanged
        self.assertEqual(prefix[3:], ["ssh", *cli_remote.host_key_check_opts(NOKEY),
                                      "-o", "NumberOfPasswordPrompts=1"])

    def test_identityless_prefix_missing_file_raises(self):
        with self.assertRaises(cli_ssh_password.SshPasswordFileError):
            cli_remote._ssh_prefix(NOKEY, False)

    def test_identity_prefix_needs_no_file(self):
        prefix, _ = cli_remote._ssh_prefix(KEYED, False)
        self.assertEqual(prefix[0], "ssh")
        self.assertNotIn("sshpass", prefix)

    def _diag(self):
        calls, err = [], io.StringIO()

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=run), \
                mock.patch("sys.stderr", err):
            cli_remote._print_deploy_leg_diagnostics(
                NOKEY, None, ["-o", "StrictHostKeyChecking=no"], [])
        return calls, err.getvalue()

    def test_diagnostics_read_the_file(self):
        expected = self.make_file()
        calls, _ = self._diag()
        self.assert_reads_file(calls[0], expected)

    def test_diagnostics_missing_file_warns_never_raises(self):
        calls, err = self._diag()
        self.assertEqual(len(calls), 0)
        self.assertIn("diagnostic ssh failed", err)
        self.assertIn(CREATE_HINT, err)


class TestDeployLoopMissingFile(_FakeHome):
    """A missing password file fails ONLY the identity-less targets, loudly,
    and the push exits non-zero; the key-based targets still deploy."""

    def test_identityless_target_fails_key_target_deploys(self):
        calls, err = [], io.StringIO()

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        failed, auth_failed = [], set()
        ctl = tempfile.mkdtemp(prefix="arsshcm-1236-")
        with mock.patch("subprocess.run", side_effect=run), \
                mock.patch("sys.stderr", err), \
                mock.patch("sys.stdout", io.StringIO()), \
                mock.patch.object(airuleset, "REMOTE_HOSTS", [NOKEY, KEYED]), \
                mock.patch.object(cli_remote, "_ssh_control_dir_for_push",
                                  return_value=ctl), \
                self.assertRaises(SystemExit) as cm:
            cli_remote._deploy_to_all_remotes(failed, auth_failed)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn(("nokey-1236", "no-password-file"), failed)
        self.assertNotIn("keyed-1236", {name for name, _ in failed})
        self.assertIn("nokey-1236", auth_failed)
        self.assertEqual(len([c for c in calls if c and c[0] == "sshpass"]), 0)
        self.assertTrue(any(c and c[0] == "ssh" and "g@192.0.2.11" in c
                            for c in calls))
        self.assertIn(CREATE_HINT, err.getvalue())


class TestFleetCollectors(_FakeHome):

    def _no_ssh(self, fn, *args):
        """`fn(*args)` with subprocess.run recorded, never executed."""
        calls, err = [], io.StringIO()

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return mock.Mock(returncode=255, stdout="", stderr="fake")

        with mock.patch("subprocess.run", side_effect=run), \
                mock.patch("sys.stderr", err):
            out = fn(*args)
        self.assertEqual(len(calls), 0)
        return out, err.getvalue()

    def test_burn_prefix_reads_the_file(self):
        expected = self.make_file()
        self.assert_reads_file(cli_burn._remote_ssh_prefix(NOKEY), expected)

    def test_burn_missing_file_warns_one_host(self):
        out, err = self._no_ssh(cli_burn._burn_remote, NOKEY, 7)
        self.assertIsNone(out)
        self.assertIn(CREATE_HINT, err)

    def test_delegation_missing_file_warns_one_host(self):
        out, err = self._no_ssh(cli_burn._delegation_remote, NOKEY, 24)
        self.assertIsNone(out)
        self.assertIn(CREATE_HINT, err)

    def test_fleet_row_missing_file_is_an_error_slot(self):
        row, _ = self._no_ssh(cli_burn._fleet_remote_row, NOKEY, 1)
        self.assertIn("SshPasswordFileError", row["error"])

    def test_webterm_prefixes_read_the_file(self):
        expected = self.make_file()
        with mock.patch("watchdog.reaper.default_box_class",
                        return_value="dev"), \
                mock.patch("sys.stderr", io.StringIO()):
            self.assert_reads_file(cli_webterm._ssh_read_prefix(NOKEY), expected)
            self.assert_reads_file(cli_webterm._ssh_interactive_prefix(NOKEY),
                                   expected)

    def _baseline(self):
        calls = []

        def run(cmd, *a, **k):
            calls.append(list(cmd))
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        with mock.patch.object(cli_remote, "_deployable_hosts",
                               return_value=[NOKEY]), \
                mock.patch.object(cli_context_baseline, "measure_box",
                                  return_value={}), \
                mock.patch.object(cli_context_baseline, "_load_registry",
                                  return_value=({}, "ok")), \
                mock.patch("subprocess.run", side_effect=run):
            return calls, cli_context_baseline.run_fleet()

    def test_context_baseline_fleet_reads_the_file(self):
        expected = self.make_file()
        calls, _ = self._baseline()
        self.assert_reads_file(calls[0], expected)

    def test_context_baseline_missing_file_fails_one_host(self):
        calls, data = self._baseline()
        self.assertEqual(len(calls), 0)
        self.assertEqual(data["failed"],
                         [{"host": "nokey-1236", "error": "SshPasswordFileError"}])


class TestPrivilegesDeclaresFile(_FakeHome):

    def _entry(self):
        return {e.name: e for e in cli_privileges.PRIVILEGES}["fleet_shared_password"]

    def test_entry_points_at_the_password_file(self):
        self.assertEqual(self._entry().local_path,
                         "~/.secrets/" + cli_ssh_password.FILE_NAME)

    def test_file_is_declared_and_probed_without_its_value(self):
        self.make_file()
        declared = {e.local_path for e in cli_privileges.PRIVILEGES if e.local_path}
        found = cli_privileges.scan_undeclared(self.home, declared)
        self.assertFalse([f for f in found if "newlevel-ssh-password" in str(f)])
        rec = cli_privileges.probe_entry(self._entry(), self.home)
        self.assertTrue(rec["present"])
        self.assertEqual(rec["mode"], "0600")
        self.assertFalse(rec["wrong_mode"])
        self.assertNotIn(FAKE_VALUE, repr(rec))

    def test_missing_file_is_reported_absent(self):
        rec = cli_privileges.probe_entry(self._entry(), self.home)
        self.assertFalse(rec["present"])


# A literal sshpass password in a tracked file: the list-argv `-p` form (code
# never passes a password in argv now), and the shell `-p <value>` / SSHPASS=
# forms whose value is not a placeholder ($VAR, <name>, %s, ...) or one of the
# hook tests' obviously fake fixture values.
ARGV_P = re.compile(r"""["']sshpass["']\s*,\s*["']-p["']""")
SHELL_P = re.compile(r"""sshpass\s+-p\s+(\S+)|\bSSHPASS=(\S+)""")
PLACEHOLDER = re.compile(r"""^(\$|<|%|\.\.\.|…)""")
FAKE_FIXTURES = {"x", "secret", "RealPassw0rd!"}


def _literal_password_hits(text):
    hits = ["argv -p" for _ in ARGV_P.finditer(text)]
    for m in SHELL_P.finditer(text):
        value = (m.group(1) or m.group(2)).strip("'\"`,;)")
        if value and not (PLACEHOLDER.match(value) or value in FAKE_FIXTURES):
            hits.append("literal")
    return hits


class TestNoLiteralInSource(unittest.TestCase):

    def test_guard_catches_both_shapes(self):
        # built by concatenation so this source itself holds no literal
        self.assertTrue(_literal_password_hits('["sshpass", ' + '"-p", "v"]'))
        self.assertTrue(_literal_password_hits("sshpass -p " + "hunter2 ssh h"))
        self.assertTrue(_literal_password_hits("SSHPASS=" + "hunter2 sshpass -e"))
        self.assertTrue(_literal_password_hits("sshpass -p " + "'hunter2' ssh h"))
        self.assertFalse(_literal_password_hits('sshpass -p "$PW" ssh h'))
        self.assertFalse(_literal_password_hits("sshpass -f ~/.secrets/x ssh"))

    def test_no_tracked_file_holds_a_literal_sshpass_password(self):
        files = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                               capture_output=True, check=True).stdout
        offenders = []
        for rel in filter(None, files.decode().split("\0")):
            try:
                text = (REPO / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _literal_password_hits(text):
                offenders.append(rel)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
