"""#1037: a DECLARED managed window (gk-infra, gk-quality on gk; the d3 impl
window) must run the managed launcher INSIDE a login shell that SURVIVES the
claude ``/exit`` -- so the owner drops to a bash prompt in the window's cwd and
types ``claude`` (the bashrc wrapper) to relaunch, exactly like the primary
window on every box. Owner escalation 21.9.2026: "NIKDE to takto nemam, vsade sa
mozem exitnut do bashu a vratit naspat cez spustenie claude cmd. Tak to mam aj v
gk."

Defect (main a0b2c040): ``cli_tmux_provisioning._managed_windows_create_body``
and ``_impl_window_create_snippet`` render the managed launcher AS the pane's
window command (``new-window ... "$HOME/.claude/airuleset-claude-launch.sh"
default``); the launcher itself ends in ``exec claude ...``, so the pane's only
process is claude and tmux CLOSES the whole window when claude exits.

Fix (Approach 1): a shared ``_window_shell_command(launcher)`` helper wraps the
launcher command in ``bash -lc "<launcher>; exec bash -l"`` -- the launcher runs
as a CHILD (its own ``exec claude`` replaces only the child), so on claude exit
control returns to the parent bash, which ``exec``s an interactive LOGIN shell in
the window's cwd. Only DOUBLE quotes are used (the create body lives inside the
session-created hook's single-quoted ``run-shell`` body), so the hook wrapping
stays valid. Both renderers share the helper; the live-apply path reuses the
create body and picks it up unchanged.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_tmux_provisioning as ctp  # noqa: E402


# A three-window gk declaration: the primary review window + two NON-primary
# declared windows (gk-infra, gk-quality), the exact shape the owner runs on gk.
_GK3 = [
    {"name": "gk", "cwd": "~/devel/odoo/odoo-erp",
     "role": "review", "mode": "parallel"},
    {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra",
     "role": "infra", "mode": "sequential"},
    {"name": "gk-quality", "cwd": "~/devel/odoo/odoo-erp-quality",
     "role": "quality", "mode": "sequential"},
]

# The bare-launcher window command the OLD (defective) renderer produced -- the
# closing double-quote sits right after `.sh`, immediately followed by ` default`.
# The fix must NEVER emit this shape (the launcher can no longer be the pane's
# only process).
_BARE = '"$HOME/.claude/airuleset-claude-launch.sh" default'


class _CP:
    """Minimal CompletedProcess stand-in for a fake tmux runner."""

    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


class TestWindowShellCommandHelper(unittest.TestCase):
    def test_wraps_a_launcher_in_a_surviving_login_shell(self):
        cmd = ctp._window_shell_command(
            "$HOME/.claude/airuleset-claude-launch.sh default")
        # exact shape verified LIVE on gk: launcher runs as a child, then the
        # parent bash execs an interactive LOGIN shell that survives /exit.
        self.assertEqual(
            cmd,
            'bash -lc "$HOME/.claude/airuleset-claude-launch.sh default; '
            'exec bash -l"')

    def test_uses_only_double_quotes(self):
        # the create body is wrapped in the hook's SINGLE-quoted run-shell body,
        # so the window command may contain NO single quote of its own.
        cmd = ctp._window_shell_command("$HOME/.claude/x.sh")
        self.assertNotIn("'", cmd)
        self.assertIn("bash -lc", cmd)
        self.assertIn("exec bash -l", cmd)


class TestManagedWindowsCreateBodyWrapsLauncher(unittest.TestCase):
    def setUp(self):
        self.body = ctp._managed_windows_create_body(_GK3)

    def test_both_non_primary_windows_are_wrapped(self):
        # one wrapper per NON-primary declared window (gk-infra, gk-quality).
        self.assertEqual(self.body.count("bash -lc"), 2, self.body)
        self.assertEqual(self.body.count("exec bash -l"), 2, self.body)
        # the full wrapped launcher command is present.
        self.assertIn(
            'bash -lc "$HOME/.claude/airuleset-claude-launch.sh default; '
            'exec bash -l"',
            self.body)

    def test_never_a_bare_launcher_as_the_window_command(self):
        # the defective bare-launcher shape must be gone.
        self.assertNotIn(_BARE, self.body)

    def test_the_launcher_and_default_arg_are_still_present(self):
        # intent of the old lock kept: the managed launcher + `default` still run.
        self.assertIn("airuleset-claude-launch.sh", self.body)
        self.assertRegex(self.body,
                         r"airuleset-claude-launch\.sh default; exec bash -l")

    def test_dedup_and_doubled_formats_unchanged(self):
        # the create-if-missing dedup by name + cwd and the doubled inner formats
        # stay byte-identical (only the window command changed).
        self.assertEqual(self.body.count("new-window"), 2)
        self.assertIn("##{window_name}", self.body)
        self.assertIn("##{pane_current_path}", self.body)
        self.assertIn("grep -Fxq gk-infra", self.body)
        self.assertIn("grep -Fxq gk-quality", self.body)
        self.assertIn('-c "$HOME/devel/odoo/odoo-erp-infra"', self.body)

    def test_body_is_valid_shell(self):
        # bash -n on the rendered create body (the fragile part: nested quoting).
        d = tempfile.mkdtemp()
        script = Path(d) / "snip.sh"
        script.write_text("S=x\n" + self.body + "\n")
        r = subprocess.run(["bash", "-n", str(script)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestImplWindowSnippetWrapsLauncher(unittest.TestCase):
    # SCOPE: this covers ONLY the session-created-hook impl creator
    # (`_impl_window_create_snippet`). The impl window has TWO OTHER creators
    # NOT wrapped by #1037 (out of this lane's cli_tmux_provisioning scope) —
    # the bashrc attach block (cli_bashrc_appliers.py ~295,298) and the watchdog
    # relaunch (watchdog/tmux_io.py ~471) — so impl is NOT yet fixed fleet-wide;
    # see the #1037 followup. gk-infra / gk-quality (the owner's actual
    # complaint) ARE fully fixed via _managed_windows_create_body.
    _MARKER = {"base_url": "http://gw", "key_file": "~/.secrets/k",
               "main": "m", "sub": "s", "fast": "f",
               "cwd": "/home/miva1/devel/odoo/odoo-erp"}

    def setUp(self):
        self.snip = ctp._impl_window_create_snippet(self._MARKER)

    def test_impl_launcher_is_wrapped(self):
        self.assertIn(
            'bash -lc "$HOME/.claude/airuleset-claude-impl.sh; exec bash -l"',
            self.snip)
        self.assertIn("bash -lc", self.snip)
        self.assertIn("exec bash -l", self.snip)

    def test_impl_never_a_bare_launcher(self):
        # the defective bare-launcher shape (quote right after .sh) must be gone.
        self.assertNotIn('"$HOME/.claude/airuleset-claude-impl.sh"', self.snip)

    def test_impl_keeps_its_dedup_remain_and_no_single_quote(self):
        # #1060 L3b intent kept byte-identical: name dedup, remain-on-exit, the
        # marker gate, and NO single quote (outer run-shell single-quote wrap).
        self.assertIn('new-window -d -t "$S" -n impl', self.snip)
        self.assertIn("grep -Fxq impl", self.snip)
        self.assertIn("remain-on-exit on", self.snip)
        self.assertIn("airuleset-model-backend.json", self.snip)
        self.assertNotIn("'", self.snip)


class TestSessionCreatedHookStillValid(unittest.TestCase):
    def test_hook_line_and_body_valid_no_single_quote(self):
        # the conf hook LINE renders and parses (bash -n), and the run-shell body
        # it wraps carries the new wrapper with NO single quote of its own.
        line = ctp._render_session_created_hook_line("gk", _GK3)
        d = tempfile.mkdtemp()
        script = Path(d) / "hook.sh"
        script.write_text(line)
        r = subprocess.run(["bash", "-n", str(script)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

        value = ctp._session_created_hook_value("gk", _GK3, marker=None)
        # extract the single-quoted run-shell body and assert it is single-quote
        # clean and carries the wrapper.
        body = value.split("run-shell '", 1)[1].rsplit("'", 1)[0]
        self.assertNotIn("'", body)
        self.assertIn("bash -lc", body)
        self.assertIn("exec bash -l", body)


class TestLiveApplyReusesTheWrapper(unittest.TestCase):
    def test_run_shell_create_body_carries_the_wrapper(self):
        # #1037: the live-apply path reuses `_managed_windows_create_body`, so an
        # already-running gk server picks the wrapper up on the next push. Assert
        # once here that the run-shell it fires carries `bash -lc`/`exec bash -l`.
        seen = []

        def run(argv):
            seen.append(argv)
            if argv[:3] == ["tmux", "list-windows", "-a"]:
                return _CP(returncode=0,
                           stdout="@0\t/home/gatekeeper/devel/odoo/odoo-erp\n")
            if argv[:3] == ["tmux", "list-sessions", "-F"]:
                return _CP(returncode=0, stdout="zbynek\n")
            return _CP(returncode=0, stdout="")

        ctp._live_apply_stream_window_name(
            "gk", windows=_GK3, home="/home/gatekeeper", run=run)
        run_shells = [a[2] for a in seen if a[:2] == ["tmux", "run-shell"]]
        self.assertTrue(run_shells, seen)
        joined = "\n".join(run_shells)
        self.assertIn("bash -lc", joined)
        self.assertIn("exec bash -l", joined)
        self.assertNotIn(_BARE, joined)


class TestWrapperFallsThroughToLoginShellAtRuntime(unittest.TestCase):
    """The load-bearing runtime proof: running the rendered window command's
    inner script -- with a stub launcher that returns and a PATH-stub `bash`
    recorder -- reaches the fallback ``exec bash -l`` AFTER the launcher exits,
    with the login flag. Uses a NON-login harness `/bin/bash -c` so /etc/profile
    can never reset the PATH stub, and an absolute outer bash so only the INNER
    `exec bash -l` resolves to the recorder."""

    def test_fallthrough_login_shell_is_execd_after_launcher_returns(self):
        d = tempfile.mkdtemp()
        home = Path(d) / "home"
        (home / ".claude").mkdir(parents=True)
        launcher = home / ".claude" / "airuleset-claude-launch.sh"
        launcher.write_text("#!/bin/sh\necho LAUNCHER_RAN\nexit 0\n")
        launcher.chmod(0o755)

        bindir = Path(d) / "bin"
        bindir.mkdir()
        rec = bindir / "bash"
        rec.write_text('#!/bin/sh\n'
                       'printf "RECORDER_ARGS:%s\\n" "$*"\n'
                       'echo RECORDER_RAN\nexit 0\n')
        rec.chmod(0o755)

        window_cmd = ctp._window_shell_command(
            "$HOME/.claude/airuleset-claude-launch.sh default")
        # the argument to `bash -lc` -- the actual command the pane's shell runs.
        inner = window_cmd.split('bash -lc "', 1)[1].rsplit('"', 1)[0]

        env = dict(os.environ)
        env["HOME"] = str(home)
        env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
        r = subprocess.run(["/bin/bash", "-c", inner],
                           env=env, capture_output=True, text=True)
        out = r.stdout
        self.assertIn("LAUNCHER_RAN", out, (out, r.stderr))
        self.assertIn("RECORDER_RAN", out, (out, r.stderr))
        # the fallback shell was exec'd with the LOGIN flag.
        self.assertIn("RECORDER_ARGS:-l", out.replace(" ", ""), out)
        # ordering: the launcher ran BEFORE the fallthrough login shell.
        self.assertLess(out.index("LAUNCHER_RAN"), out.index("RECORDER_RAN"), out)


# --------------------------------------------------------------------------- #
# #1037 follow-up: the impl window has THREE creators — the session-created hook
# snippet (above), the bashrc attach block, and the watchdog relaunch. All THREE
# must wrap the launcher in the surviving login shell, or the owner's "exit to
# bash everywhere" mandate is inconsistent per box (whichever creator won the
# race decides the pane's fate). The bashrc block and the watchdog live in lower
# / cross-layer modules that must not import cli_tmux_provisioning, so they MIRROR
# the one-line wrapper; the drift-lock below ties all three to the canonical
# `_window_shell_command` shape.
# --------------------------------------------------------------------------- #

import cli_bashrc_appliers  # noqa: E402
import watchdog.tmux_io as _tmux_io  # noqa: E402

_IMPL = "airuleset-claude-impl.sh"


class _FakeTmuxRun:
    """Minimal watchdog run(argv): returns canned list-windows output, records
    every argv."""

    def __init__(self, windows_output=""):
        self.windows_output = windows_output
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["tmux", "list-windows"]:
            return self.windows_output
        return ""

    def new_window_calls(self):
        return [c for c in self.calls if c[:2] == ["tmux", "new-window"]]


class TestBashrcAttachBlockWrapsImplLauncher(unittest.TestCase):
    def setUp(self):
        self.block = cli_bashrc_appliers.render_tmux_attach_block("miva1")

    def test_impl_launcher_is_wrapped_in_both_branches(self):
        # exactly the two impl new-window branches (with cwd + without), each
        # wrapping the launcher in the surviving login shell.
        wrapped = 'bash -lc "$HOME/.claude/%s; exec bash -l"' % _IMPL
        self.assertEqual(self.block.count(wrapped), 2, self.block)
        self.assertEqual(self.block.count("bash -lc"), 2)
        self.assertEqual(self.block.count("exec bash -l"), 2)

    def test_never_a_bare_impl_launcher(self):
        # the OLD bare shape (launcher double-quoted as the pane command) is gone.
        self.assertNotIn('-n impl -c "$_impl_cwd" "$HOME/.claude/%s"' % _IMPL,
                         self.block)
        self.assertNotIn('-n impl "$HOME/.claude/%s"' % _IMPL, self.block)

    def test_intent_preserved(self):
        # the cwd branch prefix, the dedup, and remain-on-exit are unchanged.
        self.assertIn('-n impl -c "$_impl_cwd"', self.block)
        self.assertIn("grep -Fxq impl", self.block)
        self.assertIn("remain-on-exit on", self.block)


class TestWatchdogRelaunchWrapsImplLauncher(unittest.TestCase):
    _MARKER = {"base_url": "http://gw", "key_file": "~/.secrets/k",
               "main": "m", "sub": "s", "fast": "f",
               "cwd": "/home/miva1/devel/odoo/odoo-erp"}

    def _relaunch_argv(self):
        fake = _FakeTmuxRun("miva1\t0\tmiva1\n")
        rc = _tmux_io.impl_window_presence(self._MARKER, run=fake, logs=[])
        self.assertEqual(rc, "relaunched")
        nw = fake.new_window_calls()
        self.assertEqual(len(nw), 1, nw)
        return nw[0]

    def test_relaunch_argv_wraps_the_launcher(self):
        argv = self._relaunch_argv()
        self.assertIn("bash", argv)
        i = argv.index("bash")
        self.assertEqual(argv[i:i + 2], ["bash", "-lc"], argv)
        inner = argv[i + 2]
        self.assertTrue(inner.endswith("; exec bash -l"), inner)
        self.assertIn(_IMPL, inner)

    def test_relaunch_never_appends_a_bare_launcher(self):
        argv = self._relaunch_argv()
        # the final arg is the wrapper's inner command, NOT the bare launcher
        # path (which would end in `.sh`, closing the whole tmux window on exit).
        self.assertFalse(argv[-1].endswith(_IMPL), argv)
        self.assertTrue(argv[-1].endswith("; exec bash -l"), argv)


class TestThreeImplRenderersDriftLock(unittest.TestCase):
    """All three impl-window creators produce the SAME wrapper shape, tied to the
    canonical cli_tmux_provisioning._window_shell_command. The bashrc block and
    the watchdog MIRROR the one-liner (they must not import cli_tmux_provisioning);
    this lock fails the moment any of the three drifts from the canonical shape."""

    _MARKER = {"base_url": "http://gw", "key_file": "~/.secrets/k",
               "main": "m", "sub": "s", "fast": "f",
               "cwd": "/home/miva1/devel/odoo/odoo-erp"}

    def test_canonical_shape(self):
        self.assertEqual(ctp._window_shell_command("L"),
                         'bash -lc "L; exec bash -l"')

    def test_all_three_match_the_canonical_shape(self):
        canonical = ctp._window_shell_command("$HOME/.claude/%s" % _IMPL)

        # 1) session-created hook snippet
        snip = ctp._impl_window_create_snippet(self._MARKER)
        self.assertIn(canonical, snip)

        # 2) bashrc attach block (mirror)
        block = cli_bashrc_appliers.render_tmux_attach_block("miva1")
        self.assertIn(canonical, block)

        # 3) watchdog relaunch argv (mirror; $HOME expanded to an abs path)
        fake = _FakeTmuxRun("miva1\t0\tmiva1\n")
        _tmux_io.impl_window_presence(self._MARKER, run=fake, logs=[])
        argv = fake.new_window_calls()[0]
        i = argv.index("bash")
        inner = argv[i + 2]                       # "<abs launcher>; exec bash -l"
        launcher_part = inner[:-len("; exec bash -l")]
        # reconstruct the wrapper string from the argv and compare to canonical
        self.assertEqual('bash -lc "%s"' % inner,
                         ctp._window_shell_command(launcher_part))
        self.assertTrue(launcher_part.endswith(_IMPL), launcher_part)


if __name__ == "__main__":
    unittest.main()
