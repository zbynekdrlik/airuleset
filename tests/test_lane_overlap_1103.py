"""#1103 — the overlap half: compute_overlap over liveness-classified lanes.

The #1103 liveness derivation and its unit tests moved to cli_lane_liveness /
tests/test_lane_liveness_1103.py (#1107). This file keeps the montalu1-shaped
overlap check that proves gather_live_lanes / classify_lanes / state_summary
(now re-exported from the leaf via cli_lane_overlap) feed compute_overlap
correctly — so it doubles as a live test of the back-compat re-exports.
"""
import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_overlap as lo  # noqa: E402

class _R:
    def __init__(self, rc=0, out=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = ""


class _Fake:
    """Configurable injected `run`.

    `worktrees` — porcelain string.
    `merged` — set of (branch) that ARE an ancestor of their checked base
        (per-base via `merged_bases` when a branch is merged into ONE base only).
    `merged_bases` — {branch: base-substring it is merged into} — a
        `--is-ancestor <branch> <base>` returns rc 0 iff `base-substring in base`.
    `files` — {branch: [paths]}.
    `subjects` — {branch: "commit subject"}.
    `develop` — True => a develop ref exists (3-branch repo).
    """

    def __init__(self, worktrees, *, merged_bases=None, files=None,
                 subjects=None, develop=True, upstreams=None):
        self.worktrees = worktrees
        self.merged_bases = merged_bases or {}
        self.files = files or {}
        self.subjects = subjects or {}
        self.develop = develop
        self.upstreams = upstreams or {}
        self.ancestor_calls = []

    def __call__(self, cmd):
        j = " ".join(cmd)
        if "worktree" in cmd and "list" in cmd:
            return _R(0, self.worktrees)
        if "for-each-ref" in cmd:
            return _R(0, "refs/remotes/origin/develop\n") if self.develop else _R(0, "")
        if "symbolic-ref" in cmd:
            return _R(0, "refs/remotes/origin/develop\n")
        if "rev-parse" in cmd and "@{upstream}" in j:
            br = cmd[-1].replace("@{upstream}", "")
            up = self.upstreams.get(br)
            return _R(0, up + "\n") if up else _R(1)
        if "rev-parse" in cmd and "--verify" in cmd:
            ref = cmd[-1]
            ok = ("develop" in ref or ref.endswith("/main") or ref == "main"
                  or ref.endswith("/master") or ref == "master")
            return _R(0, "sha\n") if ok else _R(1)
        if "--is-ancestor" in cmd:
            branch, base = cmd[-2], cmd[-1]
            self.ancestor_calls.append((branch, base))
            want = self.merged_bases.get(branch)
            if want is not None and want in base:
                return _R(0)
            return _R(1)
        if "merge-base" in cmd:
            return _R(0, "basesha\n")
        if cmd[:1] == ["git"] and "diff" in cmd:
            branch = cmd[-1]
            return _R(0, "\n".join(self.files.get(branch, [])) + "\n")
        if "log" in cmd and "%s" in j:
            branch = cmd[-1]
            return _R(0, self.subjects.get(branch, branch) + "\n")
        return _R(1)


def _porc(main_path, lanes):
    """Build a `git worktree list --porcelain` string: main checkout first,
    then each lane `(dirbase, branch)` under `<main>/.claude/worktrees/`."""
    s = "worktree %s\nHEAD aaaaaaaaaaaa\nbranch refs/heads/mainfeat\n" % main_path
    for dirbase, branch in lanes:
        s += ("\nworktree %s/.claude/worktrees/%s\nHEAD bbbbbbbbbbbb\n"
              "branch refs/heads/%s\n" % (main_path, dirbase, branch))
    return s


MAIN = "/home/stream/devel/proj"


class TestMontalu1ShapedOverlap(TestCase):
    def _fake(self):
        lanes = ([("agent-live%d" % i, "montalu/79%02d-live" % i) for i in (6, 7)]
                 + [("agent-fin%d" % i, "montalu/78%02d-done" % i)
                    for i in range(8)])
        porc = _porc(MAIN, lanes)
        files = {"montalu/7906-live": ["views/live_a.xml"],
                 "montalu/7907-live": ["views/live_b.xml"]}
        for i in range(8):
            files["montalu/78%02d-done" % i] = ["views/done_%d.xml" % i]
        return _Fake(porc, files=files)

    def _seams(self):
        # 2 live worker transcripts; the 8 done lanes are handed off.
        live_ids = {"agent-live6", "agent-live7"}
        handoff = {int("78%02d" % i) for i in range(8)}
        return dict(live_worker_ids=live_ids, proc_cwds=set(),
                    handoff_numbers=handoff)

    def test_counts_live_2_finished_8_idle_0(self):
        classified = lo.classify_lanes(MAIN, run=self._fake(), now=1_000_000.0,
                                       **self._seams())
        summ = lo.state_summary(classified)
        self.assertEqual(summ, "live=2 finished=8 idle=0", summ)

    def test_overlap_clear_against_finished_lane_file(self):
        live = lo.gather_live_lanes(MAIN, run=self._fake(), now=1_000_000.0,
                                    **self._seams())
        verdict, _ = lo.compute_overlap(["views/done_3.xml"], [],
                                        live_lanes=live, open_prs=[])
        self.assertEqual(verdict, "clear",
                         "a finished lane's files must NOT block a new unit")

    def test_overlap_hit_against_live_lane_file(self):
        live = lo.gather_live_lanes(MAIN, run=self._fake(), now=1_000_000.0,
                                    **self._seams())
        verdict, ov = lo.compute_overlap(["views/live_a.xml"], [],
                                         live_lanes=live, open_prs=[])
        self.assertEqual(verdict, "overlap", ov)


if __name__ == "__main__":
    main()
