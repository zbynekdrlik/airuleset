"""#1193 — no worktree reclaimer may remove a LIVE lane's isolation worktree.

Live (david1@subdev, 30.9.2026 00:21:46Z): the disk-guard ``stale-agent-worktree``
rung removed ``agent-a29b49e670b4cb8f5`` while that lane's own subagent
transcript was still being written. The rung reclaimed any unlocked, clean,
origin-contained ``agent-*`` worktree with no process cwd inside; an in-session
lane's process cwd is the MAIN checkout, so a lane that had just pushed and sat
between tool calls matched exactly.

The gate (check (e)): a worktree whose basename matches a fresh LIVE subagent
transcript (``cli_lane_liveness`` evidence: live / wedged / unreadable) is kept;
when that evidence cannot be read for a repo, the repo's worktrees are kept for
the pass. A finished or stale lane stays reclaimable. The same gate covers the
sibling reclaimers: ``cli_worktree_sweep.discover_stale_worktrees`` (feeds
``sweep_stale_worktrees``), ``cli_worktree_sweep.discover_reclaimable_worktrees``
(the disk-guard ``worktree`` rung) and ``lane_reconcile.prune_finished_worktrees``.

Real git repos + real subagent transcripts under a synthetic HOME; no sudo, no
permission tricks (CI runs as root), nothing outside the tempdir.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import watchdog.transcripts as T  # noqa: E402

LIVE_REASON = "live lane (fresh subagent transcript) — kept"

_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args, env=None):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                       text=True, env=env or _ENV)
    if r.returncode != 0:
        raise RuntimeError("git %s failed in %s: %s" % (args, cwd, r.stderr))
    return r.stdout


def _transcript(home, root, agent_id, *, finished=False, age_s=0.0, sid="sid1",
                api_error=False, meta=None):
    """A real subagent transcript at the path Claude Code writes it:
    ``<home>/.claude/projects/<enc(root)>/<sid>/subagents/<agent_id>.jsonl``.
    A last turn with a pending tool call reads LIVE; a final ``end_turn`` text
    reply reads FINISHED; an unrecovered api-error reads WEDGED; an mtime older
    than the window reads STALE. ``meta`` writes the sibling
    ``<agent_id>.meta.json`` Claude Code keeps next to it."""
    p = (Path(home) / ".claude" / "projects" / T.encode_project_dir(str(root))
         / sid / "subagents" / (agent_id + ".jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        p.with_name(agent_id + ".meta.json").write_text(json.dumps(meta))
    user = {"type": "user", "message": {"role": "user", "content": "Work issue"}}
    if api_error:
        last = {"type": "assistant", "isApiErrorMessage": True, "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "API Error: overloaded_error"}]}}
    elif finished:
        last = {"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "done"}],
            "stop_reason": "end_turn"}}
    else:
        last = {"type": "assistant", "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": "t1",
                                               "name": "Bash",
                                               "input": {"command": "sleep 1"}}],
            "stop_reason": "tool_use"}}
    p.write_text(json.dumps(user) + "\n" + json.dumps(last) + "\n")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


class _Box(unittest.TestCase):
    """A synthetic HOME holding one real repo (``devel/proj``) with a bare
    ``origin`` it has pushed ``main`` to."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.origin = self.home / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main",
                        str(self.origin)], check=True, capture_output=True)
        self.repo = self.home / "devel" / "proj"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        _git(self.repo, "remote", "add", "origin", str(self.origin))
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")

    def lane(self, name, branch=None):
        """A clean, unlocked lane worktree off main (HEAD == origin/main, so it
        is 'contained in origin' and 0 commits ahead)."""
        wt = self.repo / ".claude" / "worktrees" / name
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(self.repo, "worktree", "add", "-q", "-b",
             branch or ("worktree-" + name), str(wt))
        return wt


# --------------------------------------------------------------------------- #
# the disk-guard stale-agent-worktree rung (the live incident)
# --------------------------------------------------------------------------- #

class TestStaleAgentWorktreeRung(_Box):

    def _rows(self, **kw):
        from watchdog import disk_guard_worktrees as dgw
        kw.setdefault("live_check_fn", lambda p: False)
        kw.setdefault("dir_stats_fn", lambda p: (1000, 0))
        rows = dgw.discover_stale_agent_worktrees(home=str(self.home),
                                                  now=time.time(), **kw)
        return {Path(r["path"]).name: r for r in rows}

    def test_live_lane_is_kept_with_reason(self):
        self.lane("agent-live1")
        _transcript(self.home, self.repo, "agent-live1")
        row = self._rows()["agent-live1"]
        self.assertEqual(row["kind"], "skip", row)
        self.assertEqual(row["reason"], LIVE_REASON)

    def test_wedged_lane_is_kept(self):
        """A fresh transcript ending in an unrecovered api-error is a WEDGED
        worker — still in flight and recoverable, so its worktree stays."""
        self.lane("agent-wedge1")
        _transcript(self.home, self.repo, "agent-wedge1", api_error=True)
        self.assertEqual(self._rows()["agent-wedge1"]["reason"], LIVE_REASON)

    def test_lane_waiting_on_its_own_child_agent_is_kept(self):
        """A lane blocked on a long foreground child (its review subagent)
        writes nothing to its OWN transcript; the live child's meta names the
        lane's worktree (``inheritedWorktreePath``), which keeps it."""
        wt = self.lane("agent-parent1")
        _transcript(self.home, self.repo, "agent-parent1", age_s=20 * 60,
                    meta={"worktreePath": str(wt)})
        _transcript(self.home, self.repo, "agent-child1",
                    meta={"inheritedWorktreePath": str(wt),
                          "parentAgentId": "parent1"})
        self.assertEqual(self._rows()["agent-parent1"]["reason"], LIVE_REASON)

    def test_finished_child_does_not_keep_the_lane(self):
        wt = self.lane("agent-parent2")
        _transcript(self.home, self.repo, "agent-parent2", age_s=20 * 60)
        _transcript(self.home, self.repo, "agent-child2", finished=True,
                    meta={"inheritedWorktreePath": str(wt)})
        self.assertIsNone(self._rows()["agent-parent2"]["reason"])

    def test_finished_lane_is_reclaimed(self):
        self.lane("agent-done1")
        _transcript(self.home, self.repo, "agent-done1", finished=True)
        row = self._rows()["agent-done1"]
        self.assertIsNone(row["reason"], row)
        self.assertEqual(row["kind"], "worktree-remove")

    def test_stale_lane_is_reclaimed(self):
        self.lane("agent-old1")
        _transcript(self.home, self.repo, "agent-old1", age_s=3 * 3600)
        row = self._rows()["agent-old1"]
        self.assertIsNone(row["reason"], row)
        self.assertEqual(row["kind"], "worktree-remove")

    def test_lane_without_any_transcript_is_reclaimed(self):
        self.lane("agent-none1")
        row = self._rows()["agent-none1"]
        self.assertIsNone(row["reason"], row)

    def test_only_the_live_lane_is_kept(self):
        self.lane("agent-live2")
        self.lane("agent-done2")
        _transcript(self.home, self.repo, "agent-live2")
        _transcript(self.home, self.repo, "agent-done2", finished=True)
        rows = self._rows()
        self.assertEqual(rows["agent-live2"]["reason"], LIVE_REASON)
        self.assertIsNone(rows["agent-done2"]["reason"])

    def test_unreadable_evidence_keeps_the_repo_worktrees(self):
        """count_live_workers could not read a session (it warns, returns
        nothing): 'no live lanes' cannot be told from 'could not tell', so the
        repo's agent worktrees are all kept for this pass."""
        self.lane("agent-unk1")
        _transcript(self.home, self.repo, "agent-other", finished=True)

        def _cannot_read(projects_dir, cwd, sid, now, fresh, *, on_warn=None):
            (on_warn or (lambda m: None))("subagents dir unreadable (x): EACCES")
            return 0, []

        with mock.patch("watchdog.count_live_workers", _cannot_read):
            row = self._rows()["agent-unk1"]
        self.assertEqual(row["kind"], "skip", row)
        self.assertIn("liveness unknown", row["reason"])

    def test_evidence_read_crash_keeps_the_repo_worktrees(self):
        self.lane("agent-unk2")
        with mock.patch.object(T, "encode_project_dir",
                               side_effect=RuntimeError("boom")):
            row = self._rows()["agent-unk2"]
        self.assertEqual(row["kind"], "skip", row)
        self.assertIn("liveness unknown", row["reason"])

    def test_live_lane_never_enters_the_verdict_cache(self):
        from watchdog import disk_guard_wt_cache as wtc
        cache = self.home / "guard" / wtc.VERDICTS_NAME
        cache.parent.mkdir()
        wt = self.lane("agent-cache1")
        _transcript(self.home, self.repo, "agent-cache1")
        row = self._rows(cache_path=cache)["agent-cache1"]
        self.assertEqual(row["reason"], LIVE_REASON)
        self.assertNotIn("cached", row)
        text = cache.read_text() if cache.exists() else ""
        self.assertNotIn(str(wt), text,
                         "a live-lane keep must never be stored as a verdict")

    def test_cached_verdict_never_bypasses_the_live_gate(self):
        """Pass 1 stores a plain keep verdict (dirty tree, lane not live). Pass
        2: the lane is live with its fingerprint unchanged; (e) answers before
        the cache, so the row carries the live reason, not the reused one."""
        from watchdog import disk_guard_wt_cache as wtc
        cache = self.home / "guard" / wtc.VERDICTS_NAME
        cache.parent.mkdir()
        wt = self.lane("agent-cache2")
        (wt / "scratch.txt").write_text("uncommitted\n")
        # Files written in the index's own second are "racily clean": git
        # status rewrites the index on every call and the verdict is never
        # stable enough to store. Age them the way a finished lane's are.
        old = time.time() - 1000
        for dp, _dn, fns in os.walk(wt):
            for fn in fns:
                os.utime(os.path.join(dp, fn), (old, old))
        # a verdict is stored only once the fingerprint is stable across a
        # pass (git status may refresh the index on the first one)
        for _ in range(3):
            first = self._rows(cache_path=cache)["agent-cache2"]
            if cache.exists() and str(wt) in cache.read_text():
                break
        self.assertEqual(first["reason"], "dirty worktree — kept")
        self.assertIn(str(wt), cache.read_text())
        self.assertTrue(self._rows(cache_path=cache)["agent-cache2"].get("cached"),
                        "precondition: pass 1's keep verdict is now served cached")
        _transcript(self.home, self.repo, "agent-cache2")
        second = self._rows(cache_path=cache)["agent-cache2"]
        self.assertEqual(second["reason"], LIVE_REASON)
        self.assertNotIn("cached", second)

    def test_finished_lane_after_live_pass_is_reclaimed_fresh(self):
        from watchdog import disk_guard_wt_cache as wtc
        cache = self.home / "guard" / wtc.VERDICTS_NAME
        cache.parent.mkdir()
        self.lane("agent-cache3")
        _transcript(self.home, self.repo, "agent-cache3")
        self.assertEqual(self._rows(cache_path=cache)["agent-cache3"]["reason"],
                         LIVE_REASON)
        _transcript(self.home, self.repo, "agent-cache3", finished=True)
        row = self._rows(cache_path=cache)["agent-cache3"]
        self.assertIsNone(row["reason"], row)
        self.assertEqual(row["kind"], "worktree-remove")


# --------------------------------------------------------------------------- #
# sibling reclaimers
# --------------------------------------------------------------------------- #

class TestWorktreeSweepDiscovery(_Box):
    """cli_worktree_sweep.discover_stale_worktrees — feeds sweep_stale_worktrees
    (install/push + `sweep-worktrees`)."""

    def _rows(self):
        import cli_worktree_sweep as ws
        rows = ws.discover_stale_worktrees(home=str(self.home), now=time.time())
        return {Path(r["path"]).name: r for r in rows if r.get("path")}

    def test_live_lane_is_not_a_candidate(self):
        self.lane("agent-sw1")
        _transcript(self.home, self.repo, "agent-sw1")
        self.assertEqual(self._rows()["agent-sw1"]["reason"], LIVE_REASON)

    def test_finished_lane_stays_a_candidate(self):
        self.lane("agent-sw2")
        _transcript(self.home, self.repo, "agent-sw2", finished=True)
        self.assertIsNone(self._rows()["agent-sw2"]["reason"])


class TestReclaimableWorktreeDiscovery(_Box):
    """cli_worktree_sweep.discover_reclaimable_worktrees — the disk-guard
    `worktree` rung (fork-no-merge lanes)."""

    def _rows(self):
        import cli_worktree_sweep as ws
        rows = ws.discover_reclaimable_worktrees(
            home=str(self.home), now=time.time(), min_idle_s=0,
            in_live_use=lambda p: False)
        return {Path(r["path"]).name: r for r in rows}

    def test_live_lane_is_kept(self):
        self.lane("agent-rc1")
        _transcript(self.home, self.repo, "agent-rc1")
        self.assertEqual(self._rows()["agent-rc1"]["reason"], LIVE_REASON)

    def test_finished_lane_stays_reclaimable(self):
        self.lane("agent-rc2")
        _transcript(self.home, self.repo, "agent-rc2", finished=True)
        row = self._rows()["agent-rc2"]
        self.assertIsNone(row["reason"], row)


class TestPruneFinishedWorktrees(_Box):
    """lane_reconcile.prune_finished_worktrees — a lane whose tip is still an
    ancestor of main (a fresh lane before its first commit) classifies MERGED
    before any liveness check; it must still be kept while its transcript is
    live."""

    _OLD = staticmethod(lambda ref: 3 * 3600)
    _FREE = staticmethod(lambda p: False)

    def _prune(self, **kw):
        import watchdog.lane_reconcile as lr
        return lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers=set(),
            proc_cwds=set(), projects_dir=str(self.home / ".claude" / "projects"),
            age_fn=self._OLD, live_use_fn=self._FREE, **kw)

    def test_live_merged_lane_is_kept(self):
        wt = self.lane("agent-pr1")
        _transcript(self.home, self.repo, "agent-pr1")
        logs = self._prune()
        self.assertTrue(wt.exists(), "a live lane must never be pruned: %s" % logs)
        self.assertTrue(any("live lane" in ln for ln in logs), logs)

    def test_finished_merged_lane_is_pruned(self):
        wt = self.lane("agent-pr2")
        _transcript(self.home, self.repo, "agent-pr2", finished=True)
        logs = self._prune()
        self.assertFalse(wt.exists(), "a finished merged lane is pruned: %s" % logs)

    def test_unreadable_evidence_prunes_nothing(self):
        wt = self.lane("agent-pr3")
        with mock.patch.object(T, "encode_project_dir",
                               side_effect=RuntimeError("boom")):
            logs = self._prune()
        self.assertTrue(wt.exists(), "unknown liveness must keep it: %s" % logs)
        self.assertTrue(any("skip:lane-liveness-unknown" in ln for ln in logs), logs)


# --------------------------------------------------------------------------- #
# the evidence reader: "no live lanes" vs "could not tell"
# --------------------------------------------------------------------------- #

class TestCheckedEvidence(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.proj = self.home / ".claude" / "projects"
        self.root = str(self.home / "devel" / "proj")

    def test_no_transcripts_at_all_is_a_confident_empty(self):
        import cli_lane_live_gate as g
        ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj),
                                                   time.time())
        self.assertEqual((ids, err), (set(), None))

    def test_live_ids_are_read(self):
        import cli_lane_live_gate as g
        _transcript(self.home, self.root, "agent-a")
        _transcript(self.home, self.root, "agent-b", finished=True)
        ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj),
                                                   time.time())
        self.assertEqual((ids, err), ({"agent-a"}, None))

    def test_failure_is_reported_not_silent(self):
        import cli_lane_live_gate as g
        with mock.patch.object(T, "encode_project_dir",
                               side_effect=RuntimeError("boom")):
            ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj),
                                                       time.time())
        self.assertEqual(ids, set())
        self.assertTrue(err)

    def test_one_unreadable_session_never_sinks_the_others(self):
        """A session dir that cannot be looked into is an error for the gate,
        but the other sessions' live ids are still read (the overlap set's
        pre-#1193 behaviour: one bad session never empties the set)."""
        import cli_lane_live_gate as g
        _transcript(self.home, self.root, "agent-a", sid="sid1")
        _transcript(self.home, self.root, "agent-b", sid="sid2")
        real = g._is_dir

        def _flaky(path):
            if os.sep + "sid2" + os.sep in path:
                raise PermissionError(13, "Permission denied", path)
            return real(path)

        with mock.patch.object(g, "_is_dir", _flaky):
            ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj),
                                                       time.time())
        self.assertEqual(ids, {"agent-a"})
        self.assertIn("sid2", err)

    def test_unlistable_subagents_dir_is_not_a_confident_empty(self):
        """``count_live_workers`` walks ``subagents/`` with ``rglob``, which
        SWALLOWS a listing error: an unreadable dir would read as "no live
        lane". The gate reader must report it. Injected at ``os.scandir`` (what
        every directory walk calls) so it also holds as root."""
        import cli_lane_live_gate as g
        _transcript(self.home, self.root, "agent-a", sid="sid1")
        _transcript(self.home, self.root, "agent-b", sid="sid2")
        real = os.scandir

        def _denied(path="."):
            if os.sep + "sid2" + os.sep + "subagents" in os.fsdecode(path):
                raise PermissionError(13, "Permission denied", path)
            return real(path)

        with mock.patch("os.scandir", _denied):
            ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj),
                                                       time.time())
        self.assertIn("agent-a", ids)
        self.assertTrue(err, "an unlistable subagents dir must be 'could not tell'")

    def test_unknown_evidence_keeps_only_agent_worktrees(self):
        """Transcript evidence can only ever name an ``agent-*`` worktree, so an
        evidence error keeps those and leaves any other worktree to its own
        reclaim rules (never a permanent keep of unrelated trees)."""
        import cli_lane_live_gate as g
        gate = g.LiveLaneGate(projects_dir=str(self.proj),
                              evidence_fn=lambda *_a: (set(), "boom"))
        wts = self.root + "/.claude/worktrees/"
        self.assertIn("liveness unknown", gate.keep_reason(self.root, wts + "agent-q"))
        self.assertIsNone(gate.keep_reason(self.root, wts + "issue-12"))

    def test_gate_memoizes_one_read_per_repo_per_pass(self):
        import cli_lane_live_gate as g
        calls = []

        def _ev(root, projects_dir, now):
            calls.append(root)
            return {"agent-x"}, None

        gate = g.LiveLaneGate(projects_dir=str(self.proj), evidence_fn=_ev)
        self.assertEqual(gate.keep_reason(self.root, self.root + "/.claude/worktrees/agent-x"),
                         LIVE_REASON)
        self.assertIsNone(gate.keep_reason(self.root, self.root + "/.claude/worktrees/agent-y/"))
        self.assertEqual(calls, [self.root])

    def test_legacy_reader_keeps_its_contract(self):
        """Other callers (classify_lanes / the overlap set) still get a bare
        set, empty on failure — unchanged."""
        import cli_lane_liveness as lo
        _transcript(self.home, self.root, "agent-a")
        self.assertEqual(lo._live_worker_agent_ids(self.root, str(self.proj),
                                                   time.time()), {"agent-a"})
        with mock.patch.object(T, "encode_project_dir",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(lo._live_worker_agent_ids(
                self.root, str(self.proj), time.time()), set())


if __name__ == "__main__":
    unittest.main()
