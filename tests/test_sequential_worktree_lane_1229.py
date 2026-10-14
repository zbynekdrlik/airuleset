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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"
sys.path.insert(0, str(REPO))
import cli_lane_liveness as lanes_mod  # noqa: E402


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
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
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
        self.assertIn("A live worktree lane", r.stderr)

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


class TestLaneEvidenceOrder(unittest.TestCase):
    """Review 🔴: a lane with NO commit yet has tip == its fork point, an
    ancestor of the base, so it read `merged` while its agent was working."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")
        wt = self.repo / ".claude" / "worktrees" / "agent-fresh"
        _git(self.repo, "worktree", "add", "-q", "-b",
             "worktree-agent-fresh", str(wt))

    def _states(self, live_ids):
        lanes = lanes_mod.classify_lanes(
            str(self.repo), live_worker_ids=live_ids, proc_cwds=set(),
            handoff_numbers=set(), projects_dir=self.tmp)
        return [lane["state"] for lane in lanes]

    def test_commit_less_lane_with_live_worker_is_live(self):
        self.assertEqual(self._states({"agent-fresh"}), ["live"])

    def test_commit_less_lane_without_evidence_stays_merged(self):
        self.assertEqual(self._states(set()), ["merged"])


class TestPendingLaneClaim(unittest.TestCase):
    """Review 🔴: two worktree lanes dispatched in ONE assistant message run
    their PreToolUse hooks before either worktree exists, so the live count is
    0 for both. The first allowed dispatch claims a short pending slot."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _age_claims(self, seconds):
        root = Path(self.home) / ".claude" / "lane-pending"
        old = time.time() - seconds
        for p in root.iterdir():
            os.utime(p, (old, old))

    def test_same_message_second_lane_blocked(self):
        first = _run_hook(self.repo, self.home)
        second = _run_hook(self.repo, self.home, prompt="Implement presenter 858")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertIn("dispatched", second.stderr)
        self.assertIn("sequential", second.stderr.lower())

    def test_autopilot_worker_same_message_pair_blocked_too(self):
        first = _run_hook(self.repo, self.home, subagent_type="autopilot-worker",
                          isolation="worktree", prompt="Do the infra work")
        second = _run_hook(self.repo, self.home, subagent_type="autopilot-worker",
                           isolation="worktree", prompt="Do the other work")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 2, second.stderr)

    def test_stale_claim_expires(self):
        self.assertEqual(_run_hook(self.repo, self.home).returncode, 0)
        self._age_claims(600)
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_review_seat_never_claims_or_is_blocked(self):
        self.assertEqual(_run_hook(self.repo, self.home).returncode, 0)
        r = _run_hook(self.repo, self.home, isolation=None)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_parallel_repo_never_claims(self):
        claude = self.repo / ".claude"
        claude.mkdir()
        (claude / "lane-resources.json").write_text(json.dumps({"mode": "parallel"}))
        self.assertEqual(_run_hook(self.repo, self.home).returncode, 0)
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_repo_cwd_fails_open_without_claim(self):
        plain = Path(self.tmp) / "plain"
        plain.mkdir()
        self.assertEqual(_run_hook(plain, self.home).returncode, 0)
        r = _run_hook(plain, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestPlainLaneGateScope(unittest.TestCase):
    """Review 🟡/🔵: the plain lane must stop after the sequential gate (the
    W-drain gate is an autopilot contract); remote isolation is not a local
    worktree lane; the message names the worktree lane."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def test_plain_lane_skips_the_wdrain_gate(self):
        key = hashlib.sha1(str(self.repo).encode()).hexdigest()[:12]
        cache = Path(self.home) / ".claude" / "tickets-status"
        cache.mkdir(parents=True)
        (cache / (key + ".json")).write_text(json.dumps(
            {"ts": time.time(), "ops_wait": 20, "ops_wait_stale": 20}))
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("W-drain", r.stderr)

    def test_remote_isolation_is_not_a_local_lane(self):
        wt = self.repo / ".claude" / "worktrees" / "agent-live"
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-agent-live", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")
        r = _run_hook(self.repo, self.home, isolation="remote")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_block_message_names_the_live_worktree_lane(self):
        wt = self.repo / ".claude" / "worktrees" / "agent-live"
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-agent-live", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("A live worktree lane", r.stderr)


class TestDevIntegrationBase(unittest.TestCase):
    """Review 🔴: a 2-branch repo integrates on `dev`. A lane merged into dev
    read idle-unmerged against main, so the pane was refused until a release."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")
        _git(self.repo, "checkout", "-q", "-b", "dev")
        wt = self.repo / ".claude" / "worktrees" / "agent-done"
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-agent-done",
             str(wt), "dev")
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate lane",
             "worktree-agent-done")

    def test_lane_merged_into_dev_reads_merged(self):
        lanes = lanes_mod.classify_lanes(
            str(self.repo), live_worker_ids=set(), proc_cwds=set(),
            handoff_numbers=set(), projects_dir=self.tmp)
        self.assertEqual([lane["state"] for lane in lanes], ["merged"])

    def test_next_lane_allowed_after_dev_integration(self):
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestOwnWorktreeAndMessage(unittest.TestCase):
    """Review 🔵: a main session working inside its OWN worktree is not a lane.
    Review 🟡: the block message lists the counted lanes and forbids dropping
    isolation to get around the cap."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _lane(self, name):
        wt = self.repo / ".claude" / "worktrees" / name
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-" + name, str(wt))
        (wt / (name + ".txt")).write_text("y")
        _git(wt, "add", name + ".txt")
        _git(wt, "commit", "-qm", "work " + name)
        return wt

    def test_session_in_its_own_worktree_is_not_a_lane(self):
        own = self._lane("main-own")
        r = _run_hook(own, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_block_lists_the_lane_and_forbids_dropping_isolation(self):
        self._lane("agent-idle")
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("idle-unmerged", r.stderr)
        self.assertIn("worktree-agent-idle", r.stderr)
        self.assertIn("Do NOT drop isolation", r.stderr)
        self.assertNotIn("Subagents/consults are NOT gated", r.stderr)


class TestStaleDevBranch(unittest.TestCase):
    """The controller's own repo works on main but still carries a stale
    `origin/dev`. A lane merged into main must read merged; judging only
    against the stale dev would count it live forever."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")
        _git(self.repo, "branch", "dev")
        (self.repo / "h.txt").write_text("z")
        _git(self.repo, "add", "h.txt")
        _git(self.repo, "commit", "-qm", "main moves on")
        wt = self.repo / ".claude" / "worktrees" / "agent-done"
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-agent-done", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate lane",
             "worktree-agent-done")

    def test_lane_merged_into_main_reads_merged_despite_stale_dev(self):
        lanes = lanes_mod.classify_lanes(
            str(self.repo), live_worker_ids=set(), proc_cwds=set(),
            handoff_numbers=set(), projects_dir=self.tmp)
        self.assertEqual([lane["state"] for lane in lanes], ["merged"])

    def test_next_lane_allowed(self):
        r = _run_hook(self.repo, self.home)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
