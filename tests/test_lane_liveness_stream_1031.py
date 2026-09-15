"""#1031 (REOPENED regression) — the SEQUENTIAL-mode dispatch gate was a no-op
on stream boxes because `cli_lane_overlap.gather_live_lanes` recognised a lane
ONLY by a `worktree-` branch-name prefix. A stream autopilot-worker names its
branch by the project convention (`david3/<issue>-<slug>`, `lane5337`), so every
real lane was invisible -> live_count 0 -> `cli_concurrency.dispatch_gate_line`
answered `allow|sequential|0` -> `hooks/block-dispatch-over-wdrain.sh` never
refused the 2nd..Nth dispatch (owner saw 5-6 parallel lanes on a `sequential`
box, 2026-09-15).

Fix (Approach 2 in the #1031 design comment): a lane = a worktree whose PATH is
under `<repo>/.claude/worktrees/` (Claude Code's `isolation: worktree` dir —
what EVERY dispatched lane is, whatever its branch is named) OR whose branch
keeps the `worktree-` prefix (today's controller lanes). Base branch = the repo
default (`git symbolic-ref --quiet refs/remotes/origin/HEAD`), fallback to the
first worktree's branch. Merged lanes stay EXCLUDED; a detached-HEAD worktree
under the isolation dir counts as live (a lane mid-operation).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_concurrency as cc  # noqa: E402
import cli_lane_overlap as lo  # noqa: E402

HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"

D3 = "/home/david3/devel/odoo/odoo-erp"

# david3@subdev live tree: main checkout on a FEATURE branch, two lanes under
# .claude/worktrees/ on stream-named branches (NOT `worktree-` prefixed).
STREAM_PORCELAIN = (
    "worktree %s\n"
    "HEAD aaaaaaaaaaaa\n"
    "branch refs/heads/david3/5235-pokladna-e1\n"
    "\n"
    "worktree %s/.claude/worktrees/agent-xyz\n"
    "HEAD bbbbbbbbbbbb\n"
    "branch refs/heads/david3/7184-vyroba-dokoncenie\n"
) % (D3, D3)


class _R:
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


class _FakeGit:
    """Dependency-injected `run` returning a scripted worktree porcelain and
    recording every merged-check's (branch, base) so the base-branch fix is
    observable. `ancestor_rc=1` => the lane is UNMERGED (live)."""

    def __init__(self, porcelain, origin_head="refs/remotes/origin/develop",
                 ancestor_rc=1):
        self.porcelain = porcelain
        self.origin_head = origin_head
        self.ancestor_rc = ancestor_rc
        self.ancestor_calls = []  # (branch, base)

    def __call__(self, cmd):
        if "worktree" in cmd and "list" in cmd:
            return _R(0, self.porcelain)
        if "symbolic-ref" in cmd:
            return _R(0, self.origin_head + "\n") if self.origin_head else _R(1)
        if "--is-ancestor" in cmd:
            self.ancestor_calls.append((cmd[-2], cmd[-1]))
            return _R(self.ancestor_rc)
        if "merge-base" in cmd:
            return _R(0, "basesha\n")
        if "diff" in cmd:
            return _R(0, "addons/x/models/y.py\n")
        if "log" in cmd:
            return _R(0, "green(#7184): finish it\n")
        return _R(1)


def _seq_cwd():
    d = tempfile.mkdtemp(prefix="d3seq-")
    claude = Path(d) / ".claude"
    claude.mkdir()
    (claude / "lane-resources.json").write_text(json.dumps({"mode": "sequential"}))
    return d


class TestStreamLaneCounted(unittest.TestCase):
    # (1) a stream-named lane under .claude/worktrees/ is LIVE (today: invisible).
    def test_stream_named_worktree_lane_is_live(self):
        fake = _FakeGit(STREAM_PORCELAIN)
        lanes = lo.gather_live_lanes(D3, run=fake)
        self.assertEqual(len(lanes), 1,
                         "a stream lane under .claude/worktrees/ must count")
        self.assertEqual(lanes[0]["ref"], "david3/7184-vyroba-dokoncenie")

    def test_dispatch_gate_blocks_second_stream_lane(self):
        fake = _FakeGit(STREAM_PORCELAIN)
        d = _seq_cwd()
        self.assertEqual(
            cc.dispatch_gate_line(d, repo_root=D3, run=fake, live_count=None),
            "block|sequential|1")


class TestBaseBranchIsDefault(unittest.TestCase):
    # (2) merged-ness is judged against the repo DEFAULT branch (origin/HEAD ->
    # develop), never the main checkout's FEATURE branch (5235-pokladna-e1).
    def test_merged_check_uses_default_branch_not_feature_branch(self):
        fake = _FakeGit(STREAM_PORCELAIN,
                        origin_head="refs/remotes/origin/develop")
        lo.gather_live_lanes(D3, run=fake)
        self.assertTrue(fake.ancestor_calls, "a lane must be merged-checked")
        bases = {base for _, base in fake.ancestor_calls}
        self.assertTrue(all("develop" in b for b in bases), bases)
        self.assertFalse(any("5235" in b for b in bases), bases)

    def test_lane_ref_is_full_branch_name_not_last_segment(self):
        # rsplit("/",1)[-1] mangled a slash branch -> broke git ref resolution.
        fake = _FakeGit(STREAM_PORCELAIN)
        lo.gather_live_lanes(D3, run=fake)
        self.assertEqual(fake.ancestor_calls[0][0],
                         "david3/7184-vyroba-dokoncenie")


class TestControllerShapeUnchanged(unittest.TestCase):
    # (3) worktree-agent-… branches still count; a merged worktree-… excluded.
    CTRL = "/home/airuleset/devel/airuleset"
    PORC = (
        "worktree /home/airuleset/devel/airuleset\n"
        "HEAD aaaa\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /home/airuleset/devel/airuleset/.claude/worktrees/agent-1\n"
        "HEAD bbbb\n"
        "branch refs/heads/worktree-agent-1\n"
    )

    def test_worktree_prefixed_lane_still_counts(self):
        fake = _FakeGit(self.PORC, origin_head="refs/remotes/origin/main",
                        ancestor_rc=1)  # unmerged
        lanes = lo.gather_live_lanes(self.CTRL, run=fake)
        self.assertEqual({ln["ref"] for ln in lanes}, {"worktree-agent-1"})

    def test_merged_worktree_lane_excluded(self):
        fake = _FakeGit(self.PORC, origin_head="refs/remotes/origin/main",
                        ancestor_rc=0)  # merged -> excluded
        self.assertEqual(lo.gather_live_lanes(self.CTRL, run=fake), [])


class TestDetachedLaneCounted(unittest.TestCase):
    # a detached-HEAD worktree under the isolation dir is a lane mid-operation.
    def test_detached_worktree_under_isolation_dir_is_live(self):
        porc = (
            "worktree %s\n"
            "HEAD aaaa\n"
            "branch refs/heads/david3/5235-pokladna-e1\n"
            "\n"
            "worktree %s/.claude/worktrees/agent-det\n"
            "HEAD cccccccccccc\n"
            "detached\n"
        ) % (D3, D3)
        fake = _FakeGit(porc)
        lanes = lo.gather_live_lanes(D3, run=fake)
        self.assertEqual(len(lanes), 1, "a detached lane mid-operation is live")


class TestReceiptSeesStreamLane(unittest.TestCase):
    # Owner ROZHODNUTÉ (#1031, 2026-09-15): sequential mode is NOT a hard count
    # — its goal is (a) "the right hand knows the left" (no two lanes on similar
    # work unaware of each other) and (b) no nudge pushing max parallelism. The
    # lane-overlap RECEIPT (compute_overlap over gather_live_lanes) is the (a)
    # instrument and shares gather_live_lanes with the dispatch gate, so a new
    # unit touching the same file as a STREAM-named live lane must report
    # OVERLAP — before the fix gather_live_lanes returned 0 live lanes, so the
    # receipt falsely read CLEAR against "0 live lane(s)".
    def test_receipt_reports_overlap_against_stream_lane_same_file(self):
        fake = _FakeGit(STREAM_PORCELAIN)  # the lane touches addons/x/models/y.py
        lanes = lo.gather_live_lanes(D3, run=fake)
        self.assertEqual(len(lanes), 1, "the stream lane must be seen at all")
        verdict, overlaps = lo.compute_overlap(
            paths=["addons/x/models/y.py"], topics=["vyroba dokoncenie"],
            live_lanes=lanes, open_prs=[])
        self.assertEqual(verdict, "overlap")
        self.assertTrue(
            any(o[0] == "lane" and o[1] == "david3/7184-vyroba-dokoncenie"
                for o in overlaps),
            "receipt must flag the stream lane by its full ref, not read CLEAR")


_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True,
                   capture_output=True, text=True, env=_ENV)


class TestHookEndToEndStreamLane(unittest.TestCase):
    # (4) the REAL hook, over stdin JSON, against a REAL git repo whose live
    # lane is a stream-named worktree under .claude/worktrees/ -> exit 2.
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")
        (self.repo / ".claude").mkdir()
        (self.repo / ".claude" / "lane-resources.json").write_text(
            json.dumps({"mode": "sequential"}))
        wt = self.repo / ".claude" / "worktrees" / "agent-xyz"
        _git(self.repo, "worktree", "add", "-q", "-b",
             "david3/7184-vyroba-dokoncenie", str(wt))
        (wt / "g.txt").write_text("y")
        _git(wt, "add", "g.txt")
        _git(wt, "commit", "-qm", "lane work")

    def _run_hook(self, prompt):
        payload = {"tool_name": "Agent", "cwd": str(self.repo),
                   "tool_input": {"subagent_type": "autopilot-worker",
                                  "prompt": prompt}}
        return subprocess.run(
            ["bash", str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True,
            env={**os.environ, "HOME": self.home})

    def test_stream_lane_blocks_second_dispatch(self):
        # NO issue number in the prompt -> the #992/#993 overlap-receipt gate is
        # skipped, so the #998 sequential gate is exercised directly.
        r = self._run_hook("Do the stream work")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("sequential-mode gate", r.stderr)


if __name__ == "__main__":
    unittest.main()
