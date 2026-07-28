"""Behaviour test for hooks/block-commit-without-design.sh (#136, half 2/3).

PreToolUse(Bash) on `git commit`. Active ONLY when the payload identifies an
`autopilot-worker` subagent (agent_type) -- a MAIN session, an ad-hoc human
session, or any other subagent type passes untouched, per the ticket's own
explicit requirement ("a hook that blocks ordinary human commits is a worse
bug than the one it fixes"). Blocks a commit that references an issue with
no delivered design marker for it yet. Bypass: `[no-design: <reason>]`,
logged to audits/no-design-skips.log -- mirrors pre-push-test-check.sh's
`[no-test: <reason>]` shape exactly (bare tag rejected, reasoned tag logged).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-commit-without-design.sh"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                   # noqa: E402


def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


class _Base(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/airuleset.git")

    def mark(self, issue, repo="airuleset"):
        os.environ["HOME"] = str(self.home)
        dg.write_marker(repo, issue, "https://x/issues/%s#issuecomment-1" % issue)

    def run_hook(self, command, agent_type="autopilot-worker", agent_id="aW1",
                cwd=None, sid="commitgate-sess"):
        payload = {"tool_input": {"command": command}, "session_id": sid,
                   "cwd": str(cwd if cwd is not None else self.repo)}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        if agent_id is not None:
            payload["agent_id"] = agent_id
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)


COMMIT_41 = 'git commit -m "fix(hook): thing (#41) [green]"'
COMMIT_HEREDOC_41 = ('git commit -m "$(cat <<\'EOF\'\n'
                     'fix(hook): thing (#41) [green]\nEOF\n)"')


class TestBlocksAnUnmarkedIssue(_Base):

    def test_worker_commit_referencing_unmarked_issue_is_blocked(self):
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("41", r.stderr)

    def test_heredoc_commit_message_form_is_also_detected(self):
        # the standard gh-cli-recipes.md / system-prompt commit recipe:
        # `git commit -m "$(cat <<'EOF' ... EOF )"`.
        r = self.run_hook(COMMIT_HEREDOC_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("41", r.stderr)

    def test_multiple_referenced_issues_all_must_be_marked(self):
        self.mark(41)
        r = self.run_hook('git commit -m "docs: entry for #41/#42"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("42", r.stderr)


class TestPassesWhenMarked(_Base):

    def test_a_marked_issue_passes(self):
        self.mark(41)
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_all_referenced_issues_marked_passes(self):
        self.mark(41)
        self.mark(42)
        r = self.run_hook('git commit -m "docs: entry for #41/#42"')
        self.assertEqual(r.returncode, 0, r.stderr)


class TestScopedToAutopilotWorkerOnly(_Base):

    def test_main_session_no_agent_type_is_never_gated(self):
        r = self.run_hook(COMMIT_41, agent_type=None, agent_id=None)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_other_subagent_types_are_never_gated(self):
        for t in ("general-purpose", "Explore", "ticket-validator"):
            r = self.run_hook(COMMIT_41, agent_type=t)
            self.assertEqual(r.returncode, 0, (t, r.stderr))


class TestStaysOutOfTheWayOtherwise(_Base):

    def test_no_issue_reference_is_never_gated(self):
        r = self.run_hook('git commit -m "chore: tidy imports"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unrelated_command_is_untouched(self):
        r = self.run_hook("git status && echo done")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_git_repo_cwd_is_unmeasurable_never_a_block(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        r = self.run_hook(COMMIT_41, cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_garbage_stdin_does_not_crash(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run(["bash", str(HOOK)], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_empty_stdin_does_not_crash(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        r = subprocess.run(["bash", str(HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestBypass(_Base):

    def test_bare_bypass_without_reason_is_rejected(self):
        r = self.run_hook(COMMIT_41 + " # [no-design]")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("reason", r.stderr.lower())

    def test_reasoned_bypass_passes_and_is_logged(self):
        r = self.run_hook(COMMIT_41 + " # [no-design: gh unreachable, filed as follow-up]")
        self.assertEqual(r.returncode, 0, r.stderr)
        # same fixed-path convention as pre-push-test-check.sh's own
        # AUDIT_LOG ($HOME/devel/airuleset/audits/...) -- HOME is overridden
        # for the test, so the log lands under the fake home.
        log = self.home / "devel" / "airuleset" / "audits" / "no-design-skips.log"
        self.assertTrue(log.exists(), "expected %s to exist" % log)
        self.assertIn("gh unreachable", log.read_text())


if __name__ == "__main__":
    main()
