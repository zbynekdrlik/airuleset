"""Behaviour test for hooks/subagent-stop-check-design.sh (#136, half 3/3).

Mirrors subagent-stop-check-run-card.sh's own shape exactly (same family,
same bound-by-construction reasoning): a worker that claims a real merge AND
closed issues, with no design marker for one of them, is blocked ONCE per
(session, repo#issue) and told to post the comment now. This is the
BACKSTOP for when hooks/block-commit-without-design.sh missed (not
installed on some box yet, a commit made outside the Bash tool, an
unjustified direct git operation) -- the ticket explicitly says NOT to add
a watchdog job for this (hook-shaped, not timer-shaped), so this is the
whole second line of defense.
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
HOOK = ROOT / "hooks" / "subagent-stop-check-design.sh"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                   # noqa: E402

# Split across two halves so the source carries no literal 40-char hex RUN
# -- block-sensitive-staging.sh flags any such run as a possible key/token
# (same reason test_run_card_enforcement.py's own SHA constants are split).
SHA40 = ("014e9159ade4c55fb02a"
         "5eca823771bee26da677")

MERGED = ("issues: #41 x\nmerge_sha: " + SHA40 + "\n"
          "issue_state: #41=closed")


def _git(repo, *args):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True, env=env)


class _GateBase(TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-designstop-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-designstop-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/zbynekdrlik/airuleset.git")
        self.sid = "designstop-%d" % os.getpid()
        self.addCleanup(lambda: [
            os.remove(f) for f in
            Path("/tmp").glob("airuleset-designgate-designstop-*")
            if os.path.exists(f)])

    def mark(self, issue, repo="airuleset", kind="design"):
        os.environ["HOME"] = str(self.home)
        dg.write_marker(repo, issue, "https://x/issues/%s#issuecomment-1" % issue,
                        kind=kind)

    def mark_all(self, issue, repo="airuleset"):
        for kind in dg.ALL_KINDS:
            self.mark(issue, repo, kind)

    def run_gate(self, msg, agent_type="autopilot-worker", cwd=None, sid=None):
        payload = {"session_id": sid or self.sid, "agent_id": "aG1",
                   "hook_event_name": "SubagentStop", "agent_type": agent_type,
                   "cwd": str(self.repo if cwd is None else cwd),
                   "last_assistant_message": msg}
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def blocked(self, r):
        if r.returncode != 0:
            return False
        try:
            return json.loads(r.stdout or "{}").get("decision") == "block"
        except ValueError:
            return False


class TestGateBlocksAMissingDesignComment(_GateBase):

    def test_a_merged_closed_ticket_with_no_marker_is_blocked(self):
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.returncode, r.stdout, r.stderr))
        self.assertIn("41", r.stdout + r.stderr)

    def test_a_marker_for_every_kind_lets_the_worker_stop(self):
        self.mark_all(41)
        r = self.run_gate(MERGED)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_only_one_block_per_session_and_issue(self):
        first = self.run_gate(MERGED)
        self.assertTrue(self.blocked(first))
        second = self.run_gate(MERGED)
        self.assertFalse(self.blocked(second),
                         "a second block would wedge a worker that genuinely "
                         "cannot post the comment")

    def test_a_different_issue_in_the_same_session_still_blocks(self):
        self.run_gate(MERGED)
        other = MERGED.replace("#41", "#42")
        self.assertTrue(self.blocked(self.run_gate(other)))


class TestGateChecksEveryKind(_GateBase):
    """#213 -- design alone is no longer sufficient; validated is ALSO
    required (and #214 will add reviewed to the same check)."""

    def test_design_only_still_blocks(self):
        self.mark(41, kind="design")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        self.assertIn("validated", r.stdout)

    def test_validated_only_still_blocks(self):
        self.mark(41, kind="validated")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        self.assertIn("design", r.stdout)

    def test_reviewed_only_still_blocks(self):
        # #214
        self.mark(41, kind="reviewed")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        self.assertIn("design", r.stdout)
        self.assertIn("validated", r.stdout)

    def test_design_and_validated_without_reviewed_still_blocks(self):
        # #214
        self.mark(41, kind="design")
        self.mark(41, kind="validated")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        reason = json.loads(r.stdout)["reason"]
        self.assertIn("missing: reviewed", reason)

    def test_the_missing_list_names_only_what_is_actually_missing(self):
        self.mark(41, kind="design")
        r = self.run_gate(MERGED)
        self.assertTrue(self.blocked(r))
        reason = json.loads(r.stdout)["reason"]
        self.assertIn("missing: validated", reason)
        self.assertNotIn("missing: design", reason)

    def test_fixing_only_one_missing_kind_does_not_get_a_second_block(self):
        # First block (both kinds missing) consumes the session's one nudge
        # for #41 -- marking validated afterward must NOT re-trigger it.
        first = self.run_gate(MERGED)
        self.assertTrue(self.blocked(first))
        self.mark_all(41)
        second = self.run_gate(MERGED)
        self.assertFalse(self.blocked(second))


class TestObsoleteClosedIssuesAreExcluded(_GateBase):
    """Adversarial-review finding (amplification, not new): an issue closed
    via STEP 0's documented `gh issue close --comment` path (OBSOLETE, never
    coded) can still appear in `issue_state:` as closed -- and now needs all
    THREE markers instead of one, none of which the documented close
    pattern ever produces. The evidence block's own `obsolete_closed:` line
    already names exactly which issues these are; the gate must exclude
    them from the required set."""

    def test_an_obsolete_closed_issue_is_excluded_from_the_required_set(self):
        msg = ("issues: #41 x, #99 y\nmerge_sha: " + SHA40 + "\n"
              "issue_state: #41=closed, #99=closed\n"
              "obsolete_closed: #99 closed-as-obsolete in STEP 0 with evidence")
        self.mark_all(41)
        r = self.run_gate(msg)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_a_non_obsolete_issue_is_still_required_in_the_same_message(self):
        msg = ("issues: #41 x, #99 y\nmerge_sha: " + SHA40 + "\n"
              "issue_state: #41=closed, #99=closed\n"
              "obsolete_closed: #99 closed-as-obsolete in STEP 0 with evidence")
        # #41 left unmarked this time -- must still block for #41.
        r = self.run_gate(msg)
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))
        self.assertIn("41", r.stdout)
        self.assertNotIn("#99", r.stdout)

    def test_obsolete_handed_off_is_also_excluded(self):
        # fork-no-merge's equivalent line for a foreign-authored obsolete
        # ticket it could not close itself.
        msg = ("issues: #41 x, #99 y\nmerge_sha: " + SHA40 + "\n"
              "issue_state: #41=closed, #99=closed\n"
              "obsolete_handed_off: #99 commented OBSOLETE, left OPEN")
        self.mark_all(41)
        r = self.run_gate(msg)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))


class TestRepoResolvedFromPrLine(_GateBase):
    """#220 -- a worker dispatched with session cwd = repo A (self.repo,
    origin airuleset), working repo B (dantesync), must be checked against
    repo B's markers -- the evidence block's own `pr: #<N> <url>` line names
    the real repo the PR landed against, and is the ground truth over the
    payload's static cwd."""

    PR_MSG = ("issues: #61 x\nmerge_sha: " + SHA40 + "\n"
              "pr: #63 https://github.com/zbynekdrlik/dantesync/pull/63\n"
              "issue_state: #61=closed")

    def test_markers_under_the_pr_line_repo_let_the_worker_stop(self):
        self.mark_all(61, repo="dantesync")
        r = self.run_gate(self.PR_MSG)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_markers_only_under_the_stale_session_cwd_repo_still_blocks(self):
        # OLD cwd-only resolution would have accepted this (repo "airuleset"
        # matches self.repo's remote) -- the fix must not, since the PR
        # line says the real repo is dantesync.
        self.mark_all(61, repo="airuleset")
        r = self.run_gate(self.PR_MSG)
        # this gate's own message never names the repo (unlike run-card's) --
        # the correctness proof IS that it stays blocked despite markers
        # existing under the wrong (cwd-derived) key.
        self.assertTrue(self.blocked(r), (r.stdout, r.stderr))

    def test_no_pr_line_still_resolves_from_cwd_unchanged(self):
        self.mark_all(41)
        r = self.run_gate(MERGED)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))


class TestGateStaysOutOfTheWay(_GateBase):

    def test_a_non_worker_subagent_is_ignored(self):
        r = self.run_gate(MERGED, agent_type="general-purpose")
        self.assertFalse(self.blocked(r))

    def test_an_unmerged_worker_is_ignored(self):
        r = self.run_gate("merge_sha: NOT MERGED — supervisor merges\n"
                          "issue_state: #41=OPEN")
        self.assertFalse(self.blocked(r))

    def test_a_worker_that_closed_nothing_is_ignored(self):
        r = self.run_gate("merge_sha: " + SHA40 + "\n"
                          "issue_state: #41=OPEN (auto-closes on merge)")
        self.assertFalse(self.blocked(r))

    def test_a_cwd_that_is_not_a_git_repo_is_ignored(self):
        d = Path(tempfile.mkdtemp(prefix="airuleset-designstop-nogit-"))
        self.addCleanup(shutil.rmtree, d, True)
        r = self.run_gate(MERGED, cwd=d)
        self.assertFalse(self.blocked(r))

    def test_garbage_stdin_is_ignored(self):
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(HOOK)], input="not json at all",
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))

    def test_empty_stdin_is_ignored(self):
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(HOOK)], input="",
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))

    def test_a_null_last_assistant_message_is_ignored(self):
        payload = {"session_id": self.sid, "agent_type": "autopilot-worker",
                   "cwd": str(self.repo), "last_assistant_message": None}
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                           capture_output=True, text=True, env=env)
        self.assertFalse(self.blocked(r))


if __name__ == "__main__":
    main()
