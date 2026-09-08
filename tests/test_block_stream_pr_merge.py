"""Behaviour test for hooks/block-stream-direct-pr-merge.sh.

Issue #945: sub-dev streams must merge via `scripts/stream_merge_guard.py`,
never a bare `gh pr merge`. The hook gates on `airuleset.resolve_authority(cwd)`
— only reduced-authority boxes are blocked; full-authority boxes (gk/dev1) pass.

Tests are hermetic: authority is controlled by patching
`cli_quals.resolve_authority` (the same function `airuleset.resolve_authority`
delegates to). The hook reads stdin (`.tool_input.command`) per the CC contract.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-stream-direct-pr-merge.sh"


def _run_hook(command, authority="fork-no-merge"):
    """Run the hook with a given command and mocked authority.

    Returns (returncode, stderr_text).
    """
    payload = json.dumps({"tool_input": {"command": command}})

    # Patch resolve_authority at the module level so the inline python
    # inside the hook picks it up when it imports airuleset.
    # We achieve this by setting an env var the hook's inline python reads.
    env = dict(os.environ)
    env["_TEST_AUTHORITY_OVERRIDE"] = authority if authority else ""

    # We need to make the hook's python see our override. The cleanest way
    # is to write a tiny wrapper that patches resolve_authority before the
    # hook's inline python runs. But since the hook runs python3 inline,
    # we can inject via PYTHONSTARTUP — but that's fragile.
    #
    # Instead: create a temporary airuleset.py shim that the hook will
    # find via its REPO_ROOT_DIR. But that's complex.
    #
    # Simplest approach: the hook resolves authority via sys.path +
    # `import airuleset`. We can control this by putting a shim on the path
    # that overrides resolve_authority.
    #
    # Actually, the cleanest hermetic approach is to make a fake repo dir
    # with a minimal airuleset.py that returns the controlled authority.

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a minimal airuleset.py shim
        shim = Path(tmpdir) / "airuleset.py"
        shim.write_text(textwrap.dedent(f"""\
            def resolve_authority(cwd=None):
                return {repr(authority)}
        """))

        # Run the hook, pointing REPO_ROOT_DIR at our shim dir by
        # manipulating the hook's HOOK_DIR resolution. The hook does:
        #   HOOK_DIR=$(dirname BASH_SOURCE[0])
        #   REPO_ROOT_DIR=$(dirname HOOK_DIR)
        # So if we run the hook from its real path, REPO_ROOT_DIR will be
        # the real airuleset root. We need to override this.
        #
        # The hook passes REPO_ROOT_DIR as argv[3] to python. We can't
        # easily change that without modifying the hook. But we CAN create
        # a symlink structure: tmpdir/hooks/hook.sh -> real hook, so
        # dirname(dirname(hook)) = tmpdir which has our shim.
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
    """The inline bypass marker allows the command."""

    def test_bypass_marker(self):
        rc, _ = _run_hook(
            "gh pr merge 42 # airuleset:stream-merge-ok manual override",
            authority="fork-no-merge",
        )
        self.assertEqual(rc, 0)


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


class TestEnvPrefixStripped(TestCase):
    """Environment assignments and sudo/env prefixes are stripped."""

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
