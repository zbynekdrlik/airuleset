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


if __name__ == "__main__":
    unittest.main()
