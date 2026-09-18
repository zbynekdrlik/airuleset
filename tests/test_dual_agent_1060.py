"""#1060 Lane L3b — the dual-agent dispatch channel + implementer discipline.

Covers design items 6–9 (the L3b lane):
- (6) gates.designdispatch receiving-side CLI (`--issue N --slug owner/repo`)
      the implementer runs BEFORE any work (exit 2 unless the newest design
      comment is role-main + the Fable id); the implementer.md system prompt is
      rendered + install-copied to ~/.claude/airuleset-implementer.md.
- (7) the autopilot SKILL `dual` dispatch section, check-locked verbatim by
      goal_registry.skill_dual_drift (goal-inventory --check).
- (8) cli_authorship gains the `implementer` role (AIRULESET_ROLE) + the alias
      stamps literally; the watchdog impl-window presence line + relaunch.
- (8a) the webterm session-created hook creates the marker box's impl window.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FABLE = "claude-fable-5-1"


# --------------------------------------------------------------------------- #
# Item 6 — the receiving-side design gate CLI
# --------------------------------------------------------------------------- #
class TestDesignGateReceivingCLI(unittest.TestCase):
    def setUp(self):
        from gates import designdispatch as dd
        self.dd = dd

    def test_cli_ok_when_newest_is_main_fable(self):
        rc = self.dd._run_issue_cli(
            ["--issue", "1060", "--slug", "owner/repo"],
            fetch=lambda s, n, c: ["Design-by: main %s" % FABLE],
            fable_id=FABLE)
        self.assertEqual(rc, 0)

    def test_cli_blocks_when_newest_is_worker(self):
        rc = self.dd._run_issue_cli(
            ["--issue", "1060", "--slug", "owner/repo"],
            fetch=lambda s, n, c: ["Design-by: worker claude-opus-4-8"],
            fable_id=FABLE)
        self.assertEqual(rc, 2)

    def test_cli_blocks_when_no_design_comment(self):
        rc = self.dd._run_issue_cli(
            ["--issue", "1060", "--slug", "owner/repo"],
            fetch=lambda s, n, c: ["just a normal comment"],
            fable_id=FABLE)
        self.assertEqual(rc, 2)

    def test_cli_blocks_when_thread_unreadable_failclosed(self):
        rc = self.dd._run_issue_cli(
            ["--issue", "1060", "--slug", "owner/repo"],
            fetch=lambda s, n, c: None, fable_id=FABLE)
        self.assertEqual(rc, 2)

    def test_cli_blocks_when_non_fable_main(self):
        rc = self.dd._run_issue_cli(
            ["--issue", "1060", "--slug", "owner/repo"],
            fetch=lambda s, n, c: ["Design-by: main claude-opus-4-8"],
            fable_id=FABLE)
        self.assertEqual(rc, 2)

    def test_cli_blocks_when_slug_unresolvable(self):
        # no --slug and the resolver returns None -> fail-closed exit 2
        rc = self.dd._run_issue_cli(
            ["--issue", "1060"],
            fetch=lambda s, n, c: ["Design-by: main %s" % FABLE],
            fable_id=FABLE, resolve_slug=lambda cwd: None)
        self.assertEqual(rc, 2)

    def _run_gate_subprocess(self, comment_body):
        # End-to-end wiring: `python3 -m gates.designdispatch --issue N --slug`
        # (the canonical module invocation the implementer.md documents) routes
        # to the CLI and exits with the gate's code, using a fake `gh`.
        with tempfile.TemporaryDirectory() as td:
            gh = Path(td) / "gh"
            body = comment_body.replace("'", "'\\''")
            gh.write_text(
                '#!/usr/bin/env bash\n'
                "echo '{\"body\":\"%s\"}'\n" % body)
            gh.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = str(td) + os.pathsep + env["PATH"]
            env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
            r = subprocess.run(
                [sys.executable, "-P", "-m", "gates.designdispatch",
                 "--issue", "1060", "--slug", "owner/repo"],
                capture_output=True, text=True, env=env, cwd=str(REPO))
            return r

    def test_main_routes_issue_argv_to_cli_subprocess(self):
        r = self._run_gate_subprocess("Design-by: worker claude-opus-4-8")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_main_routes_issue_argv_ok_subprocess(self):
        r = self._run_gate_subprocess("Design-by: main %s" % FABLE)
        self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# Item 8 — cli_authorship: the `implementer` role + the alias stamps literally
# --------------------------------------------------------------------------- #
class TestImplementerAuthorship(unittest.TestCase):
    def setUp(self):
        import cli_authorship
        self.a = cli_authorship
        self._saved = {k: os.environ.get(k)
                       for k in ("AIRULESET_ROLE", "ANTHROPIC_MODEL")}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_role_implementer_from_env_beats_worktree(self):
        os.environ["AIRULESET_ROLE"] = "implementer"
        cwd = "/home/miva1/devel/x/.claude/worktrees/agent-abc"
        self.assertEqual(self.a.authorship_role(cwd), "implementer")

    def test_role_implementer_from_env_on_main_checkout(self):
        os.environ["AIRULESET_ROLE"] = "implementer"
        self.assertEqual(self.a.authorship_role("/home/miva1/devel/x"),
                         "implementer")

    def test_no_role_env_unchanged_worker(self):
        os.environ.pop("AIRULESET_ROLE", None)
        cwd = "/home/x/.claude/worktrees/agent-abc"
        self.assertEqual(self.a.authorship_role(cwd), "worker")

    def test_other_role_env_ignored(self):
        os.environ["AIRULESET_ROLE"] = "somethingelse"
        self.assertEqual(self.a.authorship_role("/home/x"), "main")

    def test_session_model_stamps_alias_literally(self):
        os.environ["AIRULESET_ROLE"] = "implementer"
        os.environ["ANTHROPIC_MODEL"] = "impl-main"
        self.assertEqual(
            self.a.session_model("/home/x/.claude/worktrees/agent-abc"),
            "impl-main")

    def test_stamp_line_implemented_by_alias(self):
        os.environ["AIRULESET_ROLE"] = "implementer"
        os.environ["ANTHROPIC_MODEL"] = "impl-main"
        self.assertEqual(
            self.a.stamp_line("Implemented", "/home/x/.claude/worktrees/agent-a"),
            "Implemented-by: implementer impl-main")


# --------------------------------------------------------------------------- #
# Item 6 — the implementer.md system prompt: exists, disciplined, install-copied
# --------------------------------------------------------------------------- #
class TestImplementerPrompt(unittest.TestCase):
    def setUp(self):
        self.src = REPO / "agents" / "implementer.md"

    def test_implementer_md_exists(self):
        self.assertTrue(self.src.is_file(), "agents/implementer.md must exist")

    def test_implementer_md_carries_the_discipline(self):
        text = self.src.read_text(encoding="utf-8")
        # the receiving-side design gate the implementer runs BEFORE any work
        self.assertIn("gates.designdispatch", text)
        self.assertIn("--issue", text)
        self.assertIn("Design-question:", text)
        # the cross-session channel + the hand-off shape
        self.assertIn("cross-session-message", text)
        self.assertIn("LANE-RETURN", text)
        self.assertIn("SendMessage", text)
        # the negative discipline (never handoff / merge / design)
        low = text.lower()
        self.assertIn("never author", low)
        self.assertIn("never merge", low)
        self.assertIn("airuleset.py handoff", text)
        # worktree durability
        self.assertIn("refs/autopilot-wip/", text)

    def test_render_returns_source_verbatim(self):
        import cli_claude_scripts as ccs
        self.assertEqual(ccs.render_claude_implementer_prompt(),
                         self.src.read_text(encoding="utf-8"))

    def test_install_copies_implementer_prompt(self):
        import cli_bashrc_appliers as cba
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            claude = home / ".claude"
            claude.mkdir()
            bashrc = home / ".bashrc"
            bashrc.write_text("")
            impl_prompt = claude / "airuleset-implementer.md"
            cba.apply_ultracode_launcher(
                bashrc_path=bashrc,
                script_path=claude / "airuleset-claude-launch.sh",
                history_script_path=claude / "airuleset-claude-history.py",
                popup_script_path=claude / "airuleset-claude-history-popup.sh",
                impl_script_path=claude / "airuleset-claude-impl.sh",
                impl_prompt_path=impl_prompt)
            self.assertTrue(impl_prompt.is_file())
            self.assertEqual(
                impl_prompt.read_text(encoding="utf-8"),
                (REPO / "agents" / "implementer.md").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Item 7 — the autopilot SKILL `dual` dispatch section, check-locked verbatim
# --------------------------------------------------------------------------- #
class TestDualDispatchClauseLock(unittest.TestCase):
    def setUp(self):
        import goal_registry
        self.gr = goal_registry
        self.skill = (REPO / "skills" / "autopilot" / "SKILL.md").read_text(
            encoding="utf-8")

    def test_goal_registry_exposes_the_dual_lock(self):
        self.assertTrue(hasattr(self.gr, "skill_dual_drift"))
        self.assertTrue(hasattr(self.gr, "_DUAL_DISPATCH"))

    def test_skill_carries_the_dual_clause_verbatim(self):
        self.assertIn(self.gr._DUAL_DISPATCH, self.skill)
        self.assertEqual(self.gr.skill_dual_drift(self.skill), [])

    def test_dual_drift_flags_a_missing_clause(self):
        self.assertNotEqual(self.gr.skill_dual_drift("no dual clause here"), [])

    def test_dual_clause_names_the_channel_and_fallback(self):
        c = self.gr._DUAL_DISPATCH
        self.assertIn("SendMessage", c)
        self.assertIn("ListAgents", c)
        self.assertIn("LANE-RETURN", c)
        self.assertIn("dual: implementer session missing", c)

    def test_skill_default_goal_lines_still_match_registry(self):
        # The dual section must NOT alter the shipped `/goal` lines (they stay
        # byte-identical to render(profile)) — drift() must stay empty.
        self.assertEqual(self.gr.drift(self.skill), [])

    def test_goal_inventory_check_passes(self):
        r = subprocess.run(
            [sys.executable, str(REPO / "airuleset.py"),
             "goal-inventory", "--check"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_goal_inventory_check_catches_a_missing_dual_clause(self):
        # A SKILL.md stripped of the dual clause must FAIL --check.
        import goal_registry as gr
        with tempfile.TemporaryDirectory() as td:
            fake_skill = Path(td) / "SKILL.md"
            stripped = self.skill.replace(gr._DUAL_DISPATCH, "")
            self.assertNotIn(gr._DUAL_DISPATCH, stripped)
            self.assertNotEqual(gr.skill_dual_drift(stripped), [])


# --------------------------------------------------------------------------- #
# Item 8 — the watchdog impl-window presence line + relaunch (fake tmux run)
# --------------------------------------------------------------------------- #
class _FakeTmux:
    """A fake `run(argv)` for the watchdog: returns canned `list-windows`
    output and records every OTHER tmux argv it is handed."""
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


class TestWatchdogImplWindowPresence(unittest.TestCase):
    def setUp(self):
        import watchdog.tmux_io as tmux_io
        self.tmux_io = tmux_io
        self.marker = {"base_url": "http://gw", "key_file": "~/.secrets/k",
                       "main": "impl-main", "sub": "impl-sub", "fast": "impl-fast",
                       "cwd": "/home/miva1/devel/odoo/odoo-erp"}

    def test_no_marker_is_a_noop_no_line(self):
        fake = _FakeTmux("miva1\t0\tmiva1\n")
        logs = []
        rc = self.tmux_io.impl_window_presence(None, run=fake, logs=logs)
        self.assertIsNone(rc)
        self.assertEqual(fake.new_window_calls(), [])
        self.assertEqual(logs, [])

    def test_impl_window_present_no_relaunch(self):
        fake = _FakeTmux("miva1\t0\tmiva1\nmiva1\t1\timpl\n")
        logs = []
        rc = self.tmux_io.impl_window_presence(self.marker, run=fake, logs=logs)
        self.assertEqual(rc, "present")
        self.assertEqual(fake.new_window_calls(), [])
        self.assertTrue(any("impl window present" in x for x in logs))

    def test_impl_window_missing_relaunched(self):
        fake = _FakeTmux("miva1\t0\tmiva1\n")
        logs = []
        rc = self.tmux_io.impl_window_presence(self.marker, run=fake, logs=logs)
        self.assertEqual(rc, "relaunched")
        nw = fake.new_window_calls()
        self.assertEqual(len(nw), 1)
        argv = nw[0]
        self.assertIn("-n", argv)
        self.assertEqual(argv[argv.index("-n") + 1], "impl")
        self.assertIn("-t", argv)
        self.assertEqual(argv[argv.index("-t") + 1], "miva1")
        # opens in the marker cwd (it exists nowhere on this box -> may be
        # omitted; assert the DECISION line regardless)
        self.assertTrue(any("impl window missing — relaunched" in x for x in logs))
        # keeps the pane visible on a refusal
        self.assertTrue(any(c[:2] == ["tmux", "set-window-option"] and
                            "remain-on-exit" in c for c in fake.calls))

    def test_relaunch_uses_marker_cwd_when_it_exists(self):
        with tempfile.TemporaryDirectory() as td:
            m = dict(self.marker, cwd=td)
            fake = _FakeTmux("miva1\t0\tmiva1\n")
            self.tmux_io.impl_window_presence(m, run=fake)
            argv = fake.new_window_calls()[0]
            self.assertIn("-c", argv)
            self.assertEqual(argv[argv.index("-c") + 1], td)

    def test_no_session_never_creates_one(self):
        fake = _FakeTmux("")  # no tmux session on the box
        logs = []
        rc = self.tmux_io.impl_window_presence(self.marker, run=fake, logs=logs)
        self.assertEqual(rc, "no-session")
        self.assertEqual(fake.new_window_calls(), [])

    def test_dry_run_never_relaunches(self):
        fake = _FakeTmux("miva1\t0\tmiva1\n")
        logs = []
        rc = self.tmux_io.impl_window_presence(self.marker, run=fake, logs=logs,
                                               dry_run=True)
        self.assertEqual(rc, "would-relaunch")
        self.assertEqual(fake.new_window_calls(), [])

    def test_reexported_on_watchdog(self):
        import watchdog
        self.assertIs(watchdog.impl_window_presence,
                      self.tmux_io.impl_window_presence)


# --------------------------------------------------------------------------- #
# Item 8a — the webterm session-created hook creates the marker box's impl window
# --------------------------------------------------------------------------- #
class TestSessionCreatedHookImplWindow(unittest.TestCase):
    def setUp(self):
        import cli_tmux_provisioning as ctp
        self.ctp = ctp
        self.marker = {"base_url": "http://gw", "key_file": "~/.secrets/k",
                       "main": "impl-main", "sub": "impl-sub", "fast": "impl-fast",
                       "cwd": "/home/miva1/devel/odoo/odoo-erp"}

    def test_non_marker_box_byte_identical(self):
        # marker=None -> byte-identical to today's bare rename-window hook.
        v = self.ctp._session_created_hook_value("miva1", [], marker=None)
        self.assertEqual(v, "rename-window miva1")

    def test_marker_box_extends_hook_with_impl_create(self):
        v = self.ctp._session_created_hook_value("miva1", [], marker=self.marker)
        self.assertIn("rename-window miva1", v)
        self.assertIn("run-shell 'S=#{session_name}; ", v)
        self.assertIn("new-window -d -t \"$S\" -n impl", v)
        # marker-gated at shell time + dedup by window name + cwd baked + visible
        self.assertIn("airuleset-model-backend.json", v)
        self.assertIn("##{window_name}", v)   # doubled for the outer run-shell
        self.assertIn("/home/miva1/devel/odoo/odoo-erp", v)
        self.assertIn("remain-on-exit on", v)

    def test_marker_box_still_creates_declared_windows_too(self):
        # a marker box that ALSO declares a non-primary window keeps both.
        windows = [{"name": "miva1", "cwd": "~/devel/x"},
                   {"name": "miva1-infra", "cwd": "devel/x-infra"}]
        v = self.ctp._session_created_hook_value("miva1", windows,
                                                 marker=self.marker)
        self.assertIn("-n miva1-infra", v)   # declared window body present
        self.assertIn("-n impl", v)          # impl window present too

    def test_marker_uses_single_quote_safe_body(self):
        # the whole run-shell body is wrapped in single quotes -> it must carry
        # NO single quote of its own (the _managed_windows_create_body contract).
        v = self.ctp._session_created_hook_value("miva1", [], marker=self.marker)
        body = v.split("run-shell '", 1)[1]
        body = body.rsplit("'", 1)[0]
        self.assertNotIn("'", body)


class TestAttachBlockImplDedup(unittest.TestCase):
    def test_attach_block_dedups_impl_window(self):
        # An interactive-ssh new-session -d also fires the -g session-created
        # hook, which may already have created impl; the attach block must NOT
        # create a second one -> it guards on a list-windows dedup check.
        import cli_bashrc_appliers as cba
        block = cba.render_tmux_attach_block("miva1")
        # the impl creation is guarded by a window-name dedup check
        self.assertIn("list-windows", block)
        self.assertIn("impl", block)
        # the dedup uses grep -Fxq impl (skip if it already exists)
        self.assertIn("grep -Fxq impl", block)


if __name__ == "__main__":
    unittest.main()
