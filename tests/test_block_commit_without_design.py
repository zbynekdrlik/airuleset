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
        self._closed = {}   # repo-name -> {issue-number-str, ...}
        self._write_fake_gh()

    def _write_fake_gh(self):
        # Repo-aware (M8, adversarial review): `gh issue view` is invoked
        # with `cwd=<work_cwd>` by `_gh_issue_state`, so THIS script's own
        # process cwd at run time IS that resolved directory -- resolve
        # "which repo am I being asked about" the same way `repo_name_for`
        # does (git remote get-url origin, last path segment), so a test
        # can prove the #206 exemption is queried against the RESOLVED
        # work repo, not the raw payload cwd. Default (no repo given to
        # closed_issues()) is "airuleset", matching every pre-existing test
        # in this file, which never leaves self.repo.
        # A PYTHON script (not bash) -- avoids sed/awk's own $-inside-
        # double-quotes traps entirely. `git remote get-url origin`, run
        # with THIS process's own cwd (which IS the resolved `work_cwd`
        # `_gh_issue_state` invokes `gh` with), resolved the same
        # last-path-segment way `notify.repo_name_for()` does.
        fake_gh = self.bindir / "gh"
        closed_by_repo = {r: sorted(n) for r, n in self._closed.items()}
        fake_gh.write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys\n"
            "CLOSED = %r\n"
            "if len(sys.argv) >= 4 and sys.argv[1:3] == ['issue', 'view']:\n"
            "    n = sys.argv[3]\n"
            "    out = subprocess.run(['git', 'remote', 'get-url', 'origin'],\n"
            "                         capture_output=True, text=True)\n"
            "    url = out.stdout.strip().rstrip('/')\n"
            "    if url.endswith('.git'):\n"
            "        url = url[:-4]\n"
            "    repo = url.replace(':', '/').split('/')[-1]\n"
            "    if n in CLOSED.get(repo, []):\n"
            "        print('CLOSED')\n"
            "    else:\n"
            "        print('OPEN')\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n" % closed_by_repo)
        fake_gh.chmod(0o755)

    def closed_issues(self, *nums, repo="airuleset"):
        self._closed.setdefault(repo, set())
        self._closed[repo] |= {str(n) for n in nums}
        self._write_fake_gh()

    def mark(self, issue, repo="airuleset"):
        os.environ["HOME"] = str(self.home)
        dg.write_marker(repo, issue, "https://x/issues/%s#issuecomment-1" % issue)

    def _other_repo(self, remote="https://github.com/zbynekdrlik/dantesync.git"):
        other = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-other-"))
        self.addCleanup(shutil.rmtree, other, True)
        _git(other, "init", "-q", "-b", "main")
        _git(other, "remote", "add", "origin", remote)
        return other

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

    def test_the_exemption_is_checked_against_the_resolved_work_repo(self):
        # M8 (adversarial review): dg.required_refs(missing, work_cwd) must
        # query the #206 CLOSED-exemption against the RESOLVED repo (from
        # an inline cd), never the raw payload cwd -- #61 is CLOSED in
        # dantesync but still OPEN in airuleset (self.repo/payload cwd); a
        # commit landing in dantesync via `cd` must see the exemption
        # apply, proving the resolved directory (not payload cwd) is what
        # the gh query actually runs against.
        other = self._other_repo()
        self.closed_issues(61, repo="dantesync")
        cmd = 'cd %s && git commit -m "fix: thing (#61) [green]"' % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_exemption_does_not_leak_from_the_payload_repo(self):
        # the mirror check: #61 CLOSED in airuleset (payload cwd) must NOT
        # exempt a commit that's actually landing in dantesync (via cd),
        # where #61 is still OPEN -- proves the query uses work_cwd, not a
        # stale/cached payload-cwd answer.
        other = self._other_repo()
        self.closed_issues(61, repo="airuleset")
        cmd = 'cd %s && git commit -m "fix: thing (#61) [green]"' % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("61", r.stderr)


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


class TestRepoResolvedFromInlineCdPrefix(_Base):
    """#187/#220 -- a worker dispatched with session cwd = repo A, running
    `cd /path/to/repo-B && git commit ...`, must be gated against repo B
    (the repo the commit is actually landing in), not repo A (the payload's
    static cwd). `_other_repo()` is inherited from `_Base`."""

    def test_marker_under_the_cd_target_repo_passes(self):
        other = self._other_repo()
        self.mark(61, repo="dantesync")
        cmd = 'cd %s && git commit -m "fix: thing (#61) [green]"' % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_marker_only_under_the_stale_session_cwd_repo_still_blocks(self):
        # the marker exists under "airuleset" (self.repo's remote) -- the
        # OLD cwd-only resolution would have accepted this; the fix must
        # not, since the commit is actually landing in dantesync.
        other = self._other_repo()
        self.mark(61, repo="airuleset")
        cmd = 'cd %s && git commit -m "fix: thing (#61) [green]"' % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dantesync", r.stderr)

    def test_no_cd_prefix_still_resolves_from_payload_cwd_unchanged(self):
        # no `cd` anywhere in the command -- must behave exactly as before.
        self.mark(41)
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_cd_to_a_non_repo_path_falls_back_to_payload_cwd_and_still_gates(self):
        # a `cd` to something that is NOT a git repo must never be trusted --
        # falls back to the payload cwd, same as having no `cd` at all. NO
        # marker is placed anywhere: this must still BLOCK (proves the
        # fallback preserves the gate's normal function) -- a prior version
        # of this test placed a marker under the payload repo first, which
        # made a mutant that drops the is-a-repo guard entirely pass for the
        # SAME observable reason as the correct code (tautological; caught
        # by adversarial review).
        not_a_repo = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-notrepo-"))
        self.addCleanup(shutil.rmtree, not_a_repo, True)
        cmd = 'cd %s && git commit -m "fix: thing (#41) [green]"' % not_a_repo
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_a_cd_mention_inside_a_quoted_argument_is_never_trusted(self):
        # adversarial-review finding: `;cd /path` embedded inside a QUOTED
        # argument (e.g. an echo string) must never be read as a real
        # statement boundary -- an attacker-influenced ticket/commit body
        # could otherwise spoof the resolved repo and let the gate check
        # an unrelated repo's marker namespace instead of the real one.
        other = self._other_repo()
        self.mark(61, repo="dantesync")   # a marker exists there, but must
                                          # NOT be reachable via a quoted
                                          # mention of its path
        cmd = ('echo "note: see readme;cd %s" && '
               'git commit -m "fix: thing (#61) [green]"') % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset", r.stderr)

    def test_a_cd_mention_inside_a_heredoc_commit_message_body_is_never_trusted(self):
        # CRITICAL adversarial-review finding: a `cd /path && ...` literal
        # sitting inside a HEREDOC BODY (not a quoted span -- the earlier
        # quote-stripping hardening cannot see it) must never be read as a
        # real statement boundary. Reachable via ordinary commit-message
        # prose (e.g. quoting a runbook recipe the ticket described), and
        # because `dg.required_refs(missing, work_cwd)` ALSO uses the
        # resolved directory for the #206 gh-issue-state check, this could
        # silently DISABLE the design gate entirely if the spoofed repo
        # happens to have the same issue number already closed.
        other = self._other_repo()
        self.mark(61, repo="dantesync")   # a marker exists there, but must
                                          # NOT be reachable via a cd merely
                                          # quoted inside a heredoc body
        cmd = ("cat > /tmp/airuleset-test-msg.md <<'EOF'\n"
               "fix: thing (#61) [green]\n"
               "\n"
               "Reported by the user; their runbook says:\n"
               "  ./build.sh && cd %s && ./deploy.sh\n"
               "EOF\n"
               "git add -A && git commit -F /tmp/airuleset-test-msg.md"
               ) % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset", r.stderr)

    def test_a_quoted_cd_path_with_spaces_still_resolves(self):
        # adversarial-review finding: the quote-stripping hardening (since
        # replaced by the string-start anchor) regressed the LEGITIMATE
        # case -- a `cd "/path with spaces"` must still resolve correctly,
        # since quoting a path is the recommended way to handle one.
        other = Path(tempfile.mkdtemp(prefix="airuleset-commitgate-with space-"))
        self.addCleanup(shutil.rmtree, other, True)
        _git(other, "init", "-q", "-b", "main")
        _git(other, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/dantesync.git")
        self.mark(61, repo="dantesync")
        cmd = 'cd "%s" && git commit -m "fix: thing (#61) [green]"' % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_cd_after_git_commit_in_the_command_is_ignored(self):
        # a `cd` appearing AFTER the git commit invocation must never be
        # trusted -- it cannot be what the shell actually did before the
        # commit ran.
        other = self._other_repo()
        self.mark(61, repo="dantesync")   # exists there, must not be reached
        cmd = ('git commit -m "fix: thing (#61) [green]" && cd %s'
              ) % other
        r = self.run_hook(cmd, cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset", r.stderr)


# --------------------------------------------------------------------------- #
# #310 -- a blocked compound `cat > path <<EOF ... && git commit -F path`
# must never leave a stale file at `path` for a later bare retry to silently
# consume. See design_gate.py's own #310 section + the design comment on the
# ticket for the full incident.
# --------------------------------------------------------------------------- #

class TestStaleMsgfileQuarantine(_Base):

    COMPOUND_41 = (
        "cat > msg.txt <<'EOF'\n"
        "fix(hook): thing (#41) [green]\n"
        "EOF\n"
        "git commit -F msg.txt"
    )

    def _seed(self, content="STALE PRE-EXISTING CONTENT\n"):
        p = self.repo / "msg.txt"
        p.write_text(content)
        return p

    def test_a_blocked_compound_quarantines_the_stale_write_target(self):
        p = self._seed()
        r = self.run_hook(self.COMPOUND_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(p.exists(),
                          "the stale content must no longer sit at the "
                          "original path once the compound that meant to "
                          "rewrite it gets blocked")
        siblings = [f for f in self.repo.iterdir()
                    if f.name.startswith("msg.txt.stale-")]
        self.assertEqual(len(siblings), 1, siblings)
        self.assertEqual(siblings[0].read_text(), "STALE PRE-EXISTING CONTENT\n")

    def test_the_block_message_names_the_quarantine(self):
        self._seed()
        r = self.run_hook(self.COMPOUND_41)
        self.assertIn("msg.txt", r.stderr)
        self.assertIn("quarantin", r.stderr.lower())

    def test_a_bare_retry_against_the_quarantined_path_fails_loud(self):
        # the whole point: a later, differently-shaped retry that assumes
        # the earlier cat already wrote the file must NOT silently succeed
        # with the stale content -- prove it end to end against a REAL git
        # commit, not just the hook's own verdict.
        self._seed()
        r = self.run_hook(self.COMPOUND_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
        retry = subprocess.run(
            ["git", "-C", str(self.repo), "commit", "--allow-empty", "-F", "msg.txt"],
            capture_output=True, text=True, env=env)
        self.assertNotEqual(retry.returncode, 0,
                             "a bare retry against the quarantined path must "
                             "fail -- never silently commit stale content")

    def test_a_marked_issue_is_never_quarantined_since_no_block_occurs(self):
        self.mark(41)
        p = self._seed()
        r = self.run_hook(self.COMPOUND_41)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(p.exists())
        self.assertEqual(p.read_text(), "STALE PRE-EXISTING CONTENT\n")

    def test_a_path_only_committed_not_written_in_this_command_is_left_alone(self):
        # the LEGITIMATE two-step pattern this ticket itself recommends: the
        # msgfile was written correctly by an EARLIER, separate call, and
        # THIS command only consumes it via -F -- must survive a block
        # untouched, never mistaken for the same-command write-then-consume
        # shape.
        p = self._seed("a genuinely correct message (#41)\n")
        cmd = 'echo "context note (#41)" >/dev/null; git commit -F msg.txt'
        r = self.run_hook(cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertTrue(p.exists())
        self.assertEqual(p.read_text(), "a genuinely correct message (#41)\n")

    def test_no_stale_file_present_still_blocks_cleanly_with_nothing_to_quarantine(self):
        r = self.run_hook(self.COMPOUND_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.repo / "msg.txt").exists())
        siblings = [f for f in self.repo.iterdir()
                    if f.name.startswith("msg.txt.stale-")]
        self.assertEqual(siblings, [])
        self.assertNotIn("quarantin", r.stderr.lower())


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


class TestRejectReasonSurfacedInBlockMessage(_Base):
    """#414 -- fail LOUD: when design_gate.write_reject_reason() recorded
    WHY the worker's last posted comment didn't classify (from
    hooks/post-record-design-comment.sh), the block message here surfaces
    it, instead of the bare "no design comment posted yet"."""

    def reject(self, issue, reason, repo="airuleset"):
        os.environ["HOME"] = str(self.home)
        dg.write_reject_reason(repo, issue, reason, kind="design")

    def test_reject_reason_is_included_in_the_block_message(self):
        self.reject(41, "Architektúra: section missing: structure/topology")
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Architektúra", r.stderr)

    def test_no_reject_reason_still_blocks_with_the_generic_message(self):
        # never crashes / never omits the block when no reject reason
        # exists yet (e.g. no comment was posted at all).
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("41", r.stderr)

    def test_a_marked_issue_never_surfaces_a_stale_reject_reason(self):
        # a reject reason from an EARLIER failed attempt must not leak into
        # a block message for an issue that is now actually marked (i.e.
        # the commit passes cleanly, no reject text anywhere in stdout).
        self.reject(41, "Architektúra: section missing: structure/topology")
        self.mark(41)
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_reject_reason_for_a_different_issue_is_not_cross_reported(self):
        # #99's reject reason must never leak into #41's block message --
        # the STANDING guidance mentions "Architektúra" unconditionally
        # (locked by test_the_general_guidance_now_mentions_architektura_
        # and_triage below), so the discriminator here is the PER-REASON
        # section header, not the bare word.
        self.reject(99, "Architektúra: section missing: structure/topology")
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn("was rejected", r.stderr)
        self.assertNotIn("#99", r.stderr)

    def test_the_general_guidance_now_mentions_architektura_and_triage(self):
        # the STANDING guidance text (shown even with no reject reason
        # recorded yet) must mention the #414 requirement, not just the
        # pre-#414 root-cause/approach/alternative wording.
        r = self.run_hook(COMMIT_41)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Triage", r.stderr)
        self.assertIn("Architekt", r.stderr)


if __name__ == "__main__":
    main()
