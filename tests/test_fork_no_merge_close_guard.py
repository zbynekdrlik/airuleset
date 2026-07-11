"""Behaviour test for hooks/block-fork-no-merge-issue-close.sh.

Incident 2026-07-10 + gatekeeper refinement 2026-07-11 (david@gk / odoo-erp):
a fork-no-merge stream must NEVER close ASSIGNED / foreign-authored tickets (the
gatekeeper maintainer closes them at cross-fork review/merge) — but closing its OWN
self-authored sub-findings with evidence is normal bookkeeping and MUST be allowed
(the original blanket block was a false positive on David's legit workflow). The
hook verifies issue author == the stream's authenticated gh login; undeterminable
(gh error / no auth) fails SAFE (block). full / branch-merge streams pass untouched,
resolved via `airuleset.py authority` (marker-aware).

Tests are hermetic: a fake `gh` is PATH-injected so no network/auth is needed —
FAKE_GH_ME controls `gh api user`, FAKE_GH_AUTHOR controls `gh issue view --json
author`, FAKE_GH_FAIL=1 makes every gh call fail (the fail-safe path).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"

_FAKE_GH = """#!/usr/bin/env bash
# Hermetic gh stand-in for the close-guard tests.
[ "${FAKE_GH_FAIL:-0}" = "1" ] && exit 1
case "$1 $2" in
  "api user")   echo "${FAKE_GH_ME:-}";;
  "issue view") echo "${FAKE_GH_AUTHOR:-}";;
  *) exit 1;;
esac
"""


def _cwd_with_authority(profile):
    d = tempfile.mkdtemp()
    (Path(d) / "CLAUDE.md").write_text(
        f"# proj\n<!-- airuleset:authority={profile} -->\n")
    return d


def _fake_gh_dir():
    d = tempfile.mkdtemp()
    gh = Path(d) / "gh"
    gh.write_text(_FAKE_GH)
    gh.chmod(0o755)
    return d


def run(cmd, cwd, hook=None, me="", author="", gh_fail=False):
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    env["PATH"] = _fake_gh_dir() + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_ME"] = me
    env["FAKE_GH_AUTHOR"] = author
    env["FAKE_GH_FAIL"] = "1" if gh_fail else "0"
    return subprocess.run(["bash", str(hook or HOOK)], input=payload,
                          capture_output=True, text=True, cwd=cwd, env=env)


class TestForkNoMergeCloseGuard(TestCase):
    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.full = _cwd_with_authority("full")
        self.branch = _cwd_with_authority("branch-merge")

    # --- foreign-authored / undeterminable: BLOCK ---

    def test_blocks_close_of_foreign_authored_issue(self):
        r = run("gh issue close 1393 --comment done", self.fork,
                me="kvaskodev", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)
        self.assertIn("READY-FOR-REVIEW", r.stderr)

    def test_blocks_close_when_gh_fails(self):
        # fail-safe: author can't be verified -> block, never silently allow
        r = run("gh issue close 1408", self.fork, gh_fail=True)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_the_api_patch_close_form_even_for_self(self):
        # the REST form is never exempted — legit self-closes use `gh issue close`
        r = run("gh api -X PATCH repos/o/n/issues/1408 -f state=closed", self.fork,
                me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_in_a_compound_command(self):
        r = run("cd sub && gh issue close 1400", self.fork,
                me="kvaskodev", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- self-authored: ALLOW (gatekeeper refinement 2026-07-11) ---

    def test_allows_close_of_self_authored_issue(self):
        # David closing his OWN kiosk sub-finding with evidence = normal bookkeeping.
        r = run("gh issue close 1408 --comment 'fixed on fork branch, tests green'",
                self.fork, me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_self_authored_close_with_repo_flag(self):
        r = run("gh issue close 1408 -R kvaskodev/odoo-erp --comment done",
                self.fork, me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- non-close commands + other authorities: untouched ---

    def test_allows_api_read_predicate_that_mentions_closed(self):
        r = run('gh api repos/o/n/issues/1408 --jq \'.state=="closed"\'', self.fork)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_issue_comment_under_fork_no_merge(self):
        r = run('gh issue comment 1408 --body "READY-FOR-REVIEW: br — tests green"',
                self.fork)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_issue_close_under_full_authority(self):
        r = run("gh issue close 5 --comment obsolete", self.full)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_issue_close_under_branch_merge(self):
        r = run("gh issue close 5 --comment obsolete", self.branch)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_unrelated_commands_under_fork_no_merge(self):
        for cmd in ("git status", "gh issue list --state open",
                    "gh issue view 5 --json title", "gh pr list"):
            r = run(cmd, self.fork)
            self.assertEqual(r.returncode, 0, f"{cmd}\n{r.stderr}")

    def test_fails_safe_when_authority_unresolvable(self):
        # If authority can't be resolved (airuleset.py missing/broken) the guard must
        # NOT silently allow the close. Copy the hook into a temp tree with NO
        # airuleset.py at its REPO_DIR → the authority call errors → BLOCK.
        import shutil
        d = tempfile.mkdtemp()
        (Path(d) / "hooks").mkdir()
        fake = Path(d) / "hooks" / "block-fork-no-merge-issue-close.sh"
        shutil.copy(str(HOOK), str(fake))
        r = run("gh issue close 1408", self.full, hook=fake)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-safe", r.stderr)


if __name__ == "__main__":
    main()
