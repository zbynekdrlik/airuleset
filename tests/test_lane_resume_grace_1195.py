"""#1195 items 1 + 2 (owner decisions 30.9.2026).

Item 1 — resume grace. A supervisor resumes a FINISHED lane via SendMessage
(seen on the controller after 34 and after 331 min). The #1193 gate treated a
transcript ending in a terminal ``end_turn`` as done, so every reclaimer could
remove the worktree at once and the resume found it gone. The gate now keeps a
finished lane for ``FINISHED_GRACE_S`` (6 h) after its last turn; only disk
level ``critical`` waives it (the caller passes the level; a caller that does
not know it keeps the lane).

Item 2 — cross-user rung. ``watchdog/disk_guard.discover_stale_home_worktrees``
(#906) removed worktrees in OTHER accounts' homes with no live-lane evidence.
It is now report-only: each account's own disk-guard reclaims its own
worktrees behind the gate, and the executor refuses a cross-user remove.

Real subagent transcripts + real git repos under a synthetic HOME; no sudo,
nothing outside the tempdir (CI runs as root).
"""
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_lane_live_gate as g  # noqa: E402
from test_live_lane_worktree_gate_1193 import LIVE_REASON, _Box, _transcript  # noqa: E402
from watchdog import disk_guard as dg  # noqa: E402

GRACE_REASON = "finished lane in 6 h resume grace — kept"
H = 3600


class TestGateResumeGrace(unittest.TestCase):
    """``LiveLaneGate.keep_reason`` on real transcripts."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.root = str(self.home / "devel" / "proj")
        self.proj = self.home / ".claude" / "projects"

    def _reason(self, agent_id, level=None):
        gate = g.LiveLaneGate(projects_dir=str(self.proj), now=time.time(),
                              disk_level=level)
        return gate.keep_reason(self.root, self.root + "/.claude/worktrees/" + agent_id)

    def test_grace_constant_is_six_hours(self):
        self.assertEqual(g.FINISHED_GRACE_S, 6 * H)
        self.assertEqual(g.FINISHED_GRACE_KEPT, GRACE_REASON)

    def test_just_finished_lane_is_kept_in_grace(self):
        _transcript(self.home, self.root, "agent-f1", finished=True)
        self.assertEqual(self._reason("agent-f1"), GRACE_REASON)

    def test_finished_lane_hours_later_is_still_kept(self):
        """Past the 15 min liveness window the lane reads ``stale``; its
        terminal end_turn still proves a finished lane inside the grace."""
        _transcript(self.home, self.root, "agent-f2", finished=True, age_s=5.5 * H)
        self.assertEqual(self._reason("agent-f2"), GRACE_REASON)

    def test_finished_lane_past_grace_is_reclaimable(self):
        _transcript(self.home, self.root, "agent-f3", finished=True, age_s=6 * H + 60)
        self.assertIsNone(self._reason("agent-f3"))

    def test_critical_level_waives_the_grace(self):
        _transcript(self.home, self.root, "agent-f4", finished=True, age_s=H)
        self.assertIsNone(self._reason("agent-f4", level="critical"))

    def test_non_critical_levels_keep_the_grace(self):
        _transcript(self.home, self.root, "agent-f5", finished=True, age_s=H)
        for level in (None, "ok", "notice", "drain"):
            self.assertEqual(self._reason("agent-f5", level=level), GRACE_REASON, level)

    def test_critical_never_waives_a_live_lane(self):
        _transcript(self.home, self.root, "agent-l1")
        self.assertEqual(self._reason("agent-l1", level="critical"), LIVE_REASON)

    def test_unfinished_stale_lane_gets_no_grace(self):
        """A lane whose last turn is a pending tool call and that went quiet
        is not a FINISHED lane — the grace is only for a completed final reply."""
        _transcript(self.home, self.root, "agent-s1", age_s=2 * H)
        self.assertIsNone(self._reason("agent-s1"))

    def test_settled_text_tail_without_stop_reason_is_kept(self):
        """~18 % of real finished transcripts end with ``stop_reason: None``.
        Past the 15 min window such a tail is long settled (> FINISH_SETTLE_S),
        and the grace decides a KEEP, so it counts as finished (review r1)."""
        _transcript(self.home, self.root, "agent-st1", settling=True, age_s=H)
        self.assertEqual(self._reason("agent-st1"), GRACE_REASON)

    def test_critical_reads_no_transcript_content(self):
        """At ``critical`` the grace is waived before any tail read (review r1)."""
        import watchdog.transcripts as T
        _transcript(self.home, self.root, "agent-cr1", finished=True, age_s=H)
        with mock.patch.object(T, "transcript_worker_finished",
                               return_value="terminal") as read:
            self.assertIsNone(self._reason("agent-cr1", level="critical"))
        self.assertEqual(read.call_count, 0, "content read at critical")

    def test_finished_child_never_extends_the_parent_lane(self):
        wt = self.root + "/.claude/worktrees/agent-p1"
        _transcript(self.home, self.root, "agent-p1", age_s=7 * H)
        _transcript(self.home, self.root, "agent-c1", finished=True, age_s=H,
                    meta={"inheritedWorktreePath": wt})
        self.assertIsNone(self._reason("agent-p1"))

    def test_resumed_lane_reads_live_again(self):
        _transcript(self.home, self.root, "agent-r1", finished=True, age_s=2 * H)
        _transcript(self.home, self.root, "agent-r1")          # SendMessage resume
        self.assertEqual(self._reason("agent-r1"), LIVE_REASON)

    def test_legacy_pair_contract_is_unchanged(self):
        _transcript(self.home, self.root, "agent-f6", finished=True, age_s=H)
        ids, err = g.live_worker_agent_ids_checked(self.root, str(self.proj), time.time())
        self.assertEqual((ids, err), (set(), None))

    def test_injected_ids_carry_no_grace(self):
        """The ``ids=`` seam (lane_reconcile tests) keeps its exact meaning."""
        _transcript(self.home, self.root, "agent-f7", finished=True, age_s=H)
        gate = g.LiveLaneGate(projects_dir=str(self.proj), ids=set())
        self.assertIsNone(gate.keep_reason(self.root, self.root + "/.claude/worktrees/agent-f7"))


class TestReclaimersHonourTheGrace(_Box):
    """Every reclaimer that asks the gate keeps a just-finished lane."""

    def test_stale_agent_worktree_rung_keeps_then_waives_at_critical(self):
        self.lane("agent-dg1")
        _transcript(self.home, self.repo, "agent-dg1", finished=True, age_s=H)
        with mock.patch("watchdog.disk_guard_worktrees._in_live_use", return_value=False):
            kept = {Path(r["path"]).name: r for r in
                    dg._plan_stale_agent_worktrees(str(self.home), time.time())}
            waived = {Path(r["path"]).name: r for r in
                      dg._plan_stale_agent_worktrees(str(self.home), time.time(),
                                                     level="critical")}
        self.assertEqual(kept["agent-dg1"]["reason"], GRACE_REASON)
        self.assertEqual(kept["agent-dg1"]["kind"], "skip")
        self.assertIsNone(waived["agent-dg1"]["reason"], waived)

    def test_worktree_rung_keeps_then_waives_at_critical(self):
        """The own-home ``worktree`` rung (fork-no-merge lanes) builds its gate
        with the drain's level (idle floor lowered so only the gate decides)."""
        self.lane("agent-wr1")
        _transcript(self.home, self.repo, "agent-wr1", finished=True, age_s=H)
        with mock.patch.dict(os.environ, {"AIRULESET_WORKTREE_IDLE_MIN_AGE_S": "0"}):
            kept = {Path(r["path"]).name: r for r in
                    dg._plan_worktrees(str(self.home), time.time())}
            waived = {Path(r["path"]).name: r for r in
                      dg._plan_worktrees(str(self.home), time.time(), level="critical")}
        self.assertEqual(kept["agent-wr1"]["reason"], GRACE_REASON)
        self.assertEqual(kept["agent-wr1"]["kind"], "skip")
        self.assertIsNone(waived["agent-wr1"]["reason"], waived)
        self.assertEqual(waived["agent-wr1"]["kind"], "worktree-remove")

    def test_install_sweep_discovery_keeps_a_just_finished_lane(self):
        import cli_worktree_sweep as ws
        self.lane("agent-sw9")
        _transcript(self.home, self.repo, "agent-sw9", finished=True)
        rows = {Path(r["path"]).name: r for r in
                ws.discover_stale_worktrees(home=str(self.home), now=time.time())
                if r.get("path")}
        self.assertEqual(rows["agent-sw9"]["reason"], GRACE_REASON)

    def test_prune_keeps_a_just_finished_merged_lane(self):
        import watchdog.lane_reconcile as lr
        wt = self.lane("agent-pr9")
        _transcript(self.home, self.repo, "agent-pr9", finished=True, age_s=H)
        logs = lr.prune_finished_worktrees(
            str(self.repo), now=time.time(), handoff_numbers=set(), proc_cwds=set(),
            projects_dir=str(self.home / ".claude" / "projects"),
            age_fn=lambda ref: 3 * H, live_use_fn=lambda p: False)
        self.assertTrue(wt.exists(), logs)
        self.assertTrue(any(GRACE_REASON in ln for ln in logs), logs)


class TestDiskGuardPassesTheLevel(unittest.TestCase):
    """``run_disk_guard`` hands the pressure level to the worktree rungs."""

    def _run(self, used_pct):
        seen = {}

        def _planners(home, now, **kw):
            seen.update(kw)
            return [("noop", lambda: [])]

        free = int(1000 * (100 - used_pct) / 100)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(dg, "_default_planners", side_effect=_planners):
            dg.run_disk_guard(
                now=1000.0, home=td, dry_run=True,
                statvfs_fn=lambda _m: types.SimpleNamespace(
                    f_blocks=1000, f_bfree=free, f_bavail=free, f_frsize=4096,
                    f_files=100000, f_ffree=50000),
                dev_fn=lambda _p: 1, geteuid_fn=lambda: 1000,
                box_class_fn=lambda: "workstation",
                scratch_discover_fn=lambda *_a: [],
                severe_run_fn=lambda *a, **kw: None,
                top_consumers_fn=lambda *a, **kw: [])
        return seen

    def test_critical_pressure_reaches_the_planners(self):
        self.assertEqual(self._run(96).get("level"), "critical")

    def test_drain_pressure_keeps_the_grace(self):
        self.assertEqual(self._run(82).get("level"), "drain")   # below the 85 % small-disk critical

    def test_grace_level(self):
        """A quota drain (the account's own hard limit at >= CRITICAL_PCT)
        and the fs critical level waive the grace; so does the #968 small-disk
        critical threshold (85 % on a root fs <= 64 GB, e.g. gk cx23)."""
        from watchdog import disk_guard_quota as dgq
        big = lambda _p: types.SimpleNamespace(f_frsize=4096, f_blocks=10 ** 9)  # noqa: E731
        small = lambda _p: types.SimpleNamespace(f_frsize=4096, f_blocks=10 ** 6)  # noqa: E731
        drain = {"level": "drain", "worst_pct": 86}
        q = dgq.QuotaState(dg.CRITICAL_PCT, True, False, False, None, None)
        self.assertEqual(dg._lane_grace_level(drain, q, statvfs_fn=big), "critical")
        self.assertEqual(dg._lane_grace_level(drain, dgq._INACTIVE, statvfs_fn=big), "drain")
        self.assertEqual(dg._lane_grace_level(drain, dgq._INACTIVE, statvfs_fn=small),
                         "critical")
        self.assertEqual(dg._lane_grace_level({"level": "critical", "worst_pct": 91},
                                              dgq._INACTIVE, statvfs_fn=big), "critical")

    def test_fs_pass_after_a_quota_pass_keeps_the_level(self):
        """Both pressures: the fs pass rebuilds its planners — with the level."""
        from watchdog import disk_guard_quota as dgq
        seen = []

        def _planners(home, now, **kw):
            seen.append(kw.get("level"))
            return []

        q = dgq.QuotaState(92, True, False, False, None, None)
        status = {"level": "critical", "worst_pct": 96}
        with mock.patch.object(dg, "_default_planners", side_effect=_planners), \
                mock.patch.object(dgq, "run_quota_pass", return_value=[]), \
                mock.patch.object(dg, "execute_drain", return_value=[]):
            dgq.run_drain_passes(status, "/nonexistent-1195", 1.0, True, [], None, q,
                                 True, None, None, None, lambda: 1000, None)
        self.assertEqual(seen, ["critical"])

    def test_default_planners_thread_the_level(self):
        with mock.patch.object(dg, "_plan_stale_agent_worktrees",
                               return_value=[]) as sa, \
                mock.patch.object(dg, "_plan_worktrees", return_value=[]) as pw:
            planners = dict(dg._default_planners("/nonexistent-1195", 1.0,
                                                 level="critical"))
            planners["stale-agent-worktree"]()
            planners["worktree"]()
        self.assertEqual(sa.call_args.kwargs.get("level"), "critical")
        self.assertEqual(pw.call_args.kwargs.get("level"), "critical")


class TestCrossUserRungIsReportOnly(unittest.TestCase):
    """Item 2: another account's worktree is never a delete action."""

    def _foreign_tree(self, td, name):
        wt = Path(td) / "otheruser" / "devel" / "repo" / ".claude" / "worktrees" / name
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: /fake")
        old = time.time() - 3 * 86400
        os.utime(str(wt), (old, old))
        return wt

    def test_old_clean_foreign_lane_worktree_is_only_reported(self):
        with tempfile.TemporaryDirectory() as td:
            for name in ("agent-1", "issue-7"):
                self._foreign_tree(td, name)
            with mock.patch.object(dg.subprocess, "run",
                                   side_effect=AssertionError("no sudo/git on a foreign home")):
                rows = dg.discover_stale_home_worktrees(
                    now=time.time(), home_glob=str(Path(td) / "*"))
        self.assertEqual(len(rows), 1, rows)      # one row per foreign owner
        row = rows[0]
        self.assertEqual(row["kind"], "report", row)
        self.assertEqual(row["cls"], "home-worktree")
        self.assertEqual(row["owner"], "otheruser")
        self.assertIn("2 worktree(s)", row["reason"])
        self.assertIn("own disk-guard", row["reason"])
        self.assertEqual(row["bytes"], 0)

    def test_foreign_scan_is_bounded(self):
        """Review r2: the report rung reads a foreign home with two shallow
        globs (``devel/*`` and ``devel/*/*`` repos), never an unbounded
        ``os.walk`` of the other account's tree."""
        import inspect
        with tempfile.TemporaryDirectory() as td:
            for parts in (("r1",), ("org", "r2"), ("a", "b", "r3")):
                wt = Path(td, "otheruser", "devel", *parts, ".claude", "worktrees", "agent-x")
                wt.mkdir(parents=True)
            with mock.patch.object(dg.os, "walk",
                                   side_effect=AssertionError("unbounded walk")):
                rows = dg._find_worktree_dirs(home_glob=str(Path(td) / "*"))
        repos = sorted(Path(r).relative_to(Path(td, "otheruser", "devel")).as_posix()
                       for _w, _o, r in rows)
        self.assertEqual(repos, ["org/r2", "r1"])
        self.assertNotIn("exclude_own_user",
                         inspect.signature(dg._find_worktree_dirs).parameters)

    def test_planner_never_yields_a_delete(self):
        with tempfile.TemporaryDirectory() as td:
            self._foreign_tree(td, "agent-2")
            with mock.patch.object(dg, "HOME_WORKTREE_GLOB", str(Path(td) / "*")), \
                    mock.patch.object(dg.subprocess, "run",
                                      side_effect=AssertionError("no sudo/git on a foreign home")):
                rows = dg._plan_home_worktrees(td, time.time())
        self.assertTrue(rows)
        self.assertTrue(all(r["kind"] in ("report", "skip") for r in rows), rows)

    def test_executor_refuses_a_cross_user_remove(self):
        calls = []
        a = {"cls": "home-worktree", "kind": "home-worktree-remove",
             "path": "/home/x/devel/r/.claude/worktrees/agent-3",
             "repo": "/home/x/devel/r", "owner": "x", "bytes": 5}
        with self.assertRaises(OSError) as cm:
            dg._perform_action(a, sudo_ok=True, run_fn=lambda *a, **k: calls.append(a))
        self.assertIn("1195", str(cm.exception))
        self.assertEqual(calls, [])

    def test_no_cross_user_sudo_class(self):
        self.assertNotIn("home-worktree", dg.SUDO_CLASSES)


if __name__ == "__main__":
    unittest.main()
