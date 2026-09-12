"""#998 item 2 — block-dispatch-over-wdrain.sh SEQUENTIAL gate (integration).

Drives the REAL hook over stdin JSON (the #963 stdin-JSON dry-run contract)
against a REAL git repo, so the whole path — resolve mode from the project's
lane-resources.json + count live worktree lanes via gather_live_lanes — is
exercised. A 2nd concurrent autopilot-worker in a sequential-mode repo with a
live lane is refused (exit 2); parallel (or zero live lanes) passes.
"""
import json
import os
import subprocess
import sys
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


def _run_hook(cwd, prompt, home):
    payload = {"tool_name": "Agent", "cwd": str(cwd),
               "tool_input": {"subagent_type": "autopilot-worker",
                              "prompt": prompt}}
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "HOME": home})


class TestSequentialDispatchGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _make_sequential(self):
        claude = self.repo / ".claude"
        claude.mkdir(exist_ok=True)
        (claude / "lane-resources.json").write_text(
            json.dumps({"mode": "sequential"}))

    def _add_live_lane(self):
        # a worktree branch AHEAD of main (unmerged) = a live lane.
        wt = Path(self.tmp) / "wt"
        _git(self.repo, "worktree", "add", "-q", "-b",
             "worktree-agent-live", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")

    def test_sequential_second_lane_blocked(self):
        self._make_sequential()
        self._add_live_lane()
        r = _run_hook(self.repo, "Do the infra work", self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("sequential", r.stderr.lower())
        self.assertIn("cap = 1", r.stderr)

    def test_sequential_first_lane_allowed(self):
        # no live lane yet -> this is the FIRST, allowed.
        self._make_sequential()
        r = _run_hook(self.repo, "Do the infra work", self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_parallel_repo_never_blocked_by_sequential_gate(self):
        # no lane-resources.json -> parallel; even with a live lane, allowed.
        self._add_live_lane()
        r = _run_hook(self.repo, "Do the work", self.home)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
