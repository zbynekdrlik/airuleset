"""#1137 review A2 — behaviour locks for code the removal KEPT.

The retirement commit deleted whole test files whose other classes locked the
removed sequential / lane-fill / session-scope machinery. Some classes in them
locked code that stays in use; mutation showed those branches were then
untested. They are restored here, adapted minimally (originals in
`git show cde955bd:tests/<file>`):

* `watchdog.goal_scan.persisted_goal_mark` — the persisted `/goal` mark reader
  (was TestPersistedGoalMark, tests/test_lanefill_enforce_1089.py);
* `cli_lane_liveness.classify_lanes` #1229 branches — a commit-less lane with a
  live worker, a lane merged into `dev` (the `dev_ref` clause), a lane merged
  into main despite a stale `dev`, and a process left in a merged worktree
  (`alive_tx` vs `alive`) (tests/test_sequential_worktree_lane_1229.py);
* `cli_remote.LIVE_CLAUDE_PGREP` matching a full-path argv[0]
  (tests/test_session_scope_1224.py);
* the `--list-dispatchable` output format (tests/test_lanefill_1078.py).
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import cli_lane_liveness as lanes_mod  # noqa: E402
import watchdog  # noqa: E402
import watchdog.goal_scan as gs  # noqa: E402


def _state_file(tmp, sid, mark_state, tmtime):
    """An `api-watchdog-state.json` carrying the dark-watch per-sid record
    exactly as `goal_dark_watch` persists it: state["goal_mark"][sid]."""
    p = os.path.join(tmp, "api-watchdog-state.json")
    rec = {"off": 0, "tmtime": tmtime, "pv": 2,
           "mark": {"state": mark_state, "payload": "work the backlog"}}
    with open(p, "w") as f:
        json.dump({"goal_mark": {sid: rec}}, f)
    return p


class TestPersistedGoalMark(unittest.TestCase):
    def test_reads_set_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            rec = gs.persisted_goal_mark("sid-A", state_path=sp)
            self.assertIsInstance(rec, dict)
            self.assertEqual(rec["mark"]["state"], "set")

    def test_missing_sid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            self.assertIsNone(gs.persisted_goal_mark("sid-OTHER", state_path=sp))

    def test_missing_file_returns_none(self):
        self.assertIsNone(gs.persisted_goal_mark(
            "sid-A", state_path="/nonexistent/api-watchdog-state.json"))

    def test_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "s.json")
            Path(p).write_text("{not json")
            self.assertIsNone(gs.persisted_goal_mark("sid-A", state_path=p))

    def test_empty_sid_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = _state_file(tmp, "sid-A", "set", time.time())
            self.assertIsNone(gs.persisted_goal_mark("", state_path=sp))

    def test_default_state_path_is_watchdog_state_path(self):
        self.assertEqual(Path(watchdog.STATE_PATH).name, "api-watchdog-state.json")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t",
                        "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t"})


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "f.txt").write_text("x")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-qm", "base")

    def _lane(self, name, base=None, commit=True):
        wt = self.repo / ".claude" / "worktrees" / name
        extra = [base] if base else []
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-" + name,
             str(wt), *extra)
        if commit:
            (wt / (name + ".txt")).write_text("y")
            _git(wt, "add", name + ".txt")
            _git(wt, "commit", "-qm", "work " + name)
        return wt

    def _states(self, live_ids=(), proc_cwds=()):
        lanes = lanes_mod.classify_lanes(
            str(self.repo), live_worker_ids=set(live_ids),
            proc_cwds=set(proc_cwds), handoff_numbers=set(),
            projects_dir=self.tmp)
        return [lane["state"] for lane in lanes]


class TestLaneEvidenceOrder(_RepoCase):
    """A lane with NO commit yet has tip == its fork point (an ancestor of the
    base); its live worker must win over the merged reading."""

    def test_commit_less_lane_with_live_worker_is_live(self):
        self._lane("agent-fresh", commit=False)
        self.assertEqual(self._states({"agent-fresh"}), ["live"])

    def test_commit_less_lane_without_evidence_stays_merged(self):
        self._lane("agent-fresh", commit=False)
        self.assertEqual(self._states(), ["merged"])


class TestDevIntegrationBase(_RepoCase):
    """A 2-branch repo integrates on `dev`: a lane merged into dev is merged,
    not idle-unmerged against main (the `dev_ref` clause)."""

    def test_lane_merged_into_dev_reads_merged(self):
        _git(self.repo, "checkout", "-q", "-b", "dev")
        self._lane("agent-done", base="dev")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate lane",
             "worktree-agent-done")
        self.assertEqual(self._states(), ["merged"])


class TestStaleDevBranch(_RepoCase):
    """A repo working on main with a stale `dev`: a lane merged into main must
    read merged; judging only against the stale dev would keep it live."""

    def test_lane_merged_into_main_despite_stale_dev(self):
        _git(self.repo, "branch", "dev")
        (self.repo / "h.txt").write_text("z")
        _git(self.repo, "add", "h.txt")
        _git(self.repo, "commit", "-qm", "main moves on")
        self._lane("agent-done")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate lane",
             "worktree-agent-done")
        self.assertEqual(self._states(), ["merged"])


class TestMergedLaneLeftover(_RepoCase):
    """A process left in a MERGED worktree must not keep it live: only a live
    worker transcript (`alive_tx`) outranks the merged reading."""

    def test_process_left_in_a_merged_worktree_keeps_it_merged(self):
        wt = self._lane("agent-old")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate",
             "worktree-agent-old")
        self.assertEqual(self._states(proc_cwds={str(wt)}), ["merged"])

    def test_live_worker_in_a_merged_worktree_stays_live(self):
        self._lane("agent-old")
        _git(self.repo, "merge", "--no-ff", "-q", "-m", "integrate",
             "worktree-agent-old")
        self.assertEqual(self._states(live_ids={"agent-old"}), ["live"])


class TestLiveSessionCount(unittest.TestCase):
    """The push's live-session report must see a claude launched with a
    resolved argv[0] (`/home/<u>/.local/bin/claude …`)."""

    def test_the_push_report_matches_a_full_path_argv0(self):
        import cli_remote
        argv = ["bash", "-c", cli_remote.LIVE_CLAUDE_PGREP]
        with tempfile.TemporaryDirectory() as d:
            proc = subprocess.Popen(
                ["bash", "-c", "exec -a %s/.local/bin/claude sleep 30" % d])
            time.sleep(0.2)              # let the exec land before pgrep reads argv
            try:
                out = subprocess.run(argv, capture_output=True, text=True)
                self.assertIn(str(proc.pid), out.stdout.split())
            finally:
                proc.kill()
                proc.wait()


class TestListDispatchable(unittest.TestCase):
    """`--list-dispatchable`: number TAB title, oldest first, dep-wait members
    excluded, the unmeasurable head passed through, and its line count equal
    to `--count-dispatchable`."""

    def setUp(self):
        import cli_quals_cmd
        self.cqc = cli_quals_cmd
        self.rows = {5: {"title": "alpha", "createdAt": "2026-01-02"},
                     3: {"title": "beta", "createdAt": "2026-01-01"},
                     9: {"title": "gamma", "createdAt": "2026-01-03"}}

    def _emit(self, fn, dep_map, disp):
        import airuleset
        buf = io.StringIO()
        with mock.patch.object(self.cqc, "_dep_wait_map_for",
                               return_value=(dep_map, "montalu", True)), \
             mock.patch.object(airuleset, "dispatchable_numbers",
                               return_value=(disp, None)), \
             contextlib.redirect_stdout(buf):
            fn(self.rows, "/repo")
        return buf.getvalue()

    def test_list_is_number_tab_title_oldest_first(self):
        out = self._emit(self.cqc._emit_list_dispatchable, {}, {3, 5, 9})
        self.assertEqual(out, "3\tbeta\n5\talpha\n9\tgamma\n")

    def test_dep_wait_member_is_excluded(self):
        out = self._emit(self.cqc._emit_list_dispatchable, {5: ["#1"]}, {3, 9})
        self.assertEqual(out, "3\tbeta\n9\tgamma\n")

    def test_line_count_equals_count_dispatchable(self):
        disp = {3, 9}
        list_out = self._emit(self.cqc._emit_list_dispatchable, {5: ["#1"]}, disp)
        count_out = self._emit(self.cqc._emit_count_dispatchable, {5: ["#1"]}, disp)
        n_lines = len([ln for ln in list_out.splitlines() if ln.strip()])
        self.assertEqual(str(n_lines), count_out.splitlines()[0])

    def test_unmeasurable_head_passthrough(self):
        buf = io.StringIO()
        with mock.patch.object(self.cqc, "_dep_wait_map_for",
                               return_value=({}, None, False)), \
             contextlib.redirect_stdout(buf):
            self.cqc._emit_list_dispatchable(self.rows, "/repo")
        self.assertEqual(buf.getvalue().strip(), "unmeasurable:meta read failed")


if __name__ == "__main__":
    unittest.main()
