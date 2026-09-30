"""#1202 review round 1 — the fixes a fresh-context adversarial review asked for.

1. The live creator is a BAKED forced command that `push` never re-renders
   (`append_controller_lane_pubkey_command` has no production caller), so
   `airuleset.py status` must name a stale controller-lane line on a stream box.
2. A project account's forced command passes the SESSION name ("marek") as the
   user; the declared-window lookup must key on the ACCOUNT, never the session.
3. The launcher guard and the status check run ON the box, so their declared
   window lookup is host-scoped (`cli_fleet._box_self_entry`): `newlevel` is
   shared by dev1/dev2/spinbike-vps.
4. The guard lets non-session subcommands through (`--version`, `update`, `mcp`).
5. A failing status check prints a line, never raises out of `cmd_status`.

Temp HOME fixtures and fake binaries only — never a real tmux or ~/.claude.
"""
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_account_bootstrap  # noqa: E402
import cli_claude_scripts     # noqa: E402
import cli_fleet              # noqa: E402
import cli_session_cwd        # noqa: E402
import cli_webterm_only       # noqa: E402

M1 = "devel/odoo/odoo-slovnormal"
KEY = "ssh-ed25519 AAAA testkey"


def _home(tc):
    d = Path(tempfile.mkdtemp(prefix="airuleset-1202r-"))
    tc.addCleanup(lambda: shutil.rmtree(d, True))
    return d


class TestStaleForcedCommand(unittest.TestCase):
    def _pub(self):
        return cli_webterm_only.WEBTERM_CONTROLLER_LANE_PUBKEYS["zbynek"]

    def test_old_baked_line_is_stale(self):
        old = 'restrict,pty,command="P=montalu1; C=\\"$HOME\\"" %s' % self._pub()
        stale = cli_session_cwd.stale_forced_commands("montalu1", old + "\n")
        self.assertEqual(len(stale), 1)

    def test_current_line_is_not_stale(self):
        cur = cli_webterm_only._controller_lane_key_line("montalu1", self._pub())
        self.assertEqual(
            cli_session_cwd.stale_forced_commands("montalu1", cur + "\n"), [])

    def test_foreign_and_plain_keys_are_ignored(self):
        text = "%s\nssh-ed25519 AAAAforeign someone\n# comment\n\n" % KEY
        self.assertEqual(cli_session_cwd.stale_forced_commands("montalu1", text), [])

    def test_status_prints_the_stale_line(self):
        home = _home(self)
        (home / ".ssh").mkdir()
        (home / ".ssh" / "authorized_keys").write_text(
            'restrict,pty,command="P=montalu1" %s\n' % self._pub())
        out = io.StringIO()
        with m.patch("sys.stdout", out):
            cli_session_cwd.print_status("montalu1", home, lambda s: None)
        self.assertIn("forced command: STALE", out.getvalue())
        self.assertIn("append_controller_lane_pubkey_command", out.getvalue())


class TestProjectAccountKeyUsesAccount(unittest.TestCase):
    def _declare_marek(self, user):
        return ([{"name": "mk", "cwd": "~/devel/odoo/odoo-erp"}]
                if user == "marek" else [])

    def test_forced_line_keys_the_declared_lookup_on_the_account(self):
        with m.patch.object(cli_session_cwd.cli_fleet, "box_windows",
                            side_effect=self._declare_marek):
            line = cli_account_bootstrap._forced_command_key_line(
                "marek", KEY, start_dir_chain=["devel/claudy"], account="claudy")
            keys = cli_account_bootstrap.desired_keys_for_service_account("claudy")
        self.assertIn("devel/claudy", line)
        self.assertNotIn("odoo-erp", line)
        forced = [k for k in keys if "command=" in k]
        self.assertTrue(forced)
        for k in forced:
            self.assertNotIn("odoo-erp", k)


class TestHostScopedLocalLookup(unittest.TestCase):
    def test_shared_user_on_another_host_gets_no_guard(self):
        hosts = [{"name": "dev1", "user": "newlevel"},
                 {"name": "spinbike-vps", "user": "newlevel",
                  "windows": [{"name": "sb", "cwd": "~/devel/spinbike"}]}]
        with m.patch.object(cli_fleet, "REMOTE_HOSTS", hosts):
            self.assertEqual(
                cli_session_cwd.launch_guard_cwds("newlevel", hostname="dev1"), "")
            self.assertEqual(
                cli_session_cwd.launch_guard_cwds("newlevel",
                                                  hostname="spinbike-vps"),
                "devel/spinbike")

    def test_unique_stream_user_resolves_without_hostname_match(self):
        self.assertEqual(
            cli_session_cwd.launch_guard_cwds("montalu1", hostname="elsewhere"), M1)


class TestGuardLetsNonSessionCommandsThrough(unittest.TestCase):
    def test_version_update_mcp_run_from_the_parent(self):
        home = _home(self)
        (home / M1 / ".git").mkdir(parents=True)
        lb = home / ".local" / "bin"
        lb.mkdir(parents=True)
        (lb / "claude").write_text("#!/usr/bin/env bash\necho \"CLAUDE_RAN $*\"\n")
        (lb / "claude").chmod(0o755)
        script = home / "launch.sh"
        script.write_text(cli_claude_scripts.render_claude_launch_script(
            cli_session_cwd.render_launch_cwd_guard(M1)))
        for args in (("--version",), ("update",), ("mcp", "list"), ("doctor",)):
            r = subprocess.run(["bash", str(script), "default"] + list(args),
                               cwd=str(home / "devel" / "odoo"),
                               env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                               capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
            self.assertIn("CLAUDE_RAN", r.stdout, args)


class TestStatusNeverRaises(unittest.TestCase):
    def test_a_failing_reader_prints_a_line(self):
        def boom(_session):
            raise RuntimeError("tmux exploded")
        out = io.StringIO()
        with m.patch("sys.stdout", out):
            cli_session_cwd.print_status("montalu1", _home(self), boom)
        self.assertIn("session cwd: check failed", out.getvalue())
        self.assertIn("tmux exploded", out.getvalue())

    def test_cwd_within_accepts_subdir_and_rejects_parent(self):
        self.assertTrue(cli_session_cwd.cwd_within("/h/a/b/c", "/h/a/b"))
        self.assertTrue(cli_session_cwd.cwd_within("/h/a/b", "/h/a/b/"))
        self.assertFalse(cli_session_cwd.cwd_within("/h/a", "/h/a/b"))
        self.assertFalse(cli_session_cwd.cwd_within("/h/a/bc", "/h/a/b"))


if __name__ == "__main__":
    unittest.main()
