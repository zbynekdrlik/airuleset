"""#1103/#1107 — a lane is LIVE only while an agent still works it.

The lane-liveness derivation (`classify_lanes` -> live | merged | finished |
idle-unmerged, plus `gather_live_lanes` / `state_summary` and the evidence
helpers) was extracted VERBATIM from `cli_lane_overlap` into its own leaf
`cli_lane_liveness` (#1107). This suite is the #1103 liveness tests moved here
with their imports repointed to the leaf, PLUS the #1107 move proofs: the #482
reconstruction assert (each moved function byte-identical to its pre-move
source), the back-compat re-export identity, the consumer-import AST lock, and
a two-order fresh-import cycle check.

Fixture idiom: fake-injected `run` for the classifier state machine + a REAL
git-in-tempdir for the prune sweep (matching test_lane_liveness_998 /
test_lane_liveness_stream_1031).
"""
import ast
import inspect
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main, mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_lane_liveness as lo  # noqa: E402
import cli_lane_overlap as ovl  # noqa: E402
import cli_concurrency as cc  # noqa: E402
import watchdog.lane_reconcile as lr  # noqa: E402

# The pre-move commit (merge-base of this lane and main): its
# cli_lane_overlap.py still carries the liveness cluster verbatim, so the #482
# reconstruction assert pins it. Always reachable on main's history; CI clones
# full depth (fetch-depth: 0).
_PREMOVE_SHA = "3997def38982a90f1ef4bd070854bbafeb46b77f"
_MOVED_FUNCS = [
    "_lane_is_merged", "_parse_worktree_records", "_pick_most_advanced_ref",
    "_resolve_base_branch", "_is_lane_worktree", "_ref_exists", "_lane_target",
    "_worktree_process_cwds", "_path_has_proc", "_live_worker_agent_ids",
    "_lane_ticket_numbers", "_handoff_numbers", "classify_lanes",
    "state_summary", "gather_live_lanes",
]

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
        self.assertEqual({ln["ref"] for ln in lanes}, {"david3/7184-vyroba"})

    def test_detached_lane_is_live(self):
        porc = ("worktree %s\nHEAD aaaa\nbranch refs/heads/mainfeat\n"
                "\nworktree %s/.claude/worktrees/agent-det\nHEAD cccccccccccc\n"
                "detached\n" % (MAIN, MAIN))
        lanes = lo.gather_live_lanes(MAIN, run=_Fake(porc), now=1_000_000.0)
        self.assertEqual(len(lanes), 1, "a detached lane mid-operation is live")

    def test_self_tracked_branch_not_dropped_as_merged(self):
        # #1103 review BLOCKER regression: a pushed lane branch tracks its OWN
        # origin ref (origin/<self>); the merged-check must be judged against the
        # integration base (develop), NOT the branch's @{upstream}, or a
        # just-pushed HEAD==origin lane reads as "merged into its upstream" and is
        # wrongly dropped from the live set.
        br = "montalu/7796-fix"
        st = self._state([("agent-self", br)], br,
                         upstreams={br: "origin/" + br},
                         merged_bases={br: "origin/" + br})  # merged into SELF only
        self.assertEqual(st, "idle-unmerged",
                         "a self-tracked, not-merged-to-base lane stays LIVE")

    def test_incidental_subject_ticket_does_not_finish_lane(self):
        # #1103 review 🟡: a commit subject naming a DIFFERENT handed-off ticket
        # must not mark this lane finished — only its OWN (branch) ticket counts.
        br = "montalu/7840-searchmore"
        st = self._state([("agent-inc", br)], br,
                         subjects={br: "green(#7840): work; also closes #7000"},
                         handoff_numbers={7000})  # 7000 handed off, 7840 is NOT
        self.assertEqual(st, "idle-unmerged",
                         "an incidental #7000 in the subject must not finish #7840")


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


class TestPruneFinishedWorktrees(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("base")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _add_lane(self, dirbase, branch, *, commit=True, dirty=False,
                  old_commit=False):
        wt = self.repo / ".claude" / "worktrees" / dirbase
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt))
        if commit:
            (wt / "w.txt").write_text("x")
            _git(wt, "add", "w.txt")
            env = dict(_ENV)
            if old_commit:  # a hand-off commit >2h ago (real commit-age path)
                env["GIT_COMMITTER_DATE"] = "2020-01-01T00:00:00"
                env["GIT_AUTHOR_DATE"] = "2020-01-01T00:00:00"
            _git(wt, "commit", "-qm", branch + " work", env=env)
        if dirty:
            (wt / "dirty.txt").write_text("uncommitted")
        return wt

    def test_real_helpers_remove_old_finished_worktree(self):
        # NO injected seams: exercises the REAL cli_worktree_sweep clean +
        # live-use helpers AND the real commit-age guard end-to-end. A backdated
        # hand-off commit (>2h) + clean tree + no process rooted in it => removed.
        wt = self._add_lane("agent-realrm", "montalu/7850-done", old_commit=True)
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers={7850},
            live_worker_ids=set(), proc_cwds=set())
        self.assertFalse(wt.exists(), "real-helper path must remove it: %s" % logs)
        self.assertTrue(any("remove" in ln for ln in logs), logs)

    # The removal-safety leaf plumbing is reused from cli_worktree_sweep, which
    # reads REAL mtimes (recency age) and REAL /proc (live-use). A freshly-created
    # tempdir worktree is always "recent" and holds no rooted process, so the
    # tests inject the age_fn / live_use_fn seams to drive those two guards
    # deterministically; the clean guard runs the REAL git status on the real
    # worktree (so the dirty test needs no seam).
    _OLD = staticmethod(lambda p: 3 * 3600)     # idle > 2h
    _FRESH = staticmethod(lambda p: 60)         # idle < 2h
    _FREE = staticmethod(lambda p: False)       # no live process
    _HELD = staticmethod(lambda p: True)        # a live process holds it

    def test_finished_clean_old_worktree_removed_branch_kept(self):
        wt = self._add_lane("agent-fin", "montalu/7840-done")
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers={7840},
            live_worker_ids=set(), proc_cwds=set(),
            age_fn=self._OLD, live_use_fn=self._FREE)
        self.assertFalse(wt.exists(), "the worktree dir must be gone: %s" % logs)
        r = subprocess.run(["git", "-C", str(self.repo), "rev-parse",
                            "--verify", "montalu/7840-done"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "the branch ref must be kept")
        self.assertTrue(any("remove" in ln for ln in logs), logs)

    def test_dirty_worktree_kept(self):
        wt = self._add_lane("agent-dirty", "montalu/7841-done", dirty=True)
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers={7841},
            live_worker_ids=set(), proc_cwds=set(),
            age_fn=self._OLD, live_use_fn=self._FREE)
        self.assertTrue(wt.exists(), "a dirty worktree must be kept")
        self.assertTrue(any("dirty" in ln.lower() for ln in logs), logs)

    def test_process_holding_worktree_kept(self):
        wt = self._add_lane("agent-proc", "montalu/7842-done")
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers={7842},
            live_worker_ids=set(), proc_cwds=set(),
            age_fn=self._OLD, live_use_fn=self._HELD)
        self.assertTrue(wt.exists(), "a process-holding worktree must be kept")
        self.assertTrue(any("live use" in ln.lower() for ln in logs), logs)

    def test_young_worktree_kept(self):
        wt = self._add_lane("agent-young", "montalu/7843-done")
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers={7843},
            live_worker_ids=set(), proc_cwds=set(),
            age_fn=self._FRESH, live_use_fn=self._FREE)
        self.assertTrue(wt.exists(), "a <2h worktree must be kept: %s" % logs)
        self.assertTrue(any("last commit" in ln.lower() for ln in logs), logs)

    def test_idle_unmerged_worktree_kept(self):
        # no hand-off, no evidence => idle-unmerged => NOT a prune candidate,
        # even when old + free (the classifier keeps it out of the candidate set).
        wt = self._add_lane("agent-idle", "montalu/7844-wip")
        lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers=set(),
            live_worker_ids=set(), proc_cwds=set(),
            age_fn=self._OLD, live_use_fn=self._FREE)
        self.assertTrue(wt.exists(), "an idle-unmerged (live) worktree must be kept")

    def test_cadence_gate_skips_within_hour(self):
        self._add_lane("agent-cad", "montalu/7845-done")
        state = {}
        now = time.time()
        lr.prune_finished_worktrees(str(self.repo), now=now, state=state,
                                    live_worker_ids=set(), proc_cwds=set(),
                                    handoff_numbers=set())
        # second call within the hour is a no-op (returns [] without re-listing)
        logs2 = lr.prune_finished_worktrees(str(self.repo), now=now + 60,
                                            state=state, live_worker_ids=set(),
                                            proc_cwds=set(), handoff_numbers=set())
        self.assertEqual(logs2, [])


class TestVerbatimMove1107(TestCase):
    """#482 reconstruction: every moved function's source in cli_lane_liveness
    is byte-identical to its pre-move source in cli_lane_overlap (the whole
    point of a VERBATIM move — no logic changed)."""

    @classmethod
    def setUpClass(cls):
        out = subprocess.run(
            ["git", "-C", str(REPO), "show",
             "%s:cli_lane_overlap.py" % _PREMOVE_SHA],
            capture_output=True, text=True)
        assert out.returncode == 0 and out.stdout, (
            "pre-move blob %s:cli_lane_overlap.py unavailable (rc=%s): %s"
            % (_PREMOVE_SHA, out.returncode, out.stderr))
        cls.pre_lines = out.stdout.splitlines()
        cls.pre_spans = {}
        for node in ast.walk(ast.parse(out.stdout)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cls.pre_spans[node.name] = (node.lineno, node.end_lineno)

    def test_every_moved_function_is_byte_identical(self):
        for name in _MOVED_FUNCS:
            with self.subTest(fn=name):
                self.assertIn(name, self.pre_spans,
                              "%s not found in the pre-move overlap module" % name)
                a, b = self.pre_spans[name]
                pre = self.pre_lines[a - 1:b]
                got = inspect.getsource(getattr(lo, name)).splitlines()
                self.assertEqual(got, pre,
                                 "%s drifted from its pre-move source" % name)


class TestBackCompatReexport1107(TestCase):
    """cli_lane_overlap re-exports the moved public names for one release — the
    SAME objects (identity), so an out-of-tree caller of cli_lane_overlap keeps
    reading the canonical derivation."""

    def test_public_names_are_the_leaf_objects(self):
        for name in ("classify_lanes", "state_summary", "gather_live_lanes",
                     "_LIVE_STATES"):
            with self.subTest(name=name):
                self.assertIs(getattr(ovl, name), getattr(lo, name))


class TestConsumersImportLeaf1107(TestCase):
    """AST lock: cli_concurrency and watchdog/lane_reconcile import the liveness
    derivation from the leaf cli_lane_liveness, NOT from cli_lane_overlap
    (#1107 item 3 — every consumer reads the canonical home)."""

    def _imported_modules(self, relpath):
        src = (REPO / relpath).read_text(encoding="utf-8")
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
        return mods

    def test_cli_concurrency_imports_the_leaf(self):
        mods = self._imported_modules("cli_concurrency.py")
        self.assertIn("cli_lane_liveness", mods)
        self.assertNotIn("cli_lane_overlap", mods)

    def test_lane_reconcile_imports_the_leaf(self):
        mods = self._imported_modules("watchdog/lane_reconcile.py")
        self.assertIn("cli_lane_liveness", mods)
        self.assertNotIn("cli_lane_overlap", mods)


class TestBothImportOrders1107(TestCase):
    """The deferred bottom cross-imports break the load-time cycle in EITHER
    order — importing the leaf first, or the overlap module first, both succeed
    and resolve classify_lanes to the same object."""

    def _probe(self, first):
        code = (
            "import %s as m; import cli_lane_liveness as L; "
            "import cli_lane_overlap as O; "
            "assert O.classify_lanes is L.classify_lanes; "
            "assert L._run_default is O._run_default; print('OK')" % first)
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         "import-first=%s failed: %s" % (first, r.stderr))
        self.assertIn("OK", r.stdout)

    def test_leaf_first(self):
        self._probe("cli_lane_liveness")

    def test_overlap_first(self):
        self._probe("cli_lane_overlap")


if __name__ == "__main__":
    main()
