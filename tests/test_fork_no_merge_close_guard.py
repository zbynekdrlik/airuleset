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

#349 shared-identity refinement + #533 stream-label acceptance-close carve-out
(2026-08-18, montalu3 acceptance-doctrine): authorship is a DEAD ownership signal
on a shared-bot-identity box (every ticket a montalu stream files/works is authored
by the maintainer, so the author carve-out refuses even a genuine stream close).
The #533 additive exemption lets a REDUCED-authority stream CLOSE its OWN
`stream:<user>`-labeled `needs-acceptance` ticket WITH an evidence `--comment`
(the `gh issue close` form only; the `gh api PATCH` form stays blocked forever),
gated on the stream label (survives shared identity), the acceptance state (a
gatekeeper-applied post-pipeline label), and NONE of the #512 re-hand-off/bounce
override labels. Every failure fails toward hand-off (#349/#463 direction).

Tests are hermetic: a fake `gh` is PATH-injected so no network/auth is needed —
FAKE_GH_ME controls `gh api user`, FAKE_GH_AUTHOR controls `gh issue view --json
author`, FAKE_GH_LABELS (space-separated) controls `gh issue view --json labels
-q .labels[].name`, FAKE_GH_LABELS_FAIL=1 makes ONLY the labels read fail (models
an unreadable label set with a still-readable author), FAKE_GH_FAIL=1 makes every
gh call fail (the global fail-safe path).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset                                          # noqa: E402
import cli_quals                                          # noqa: E402

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
  "issue view")
    # #756: the verdict carve-out reads labels+comments in ONE `--json
    # labels,comments` call (raw JSON, no -q) — must be checked BEFORE the
    # `--json labels` substring branch (which it also matches). Emits a JSON
    # object the hook parses with jq: labels from FAKE_GH_LABELS (space-sep),
    # ONE comment whose body is FAKE_GH_COMMENTS. FAKE_GH_VERDICT_FAIL=1 makes
    # this read fail (models an unreadable ticket → fail-SAFE block).
    if printf '%s ' "$@" | grep -q -- '--json labels,comments'; then
      [ "${FAKE_GH_VERDICT_FAIL:-0}" = "1" ] && exit 1
      jq -n --arg labels "${FAKE_GH_LABELS:-}" --arg comments "${FAKE_GH_COMMENTS:-}" \
        '{labels: ($labels|split(" ")|map(select(length>0)|{name:.})),
          comments: (if ($comments|length)>0 then [{body:$comments}] else [] end)}'
    # #533: distinguish the labels read (`--json labels -q .labels[].name`) from
    # the author read (`--json author -q .author.login`). The labels read honors
    # FAKE_GH_LABELS_FAIL (an unreadable label set) and emits one name per line,
    # exactly what gh's own -q '.labels[].name' produces.
    elif printf '%s ' "$@" | grep -q -- '--json labels'; then
      [ "${FAKE_GH_LABELS_FAIL:-0}" = "1" ] && exit 1
      for lbl in ${FAKE_GH_LABELS:-}; do echo "$lbl"; done
    else
      echo "${FAKE_GH_AUTHOR:-}"
    fi;;
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
        app_token_dir=None, api_user_403=False, labels="", labels_fail=False,
        comments="", verdict_fail=False):
    # airuleset#839 dropped the `user=` param: `_current_user()` is now uid-based,
    # so LOGNAME/USER no longer set the subprocess's own identity (a stream can't
    # spoof another stream's `stream:<user>` ownership). Tests that need a
    # specific renamed identity assert the ownership SET in-process instead (see
    # TestStreamLabelAcceptanceClose).
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    env["PATH"] = _fake_gh_dir() + os.pathsep + env.get("PATH", "")
    env["FAKE_GH_ME"] = me
    env["FAKE_GH_AUTHOR"] = author
    env["FAKE_GH_FAIL"] = "1" if gh_fail else "0"
    env["FAKE_GH_API_USER_403"] = "1" if api_user_403 else "0"
    env["FAKE_GH_LABELS"] = labels
    env["FAKE_GH_LABELS_FAIL"] = "1" if labels_fail else "0"
    env["FAKE_GH_COMMENTS"] = comments
    env["FAKE_GH_VERDICT_FAIL"] = "1" if verdict_fail else "0"
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

    def test_blocks_self_authored_close_with_glued_repo_flag_on_app_token_box(self):
        # #807: the PRIMARY author carve-out (ME==AUTHOR) shares the glued-`-R`
        # cwd-repo residual the #773 ME-empty fallback already guards. On a
        # DETECTED App-token box `authority --self-login` RESOLVES ME to the
        # stream bot login (a LOCAL check, no `/user` call), so the ME-non-empty
        # author carve-out -- NOT the #773 fallback -- is the branch reached. A
        # glued `-Rzbynekdrlik/odoo-erp` (no separator) leaves REPO_ARG empty, so
        # AUTHOR is read from the CWD repo's issue while the close targets
        # odoo-erp. RED on pre-#807 code (the carve-out wrong-ALLOWS via a
        # cwd-repo author read); GREEN once `! _repo_flag_unparseable "$REPO_ARG"`
        # gates the carve-out -> fail toward hand-off (BLOCK), mirroring the #773
        # review-fix's own glued-`-R` test above.
        r = run("gh issue close 4006 -Rzbynekdrlik/odoo-erp --comment done",
                self.branch, api_user_403=True,
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir=_app_token_dir())
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_self_authored_close_with_quoted_glued_repo_flag_on_app_token_box(self):
        # #816 residual 1: a QUOTED glued `-R` — `'-Rowner/repo'` / `"-Rowner/repo"`
        # — puts a quote IMMEDIATELY before `-R`, so the shared #760
        # `_repo_flag_unparseable` boundary class `(^|[[:space:]])(-R|--repo)` (only
        # start/whitespace) MISSES it → the fail-safe returns FALSE ("not
        # unparseable") though REPO_ARG is empty (the `[[:space:]=]+` parser cannot
        # read a glued form) → the author carve-out reads AUTHOR from the CWD repo
        # while the close targets odoo-erp → wrong-ALLOW. RED on the pre-#816
        # boundary class (rc 0); GREEN once the class is widened to include the
        # quote/separator chars, mirroring `_CLOSE_OPEN` (#471/#540) → BLOCK. Both
        # single- and double-quote must block (the class carries both `'` and `"`).
        for cmd in ("gh issue close 4006 '-Rzbynekdrlik/odoo-erp' --comment done",
                    'gh issue close 4006 "-Rzbynekdrlik/odoo-erp" --comment done'):
            r = run(cmd, self.branch, api_user_403=True,
                    author=airuleset.STREAM_APP_BOT_LOGIN,
                    app_token_dir=_app_token_dir())
            self.assertEqual(r.returncode, 2, "%s\n%s" % (cmd, r.stderr))

    def test_blocks_self_authored_close_with_backslash_glued_repo_flag_on_app_token_box(self):
        # #816 review M2: a BACKSLASH-escaped glued flag `\-Rowner/repo`. bash strips
        # the `\`, so gh receives a valid glued `-Rowner/repo` and closes the NAMED
        # repo, while `$CMD` (the raw command text the hook scans) carries a literal
        # `\` immediately before `-R`. Neither the pre-#816 class nor the quote-ONLY
        # widening carries `\`, so `_repo_flag_unparseable` returns FALSE though
        # REPO_ARG is empty → the author carve-out reads AUTHOR from the CWD repo while
        # the close targets odoo-erp → the SAME wrong-ALLOW class as the quoted-glued
        # shape. RED (rc 0) on a class without `\`; GREEN (BLOCK) once the class carries
        # `\` — a DELIBERATE superset of `_CLOSE_OPEN`, whose own `\gh issue close`
        # blind spot is out of this helper's scope (follow-up #824). The Python `\\` is
        # ONE literal backslash; json.dumps preserves it end-to-end into `$CMD`.
        r = run("gh issue close 4006 \\-Rzbynekdrlik/odoo-erp --comment done",
                self.branch, api_user_403=True,
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir=_app_token_dir())
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- #773: bot box whose App-token dir is NOT detected -> self-login empty ---
    #
    # `app_token_dir` points at a NON-EXISTENT path here, so
    # `_is_gh_app_token_box()` is False and `authority --self-login` falls to
    # `_gh_login()` -> `gh api user`, which 403s on an App token (`api_user_403`)
    # -> ME EMPTY. That is the montalu2 #5560 failure: a bot-authored, own
    # stream-labeled ticket the stream tries to self-close, blocked because the
    # identity check could not resolve the box's own login.

    def test_allows_self_close_when_bot_box_selflogin_unresolvable(self):
        # RED (block) on current code; GREEN after the fallback: a ticket
        # authored by the shared stream App bot (!= maintainer) is stream-filed,
        # so a reduced-authority stream may self-close it without resolving ME.
        self.assertNotEqual(airuleset.STREAM_APP_BOT_LOGIN,
                            airuleset.MAINTAINER_GH_LOGIN)
        r = run("gh issue close 5560 --comment 'box recreated + verified'",
                self.branch, api_user_403=True, me="",
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir="/nonexistent-773-app-token-dir")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_maintainer_authored_close_when_selflogin_unresolvable(self):
        # The #349 discriminator survives the fallback: a maintainer-ASSIGNED
        # ticket is authored by the human maintainer, NEVER the App bot, so the
        # fallback (AUTHOR == the App bot) never fires and it stays BLOCKED even
        # when ME is unresolvable. The exact review-requiring work the guard
        # exists to protect.
        r = run("gh issue close 5561 --comment done",
                self.branch, api_user_403=True, me="",
                author=airuleset.MAINTAINER_GH_LOGIN,
                app_token_dir="/nonexistent-773-app-token-dir")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_foreign_bot_authored_close_when_selflogin_unresolvable(self):
        # The fallback compares against the SPECIFIC stream slug, never "any bot"
        # -- a dependabot-authored ticket stays BLOCKED with ME unresolvable.
        self.assertNotEqual("app/dependabot", airuleset.STREAM_APP_BOT_LOGIN)
        r = run("gh issue close 5562 --comment done",
                self.fork, api_user_403=True, me="",
                author="app/dependabot",
                app_token_dir="/nonexistent-773-app-token-dir")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)

    def test_bot_authored_fallback_is_strict_last_resort_when_me_resolves(self):
        # The fallback is a STRICT ME-EMPTY last resort: a box whose self-login
        # RESOLVES to a real (non-bot) login must NOT gain the ability to close a
        # bot-authored ticket through it. Here `gh api user` succeeds (not a bot
        # box) so ME resolves to a real user != the bot author -> BLOCK. This
        # passes on both current and fixed code -- it locks the `[ -z "$ME" ]`
        # guard so a future edit cannot widen the fallback to a resolved box.
        r = run("gh issue close 5563 --comment done",
                self.branch, me="some-real-user",
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir="/nonexistent-773-app-token-dir")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_bot_authored_close_with_glued_repo_flag_when_selflogin_unresolvable(self):
        # #773 review (MINOR): a glued `-Rowner/repo` (no separator) leaves
        # REPO_ARG empty, so AUTHOR would be read from the CWD repo while the
        # close targets the named one -- the fallback refuses via the same
        # _repo_flag_unparseable fail-safe the #533/#756 carve-outs use, even
        # though the ticket IS bot-authored.
        r = run("gh issue close 5560 -Rzbynekdrlik/odoo-erp --comment done",
                self.branch, api_user_403=True, me="",
                author=airuleset.STREAM_APP_BOT_LOGIN,
                app_token_dir="/nonexistent-773-app-token-dir")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_bot_authored_close_with_quoted_glued_repo_flag_when_selflogin_unresolvable(self):
        # #816 residual 1 on the #773 ME-empty fallback: a QUOTED glued
        # `'-Rowner/repo'` / `"-Rowner/repo"` defeats the pre-#816 boundary class
        # exactly as it does the author carve-out above — AUTHOR read from the CWD
        # repo → wrong-ALLOW. #816-review m4: cover BOTH quotes on this carve-out too
        # (the class carries both `'` and `"`), so all four carve-outs lock the single-
        # AND double-quote shape. RED (rc 0) on the pre-#816 class; GREEN (BLOCK) once
        # widened.
        for cmd in ("gh issue close 5560 '-Rzbynekdrlik/odoo-erp' --comment done",
                    'gh issue close 5560 "-Rzbynekdrlik/odoo-erp" --comment done'):
            r = run(cmd, self.branch, api_user_403=True, me="",
                    author=airuleset.STREAM_APP_BOT_LOGIN,
                    app_token_dir="/nonexistent-773-app-token-dir")
            self.assertEqual(r.returncode, 2, "%s\n%s" % (cmd, r.stderr))

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


class TestStreamLabelAcceptanceClose(TestCase):
    """#533 — a reduced-authority stream may CLOSE its OWN `stream:<user>`-labeled
    `needs-acceptance` ticket WITH an evidence `--comment`, even though the ticket
    is authored by the maintainer (shared bot identity), so the #349 author
    carve-out refuses it. Ownership is the stream LABEL, not authorship (Fable
    synthesis Variant 1). The `gh api PATCH` form stays blocked forever; every
    failure fails toward hand-off.

    The stream label the hook resolves is `stream:<the subprocess's own unix
    user>` (airuleset.py `authority --stream-label`), so the tests derive it from
    the SAME `airuleset._current_user()` the subprocess reads, not a hardcoded
    name — portable across boxes."""

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.full = _cwd_with_authority("full")
        self.branch = _cwd_with_authority("branch-merge")

    def _self_stream(self):
        return "stream:%s" % airuleset._current_user()

    # --- ALLOW: the live acceptance-close case ---

    def test_allows_acceptance_close_of_own_stream_labeled_needs_acceptance(self):
        # Case 1 (the live case, odoo-erp #3313/#3785/#3333): author IS the
        # maintainer (shared identity → author carve-out refuses), but the ticket
        # carries THIS stream's label + needs-acceptance + a --comment. RED on
        # current code (author==maintainer → BLOCK); GREEN allows.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp "
                "--comment 'fixed on PROD, client confirmed'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    # #564 rename-equivalents — verified IN-PROCESS since airuleset#839.
    #
    # These two used to run the hook as unix user montalu1 via LOGNAME/USER so
    # `authority --stream-label` (a `python3 airuleset.py …` subprocess) resolved
    # `montalu1`. #839 hardened `_current_user()` to the UNSPOOFABLE
    # `pwd.getpwuid(os.getuid()).pw_name`, so the env no longer sets a subprocess
    # identity — the montalu1-box round-trip is only exercisable on a real
    # montalu1 uid. The hook's ownership decision is `ticket-label ∈ the set
    # `authority --stream-label` emits`, so we assert that SET (the exact thing
    # the hook matches against) IN-PROCESS with the identity patched — mirroring
    # test_authority_profiles.py::test_cli_stream_label_emits_rename_equivalents.
    # The hook's subprocess set-matching + carve-out is still exercised end-to-end
    # by the real-identity tests above (own label ALLOWS, foreign BLOCKS).
    def _stream_label_set(self, user):
        with m.patch.object(cli_quals, "resolve_authority",
                            return_value="branch-merge"):
            with m.patch.object(airuleset, "_current_user", return_value=user):
                with m.patch("builtins.print") as p:
                    airuleset.cmd_authority(m.Mock(
                        explain=False, maintainer_login=False, self_login=False,
                        stream_label=True, app_bot_login=False))
        return [str(c.args[0]) for c in p.call_args_list if c.args]

    def test_ownership_set_includes_legacy_prerename_stream_label(self):
        # #564: a montalu1 box's ownership set MUST include the OLD `stream:montalu`
        # label (transition tickets still carry it), so the hook allows their
        # acceptance-close. RED before #564 (only `stream:montalu1` emitted).
        labels = self._stream_label_set("montalu1")
        self.assertIn("stream:montalu", labels, labels)

    def test_ownership_set_includes_current_name_label_after_rename(self):
        # #564 regression lock: emitting MULTIPLE equivalents must not drop the
        # exact CURRENT-name label — `stream:montalu1` stays in the set.
        labels = self._stream_label_set("montalu1")
        self.assertIn("stream:montalu1", labels, labels)

    def test_blocks_acceptance_close_with_foreign_stream_label(self):
        # #564 fail-safe: a reduced box must NOT get the carve-out for a DIFFERENT
        # stream's ticket. `stream:david` is not in THIS box's own stream-label
        # set (the real uid's equivalents), so BLOCK — the un-spoofable half of
        # the alias fail-safe (that a stream can't reach another stream's alias is
        # now guaranteed by the uid-based identity, #839; the montalu-alias-not-
        # david-alias distinction is asserted in-process above).
        labels = "stream:david needs-acceptance"
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_acceptance_close_with_short_c_comment_flag(self):
        # The `-c` short form of --comment must be honored identically.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp -c 'client confirmed'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_acceptance_close_with_comment_equals_form(self):
        # The `--comment=X` glued form must be honored identically.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment=confirmed",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_acceptance_close_under_fork_no_merge_too(self):
        # The carve-out is UNIFORM across reduced profiles (in practice a
        # fork-no-merge stream carries no such labels, so this never matches for
        # real — but the hook must behave the same when it does).
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 4006 -R kvaskodev/odoo-erp --comment 'accepted'",
                self.fork, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- BLOCK: every failure path fails toward hand-off ---

    def test_blocks_close_without_needs_acceptance(self):
        # Case 2 (the #349 replay lock): own stream label but NO needs-acceptance
        # → not an acceptance state → BLOCK. A verbatim replay of the montalu3
        # regression (a merged-into-integration ticket has the stream label but
        # not yet needs-acceptance) still blocks.
        labels = self._self_stream()   # no needs-acceptance
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_close_with_foreign_stream_label(self):
        # Case 3: needs-acceptance but a DIFFERENT stream's label → not THIS
        # stream's ticket → BLOCK.
        labels = "stream:someoneelse needs-acceptance"
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_when_ready_for_review_also_present(self):
        # Case 4 (#512 mirror): a re-hand-off (`ready-for-review`) overrides the
        # acceptance state — the gatekeeper owns it again → BLOCK.
        labels = "%s needs-acceptance ready-for-review" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_when_needs_gatekeeper_also_present(self):
        # Case 4b (#512 mirror): `needs-gatekeeper` is the other re-hand-off
        # override label → BLOCK.
        labels = "%s needs-acceptance needs-gatekeeper" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_when_prio_bounce_also_present(self):
        # Case 4c (#512 mirror): `prio:bounce` (a returned bounce, reworkable by
        # the stream) is not an acceptance state → BLOCK.
        labels = "%s needs-acceptance prio:bounce" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_when_labels_unreadable(self):
        # Case 5: a gh error reading labels must fail SAFE (BLOCK), never exempt
        # on an unverifiable label set (#349/#463 fail direction). The AUTHOR
        # read still succeeds (returns the maintainer), only the LABELS read fails.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels,
                labels_fail=True)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_close_without_comment_and_hints_the_recipe(self):
        # Case 6: ownership + acceptance state OK but NO --comment → BLOCK, and
        # the stderr NAMES the acceptance recipe (only condition 3 failed).
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--comment", r.stderr)
        self.assertIn("acceptance", r.stderr.lower())
        self.assertIn("533", r.stderr)

    def test_blocks_the_api_patch_close_form_even_with_acceptance_labels(self):
        # Case 7: the REST PATCH form is NEVER exempted, even with a perfect
        # acceptance label set — legit acceptance closes use `gh issue close`.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 "
                "-f state=closed -f body=done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_no_acceptance_hint_when_ownership_state_itself_failed(self):
        # The acceptance recipe hint fires ONLY when conditions 1+2 passed and
        # ONLY the --comment was missing — not for an ordinary foreign/assigned
        # block (where naming the recipe would wrongly invite a workaround).
        labels = "stream:someoneelse"   # neither ownership nor acceptance
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("533", r.stderr)

    # --- #533 review C1 (CRITICAL): compound/batch smuggle must BLOCK ---

    def test_blocks_batch_close_smuggling_a_foreign_close(self):
        # The exemption used to exit 0 on the FIRST close and allow the WHOLE
        # command — so a batch closed a foreign/assigned 2nd ticket too. Even
        # though ticket 3313 carries a perfect acceptance label set, a second
        # top-level `gh issue close` makes it NOT a single close action → BLOCK.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment accepted "
                "&& gh issue close 3320 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_chained_after_a_qualifying_acceptance_close(self):
        # A PATCH close chained after a qualifying `gh issue close` must NOT ride
        # the exemption through — "the PATCH form is never exempted" must hold even
        # when chained.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment ok "
                "&& gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/999 -f state=closed",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_interpreter_chained_after_a_qualifying_acceptance_close(self):
        # A nested interpreter (bash -c) could hide another close; fail safe.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment ok "
                "&& bash -c 'gh issue close 999 -R zbynekdrlik/odoo-erp'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_acceptance_close_with_a_benign_cd_prefix(self):
        # The single-action guard must NOT over-block a benign `cd dir &&` prefix
        # — exactly one close, no PATCH, no interpreter → still ALLOWED.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("cd /tmp && gh issue close 3313 -R zbynekdrlik/odoo-erp --comment accepted",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- #533 review M3 / m5: PATCH-method + repo-flag parsing edges ---

    def test_blocks_glued_method_patch_form(self):
        # The glued `--method=PATCH` form is now DETECTED as a close (is_close) and,
        # being the PATCH form, is never exempted → BLOCK (even with acceptance labels).
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh api --method=PATCH repos/zbynekdrlik/odoo-erp/issues/3313 -f state=closed",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_glued_repo_flag_failsafe(self):
        # A glued `-Rowner/repo` yields an empty REPO_ARG (no separator); rather
        # than read labels against the CWD repo (a theoretical wrong-allow), fail
        # SAFE → BLOCK.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -Rzbynekdrlik/odoo-erp --comment accepted",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_quoted_glued_repo_flag_failsafe(self):
        # #816 residual 1 on the #533 acceptance carve-out: a QUOTED glued `-R`
        # (`'-Rowner/repo'` / `"-Rowner/repo"`) leaves REPO_ARG empty AND the
        # pre-#816 boundary class misses it → the labels read falls back to the CWD
        # repo (fake gh returns the acceptance labels regardless) → wrong-ALLOW.
        # RED (rc 0) on the pre-#816 class; GREEN (BLOCK) once widened.
        labels = "%s needs-acceptance" % self._self_stream()
        for cmd in ("gh issue close 3313 '-Rzbynekdrlik/odoo-erp' --comment accepted",
                    'gh issue close 3313 "-Rzbynekdrlik/odoo-erp" --comment accepted'):
            r = run(cmd, self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                    author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
            self.assertEqual(r.returncode, 2, "%s\n%s" % (cmd, r.stderr))

    def test_allows_single_quoted_repo_flag(self):
        # A single-quoted `-R 'owner/repo'` now parses correctly → the labels read
        # targets the right repo → ALLOW (no more false-block on single quotes).
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh issue close 3313 -R 'zbynekdrlik/odoo-erp' --comment accepted",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- Case 8: the `authority --stream-label` CLI flag itself ---

    def test_stream_label_flag_is_empty_under_full_authority(self):
        r = subprocess.run(
            ["python3", str(ROOT / "airuleset.py"), "authority", "--stream-label"],
            capture_output=True, text=True, cwd=self.full)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "", r.stdout)

    def test_stream_label_flag_prints_stream_user_under_reduced_authority(self):
        r = subprocess.run(
            ["python3", str(ROOT / "airuleset.py"), "authority", "--stream-label"],
            capture_output=True, text=True, cwd=self.branch)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(),
                         "stream:%s" % airuleset._current_user(), r.stdout)


class TestIsCloseDetectorHardening(TestCase):
    """#540 — harden the `is_close` FRONT gate against two close forms that
    escaped the WHOLE guard (surfaced by #533's Fable adversarial review, M3):

      FORM 1 — a `gh api -X PATCH …/issues/N --input body.json` (or `--input -`,
      or `-F state=@file`): the `state=closed` lives in the request BODY, not
      argv, so the literal-`state=closed` grep missed it → is_close=0 → the hook
      exited 0 at the top, bypassing everything. Chosen fix (Approach 3): the
      PATCH branch fires on `state=closed` visible OR a HIDDEN body (`--input`
      token / a `=@`-valued field). The `gh api PATCH` form is NEVER exempted, so
      detection == block for a reduced-authority stream. A visible non-close
      PATCH (a `-f state=open` reopen, or an `issues/N` mention inside a field
      value on a NON-issue endpoint) stays allowed — no new false-positive.

      FORM 2 — a STANDALONE `bash -c 'gh issue close N'` / `sh -c "…"` /
      `eval "…"`: the boundary class excluded the quote chars `'`/`"`, so a close
      opening an interpreter's quoted argument was not a boundary match →
      is_close=0. Chosen fix (Approach 3): widen the boundary class symmetrically
      to include the quotes; #533's existing HAS_INTERP then blanks ISSUE_NUM so
      the nested close can never ride a carve-out → BLOCK.

    All cases run under a REDUCED-authority cwd with a FOREIGN author so neither
    #533 carve-out can fire — detection alone must reach the BLOCK.
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.full = _cwd_with_authority("full")
        self.branch = _cwd_with_authority("branch-merge")

    def _self_stream(self):
        return "stream:%s" % airuleset._current_user()

    # --- FORM 1: --input / @file-body PATCH must be DETECTED → BLOCK ---

    def test_blocks_patch_close_via_input_body_file(self):
        # state=closed lives in body.json, not argv — the escape #540 exists for.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 "
                "--input body.json", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_patch_close_via_input_stdin(self):
        # `--input -` reads the body from stdin — invisible to the hook, but the
        # `--input` TOKEN is the argv marker that fails safe.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 --input -",
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_close_via_input_equals_form(self):
        # `--input=body.json` (glued) must be treated identically to `--input f`.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 "
                "--input=body.json", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_close_via_glued_method_and_input(self):
        # The glued `--method=PATCH` (already detected by #533) combined with a
        # hidden --input body — both #540 residual shapes at once.
        r = run("gh api --method=PATCH repos/zbynekdrlik/odoo-erp/issues/3313 "
                "--input body.json", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_close_via_at_file_state_field(self):
        # `-F state=@state.txt` reads the state value from a file (a `=@` field),
        # hiding `state=closed` from argv — the 5th body-hiding shape.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 "
                "-F state=@state.txt", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_close_via_value_quoted_state_double(self):
        # #540 review FINDING 1 (MAJOR): `-f state="closed"` — quoting a shell
        # VALUE the ordinary way puts a quote between `=` and `closed`, so the
        # literal `state=closed` grep missed it. This is NOT obfuscation — it is
        # the most natural way a model writes a shell value, and it escaped.
        r = run('gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 -f state="closed"',
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_patch_close_via_value_quoted_state_single(self):
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 -f state='closed'",
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_close_via_value_quoted_raw_field_state(self):
        r = run('gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 '
                '--raw-field state="closed"', self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_value_quoted_state_open_reopen(self):
        # The widened `state=["']?closed` must still NOT match a value-quoted
        # reopen — precision guard against over-widening FINDING 1's fix.
        r = run('gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 -f state="open"',
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_input_body_patch_blocks_even_with_acceptance_labels(self):
        # The `gh api PATCH` form is NEVER exempted — even a perfect acceptance
        # label set cannot allow the --input body form (mirror of
        # test_blocks_the_api_patch_close_form_even_with_acceptance_labels).
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 --input b.json",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- FORM 1 precision: a visible NON-close PATCH must stay ALLOWED ---

    def test_allows_visible_state_open_reopen_patch(self):
        # A visible `-f state=open` reopen (no state=closed, no hidden body) is
        # NOT a close → allowed. Confirms Approach 3 does not over-block reopens.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/3313 -f state=open",
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_patch_to_non_issue_endpoint_mentioning_issues_in_body(self):
        # A PATCH to a PR that merely MENTIONS `issues/N` inside a field value,
        # with no state=closed and no hidden body, must NOT be read as a close —
        # the false-positive Approach 1 (drop state=closed) would have caused.
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/pulls/5 "
                "-f body='fixes issues/3 somehow'", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- FORM 2: quoted-interpreter close must be DETECTED → BLOCK ---

    def test_blocks_standalone_bash_c_close(self):
        r = run("bash -c 'gh issue close 3313 --comment done'", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_standalone_sh_c_close_double_quoted(self):
        r = run('sh -c "gh issue close 3313"', self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)

    def test_blocks_standalone_eval_close(self):
        r = run('eval "gh issue close 3313"', self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_standalone_dash_c_close(self):
        r = run("dash -c 'gh issue close 3313'", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_standalone_interpreter_close_blocks_even_with_acceptance_labels(self):
        # A perfect acceptance label set cannot rescue a quoted-interpreter close
        # — HAS_INTERP blanks ISSUE_NUM so no carve-out fires → BLOCK.
        labels = "%s needs-acceptance" % self._self_stream()
        r = run("bash -c 'gh issue close 3313 -R zbynekdrlik/odoo-erp --comment ok'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- FORM 2 precision: a bash -c with a NON-close gh command stays ALLOWED ---

    def test_allows_bash_c_with_a_non_close_gh_command(self):
        # A `bash -c 'gh issue list'` carries no close phrase → is_close=0 →
        # allowed, even though it is a nested interpreter. Confirms the widened
        # boundary does not over-block every quoted gh command.
        r = run("bash -c 'gh issue list --state open'", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestGkVerdictArtifactClose(TestCase):
    """#756 — a reduced-authority stream may CLOSE a foreign-authored ODOO-ERP
    ticket that carries a gatekeeper REVIEW-VERDICT artifact, WITH an evidence
    `--comment` (odoo-erp owner ruling #5378: gatekeeper reviews+merges, posts its
    verdict, DROPS the queue label, hands the ticket BACK; the delivering STREAM
    closes it after review — the gatekeeper no longer closes stream tickets).

    The signal is the SAME artifact odoo-erp's `subdev-self-close-guard.yml`
    (#3784) keys on — never WHO pressed close: a case-insensitive H1-H3 heading
    that STARTS (after non-word decoration) with `gatekeeper` and carries a verdict
    word (review|verification|verdict), OR a line-start `GATEKEEPER-CLOSE:` marker
    (#3712). That reopen-guard (POST-close, precise time-window) is the second net;
    this hook (PRE-close) just stops blocking a reviewed close.

    Additive carve-out (author + #533 acceptance carve-outs BYTE-UNTOUCHED). Every
    failure fails toward hand-off: no artifact / non-odoo-erp repo / a re-hand-off/
    bounce/acceptance override label / no --comment / unreadable ticket / the
    `gh api PATCH` form → BLOCK. Live incident it fixes: montalu3 2026-08-30, gk
    PASS verdicts on odoo-erp #5345/#5522, the stream told to close per #5378, the
    hook blocked both `gh issue close` and `gh api PATCH` → close-ready tickets rot.
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.full = _cwd_with_authority("full")
        self.branch = _cwd_with_authority("branch-merge")
        self.M = airuleset.MAINTAINER_GH_LOGIN
        self.HEADING = "## Gatekeeper cross-fork review - CLEAN\n\nDiff read, CI green."
        self.MARKER = "GATEKEEPER-CLOSE: released 2.221, verified on PROD."

    # --- ALLOW: the live post-review stream self-close ---

    def test_allows_reviewed_close_with_verdict_heading(self):
        # THE live case (odoo-erp #5345): foreign author, no acceptance label; the
        # ticket carries a gk review-verdict heading + the close carries --comment.
        # RED on current code (foreign author → BLOCK); GREEN allows.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp "
                "--comment 'merged 38e38b2, gk verdict CLEAN'",
                self.branch, me=self.M, author=self.M,
                labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_reviewed_close_with_gatekeeper_close_marker(self):
        # The line-start `GATEKEEPER-CLOSE:` marker (#3712) is the other artifact.
        r = run("gh issue close 5522 -R zbynekdrlik/odoo-erp --comment 'done, ref verdict'",
                self.branch, me=self.M, author=self.M,
                labels="", comments=self.MARKER)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_reviewed_close_with_emoji_decorated_heading(self):
        # #5089: gatekeeper's real cold-review headings carry a leading emoji —
        # the `[^[:alnum:]_#]*` decoration class must tolerate it.
        c = "## 🔎 Gatekeeper cold review — CLEAN\n\nall good"
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=c)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_reviewed_close_with_verification_verdict_words(self):
        # #4280: the verdict word may be `verification`/`verdict`, not only `review`.
        for c in ("## GATEKEEPER VERIFICATION - PASS\n",
                  "### gatekeeper re-review verdict - CLEAN\n"):
            r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                    self.branch, me=self.M, author=self.M, labels="", comments=c)
            self.assertEqual(r.returncode, 0, "%s\n%s" % (c, r.stderr))

    def test_allows_reviewed_close_under_fork_no_merge_too(self):
        r = run("gh issue close 4006 -R zbynekdrlik/odoo-erp --comment ok",
                self.fork, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_reviewed_close_with_short_c_flag(self):
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp -c 'gk CLEAN'",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_reviewed_close_resolving_repo_from_cwd_remote(self):
        # No -R flag: the repo is resolved from the cwd git remote. Point the cwd's
        # origin at odoo-erp so the odoo-erp scope engages without an explicit -R.
        subprocess.run(["git", "init", "-q"], cwd=self.branch)
        subprocess.run(["git", "remote", "add", "origin",
                        "https://github.com/zbynekdrlik/odoo-erp.git"], cwd=self.branch)
        r = run("gh issue close 5345 --comment 'gk CLEAN'",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_quoted_glued_repo_flag_failsafe_verdict(self):
        # #816 residual 1 on the #756 verdict carve-out. A QUOTED glued `-R`
        # (`'-Rzbynekdrlik/odoo-erp'`) leaves REPO_ARG empty; with the pre-#816
        # boundary class the fail-safe MISSES it, so VERDICT_REPO_UNPARSEABLE stays
        # 0 and VERDICT_REPOFULL is resolved from the CWD git remote. Point the cwd
        # origin at odoo-erp (as the cwd-remote test above does) so VERDICT_REPOFULL
        # DOES match `zbynekdrlik/odoo-erp` — the carve-out then reads the artifact
        # from the CWD repo and, with the verdict heading + --comment present,
        # wrong-ALLOWs a close whose `-R` should have hit the present-but-unparseable
        # fail-safe. RED (rc 0) on the pre-#816 class; GREEN once the class is widened
        # → VERDICT_REPO_UNPARSEABLE=1 → the carve-out is skipped → BLOCK.
        subprocess.run(["git", "init", "-q"], cwd=self.branch)
        subprocess.run(["git", "remote", "add", "origin",
                        "https://github.com/zbynekdrlik/odoo-erp.git"], cwd=self.branch)
        for cmd in ("gh issue close 5345 '-Rzbynekdrlik/odoo-erp' --comment 'gk CLEAN'",
                    'gh issue close 5345 "-Rzbynekdrlik/odoo-erp" --comment "gk CLEAN"'):
            r = run(cmd, self.branch, me=self.M, author=self.M,
                    labels="", comments=self.HEADING)
            self.assertEqual(r.returncode, 2, "%s\n%s" % (cmd, r.stderr))

    def test_allows_reviewed_close_with_large_comment_payload(self):
        # #772: a legit #756 verdict self-close must NOT be intermittently
        # blocked by the SIGPIPE race in _has_gk_verdict_artifact (recurrence of
        # #192). On a long-lived ticket V_COMMENTS is hundreds of KB; the
        # `printf '%s\n' "$1" | grep -q` pipe under `set -o pipefail` collapses
        # to printf's SIGPIPE-141 exit once grep -q short-circuits on the
        # first-line match, so the artifact reads as ABSENT and the close is
        # spuriously BLOCKED. A ~260KB payload with the verdict heading on line 1
        # makes the race DETERMINISTIC: grep -q matches line 1 and exits while
        # printf is still blocked writing past the full 64KB pipe -> SIGPIPE.
        # The payload is ~97KB (2500 filler lines): >64KB (the pipe capacity, so
        # printf always blocks) yet under the ~128KB single-env-var limit
        # (MAX_ARG_STRLEN) the hermetic fake gh passes it through. RED on the pipe
        # form (12/12 spurious NOT-FOUND measured at ~97KB), GREEN on the
        # here-string.
        big = self.HEADING + "\n" + ("filler line to pad the comment payload\n" * 2500)
        self.assertGreater(len(big), 90000)
        self.assertLess(len(big), 120000)
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp "
                "--comment 'merged, gk verdict CLEAN'",
                self.branch, me=self.M, author=self.M, labels="", comments=big)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- BLOCK: every failure fails toward hand-off ---

    def test_blocks_close_without_verdict_artifact(self):
        # The #349 replay: merged into integration, NO gk review yet → no artifact
        # → BLOCK. The exact review-bypass the guard exists to prevent stays closed.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=self.M, author=self.M,
                labels="", comments="Merged into develop. Tests green on my box.")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_reviewed_close_without_comment_and_hints_the_recipe(self):
        # Verdict artifact present but NO --comment → BLOCK, and the stderr NAMES
        # the #756 evidence-citation recipe (fires for no other block reason).
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("756", r.stderr)
        self.assertIn("--comment", r.stderr)

    def test_blocks_reviewed_close_with_ready_for_review_still_present(self):
        # gk drops the queue label at hand-back; if `ready-for-review` is STILL
        # present the gk owns it again → BLOCK.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M,
                labels="ready-for-review", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_with_needs_gatekeeper_still_present(self):
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M,
                labels="needs-gatekeeper", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_with_prio_bounce(self):
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M,
                labels="prio:bounce", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_verdict_carveout_defers_needs_acceptance_to_the_acceptance_path(self):
        # A `needs-acceptance` ticket is closed via the #533 acceptance carve-out
        # (which REQUIRES --comment for the client-confirmation citation). The
        # verdict carve-out must EXCLUDE needs-acceptance so it can't bypass that
        # --comment requirement: a needs-acceptance ticket + verdict artifact + NO
        # --comment must still BLOCK.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp",
                self.branch, me=self.M, author=self.M,
                labels="stream:%s needs-acceptance" % airuleset._current_user(),
                comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_on_non_odoo_erp_repo(self):
        # Same verdict artifact, but a NON-odoo-erp repo → the carve-out never
        # engages (odoo-erp is where #5378 applies AND the reopen-guard is the
        # second net) → BLOCK.
        r = run("gh issue close 5345 -R kvaskodev/other-repo --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_when_verdict_is_a_bare_prose_line(self):
        # Self-exemption immunity (reopen-guard #4224): a prose line "gatekeeper
        # review comment landed ..." with NO leading `#` heading is NOT an artifact.
        c = "gatekeeper review comment landed earlier, closing now"
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=c)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_when_heading_is_stream_readiness_line(self):
        # A stream's own "## Ready for gatekeeper cross-fork review" readiness line
        # STARTS with "Ready" (a word char stops the decoration class), so it is
        # NOT a gatekeeper verdict heading → BLOCK.
        c = "## Ready for gatekeeper cross-fork review\n\nplease review"
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=c)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_when_heading_is_h4(self):
        # `#{1,3}` only — an H4+ `#### Gatekeeper review` is NOT a match (mirrors
        # the reopen-guard's own H1-H3 restriction).
        c = "#### Gatekeeper review - CLEAN\n"
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=c)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_patch_form_even_with_verdict_artifact(self):
        # The `gh api PATCH` form is NEVER exempted — even a perfect verdict artifact
        # cannot allow it (ISSUE_NUM is empty for a pure PATCH).
        r = run("gh api -X PATCH repos/zbynekdrlik/odoo-erp/issues/5345 -f state=closed",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_batch_smuggle_after_a_verdict_close(self):
        # A second top-level close makes it NOT a single close action → the
        # single-action guard blanks ISSUE_NUM → no carve-out fires → BLOCK.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok "
                "&& gh issue close 5346 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reviewed_close_when_ticket_read_fails(self):
        # A gh error reading the ticket must fail SAFE (BLOCK), never exempt on an
        # unverifiable ticket (the #349/#463 fail direction).
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="",
                comments=self.HEADING, verdict_fail=True)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_reviewed_close_never_engages_under_full_authority(self):
        # A full-authority box already closes freely (AUTH==full → exit 0 before the
        # carve-out) — the verdict path is reduced-authority only.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                self.full, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_reviewed_close_on_same_name_foreign_owner_repo(self):
        # #756 review F4: a same-BASENAME but foreign-OWNER repo (`otherowner/odoo-erp`
        # — a fork or an unrelated same-named repo) has NO reopen-guard second net, so
        # the carve-out must require the FULL `zbynekdrlik/odoo-erp`, not just the
        # `odoo-erp` basename → BLOCK.
        r = run("gh issue close 5345 -R otherowner/odoo-erp --comment ok",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_backtick_smuggle_after_a_verdict_close(self):
        # #756 review F6: a backtick command-substitution nested close is a smuggle the
        # HAS_INTERP grep and the N_CLOSE boundary class both miss — it must blank
        # ISSUE_NUM so the verdict carve-out cannot ride it → BLOCK.
        r = run("gh issue close 5345 -R zbynekdrlik/odoo-erp "
                "--comment x`gh issue close 9999 -R zbynekdrlik/odoo-erp -c y`",
                self.branch, me=self.M, author=self.M, labels="", comments=self.HEADING)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestRepoFlagUnparseableHereString(TestCase):
    """#816 residual 2 — `_repo_flag_unparseable` must read `$CMD` via a
    here-string (`grep -qE ... <<<"$CMD"`), NOT `printf '%s' "$CMD" | grep -q`.

    Under the hook's `set -o pipefail`, on a MULTI-LINE command whose `-R` match
    is on an early line with >64KB of trailing lines, `grep -q` short-circuits and
    exits while `printf` is still blocked writing past the 64KB pipe buffer →
    SIGPIPE → the pipeline returns 141 → the helper returns non-zero (FALSE, "not
    unparseable") → the present-but-unparseable fail-safe is DEFEATED (a wrong-ALLOW,
    the OPPOSITE fail direction from #772's `_has_gk_verdict_artifact`, which fails
    toward block). Measured deterministic: pipe form 12/12 wrong-FALSE at 147KB /
    3002 lines; here-string 0/12.

    Tested at the HELPER level (not end-to-end through the hook) BY DESIGN: on the
    SAME >64KB multi-line command the pre-existing `is_close` grep at ~L202 (a sibling
    `printf | grep -qE`, OUT OF SCOPE for #816) SIGPIPEs FIRST and exits the hook 0
    (allow) at ~L207 before any carve-out is reached, so an end-to-end test could
    never isolate this helper's fix (see the #816 design comment + follow-up #824).
    The function bytes are EXTRACTED from the live hook, so a regression that reverts
    the here-string re-introduces the SIGPIPE and fails this test.
    """

    def _extract_helper(self):
        src = HOOK.read_text()
        m = re.search(r"(?ms)^_repo_flag_unparseable\(\) \{.*?^\}", src)
        self.assertIsNotNone(
            m, "could not locate _repo_flag_unparseable() in the hook")
        return m.group(0)

    def test_helper_blocks_a_multiline_over_64kb_command(self):
        func = self._extract_helper()
        # -R glued on line 1, then >64KB of trailing filler LINES → an early
        # match with a large blocked-printf tail = the deterministic SIGPIPE case.
        filler = "filler line to pad the command payload past 64KB\n" * 3000
        cmd = "gh issue close 3313 -Rzbynekdrlik/odoo-erp --comment 'note\n%s'" % filler
        self.assertGreater(len(cmd), 64 * 1024)
        self.assertGreater(cmd.count("\n"), 100)
        # Faithful reproduction of the real hook conditions: `set -euo pipefail`,
        # $CMD read from stdin (avoids the ~128KB single-argv/env MAX_ARG_STRLEN
        # limit), the helper called in an `if` (so `set -e` is suspended for its
        # body, exactly as at the real call sites), REPO_ARG (`$1`) empty.
        driver = (
            "set -euo pipefail\n"
            "CMD=$(cat)\n"
            + func + "\n"
            "if _repo_flag_unparseable \"\"; then echo TRUE; else echo FALSE; fi\n"
        )
        r = subprocess.run(["bash", "-c", driver], input=cmd,
                           capture_output=True, text=True)
        # RED on the `printf | grep` form (SIGPIPE → FALSE / wrong-allow);
        # GREEN on the here-string form (TRUE → the fail-safe blocks).
        self.assertEqual(r.stdout.strip(), "TRUE",
                         "helper must detect the -R flag (fail-safe) on a >64KB "
                         "multi-line command; stdout=%r stderr=%r"
                         % (r.stdout, r.stderr))


# ===========================================================================
# #824 — front-gate + sibling-detector SIGPIPE hardening (concern 1+2), the
# `\gh issue close` boundary blind spot + its ISSUE_NUM/N_CLOSE blast radius
# (concern 3), and comment-value REPO_ARG poisoning (concern 4). Each fix half
# is independently mutation-verifiable (revert one → only its RED test(s) fail).
# ===========================================================================

# An EARLY grep match + a >64KB blocked-printf tail is the deterministic SIGPIPE
# case (the pipe buffer is 64KB). Fed via stdin (run()/_run_with_gh_body pass the
# payload as `input=`), so the ~128KB single-argv MAX_ARG_STRLEN limit never bites.
_FILLER_64K = "filler line to pad the command payload past 64KB\n" * 3000


def _run_with_gh_body(cmd, cwd, gh_body, hook=None):
    """Like run(), but injects a CUSTOM fake `gh` body — for the per-repo /
    per-issue-number author reads the fixed _FAKE_GH cannot express. ME resolves
    via `authority --self-login` → `gh api user`, which every body below answers
    'someoneelse'."""
    d = tempfile.mkdtemp()
    gh = Path(d) / "gh"
    gh.write_text(gh_body)
    gh.chmod(0o755)
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    env["PATH"] = d + os.pathsep + env.get("PATH", "")
    env.pop("GH_APP_TOKEN_DIR", None)
    return subprocess.run(["bash", str(hook or HOOK)], input=payload,
                          capture_output=True, text=True, cwd=cwd, env=env)


# Author read keyed on the -R VALUE: the `baz/qux` poison repo reads SELF
# ('someoneelse' == ME), every other repo (incl. the cwd fallback) FOREIGN.
_GH_REPO_SCOPED_AUTHOR = """#!/usr/bin/env bash
case "$1 $2" in
  "api user") echo "someoneelse";;
  "issue view")
    if printf '%s ' "$@" | grep -q -- '--json labels,comments'; then echo '{"labels":[],"comments":[]}';
    elif printf '%s ' "$@" | grep -q -- '--json labels'; then :;
    else
      if printf '%s ' "$@" | grep -q 'baz/qux'; then echo "someoneelse"; else echo "zbynekdrlik"; fi
    fi;;
  *) exit 1;;
esac
"""

# Author read keyed on the ISSUE NUMBER (the `view <n>` positional): issue 3312
# is SELF, everything else FOREIGN.
_GH_NUMBER_SCOPED_AUTHOR = """#!/usr/bin/env bash
case "$1 $2" in
  "api user") echo "someoneelse";;
  "issue view")
    if printf '%s ' "$@" | grep -q -- '--json labels,comments'; then echo '{"labels":[],"comments":[]}';
    elif printf '%s ' "$@" | grep -q -- '--json labels'; then :;
    else
      if [ "$3" = "3312" ]; then echo "someoneelse"; else echo "zbynekdrlik"; fi
    fi;;
  *) exit 1;;
esac
"""


class TestFrontGateSigpipe824(TestCase):
    """#824 concern 1+2: the front gate `is_close` and its sibling `printf|grep`
    detectors SIGPIPE under `set -o pipefail` on a >64KB multi-line command whose
    close phrase matches on an early line — `grep -q` short-circuits + exits while
    `printf` is still blocked writing past the 64KB pipe buffer → SIGPIPE (141) →
    the detector reads FALSE → the whole hook `exit 0`s (concern 1) before
    authority is resolved. Fix = here-string `grep … <<< "$X"` (the #772/#816
    convention) at EVERY `printf|grep` site. Foreign author + reduced authority so
    no carve-out can rescue the close — detection alone must reach the BLOCK."""

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    def test_blocks_over_64kb_multiline_foreign_close(self):
        # is_close front gate: RED (rc 0, SIGPIPE bypass) on `printf|grep`, GREEN
        # (rc 2) on the here-string.
        r = run("gh issue close 3312 --comment 'note\n%s'" % _FILLER_64K,
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_small_multiline_foreign_close_blocks_control(self):
        # <64KB: no SIGPIPE, blocks on BOTH old and new code — proves the >64KB
        # test above is SIGPIPE-SIZE-triggered, not a general miss.
        small = "x\n" * 100
        r = run("gh issue close 3312 --comment 'note\n%s'" % small,
                self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_over_64kb_patch_close(self):
        # _is_patch_close_cmd's first grep (`gh api … issues/N`) matches early on a
        # >64KB PATCH command → SIGPIPE → `|| return 1` → not-a-close → is_close=0 →
        # bypass. RED (rc 0) today, GREEN (rc 2) on the here-string. The PATCH form
        # is never exempted, so detection == block for a reduced-authority stream.
        r = run("gh api -X PATCH repos/o/n/issues/3313 --input body.json %s"
                % _FILLER_64K, self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_no_printf_pipe_grep_remains_in_the_hook(self):
        # Source-lock: every `printf … | grep` SIGPIPE site is converted to a
        # here-string, so NONE remains. A revert of ANY single conversion
        # re-introduces its `printf|grep` and fails this lock — the mutation-verify
        # net for the shadowed sibling detectors (concern 2) the front gate ties
        # off end-to-end. `printf … | jq` (the JSON read) is untouched + allowed.
        src = HOOK.read_text()
        # Strip the comment part (from the first `#`) so the "NOT printf|grep"
        # explanatory comments this fix leaves behind are not read as offenders;
        # a real reverted CODE line has its `printf … | grep` before any `#`.
        offenders = [ln.strip() for ln in src.splitlines()
                     if re.search(r"printf\b.*\|\s*grep\b", ln.split("#", 1)[0])]
        self.assertEqual(offenders, [],
                         "these `printf|grep` sites must use a here-string "
                         "(#824 SIGPIPE hardening):\n" + "\n".join(offenders))

    def test_cmd_has_comment_flag_helper_is_sigpipe_immune(self):
        # Helper-level SIGPIPE proof for a SHADOWED detector (concern 2), the #816
        # TestRepoFlagUnparseableHereString precedent. `_cmd_has_comment_flag` reads
        # the global $CMD; on a >64KB command with `--comment` early + a huge tail,
        # `printf|grep -q` SIGPIPEs → the helper reads "no comment" (FALSE). Extract
        # the LIVE helper and prove the here-string form returns TRUE.
        src = HOOK.read_text()
        m = re.search(r"(?ms)^_cmd_has_comment_flag\(\) \{.*?^\}", src)
        self.assertIsNotNone(m, "could not locate _cmd_has_comment_flag() in the hook")
        func = m.group(0)
        cmd = "gh issue close 3312 --comment 'note\n%s'" % _FILLER_64K
        self.assertGreater(len(cmd), 64 * 1024)
        driver = ("set -euo pipefail\nCMD=$(cat)\n" + func + "\n"
                  "if _cmd_has_comment_flag; then echo TRUE; else echo FALSE; fi\n")
        r = subprocess.run(["bash", "-c", driver], input=cmd,
                           capture_output=True, text=True)
        # RED (FALSE, SIGPIPE) on the pipe form; GREEN (TRUE) on the here-string.
        self.assertEqual(r.stdout.strip(), "TRUE",
                         "stdout=%r stderr=%r" % (r.stdout, r.stderr))


class TestBackslashCloseBoundary824(TestCase):
    """#824 concern 3: `\\gh issue close` (bash strips the `\\`, gh runs a real
    close) is not a `_CLOSE_OPEN` boundary match → is_close=0 → bypass. Fix = widen
    `_CLOSE_OPEN` to include `\\` (a deliberate superset of `_repo_flag_unparseable`'s
    #816 class). Its blast radius (the ticket's own RED-matrix note, verified live
    by the Fable design consult): the unbounded `ISSUE_NUM` substring extraction can
    grab the `\\gh` self-number while the narrow `N_CLOSE` counts only a later
    top-level FOREIGN close → the carve-out is checked against the WRONG issue →
    wrong-ALLOW. Fix = boundary-align `ISSUE_NUM` with N_CLOSE's narrow class.
    N_CLOSE stays narrow (the #816/#540 deliberate decoupling)."""

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    def test_blocks_backslash_gh_close_of_foreign_issue(self):
        # RED (rc 0, is_close=0 bypass) today; GREEN (rc 2) once `\\` is in _CLOSE_OPEN.
        r = run("\\gh issue close 3312 --comment done", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_blocks_backslash_gh_close_under_fork_no_merge(self):
        r = run("\\gh issue close 3312 --comment done", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)

    def test_blocks_backslash_self_then_foreign_two_close(self):
        # The Q2 blast-radius hole (verified live rc 0): `\\gh issue close SELF && gh
        # issue close FOREIGN`. Today ISSUE_NUM grabs SELF (3312, self-authored) from
        # the `\\gh` substring while N_CLOSE=1 counts only the top-level FOREIGN 3399
        # → the carve-out allows on SELF → the foreign 3399 rides through. GREEN once
        # ISSUE_NUM is boundary-aligned → ISSUE_NUM=3399 (foreign) → BLOCK.
        r = _run_with_gh_body(
            "\\gh issue close 3312 --comment ok && gh issue close 3399 --comment ok",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_single_backslash_self_close(self):
        # A single `\\gh issue close SELF`: today is_close=0 → bypass (rc 0). After
        # fix is_close=1 but N_CLOSE=0 (narrow class misses `\\gh`) → the
        # single-action guard blanks ISSUE_NUM → BLOCK (over-block, safe: `\\gh` is a
        # bizarre way to type a self-close).
        r = _run_with_gh_body("\\gh issue close 3312 --comment ok",
                              self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_ordinary_self_close_still_allowed_after_widening(self):
        # Control (passes on BOTH old and new code): a normal `gh issue close SELF`
        # (start boundary) is unaffected by the `\\` widening + ISSUE_NUM boundary
        # alignment → still ALLOWED.
        r = _run_with_gh_body("gh issue close 3312 --comment ok",
                              self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_benign_cd_prefix_self_close_still_allowed(self):
        # Control: `cd x && gh issue close SELF` (space boundary) → ISSUE_NUM=SELF
        # via the narrow class → ALLOWED (the boundary alignment must not break the
        # benign-prefix case #533 already locks).
        r = _run_with_gh_body("cd /tmp && gh issue close 3312 --comment ok",
                              self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestRepoArgPoison824(TestCase):
    """#824 concern 4: the REPO_ARG grep scans the WHOLE command including
    `--comment`/`-c`/`--reason`/`-r` VALUES, so a `-R x/y`-looking string inside a
    value yields a NON-EMPTY REPO_ARG → the carve-out reads author/labels from the
    poisoned repo while the close targets another → wrong-ALLOW. Fix = strip
    value-flag VALUES into `_CMD_STRIPPED` and run EXTRACTION + the
    `_repo_flag_unparseable` presence-grep on the stripped copy (DETECTION stays on
    the original), plus a ≥2-`-R`-tokens-in-stripped fail-safe for the escaped-quote
    residual. `_GH_REPO_SCOPED_AUTHOR` reads `baz/qux` as SELF, every other repo
    (incl. the cwd) FOREIGN — so each poison is a wrong-ALLOW today."""

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    def test_blocks_comment_value_poison_variant_a_glued_real_repo(self):
        # The ticket's example: glued `-Rfoo/bar` (gh's real FOREIGN target) + `-R
        # baz/qux` (SELF) inside the comment. Today REPO_ARG=baz/qux → author read
        # hits SELF → ALLOW (rc 0). After fix: comment stripped → `-Rfoo/bar` glued →
        # REPO_ARG empty → `_repo_flag_unparseable` fires → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 '-Rfoo/bar' --comment 'per -R baz/qux'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_comment_value_poison_variant_b_no_real_repo(self):
        # No real `-R` (the cwd repo is the FOREIGN target) + `-R baz/qux` (SELF) in
        # the comment. Today REPO_ARG=baz/qux → author read hits SELF → ALLOW. After
        # fix: comment stripped → no `-R` → REPO_ARG empty → author read hits the cwd
        # repo (FOREIGN) → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 --comment 'per -R baz/qux'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_reason_value_poison(self):
        # The same poison via a `--reason` value (also stripped).
        r = _run_with_gh_body(
            "gh issue close 4 '-Rfoo/bar' --reason 'closed per -R baz/qux'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_escaped_quote_repo_poison(self):
        # The escaped-quote residual (Fable Q3): `--comment 'it'\\''s -R baz/qux'`.
        # The `'[^']*'` strip eats only `'it'`, leaving a ` -R baz/qux` residue, so
        # the strip alone does not remove it. The ≥2-`-R`-tokens-in-stripped fail-safe
        # (a legit close carries exactly one) forces the block: the stripped copy
        # still has BOTH the glued `'-Rfoo/bar'` and the residue `-R baz/qux` →
        # `_repo_flag_unparseable` fires → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 '-Rfoo/bar' --comment 'it'\\''s -R baz/qux here'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_legit_self_close_whose_comment_mentions_repo_flag(self):
        # Control (passes on BOTH old and new code): a legit SELF close (real `-R
        # baz/qux`, self-authored) whose comment merely MENTIONS `-R` in prose must
        # NOT be over-blocked. The comment's `-R` is stripped, leaving one real `-R`.
        r = _run_with_gh_body(
            "gh issue close 4 -R baz/qux --comment 'note about -R flag'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_legit_close_repo_flag_before_comment(self):
        # The common case: real `-R baz/qux` (SELF) then a --comment. REPO_ARG still
        # parses baz/qux from the STRIPPED copy → author read hits baz/qux (SELF) →
        # ALLOW.
        r = _run_with_gh_body(
            "gh issue close 4 -R baz/qux --comment 'fixed on PROD'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestReviewFindings824(TestCase):
    """#824 adversarial-review findings (salvaged Fable review #1). The base #824
    implementation (`_strip_value_flags` + stripped-copy extraction/counting) opened
    NEW wrong-ALLOW regressions the review constructed; each fixture below RED-locks
    one. All run under a REDUCED-authority cwd with a SELF-vs-FOREIGN author fixture
    so a wrong target read is a wrong-ALLOW today.

      A-1: the unquoted value arm `[^[:space:]]+` spans shell operators, ERASING a
           real top-level close from the stripped copy → N_CLOSE under-counts.
      A-2: the SINGLE-residue escaped-quote poison (no real `-R`, just the residue) —
           the ≥2-`-R` leg only fires with a SECOND real `-R`, so it escaped (the
           code comment falsely claimed it caught it).
      A-3: a GLUED short-flag value (`-c'…'`) is not stripped → its `-R` poisons REPO_ARG.
      C-1: a reversed `gh SELF && \\gh FOREIGN` pair — the `\\gh` close is invisible to
           the narrow N_CLOSE + narrow ISSUE_NUM → the self carve-out allows the pair.
      D-1: `$(gh issue close FOREIGN)` inside a stripped comment value vanishes from
           the stripped copy → N_CLOSE no longer counts it (the `(` used to).
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    # --- A-1: operator-spanning unquoted value arm erases a top-level close ---

    def test_blocks_operator_spanning_comment_value(self):
        # `--comment done&&gh issue close FOREIGN`: bash runs BOTH closes. Today the
        # unquoted value arm eats `done&&gh` as one value → the FOREIGN close is
        # erased from the stripped copy → N_CLOSE=1 on the self close → carve-out
        # `exit 0` allows the whole command (rc 0). GREEN: the arm is operator-bounded
        # so `&&gh issue close 3399` stays → N_CLOSE=2 → blank ISSUE_NUM → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 3312 --comment done&&gh issue close 3399 --comment ok",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_operator_spanning_via_semicolon(self):
        # Same class via `;` instead of `&&`.
        r = _run_with_gh_body(
            "gh issue close 3312 --comment done;gh issue close 3399 --comment ok",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- A-2: single-residue escaped-quote REPO_ARG poison ---

    def test_blocks_single_residue_escaped_quote_repo_poison(self):
        # `--comment 'it'\\''s -R baz/qux done'` with NO real `-R`: the strip's
        # single-quote arm loses to leftmost-longest and eats `'it'\\''s`, leaving a
        # ` -R baz/qux done'` residue → REPO_ARG=baz/qux (SELF in the fixture) while
        # the close targets the cwd (FOREIGN) → wrong-ALLOW (rc 0) today. The ≥2-`-R`
        # leg does NOT fire (only ONE `-R` residue). GREEN: the unbalanced-quote leg
        # (odd `'` count + a residual `-R`) forces `_repo_flag_unparseable` → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 --comment 'it'\\''s -R baz/qux done'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- A-3: glued short-flag value hides a REPO_ARG poison ---

    def test_blocks_glued_short_flag_repo_poison(self):
        # `-c'evidence -R baz/qux x'` (glued short flag): today the value is NOT
        # stripped (the pre-#824 separator required a space/`=`), so REPO_ARG=baz/qux
        # (SELF) → wrong-ALLOW. GREEN: the short flags strip a glued value → the poison
        # is removed → REPO_ARG empty → author read hits the cwd (FOREIGN) → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 -c'evidence -R baz/qux x'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_glued_short_flag_plain_self_close(self):
        # Control: a legit self-close with a glued `-c'plain'` comment (no poison)
        # still ALLOWS — the A-3 strip must not over-block a glued self-close. Passes
        # on BOTH base (glued not stripped, but no -R so REPO_ARG empty → cwd author
        # read is issue 3312 = SELF) and fixed (glued stripped → same).
        r = _run_with_gh_body("gh issue close 3312 -c'client confirmed'",
                              self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- C-1: reversed self-then-backslash-foreign mixed pair ---

    def test_blocks_reversed_self_then_backslash_foreign(self):
        # `gh issue close SELF --comment ok && \\gh issue close FOREIGN`: the `\\gh`
        # FOREIGN close is invisible to the narrow N_CLOSE (\\ not in the narrow class)
        # and the narrow ISSUE_NUM extractor → N_CLOSE=1 on SELF → carve-out `exit 0`
        # allows the pair (rc 0) today (is_close IS 1 via the #824 `\\`-wide _CLOSE_OPEN,
        # but the count/extraction stay narrow). GREEN: the WIDE N_CLOSE count (2) !=
        # narrow (1) → blank ISSUE_NUM → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 3312 --comment ok && \\gh issue close 3399 --comment ok",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- D-1: $(…) nested close erased from the stripped count ---

    def test_blocks_dollar_paren_nested_close(self):
        # `--comment "$(gh issue close FOREIGN)"`: bash runs the substitution (closing
        # FOREIGN). The #824 value-strip removes the whole `"$(…)"` comment value from
        # the stripped copy, so N_CLOSE no longer counts the nested close (pre-#824 the
        # `(` in the count class caught it) → carve-out `exit 0` on SELF (rc 0) today.
        # GREEN: the `$(` HAS_INTERP guard on the ORIGINAL $CMD blanks ISSUE_NUM → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "$(gh issue close 3399)"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_unquoted_dollar_paren_nested_close(self):
        # The unquoted `--comment $(gh issue close FOREIGN)` form blocks too (the `$(`
        # guard reads the ORIGINAL $CMD, so quoting is irrelevant).
        r = _run_with_gh_body(
            "gh issue close 3312 --comment $(gh issue close 3399)",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- flag-first ISSUE_NUM extraction (#824 review Carve-out preservation, 🔵) ---

    def test_flag_first_reason_self_close_extracts_number(self):
        # PIN (passes on base AND fixed): `gh issue close --reason done <SELF>` — the
        # value-strip removes `--reason done`, so the number after it is extracted as
        # the (correct) close target ISSUE_NUM → the SELF carve-out fires → ALLOW. Locks
        # the correct-target widening so a future extraction change cannot regress it.
        r = _run_with_gh_body("gh issue close --reason done 3312 --comment ok",
                              self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestReviewFindings2_824(TestCase):
    """#824 adversarial-review #2 findings (salvaged Fable review #2). The review-#1
    fix round (`_strip_value_flags` operator-bounded arms, `\\`-wide _CLOSE_OPEN, the
    `$(`/backtick HAS_INTERP guards) closed A-1/A-3/D-1 but left/opened five more:

      N-1 (wrong-ALLOW): an EVEN-parity escaped-quote value (`--comment "say \\"hi\\"
          -R x/y"` / the `'"'"'` idiom) terminates the value arm EARLY, leaving a
          `-R x/y` residue → REPO_ARG poison. The odd-count belt (A-2) misses even
          parity. FIX: pre-delete the three escaped-quote idioms before the value arms.
      N-2 (over-BLOCK regression): a MULTI-LINE quoted comment + a real `-R` — the
          per-line sed left a lone quote on line 2 → odd count → leg (c) over-blocked.
          FIX: `sed -Ez` spans the whole buffer.
      N-3 (over-BLOCK regression): the blanket `$(` guard blocked the legit
          `--comment "$(cat …)"` / `$(date)` idiom. FIX: narrow to a `gh`-carrying
          substitution.
      N-4 (wrong-ALLOW, bash>=5.3): a funsub `${ gh issue close …; }` runs a nested
          close with NO `$(` → the `$(` guard misses it. FIX: a `${[ |]` funsub guard.
      N-5 (wrong-ALLOW): a QUOTED command word `"gh" issue close` chained after a self
          close — the WIDE count misses it (`gh"` breaks `gh[[:space:]]+`). FIX: a
          DE-QUOTED narrow count; a hidden close raises it above N_CLOSE → blank.

    All run under a REDUCED-authority cwd with a SELF-vs-FOREIGN author fixture so a
    wrong target read is a wrong-ALLOW today; the over-block regressions (N-2/N-3)
    assert the legit close is ALLOWED (rc 0), FAILING on the review-#1 code."""

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    # --- N-1: even-parity escaped-quote REPO_ARG poison ---

    def test_blocks_even_parity_escaped_double_quote_repo_poison(self):
        # `--comment "say \\"hi\\" -R baz/qux"`: bash's argv is ONE comment value
        # `say "hi" -R baz/qux`, but the hook text carries the escaped `\\"`. The
        # `"[^"]*"` arm stops at the FIRST (escaped) `"` → residue `hi\\" -R baz/qux"`
        # → REPO_ARG=baz/qux (SELF) while the close targets the cwd (FOREIGN) →
        # wrong-ALLOW (rc 0) today (EVEN quote count, so the odd-count belt is silent).
        # GREEN: the escaped-quote pre-delete collapses `"say \\"hi\\" -R baz/qux"` to
        # `"say hi -R baz/qux"` → the whole value strips → REPO_ARG empty → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 4 --comment "say \\"hi\\" -R baz/qux"',
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_even_parity_single_quote_idiom_repo_poison(self):
        # The `'"'"'` single-quote-in-single-quote idiom, EVEN parity: the residue
        # `-R baz/qux` survives the value arm today → REPO_ARG poison (rc 0). GREEN:
        # the `'"'"'` pre-delete removes it → the value strips whole → BLOCK.
        r = _run_with_gh_body(
            "gh issue close 4 --comment 'a'\"'\"'b -R baz/qux x'\"'\"'y'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- N-2: multi-line quoted comment + real -R over-block regression ---

    def test_allows_multiline_single_quoted_comment_with_real_repo(self):
        # A LEGIT self-close: real `-R baz/qux` (SELF) + a MULTI-LINE single-quoted
        # comment. Review-#1's per-line sed cannot span the newline, so `'[^']*'`
        # eats `'fixed on PROD` and leaves a lone `'` on line 2 → odd count → leg (c)
        # over-BLOCKS (rc 2) today. GREEN: `sed -Ez` spans the newline → the whole
        # comment strips → one real `-R` → ALLOW (rc 0).
        r = _run_with_gh_body(
            "gh issue close 4 -R baz/qux --comment 'fixed on PROD\nevidence: log 42'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_multiline_double_quoted_comment_with_real_repo(self):
        # Same class, double-quoted multi-line comment.
        r = _run_with_gh_body(
            'gh issue close 4 -R baz/qux --comment "fixed on PROD\nevidence: log 42"',
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- N-3: blanket $( guard over-blocks a legit substitution ---

    def test_allows_cat_substitution_comment(self):
        # A LEGIT self-close whose comment is `"$(cat /tmp/note.md)"` (NO gh command).
        # Review-#1's blanket `$(` guard blanks ISSUE_NUM → over-BLOCK (rc 2) today.
        # GREEN: the narrowed guard (`$(` … `gh `) does not fire → self carve-out → ALLOW.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "$(cat /tmp/note.md)"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_date_substitution_comment(self):
        # Same class, a `$(date +%F)` comment substitution.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "$(date +%F)"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_still_blocks_gh_substitution_after_narrowing(self):
        # CONTROL (blocks on BOTH review-#1 and the fix): the narrowed `$(` guard must
        # STILL catch a `$(gh issue close FOREIGN)` nested smuggle — the narrowing must
        # not reopen D-1. Regression-guard for the N-3 fix.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "$(gh issue close 3399)"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- N-4: bash>=5.3 funsub nested close ---

    def test_blocks_funsub_nested_close(self):
        # `--comment "${ gh issue close 3399; }"`: a bash>=5.3 command-substitution
        # funsub runs the nested FOREIGN close with NO `$(`, so review-#1's `$(` guard
        # misses it and the value-strip erases it → self carve-out `exit 0` (rc 0)
        # today. GREEN: the `${[ |]` funsub guard sets HAS_INTERP → blank → BLOCK. (The
        # static grep fires regardless of the box's bash version.)
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "${ gh issue close 3399; }"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_ordinary_param_expansion_in_comment(self):
        # CONTROL (allows on BOTH): an ORDINARY parameter expansion `${STEP}` in a
        # comment is NOT a funsub (no space/`|` after `${`) → the funsub guard must not
        # over-block a legit self-close carrying it.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "progress ${STEP} done"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- N-5: de-quoted count catches a chained QUOTED command word ---

    def test_blocks_dequoted_chained_double_quoted_command_word(self):
        # `gh issue close SELF --comment ok && "gh" issue close FOREIGN`: the quoted
        # `"gh"` breaks the WIDE count's `gh[[:space:]]+` (on the closing `"`), so
        # N_CLOSE_WIDE == N_CLOSE == 1 → the self carve-out allows the pair (rc 0)
        # today. GREEN: the DE-QUOTED narrow count sees `gh issue close 3399` → 2 !=
        # N_CLOSE(1) → blank ISSUE_NUM → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && "gh" issue close 3399',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_dequoted_chained_single_quoted_command_word(self):
        # Same class, single-quoted `'gh'` command word.
        r = _run_with_gh_body(
            "gh issue close 3312 --comment ok && 'gh' issue close 3399",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestReviewFindings3_824(TestCase):
    """#824 adversarial-review #3 (Fable pass over the review-#2 fix, commit 57ed31c8).
    The review-#2 fix closed N-1..N-5 but the N-3 `$(` narrowing + the N-4 funsub guard
    admitted new escapes; each fixture RED-locks one:

      F1 (wrong-ALLOW REGRESSION): the narrowed `$(` guard requires `gh` INSIDE the
          substitution, so a DECOY/EMPTY substitution glued to a real close
          (`$(:)gh issue close FOREIGN`, `$()gh …`) rides a self carve-out — the old
          blanket `$(` guard blocked it. FIX: add `)`/`}`/`{` to the de-quoted count
          boundary class so the glued close raises N_CLOSE_DEQUOTED > N_CLOSE.
      F2 (wrong-ALLOW): the same empty-glue via `${x}gh issue close FOREIGN` (an unset
          param expands empty and glues to `gh`). Same de-quoted-boundary fix.
      F3 (weakened NEW detection): the funsub guard is line-based, so `${` at
          end-of-line (a NEWLINE as the funsub whitespace) is missed. FIX: `grep -z`.
      F4 (over-BLOCK): the funsub guard is not narrowed to `gh` (asymmetric with N-3),
          so a legit close whose comment merely mentions `${ }` is refused. FIX:
          require a `gh` command inside the funsub (`\\$\\{[[:space:]|][^}]*gh[[:space:]]`).
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    # --- F1: empty/decoy $( substitution glued to a chained foreign close ---

    def test_blocks_empty_colon_substitution_glued_chained_close(self):
        # `SELF --comment ok && $(:)gh issue close FOREIGN`: `$(:)` expands empty and
        # glues onto `gh`, so the FOREIGN close runs. The narrowed `$(` guard (needs
        # `gh` inside) misses the decoy, and `)gh` is in no close-count boundary → all
        # counts stay 1 → self carve-out `exit 0` (rc 0) today. GREEN: `)` in the
        # de-quoted count boundary → N_CLOSE_DEQUOTED=2 != 1 → blank → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && $(:)gh issue close 999 --comment y',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_blocks_empty_paren_substitution_glued_chained_close(self):
        # The `$()` empty-substitution variant of F1.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && $()gh issue close 999',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- F2: empty ${x} param expansion glued to a chained foreign close ---

    def test_blocks_empty_param_expansion_glued_chained_close(self):
        # `SELF --comment ok && ${x}gh issue close FOREIGN`: an unset `${x}` expands
        # empty and glues onto `gh` → the FOREIGN close runs. No `$(`/backtick/funsub-
        # whitespace, so no interp guard; `}gh` is in no count boundary → rc 0 today.
        # GREEN: `}` in the de-quoted count boundary → N_CLOSE_DEQUOTED=2 → blank → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && ${x}gh issue close 999 --comment y',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- F3: funsub guard misses a newline immediately after ${ ---

    def test_blocks_funsub_nested_close_with_newline_after_brace(self):
        # `--comment "${<newline>gh issue close FOREIGN;<newline>}"`: the funsub
        # whitespace IS the newline, so `${` sits at end-of-line and the line-based
        # guard's char-class-after-`${` never matches → rc 0 today. GREEN: `grep -z`
        # spans the newline → the funsub guard fires → blank → BLOCK.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "${\ngh issue close 999;\n}"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- F4: funsub guard over-blocks a gh-less ${ } comment ---

    def test_allows_gh_less_funsub_text_in_comment(self):
        # A LEGIT self-close whose comment merely mentions `${ }` (NO gh command inside)
        # is over-BLOCKED (rc 2) today by the un-narrowed funsub guard. GREEN: the guard
        # requires a `gh` command inside the funsub → this no longer fires → ALLOW (rc 0).
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "use bash ${ } funsub"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- controls (pass on BOTH review-#2 and the review-#3 fix) ---

    def test_narrowed_dollar_paren_still_allows_cat_substitution(self):
        # CONTROL: the de-quoted-boundary change must NOT reopen N-3's over-block — a
        # legit `--comment "$(cat …)"` self-close still ALLOWS (its value is stripped,
        # so the de-quoted count never sees a `)gh` adjacency).
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "$(cat /tmp/note.md)"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_funsub_gh_smuggle_still_blocks_after_narrowing(self):
        # CONTROL: the funsub narrowing must still catch a REAL `${ gh issue close; }`
        # smuggle — the F4 fix must not let the N-4 case through.
        r = _run_with_gh_body(
            'gh issue close 3312 --comment "${ gh issue close 999; }"',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)


class TestPythonSegmenter837(TestCase):
    """#837 — the sed/grep EXTRACTION+COUNTING+DETECTION layer is replaced by the
    python quote/backslash-aware segmenter `hooks/close_guard_segment.py`, closing the
    four STRUCTURAL residuals a regex fundamentally cannot reach (the #824 review-#2
    N-6/N-7 comment + the A-4 / REPO_ARG-in-quoted-arg shapes). Every RED below is a
    genuine wrong-ALLOW on the pre-#837 hook (rc 0), GREEN (BLOCK, rc 2) after the
    segmenter lands; the controls pass on BOTH.

    `_GH_NUMBER_SCOPED_AUTHOR` reads issue 3312 as SELF, everything else FOREIGN;
    `_GH_REPO_SCOPED_AUTHOR` reads repo baz/qux as SELF, every other repo FOREIGN —
    so a close that rides a carve-out against the WRONG target is a wrong-ALLOW today.
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    # --- N-6: a STANDALONE quoted/backslashed/aliased command word bypasses the
    # front gate entirely (is_close=0 → exit 0) on the pre-#837 hook. The segmenter
    # de-quotes the command word to a clean `gh` close → a foreign close BLOCKS. ---

    def test_n6_standalone_double_quoted_command_word(self):
        r = run('"gh" issue close 3399', self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("branch-merge", r.stderr)

    def test_n6_standalone_single_quoted_command_word(self):
        r = run("'gh' issue close 3399", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fork-no-merge", r.stderr)

    def test_n6_standalone_backslash_in_command_word(self):
        # `g\h` — bash strips the backslash and runs `gh`.
        r = run("g\\h issue close 3399", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n6_standalone_backslash_in_close_keyword(self):
        # `clo\se` — the intra-keyword backslash the `*close*` prefilter would miss;
        # the de-backslashed prefilter still routes it to the segmenter, which
        # de-quotes it to `close`.
        r = run("gh issue clo\\se 3399", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n6_standalone_quoted_close_keyword(self):
        r = run('gh issue "close" 3399', self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n6_standalone_absolute_path_to_gh(self):
        r = run("/usr/bin/gh issue close 3399", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n6_ansi_c_obfuscated_subcommand_blocks(self):
        # `gh issue $'close'` — shlex yields `$close` (not the literal `close`); the
        # ANSI-C / expansion-obfuscated subcommand fails CLOSED (the load-bearing
        # invariant: an unresolvable command/subcommand word blocks).
        r = run("gh issue $'close' 3399", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n6_allows_self_close_via_absolute_path(self):
        # CONTROL (passes on BOTH): a LEGIT self-close invoked as `/usr/bin/gh` must
        # still be ALLOWED — the segmenter recognises the basename `gh`.
        r = run("/usr/bin/gh issue close 1408 --comment 'fixed on fork'",
                self.fork, me="kvaskodev", author="kvaskodev")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- N-7: a non-shell interpreter hides a nested close the shell-only HAS_INTERP
    # enumeration misses. On the pre-#837 hook the self close 3312 rides while the
    # python payload's foreign close is invisible (rc 0). ---

    def test_n7_python_interpreter_hides_nested_close(self):
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && '
            'python3 -c \'import os;os.system("gh issue close 3399")\'',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n7_node_interpreter_hides_nested_close(self):
        r = _run_with_gh_body(
            'gh issue close 3312 --comment ok && node -e \'run("gh issue close 3399")\'',
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_n7_allows_python_without_a_gh_close(self):
        # CONTROL (passes on BOTH): a `python3 -c` that merely calls `.close()` (no
        # `gh issue close`) is NOT a close → allowed. The interpreter check must key
        # on the `gh issue close` PHRASE, not the bare word `close`.
        r = run("python3 -c 'db.close()'", self.branch,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- A-4: a `-c` INSIDE a quoted argument makes the sed value-strip's single-quote
    # arm span a REAL top-level close, erasing it from the count (rc 0 today). The
    # segmenter tokenises each segment, so both closes are counted → BLOCK. ---

    def test_a4_quoted_c_flag_erases_a_top_level_close(self):
        r = _run_with_gh_body(
            "gh issue close 3312 ; echo \"y -c '\" ; gh issue close 3399 ; echo \"'z\"",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a4_allows_self_close_with_harmless_trailing_echo(self):
        # CONTROL (passes on BOTH): a legit single self-close followed by a harmless
        # echo must still ALLOW — the segmenter must not over-count a non-close echo.
        r = _run_with_gh_body(
            "gh issue close 3312 --comment ok ; echo 'harmless note'",
            self.branch, _GH_NUMBER_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- REPO_ARG-in-quoted-arg: a balanced-quote `-R x/y` inside a NON-value argument
    # poisons the whole-command REPO_ARG grep (rc 0 today). The segmenter reads -R only
    # from the close segment's own tokens → the poison is neutralised → BLOCK. ---

    def test_poison_balanced_quote_repo_flag_in_echo(self):
        # The ticket's exact shape: `-R baz/qux` (SELF in the fixture) inside a
        # separate `echo` argument; the close targets the cwd repo (FOREIGN).
        r = _run_with_gh_body(
            "gh issue close 4 && echo 'foo -R baz/qux'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_poison_balanced_quote_repo_flag_via_semicolon(self):
        r = _run_with_gh_body(
            "gh issue close 4 ; echo 'ref -R baz/qux'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_poison_control_real_repo_flag_in_close_wins(self):
        # A real `-R baz/qux` (SELF) ON THE CLOSE plus a stray `-R other/repo` in a
        # later echo must ALLOW — the segmenter reads the CLOSE segment's own -R
        # (baz/qux), never the echo's. This ALSO removes a pre-#837 over-BLOCK: the
        # old `>=2 -R tokens` belt counted the echo's `-R` and refused the exemption
        # (rc 2, a false-block); the segment-scoped read allows the legit self-close.
        r = _run_with_gh_body(
            "gh issue close 4 -R baz/qux --comment ok ; echo 'note -R other/repo'",
            self.branch, _GH_REPO_SCOPED_AUTHOR)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestFalsePositives873(TestCase):
    """#873 — three live false-positive shapes from a montalu3 session (2026-09-05):
    (1) a heredoc whose CONTENT contains an apostrophe + the word "close",
    (2) a read-only poll loop `$(gh issue view ...)` / `$(gh api ...)`,
    (3) a `gh api .../actions/runs/.../jobs --jq ...conclusion...` with no issue cmd.

    All three should exit 0 (ALLOW) on ANY authority. They close nothing.
    """

    def setUp(self):
        self.fork = _cwd_with_authority("fork-no-merge")
        self.branch = _cwd_with_authority("branch-merge")

    # --- FP1: heredoc body with apostrophe + "close" ---

    def test_allows_heredoc_body_with_apostrophe_and_close_word(self):
        cmd = "cat > /tmp/body.md <<'EOF'\nTiket sa zavrie (close) po akceptacii klient's.\nEOF"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_heredoc_body_with_unbalanced_double_quote(self):
        cmd = 'cat > /tmp/msg.md <<\'EOF\'\nNeed to "close this properly.\nEOF'
        r = run(cmd, self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- FP2: read-only poll loops with $(gh issue view / gh api) ---

    def test_allows_bash_c_poll_with_gh_issue_view_subst(self):
        # The literal "CLOSED" (case-insensitive -> "closed") passes the prefilter
        # and reaches the segmenter, where $(gh issue view ...) must NOT trigger.
        cmd = ("bash -c 'while :; do s=$(gh issue view 5560 --json state "
               "--jq .state); [ \"$s\" = \"CLOSED\" ] && break; done'")
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_top_level_poll_with_gh_issue_view_subst(self):
        # Same shape at top level with "CLOSED" in the comparison.
        cmd = ('while :; do s=$(gh issue view 5560 --json state --jq .state); '
               '[ "$s" = "CLOSED" ] && echo "CLOSED" && break; done')
        r = run(cmd, self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- FP3: gh api actions/runs/jobs with jq conclusion ---

    def test_allows_gh_api_runs_jobs_with_conclusion_jq(self):
        cmd = ("s=$(gh api repos/o/r/actions/runs/123/jobs --jq "
               "'if .status==\"completed\" then \"TERMINAL\" "
               "elif ([.jobs[]?|select(.conclusion==\"failure\")]"
               "|length)>0 then \"JOBFAIL\" else \"PENDING\" end')")
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_gh_api_runs_jobs_simple(self):
        cmd = "s=$(gh api repos/o/r/actions/runs/123/jobs --jq '.jobs[].name')"
        r = run(cmd, self.branch, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- Negative controls: real closes must STILL block ---

    def test_still_blocks_plain_gh_issue_close(self):
        r = run("gh issue close 123", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_close_with_comment(self):
        r = run('gh issue close 123 -r completed -c "fixed"', self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_api_patch_close(self):
        r = run("gh api repos/o/r/issues/123 -X PATCH -f state=closed",
                self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_close_after_and(self):
        r = run('echo "done" && gh issue close 456', self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_bash_c_with_real_close(self):
        r = run("bash -c 'gh issue close 789'", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_close_in_substitution(self):
        r = run("result=$(gh issue close 999)", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_close_in_backtick(self):
        r = run("result=`gh issue close 300`", self.fork,
                me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_executed_heredoc_with_close(self):
        cmd = "bash <<'EOF'\ngh issue close 555\nEOF"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- Review F1: quote-split close in substitution.  `cl""ose` does NOT
    # contain the substring "close" so the shell PREFILTER exits 0 before the
    # segmenter runs — this is a pre-existing residual on BOTH old and new code,
    # NOT a #873 regression. The segmenter's de-quoted scan would catch it IF the
    # prefilter let it through, but the prefilter is a cheaper gate. Accepted. ---

    # --- Review F2: herestring/quoted-<< must not blank following lines ---

    def test_still_blocks_close_after_herestring(self):
        # A `<<<` herestring followed by a real close on the next line.
        cmd = 'grep -q x <<<"$STATUS"\ngh issue close 123'
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_still_blocks_close_after_quoted_heredoc_string(self):
        # A `<<EOF` inside a quoted string, followed by a real close.
        cmd = "echo 'send <<EOF'\ngh issue close 123"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- Review F3: PATCH close in executed heredoc must block ---

    def test_still_blocks_patch_close_in_executed_heredoc(self):
        cmd = "bash <<'EOF'\ngh api repos/o/r/issues/5 -X PATCH -f state=closed\nEOF"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- Review F4: python heredoc executes, close must block ---

    def test_still_blocks_python_heredoc_with_close(self):
        cmd = "python3 <<'PY'\nimport os\nos.system(\"gh issue close 123\")\nPY"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 2, r.stderr)

    # --- Data heredoc mentioning close literally must NOT block ---

    def test_allows_data_heredoc_mentioning_close(self):
        cmd = "cat <<'EOF'\ngh issue close 555\nEOF"
        r = run(cmd, self.fork, me="someoneelse", author="zbynekdrlik")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    main()
