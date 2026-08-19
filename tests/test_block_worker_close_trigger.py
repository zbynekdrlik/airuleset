"""Behaviour test for hooks/block-worker-close-trigger.sh + close_trigger.py
(#567 -- commit-time GitHub close-trigger ban for worker/worktree commits).

GitHub auto-closes an issue from a close-trigger (close/closes/closed |
fix/fixes/fixed | resolve/resolves/resolved , optional ':' , optional
whitespace , then '#N' or 'owner/repo#N') in a commit reachable on the default
branch. A worktree/autopilot WORKER must never emit one -- the supervisor closes
the ticket with evidence AFTER review (#152/#348); a worker's auto-close bypasses
that review. Live incident #564: `fix: #564` auto-closed #564 because the grammar
accepts the OPTIONAL COLON, a form the space-requiring post-hoc scan missed.

Two layers:
  * pure-python unit tests of close_trigger.py (grammar + extraction + context);
  * stdin-contract hook tests (payload on STDIN `.tool_input.command`/`.cwd`/
    `.agent_type`, exit 2 + reason on STDERR), incl. the ticket's mandated
    negative cases (`review (#12)`, `fix(review) (#12)`, prose `the fix for
    (#12)`) AND the same trigger text OUTSIDE a worker context (MAIN session).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-worker-close-trigger.sh"

sys.path.insert(0, str(ROOT))
import close_trigger as ct                                   # noqa: E402

WORKTREE_CWD = "/some/repo/.claude/worktrees/agent-abc123"
MAIN_CWD = "/some/repo"


# --------------------------------------------------------------------------- #
# Layer 1 -- pure-python grammar / extraction / context
# --------------------------------------------------------------------------- #

class TestGrammar(TestCase):
    def _hit(self, text):
        return ct.find_close_trigger(text)

    def test_positives_all_match_github_grammar(self):
        for t in ("fix: #12", "Fixes:#12", "fix #12", "fix:#12", "FIXES #12",
                  "close #4", "closes #4", "closed #4", "Closed:#4",
                  "resolve: octocat/Hello-World#3", "resolves #9", "Resolved #1",
                  "fixes owner/repo#7"):
            self.assertIsNotNone(self._hit(t), "should match: %r" % t)

    def test_optional_colon_is_the_564_regression(self):
        # the exact #564 shape: colon + space form the old `\s+` scan missed.
        self.assertEqual(self._hit("fix: #564 review — glob hardening"), "fix: #564")

    def test_negatives_never_match(self):
        for t in ("review (#12): tighten", "fix(review) (#12): x",
                  "the fix for (#12)", "safe (#12) [green]", "see #12",
                  "refs #12", "docs: entry for #137/#139", "bump to 0.28.0",
                  # substring-of-a-word keywords GitHub does NOT close on:
                  "hotfix: #12", "prefix: #12", "suffix #12", "affix #12",
                  "closer #12", "closed-loop #12", "resolver #12",
                  "fixes-12.txt", "fix the parser"):
            self.assertIsNone(self._hit(t), "should NOT match: %r" % t)

    def test_keyword_and_ref_across_a_newline_still_match(self):
        # a keyword ending a line + '#N' starting the next -- GitHub collapses
        # whitespace, so this DOES close; fail-safe toward blocking.
        self.assertIsNotNone(self._hit("this is a fix\n#123 was the report"))


class TestContext(TestCase):
    def test_autopilot_worker_agent_type_is_a_worker(self):
        self.assertTrue(ct.is_worker_context(MAIN_CWD, "autopilot-worker"))

    def test_worktree_cwd_is_a_worker(self):
        self.assertTrue(ct.is_worker_context(WORKTREE_CWD, None))

    def test_main_session_is_not_a_worker(self):
        self.assertFalse(ct.is_worker_context(MAIN_CWD, None))
        self.assertFalse(ct.is_worker_context(MAIN_CWD, "general-purpose"))
        self.assertFalse(ct.is_worker_context("", None))


class TestExtraction(TestCase):
    def _scan(self, cmd, cwd=WORKTREE_CWD):
        return ct.scan_commit_command(cmd, cwd)

    def test_inline_dash_m(self):
        self.assertTrue(self._scan('git commit -m "fix: #12"'))
        self.assertFalse(self._scan('git commit -m "safe (#12)"'))

    def test_bundled_short_flag_dash_am(self):
        self.assertTrue(self._scan('git commit -am "closes #7"'))

    def test_multiple_dash_m_are_all_scanned(self):
        self.assertTrue(self._scan('git commit -m "safe subject" -m "body fixes #9"'))

    def test_inline_heredoc_substitution_message(self):
        cmd = 'git commit -m "$(cat <<\'EOF\'\nfix: #12 review\nEOF\n)"'
        self.assertEqual(self._scan(cmd), "fix: #12")

    def test_trailing_shell_comment_is_not_the_message(self):
        # `# fixes #99` is a shell comment (comments=True), not the commit text.
        self.assertFalse(self._scan('git commit -m "safe (#12)" # fixes #99'))

    def test_sibling_command_in_a_compound_is_not_scanned(self):
        self.assertFalse(
            self._scan('git commit -m "safe (#12)" && echo "this fixes #13"'))

    def test_git_dash_C_global_option(self):
        self.assertTrue(self._scan('git -C /x commit -m "resolve: o/r#3"'))

    def test_non_commit_git_subcommand_is_ignored(self):
        self.assertFalse(self._scan('git log --grep "fixes #5"'))

    def test_dash_F_file_on_disk_is_read(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-ct-"))
        self.addCleanup(_rmtree, d)
        (d / "msg.txt").write_text("fix: #564 review\n")
        self.assertEqual(ct.scan_commit_command("git commit -F msg.txt", str(d)),
                         "fix: #564")
        (d / "ok.txt").write_text("review (#564): x\n")
        self.assertIsNone(ct.scan_commit_command("git commit -F ok.txt", str(d)))

    def test_dash_F_dash_direct_heredoc(self):
        self.assertTrue(self._scan("git commit -F - <<'EOF'\nfixes #88\nEOF"))
        self.assertFalse(self._scan("git commit -F - <<'EOF'\nsafe (#88)\nEOF"))

    def test_cat_heredoc_file_in_same_command(self):
        self.assertTrue(
            self._scan("cat > m.txt <<'EOF'\nresolve: #90\nEOF\ngit commit -F m.txt"))

    def test_cd_tracked_relative_dash_F(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-ct-"))
        self.addCleanup(_rmtree, d)
        (d / "sub").mkdir()
        (d / "sub" / "n.txt").write_text("closed #4\n")
        self.assertEqual(
            ct.scan_commit_command("cd sub && git commit -F n.txt", str(d)),
            "closed #4")


# --------------------------------------------------------------------------- #
# Layer 2 -- the hook end-to-end (stdin JSON contract, exit 2 + STDERR reason)
# --------------------------------------------------------------------------- #

class _HookBase(TestCase):
    def run_hook(self, command, cwd=WORKTREE_CWD, agent_type=None,
                 sid="ct-sess"):
        payload = {"tool_input": {"command": command}, "session_id": sid,
                   "cwd": cwd}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True)


class TestHookBlocksWorkerCloseTriggers(_HookBase):
    def test_colon_form_in_worktree_is_blocked(self):
        r = self.run_hook('git commit -m "fix: #564 review"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#564", r.stderr)

    def test_no_space_colon_form_is_blocked(self):
        r = self.run_hook('git commit -m "Fixes:#12"')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_cross_repo_ref_is_blocked(self):
        r = self.run_hook('git commit -m "resolve: octocat/Hello-World#3"')
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_closed_in_dash_F_file_is_blocked(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-ct-hook-"))
        self.addCleanup(_rmtree, d)
        (d / "b.txt").write_text("closed #4 during the fix\n")
        r = self.run_hook("git commit -F b.txt", cwd=str(d),
                          agent_type="autopilot-worker")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_serial_fallback_worker_by_agent_type_is_blocked(self):
        # agent_type worker but cwd is the MAIN checkout (serial fallback).
        r = self.run_hook('git commit -m "fix: #7"', cwd=MAIN_CWD,
                          agent_type="autopilot-worker")
        self.assertEqual(r.returncode, 2, r.stderr)


class TestHookPassesSafeAndNonWorker(_HookBase):
    def test_parenthesised_ref_passes(self):
        self.assertEqual(self.run_hook('git commit -m "review (#12): x"').returncode, 0)

    def test_fix_paren_review_form_passes(self):
        self.assertEqual(
            self.run_hook('git commit -m "fix(review) (#12): tighten"').returncode, 0)

    def test_prose_the_fix_for_passes(self):
        self.assertEqual(
            self.run_hook('git commit -m "the fix for (#12) landed"').returncode, 0)

    def test_main_session_is_never_gated(self):
        # THE load-bearing carve-out: an ordinary/supervisor MAIN session's
        # deliberate `Closes #N` must stay possible.
        r = self.run_hook('git commit -m "resolve #12"', cwd=MAIN_CWD,
                          agent_type=None)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_general_purpose_subagent_in_main_checkout_passes(self):
        r = self.run_hook('git commit -m "fixes #12"', cwd=MAIN_CWD,
                          agent_type="general-purpose")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_commit_command_passes(self):
        self.assertEqual(self.run_hook('echo "fixes #12"').returncode, 0)

    def test_bypass_marker_outside_quotes_passes(self):
        r = self.run_hook(
            'git commit -m "fix: #12"  # airuleset:close-trigger-ok deliberate')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bypass_marker_inside_message_still_blocks(self):
        r = self.run_hook('git commit -m "fix: #12 airuleset:close-trigger-ok"')
        self.assertEqual(r.returncode, 2, r.stderr)


def _rmtree(p):
    import shutil
    shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    main()
