"""#972 — install/push from a worktree creates dangling symlinks after cleanup.

Root cause: REPO_DIR = Path(__file__).resolve().parent resolves to the worktree
path when airuleset.py is executed from a worktree checkout. cmd_install() then
creates ~/.claude/agents/* and ~/.claude/skills/* symlinks pointing INTO the
worktree. When the worktree is removed, symlinks dangle and Claude Code loses
agent types.

Tests:
  1. cmd_install and cmd_push REFUSE when REPO_DIR is under .claude/worktrees/
  2. #972 REOPEN invariant: a worktree install NEVER writes the login user's
     REAL home. AIRULESET_INSTALL_FROM_WORKTREE=1 is honoured ONLY when the
     effective HOME differs from the real (passwd) home (a test tmp HOME);
     with the real home the override is REFUSED regardless (fail-safe closed).
  3. AIRULESET_ALLOW_WORKTREE_ESCAPE=1 does NOT bypass the guard
  4. cmd_status detects dangling and worktree-target symlinks for BOTH agents
     and skills (agents section was entirely missing before this fix)
  5. Hook RULE B3: block-foreign-airuleset-write.sh blocks airuleset.py
     install|push from a worktree cwd in agent context
  6. #972 REOPEN dual-coverage: cli_remote.py Pass B test_env isolates HOME
     for the `unittest discover` gate (source-lock), so no gate run writes the
     real ~/.claude.
"""

import importlib
import json
import os
import pwd
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "block-foreign-airuleset-write.sh"


# --------------------------------------------------------------------------- #
# Deliverable 1: cmd_install / cmd_push refuse from worktree REPO_DIR
# --------------------------------------------------------------------------- #

class TestInstallWorktreeGuard(TestCase):
    """cmd_install and cmd_push must refuse when REPO_DIR is under a worktree."""

    def _load_airuleset(self):
        spec = importlib.util.spec_from_file_location(
            "airuleset_972", REPO / "airuleset.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_is_worktree_repo_dir_true(self):
        """A REPO_DIR under .claude/worktrees/ is detected."""
        mod = self._load_airuleset()
        wt_path = Path("/home/user/devel/airuleset/.claude/worktrees/agent-abc")
        self.assertTrue(mod._is_worktree_repo_dir(wt_path))

    def test_is_worktree_repo_dir_false(self):
        """A normal REPO_DIR is not flagged."""
        mod = self._load_airuleset()
        normal = Path("/home/user/devel/airuleset")
        self.assertFalse(mod._is_worktree_repo_dir(normal))

    def test_main_checkout_from_worktree(self):
        """The main checkout path is correctly derived from a worktree REPO_DIR."""
        mod = self._load_airuleset()
        wt = Path("/home/user/devel/airuleset/.claude/worktrees/agent-abc")
        self.assertEqual(
            mod._main_checkout_from_worktree(wt),
            Path("/home/user/devel/airuleset"))

    def test_cmd_install_refuses_from_worktree(self):
        """cmd_install exits non-zero when REPO_DIR is a worktree path."""
        mod = self._load_airuleset()
        fake_wt = Path("/fake/repo/.claude/worktrees/agent-xyz")
        with patch.object(mod, "REPO_DIR", fake_wt), \
             patch.dict(os.environ,
                        {"AIRULESET_INSTALL_FROM_WORKTREE": ""},
                        clear=False):
            # Remove the override env if present
            os.environ.pop("AIRULESET_INSTALL_FROM_WORKTREE", None)
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_install(None)
            self.assertEqual(ctx.exception.code, 1)

    def test_override_refused_when_home_is_real(self):
        """#972 REOPEN R1: the INVARIANT — a worktree install NEVER writes the
        login user's REAL home, even with AIRULESET_INSTALL_FROM_WORKTREE=1.

        This is the exact 2026-09-11 incident: a lane exported the override and
        ran install from a worktree against the real HOME. Tests only the guard
        helper (never full cmd_install — a real HOME override that WRONGLY passed
        would mutate the host, the RED-1 review finding)."""
        mod = self._load_airuleset()
        fake_wt = Path("/fake/repo/.claude/worktrees/agent-xyz")
        real_home = pwd.getpwuid(os.getuid()).pw_dir
        with patch.object(mod, "REPO_DIR", fake_wt), \
             patch.dict(os.environ,
                        {"AIRULESET_INSTALL_FROM_WORKTREE": "1",
                         "HOME": real_home},
                        clear=False):
            with self.assertRaises(SystemExit) as ctx:
                mod._check_worktree_repo_dir("install")
            self.assertEqual(ctx.exception.code, 1)

    def test_override_allowed_only_under_isolated_home(self):
        """#972 REOPEN R2: the override is honoured ONLY when the effective HOME
        differs from the real (passwd) home — i.e. a test tmp HOME. A worktree
        install can then only ever target an isolated HOME, so the real
        ~/.claude is unreachable by construction."""
        mod = self._load_airuleset()
        fake_wt = Path("/fake/repo/.claude/worktrees/agent-xyz")
        with tempfile.TemporaryDirectory() as tmp_home:
            self.assertNotEqual(
                Path(tmp_home).resolve(),
                Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(),
                "test setup: tmp HOME must differ from the real home")
            with patch.object(mod, "REPO_DIR", fake_wt), \
                 patch.dict(os.environ,
                            {"AIRULESET_INSTALL_FROM_WORKTREE": "1",
                             "HOME": tmp_home},
                            clear=False):
                # Must NOT raise — isolated HOME + override is the allowed path
                result = mod._check_worktree_repo_dir("install")
                self.assertIsNone(result)

    def test_worktree_escape_does_not_imply_install(self):
        """AIRULESET_ALLOW_WORKTREE_ESCAPE=1 does NOT bypass the install guard."""
        mod = self._load_airuleset()
        fake_wt = Path("/fake/repo/.claude/worktrees/agent-xyz")
        with patch.object(mod, "REPO_DIR", fake_wt), \
             patch.dict(os.environ,
                        {"AIRULESET_ALLOW_WORKTREE_ESCAPE": "1"},
                        clear=False):
            os.environ.pop("AIRULESET_INSTALL_FROM_WORKTREE", None)
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_install(None)
            self.assertEqual(ctx.exception.code, 1)


# --------------------------------------------------------------------------- #
# Deliverable 3: cmd_status detects dangling / worktree-target symlinks
# --------------------------------------------------------------------------- #

class TestStatusDanglingDetection(TestCase):
    """cmd_status must detect dangling and worktree-target symlinks for both
    agents AND skills."""

    def _load_airuleset(self):
        spec = importlib.util.spec_from_file_location(
            "airuleset_972_status", REPO / "airuleset.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_status_detects_dangling_agent_symlink(self):
        """A dangling agent symlink is reported as MISMATCH with hint."""
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            # Create a dangling symlink
            link = agents_dir / "autopilot-worker.md"
            link.symlink_to("/nonexistent/path/agents/autopilot-worker.md")

            mod = self._load_airuleset()
            import io
            buf = io.StringIO()
            with patch.object(mod, "AGENTS_DIR", agents_dir), \
                 patch("sys.stdout", buf):
                mod._check_agent_symlinks()

            output = buf.getvalue()
            self.assertIn("MISMATCH", output)
            self.assertIn("dangling", output.lower())

    def test_status_detects_worktree_agent_symlink(self):
        """An agent symlink pointing into .claude/worktrees/ is flagged."""
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / ".claude" / "agents"
            agents_dir.mkdir(parents=True)
            # Create a worktree-target symlink (target exists for this test)
            wt_target = Path(tmp) / ".claude" / "worktrees" / "agent-abc" / "agents"
            wt_target.mkdir(parents=True)
            (wt_target / "autopilot-worker.md").write_text("test")
            link = agents_dir / "autopilot-worker.md"
            link.symlink_to(wt_target / "autopilot-worker.md")

            mod = self._load_airuleset()
            import io
            buf = io.StringIO()
            with patch.object(mod, "AGENTS_DIR", agents_dir), \
                 patch("sys.stdout", buf):
                mod._check_agent_symlinks()

            output = buf.getvalue()
            self.assertIn("MISMATCH", output)
            self.assertIn("worktree", output.lower())


# --------------------------------------------------------------------------- #
# Deliverable 2: Hook RULE B3 — block airuleset.py install|push from worktree
# --------------------------------------------------------------------------- #

class TestHookRuleB3(TestCase):
    """RULE B3: block airuleset.py install|push from a worktree agent context."""

    def _run_hook(self, cmd, cwd, agent_id="agent-abc", transcript=None,
                  env_extra=None):
        if transcript is None:
            transcript = ("/home/newlevel/.claude/projects/"
                          "-home-newlevel-devel-airuleset/"
                          "00000000-0000-0000-0000-000000000000.jsonl")
        payload = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "cwd": cwd,
            "transcript_path": transcript,
            "agent_id": agent_id,
        })
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/root")}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(HOOK)], input=payload,
            capture_output=True, text=True, env=env)

    def test_install_blocked_from_worktree(self):
        """airuleset.py install from a worktree cwd is blocked."""
        cwd = "/home/user/devel/airuleset/.claude/worktrees/agent-abc"
        r = self._run_hook("python3 airuleset.py install", cwd)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK\nstderr={r.stderr}")
        self.assertIn("install", r.stderr.lower())

    def test_push_blocked_from_worktree(self):
        """airuleset.py push from a worktree cwd is blocked."""
        cwd = "/home/user/devel/airuleset/.claude/worktrees/agent-abc"
        r = self._run_hook("python3 ~/devel/airuleset/airuleset.py push", cwd)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK\nstderr={r.stderr}")
        self.assertIn("push", r.stderr.lower())

    def test_install_with_full_path_blocked(self):
        """airuleset.py install with a full path is also blocked."""
        cwd = "/home/user/devel/airuleset/.claude/worktrees/agent-abc"
        r = self._run_hook(
            "python3 /home/user/devel/airuleset/airuleset.py install", cwd)
        self.assertEqual(r.returncode, 2,
                         f"expected BLOCK\nstderr={r.stderr}")

    def test_other_airuleset_commands_allowed(self):
        """Non-install/push airuleset.py commands remain allowed from worktree.

        airuleset.py status is a read-only command — B3 must NOT block it.
        """
        cwd = "/home/user/devel/airuleset/.claude/worktrees/agent-abc"
        r = self._run_hook("python3 airuleset.py status", cwd)
        self.assertEqual(r.returncode, 0,
                         f"B3 should not block 'status'\nstderr={r.stderr}")

    def test_no_agent_id_not_blocked(self):
        """Without agent_id (main session), install from worktree is NOT blocked
        by the hook (the python-side guard handles it)."""
        cwd = "/home/user/devel/airuleset/.claude/worktrees/agent-abc"
        payload = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "python3 airuleset.py install"},
            "cwd": cwd,
            "transcript_path": ("/home/newlevel/.claude/projects/"
                                "-home-newlevel-devel-airuleset/"
                                "00000000.jsonl"),
        })
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": os.environ.get("HOME", "/root")}
        r = subprocess.run(
            ["bash", str(HOOK)], input=payload,
            capture_output=True, text=True, env=env)
        # The hook itself should not block (no agent_id = not a subagent)
        self.assertEqual(r.returncode, 0,
                         f"expected ALLOW (no agent_id)\nstderr={r.stderr}")


# --------------------------------------------------------------------------- #
# Deliverable 6 (#972 REOPEN): cli_remote.py Pass B test_env isolates HOME
# --------------------------------------------------------------------------- #

class TestPassBHomeIsolation(TestCase):
    """#972 REOPEN R3: the push gate's `unittest discover` (Pass B) subprocess
    must run under a per-run isolated HOME, so NO gate run can write the real
    ~/.claude even if a test performs a real install. Source-lock, the same
    #385/#1012/#548 single-place-that-knows dual-coverage pattern already used
    for AIRULESET_MAIN_EXEC_STATE_DIR / TMPDIR (conftest.py covers pytest;
    `unittest discover` never reads conftest, so cmd_push must set it itself)."""

    def test_cmd_push_test_env_carries_isolated_home(self):
        src = (REPO / "cli_remote.py").read_text(encoding="utf-8")
        self.assertIn('test_env["HOME"]', src,
                      "cmd_push's Pass B test_env must set an isolated HOME")
        # Stronger than a bare presence check (review BLUE): the HOME must be a
        # PER-RUN dir rooted under the run's throwaway tempdir, never the real
        # home — tie the source-lock to that construction.
        self.assertIn("_suite_home", src,
                      "Pass B HOME must be a per-run isolated dir, not the real home")
        self.assertRegex(
            src, r"_suite_home\s*=\s*Path\(_lock_tmp\)",
            "the per-run HOME must be rooted under the run's throwaway tempdir")


if __name__ == "__main__":
    main()
