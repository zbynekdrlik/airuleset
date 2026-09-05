"""#651 — managed `t`/`tmux()` interactive attach-or-create wrapper +
idempotent owner-session normalization.

Root cause (repro'd live, isolated `-S` socket): `tmux new -t <name>` is the
GROUP-target form — it always creates a NEW grouped sibling (`<name>-1`,
`<name>-2`, …), so the owner's arrow-up `tmux new -t zbynek` piles them up.
The native attach-or-create primitive is `tmux new-session -A -s <name>`.

These tests exercise the GENERATED bashrc block's shell functions with NO
real tmux: a FAKE `command tmux` shim on PATH records the argv it receives,
in a bash subshell whose env is scrubbed of TMUX/TMUX_PANE (so nothing can
ever reach the box's real server — the #613 isolation discipline; the
`tests/test_tmux_test_isolation_lock.py` lock is satisfied because no
`subprocess.run` here passes a real destructive `tmux` argv literal at all).
The `_live_normalize_owner_session` tests use the repo's established
dependency-injected fake-`run` pattern (records argv, returns canned
output) — also no real tmux.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli_bashrc_appliers as appliers
import cli_tmux_provisioning as tmuxprov


# --------------------------------------------------------------------------- #
# Shell-side harness: source the generated block, run one shell command line
# against a fake `command tmux` recorder. No real tmux, no TTY.
# --------------------------------------------------------------------------- #
FAKE_TMUX = '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$REC"\n'


def _run_shell(block, cmdline, interactive=True):
    """Source `block` then run `cmdline` in a bash subshell. Returns
    (recorded_argv_lines, declared_funcs). `interactive=True` forces the
    `$- == *i*` guard branch on via `bash -ic` (TTY-free — verified);
    `False` uses plain `-c` so the guard must SKIP."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        fake = tdp / "tmux"
        fake.write_text(FAKE_TMUX)
        os.chmod(str(fake), 0o755)
        blockfile = tdp / "block.sh"
        blockfile.write_text(block + "\n")
        rec = tdp / "rec"
        rec.write_text("")
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env["PATH"] = str(tdp) + os.pathsep + env.get("PATH", "")
        env["REC"] = str(rec)
        script = ('source "%s"; declare -F t tmux; echo "__RUN__"; %s'
                  % (blockfile, cmdline))
        flag = "-ic" if interactive else "-c"
        subprocess.run(["bash", "--norc", "--noprofile", flag, script],
                       env=env, capture_output=True, text=True, timeout=20)
        recorded = [ln for ln in rec.read_text().splitlines() if ln.strip()]
        return recorded


def _declared_funcs(block, interactive):
    """The `t`/`tmux` functions bash sees defined after sourcing `block`."""
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        blockfile = tdp / "block.sh"
        blockfile.write_text(block + "\n")
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        script = 'source "%s"; declare -F t tmux' % blockfile
        flag = "-ic" if interactive else "-c"
        out = subprocess.run(["bash", "--norc", "--noprofile", flag, script],
                             env=env, capture_output=True, text=True, timeout=20)
        return out.stdout


class TestTmuxWrapperRewrite(unittest.TestCase):
    def setUp(self):
        self.block = appliers.render_tmux_attach_block("zbynek")

    def test_new_t_rewritten_to_attach_or_create(self):
        # THE bug shape: `tmux new -t X` must become `new-session -A -s X`.
        rec = _run_shell(self.block, "tmux new -t proj")
        self.assertEqual(rec, ["new-session -A -s proj"])

    def test_new_session_t_rewritten(self):
        rec = _run_shell(self.block, "tmux new-session -t proj")
        self.assertEqual(rec, ["new-session -A -s proj"])

    def test_all_attach_forms_rewritten(self):
        for verb in ("a", "attach", "attach-session"):
            rec = _run_shell(self.block, "tmux %s -t proj" % verb)
            self.assertEqual(rec, ["new-session -A -s proj"],
                             "verb %r not rewritten" % verb)

    def test_extra_flags_pass_through_unchanged(self):
        # 4+ args (an extra flag present) must NOT be rewritten.
        rec = _run_shell(self.block, "tmux new -t proj -d")
        self.assertEqual(rec, ["new -t proj -d"])

    def test_other_subcommands_pass_through(self):
        for line, expect in (("tmux ls", "ls"),
                             ("tmux kill-server", "kill-server"),
                             ("tmux list-sessions", "list-sessions")):
            rec = _run_shell(self.block, line)
            self.assertEqual(rec, [expect], "%r mis-handled" % line)

    def test_t_bare_uses_default_session(self):
        rec = _run_shell(self.block, "t")
        self.assertEqual(rec, ["new-session -A -s zbynek"])

    def test_t_with_name(self):
        rec = _run_shell(self.block, "t other")
        self.assertEqual(rec, ["new-session -A -s other"])

    def test_functions_defined_only_when_interactive(self):
        # `declare -F t tmux` prints just the bare names of the DEFINED
        # functions (nothing for an undefined one). Interactive: both defined.
        interactive = _declared_funcs(self.block, interactive=True).split()
        self.assertIn("t", interactive)
        self.assertIn("tmux", interactive)
        # Non-interactive: guard skips -> neither defined -> empty output.
        noninteractive = _declared_funcs(self.block, interactive=False).split()
        self.assertNotIn("t", noninteractive)
        self.assertNotIn("tmux", noninteractive)


class TestOwnerSessionDefault(unittest.TestCase):
    def test_owner_box_uses_owner_group(self):
        with mock.patch.object(appliers, "is_single_session_box_user",
                               return_value=False), \
             mock.patch("cli_webterm.OWNER_GROUP", "zbynek"):
            self.assertEqual(appliers._owner_session_default("newlevel"), "zbynek")

    def test_stream_box_uses_own_username(self):
        with mock.patch.object(appliers, "is_single_session_box_user",
                               return_value=True):
            self.assertEqual(appliers._owner_session_default("montalu4"), "montalu4")


class _FakeRun:
    """Records argv, returns canned list-sessions output for the FIRST
    list-sessions call, and a success object for everything else."""
    def __init__(self, session_names):
        self._names = session_names
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if "list-sessions" in argv:
            return subprocess.CompletedProcess(
                argv, 0, stdout="\n".join(self._names) + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def rename_calls(self):
        return [c for c in self.calls if "rename-session" in c]


class TestNormalizeOwnerSession(unittest.TestCase):
    def test_renames_lone_numbered_survivor(self):
        run = _FakeRun(["zbynek-3"])
        tmuxprov._live_normalize_owner_session("zbynek", run=run)
        self.assertEqual(run.rename_calls(),
                         [["tmux", "rename-session", "-t", "zbynek-3", "zbynek"]])

    def test_noop_when_exact_session_exists(self):
        run = _FakeRun(["zbynek", "zbynek-3"])
        tmuxprov._live_normalize_owner_session("zbynek", run=run)
        self.assertEqual(run.rename_calls(), [])

    def test_noop_when_multiple_survivors(self):
        run = _FakeRun(["zbynek-1", "zbynek-2"])
        tmuxprov._live_normalize_owner_session("zbynek", run=run)
        self.assertEqual(run.rename_calls(), [])

    def test_noop_when_no_survivor(self):
        run = _FakeRun(["someproject", "another"])
        tmuxprov._live_normalize_owner_session("zbynek", run=run)
        self.assertEqual(run.rename_calls(), [])

    def test_does_not_touch_sessions_outside_owner_namespace(self):
        # a `zbynek-foo` (non-numeric tail) is NOT an owner grouped sibling.
        run = _FakeRun(["zbynek-foo"])
        tmuxprov._live_normalize_owner_session("zbynek", run=run)
        self.assertEqual(run.rename_calls(), [])

    def test_no_server_is_noop_not_exception(self):
        def boom(argv):
            raise FileNotFoundError("tmux: no server")
        # must not raise
        tmuxprov._live_normalize_owner_session("zbynek", run=boom)

    def test_list_failure_returncode_noop(self):
        def failing(argv):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no server")
        run_wrap = failing
        tmuxprov._live_normalize_owner_session("zbynek", run=run_wrap)
        # nothing to assert beyond "no crash + no rename"; a bare fn returns None


# --------------------------------------------------------------------------- #
# #660 — FLEET-WIDE kill-sweep for standalone idle-shell strays.
#
# Root cause (repro'd live across dev1+dev2 via the audit hook): the creator is
# always `tmux new-session -A -s <name>` from an interactive ssh session (the
# #651 `t`/`tmux()` wrapper mechanism) whose attach hangs, leaving a STANDALONE
# idle bare-bash session; `<name>` is either the owner group with a numeric
# suffix (`zbynek-0`) or a fleet STREAM username (dev2's `marek`/`marek-12`).
# When the canonical `<owner>` exists, normalization absorbs each STRAY-named
# session (owner-N, plus on an owner box the stream families) IFF it is provably
# idle: unattached AND ungrouped AND every pane a bare shell with no child
# process — NEVER one running claude / a non-shell / a suspended-or-background
# job / a grouped or attached session (feedback_never_touch_stopped_sessions).
# Same dependency-injected fake-`run`+`ps` discipline; no real tmux/ps.
# --------------------------------------------------------------------------- #
# the fake box's HOME -- the cwd-guard passes iff a pane's cwd equals it.
_HOME = "/home/tester"


class _FakeServer:
    """Fake tmux+ps runner: maps session name -> (attached, group, [cmds]) and
    answers list-sessions / list-panes (5-field `#{session_attached}\\t
    #{session_group}\\t#{pane_current_command}\\t#{pane_pid}\\t
    #{pane_current_path}`) and the ps child-probe (`ps -o pid= --ppid <pid>`).
    A session in `busy` has a pane shell with a child (a suspended/background
    job); `cwds` overrides a session's pane cwd (default `_HOME`) so the
    cwd-guard can be exercised. No real tmux/ps, invisible to the isolation
    lock. `kill_rc` forces a non-zero kill-session return."""
    def __init__(self, sessions, busy=None, kill_rc=0, cwds=None):
        self._sessions = {}
        for name, spec in sessions.items():
            att, grp, cmds = (spec if len(spec) == 3
                              else (spec[0], "", spec[1]))
            self._sessions[name] = (att, grp, list(cmds))
        self._busy = set(busy or ())
        self._kill_rc = kill_rc
        self._cwds = dict(cwds or {})
        self._pid2sess = {}
        self._pc = 9000
        self.calls = []

    @staticmethod
    def _target(argv):
        for i, a in enumerate(argv):
            if a == "-t" and i + 1 < len(argv):
                return argv[i + 1].lstrip("=")
        return None

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:1] == ["ps"]:
            pid = argv[-1]
            name = self._pid2sess.get(pid)
            has = name in self._busy
            return subprocess.CompletedProcess(
                argv, 0 if has else 1,
                stdout=("4242\n" if has else ""), stderr="")
        if "list-sessions" in argv:
            out = "".join(n + "\n" for n in self._sessions)
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        if "list-panes" in argv:
            name = self._target(argv)
            att, grp, cmds = self._sessions.get(name, ("0", "", []))
            cwd = self._cwds.get(name, _HOME)
            out = ""
            for c in cmds:
                pid = str(self._pc)
                self._pc += 1
                self._pid2sess[pid] = name
                out += "%s\t%s\t%s\t%s\t%s\n" % (att, grp, c, pid, cwd)
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        if "kill-session" in argv:
            if self._kill_rc == 0:
                self._sessions.pop(self._target(argv), None)
            return subprocess.CompletedProcess(argv, self._kill_rc,
                                               stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def kill_calls(self):
        return [c for c in self.calls if "kill-session" in c]

    def rename_calls(self):
        return [c for c in self.calls if "rename-session" in c]


# The widened owner-box stray patterns (owner-N + fleet stream families), the
# SAME the cmd_install caller builds via `_owner_box_stray_name_res`.
_OWNER_BOX_RES = tmuxprov._owner_box_stray_name_res("zbynek", single_session=False)


class TestNormalizeKillSweep660(unittest.TestCase):
    def _run(self, sessions, owner="zbynek", audit_dir=None,
             stray_name_res=None, busy=None, kill_rc=0, cwds=None):
        srv = _FakeServer(sessions, busy=busy, kill_rc=kill_rc, cwds=cwds)
        tmuxprov._live_normalize_owner_session(
            owner, run=srv, audit_dir=audit_dir,
            stray_name_res=stray_name_res, ps_run=srv, home=_HOME)
        return srv

    # --- <owner>-N (default namespace) --------------------------------------
    def test_kills_idle_bare_shell_stray_when_owner_exists(self):
        srv = self._run({"zbynek": ("1", "", ["node"]),
                         "zbynek-0": ("0", "", ["bash"])})
        self.assertEqual(srv.kill_calls(),
                         [["tmux", "kill-session", "-t", "=zbynek-0"]])

    def test_never_kills_stray_running_claude(self):
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "zbynek-0": ("0", "", ["claude"])})
        self.assertEqual(srv.kill_calls(), [])

    def test_never_kills_attached_stray(self):
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "zbynek-0": ("1", "", ["bash"])})
        self.assertEqual(srv.kill_calls(), [])

    def test_never_kills_grouped_stray(self):
        # a grouped session shares a base's windows / is a work session.
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "zbynek-0": ("0", "zbynek", ["bash"])})
        self.assertEqual(srv.kill_calls(), [])

    def test_never_kills_when_any_pane_is_non_shell(self):
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "zbynek-0": ("0", "", ["bash", "vim"])})
        self.assertEqual(srv.kill_calls(), [])

    def test_never_kills_when_pane_has_child(self):
        # bare bash foreground but a SUSPENDED/background child (Ctrl-Z'd claude)
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "zbynek-0": ("0", "", ["bash"])},
                        busy={"zbynek-0"})
        self.assertEqual(srv.kill_calls(), [])

    def test_kills_multiple_idle_strays(self):
        srv = self._run({"zbynek": ("1", "", ["node"]),
                         "zbynek-0": ("0", "", ["bash"]),
                         "zbynek-2": ("0", "", ["-bash"])})
        killed = {c[-1] for c in srv.kill_calls()}
        self.assertEqual(killed, {"=zbynek-0", "=zbynek-2"})

    def test_no_kill_sweep_when_owner_absent(self):
        # owner missing -> the RENAME path runs, never the kill sweep.
        srv = self._run({"zbynek-0": ("0", "", ["bash"])})
        self.assertEqual(srv.kill_calls(), [])
        self.assertEqual(srv.rename_calls(),
                         [["tmux", "rename-session", "-t", "zbynek-0", "zbynek"]])

    def test_kill_non_zero_does_not_log_killed(self):
        # review 🟡3: a kill-session that returns non-zero is a SKIP, never
        # a false "killed" audit line.
        with tempfile.TemporaryDirectory() as td:
            self._run({"zbynek": ("1", "", ["node"]),
                       "zbynek-0": ("0", "", ["bash"])},
                      audit_dir=td, kill_rc=1)
            text = (Path(td) / "normalize.log").read_text()
            self.assertNotIn("killed", text)
            self.assertIn("skip", text)

    # --- fleet-wide stream-family strays (dev2 incident) ----------------------
    def test_kills_foreign_stream_stray_on_owner_box(self):
        # `montalu4` (a stream user) STANDALONE + idle on an owner box IS a stray.
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "montalu4": ("0", "", ["bash"]),
                         "montalu4-12": ("0", "", ["bash"])},
                        stray_name_res=_OWNER_BOX_RES)
        killed = {c[-1] for c in srv.kill_calls()}
        self.assertEqual(killed, {"=montalu4", "=montalu4-12"})

    def test_preserves_grouped_stream_work_session(self):
        # montalu4-3 (grouped, real work) + david-0 (attached) preserved; only
        # the standalone idle `montalu4` stray is absorbed.
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "montalu4": ("0", "", ["bash"]),
                         "montalu4-3": ("0", "montalu4", ["bash", "bash"]),
                         "david-0": ("1", "david", ["node"])},
                        stray_name_res=_OWNER_BOX_RES)
        self.assertEqual({c[-1] for c in srv.kill_calls()}, {"=montalu4"})

    def test_default_namespace_ignores_stream_names(self):
        # WITHOUT the widened res, a `montalu4` stray is NOT swept (owner-N only).
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "montalu4": ("0", "", ["bash"])})
        self.assertEqual(srv.kill_calls(), [])

    def test_never_kills_stream_work_session_cwd_in_project_dir(self):
        # the second-review residual: a `montalu4` name doubles as a real work
        # session whose stopped-claude bare shell sits in a PROJECT dir -- the
        # cwd-guard preserves it while a $HOME stray is still absorbed.
        srv = self._run({"zbynek": ("1", "", ["bash"]),
                         "montalu4": ("0", "", ["bash"]),         # cwd $HOME -> kill
                         "montalu4-2": ("0", "", ["bash"])},      # cwd project -> skip
                        stray_name_res=_OWNER_BOX_RES,
                        cwds={"montalu4-2": "/home/tester/devel/odoo"})
        self.assertEqual({c[-1] for c in srv.kill_calls()}, {"=montalu4"})

    def test_audit_log_records_kill(self):
        with tempfile.TemporaryDirectory() as td:
            self._run({"zbynek": ("1", "", ["node"]),
                       "zbynek-0": ("0", "", ["bash"])}, audit_dir=td)
            log = Path(td) / "normalize.log"
            self.assertTrue(log.is_file())
            text = log.read_text()
            self.assertIn("killed", text)
            self.assertIn("zbynek-0", text)

    def test_audit_log_records_skip_for_claude(self):
        with tempfile.TemporaryDirectory() as td:
            self._run({"zbynek": ("1", "", ["bash"]),
                       "zbynek-0": ("0", "", ["claude"])}, audit_dir=td)
            text = (Path(td) / "normalize.log").read_text()
            self.assertIn("skip", text)
            self.assertIn("zbynek-0", text)


class TestCmdInstallWiring660(unittest.TestCase):
    """#660 review 🟡1/🟡2: lock the two cmd_install invariants a `cli_quals`/
    doctrine grep misses -- the wiring is the ONE place they live.

    (getsource can read a mid-integration-mutated file, the #629 race -- if
    these ever fail together with the airuleset getsource canaries, look for
    the push-gate VOID report before treating it as a regression.)"""
    def setUp(self):
        import inspect
        import airuleset
        self.src = inspect.getsource(airuleset.cmd_install)

    def test_stream_widening_is_gated_on_owner_box_only(self):
        # the widened stray patterns are built via `_owner_box_stray_name_res`
        # and MUST be gated on `is_single_session_box_user` (owner boxes only) --
        # dropping the gate would sweep a subdev account's own real session.
        self.assertIn("_owner_box_stray_name_res(", self.src)
        self.assertIn("is_single_session_box_user(_current_user())", self.src)
        self.assertIn("_live_normalize_owner_session(", self.src)

    def test_audit_applier_runs_after_window_name_applier(self):
        # the audit `set-hook -g session-created` MUST be applied AFTER
        # apply_stream_tmux_window_name (whose owner-box revert live-UNSETS
        # session-created), or the live hook is wiped on every install.
        i_window = self.src.index("apply_stream_tmux_window_name()")
        i_audit = self.src.index("apply_owner_session_created_audit()")
        self.assertLess(i_window, i_audit,
                        "audit applier must run after apply_stream_tmux_window_name")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
