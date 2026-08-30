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
        user=None, comments="", verdict_fail=False):
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(os.environ)
    env["PATH"] = _fake_gh_dir() + os.pathsep + env.get("PATH", "")
    if user is not None:
        # #564: force the subprocess's own unix identity (getpass.getuser()
        # reads LOGNAME/USER) so `authority --stream-label` resolves a specific
        # stream — e.g. a rename target (montalu1) whose equivalents include the
        # legacy `montalu`. The authority PROFILE still comes from the cwd
        # marker (resolve_authority is marker-first), so this does not change
        # branch-merge/fork-no-merge selection.
        env["LOGNAME"] = user
        env["USER"] = user
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

    def test_allows_acceptance_close_of_ticket_with_legacy_prerename_stream_label(self):
        # #564 (the live residual): a montalu1 box (post base-stream rename)
        # closing its OWN needs-acceptance ticket that still carries the OLD
        # `stream:montalu` label during the transition. `authority --stream-label`
        # must emit BOTH equivalents and the hook must match ANY of them. RED
        # before the fix (only `stream:montalu1` emitted → old label never
        # matches → BLOCK). The subprocess runs as unix user montalu1 (a real
        # STREAM_RENAME_ALIASES target), so this uses the real rename table.
        labels = "stream:montalu needs-acceptance"
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp "
                "--comment 'client confirmed on PROD'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels,
                user="montalu1")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_acceptance_close_with_current_name_label_after_rename(self):
        # #564 regression lock: emitting MULTIPLE equivalents must not break the
        # exact CURRENT-name match — a montalu1 box closing a ticket carrying the
        # current `stream:montalu1` label still ALLOWS.
        labels = "stream:montalu1 needs-acceptance"
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp "
                "--comment 'client confirmed'",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels,
                user="montalu1")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_blocks_acceptance_close_with_foreign_stream_label_even_with_alias(self):
        # #564 fail-safe: a montalu1 box must NOT get the carve-out for a
        # DIFFERENT stream's ticket. `stream:david` is neither montalu1 nor its
        # legacy montalu equivalent → BLOCK, exactly like the pre-alias
        # foreign-label case.
        labels = "stream:david needs-acceptance"
        r = run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                self.branch, me=airuleset.MAINTAINER_GH_LOGIN,
                author=airuleset.MAINTAINER_GH_LOGIN, labels=labels,
                user="montalu1")
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


if __name__ == "__main__":
    main()
