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
import subprocess
import sys
import tempfile
import unittest.mock as m
from io import StringIO
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_remote  # noqa: E402  (#433 L-E seam re-target)
# #433 cluster L: the installers moved here; a leaf→leaf internal call
# (ensure_claude_cli_installed → _claude_cli_installed → _claude_cli_env)
# resolves in this leaf, so those helpers are patched via cli_binary_installers.
import cli_binary_installers


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

    def test_false_when_which_and_login_shell_both_find_nothing(self):
        env = {"PATH": "/usr/bin:/bin"}
        with m.patch("shutil.which", return_value=None), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=1, stdout="")):
            self.assertFalse(airuleset._claude_cli_installed(env))

    def test_true_via_login_shell_fallback_when_which_finds_nothing(self):
        # #263 review finding: an account whose PATH machinery only
        # resolves `claude` through a LOGIN shell (nvm, .profile, etc.)
        # must not read as "missing" and get a second, shadowing install.
        env = {"PATH": "/usr/bin:/bin"}
        with m.patch("shutil.which", return_value=None), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=0,
                                             stdout="/opt/claude/claude\n")) as run:
            self.assertTrue(airuleset._claude_cli_installed(env))
        argv = run.call_args[0][0]
        self.assertEqual(argv, ["bash", "-lc", "command -v claude"])

    def test_login_shell_fallback_exception_is_treated_as_missing(self):
        env = {"PATH": "/usr/bin:/bin"}
        with m.patch("shutil.which", return_value=None), \
                m.patch("subprocess.run", side_effect=OSError("no bash")):
            self.assertFalse(airuleset._claude_cli_installed(env))   # must not raise

    def test_defaults_to_claude_cli_env_when_no_env_given(self):
        with m.patch.object(cli_binary_installers, "_claude_cli_env",
                             return_value={"PATH": "/x"}) as env_fn, \
                m.patch("shutil.which", return_value=None) as which_fn, \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=1, stdout="")):
            airuleset._claude_cli_installed()
        env_fn.assert_called_once()
        self.assertEqual(which_fn.call_args.kwargs.get("path"), "/x")


class TestEnsureClaudeCliInstalled(TestCase):
    def test_no_op_when_already_installed(self):
        with m.patch.object(cli_binary_installers, "_claude_cli_installed", return_value=True), \
                m.patch("subprocess.run") as run:
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})
        run.assert_not_called()

    def test_installs_via_the_official_installer_when_missing(self):
        calls = {"n": 0}

        def fake_installed(env=None):
            calls["n"] += 1
            return calls["n"] > 1  # missing on the check, present after install

        with m.patch.object(cli_binary_installers, "_claude_cli_installed",
                             side_effect=fake_installed), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=0)) as run:
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})
        run.assert_called_once()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "bash")
        self.assertEqual(argv[1], "-c")
        # pipefail so a curl failure isn't masked by bash's own exit code
        self.assertIn("set -o pipefail", argv[2])
        self.assertIn("curl -fsSL https://claude.ai/install.sh | bash", argv[2])
        self.assertIn("env", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/x"})

    def test_install_failure_is_loud_but_non_fatal(self):
        out = StringIO()
        with m.patch.object(cli_binary_installers, "_claude_cli_installed", return_value=False), \
                m.patch("subprocess.run",
                        return_value=m.Mock(returncode=1, stderr="boom", stdout="")), \
                m.patch("sys.stderr", out):
            airuleset.ensure_claude_cli_installed({"PATH": "/x"})   # must not raise
        self.assertIn("claude CLI MISSING", out.getvalue())
        self.assertIn("install.sh", out.getvalue())

    def test_install_exception_is_non_fatal(self):
        out = StringIO()
        with m.patch.object(cli_binary_installers, "_claude_cli_installed", return_value=False), \
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

    # --- #563: cwd fallback CHAIN. montalu1's real project dir is
    # ~/devel/odoo (no odoo-erp subdir), so the old binary "odoo-erp or
    # $HOME" fallback dropped it into $HOME (wrong project key = no
    # history/memory). The chain tries odoo-erp, THEN devel/odoo, THEN $HOME.

    def test_returns_devel_odoo_when_only_that_exists(self):
        d = Path(tempfile.mkdtemp())
        (d / "devel" / "odoo").mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            self.assertEqual(airuleset._stream_session_cwd(),
                             d / "devel" / "odoo")

    def test_odoo_erp_wins_when_both_dirs_exist(self):
        # priority preserved: odoo-erp is first in the chain, so an account
        # WITH the odoo-erp checkout still lands there, not one level up.
        d = Path(tempfile.mkdtemp())
        (d / "devel" / "odoo" / "odoo-erp").mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            self.assertEqual(airuleset._stream_session_cwd(),
                             d / "devel" / "odoo" / "odoo-erp")


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

    def test_calls_has_session_with_an_exact_match_target(self):
        # #263 review finding: a bare `-t name` does PREFIX matching (tmux
        # 3.7b, live-verified) -- `has-session -t montalu2` reports "exists"
        # even when only `montalu2-review` is alive. `-t "=name"` anchors
        # to an EXACT match; without it this check silently reports a
        # not-yet-provisioned account as already done, forever.
        calls = []

        def run(argv):
            calls.append(argv)
            return _FakeCP(returncode=1)

        airuleset._tmux_session_exists("montalu2", run=run)
        self.assertEqual(calls, [["tmux", "has-session", "-t", "=montalu2"]])


class TestEnsureStreamTmuxSession(TestCase):
    def _sentinel(self):
        # a fresh, nonexistent path per test -- the real default
        # (STREAM_TMUX_BOOTSTRAP_SENTINEL) lives under this machine's own
        # real ~/.claude/ and must never be touched by a test.
        return Path(tempfile.mkdtemp()) / ".airuleset-stream-session-bootstrapped"

    def test_none_for_a_non_stream_user(self):
        # dev1/dev2/gatekeeper's own linux users are never in AUTHORITY_BY_USER
        result = airuleset.ensure_stream_tmux_session(
            user="newlevel", run=lambda a: _FakeCP(), sentinel_path=self._sentinel())
        self.assertIsNone(result)

    def test_never_touches_an_existing_session(self):
        # #308: a MATCHING cwd stays exactly as quiet as before -- the new
        # read-only cwd probe changes nothing observable for the healthy
        # case, and NEITHER call ever mutates anything.
        calls = []
        d = Path(tempfile.mkdtemp())

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)   # has-session says: exists
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=0, stdout=str(d) + "\n")
            return _FakeCP(returncode=0)

        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=self._sentinel())
        self.assertIn("already exists", result)
        self.assertIn("left untouched", result)
        # ONLY read-only probes ran -- has-session + the cwd check -- no
        # new-session, no send-keys, ever.
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[:2] in (["tmux", "has-session"],
                                      ["tmux", "list-panes"])
                            for c in calls))

    def test_the_cwd_probe_targets_the_session_with_list_panes(self):
        # #308 review CRITICAL: `tmux display-message -t "=<session>"` (no
        # `:<window>` qualifier) resolves to NO PANE at all -- every
        # `#{pane_*}` field expands to empty and it STILL exits 0
        # (live-verified against this box's own real tmux 3.7b), which made
        # the mismatch check permanently inert: every session, live or not,
        # read back as "can't determine". `list-panes -s -t "=<session>"`
        # correctly targets the session. Lock the ARGV shape itself, not
        # just canned stdout -- a garbage target string would otherwise
        # pass every other test in this class unnoticed.
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)
            return _FakeCP(returncode=1)   # inconclusive either way

        airuleset.ensure_stream_tmux_session(
            user="montalu2", run=run, sentinel_path=self._sentinel())
        probe = next(c for c in calls if c[:2] == ["tmux", "list-panes"])
        self.assertEqual(probe, ["tmux", "list-panes", "-s", "-t", "=montalu2",
                                 "-F", "#{pane_current_path}"])

    def test_a_mismatched_cwd_on_an_existing_session_is_reported_loudly(self):
        # #308 (the miva1 incident): a session created by ANY path other
        # than airuleset's own bootstrap (manual provisioning, before
        # registration) silently wins the first login with the WRONG cwd
        # forever -- neither this function (never touches an existing
        # session, by design) nor the ssh auto-attach's `-A` (ignores `-c`
        # on attach) can ever correct it. The fix is VISIBILITY, never a
        # mutation: report the mismatch loudly, kill/re-cwd nothing.
        calls = []
        d = Path(tempfile.mkdtemp())

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=0, stdout="/home/miva1\n")  # $HOME, wrong
            return _FakeCP(returncode=0)

        checkout = d / "devel" / "odoo" / "odoo-erp"
        checkout.mkdir(parents=True)
        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="miva1", run=run, sentinel_path=self._sentinel())
        self.assertIn("WARNING", result)
        self.assertIn("/home/miva1", result)
        self.assertIn(str(checkout), result)
        self.assertIn("kill it manually", result)
        # #309 adversarial-review MINOR: "re-run push" was never true -- a
        # bootstrapped account's next push always hits the sentinel's
        # "already bootstrapped once -- never re-created" branch, so
        # nothing about a manual kill is ever undone by pushing again. The
        # #264 ssh auto-attach (`tmux new-session -A ... -c`, honored only
        # on CREATE, i.e. only once the session genuinely no longer exists)
        # is what actually rebuilds it, on the operator's NEXT SSH LOGIN.
        self.assertNotIn("re-run push", result)
        self.assertIn("ssh login", result)
        # NEVER auto-kill, NEVER re-cwd, NEVER send keys -- report only.
        self.assertFalse(any(c[:2] == ["tmux", "new-session"] for c in calls))
        self.assertFalse(any(c[:2] == ["tmux", "send-keys"] for c in calls))
        self.assertFalse(any(c[:2] == ["tmux", "kill-session"] for c in calls))

    def test_a_subdirectory_of_the_checkout_is_not_a_mismatch(self):
        # #308 review MAJOR: a raw string compare false-positives on a
        # perfectly healthy session that's simply `cd`'d into a
        # SUBDIRECTORY of the checkout -- routine odoo-erp work (e.g.
        # .../odoo-erp/addons). Containment, not just equality.
        calls = []
        d = Path(tempfile.mkdtemp())
        checkout = d / "devel" / "odoo" / "odoo-erp"
        subdir = checkout / "addons" / "montalu_install_config"
        subdir.mkdir(parents=True)

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=0, stdout=str(subdir) + "\n")
            return _FakeCP(returncode=0)

        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=self._sentinel())
        self.assertNotIn("WARNING", result)
        self.assertIn("already exists", result)

    def test_an_inconclusive_cwd_probe_stays_quiet(self):
        # tmux unreachable for JUST the cwd probe (a transient failure, a
        # session whose pane can't be queried) must never manufacture a
        # false WARNING -- degrade to the pre-#308 quiet message.
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=1, stderr="can't find pane")
            return _FakeCP(returncode=0)

        result = airuleset.ensure_stream_tmux_session(
            user="montalu2", run=run, sentinel_path=self._sentinel())
        self.assertIn("already exists", result)
        self.assertNotIn("WARNING", result)

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
                user="montalu2", run=run, launch_script="/x/launch.sh",
                sentinel_path=self._sentinel())
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
            airuleset.ensure_stream_tmux_session(
                user="montalu3", run=run, sentinel_path=self._sentinel())
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

        result = airuleset.ensure_stream_tmux_session(
            user="montalu4", run=run, sentinel_path=self._sentinel())
        self.assertIn("FAILED", result)
        self.assertFalse(any(c[:2] == ["tmux", "send-keys"] for c in calls))

    def test_unreachable_tmux_is_reported_and_left_untouched(self):
        def run(argv):
            raise OSError("tmux missing")
        result = airuleset.ensure_stream_tmux_session(
            user="marek", run=run, sentinel_path=self._sentinel())
        self.assertIn("unreachable", result)

    def test_unreachable_tmux_never_writes_the_sentinel(self):
        # nothing was decided -- a later push should still get the very
        # first real attempt once tmux becomes reachable.
        sentinel = self._sentinel()

        def run(argv):
            raise OSError("tmux missing")

        airuleset.ensure_stream_tmux_session(
            user="marek", run=run, sentinel_path=sentinel)
        self.assertFalse(sentinel.exists())

    def test_second_call_never_recreates_a_session_the_user_deliberately_killed(self):
        # #263 review CRITICAL finding: the original version re-created (and
        # auto-launched claude into) a session on EVERY install/push run
        # whenever has-session reported "doesn't exist" -- including a
        # session the user had deliberately killed (out of token budget,
        # done for the day). This is the standing, repeatedly-reported user
        # complaint this repo's own memory records ('never touch a session
        # the user deliberately stopped'). The fix: bootstrap exactly ONCE,
        # ever, per account, gated on a sentinel file.
        calls = []

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=1)   # doesn't exist (either fresh, or killed)
            return _FakeCP(returncode=0)

        sentinel = self._sentinel()
        d = Path(tempfile.mkdtemp())
        with m.patch.object(Path, "home", return_value=d):
            first = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=sentinel)
        self.assertIn("created session", first)
        self.assertTrue(sentinel.exists())

        calls.clear()
        with m.patch.object(Path, "home", return_value=d):
            second = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=sentinel)
        self.assertIn("already bootstrapped once", second)
        self.assertIn("never re-created", second)
        # #309: has-session now DOES run on every call, even a bootstrapped
        # one -- that is the whole point of the fix (a later push must still
        # be able to see a session that reappeared with the wrong cwd). The
        # invariant this test actually protects is narrower and unchanged:
        # NO CREATE ever happens a second time -- no new-session, no
        # send-keys, no kill-session -- regardless of how many read-only
        # probe calls ran.
        self.assertEqual(calls, [["tmux", "has-session", "-t", "=montalu2"]])
        self.assertFalse(any(c[:2] in (["tmux", "new-session"],
                                        ["tmux", "send-keys"],
                                        ["tmux", "kill-session"])
                              for c in calls))

    def test_an_already_bootstrapped_accounts_drift_is_still_caught(self):
        # #309: the #308 cwd-mismatch WARNING used to be structurally
        # unreachable for any account whose sentinel was already written by
        # a PRIOR push (the sentinel-exists early return fired before the
        # probe ever ran) -- i.e. every currently-registered stream account.
        # A LATER push for an already-bootstrapped account must still catch
        # a session that has since drifted to the wrong cwd (a future
        # scripted change, an operator mistake, or a session created before
        # the checkout existed and only later diverging) -- never silently
        # report "already bootstrapped, left alone" for a wrong cwd.
        calls = []
        d = Path(tempfile.mkdtemp())

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)   # a session exists
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=0, stdout="/home/montalu2\n")  # wrong cwd
            return _FakeCP(returncode=0)

        checkout = d / "devel" / "odoo" / "odoo-erp"
        checkout.mkdir(parents=True)
        sentinel = self._sentinel()
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("bootstrapped for montalu2\n")   # already bootstrapped
        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=sentinel)
        self.assertIn("WARNING", result)
        self.assertIn("/home/montalu2", result)
        self.assertIn(str(checkout), result)
        # Still never a mutation, on an already-bootstrapped account either.
        self.assertFalse(any(c[:2] == ["tmux", "new-session"] for c in calls))
        self.assertFalse(any(c[:2] == ["tmux", "send-keys"] for c in calls))
        self.assertFalse(any(c[:2] == ["tmux", "kill-session"] for c in calls))

    def test_an_already_bootstrapped_accounts_matching_cwd_stays_quiet(self):
        # #309 companion: the SAME already-bootstrapped account, but the
        # session's cwd genuinely still matches -- the probe now runs every
        # time, but the message stays the same quiet one it always was.
        calls = []
        d = Path(tempfile.mkdtemp())

        def run(argv):
            calls.append(argv)
            if argv[:2] == ["tmux", "has-session"]:
                return _FakeCP(returncode=0)
            if argv[:2] == ["tmux", "list-panes"]:
                return _FakeCP(returncode=0, stdout=str(d) + "\n")   # matches
            return _FakeCP(returncode=0)

        sentinel = self._sentinel()
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("bootstrapped for montalu2\n")
        with m.patch.object(Path, "home", return_value=d):
            result = airuleset.ensure_stream_tmux_session(
                user="montalu2", run=run, sentinel_path=sentinel)
        self.assertNotIn("WARNING", result)
        self.assertIn("already bootstrapped once", result)
        self.assertIn("never re-created", result)
        self.assertFalse(any(c[:2] == ["tmux", "new-session"] for c in calls))
        self.assertFalse(any(c[:2] == ["tmux", "send-keys"] for c in calls))

    def test_sentinel_is_written_even_when_the_session_already_existed(self):
        # the ONE-TIME decision (whether to act or not) is what's recorded,
        # not just "we created something" -- a session that already existed
        # on the very first sighting must also never be re-evaluated later.
        sentinel = self._sentinel()

        def run(argv):
            return _FakeCP(returncode=0)   # has-session: exists

        airuleset.ensure_stream_tmux_session(
            user="montalu2", run=run, sentinel_path=sentinel)
        self.assertTrue(sentinel.exists())


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
            gaps = airuleset._stream_provisioning_gaps()
        self.assertEqual(len(gaps), 2)
        self.assertTrue(any("OAuth" in g or "login" in g for g in gaps))
        self.assertTrue(any("PAT" in g or "gh auth" in g for g in gaps))

    def test_no_gaps_once_both_are_satisfied(self):
        d = self._home(claude_creds=True, gh_hosts=True)
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps()
        self.assertEqual(gaps, [])

    def test_git_credentials_alone_also_satisfies_the_pat_gap(self):
        d = self._home(claude_creds=True, git_creds=True)
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps()
        self.assertEqual(gaps, [])

    def test_empty_credentials_file_does_not_count_as_present(self):
        d = self._home()
        (d / ".claude" / ".credentials.json").write_text("")
        with m.patch.object(Path, "home", return_value=d):
            gaps = airuleset._stream_provisioning_gaps()
        self.assertTrue(any("OAuth" in g or "login" in g for g in gaps))


class TestReportStreamDevEnv(TestCase):
    def test_no_op_for_non_stream_user(self):
        out, err = StringIO(), StringIO()
        with m.patch("sys.stdout", out), m.patch("sys.stderr", err):
            airuleset.report_stream_dev_env(user="newlevel")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")

    def test_reports_gaps_loudly_to_stderr_and_leaves_todo_file_in_place(self):
        # #263 review finding: the gap report must go to STDERR (loud,
        # never buried on stdout among hundreds of routine install lines).
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        todo = d / "TODO-PROVISIONING.md"
        todo.write_text("STILL MISSING: ...\n")
        out, err = StringIO(), StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out), m.patch("sys.stderr", err):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertIn("gap(s)", err.getvalue())
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(todo.exists())

    def test_renames_todo_file_once_fully_provisioned(self):
        # #263 review finding: RENAME, never unlink -- the file is
        # gatekeeper-authored and airuleset cannot recreate it, so a
        # false-positive gap-closed read must not destroy it.
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        (d / ".claude" / ".credentials.json").write_text("{}")
        (d / ".config" / "gh").mkdir(parents=True)
        (d / ".config" / "gh" / "hosts.yml").write_text("github.com:\n")
        todo = d / "TODO-PROVISIONING.md"
        todo.write_text("STILL MISSING: ...\n")
        out, err = StringIO(), StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out), m.patch("sys.stderr", err):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertIn("Renamed", out.getvalue())
        self.assertFalse(todo.exists())
        done = d / "TODO-PROVISIONING.md.done"
        self.assertTrue(done.exists())
        self.assertEqual(done.read_text(), "STILL MISSING: ...\n")

    def test_no_todo_file_and_fully_provisioned_prints_nothing(self):
        d = Path(tempfile.mkdtemp())
        (d / ".claude").mkdir()
        (d / ".claude" / ".credentials.json").write_text("{}")
        (d / ".config" / "gh").mkdir(parents=True)
        (d / ".config" / "gh" / "hosts.yml").write_text("github.com:\n")
        out, err = StringIO(), StringIO()
        with m.patch.object(Path, "home", return_value=d), \
                m.patch("sys.stdout", out), m.patch("sys.stderr", err):
            airuleset.report_stream_dev_env(user="montalu2")
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(err.getvalue(), "")


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

    def test_guarded_against_a_missing_tmux_binary(self):
        # #263 review finding: without this, `exec tmux ...` on a box where
        # tmux is missing/broken fails AFTER the shell has already been
        # replaced -- closing the ssh session outright instead of leaving
        # a working interactive shell behind.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn("command -v tmux", block)

    def test_write_is_atomic_no_tmp_file_left_behind(self):
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        tmp = p.with_suffix(p.suffix + ".airuleset-tmp")
        self.assertFalse(tmp.exists())
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_START, p.read_text())

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

    # --- #562: the gk box `gatekeeper` account also gets the ssh auto-attach
    # block. It is NOT a subdev stream account (not in AUTHORITY_BY_USER), so
    # the eligibility gate is widened by an explicit extra-user set, NOT by
    # adding it to the merge-authority map (which would misclassify it as a
    # stream everywhere downstream). Owner ask (2026-08-19): "uz ma skor vsade
    # po ssh pekne joine do tmux okrem ked sa ssh do gk, tam musim vsetko sam".

    def test_adds_block_for_the_gatekeeper_account(self):
        p = self._tmp("# existing content\n")
        changed = airuleset.apply_stream_ssh_attach(p, user="gatekeeper")
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_START, text)
        self.assertIn(airuleset.STREAM_SSH_ATTACH_MARK_END, text)
        self.assertIn("exec tmux new-session -A -s", text)

    def test_gatekeeper_block_is_the_byte_identical_stream_block(self):
        # #562 is an ELIGIBILITY-only widening -- the block content added for
        # the gatekeeper account must be byte-identical to the reviewed
        # #264/#284 stream block, never a gk-specific variant.
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_ssh_attach(p, user="gatekeeper")
        self.assertIn(airuleset.STREAM_SSH_ATTACH_BLOCK, p.read_text())

    def test_idempotent_second_call_for_gatekeeper_is_a_no_op(self):
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_ssh_attach(p, user="gatekeeper")
        changed = airuleset.apply_stream_ssh_attach(p, user="gatekeeper")
        self.assertFalse(changed)

    def test_widening_is_scoped_to_gatekeeper_not_arbitrary_non_stream_users(self):
        # The widening must stay minimal: newlevel (dev1/dev2) AND any other
        # non-stream, non-gatekeeper account still get NO block. Catches an
        # over-broad gate (e.g. `or True`) that the gatekeeper test alone
        # would not.
        for user in ("newlevel", "root", "somerandomuser"):
            p = self._tmp("# existing content\n")
            changed = airuleset.apply_stream_ssh_attach(p, user=user)
            self.assertFalse(changed, f"{user} must not get the block")
            self.assertNotIn(airuleset.STREAM_SSH_ATTACH_MARK_START,
                             p.read_text(), f"{user} must not get the block")

    def test_ssh_attach_block_cwd_uses_the_fallback_chain(self):
        # #563: the block's cwd must iterate STREAM_DEV_CWD_CHAIN (odoo-erp,
        # then devel/odoo) before $HOME -- the old binary "odoo-erp or $HOME"
        # fallback dropped montalu1 (project dir ~/devel/odoo, no odoo-erp
        # subdir) into $HOME, so a claude launched there wrote under the wrong
        # project key (no history/memory). Derive the expected loop FROM the
        # shared constant so the bash block and the Python _stream_session_cwd()
        # chain can never drift apart (the divergence guard both fresh-context
        # reviews asked for), and so the test is not a duplicated source of
        # truth for the chain order.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        expected_loop = "for __airuleset_rel in " + " ".join(
            airuleset.STREAM_DEV_CWD_CHAIN)
        self.assertIn(expected_loop, block)
        # the old binary fallback (odoo-erp OR $HOME, no intermediate) is gone
        self.assertNotIn('[ -d "$__airuleset_cwd" ] || __airuleset_cwd="$HOME"',
                         block)

    # --- #593: the ssh survivor-join clone is a grouped-session creator, so it
    # must carry the SAME per-session `client-attached destroy-unattached on`
    # hook cli_webterm's #591 clone got -- otherwise, once #591 removed the
    # GLOBAL keep-last sweep, its detached duplicates orphan forever (the #254
    # pile-up, returning for the ssh path).

    def test_survivor_join_clone_carries_the_per_session_destroy_unattached_hook(self):
        # The #591 mechanism reused verbatim: a per-session `client-attached`
        # hook arming `destroy-unattached on`, targeting the join clone by its
        # own name. Same three tokens the webterm clone's hook carries.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn("set-hook", block)
        self.assertIn("client-attached", block)
        self.assertIn("set-option destroy-unattached on", block)

    def test_survivor_join_clone_is_created_detached_named_then_attached(self):
        # `destroy-unattached on` on a zero-client session destroys it
        # IMMEDIATELY, so (like #591's webterm clone) the join clone is created
        # DETACHED (`-d`) with an explicit NAME (`-s`, so the hook can target
        # it -- set-hook -t does not take tmux's `=` anchor), the hook armed,
        # then attached. The old single `exec new-session -t "$survivor"` (no
        # detach, no name, no hook) is gone.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn('new-session -d -t "$__airuleset_survivor" '
                      '-s "$__airuleset_join"', block)
        self.assertIn('exec tmux attach-session -t "$__airuleset_join"', block)
        # the pre-#593 shape (attach-in-one-exec, no hook) must be gone
        self.assertNotIn('exec tmux new-session -t "$__airuleset_survivor"',
                         block)

    def test_survivor_join_clone_name_is_pid_scoped_off_the_base_name(self):
        # A unique per-login name so two concurrent survivor-joins never clash;
        # derived from the base session name (== whoami) + the login shell pid.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn('__airuleset_join="${__airuleset_me}-join-$$"', block)

    def test_stale_284_global_sweep_comment_is_fixed(self):
        # #593 point 2: the #284 comment used to defend against a tmux
        # destroy-unattached sweep as if it still ran; after #591 removed the
        # GLOBAL sweep it no longer does (the survivor-search stays only as
        # harmless defense-in-depth). The comment must say so.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        self.assertIn("no longer happens globally after #591", block)
        # the survivor-search itself is KEPT (defense-in-depth), so its own
        # marker stays present -- this lock is about the comment's framing, not
        # deleting the search.
        self.assertIn("__airuleset_survivor", block)


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

    def test_two_full_blocks_both_rewrite_cleanly_and_preserve_content_between(self):
        two_blocks = (
            f"{airuleset.STREAM_SSH_ATTACH_BLOCK}\n"
            "MID_CONTENT=kept\n"
            f"{airuleset.STREAM_SSH_ATTACH_BLOCK}\n"
        )
        p = Path(tempfile.mkdtemp()) / ".bashrc"
        p.write_text(two_blocks)
        # both copies are already byte-identical to the canonical block, so
        # the FIRST call is a genuine no-op (nothing to rewrite) -- the
        # real assertion is that BOTH blocks survive, distinct, with the
        # mid-content preserved, never merged/deduplicated into one.
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        text1 = p.read_text()
        self.assertIn("MID_CONTENT=kept", text1)
        self.assertEqual(text1.count(airuleset.STREAM_SSH_ATTACH_MARK_START), 2)
        # idempotent on a second run too
        changed2 = airuleset.apply_stream_ssh_attach(p, user="montalu2")
        self.assertFalse(changed2)
        self.assertEqual(p.read_text(), text1)

    def test_known_residual_a_single_isolated_stray_pair_still_loses_its_content(self):
        # Honestly-documented limitation (both functions' own docstrings
        # say so explicitly, per an adversarial review that found the
        # original docstring OVERCLAIMED "self-heals... never loses
        # content"): a single ISOLATED stray START/END pair, with no OTHER
        # marker literal between them, is structurally indistinguishable
        # from "this is genuinely our own block" to a purely positional
        # scan -- so its content IS still lost on rewrite. This test locks
        # that the documented residual stays exactly what the docstring
        # claims, so a future "fix" that silently changes this behavior
        # either way is caught rather than drifting unnoticed.
        corrupted = (
            f"{airuleset.STREAM_SSH_ATTACH_MARK_START}\n"
            "export IMPORTANT_SECRET=xyz\n"
            f"{airuleset.STREAM_SSH_ATTACH_MARK_END}\n"
        )
        p = Path(tempfile.mkdtemp()) / ".bashrc"
        p.write_text(corrupted)
        airuleset.apply_stream_ssh_attach(p, user="montalu2")
        self.assertNotIn("IMPORTANT_SECRET", p.read_text())


# ---------------------------------------------------------------------------
# #284: grouped-session cleanup survivor -- the launcher's exact-name -A
# reattach must not silently orphan a surviving, differently-named sibling
# once #254's destroy-unattached sweep reduces a multi-member group down to
# one iteration-order-arbitrary survivor. Drives the REAL bash content of
# STREAM_SSH_ATTACH_BLOCK through a real `bash -c` interpreter against a
# scripted fake `tmux`/`whoami` on PATH -- never a string/regex proxy for
# shell semantics, which could pass while the actual bash is wrong.
# ---------------------------------------------------------------------------

_FAKE_TMUX_SRC = """#!/usr/bin/env bash
if [ -n "${FAKE_TMUX_LOG:-}" ]; then
  # [%d] argv-count prefix lets a test distinguish "one arg containing a
  # space" from "several separate args" -- plain `"$*"` alone collapses
  # both to the same joined text (#284 adversarial review, MINOR-2
  # regression test). assertIn substring checks elsewhere in this file
  # keep working unmodified since the prefix is just extra leading text.
  printf '[%d] %s\\n' "$#" "$*" >> "$FAKE_TMUX_LOG"
fi
case "$1" in
  has-session)
    rc=1
    if [ -n "${FAKE_TMUX_HAS_SESSION_RC:-}" ] && [ -f "$FAKE_TMUX_HAS_SESSION_RC" ]; then
      rc="$(cat "$FAKE_TMUX_HAS_SESSION_RC")"
    fi
    exit "${rc:-1}"
    ;;
  list-sessions)
    if [ -n "${FAKE_TMUX_LIST_SESSIONS_OUT:-}" ] && [ -f "$FAKE_TMUX_LIST_SESSIONS_OUT" ]; then
      cat "$FAKE_TMUX_LIST_SESSIONS_OUT"
    fi
    exit 0
    ;;
  new-session)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""


class TestGroupSurvivorReattach(TestCase):
    def _run_block(self, has_session_rc=1, list_sessions_out="", whoami="zbynek"):
        d = Path(tempfile.mkdtemp())
        bin_dir = d / "bin"
        bin_dir.mkdir()
        fake_tmux = bin_dir / "tmux"
        fake_tmux.write_text(_FAKE_TMUX_SRC)
        fake_tmux.chmod(0o755)
        fake_whoami = bin_dir / "whoami"
        fake_whoami.write_text("#!/usr/bin/env bash\necho '%s'\n" % whoami)
        fake_whoami.chmod(0o755)
        rc_file = d / "has_session_rc"
        rc_file.write_text(str(has_session_rc))
        list_file = d / "list_sessions_out"
        list_file.write_text(list_sessions_out)
        log_path = d / "tmux.log"
        home_dir = d / "home"
        home_dir.mkdir()
        env = {
            "PATH": "%s:/usr/bin:/bin" % bin_dir,
            "HOME": str(home_dir),
            "SSH_TTY": "/dev/pts/0",
            "FAKE_TMUX_LOG": str(log_path),
            "FAKE_TMUX_HAS_SESSION_RC": str(rc_file),
            "FAKE_TMUX_LIST_SESSIONS_OUT": str(list_file),
        }
        r = subprocess.run(
            ["bash", "--norc", "-i", "-c", airuleset.STREAM_SSH_ATTACH_BLOCK],
            env=env, capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
        )
        log = log_path.read_text() if log_path.exists() else ""
        return log, r

    def test_group_survivor_is_joined_as_an_independent_view(self):
        # #284's own reproduction: exact-name "zbynek" is gone, but
        # "zbynek-4" survives in the SAME group -- the launcher must join
        # it (`new-session -t`), never blindly create a fresh empty one.
        # list-sessions is queried "-F '#{session_group} #{session_name}'"
        # (group FIRST -- see the spacy-name test below), so a fixture
        # line is "<group> <name>".
        log, r = self._run_block(
            has_session_rc=1,
            list_sessions_out="zbynek zbynek-4\nmarek marek-0\n",
            whoami="zbynek",
        )
        # #593: the join is now a DETACHED, NAMED grouped clone (so its own
        # per-session destroy-unattached hook can target it), attached
        # afterwards -- never the old single `new-session -t zbynek-4`.
        self.assertIn("new-session -d -t zbynek-4 -s zbynek-join-", log)
        self.assertIn("set-hook -t zbynek-join-", log)
        self.assertIn("client-attached", log)
        self.assertIn("destroy-unattached on", log)
        self.assertIn("attach-session -t zbynek-join-", log)
        self.assertNotIn("new-session -A -s zbynek", log)
        # MINOR-1 (adversarial review, #284): the exact-name check MUST use
        # tmux's `=`-anchored exact match, never a bare (prefix-matching,
        # per #263) target -- assert the real ARGV, not just the observed
        # outcome, so a regression to a bare target is caught even though
        # this fake `has-session` ignores its own target and would let it
        # silently pass otherwise.
        self.assertIn("has-session -t =zbynek", log)

    def test_no_survivor_falls_back_to_the_exact_name_create_or_attach(self):
        log, r = self._run_block(
            has_session_rc=1,
            list_sessions_out="",
            whoami="zbynek",
        )
        self.assertIn("new-session -A -s zbynek", log)

    def test_exact_name_session_present_skips_the_survivor_scan_entirely(self):
        # the common case (the pre-existing behaviour) must stay exactly as
        # cheap as before -- no extra tmux round-trip when the exact-name
        # session already exists.
        log, r = self._run_block(
            has_session_rc=0,
            list_sessions_out="marek marek-0\n",
            whoami="zbynek",
        )
        self.assertNotIn("list-sessions", log)
        self.assertIn("new-session -A -s zbynek", log)
        self.assertIn("has-session -t =zbynek", log)

    def test_a_sibling_in_a_DIFFERENT_group_is_never_mistaken_for_a_survivor(self):
        # marek-0's group is "marek", not "zbynek" -- must never be joined.
        log, r = self._run_block(
            has_session_rc=1,
            list_sessions_out="marek marek-0\n",
            whoami="zbynek",
        )
        self.assertNotIn("new-session -t marek-0", log)
        self.assertIn("new-session -A -s zbynek", log)

    def test_a_session_name_containing_a_space_is_never_misparsed_as_a_group(self):
        # MINOR-2 (adversarial review, #284): live-reproduced against the
        # real block -- an UNGROUPED session (empty #{session_group})
        # named "cats zbynek" used to print as "cats zbynek " under the
        # old name-then-group field order, which `read -r n g` misparsed
        # as n="cats" g="zbynek" -- a false match on this user's own
        # group, since the embedded space in the NAME collided with the
        # field separator. Group-FIRST ordering ("#{session_group}
        # #{session_name}") avoids this: "" + " " + "cats zbynek" reads
        # as g="cats" n="zbynek", and g != "zbynek" correctly refuses it.
        log, r = self._run_block(
            has_session_rc=1,
            list_sessions_out=" cats zbynek\n",
            whoami="zbynek",
        )
        self.assertNotIn("new-session -t cats", log)
        self.assertNotIn('new-session -t "cats zbynek"', log)
        self.assertIn("new-session -A -s zbynek", log)

    def test_a_grouped_session_whose_name_contains_a_space_is_still_joined_intact(self):
        # the positive counterpart of the above: a REAL survivor whose own
        # name has an embedded space must still be captured WHOLE (never
        # truncated at the space) and passed as ONE argument -- the [%d]
        # argv-count prefix is what proves "my session two" landed as a
        # SINGLE argument. #593: the detached-create argv is
        # `new-session -d -t "my session two" -s "$join"` == 6 args; a naive
        # split of the spacy name would push the count past 6.
        log, r = self._run_block(
            has_session_rc=1,
            list_sessions_out="zbynek my session two\n",
            whoami="zbynek",
        )
        self.assertIn("[6] new-session -d -t my session two -s zbynek-join-",
                      log)

    def test_the_survivor_exec_runs_outside_the_process_substitution_loop(self):
        # CRITICAL-1 (adversarial review, #284, live-verified against a
        # real tmux client + pty): an `exec` sitting INSIDE
        # `while read ...; done < <(tmux list-sessions ...)` inherits that
        # process-substitution PIPE as its own stdin -- a real tmux client
        # then refuses to attach ("open terminal failed: not a terminal")
        # and the ssh login dies right there, since `exec` already
        # replaced the shell. This structural check (the fake tmux here
        # is stdin-indifferent and cannot see the bug itself, per the
        # review's own finding) asserts the source POSITION instead: the
        # captured-survivor `exec` line must appear strictly AFTER the
        # `done < <(...)` line that closes the loop's own redirect.
        block = airuleset.STREAM_SSH_ATTACH_BLOCK
        done_idx = block.index("done < <(tmux list-sessions")
        # #593: the survivor branch now exec's the ATTACH of the named
        # detached clone (after create + set-hook), not a single
        # `new-session -t`; the CRITICAL-1 invariant is unchanged -- this
        # exec must still run AFTER the process-substitution loop closes.
        survivor_exec_idx = block.index(
            'exec tmux attach-session -t "$__airuleset_join"'
        )
        self.assertGreater(
            survivor_exec_idx, done_idx,
            "the survivor exec must run after the process-substitution "
            "while-loop closes, never inside it",
        )
        # and no `exec` line at all sits between the loop's own `while`
        # opener and its `done` -- i.e. nothing execs from inside the body.
        while_idx = block.index("while read -r __airuleset_g __airuleset_n")
        loop_body = block[while_idx:done_idx]
        self.assertNotIn("exec ", loop_body)


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
    def test_timeout_constant_exceeds_the_worst_case_inner_timeout_sum(self):
        # #263 review MAJOR finding: the first version set this to 300s,
        # which is LESS than the sum of the inner best-effort timeouts a
        # single install can burn through in the exact scenario #263 exists
        # for -- check_runtime_deps()'s apt-get (300s PER package),
        # ensure_claude_cli_installed()'s curl-install (180s), and
        # ensure_playwright_browsers()'s npx install (300s) -- up to ~780s.
        # A remote timeout SHORTER than that guarantees the outer ssh call
        # gets killed mid-install on exactly the fresh-account case this
        # whole ticket is about, silently skipping every step after the
        # kill point (managed plugins, file-drop, the watchdog timer).
        #
        # Adversarial-review finding (plugin-marketplace fix, 2026-08-06):
        # ensure_marketplace_registered() adds up to TWO more 150s calls on
        # a fresh account -- one for caveman's marketplace, one (shared,
        # market_ok-cached) for claude-plugins-official, which also covers
        # BOTH superpowers and playwright's own install() calls (180s each).
        # Sum every inner best-effort timeout a single fresh-account remote
        # install can burn through in sequence: apt-get(300) + claude CLI
        # curl(180) + caveman marketplace-add(150) + caveman install(120) +
        # managed-plugins marketplace-add(150) + superpowers install(180) +
        # playwright install(180) + npx playwright browsers(300) = 1560s.
        worst_case_inner_sum = 300 + 180 + 150 + 120 + 150 + 180 + 180 + 300
        self.assertGreater(airuleset.REMOTE_DEPLOY_TIMEOUT_S, worst_case_inner_sum)

    def test_cmd_push_uses_the_named_timeout_constant(self):
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("REMOTE_DEPLOY_TIMEOUT_S", src)
        self.assertNotIn("timeout=60,", src)

    def test_cmd_push_catches_timeout_expired_around_the_ssh_call(self):
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("subprocess.TimeoutExpired", src)

    def test_cmd_push_surfaces_stderr_on_a_successful_remote_call(self):
        # a successful remote install's own loud warnings go to stderr --
        # cmd_push used to discard it entirely on success.
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("stderr_out", src)

    def test_cmd_push_tracks_failures_and_exits_non_zero_when_any_occur(self):
        # #263 review MAJOR finding: before this diff, an uncaught
        # TimeoutExpired propagated out of cmd_push() with a loud
        # traceback and non-zero exit -- impossible to miss. The `continue`
        # needed so ONE slow remote can't abort deployment to every
        # REMAINING host turned that into a single line among hundreds,
        # with the run still ending "All deployments complete." at exit 0
        # -- a SILENT partial deploy. Every failure (timeout or non-zero
        # rc) must be tracked and the whole command must exit non-zero if
        # any occurred.
        src = inspect.getsource(airuleset.cmd_push)
        self.assertIn("failed.append", src)
        self.assertIn("if failed:", src)
        self.assertIn("sys.exit(1)", src)


# ---------------------------------------------------------------------------
# cmd_push: never burn repeated password-auth attempts against an
# unprovisioned/unreachable subdev account (#341)
# ---------------------------------------------------------------------------

class TestPushSshHardeningFlags(TestCase):
    """#341: neither ssh branch capped retries PER connection -- openssh's
    own default (NumberOfPasswordPrompts=3) let a single sshpass call to a
    wrong/unprovisioned account send up to 3 password guesses, and a failed
    pubkey attempt on an identity-based host fell through to an interactive
    password/keyboard-interactive attempt too (BatchMode unset) -- both
    multiply the auth-failure log lines a single connection can generate,
    which is what let 3 new subdev accounts alone trip fail2ban in one
    `push` run.

    #341 adversarial-review F2: the original version of these two tests
    asserted on `inspect.getsource(cmd_push)` -- which ALSO contains the
    explanatory code COMMENT sitting right next to each new `-o` flag, so a
    mutant that deletes the actual argv element while leaving the comment
    in place survives both tests untouched (this repo's own documented
    "a lock test matches its own prose" trap). These now assert on the
    ARGV a fake `subprocess.run` actually records, exactly like the
    soniox-side hardening tests already correctly do."""

    def _fake_run(self, calls):
        import unittest.mock as m

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return m.Mock(returncode=0, stdout="ok", stderr="")
        return fake_run

    def _deploy_calls(self, calls):
        # the deploy leg's remote command always ends in this literal
        # suffix (see cmd_push's own `remote_cmd` string) -- the soniox
        # phase's own remote command never contains it, so this is a safe
        # way to isolate deploy-leg argv from soniox-leg argv.
        return [c for c in calls
                if any("python3 airuleset.py install" in str(a) for a in c)]

    def test_cmd_push_sshpass_branch_argv_caps_password_prompts(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "dev2", "host": "1.2.3.4", "user": "newlevel",
             "repo_path": "~/devel/airuleset"},
        ]
        with m.patch("subprocess.run", side_effect=self._fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
            airuleset.cmd_push(args)
        deploy = self._deploy_calls(calls)
        self.assertEqual(len(deploy), 1)
        self.assertIn("-o", deploy[0])
        self.assertIn("NumberOfPasswordPrompts=1", deploy[0])

    def test_cmd_push_identity_branch_argv_uses_batch_mode(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "gk", "host": "5.6.7.8", "user": "gatekeeper",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        with m.patch("subprocess.run", side_effect=self._fake_run(calls)), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", {}):
            airuleset.cmd_push(args)
        deploy = self._deploy_calls(calls)
        self.assertEqual(len(deploy), 1)
        self.assertIn("-o", deploy[0])
        self.assertIn("BatchMode=yes", deploy[0])


class TestCmdPushNeverReattemptsAuthFailedHostForSoniox(TestCase):
    """#341: an account whose deploy leg already failed with `Permission
    denied` must not be probed a SECOND time by the soniox-key delivery
    phase that runs right after it in the same push -- contacting the same
    known-bad account twice is what tripped subdev's fail2ban and knocked
    out every LATER ssh call in the run (montalu got TimeoutExpired
    mid-ban-onset in the reported incident)."""

    def _fake_run(self, calls, deploy_rc_by_user):
        import unittest.mock as m

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            # a real ssh/sshpass invocation -- the user@host token is always
            # the second-to-last argv element on both call sites.
            user_at_host = str(cmd[-2])
            user = user_at_host.split("@")[0]
            rc = deploy_rc_by_user.get(user, 0)
            if rc:
                # ssh's OWN client-side auth-exhaustion message: rc 255,
                # literally prefixed "<user>@<host>: " -- #341
                # adversarial-review F1's fixture, so the tightened
                # classifier (rc==255 + this exact message shape) still
                # recognizes a REAL auth failure correctly.
                return m.Mock(returncode=rc, stdout="",
                               stderr="%s: Permission denied (publickey,"
                                      "password)." % user_at_host)
            return m.Mock(returncode=0, stdout="ok", stderr="")
        return fake_run

    def test_a_permission_denied_deploy_leg_is_never_reattempted_for_soniox(self):
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "david2@subdev", "host": "9.9.9.9", "user": "david2",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
            {"name": "david@subdev", "host": "9.9.9.9", "user": "david",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        fake_authority = {"david2": "fork-no-merge", "david": "fork-no-merge"}
        with m.patch("subprocess.run",
                     side_effect=self._fake_run(calls, {"david2": 255})), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", fake_authority), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=fake"):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        david2_calls = [c for c in calls if any("david2@" in str(a) for a in c)]
        self.assertEqual(len(david2_calls), 1,
                          "david2 must be contacted at most ONCE across the "
                          "whole push (its own deploy leg only) -- the "
                          "soniox phase must skip an account already known "
                          "to have failed auth this run: %r" % david2_calls)
        david_calls = [c for c in calls if any("david@" in str(a) for a in c)]
        self.assertEqual(len(david_calls), 2,
                          "a healthy sibling account must still get BOTH "
                          "its deploy leg AND its soniox key delivery")

    def test_a_healthy_account_is_never_skipped(self):
        # control: nothing failed this run -- both phases must run normally
        # for every account, exactly as before.
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "marek@subdev", "host": "9.9.9.9", "user": "marek",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        fake_authority = {"marek": "branch-merge"}
        with m.patch("subprocess.run", side_effect=self._fake_run(calls, {})), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", fake_authority), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=fake"):
            airuleset.cmd_push(args)   # must NOT raise
        marek_calls = [c for c in calls if any("marek@" in str(a) for a in c)]
        self.assertEqual(len(marek_calls), 2)

    def test_a_remote_command_failure_is_not_treated_as_an_auth_failure(self):
        # rc != 0 with no "Permission denied" in stderr means auth SUCCEEDED
        # and the remote command itself failed (e.g. a bad `git pull`) --
        # the soniox phase must still be attempted normally for that host.
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "simap@subdev", "host": "9.9.9.9", "user": "simap",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        fake_authority = {"simap": "branch-merge"}
        simap_ssh_calls_seen = {"n": 0}

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if any("simap@" in str(a) for a in cmd):
                simap_ssh_calls_seen["n"] += 1
                if simap_ssh_calls_seen["n"] == 1:
                    # the deploy leg's own remote command failed (e.g. a
                    # `git pull` conflict) -- auth worked, nothing to do
                    # with credentials.
                    return m.Mock(returncode=1, stdout="", stderr="conflict")
            return m.Mock(returncode=0, stdout="ok", stderr="")

        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", fake_authority), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=fake"):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        simap_calls = [c for c in calls if any("simap@" in str(a) for a in c)]
        self.assertEqual(len(simap_calls), 2,
                          "a plain remote-command failure (auth succeeded) "
                          "must NOT suppress the soniox delivery attempt")

    def test_a_remote_side_permission_denied_message_is_not_ssh_auth_failure(self):
        # #341 adversarial-review F1 (MAJOR, TRIGGERED): a REMOTE command's
        # own stderr (forwarded verbatim through ssh's capture_output) can
        # legitimately contain the literal substring "Permission denied"
        # with ssh auth completely intact -- a `git pull` hitting a
        # root-owned file under .git, or an `airuleset.py install`
        # traceback, are both real shapes on this fleet. The classifier
        # must NOT skip soniox delivery for this host: ssh's own
        # auth-exhaustion message always exits 255 and is always literally
        # prefixed "<user>@<host>: " -- a remote command has no reason to
        # reproduce either property.
        import unittest.mock as m
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "miva1@subdev", "host": "9.9.9.9", "user": "miva1",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        fake_authority = {"miva1": "fork-no-merge"}
        miva1_ssh_calls_seen = {"n": 0}

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if cmd[:2] == ["ruff", "check"]:
                return m.Mock(returncode=0, stdout="", stderr="")
            if "unittest" in cmd:
                return m.Mock(returncode=0, stdout="", stderr="")
            if cmd[:2] == ["git", "push"]:
                return m.Mock(returncode=0, stdout="ok", stderr="")
            if any("miva1@" in str(a) for a in cmd):
                miva1_ssh_calls_seen["n"] += 1
                if miva1_ssh_calls_seen["n"] == 1:
                    # rc=1 (a real remote `git`/python exit code, never
                    # ssh's own 255), no "user@host: " prefix -- a genuine
                    # remote-side permission error, auth fully intact.
                    return m.Mock(
                        returncode=1, stdout="",
                        stderr="error: cannot open '.git/FETCH_HEAD': "
                               "Permission denied")
            return m.Mock(returncode=0, stdout="ok", stderr="")

        with m.patch("subprocess.run", side_effect=fake_run), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", fake_authority), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=fake"):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        miva1_calls = [c for c in calls if any("miva1@" in str(a) for a in c)]
        self.assertEqual(len(miva1_calls), 2,
                          "a remote-side 'Permission denied' MESSAGE with "
                          "ssh auth genuinely intact must NOT suppress the "
                          "soniox delivery attempt: %r" % miva1_calls)

    def test_failure_summary_counts_distinct_hosts_not_failed_entries(self):
        # #341 adversarial-review F3 (MINOR, TRIGGERED): an auth-failed
        # host now yields TWO `failed` entries by design (its own deploy
        # `rc=...` PLUS the soniox `skipped-known-auth-failure`) -- the
        # final summary line must still report the count of DISTINCT hosts
        # that failed, not len(failed) (which would print "2 of 1" for a
        # single bad account).
        import unittest.mock as m
        out = StringIO()
        calls = []
        args = m.Mock()
        fake_hosts = [
            {"name": "david3@subdev", "host": "9.9.9.9", "user": "david3",
             "repo_path": "~/devel/airuleset",
             "identity": "~/.secrets/gatekeeper_access_ed25519"},
        ]
        fake_authority = {"david3": "fork-no-merge"}
        with m.patch("subprocess.run",
                     side_effect=self._fake_run(calls, {"david3": 255})), \
                m.patch.object(airuleset, "cmd_install"), \
                m.patch.object(airuleset, "REMOTE_HOSTS", fake_hosts), \
                m.patch.object(airuleset, "AUTHORITY_BY_USER", fake_authority), \
                m.patch.object(cli_remote, "_soniox_key_line",
                                return_value="SONIOX_API_KEY=fake"), \
                m.patch("sys.stderr", out):
            with self.assertRaises(SystemExit):
                airuleset.cmd_push(args)
        self.assertIn("1 of 1 remote(s) FAILED", out.getvalue(),
                      "must count the ONE distinct host, not the two "
                      "failed() entries it produced: %r" % out.getvalue())


class TestApplyStreamTmuxWindowName(TestCase):
    """#554/#592: the tmux WINDOW name carries the box's short TARGET ALIAS so
    the owner, attached to one of many fleet sessions, can tell at a glance
    where they are. #554 gated this to subdev stream accounts only, so gk/dev1/
    dev2 windows showed `bash` (owner report 2026-08-20); #592 renders it on
    EVERY managed box, with the name = the box's `cli_aliases.short_target_alias`
    (gatekeeper->gk, dev1->dev1, dev2->dev2, montaluN->mN, davidN->dN, ...) --
    ONE shared alias source, never a second parallel map. Idempotent per-box
    marker-block (shape of apply_stream_ssh_attach, #264); the block is stripped
    only when a box has no safe alias (never in practice)."""

    def _tmp(self, content=None):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        if content is not None:
            p.write_text(content)
        return p

    def test_renders_the_window_naming_directives_with_the_baked_stream_name(self):
        block = airuleset.render_stream_tmux_window_block("montalu1")
        self.assertIn(airuleset.STREAM_TMUX_WINDOW_MARK_START, block)
        self.assertIn(airuleset.STREAM_TMUX_WINDOW_MARK_END, block)
        # automatic-rename off makes the name STICK against a command change.
        self.assertIn("set-option -gw automatic-rename off", block)
        # session-created hook renames every (primary AND grouped-attach)
        # new session's window to the baked stream literal -- verified live
        # to converge on both tmux 3.4 and 3.7b.
        self.assertIn('set-hook -g session-created "rename-window montalu1"', block)

    def test_after_new_window_hook_is_never_emitted(self):
        # #554: `after-new-window` fires ONLY on windows opened after the
        # initial one, so it never names the session's FIRST (claude)
        # window -- the one the owner sees on attach. `session-created` is
        # the right hook (live-verified rc=0 on tmux 3.4 + 3.7b), so
        # after-new-window must never appear in the block.
        block = airuleset.render_stream_tmux_window_block("david1")
        self.assertNotIn("after-new-window", block)

    def test_adds_block_for_a_stream_account_with_its_alias(self):
        # #592: a stream box is named by its ALIAS (montalu2 -> m2), matching
        # the owner's own webterm tab + live mitigation, not the full username.
        # `host="subdev"` = the stream account's real box; without it the test
        # would inherit the test box's own hostname (dev1) and the `dev1`
        # short-circuit would fire (montalu2 never actually runs on dev1).
        p = self._tmp("# existing content\n")
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", host="subdev", run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertIn(airuleset.STREAM_TMUX_WINDOW_MARK_START, text)
        self.assertIn('set-hook -g session-created "rename-window m2"', text)

    def test_gk_gets_the_gk_alias(self):
        # #592: gk (gatekeeper account) was the owner's report -- window showed
        # `bash`. Now it carries the `gk` alias block.
        p = self._tmp("# existing content\n")
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="gatekeeper", host="gatekeeper-cx23", run=lambda argv: None)
        self.assertTrue(changed)
        self.assertIn('set-hook -g session-created "rename-window gk"', p.read_text())

    def test_dev1_and_dev2_owner_boxes_get_NO_window_naming_block(self):
        # #593 REGRESSION FIX: dev1/dev2 (unix user `newlevel`) are MULTI-PROJECT
        # owner boxes -- many project sessions, windows named per-command by
        # automatic-rename. #592 wrongly rendered the single-fixed-name block
        # there (automatic-rename off + session-created rename-window dev1),
        # destroying per-project navigation. They must get NO block at all.
        for box in ("dev1", "dev2"):
            p = self._tmp("# existing content\n")
            changed = airuleset.apply_stream_tmux_window_name(
                p, user="newlevel", host=box, run=lambda argv: None)
            self.assertFalse(changed, box)
            text = p.read_text()
            self.assertNotIn(airuleset.STREAM_TMUX_WINDOW_MARK_START, text, box)
            self.assertNotIn("automatic-rename off", text, box)
            self.assertNotIn("session-created", text, box)

    def test_is_single_session_box_user_gates_window_naming(self):
        # #593: the eligibility property is "one unix account = one tmux
        # session" -- the SAME set the #264 ssh-auto-attach uses. gk + subdev
        # streams YES, the owner's newlevel multi-project boxes NO.
        self.assertFalse(airuleset.is_single_session_box_user("newlevel"))
        self.assertTrue(airuleset.is_single_session_box_user("gatekeeper"))
        self.assertTrue(airuleset.is_single_session_box_user("montalu2"))

    def test_window_naming_gates_on_the_same_set_as_ssh_attach(self):
        # #593: the extracted `is_single_session_box_user` predicate IS the
        # ssh-auto-attach eligibility gate -- one source of truth, so the two
        # features can never drift on "which boxes are single-session". Note
        # newlevel@subdev yields a SAFE alias ("subdev"), so the predicate --
        # not alias safety -- is what must exclude it.
        for u in ("newlevel", "gatekeeper", "montalu2", "david", "root"):
            ps = self._tmp("# c\n")
            airuleset.apply_stream_ssh_attach(ps, user=u)
            ssh_has = airuleset.STREAM_SSH_ATTACH_MARK_START in ps.read_text()
            self.assertEqual(
                airuleset.is_single_session_box_user(u), ssh_has,
                "%s: is_single_session_box_user must match ssh-attach" % u)

    def test_strips_block_when_the_derived_alias_is_unsafe(self):
        # An alias that does not match the safe unix-name shape (injection guard)
        # is never rendered -- and an existing block is stripped. `9bad` fails
        # `_SAFE_STREAM_NAME_RE` (must start with a letter).
        block = airuleset.render_stream_tmux_window_block("m2")
        p = self._tmp(f"# before\n{block}\n# after\n")
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="9bad", host="9bad", run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertNotIn(airuleset.STREAM_TMUX_WINDOW_MARK_START, text)
        self.assertIn("# before", text)
        self.assertIn("# after", text)

    def test_idempotent_second_call_is_a_no_op(self):
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(p, user="montalu2", run=lambda argv: None)
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", run=lambda argv: None)
        self.assertFalse(changed)

    def test_never_touches_content_outside_the_markers(self):
        p = self._tmp("set -g mouse on\nset -g status-bg colour234\n")
        airuleset.apply_stream_tmux_window_name(p, user="david", run=lambda argv: None)
        text = p.read_text()
        self.assertIn("set -g mouse on", text)
        self.assertIn("set -g status-bg colour234", text)

    def test_creates_file_when_absent(self):
        d = tempfile.mkdtemp()
        p = Path(d) / ".tmux.conf"
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="marek", run=lambda argv: None)
        self.assertTrue(changed)
        self.assertTrue(p.exists())

    def test_a_stray_unpaired_start_does_not_eat_content_up_to_a_later_end(self):
        block = airuleset.render_stream_tmux_window_block("montalu2")
        corrupted = (
            f"{airuleset.STREAM_TMUX_WINDOW_MARK_START}\n"
            "# stray leftover start with no matching end anywhere before this\n"
            "IMPORTANT_UNRELATED_LINE=1\n"
            f"{block}\n"
        )
        p = self._tmp(corrupted)
        airuleset.apply_stream_tmux_window_name(p, user="montalu2", run=lambda argv: None)
        airuleset.apply_stream_tmux_window_name(p, user="montalu2", run=lambda argv: None)
        self.assertIn("IMPORTANT_UNRELATED_LINE=1", p.read_text())

    def test_live_applies_the_server_options_for_a_stream_account(self):
        calls = []
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", host="subdev", run=calls.append)
        # server-option sets (no keystrokes) -- automatic-rename off + the
        # session-created rename hook, exactly what the conf block carries.
        # #592: the hook renames to the ALIAS (m2), not the full username.
        self.assertIn(["tmux", "set-option", "-gw", "automatic-rename", "off"], calls)
        self.assertIn(
            ["tmux", "set-hook", "-g", "session-created", "rename-window m2"],
            calls)

    def test_live_apply_renames_every_window_on_the_server_to_the_alias(self):
        # #592-review (B3): the live-apply lists ALL windows on this user's
        # server (`list-windows -a`), NOT the `=<unix-user>` session -- on
        # dev1/dev2 the owner's real session is zbynek-N/marek-N while the unix
        # user is `newlevel`, so a `=<unix-user>` target would rename NOTHING.
        # A run that returns two window ids for `list-windows -a`, then records
        # the rename calls; each is renamed to the alias (config-path, never
        # send-keys).
        seen = []

        def run(argv):
            seen.append(argv)
            if argv[:3] == ["tmux", "list-windows", "-a"]:
                return _FakeCP(returncode=0, stdout="@0\n@3\n")
            return _FakeCP(returncode=0, stdout="")

        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", host="subdev", run=run)
        self.assertIn(["tmux", "rename-window", "-t", "@0", "m2"], seen)
        self.assertIn(["tmux", "rename-window", "-t", "@3", "m2"], seen)

    def test_newlevel_owner_box_does_NO_window_naming_live_apply(self):
        # #593 REGRESSION FIX: a newlevel multi-project owner box must NOT
        # live-apply the window naming -- no automatic-rename off, no
        # session-created rename hook, no rename-window of any window. The old
        # #592 code renamed EVERY window on the server to `dev1`, destroying
        # per-project navigation.
        seen = []

        def run(argv):
            seen.append(argv)
            if argv[:3] == ["tmux", "list-windows", "-a"]:
                return _FakeCP(returncode=0, stdout="@7\n")
            return _FakeCP(returncode=0, stdout="")

        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(
            p, user="newlevel", host="dev1", run=run)
        self.assertNotIn(
            ["tmux", "set-option", "-gw", "automatic-rename", "off"], seen)
        self.assertNotIn(
            ["tmux", "set-hook", "-g", "session-created", "rename-window dev1"],
            seen)
        self.assertNotIn(["tmux", "rename-window", "-t", "@7", "dev1"], seen)

    def test_newlevel_owner_box_live_reverts_the_bad_592_server_options(self):
        # #593: symmetric self-heal -- a newlevel box whose running server still
        # carries the bad #592 options gets them UNSET on the next install
        # (automatic-rename back to default `on`, session-created hook removed),
        # not just stripped from the conf. Config-path only, never send-keys.
        seen = []
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(
            p, user="newlevel", host="dev1", run=seen.append)
        self.assertIn(["tmux", "set-option", "-gwu", "automatic-rename"], seen)
        self.assertIn(["tmux", "set-hook", "-gu", "session-created"], seen)

    def test_owner_box_with_existing_bad_block_gets_it_stripped(self):
        # #593: a newlevel box whose ~/.tmux.conf already carries the (wrongly
        # provisioned) #592 block gets it STRIPPED on the next apply -- so a
        # server restart never re-arms the bad options.
        block = airuleset.render_stream_tmux_window_block("dev1")
        p = self._tmp(f"# before\n{block}\n# after\n")
        changed = airuleset.apply_stream_tmux_window_name(
            p, user="newlevel", host="dev1", run=lambda argv: None)
        self.assertTrue(changed)
        text = p.read_text()
        self.assertNotIn(airuleset.STREAM_TMUX_WINDOW_MARK_START, text)
        self.assertIn("# before", text)
        self.assertIn("# after", text)

    def test_no_live_apply_calls_when_alias_is_unsafe(self):
        # #592: a box with no SAFE alias (injection guard) gets no block AND no
        # tmux mutation -- the strip path. `9bad` fails `_SAFE_STREAM_NAME_RE`.
        calls = []
        p = self._tmp("# existing content\n")
        airuleset.apply_stream_tmux_window_name(
            p, user="9bad", host="9bad", run=calls.append)
        self.assertEqual(calls, [], "an unsafe-alias box must get NO tmux mutation")

    def test_marker_sets_are_mutually_non_substring(self):
        # #554 review F2: apply_tmux_history_limit and this feature share the
        # SAME positional-span scanner (_clean_tmux_block_spans), so their
        # correctness across repeated installs rests on neither marker being
        # a substring of the other -- else one scanner would match the other's
        # block. Lock it so a future marker rename can't silently corrupt.
        hs, he = airuleset.TMUX_MARK_START, airuleset.TMUX_MARK_END
        ss, se = (airuleset.STREAM_TMUX_WINDOW_MARK_START,
                  airuleset.STREAM_TMUX_WINDOW_MARK_END)
        self.assertNotIn(hs, ss)
        self.assertNotIn(ss, hs)
        self.assertNotIn(he, se)
        self.assertNotIn(se, he)

    def test_coexists_with_the_history_block_across_repeated_installs(self):
        # #554 review F2: both managed blocks live in ONE ~/.tmux.conf. Apply
        # both, then re-apply both, and assert each survives intact with the
        # user's own content preserved -- neither scanner eats the other.
        p = self._tmp("set -g mouse on\n")
        airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", host="subdev", run=lambda argv: None)
        # a second install of BOTH must be a byte-for-byte no-op
        before = p.read_text()
        c1 = airuleset.apply_tmux_history_limit(p, run=lambda argv: None)
        c2 = airuleset.apply_stream_tmux_window_name(
            p, user="montalu2", host="subdev", run=lambda argv: None)
        after = p.read_text()
        self.assertFalse(c1)
        self.assertFalse(c2)
        self.assertEqual(before, after)
        # both managed blocks + the user's own line all present, exactly once
        self.assertEqual(after.count(airuleset.TMUX_MARK_START), 1)
        self.assertEqual(after.count(airuleset.STREAM_TMUX_WINDOW_MARK_START), 1)
        self.assertIn("set -g mouse on", after)
        self.assertIn("set-option -g history-limit", after)
        self.assertIn('rename-window m2', after)  # #592: alias, not full username


if __name__ == "__main__":
    main()
