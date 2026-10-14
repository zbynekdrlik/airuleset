"""#1229 — the #998 sequential gate must count ANY worktree lane, not only an
autopilot-worker.

presenter@dev2 (14.10.2026) resolved to `sequential` (the #1137 default) yet its
plain, un-armed main session opened three `general-purpose` +
`isolation: "worktree"` implementation lanes on three tickets at once.
`cli_concurrency.dispatch_gate_line` already answered `block|sequential|3`, but
`hooks/block-dispatch-over-wdrain.sh` exited before the gate for every type
other than `autopilot-worker`.

Drives the REAL hook over stdin JSON against a REAL git repo (the #963 dry-run
contract, same harness shape as test_dispatch_sequential_gate_998). A
non-worktree subagent (review seat, research, design draft) stays free: the
owner's rulings (#1137, 15.9., 23.9.) forbid gating ordinary delegation.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


def _run_hook(cwd, home, subagent_type="general-purpose", isolation="worktree",
              prompt="Implement presenter 855 stream output delete",
              tool="Agent"):
    tool_input = {"subagent_type": subagent_type, "prompt": prompt,
                  "run_in_background": True}
    if isolation:
        tool_input["isolation"] = isolation
    payload = {"tool_name": tool, "cwd": str(cwd), "tool_input": tool_input}
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "HOME": home})


class TestSequentialWorktreeLane(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _declare(self, mode):
        claude = self.repo / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "lane-resources.json").write_text(json.dumps({"mode": mode}))

    def _add_live_lane(self):
        # Claude Code's isolation dir, unmerged work = a live lane (#1031).
        wt = self.repo / ".claude" / "worktrees" / "agent-live"
        _git(self.repo, "worktree", "add", "-q", "-b",
             "worktree-agent-live", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")

    def test_second_general_purpose_worktree_lane_blocked(self):
        self._add_live_lane()
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("sequential", r.stderr.lower())
        self.assertIn("cap = 1", r.stderr)
        self.assertIn("worktree lane", r.stderr)

    def test_task_tool_worktree_lane_blocked_too(self):
        self._add_live_lane()
        r = _run_hook(self.repo, self.home, tool="Task")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_any_custom_type_with_worktree_isolation_blocked(self):
        self._add_live_lane()
        r = _run_hook(self.repo, self.home, subagent_type="caveman:cavecrew-builder")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_review_seat_without_isolation_allowed(self):
        self._add_live_lane()
        r = _run_hook(self.repo, self.home, isolation=None,
                      prompt="Review the 850 branch, report findings")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_first_worktree_lane_allowed(self):
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_declared_parallel_never_blocked(self):
        self._declare("parallel")
        self._add_live_lane()
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_plain_lane_skips_the_autopilot_receipt_gate(self):
        # The #992 independence receipt is an autopilot-loop contract. A plain
        # session's FIRST worktree lane naming an issue has no receipt and
        # must still pass: only the sequential cap applies to it.
        r = _run_hook(self.repo, self.home,
                      prompt="Work issue 855: delete a stream output")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("lane-overlap", r.stderr)


if __name__ == "__main__":
    unittest.main()
