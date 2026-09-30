"""#1202 — every session creator starts a stream's tmux session in its DECLARED
window cwd; `claude -c` never resumes from the wrong project dir.

Regression of #1088/#961. After the 2026-09-30 subdev reboot montalu1's tmux
session was created as `tmux new-session -A -s montalu1 -c
/home/montalu1/devel/odoo` (ppid 1). The creator was the controller-lane FORCED
COMMAND in montalu1's authorized_keys, baked by hand on 2026-09-09 from the
chain of that day `("devel/odoo/odoo-erp", "devel/odoo")` with a `[ -d ]`
predicate; the webterm tabs reconnected after the reboot and it resolved to the
bare parent. `claude -c` from there resumed a stale copy of the owner's session
(the same session id lives under both project keys).

Approach 1 (the main's design):
1. the declared window (`cli_fleet.box_windows(user)[0].cwd`) is the ONE cwd
   source for every creator (ssh attach block, webterm command incl. its baked
   forced-command form, the #263 Python bootstrap); without a declared window a
   chain entry counts only when it is a git checkout, never a bare parent;
2. the claude launcher refuses `claude` / `claude -c` / `--continue` outside a
   declared window's checkout and prints `cd <cwd> && claude -c`;
3. `airuleset.py status` flags a first-pane cwd that differs from the declared
   window cwd (status only, never the footer).

All tests use temp HOME fixtures and fake tmux/claude binaries — never a real
tmux server, never ~/.claude.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset             # noqa: E402
import cli_bashrc_appliers   # noqa: E402
import cli_claude_scripts    # noqa: E402
import cli_session_cwd       # noqa: E402
import cli_webterm           # noqa: E402
import cli_webterm_only      # noqa: E402

M1 = "devel/odoo/odoo-slovnormal"
ERP = "devel/odoo/odoo-erp"
KEY = "ssh-ed25519 AAAA testkey"


def _tmpdir(tc, prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    tc.addCleanup(lambda: shutil.rmtree(d, True))
    return d


def _montalu1_home(tc, erp_checkout=True):
    """montalu1's real layout + an odoo-erp checkout so a CHAIN would win it:
    only the declared window can pick odoo-slovnormal."""
    home = _tmpdir(tc, "airuleset-1202-home-")
    (home / M1 / ".git").mkdir(parents=True)
    (home / "devel" / "odoo" / "screenshots").mkdir(parents=True)
    if erp_checkout:
        (home / ERP / ".git").mkdir(parents=True)
    return home


# --------------------------------------------------------------------------- #
# 1. the declared window is read from the fleet table.
# --------------------------------------------------------------------------- #
class TestDeclaredWindowSource(unittest.TestCase):
    def test_montalu1_primary_declared_cwd(self):
        self.assertEqual(cli_session_cwd.primary_declared_rel("montalu1"), M1)

    def test_gk_declares_all_its_window_cwds(self):
        rels = cli_session_cwd.declared_window_rels("gatekeeper")
        self.assertEqual(rels[0], ERP)
        self.assertIn("devel/odoo/odoo-erp-infra", rels)

    def test_undeclared_account_has_none(self):
        for user in ("newlevel", "airuleset", "montalu2", "", None):
            self.assertIsNone(cli_session_cwd.primary_declared_rel(user), user)
        self.assertEqual(cli_session_cwd.declared_window_rels("newlevel"), ())

    def test_unsafe_cwd_is_never_baked(self):
        fake = [{"name": "x", "cwd": "~/devel/$(id)"}]
        with m.patch.object(cli_session_cwd.cli_fleet, "box_windows",
                            return_value=fake):
            self.assertIsNone(cli_session_cwd.primary_declared_rel("x"))
            self.assertEqual(cli_session_cwd.launch_guard_cwds("x"), "")


# --------------------------------------------------------------------------- #
# 2. every creator renders the declared cwd.
# --------------------------------------------------------------------------- #
_FAKE_TMUX = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_TMUX_LOG"
case "$1" in
  has-session) exit 1 ;;
  *) exit 0 ;;
esac
"""


def _fake_bin(d, whoami):
    b = d / "bin"
    b.mkdir()
    (b / "tmux").write_text(_FAKE_TMUX)
    (b / "whoami").write_text("#!/usr/bin/env bash\necho '%s'\n" % whoami)
    for f in b.iterdir():
        f.chmod(0o755)
    return b


class TestAttachBlockUsesDeclaredCwd(unittest.TestCase):
    def _run_block(self, block, home, whoami="montalu1"):
        d = _tmpdir(self, "airuleset-1202-bash-")
        b = _fake_bin(d, whoami)
        log = d / "tmux.log"
        env = {"PATH": "%s:/usr/bin:/bin" % b, "HOME": str(home),
               "SSH_TTY": "/dev/pts/0", "FAKE_TMUX_LOG": str(log)}
        r = subprocess.run(["bash", "--norc", "-i", "-c", block], env=env,
                           capture_output=True, text=True, timeout=15,
                           stdin=subprocess.DEVNULL)
        return (log.read_text() if log.exists() else ""), r

    def test_montalu1_block_lands_in_declared_cwd_not_chain_winner(self):
        home = _montalu1_home(self)
        block = cli_bashrc_appliers._ssh_attach_block_for_user("montalu1")
        log, _ = self._run_block(block, home)
        self.assertIn("new-session -A -s montalu1 -c %s/%s\n" % (home, M1), log)

    def test_declared_dir_without_git_is_still_used_never_a_parent(self):
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / M1).mkdir(parents=True)          # declared, not cloned yet
        block = cli_bashrc_appliers._ssh_attach_block_for_user("montalu1")
        log, r = self._run_block(block, home)
        self.assertIn("-c %s/%s\n" % (home, M1), log)
        self.assertIn("no git checkout found", r.stderr)

    def test_default_block_never_resolves_a_bare_parent(self):
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / M1).mkdir(parents=True)          # plain, no .git anywhere
        log, _ = self._run_block(cli_bashrc_appliers.STREAM_SSH_ATTACH_BLOCK,
                                 home, whoami="montalu2")
        self.assertIn("new-session -A -s montalu2 -c %s\n" % home, log)
        self.assertNotIn("-c %s/devel/odoo\n" % home, log)

    def test_apply_writes_the_declared_block_for_montalu1(self):
        d = _tmpdir(self, "airuleset-1202-rc-")
        rc = d / ".bashrc"
        rc.write_text("# existing\n")
        with m.patch.object(airuleset, "AUTHORITY_BY_USER",
                            dict(airuleset.AUTHORITY_BY_USER, montalu1="full")):
            cli_bashrc_appliers.apply_stream_ssh_attach(rc, user="montalu1")
        text = rc.read_text()
        self.assertIn("for __airuleset_rel in %s; do" % M1, text)
        self.assertNotIn("for __airuleset_rel in %s" % ERP, text)


class TestPythonBootstrapUsesDeclaredCwd(unittest.TestCase):
    def test_resolver_prefers_declared_over_chain(self):
        home = _montalu1_home(self)
        chosen, no_repo = cli_bashrc_appliers.resolve_stream_cwd(
            home, user="montalu1")
        self.assertEqual(chosen, home / M1)
        self.assertFalse(no_repo)

    def test_stream_session_cwd_takes_the_user(self):
        home = _montalu1_home(self)
        with m.patch.object(Path, "home", return_value=home):
            self.assertEqual(airuleset._stream_session_cwd("montalu1"),
                             home / M1)

    def test_bootstrap_creates_the_session_in_declared_cwd(self):
        home = _montalu1_home(self)
        calls = []

        def run(argv):
            calls.append(list(argv))
            rc = 1 if argv[1] == "has-session" else 0
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="")

        sentinel = _tmpdir(self, "airuleset-1202-sent-") / "sentinel"
        with m.patch.object(Path, "home", return_value=home):
            airuleset.ensure_stream_tmux_session(
                user="montalu1", run=run, launch_script=Path("/x/launch"),
                sentinel_path=sentinel)
        created = [c for c in calls if c[1] == "new-session"]
        self.assertEqual(created[0][-2:], ["-c", str(home / M1)])


class TestWebtermUsesDeclaredCwd(unittest.TestCase):
    def _eval(self, cmd, home):
        snippet = cmd[:cmd.index('T=""')] + 'echo "RESULT=$C"'
        r = subprocess.run(["sh", "-c", snippet], capture_output=True,
                           text=True, timeout=10,
                           env=dict(os.environ, HOME=str(home)))
        return r.stdout.strip().split("RESULT=", 1)[1]

    def test_remote_command_resolves_declared_cwd(self):
        home = _montalu1_home(self)
        cmd = cli_webterm._remote_command("montalu1", user="montalu1")
        self.assertEqual(self._eval(cmd, home), str(home / M1))

    def test_baked_forced_command_carries_the_declared_cwd(self):
        # the #1202 creator: the authorized_keys forced command
        line = cli_webterm_only._controller_lane_key_line("montalu1", KEY)
        self.assertIn(M1, line)
        self.assertNotIn(ERP, line)

    def test_build_connect_argv_passes_the_target_user(self):
        entry = {"id": "m1", "local": True, "host": None, "user": "montalu1",
                 "preferred": "montalu1"}
        argv = cli_webterm.build_connect_argv(entry)
        self.assertIn("for __r in %s;" % M1, argv[-1])

    def test_default_chain_never_resolves_a_bare_parent(self):
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / M1).mkdir(parents=True)          # plain, no .git anywhere
        cmd = cli_webterm._remote_command("montalu2")
        self.assertEqual(self._eval(cmd, home), str(home))

    def test_default_chain_picks_the_checkout(self):
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / M1 / ".git").mkdir(parents=True)
        cmd = cli_webterm._remote_command("montalu2")
        self.assertEqual(self._eval(cmd, home), str(home / M1))

    def test_forced_command_stays_single_line(self):
        line = cli_webterm_only._controller_lane_key_line("montalu1", KEY)
        self.assertNotIn("\n", line)


# --------------------------------------------------------------------------- #
# 3. the claude launcher's resume guard.
# --------------------------------------------------------------------------- #
_FAKE_CLAUDE = "#!/usr/bin/env bash\necho \"CLAUDE_RAN $*\"\n"


class TestLauncherResumeGuard(unittest.TestCase):
    def _launch(self, declared, cwd_rel, *args, env_extra=None, home=None):
        home = home or _montalu1_home(self, erp_checkout=False)
        (home / "devel" / "odoo").mkdir(parents=True, exist_ok=True)
        lb = home / ".local" / "bin"
        lb.mkdir(parents=True, exist_ok=True)
        (lb / "claude").write_text(_FAKE_CLAUDE)
        (lb / "claude").chmod(0o755)
        script = home / "launch.sh"
        script.write_text(cli_claude_scripts.render_claude_launch_script(
            cli_session_cwd.render_launch_cwd_guard(declared)))
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
        env.update(env_extra or {})
        r = subprocess.run(["bash", str(script)] + list(args),
                           cwd=str(home / cwd_rel) if cwd_rel else str(home),
                           env=env, capture_output=True, text=True, timeout=15)
        return r

    def _declared(self, user="montalu1"):
        return cli_session_cwd.launch_guard_cwds(user)

    def test_refuses_continue_from_the_parent_and_prints_the_fix(self):
        for args in (("default",), ("default", "-c"), ("default", "--continue"),
                     ("ultracode",), ("fullscreen",)):
            r = self._launch(self._declared(), "devel/odoo", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertNotIn("CLAUDE_RAN", r.stdout, args)
            self.assertIn("cd ~/%s && claude -c" % M1, r.stderr, args)

    def test_refuses_from_home(self):
        r = self._launch(self._declared(), "", "default")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("CLAUDE_RAN", r.stdout)

    def test_runs_in_the_declared_checkout(self):
        r = self._launch(self._declared(), M1, "default", "-c")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE_RAN", r.stdout)

    def test_refuses_a_declared_dir_that_is_not_a_checkout(self):
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / M1).mkdir(parents=True)
        r = self._launch(self._declared(), M1, "default", home=home)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git checkout", r.stderr)

    def test_any_declared_window_cwd_is_accepted(self):
        # gk declares gk / gk-infra / gk-quality — each window runs claude
        home = _tmpdir(self, "airuleset-1202-home-")
        (home / "devel/odoo/odoo-erp-infra/.git").mkdir(parents=True)
        r = self._launch(self._declared("gatekeeper"),
                         "devel/odoo/odoo-erp-infra", "default", home=home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE_RAN", r.stdout)

    def test_fresh_and_plain_modes_are_never_guarded(self):
        for mode in ("new", "plain"):
            r = self._launch(self._declared(), "devel/odoo", mode)
            self.assertEqual(r.returncode, 0, (mode, r.stderr))
            self.assertIn("CLAUDE_RAN", r.stdout, mode)

    def test_explicit_bypass(self):
        r = self._launch(self._declared(), "devel/odoo", "default",
                         env_extra={"AIRULESET_CWD_GUARD": "off"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE_RAN", r.stdout)

    def test_no_declared_window_means_no_guard(self):
        r = self._launch(self._declared("newlevel"), "devel/odoo", "default")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLAUDE_RAN", r.stdout)
        # an undeclared account's launcher is byte-identical to pre-#1202
        self.assertEqual(
            cli_claude_scripts.render_claude_launch_script(
                cli_session_cwd.render_launch_cwd_guard("")),
            cli_claude_scripts.render_claude_launch_script())
        self.assertNotIn("{{", cli_claude_scripts.render_claude_launch_script())

    def test_install_renders_the_accounts_declared_cwds(self):
        d = _tmpdir(self, "airuleset-1202-inst-")
        paths = {k: d / k for k in ("script", "hist", "popup", "impl", "prompt")}
        (d / ".bashrc").write_text("")
        kw = dict(bashrc_path=d / ".bashrc", script_path=paths["script"],
                  history_script_path=paths["hist"],
                  popup_script_path=paths["popup"],
                  impl_script_path=paths["impl"],
                  impl_prompt_path=paths["prompt"])
        cli_bashrc_appliers.apply_ultracode_launcher(user="montalu1", **kw)
        self.assertIn("_declared_cwds=(%s)" % M1, paths["script"].read_text())
        cli_bashrc_appliers.apply_ultracode_launcher(user="newlevel", **kw)
        self.assertNotIn("_cwd_guard", paths["script"].read_text())


# --------------------------------------------------------------------------- #
# 4. `airuleset.py status` flags a first-pane cwd mismatch.
# --------------------------------------------------------------------------- #
class TestStatusPaneCwdCheck(unittest.TestCase):
    def test_parent_dir_is_a_mismatch(self):
        home = _montalu1_home(self)
        ok, line = cli_session_cwd.pane_cwd_status(
            "montalu1", home, str(home / "devel" / "odoo"))
        self.assertIs(ok, False)
        self.assertIn("MISMATCH", line)
        self.assertIn(M1, line)

    def test_declared_dir_and_subdir_are_ok(self):
        home = _montalu1_home(self)
        for pane in (home / M1, home / M1 / ".git"):
            ok, _ = cli_session_cwd.pane_cwd_status("montalu1", home, str(pane))
            self.assertIs(ok, True, pane)

    def test_sibling_checkout_is_a_mismatch(self):
        home = _montalu1_home(self)
        ok, _ = cli_session_cwd.pane_cwd_status("montalu1", home, str(home / ERP))
        self.assertIs(ok, False)

    def test_unknown_pane_is_inconclusive_and_undeclared_is_skipped(self):
        home = _montalu1_home(self)
        ok, line = cli_session_cwd.pane_cwd_status("montalu1", home, None)
        self.assertIsNone(ok)
        self.assertIn("inconclusive", line)
        self.assertIsNone(cli_session_cwd.pane_cwd_status("newlevel", home, "/x"))

    def test_cmd_status_prints_the_mismatch(self):
        home = _montalu1_home(self)
        out = io.StringIO()
        with m.patch.object(airuleset, "_current_user", return_value="montalu1"), \
                m.patch.object(airuleset, "_tmux_session_pane_cwd",
                               return_value=str(home / "devel" / "odoo")), \
                m.patch.object(Path, "home", return_value=home), \
                m.patch("sys.stdout", out):
            airuleset.cmd_status(types.SimpleNamespace(skill_parity=False))
        self.assertIn("session cwd: MISMATCH", out.getvalue())


if __name__ == "__main__":
    unittest.main()
