"""Behaviour test for hooks/block-stream-direct-pr-merge.sh.

Issue #945: sub-dev streams must merge via `scripts/stream_merge_guard.py`,
never a bare `gh pr merge`. The hook gates on `airuleset.resolve_authority(cwd)`
— only reduced-authority boxes are blocked; full-authority boxes (gk/dev1) pass.

Tests are hermetic: authority is controlled via a symlinked hook dir structure.
The hook resolves REPO_ROOT_DIR as dirname(dirname(BASH_SOURCE[0])); by
symlinking tmpdir/hooks/<hook> -> real hook, REPO_ROOT_DIR = tmpdir, where a
shim airuleset.py returns the controlled authority (sys.path.insert(0, tmpdir)
ahead of cwd). The hook reads stdin (.tool_input.command) per the CC contract.
"""

import json
import os
import subprocess
import textwrap
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-stream-direct-pr-merge.sh"


def _run_hook(command, authority="fork-no-merge"):
    """Run the hook with a given command and mocked authority.

    Returns (returncode, stderr_text).
    """
    payload = json.dumps({"tool_input": {"command": command}})
    env = dict(os.environ)

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a minimal airuleset.py shim that returns the controlled authority.
        shim = Path(tmpdir) / "airuleset.py"
        shim.write_text(textwrap.dedent(f"""\
            def resolve_authority(cwd=None):
                return {repr(authority)}
        """))

        # Symlink tmpdir/hooks/<hook> -> real hook so REPO_ROOT_DIR = tmpdir.
        hooks_dir = Path(tmpdir) / "hooks"
        hooks_dir.mkdir()
        hook_link = hooks_dir / "block-stream-direct-pr-merge.sh"
        hook_link.symlink_to(HOOK)

        result = subprocess.run(
            ["bash", str(hook_link)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            env=env,
            timeout=10,
        )

    return result.returncode, result.stderr


class TestReducedAuthorityBlocked(TestCase):
    """A reduced-authority box is BLOCKED on bare `gh pr merge`."""

    def test_bare_gh_pr_merge_blocked(self):
        rc, stderr = _run_hook("gh pr merge 42", authority="fork-no-merge")
        self.assertEqual(rc, 2, f"Expected block (exit 2), got {rc}. stderr: {stderr}")
        self.assertIn("stream_merge_guard", stderr)

    def test_gh_pr_merge_with_flags_blocked(self):
        rc, stderr = _run_hook(
            "gh pr merge 42 --squash --delete-branch",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)

    def test_gh_pr_merge_with_repo_flag_blocked(self):
        rc, stderr = _run_hook(
            "gh pr merge 42 -R zbynekdrlik/odoo-erp",
            authority="branch-merge",
        )
        self.assertEqual(rc, 2)

    def test_compound_command_blocked(self):
        rc, stderr = _run_hook(
            "echo 'merging' && gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)


class TestFullAuthorityAllowed(TestCase):
    """Full-authority boxes pass through unaffected."""

    def test_full_authority_bare_merge(self):
        rc, _ = _run_hook("gh pr merge 42", authority="full")
        self.assertEqual(rc, 0)

    def test_full_authority_with_flags(self):
        rc, _ = _run_hook(
            "gh pr merge 42 --squash -R zbynekdrlik/odoo-erp",
            authority="full",
        )
        self.assertEqual(rc, 0)


class TestAuthorityResolutionFailure(TestCase):
    """Unresolvable authority degrades to allow (fail-open)."""

    def test_none_authority_allowed(self):
        rc, _ = _run_hook("gh pr merge 42", authority=None)
        self.assertEqual(rc, 0)

    def test_import_failure_allowed(self):
        """When the airuleset.py shim cannot be imported (no shim at all),
        the hook's except clause returns None -> exit 0 (fail-open)."""
        payload = json.dumps({"tool_input": {"command": "gh pr merge 42"}})
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # NO airuleset.py shim — import will fail.
            hooks_dir = Path(tmpdir) / "hooks"
            hooks_dir.mkdir()
            hook_link = hooks_dir / "block-stream-direct-pr-merge.sh"
            hook_link.symlink_to(HOOK)

            result = subprocess.run(
                ["bash", str(hook_link)],
                input=payload,
                capture_output=True,
                text=True,
                cwd=str(tmpdir),  # cwd with no airuleset.py either
                timeout=10,
            )
        self.assertEqual(result.returncode, 0)


class TestGuardedMergeAllowed(TestCase):
    """The sanctioned path via stream_merge_guard.py is allowed."""

    def test_guard_script_not_blocked(self):
        """When the model runs the guard script, the command has no
        `gh pr merge` tokens — the hook's pre-filter exits 0."""
        rc, _ = _run_hook(
            "python3 scripts/stream_merge_guard.py --repo zbynekdrlik/odoo-erp --pr 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)

    def test_guard_script_full_path(self):
        rc, _ = _run_hook(
            "python3 ~/devel/odoo-erp/scripts/stream_merge_guard.py --repo zbynekdrlik/odoo-erp --pr 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)


class TestBypass(TestCase):
    """The inline bypass marker allows the command (quote-aware, logged)."""

    def test_bypass_marker(self):
        rc, _ = _run_hook(
            "gh pr merge 42 # airuleset:stream-merge-ok manual override",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)

    def test_bypass_marker_inside_quotes_does_not_bypass(self):
        """F1 fix: a bypass marker MENTIONED inside a quoted string must NOT
        disarm the guard for a real `gh pr merge` elsewhere on the line."""
        rc, stderr = _run_hook(
            'echo "# airuleset:stream-merge-ok" && gh pr merge 42',
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2, f"Quoted bypass marker should not disarm. stderr: {stderr}")


class TestIrrelevantCommands(TestCase):
    """Commands that don't contain `gh pr merge` are ignored."""

    def test_git_push(self):
        rc, _ = _run_hook("git push origin dev", authority="fork-no-merge")
        self.assertEqual(rc, 0)

    def test_gh_issue_close(self):
        rc, _ = _run_hook("gh issue close 42", authority="fork-no-merge")
        self.assertEqual(rc, 0)

    def test_gh_pr_view(self):
        rc, _ = _run_hook("gh pr view 42", authority="fork-no-merge")
        self.assertEqual(rc, 0)

    def test_empty_command(self):
        rc, _ = _run_hook("", authority="fork-no-merge")
        self.assertEqual(rc, 0)

    def test_merge_in_quoted_string(self):
        """A `gh pr merge` inside a quoted echo should NOT be blocked."""
        rc, _ = _run_hook(
            "echo 'use gh pr merge to merge PRs'",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)


class TestQuotedMergeNotBlocked(TestCase):
    """Ensure `gh pr merge` inside quotes is not treated as a real command."""

    def test_in_single_quotes(self):
        rc, _ = _run_hook(
            "echo 'you should run gh pr merge 42'",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)

    def test_in_double_quotes(self):
        rc, _ = _run_hook(
            'echo "run gh pr merge 42 next"',
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)


class TestHeredocNotBlocked(TestCase):
    """F2 fix: heredoc bodies mentioning `gh pr merge` must not false-block."""

    def test_cat_heredoc_allowed(self):
        """A cat heredoc with gh pr merge in the body is data, not a command."""
        rc, _ = _run_hook(
            "cat > /tmp/body.md <<'EOF'\ngh pr merge 42\nEOF",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)

    def test_gh_issue_comment_heredoc_allowed(self):
        """A gh issue comment with a heredoc body mentioning merge is data."""
        rc, _ = _run_hook(
            'gh issue comment 5 --body "$(cat <<\'EOF\'\nuse gh pr merge 42\nEOF\n)"',
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)


class TestEnvPrefixStripped(TestCase):
    """Environment assignments and wrapper prefixes are stripped."""

    def test_env_prefix(self):
        rc, _ = _run_hook(
            "GH_TOKEN=xxx gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)

    def test_sudo_prefix(self):
        rc, _ = _run_hook(
            "sudo gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)

    def test_command_prefix(self):
        """F3 fix: `command gh pr merge` is detected."""
        rc, _ = _run_hook(
            "command gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)

    def test_nohup_prefix(self):
        """F3 fix: `nohup gh pr merge` is detected."""
        rc, _ = _run_hook(
            "nohup gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)

    def test_time_prefix(self):
        """F3 fix: `time gh pr merge` is detected."""
        rc, _ = _run_hook(
            "time gh pr merge 42",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 2)


class TestAdminStillBlockedBySibling(TestCase):
    """Verify `gh pr merge --admin` is caught — by block-history-rewrite.sh,
    not this hook. This hook catches the bare merge; --admin is already covered.
    This test documents the division of responsibility."""

    def test_admin_passes_this_hook_on_full(self):
        """Full authority: this hook passes; block-history-rewrite catches --admin."""
        rc, _ = _run_hook("gh pr merge 42 --admin", authority="full")
        self.assertEqual(rc, 0)

    def test_admin_blocked_by_this_hook_on_reduced(self):
        """Reduced authority: this hook blocks the bare merge shape anyway
        (--admin is just an additional flag on a blocked command)."""
        rc, _ = _run_hook("gh pr merge 42 --admin", authority="fork-no-merge")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    main()
