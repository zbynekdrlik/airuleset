"""Subdev stream dev-env provisioning (#263, #264).

#263: montalu2/montalu3/montalu4 (and any future subdev stream account) got
every OTHER piece of airuleset's provisioning — the push target, the
launcher wrapper, the tmux boot-time cutover — but nothing ever installed
the `claude` CLI binary itself, and nothing bootstrapped a tmux session with
claude running in it. This extends `cmd_install()` (the same shape as
`check_runtime_deps()`/`ensure_playwright_browsers()`) to close that gap:
`ensure_claude_cli_installed()` (the binary), `ensure_stream_tmux_session()`
(session + claude launched), `report_stream_dev_env()` (loud human-gap
reporting + self-cleaning TODO-PROVISIONING.md).

#264: a subdev stream account's interactive ssh login should attach
straight into its one tmux session instead of the user attaching by hand —
`apply_stream_ssh_attach()`, a new idempotent marker block in ~/.bashrc,
scoped to the exact same AUTHORITY_BY_USER registry.

Both scoped STRICTLY to AUTHORITY_BY_USER's keys (the subdev stream
accounts) — dev1/dev2/gatekeeper are the human's own interactive login and
must never be touched by either feature.
"""

import inspect
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset


class _FakeCP:
    """A subprocess.CompletedProcess stand-in -- just returncode/stdout/stderr."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# #263a: claude CLI binary install
# ---------------------------------------------------------------------------

class TestClaudeCliInstalled(TestCase):
    def test_true_when_which_resolves(self):
        env = {"PATH": "/usr/bin:/bin"}
        with m.patch("shutil.which", return_value="/usr/bin/claude"):
            self.assertTrue(airuleset._claude_cli_installed(env))

    def test_false_when_which_finds_nothing(self):
        env = {"PATH": "/usr/bin:/bin"}
        with m.patch("shutil.which", return_value=None):
            self.assertFalse(airuleset._claude_cli_installed(env))

    def test_defaults_to_claude_cli_env_when_no_env_given(self):
        with m.patch.object(airuleset, "_claude_cli_env",
                             return_value={"PATH": "/x"}) as env_fn, \
                m.patch("shutil.which", return_value=None) as which_fn:
            airuleset._claude_cli_installed()
        env_fn.assert_called_once()
        self.assertEqual(which_fn.call_args.kwargs.get("path"), "/x")


class TestEnsureClaudeCliInstalled(TestCase):
    def test_no_op_when_already_installed(self):
        with m.patch.object(airuleset, "_claude_cli_installed", return_value=True), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})
        run.assert_not_called()

    def test_installs_via_the_official_installer_when_missing(self):
        calls = {"n": 0}

        def fake_installed(env=None):
            calls["n"] += 1
            return calls["n"] > 1  # missing on the check, present after install

        with m.patch.object(airuleset, "_claude_cli_installed",
                             side_effect=fake_installed), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=0)) as run:
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["bash", "-c",
                                 "curl -fsSL https://claude.ai/install.sh | bash"])
        self.assertIn("env", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/x"})

    def test_install_failure_is_loud_but_non_fatal(self):
        out = StringIO()
        with m.patch.object(airuleset, "_claude_cli_installed", return_value=False), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=1, stderr="boom", stdout="")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})   # must not raise
        self.assertIn("claude CLI MISSING", out.getvalue())
        self.assertIn("install.sh", out.getvalue())

    def test_install_exception_is_non_fatal(self):
        out = StringIO()
        with m.patch.object(airuleset, "_claude_cli_installed", return_value=False), \
                m.patch("subprocess.run", side_effect=FileNotFoundError("curl")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})   # must not raise
        self.assertIn("claude CLI MISSING", out.getvalue())


# ---------------------------------------------------------------------------
# #263b: tmux session bootstrap
# ---------------------------------------------------------------------------

class TestStreamSessionCwd(TestCase):
    def test_returns_the_odoo_erp_checkout_when_it_exists(self):
        d = Path(tempfile.mkdtemp())
        checkout = d / "devel" / "odoo" / "odoo-erp"
        checkout.mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            self.assertEqual(airuleset._stream_session_cwd(), checkout)

    def test_falls_back_to_home_when_checkout_missing(self):
        d = Path(tempfile.mkdtemp())
        with m.patch.object(Path, "home", return_value=d):
            self.assertEqual(airuleset._stream_session_cwd(), d)


class TestTmuxSessionExists(TestCase):
    def test_true_on_rc_zero(self):
        def run(argv):
            return _FakeCP(returncode=0)
        self.assertTrue(airuleset._tmux_session_exists("montalu2", run=run))

    def test_false_on_rc_nonzero(self):
        def run(argv):
            return _FakeCP(returncode=1)
        self.assertFalse(airuleset._tmux_session_exists("montalu2", run=run))

    def test_none_when_tmux_unreachable(self):
        def run(argv):
            raise OSError("no such file")
        self.assertIsNone(airuleset._tmux_session_exists("montalu2", run=run))

    def test_calls_has_session_with_the_right_target(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return _FakeCP(returncode=1)

        airuleset._tmux_session_exists("montalu2", run=run)
        self.assertEqual(calls, [["tmux", "has-session", "-t", "montalu2"]])


class TestEnsureStreamTmuxSession(TestCase):
    def test_none_for_a_non_stream_user(self):
        # dev1/dev2/gatekeeper's own linux users are never in AUTHORITY_BY_USER
        result = airuleset.ensure_stream_tmux_session(user="newlevel",
                                                        run=lambda a: _FakeCP())
        self.assertIsNone(result)

    def test_never_touches_an_existing_session(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return _FakeCP(returncode=0)   # has-session says: exists

        result = airuleset.ensure_stream_tmux_session(user="montalu2", run=run)
        self.assertIn("already exists", result)
        # ONLY the has-session probe ran -- no new-session, no send-keys
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["tmux", "has-session"])

    def test_creates_a_new_session_and_launches_claude_when_absent(self):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=1)   # doesn't exist yet
            return _FakeCP(returncode=0)

        d = Path(tempfile.mkdtemp())
        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, launch_script="/x/launch.sh")
        self.assertIn("created session 'montalu2'", result)
        self.assertIn("claude launched", result)
        self.assertTrue(any(
            c[:4] == ["tmux", "new-session", "-d", "-s"] and "montalu2" in c
            for c in calls))
        # cwd falls back to $HOME (no odoo-erp checkout in this fresh tmpdir)
        self.assertTrue(any(str(d) in " ".join(c) for c in calls
                             if c[:2] == ["tmux", "new-session"]))
        self.assertTrue(any(
            c[:3] == ["tmux", "send-keys", "-t"] and "/x/launch.sh default" in " ".join(c)
            for c in calls))

    def test_new_session_uses_the_odoo_erp_checkout_cwd_when_present(self):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=1)
            return _FakeCP(returncode=0)

        d = Path(tempfile.mkdtemp())
        checkout = d / "devel" / "odoo" / "odoo-erp"
        checkout.mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            airuleset.ensure_stream_tmux_session(user="montalu3", run=run)
        new_session_call = next(c for c in calls if c[:2] == ["tmux", "new-session"])
        self.assertIn(str(checkout), new_session_call)

    def test_session_create_failure_is_reported_and_never_sends_keys(self):
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=1)
            if argv[:2] == ["tmux", "new-session"]:
                return _FakeCP(returncode=1, stderr="no server running")
            return _FakeCP(returncode=0)

        result = airuleset.ensure_stream_tmux_session(user="montalu4", run=run)
        self.assertIn("FAILED", result)
        self.assertFalse(any(c[:2] == ["tmux", "send-keys"] for c in calls))

    def test_unreachable_tmux_is_reported_and_left_untouched(self):
        def run(argv):
            raise OSError("tmux missing")
        result = airuleset.ensure_stream_tmux_session(user="marek", run=run)
        self.assertIn("unreachable", result)


# ---------------------------------------------------------------------------
# #263c: human-gap reporting + TODO-PROVISIONING.md self-cleanup
# ---------------------------------------------------------------------------

class TestStreamProvisioningGaps(TestCase):
    def _home(self, claude_creds=False, gh_hosts=False, git_creds=False):
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        if claude_creds:
            (d / ".claude" / ".credentials.json").write_text("{}")
        if gh_hosts:
            (d / ".config" / "gh").mkdir(parents=True)
            (d / ".config" / "gh" / "hosts.yml").write_text("github.com:\n")
        if git_creds:
            (d / ".git-credentials").write_text("https://x:y@github.com\n")
        return d

    def test_both_gaps_reported_on_a_fresh_account(self):
        d = self._home()
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps("montalu2")
        self.assertEqual(len(gaps), 2)
        self.assertTrue(any("OAuth" in g or "login" in g for g in gaps))
        self.assertTrue(any("PAT" in g or "gh auth" in g for g in gaps))

    def test_no_gaps_once_both_are_satisfied(self):
        d = self._home(claude_creds=True, gh_hosts=True)
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps("montalu2")
        self.assertEqual(gaps, [])

    def test_git_credentials_alone_also_satisfies_the_pat_gap(self):
        d = self._home(claude_creds=True, git_creds=True)
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps("montalu2")
        self.assertEqual(gaps, [])

    def test_empty_credentials_file_does_not_count_as_present(self):
        d = self._home()
        (d / ".claude" / ".credentials.json").write_text("")
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps("montalu2")
        self.assertTrue(any("OAuth" in g or "login" in g for g in gaps))


class TestReportStreamDevEnv(TestCase):
    def test_no_op_for_non_stream_user(self):
        out = StringIO()
        with m.patch("sys.stdout", out):
            airuleset.report_stream_dev_env(user="newlevel")
        self.assertEqual(out.getvalue(), "")

    def test_reports_gaps_loudly_and_leaves_todo_file_in_place(self):
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        todo = d / "TODO-PROVISIONING.md"
        todo.write_text("STILL MISSING: ...\n")
        out = StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertIn("gap(s)", out.getvalue())
        self.assertTrue(todo.exists())

    def test_removes_todo_file_once_fully_provisioned(self):
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        (d / ".claude" / ".credentials.json").write_text("{}")
        (d / ".config" / "gh").mkdir(parents=True)
        (d / ".config" / "gh" / "hosts.yml").write_text("github.com:\n")
        todo = d / "TODO-PROVISIONING.md"
        todo.write_text("STILL MISSING: ...\n")
        out = StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertIn("Removed", out.getvalue())
        self.assertFalse(todo.exists())

    def test_no_todo_file_and_fully_provisioned_prints_nothing(self):
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        (d / ".claude" / ".credentials.json").write_text("{}")
        (d / ".config" / "gh").mkdir(parents=True)
        (d / ".config" / "gh" / "hosts.yml").write_text("github.com:\n")
        out = StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertEqual(out.getvalue(), "")


# ---------------------------------------------------------------------------
# #264: subdev ssh auto-attach
# ---------------------------------------------------------------------------

class TestApplyStreamSshAttach(TestCase):
    def _tmp(self, content=None):
        d = tempfile.mkdtemp()
        p = Path(d) / ".bashrc"
        if content is not None:
            p.write_text(content)
        return p

    def test_adds_block_for_a_stream_account(self):
        p = self._tmp("# existing content\n")
        changed = airuleset.apply_stream_ssh_attach(p, user="montalu2")
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_START, text)
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_END, text)
        self.assertIn("exec tmux new-session -A -s", text)

    def test_never_added_for_dev1_style_user(self):
        p = self._tmp("# existing content\n")
        changed = airuleset.apply_stream_ssh_attach(p, user="newlevel")
        self.assertFalse(changed)
        self.assertNotIn(airuleset.STREAM_SSH_ATTACH_MARK_START, p.read_text())

    def test_removes_block_from_a_non_stream_account_if_ever_present(self):
        p = self._tmp(f"# before\n{airuleset.STREAM_SSH_ATTACH_BLOCK}\n# after\n")
        changed = airuleset.apply_stream_ssh_attach(p, user="newlevel")
        self.assertTrue(changed)
        text = p.read_text()
        self.assertNotIn(airuleset.STREAM_SSH_ATTACH_MARK_START, text)
        self.assertIn("# before", text)
        self.assertIn("# after", text)

    def test_idempotent_second_call_is_a_no_op(self):
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        changed = airuleset.apply_stream_ssh_attach(p, user="montalu2")
        self.assertFalse(changed)

    def test_guarded_for_interactive_ssh_tty_and_not_already_in_tmux(self):
        # the three non-negotiable guards from the design comment
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn('$- == *i*', block)          # interactive shell
        self.assertIn('SSH_TTY', block)             # a real ssh TTY
        self.assertIn('-z "${TMUX:-}"', block)       # not already inside tmux

    def test_creates_file_when_absent(self):
        d = tempfile.mkdtemp()
        p = Path(d) / ".bashrc"
        changed = airuleset.apply_stream_ssh_attach(p, user="marek")
        self.assertTrue(changed)
        self.assertTrue(p.exists())

    def test_never_touches_content_outside_the_markers(self):
        p = self._tmp("alias ll='ls -alF'\nexport FOO=bar\n")
        airuleset.apply_stream_ssh_attach(p, user="david")
        text = p.read_text()
        self.assertIn("alias ll='ls -alF'", text)
        self.assertIn("export FOO=bar", text)


class TestStreamMarkerBlockSpansSafety(TestCase):
    """#235's own documented corruption class: a lazy regex `.*?` block scan
    silently deletes real content between a stray leftover START and the
    NEAREST end on a second run against an externally-corrupted file. The
    positional span scan must self-heal instead — skip an unpaired/crossed
    marker, treat it as inert text, never merge it with anything else."""

    def test_a_stray_unpaired_start_does_not_eat_content_up_to_a_later_end(self):
        corrupted = (
            f"{airuleset.STREAM_SSH_ATTACH_MARK_START}\n"
            "# stray leftover start with no matching end anywhere before this\n"
            "IMPORTANT_UNRELATED_LINE=1\n"
            f"{airuleset.STREAM_SSH_ATTACH_BLOCK}\n"
        )
        p = Path(tempfile.mkdtemp()) / ".bashrc"
        p.write_text(corrupted)
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        # run it AGAIN -- the second run is where the lazy-regex bug bites
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        self.assertIn("IMPORTANT_UNRELATED_LINE=1", p.read_text())


# ---------------------------------------------------------------------------
# cmd_install wiring
# ---------------------------------------------------------------------------

class TestInstallWiresDevEnvProvisioning(TestCase):
    def test_cmd_install_calls_ensure_claude_cli_installed(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("ensure_claude_cli_installed()", src)

    def test_cmd_install_calls_ensure_stream_tmux_session(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("ensure_stream_tmux_session()", src)

    def test_cmd_install_calls_apply_stream_ssh_attach(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("apply_stream_ssh_attach()", src)

    def test_cmd_install_calls_report_stream_dev_env(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertIn("report_stream_dev_env()", src)


# ---------------------------------------------------------------------------
# cmd_push: the remote-deploy timeout/stderr companion fix (#263)
# ---------------------------------------------------------------------------

class TestPushRemoteDeployTimeoutAndStderr(TestCase):
    def test_timeout_constant_is_generous_enough_for_a_first_time_install(self):
        # the old bound (60s) is not enough headroom for a curl-install of
        # the claude CLI binary plus ensure_playwright_browsers()'s npx
        # install on a slow link; must be meaningfully larger.
        self.assertGreaterEqual(airuleset.REMOTE_DEPLOY_TIMEOUT_S, 180)

    def test_cmd_push_uses_the_named_timeout_constant(self):
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("REMOTE_DEPLOY_TIMEOUT_S", src)
        self.assertNotIn("timeout=60,", src)

    def test_cmd_push_catches_timeout_expired_around_the_ssh_call(self):
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("subprocess.TimeoutExpired", src)

    def test_cmd_push_surfaces_stderr_on_a_successful_remote_call(self):
        # a successful remote install's own loud "MISSING"/"gap" warnings
        # go to stderr -- cmd_push used to discard it entirely on success.
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("stderr_out", src)


if __name__ == "__main__":
    main()
