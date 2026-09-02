"""Behaviour test for hooks/subagent-stop-check-lane-return.sh (#844).

A worktree-mode autopilot-worker's LAST act before returning is a durable
`LANE-RETURN:` comment (branch, head sha, worktree, evidence), so a lost
lane-completion notification (the #844 forced-compact residual) loses nothing.
This gate BLOCKS a worktree-mode return (a `branch:` naming a worktree branch,
NOT merged) with no LANE-RETURN marker for its issue(s), ONCE per (session,
repo#issue) — the same non-wedging bound as the design gate. A MERGED (full-flow)
return is NOT gated here (subagent-stop-check-design.sh owns that).

RED against the pre-#844 tree: hooks/subagent-stop-check-lane-return.sh does not
exist (so `bash <missing>` errors, returncode != 0, `blocked()` False → the
'blocked' assertions fail).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "subagent-stop-check-lane-return.sh"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                   # noqa: E402

# Split so no literal 40-char hex RUN trips block-sensitive-staging.sh.
SHA40 = ("014e9159ade4c55fb02a"
         "5eca823771bee26da677")

# A WORKTREE-mode return: a worktree branch + head sha, and NO merge_sha (the
# supervisor merges). `notify.parse_worker_evidence` reads merged=False.
WORKTREE_RETURN = (
    "issues: #41 fix money gate\n"
    "worktree: /home/x/.claude/worktrees/agent-abc\n"
    "branch: worktree-agent-abc — head " + SHA40 + "; wip backup pushed\n"
    "local_verify: pytest green")

# A MERGED (full-flow) return — NOT gated by THIS hook.
MERGED = ("issues: #41 x\nmerge_sha: " + SHA40 + "\nissue_state: #41=closed")


class _Base(TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-lanereturn-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        _prev_home = os.environ.get("HOME")

        def _restore_home():
            if _prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = _prev_home
        self.addCleanup(_restore_home)
        (self.home / ".claude").mkdir(parents=True)
        self.repo = Path(tempfile.mkdtemp(prefix="airuleset-lanereturn-repo-"))
        self.addCleanup(shutil.rmtree, self.repo, True)
        subprocess.run(["git", "-C", str(self.repo), "init", "-q", "-b", "main"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "https://github.com/zbynekdrlik/airuleset.git"],
                       check=True, capture_output=True)
        self.sid = "lanereturn-" + uuid.uuid4().hex
        self._state = Path("/tmp") / ("airuleset-lanereturn-" + self.sid)
        self.addCleanup(self._state.unlink, missing_ok=True)

    def mark(self, issue, repo="airuleset"):
        os.environ["HOME"] = str(self.home)
        dg.write_marker(repo, issue,
                        "https://x/issues/%s#issuecomment-9" % issue,
                        kind="lane-return")

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


class TestLaneReturnGate844(_Base):

    def test_worktree_return_without_lane_return_is_blocked(self):
        r = self.run_gate(WORKTREE_RETURN)
        self.assertTrue(self.blocked(r), (r.returncode, r.stdout, r.stderr))
        self.assertIn("41", r.stdout + r.stderr)
        self.assertIn("LANE-RETURN", r.stdout + r.stderr)

    def test_lane_return_marker_lets_the_worker_stop(self):
        self.mark(41)
        r = self.run_gate(WORKTREE_RETURN)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_only_one_block_per_session_and_issue(self):
        first = self.run_gate(WORKTREE_RETURN)
        self.assertTrue(self.blocked(first))
        second = self.run_gate(WORKTREE_RETURN)
        self.assertFalse(self.blocked(second),
                         "a second block would wedge a worker that genuinely "
                         "cannot post the comment")

    def test_a_merged_return_is_not_gated_here(self):
        # The full merge flow is subagent-stop-check-design.sh's job; a merged
        # return carries no worktree branch and merged=True, so this gate skips.
        r = self.run_gate(MERGED)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_non_autopilot_worker_is_skipped(self):
        r = self.run_gate(WORKTREE_RETURN, agent_type="general-purpose")
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))

    def test_a_return_without_a_worktree_branch_is_skipped(self):
        msg = ("issues: #41 x\nlocal_verify: green\n"
               "ready_for_review: #41 comment posted")   # a hand-off, not worktree
        r = self.run_gate(msg)
        self.assertFalse(self.blocked(r), (r.stdout, r.stderr))


if __name__ == "__main__":
    main()
