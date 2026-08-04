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
        # #206 -- the hook now checks each still-unmarked ref's OPEN/CLOSED
        # state via `gh` before requiring a marker for it. Stub `gh` on a
        # dedicated PATH dir: default every issue to OPEN (== the pre-#206
        # unconditional-required behaviour), so every pre-existing test in
        # this file stays valid completely unchanged. A test that needs a
        # CLOSED issue calls closed_issues(...).
        self.bindir = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-bin-"))
        self.addCleanup(shutil.rmtree, self.bindir, True)
        # A `gh` that always FAILS (simulates network-down / auth-broken --
        # `gh` itself is present so nothing else on PATH resolution breaks,
        # it just can never answer). Shadows any real `gh` further down PATH.
        self.no_gh_dir = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-nogh-"))
        self.addCleanup(shutil.rmtree, self.no_gh_dir, True)
        broken_gh = self.no_gh_dir / "gh"
        broken_gh.write_text("#!/usr/bin/env bash\nexit 1\n")
        broken_gh.chmod(0o755)
        self._closed = set()
        self._write_fake_gh()

    def _write_fake_gh(self):
        fake_gh = self.bindir / "gh"
        closed = " ".join(sorted(self._closed))
        fake_gh.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = issue ] && [ "$2" = view ]; then\n'
            '  n="$3"\n'
            '  for c in %s; do [ "$c" = "$n" ] && echo CLOSED && exit 0; done\n'
            '  echo OPEN\n'
            '  exit 0\n'
            'fi\n'
            'exit 1\n' % (closed if closed else '""'))
        fake_gh.chmod(0o755)

    def closed_issues(self, *nums):
        self._closed |= {str(n) for n in nums}
        self._write_fake_gh()

    def mark(self, issue, repo="airuleset"):
        os.environ["HOME"] = str(self.home)
        dg.write_marker(repo, issue, "https://x/issues/%s#issuecomment-1" % issue)

    def run_hook(self, command, agent_type="autopilot-worker", agent_id="aW1",
                cwd=None, sid="commitgate-sess", gh_on_path=True):
        payload = {"tool_input": {"command": command}, "session_id": sid,
                   "cwd": str(cwd if cwd is not None else self.repo)}
        if agent_type is not None:
            payload["agent_type"] = agent_type
        if agent_id is not None:
            payload["agent_id"] = agent_id
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        # gh_on_path=False simulates `gh` genuinely failing (always errors) --
        # the fail-toward-block (unmeasurable -> still required) case. Either
        # way the stub dir is PREPENDED, never replaces PATH -- the hook's
        # own bash/python3/jq/sed/grep must still resolve normally.
        stub = self.bindir if gh_on_path else self.no_gh_dir
        env["PATH"] = str(stub) + os.pathsep + env.get("PATH", "")
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


class TestClosedIssueReferencesAreExempt(_Base):
    """#206 -- a `#N` reference to an issue that is already CLOSED on GitHub
    no longer requires a design marker: it's overwhelmingly likely to be a
    historical/context reference in the commit's own prose (the reported
    shape: "(owner decisions #1734/#1766)", both long-closed), not the
    ticket this commit is actually for."""

    def test_closed_issue_reference_is_exempt_from_marker_requirement(self):
        self.closed_issues(1734)
        r = self.run_hook(
            'git commit -m "fix: dedup pass (owner decisions #1734)"')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_still_open_issue_reference_still_requires_marker(self):
        # the default stub reports every issue OPEN unless closed_issues()
        # says otherwise -- confirms the exemption is CLOSED-specific, not a
        # blanket "gh answered something" pass.
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("41", r.stderr)

    def test_mixed_refs_only_the_still_open_one_is_required(self):
        self.closed_issues(1734, 1766)
        r = self.run_hook(
            'git commit -m "fix: thing (#42) (owner decisions #1734/#1766)"')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("42", r.stderr)
        self.assertNotIn("1734", r.stderr)
        self.assertNotIn("1766", r.stderr)

    def test_gh_failure_falls_back_to_still_required(self):
        # unmeasurable (gh missing/erroring) -> never guess an issue is safe
        # to skip -> the pre-#206 behaviour (still required, still blocks).
        r = self.run_hook(COMMIT_41, gh_on_path=False)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("41", r.stderr)

    def test_closed_ref_that_is_already_marked_still_passes(self):
        # a marked AND closed issue -- the marker check alone already passes
        # it; confirms the new gh-state filter never turns a marked ref back
        # into a requirement.
        self.mark(41)
        self.closed_issues(41)
        r = self.run_hook(COMMIT_41)
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
