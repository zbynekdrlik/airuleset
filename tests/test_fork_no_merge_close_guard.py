"""Behaviour test for hooks/block-fork-no-merge-issue-close.sh.

Incident 2026-07-10 + gatekeeper refinement 2026-07-11 (david@gk / odoo-erp):
a fork-no-merge stream must NEVER close ASSIGNED / foreign-authored tickets (the
gatekeeper maintainer closes them at cross-fork review/merge) — but closing its OWN
self-authored sub-findings with evidence is normal bookkeeping and MUST be allowed
(the original blanket block was a false positive on David's legit workflow). The
hook verifies issue author == the stream's authenticated gh login; undeterminable
(gh error / no auth) fails SAFE (block).

#349 (2026-08-09, montalu3 regression): the guard used to exempt `branch-merge`
on the false assumption its PR "legitimately closes issues via a merged PR's
`Closes #N`" — a branch-merge PR merges into the project's INTEGRATION branch,
never the repo's actual DEFAULT branch, so GitHub's auto-close never fires there
either. The guard now gates ANY reduced-authority stream (authority != `full`) —
only `full` authority passes untouched, resolved via `airuleset.py authority`
(marker-aware).

Tests are hermetic: a fake `gh` is PATH-injected so no network/auth is needed —
FAKE_GH_ME controls `gh api user`, FAKE_GH_AUTHOR controls `gh issue view --json
author`, FAKE_GH_FAIL=1 makes every gh call fail (the fail-safe path).
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"

_FAKE_GH = """#!/usr/bin/env bash
# Hermetic gh stand-in for the close-guard tests.
[ "${FAKE_GH_FAIL:-0}" = "1" ] && exit 1
case "$1 $2" in
  "api user")
    # An App installation token 403s structurally on /user (#463) — model it.
    [ "${FAKE_GH_API_USER_403:-0}" = "1" ] && \
      { echo "gh: Resource not accessible by integration (HTTP 403)" >&2; exit 1; }
    echo "${FAKE_GH_ME:-}";;
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


def run(cmd, cwd, hook=None, me="", author="", gh_fail=False,
        app_token_dir=None, api_user_403=False):
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    env["PATH"] = _fake_gh_dir() + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_ME"] = me
    env["FAKE_GH_AUTHOR"] = author
    env["FAKE_GH_FAIL"] = "1" if gh_fail else "0"
    env["FAKE_GH_API_USER_403"] = "1" if api_user_403 else "0"
    if app_token_dir is not None:
        # An existing dir here makes cli_quals._is_gh_app_token_box() true, so
        # `authority --self-login` returns STREAM_APP_BOT_LOGIN with no gh call
        # (the #463 App-token identity path).
        env["GH_APP_TOKEN_DIR"] = app_token_dir
    else:
        env.pop("GH_APP_TOKEN_DIR", None)
    return subprocess.run(["bash", str(hook or HOOK)], input=payload,
                          capture_output=True, text=True, cwd=cwd, env=env)


def _app_token_dir():
    return tempfile.mkdtemp()


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

    # --- #349: branch-merge is gated identically to fork-no-merge ---

    def test_blocks_close_of_foreign_authored_issue_under_branch_merge(self):
        # Was `test_allows_gh_issue_close_under_branch_merge` — INVERTED (#349):
        # the pre-fix behaviour this asserted (unconditional allow) was the
        # montalu3 regression itself, not a legitimate exemption.
        r = run("gh issue close 5 --comment obsolete", self.branch,
                me="kvaskodev", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)
        self.assertIn("READY-FOR-REVIEW", r.stderr)
        self.assertIn("process-subdev", r.stderr)
        self.assertNotIn("fork-no-merge", r.stderr)

    def test_allows_close_of_self_authored_issue_under_branch_merge(self):
        # Mirrors test_allows_close_of_self_authored_issue — the same bookkeeping
        # exception must apply identically under branch-merge.
        r = run("gh issue close 1408 --comment 'fixed, tests green'",
                self.branch, me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_close_in_a_compound_command_under_branch_merge(self):
        r = run("cd sub && gh issue close 1400", self.branch,
                me="kvaskodev", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- #349 adversarial-review CRITICAL: shared-gh-identity streams ---

    def test_blocks_close_when_stream_shares_identity_with_the_maintainer(self):
        # Every REAL branch-merge stream (marek, montalu, montalu2/3/4)
        # authenticates as the SAME shared gh identity as the repo's
        # maintainer, so ME == AUTHOR is ALWAYS true (the maintainer files
        # virtually every ticket) -- the naive exemption let a verbatim
        # replay of the montalu3 incident close cleanly. The exemption must
        # be refused when ME equals the maintainer's own login, even though
        # ME == AUTHOR still holds.
        r = run("gh issue close 3312 --comment done", self.branch,
                me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_when_fork_no_merge_shares_identity_with_the_maintainer(self):
        # The SAME shared-identity refusal must apply under fork-no-merge too,
        # even though no currently-registered fork-no-merge stream shares an
        # identity with the maintainer -- the guard has no way to know that
        # in general, so it must hold regardless of profile.
        r = run("gh issue close 1408 --comment done", self.fork,
                me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_close_when_a_genuinely_separate_identity_matches_the_author(self):
        # The exemption must still work for a genuinely separate identity
        # (kvaskodev != the real MAINTAINER_GH_LOGIN) -- confirms the fix
        # narrows the exemption rather than deleting it.
        self.assertNotEqual("kvaskodev", airuleset.MAINTAINER_GH_LOGIN)
        r = run("gh issue close 1408 --comment 'fixed, tests green'",
                self.branch, me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- #463: App-token stream (`gh api user` 403s) self-close carve-out ---
    #
    # Every montalu-family subdev stream migrated to a GitHub App installation
    # token (odoo-erp #3284). `gh api user` 403s structurally there, so the
    # identity read must come from `authority --self-login` (the fixed stream
    # bot login STREAM_APP_BOT_LOGIN), not a raw `gh api user`. The App identity
    # is DISTINCT from the maintainer, so it restores the self-vs-assigned
    # distinguishability the pre-App shared-PAT setup destroyed.
    #
    # `api_user_403=True` below models the REAL App-box environment (its `gh api
    # user` genuinely 403s) — but note the App path SHORT-CIRCUITS on
    # `_is_gh_app_token_box()` (driven here by `app_token_dir`) and never calls
    # `gh api user` at all, so the 403 model is a realistic-environment guard,
    # not the thing under test: these tests prove identity resolution works
    # WITHOUT any `/user` call (#463 adversarial review MINOR-1).

    def test_allows_self_authored_close_on_app_token_box(self):
        # The odoo-erp #4006 live case: an App-authored, stream-filed sub-finding
        # on an App-token box. `gh api user` 403s, but the ticket's author IS the
        # stream's own bot identity -> self-close ALLOWED.
        self.assertNotEqual(airuleset.STREAM_APP_BOT_LOGIN,
                            airuleset.MAINTAINER_GH_LOGIN)
        r = run("gh issue close 4006 --comment 'docs-only fix, merged to develop'",
                self.branch, api_user_403=True,
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir=_app_token_dir())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_maintainer_authored_close_on_app_token_box(self):
        # A maintainer-ASSIGNED ticket on the same App-token box is authored by
        # the human maintainer (never the App), so author != the App identity ->
        # still BLOCKED. This is the review-requiring work the guard protects.
        r = run("gh issue close 3312 --comment done", self.branch,
                api_user_403=True, author=airuleset.MAINTAINER_GH_LOGIN,
                app_token_dir=_app_token_dir())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_foreign_bot_authored_close_on_app_token_box(self):
        # A ticket authored by a DIFFERENT bot (dependabot, a foreign App) must
        # NOT be self-closable -- the fix compares against the SPECIFIC stream
        # slug, never "any bot-authored ticket on an App-token box".
        self.assertNotEqual("app/dependabot", airuleset.STREAM_APP_BOT_LOGIN)
        r = run("gh issue close 4100 --comment done", self.fork,
                api_user_403=True, author="app/dependabot",
                app_token_dir=_app_token_dir())
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)

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
