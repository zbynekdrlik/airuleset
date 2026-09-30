"""#1201 scope extension: ``airuleset.py accounts verify <acct>`` — the go-live
gate of a project account, run AS the account over its ssh identity.

The REAL rendered probe is executed by the fake ``run`` (``bash -c`` of the
command the ssh argv carries) under a fake home with PATH stubs, so the probe
bash itself is test-locked — never only canned stdout (the #1199 lesson: a
canned-stdout test let a redirect-order mutation survive). The "system" tools
live in a directory OUTSIDE the fake home; a per-account copy is a stub inside
it.
"""
import io
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cli_account_bootstrap as bootstrap  # noqa: E402
import cli_account_session as session  # noqa: E402
import cli_account_verify as verify  # noqa: E402
import cli_accounts as accounts  # noqa: E402
import cli_playwright_mcp as pw  # noqa: E402
import cli_webterm  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
import cli_webterm_only as wo  # noqa: E402

_FAKE_TIMO = "ssh-ed25519 " + "AAAA" + "X" * 64 + " webterm-timo-controller"
_NOREPLY = "12345+zbynekdrlik@users.noreply.github.com"


def _stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


class _World:
    """A fake fohmixer home + a fake system toolchain outside it. Every knob
    below is one way the live account can be broken; the probe (the REAL
    rendered bash) reads it through the stubs or the environment."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.home = self.tmp / "home" / "fohmixer"
        self.sysbin = self.tmp / "sys" / "bin"
        self.pw = self.tmp / "sys" / "ms-playwright"
        self.project = self.home / "devel" / "fohmixer"
        self.project.mkdir(parents=True)
        self.pane_cmd = ["bash", "-l", "-c", "sleep 60; :"]  # `; :` keeps bash (no exec)
        self.knobs = {"VISIBILITY": "PUBLIC", "DEFAULT_COMMAND": "", "RUFF_RC": "0",
                      "RUFF_VER": "0.16.2", "FMT_RC": "0", "CLIPPY_RC": "0",
                      "SHELL_RC": "0", "RUSTUP_HOME_OUT": "/opt/rust/rustup",
                      "NPX_MODE": "ok"}
        self.email = _NOREPLY
        self.browsers_path = str(self.pw)
        self.drop_local_bin = False
        self.home_path_first = []     # dirs put BEFORE the system bin
        self.probe_rc = None
        self.calls = []
        self._make()

    def _make(self):
        s = self.sysbin
        _stub(s / "tmux", 'case "$1" in\n'
              '  has-session) exit 0 ;;\n'
              '  show-options) echo "$DEFAULT_COMMAND" ;;\n'
              '  list-panes) cat "$PANES" ;;\n'
              'esac\n')
        _stub(self.home / ".local/bin/claude", 'echo "2.1.290 (Claude Code)"\n')
        _stub(s / "ruff", 'echo "ruff $RUFF_VER"; exit "$RUFF_RC"\n')
        _stub(s / "cargo", 'case "$1" in fmt) exit "$FMT_RC" ;; clippy) exit "$CLIPPY_RC" ;; esac\n')
        _stub(s / "rustfmt", "exit 0\n")
        _stub(s / "cargo-clippy", "exit 0\n")
        _stub(s / "rustup", 'case "$1" in\n'
              '  show) echo "$RUSTUP_HOME_OUT" ;;\n'
              '  which) echo /opt/rust/rustup/toolchains/1.98.1/bin/rustfmt ;;\n'
              'esac\n')
        _stub(s / "gh", 'echo "$VISIBILITY"\n')
        # `npx -y playwright@1.58.2 install --dry-run chromium webkit`
        _stub(s / "npx", '[ "$NPX_MODE" = fail ] && exit 1\n'
              'echo "browser: chromium version 1"\n'
              'echo "  Install location:    $PW_ROOT/chromium-1208"\n'
              'echo "  Install location:    $PW_ROOT/webkit-2140"\n'
              '[ "$NPX_MODE" = missing ] && echo "  Install location:    $PW_ROOT/ffmpeg-9"\n'
              'exit 0\n')
        for name in ("chromium-1208", "webkit-2140"):
            (self.pw / name).mkdir(parents=True)
            (self.pw / name / "INSTALLATION_COMPLETE").touch()
        shell = self.pw / ("chromium_headless_shell-%s" % pw.PLAYWRIGHT_CHROMIUM_BUILD
                           ) / "chrome-headless-shell-linux64" / "chrome-headless-shell"
        _stub(shell, 'exit "$SHELL_RC"\n')
        claude = self.home / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "airuleset-playwright-browsers-path").write_text(str(self.pw))
        key = session.project_key("/home/fohmixer/devel/fohmixer")
        (claude / "projects" / key).mkdir(parents=True)
        (claude / "projects" / key / "1b11a9f0.jsonl").write_text("{}\n")
        (self.home / ".profile").write_text("# the #1201 block landed before the pane\n")
        old = time.time() - 3600
        os.utime(self.home / ".profile", (old, old))
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        ssh = self.home / ".ssh"
        ssh.mkdir()
        with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS, {"timo": _FAKE_TIMO}):
            lines = bootstrap.desired_keys_for_service_account("fohmixer")
        (ssh / "authorized_keys").write_text("".join(ln + "\n" for ln in lines))

    def run(self, argv, **kw):
        """The fake ssh: execute the probe command (``bash -lc <probe>``)."""
        self.calls.append(argv)
        if self.probe_rc is not None:
            return subprocess.CompletedProcess(argv, self.probe_rc, "", "ssh: refused")
        cmd = shlex.split(argv[-1])
        assert cmd[:2] == ["bash", "-lc"], cmd
        if self.project.is_dir():
            subprocess.run(["git", "-C", str(self.project), "config", "user.email",
                            self.email], check=True)
        pane = subprocess.Popen(self.pane_cmd)
        try:
            panes = self.tmp / "panes"
            panes.write_text("%d\n" % pane.pid)
            local = [] if self.drop_local_bin else [str(self.home / ".local/bin")]
            path = ":".join(self.home_path_first + local
                            + [str(self.sysbin), "/usr/bin", "/bin"])
            env = dict(self.knobs, HOME=str(self.home), PATH=path, PANES=str(panes),
                       PLAYWRIGHT_BROWSERS_PATH=self.browsers_path, PW_ROOT=str(self.pw))
            return subprocess.run(["bash", "-c", cmd[2]], env=env, text=True,
                                  capture_output=True, timeout=60)
        finally:
            pane.kill()
            pane.wait()


class _Base(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="verify-1201-")
        self.addCleanup(subprocess.run, ["rm", "-rf", tmp])
        self.w = _World(tmp)
        self.gh_rc = 0

    def verify(self, **kw):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(wo.WEBTERM_CONTROLLER_LANE_PUBKEYS, {"timo": _FAKE_TIMO}), \
                redirect_stdout(out), redirect_stderr(err):
            rc = verify.verify_account("fohmixer", run=self.w.run,
                                       gh_verify=lambda a: self.gh_rc, **kw)
        self.out = out.getvalue()
        self.err = err.getvalue()
        return rc

    def line(self, check):
        (ln,) = [x for x in self.out.splitlines()
                 if x.startswith("accounts verify fohmixer: %s " % check)]
        return ln


class TestGreen(_Base):

    def test_a_complete_account_passes_every_check(self):
        self.assertEqual(self.verify(), 0, self.out + self.err)
        for check in verify.CHECKS:
            self.assertIn(" OK — ", self.line(check), self.out)
        self.assertIn("ALL %d CHECKS OK" % len(verify.CHECKS), self.out)
        self.assertIn("declared 1.58.2 installed", self.line("playwright"))
        self.assertIn("ruff 0.16.2", self.line("ruff"))

    def test_the_probe_runs_as_the_account_over_its_identity(self):
        self.verify()
        (argv,) = self.w.calls
        self.assertEqual(argv[:3], ["ssh", "-i", os.path.expanduser(
            "~/.secrets/airuleset_push_ed25519")])
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("fohmixer@100.104.8.125", argv)
        self.assertTrue(argv[-1].startswith("bash -lc "))


class TestFailures(_Base):

    def assertFails(self, check, needle, **kw):
        self.assertEqual(self.verify(**kw), 1, self.out)
        self.assertIn(" FAIL — ", self.line(check))
        self.assertIn(needle, self.line(check))
        self.assertIn("NOT live", self.err)

    # tmux
    def test_a_non_login_pane_fails(self):
        self.w.pane_cmd = ["sleep", "60"]
        self.assertFails("tmux", "is not a login shell")

    def test_a_pane_older_than_the_profile_block_fails(self):
        future = time.time() + 3600
        os.utime(self.w.home / ".profile", (future, future))
        self.assertFails("tmux", "respawn")

    def test_a_non_login_default_command_fails(self):
        self.w.knobs["DEFAULT_COMMAND"] = "bash"
        self.assertFails("tmux", "default-command")

    def test_a_login_path_without_local_bin_fails(self):
        self.w.drop_local_bin = True
        self.assertFails("tmux", "the login PATH has no")

    def test_a_missing_claude_fails(self):
        (self.w.home / ".local/bin/claude").unlink()
        self.assertFails("tmux", "claude --version")

    # ruff
    def test_a_tool_in_the_home_fails(self):
        _stub(self.w.home / ".local/bin/ruff", 'echo "ruff 0.16.2"\n')
        self.assertFails("ruff", str(self.w.home / ".local/bin/ruff"))

    def test_a_failing_ruff_fails(self):
        self.w.knobs["RUFF_RC"] = "1"
        self.assertFails("ruff", "ruff --version")

    def test_a_ruff_off_the_declared_pin_fails(self):
        self.w.knobs["RUFF_VER"] = "0.15.0"
        self.assertFails("ruff", "the declared pin is 0.16.2")

    # rust
    def test_a_rustup_in_the_home_fails(self):
        own = self.w.home / ".cargo" / "bin"
        _stub(own / "cargo", "exit 0\n")
        self.w.home_path_first = [str(own)]
        self.assertFails("rust", str(own / "cargo"))

    def test_a_missing_pinned_toolchain_fails(self):
        self.w.knobs["FMT_RC"] = "1"
        self.assertFails("rust", "cargo fmt --version")

    def test_a_failing_clippy_fails(self):
        self.w.knobs["CLIPPY_RC"] = "1"
        self.assertFails("rust", "cargo clippy --version")

    def test_a_rustup_home_in_the_home_fails(self):
        self.w.knobs["RUSTUP_HOME_OUT"] = str(self.w.home / ".rustup")
        self.assertFails("rust", "RUSTUP_HOME")

    def test_a_missing_checkout_fails(self):
        subprocess.run(["rm", "-rf", str(self.w.project)], check=True)
        self.assertFails("rust", "the project checkout is missing")

    # playwright
    def test_playwright_marker_in_the_home_fails(self):
        (self.w.home / ".claude" / "airuleset-playwright-browsers-path").write_text(
            str(self.w.home / ".cache" / "ms-playwright"))
        self.assertFails("playwright", "MCP browsers marker")

    def test_a_browsers_path_in_the_home_fails(self):
        self.w.browsers_path = str(self.w.home / ".cache" / "ms-playwright")
        self.assertFails("playwright", "PLAYWRIGHT_BROWSERS_PATH")

    def test_a_headless_shell_that_does_not_launch_fails(self):
        self.w.knobs["SHELL_RC"] = "127"
        self.assertFails("playwright", "does not launch")

    def test_a_declared_version_with_a_missing_browser_fails(self):
        self.w.knobs["NPX_MODE"] = "missing"
        self.assertFails("playwright", "ffmpeg-9 is incomplete")

    def test_a_declared_version_whose_dry_run_fails_fails(self):
        self.w.knobs["NPX_MODE"] = "fail"
        self.assertFails("playwright", "playwright 1.58.2: its install dry run failed")

    # the rest
    def test_leftover_toolchain_copies_fail(self):
        (self.w.home / ".rustup").mkdir()
        (self.w.home / ".cache" / "ms-playwright").mkdir(parents=True)
        self.assertFails("home-duplicates", "~/.rustup, ~/.cache/ms-playwright")

    def test_cargo_registry_cache_is_not_a_duplicate(self):
        (self.w.home / ".cargo" / "registry").mkdir(parents=True)
        self.assertEqual(self.verify(), 0, self.out)

    def test_public_repo_without_noreply_fails(self):
        self.w.email = "someone@student.example"
        self.assertFails("git-identity", "someone@student.example")

    def test_private_repo_needs_no_noreply(self):
        self.w.email = "someone@student.example"
        self.w.knobs["VISIBILITY"] = "PRIVATE"
        self.assertEqual(self.verify(), 0, self.out)
        self.assertIn("n/a (private repo)", self.line("git-identity"))

    def test_no_conversation_fails_unless_no_transfer(self):
        key = session.project_key("/home/fohmixer/devel/fohmixer")
        for f in (self.w.home / ".claude" / "projects" / key).iterdir():
            f.unlink()
        self.assertFails("session", "transfer-session fohmixer")
        self.assertEqual(self.verify(no_transfer=True), 0, self.out)

    def test_a_missing_forced_command_line_fails(self):
        ak = self.w.home / ".ssh" / "authorized_keys"
        ak.write_text("".join(ln + "\n" for ln in ak.read_text().splitlines()
                              if "webterm-timo" not in ln))
        self.assertFails("webterm", "webterm-timo-controller")

    def test_a_human_without_the_tab_fails(self):
        with mock.patch.object(profiles, "timo_inventory", return_value=[]):
            self.assertFails("webterm", "timo has no tab to fohmixer@dev1")

    def test_a_dashboard_list_without_the_tab_fails(self):
        tabs = dict(cli_webterm.WEBTERM_DASHBOARD_TABS)
        tabs["marek"] = [t for t in tabs["marek"] if t != "fohmixer"]
        with mock.patch.object(cli_webterm, "WEBTERM_DASHBOARD_TABS", tabs):
            self.assertFails("webterm", "marek's dashboard list lacks 'fohmixer'")

    def test_gh_token_failure_fails(self):
        self.gh_rc = 1
        self.assertFails("gh-token", "FAILED")

    def test_an_account_without_github_app_fails(self):
        spec = dict(bootstrap.SERVICE_ACCOUNTS["fohmixer"])
        spec.pop("github_app")
        spec.pop("repo_secrets")
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"fohmixer": spec}):
            self.assertFails("gh-token", "no github_app declared")
            self.assertIn(" FAIL — ", self.line("secret-sync"))

    def test_an_ssh_failure_fails_every_remote_check(self):
        self.w.probe_rc = 255
        self.assertEqual(self.verify(), 1)
        for check in ("tmux", "ruff", "rust", "playwright", "session", "webterm"):
            self.assertIn("ssh probe as fohmixer failed", self.line(check))
        self.assertIn(" OK — ", self.line("secret-sync"))


class TestLoginShell(unittest.TestCase):

    def test_login_shapes(self):
        for cmd in ("-bash", "-zsh", "bash -l", "/bin/bash -l", "bash --login",
                    "bash -lc set -m; exec bash -l", "bash -il"):
            self.assertTrue(verify._is_login(cmd), cmd)

    def test_non_login_shapes(self):
        for cmd in ("bash", "sh", "ls -la", "claude", "bash script.sh -l",
                    "-foo", "python3 -l", ""):
            self.assertFalse(verify._is_login(cmd), cmd)


class TestSecretSyncDeclaration(unittest.TestCase):

    def test_missing_allow_list_fails(self):
        ok, why = verify._check_secret_sync({"github_app": True, "repo": "o/r"})
        self.assertFalse(ok)
        self.assertIn("repo_secrets", why)

    def test_declared_allow_list_passes(self):
        ok, _ = verify._check_secret_sync(bootstrap.account_spec("fohmixer"))
        self.assertTrue(ok)

    def test_a_declared_empty_allow_list_passes(self):
        ok, why = verify._check_secret_sync(
            {"github_app": True, "repo": "o/r", "repo_secrets": []})
        self.assertTrue(ok)
        self.assertIn("declared empty", why)


class TestCli(unittest.TestCase):

    def _cmd(self, argv):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        accounts.register_parser(sub)
        args = parser.parse_args(argv)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            return accounts.cmd_accounts(args), err.getvalue()

    def test_verify_needs_an_account(self):
        rc, err = self._cmd(["accounts", "verify"])
        self.assertEqual(rc, 2)
        self.assertIn("accounts verify <account>", err)

    def test_unknown_account_is_refused(self):
        rc, err = self._cmd(["accounts", "verify", "newlevel"])
        self.assertEqual(rc, 2)
        self.assertIn("not a declared project account", err)

    def test_verify_dispatches_with_no_transfer(self):
        with mock.patch.object(verify, "verify_account", return_value=0) as v:
            rc, _ = self._cmd(["accounts", "verify", "fohmixer", "--no-transfer"])
        self.assertEqual(rc, 0)
        v.assert_called_once_with("fohmixer", no_transfer=True)


class TestPlaybook(unittest.TestCase):

    def test_migration_recipe_makes_verify_the_last_step(self):
        text = (ROOT / ".claude" / "rules" / "internals-accounts.md").read_text()
        self.assertIn("accounts verify <acct>", text)
        self.assertIn("LAST", text)


if __name__ == "__main__":
    unittest.main()
