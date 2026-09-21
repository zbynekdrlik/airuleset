"""#1103 — a lane is LIVE only while an agent still works it.

`cli_lane_overlap.gather_live_lanes` used to treat EVERY unmerged lane worktree
as live, so hand-offs already with the gatekeeper (montalu1), gk hotfix branches
targeting `main`, and dead-worker worktrees all pinned their files in the
independence receipt and the sequential cap. This suite pins the evidence-based
liveness (worker-transcript / process / hand-off-state) + the finished-worktree
prune (Approach 1 of the main-authored design comment).

Fixture idiom: fake-injected `run` for the classifier state machine + a REAL
git-in-tempdir for the prune sweep (matching test_lane_liveness_998 /
test_lane_liveness_stream_1031).
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main, mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_overlap as lo  # noqa: E402
import cli_concurrency as cc  # noqa: E402

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def _git(cwd, *a, env=None):
    subprocess.run(["git", "-C", str(cwd), *a], check=True,
                   capture_output=True, text=True, env=env or _ENV)


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


# --------------------------------------------------------------------------
# classifier state machine (a)-(d)
# --------------------------------------------------------------------------
class TestClassifyStates(TestCase):
    def _classify(self, lanes, **kw):
        porc = _porc(MAIN, lanes)
        fake = _Fake(porc, **{k: kw.pop(k) for k in
                              ("merged_bases", "files", "subjects", "develop",
                               "upstreams") if k in kw})
        return lo.classify_lanes(MAIN, run=fake, now=1_000_000.0,
                                 live_worker_ids=kw.get("live_worker_ids", set()),
                                 proc_cwds=kw.get("proc_cwds", set()),
                                 handoff_numbers=kw.get("handoff_numbers", set()))

    def _state(self, lanes, ref, **kw):
        for c in self._classify(lanes, **kw):
            if c["ref"] == ref:
                return c["state"]
        return None

    def test_a_handed_off_no_process_is_finished(self):
        # unmerged + no worker transcript + no process + ticket handed off.
        st = self._state([("agent-1", "montalu/7840-searchmore")],
                         "montalu/7840-searchmore",
                         handoff_numbers={7840})
        self.assertEqual(st, "finished")

    def test_a_finished_excluded_from_gather(self):
        porc = _porc(MAIN, [("agent-1", "montalu/7840-x")])
        fake = _Fake(porc)
        lanes = lo.gather_live_lanes(MAIN, run=fake, now=1_000_000.0,
                                     live_worker_ids=set(), proc_cwds=set(),
                                     handoff_numbers={7840})
        self.assertEqual(lanes, [], "a finished lane must not be live")

    def test_b_fresh_worker_transcript_is_live(self):
        # the worktree dir basename matches a live worker agent-id.
        st = self._state([("agent-abc", "montalu/7796-fix")],
                         "montalu/7796-fix",
                         live_worker_ids={"agent-abc"}, handoff_numbers={7796})
        self.assertEqual(st, "live", "a fresh worker transcript beats a hand-off label")

    def test_b_process_in_worktree_is_live(self):
        wt = "%s/.claude/worktrees/agent-proc" % MAIN
        st = self._state([("agent-proc", "montalu/7797-x")],
                         "montalu/7797-x",
                         proc_cwds={wt}, handoff_numbers={7797})
        self.assertEqual(st, "live", "a process cwd inside the worktree is live")

    def test_c_hotfix_merged_into_main_is_merged(self):
        # a hotfix-main-* branch merged into main but NOT develop => merged.
        st = self._state([("agent-hf", "gatekeeper/hotfix-main-7520-7842")],
                         "gatekeeper/hotfix-main-7520-7842",
                         merged_bases={"gatekeeper/hotfix-main-7520-7842": "main"})
        self.assertEqual(st, "merged")

    def test_c_hotfix_merged_excluded_from_gather(self):
        porc = _porc(MAIN, [("agent-hf", "gatekeeper/hotfix-main-7520-7842")])
        fake = _Fake(porc,
                     merged_bases={"gatekeeper/hotfix-main-7520-7842": "main"})
        lanes = lo.gather_live_lanes(MAIN, run=fake, now=1_000_000.0,
                                     live_worker_ids=set(), proc_cwds=set(),
                                     handoff_numbers=set())
        self.assertEqual(lanes, [], "a hotfix merged into main is not live")

    def test_d_no_evidence_is_idle_unmerged_and_live(self):
        # unmerged + no transcript + no process + no hand-off => idle-unmerged,
        # which STILL counts as live (the #998 fail-safe toward overlap).
        st = self._state([("agent-idle", "david3/7184-vyroba")],
                         "david3/7184-vyroba")
        self.assertEqual(st, "idle-unmerged")
        porc = _porc(MAIN, [("agent-idle", "david3/7184-vyroba")])
        lanes = lo.gather_live_lanes(MAIN, run=_Fake(porc), now=1_000_000.0)
        self.assertEqual({l["ref"] for l in lanes}, {"david3/7184-vyroba"})

    def test_detached_lane_is_live(self):
        porc = ("worktree %s\nHEAD aaaa\nbranch refs/heads/mainfeat\n"
                "\nworktree %s/.claude/worktrees/agent-det\nHEAD cccccccccccc\n"
                "detached\n" % (MAIN, MAIN))
        lanes = lo.gather_live_lanes(MAIN, run=_Fake(porc), now=1_000_000.0)
        self.assertEqual(len(lanes), 1, "a detached lane mid-operation is live")


# --------------------------------------------------------------------------
# (e) montalu1-shaped overlap + per-state counts
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# (f) dispatch_gate_line counts only live (finished excluded)
# --------------------------------------------------------------------------
class TestDispatchGateCountsOnlyLive(TestCase):
    def test_sequential_counts_live_not_finished(self):
        lanes = ([("agent-live6", "montalu/7906-live"),
                  ("agent-live7", "montalu/7907-live")]
                 + [("agent-fin%d" % i, "montalu/78%02d-done" % i)
                    for i in range(8)])
        porc = _porc(MAIN, lanes)
        fake = _Fake(porc)
        live_count = len(lo.gather_live_lanes(
            MAIN, run=fake, now=1_000_000.0,
            live_worker_ids={"agent-live6", "agent-live7"}, proc_cwds=set(),
            handoff_numbers={int("78%02d" % i) for i in range(8)}))
        self.assertEqual(live_count, 2)
        d = tempfile.mkdtemp(prefix="seq1103-")
        claude = Path(d) / ".claude"
        claude.mkdir()
        (claude / "lane-resources.json").write_text('{"mode": "sequential"}')
        self.assertEqual(
            cc.dispatch_gate_line(d, live_count=live_count),
            "block|sequential|2")


# --------------------------------------------------------------------------
# ticket-number parsing from branch + commit subjects
# --------------------------------------------------------------------------
class TestTicketNumberParse(TestCase):
    def test_from_branch_stream_slash_number(self):
        self.assertIn(7840, lo._lane_ticket_numbers(
            MAIN, "montalu/7840-searchmore", _Fake("")))

    def test_from_worktree_issue_prefix(self):
        self.assertIn(1234, lo._lane_ticket_numbers(
            MAIN, "worktree-issue-1234", _Fake("")))

    def test_from_commit_subject_hash(self):
        fake = _Fake("", subjects={"diag/searchmore-x": "green(#2314): fix it"})
        self.assertIn(2314, lo._lane_ticket_numbers(
            MAIN, "diag/searchmore-x", fake))


# --------------------------------------------------------------------------
# worker-transcript evidence glue (agent-id extraction reuses count_live_workers)
# --------------------------------------------------------------------------
class TestLiveWorkerAgentIds(TestCase):
    def test_reads_live_agent_ids_from_sessions(self):
        from collections import namedtuple
        WL = namedtuple("WorkerLane", ["agent_id", "state", "age_s",
                                       "agent_type", "detail"])
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "projects"
            import watchdog.transcripts as T
            enc = T.encode_project_dir(MAIN)
            (proj / enc / "sid1" / "subagents").mkdir(parents=True)
            fake_count = mock.Mock(return_value=(
                1, [WL("agent-live", "live", 5, None, ""),
                    WL("agent-dead", "stale", 999, None, "")]))
            with mock.patch("watchdog.count_live_workers", fake_count):
                ids = lo._live_worker_agent_ids(MAIN, projects_dir=str(proj),
                                                now=1_000.0)
        self.assertIn("agent-live", ids)
        self.assertNotIn("agent-dead", ids)


# --------------------------------------------------------------------------
# hand-off numbers read from the fresh tickets-status cache (no gh)
# --------------------------------------------------------------------------
class TestHandoffNumbersFromCache(TestCase):
    def test_reads_gk_numbers_and_merged_unreleased(self):
        import statusbar
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"HOME": home}):
                cd = statusbar.cache_dir(home=home)
                cd.mkdir(parents=True, exist_ok=True)
                key = statusbar.cwd_key(MAIN)
                (cd / (key + ".json")).write_text(
                    '{"ts": %f, "gk_numbers": [7840, 7832],'
                    ' "merged_unreleased_numbers": [7000]}' % time.time())
                nums = lo._handoff_numbers(MAIN, home=home)
        self.assertEqual(nums, {7840, 7832, 7000})

    def test_stale_cache_yields_empty(self):
        import statusbar
        with tempfile.TemporaryDirectory() as home:
            cd = statusbar.cache_dir(home=home)
            cd.mkdir(parents=True, exist_ok=True)
            key = statusbar.cwd_key(MAIN)
            (cd / (key + ".json")).write_text(
                '{"ts": 1.0, "gk_numbers": [7840]}')  # ancient ts
            nums = lo._handoff_numbers(MAIN, home=home)
        self.assertEqual(nums, set())


# --------------------------------------------------------------------------
# prune sweep — REAL git worktrees in a tempdir
# --------------------------------------------------------------------------
class TestPruneFinishedWorktrees(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("base")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _add_lane(self, dirbase, branch, *, commit=True, old=True, dirty=False):
        wt = self.repo / ".claude" / "worktrees" / dirbase
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt))
        if commit:
            (wt / "w.txt").write_text("x")
            _git(wt, "add", "w.txt")
            env = dict(_ENV)
            if old:
                env["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00"
                env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00"
            _git(wt, "commit", "-qm", branch + " work", env=env)
        if dirty:
            (wt / "dirty.txt").write_text("uncommitted")
        return wt

    def test_finished_clean_old_worktree_removed_branch_kept(self):
        wt = self._add_lane("agent-fin", "montalu/7840-done")
        logs = lo.prune_finished_worktrees(
            str(self.repo), now=time.time(),
            live_worker_ids=set(), proc_cwds=set(), handoff_numbers={7840})
        self.assertFalse(wt.exists(), "the worktree dir must be gone: %s" % logs)
        # branch ref kept
        r = subprocess.run(["git", "-C", str(self.repo), "rev-parse",
                            "--verify", "montalu/7840-done"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "the branch ref must be kept")
        self.assertTrue(any("remove" in ln for ln in logs), logs)

    def test_dirty_worktree_kept(self):
        wt = self._add_lane("agent-dirty", "montalu/7841-done", dirty=True)
        logs = lo.prune_finished_worktrees(
            str(self.repo), now=time.time(),
            live_worker_ids=set(), proc_cwds=set(), handoff_numbers={7841})
        self.assertTrue(wt.exists(), "a dirty worktree must be kept")
        self.assertTrue(any("dirty" in ln.lower() for ln in logs), logs)

    def test_process_holding_worktree_kept(self):
        wt = self._add_lane("agent-proc", "montalu/7842-done")
        logs = lo.prune_finished_worktrees(
            str(self.repo), now=time.time(),
            live_worker_ids=set(),
            proc_cwds={os.path.realpath(str(wt))}, handoff_numbers={7842})
        self.assertTrue(wt.exists(), "a process-holding worktree must be kept")

    def test_young_worktree_kept(self):
        # committed NOW (< 2h old) => kept even though finished.
        wt = self._add_lane("agent-young", "montalu/7843-done", old=False)
        logs = lo.prune_finished_worktrees(
            str(self.repo), now=time.time(),
            live_worker_ids=set(), proc_cwds=set(), handoff_numbers={7843})
        self.assertTrue(wt.exists(), "a <2h worktree must be kept: %s" % logs)

    def test_idle_unmerged_worktree_kept(self):
        # no hand-off, no evidence => idle-unmerged => NOT a prune candidate.
        wt = self._add_lane("agent-idle", "montalu/7844-wip")
        lo.prune_finished_worktrees(
            str(self.repo), now=time.time(),
            live_worker_ids=set(), proc_cwds=set(), handoff_numbers=set())
        self.assertTrue(wt.exists(), "an idle-unmerged (live) worktree must be kept")

    def test_cadence_gate_skips_within_hour(self):
        self._add_lane("agent-cad", "montalu/7845-done")
        state = {}
        now = time.time()
        lo.prune_finished_worktrees(str(self.repo), now=now, state=state,
                                    live_worker_ids=set(), proc_cwds=set(),
                                    handoff_numbers=set())
        # second call within the hour is a no-op (returns [] without re-listing)
        logs2 = lo.prune_finished_worktrees(str(self.repo), now=now + 60,
                                            state=state, live_worker_ids=set(),
                                            proc_cwds=set(), handoff_numbers=set())
        self.assertEqual(logs2, [])


if __name__ == "__main__":
    main()
