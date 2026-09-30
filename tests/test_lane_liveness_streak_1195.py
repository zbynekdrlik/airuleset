"""#1195 item 3 — persistently unreadable lane liveness is surfaced in `status`.

``cli_lane_live_gate.LiveLaneGate.keep_reason`` keeps every ``agent-*``
worktree of a repo whose transcript evidence cannot be read, and said so only
in one stderr line per pass. A box whose evidence stayed unreadable kept those
worktrees forever and silently lost that part of its disk drain.

Now the disk-guard ``stale-agent-worktree`` rung records, per repo, how many
consecutive real (non-dry-run) passes found the evidence unreadable, in
``~/.claude/disk-guard/lane-liveness-unknown.json``. At
``ALERT_PASSES`` passes ``airuleset.py status`` prints
``lane liveness unreadable for <repo> since <ts>`` next to the existing
``root disk-guard:`` row. A readable pass resets the streak. No footer
segment, no Discord ping (#693).

Real git repos + real worktrees under a synthetic HOME; the only seam is the
transcript reader (``cli_lane_live_gate.live_worker_agent_ids_checked``).
"""
import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_lane_live_gate  # noqa: E402
from watchdog import disk_guard as dg  # noqa: E402
from watchdog import disk_guard_lane_unknown as dgl  # noqa: E402

NOW = 1_800_000_000.0
_ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, env=_ENV)


# --------------------------------------------------------------------------- #
# the pure streak update
# --------------------------------------------------------------------------- #

class TestUpdate(unittest.TestCase):
    def test_threshold_is_three_passes(self):
        self.assertEqual(dgl.ALERT_PASSES, 3)

    def test_consecutive_unknown_passes_count_up_and_keep_since(self):
        repos = {}
        for i in range(3):
            repos = dgl.update(repos, {"/r": "unlistable"}, NOW + i,
                               exists_fn=lambda p: True)
        self.assertEqual(repos["/r"]["passes"], 3)
        self.assertEqual(repos["/r"]["since"], NOW)
        self.assertEqual(repos["/r"]["last"], NOW + 2)
        self.assertEqual(repos["/r"]["err"], "unlistable")

    def test_readable_pass_resets(self):
        repos = dgl.update({}, {"/r": "x"}, NOW, exists_fn=lambda p: True)
        repos = dgl.update(repos, {"/r": None}, NOW + 1, exists_fn=lambda p: True)
        self.assertNotIn("/r", repos)
        repos = dgl.update(repos, {"/r": "x"}, NOW + 2, exists_fn=lambda p: True)
        self.assertEqual((repos["/r"]["passes"], repos["/r"]["since"]), (1, NOW + 2))

    def test_repo_not_consulted_keeps_its_streak(self):
        """A pass that never asked about a repo (every agent worktree locked or
        in live use) is no evidence either way."""
        repos = dgl.update({}, {"/r": "x"}, NOW, exists_fn=lambda p: True)
        repos = dgl.update(repos, {"/other": None}, NOW + 1, exists_fn=lambda p: True)
        self.assertEqual(repos["/r"]["passes"], 1)

    def test_vanished_checkout_is_dropped(self):
        repos = dgl.update({}, {"/r": "x"}, NOW, exists_fn=lambda p: True)
        self.assertEqual(dgl.update(repos, {}, NOW + 1, exists_fn=lambda p: False), {})

    def test_malformed_prior_entry_restarts(self):
        repos = dgl.update({"/r": {"passes": "many"}, "/q": 7}, {"/r": "x"}, NOW,
                           exists_fn=lambda p: True)
        self.assertEqual(repos["/r"]["passes"], 1)
        self.assertNotIn("/q", repos)


# --------------------------------------------------------------------------- #
# status lines
# --------------------------------------------------------------------------- #

class TestStatusLines(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / dgl.STATE_NAME

    def _write(self, passes):
        self.path.write_text(json.dumps({"repos": {"/home/x/devel/proj": {
            "passes": passes, "since": NOW, "last": NOW + 600, "err": "e"}}}))

    def test_below_threshold_prints_nothing(self):
        self._write(dgl.ALERT_PASSES - 1)
        self.assertEqual(dgl.status_lines(path=self.path), [])

    def test_at_threshold_prints_the_line(self):
        self._write(dgl.ALERT_PASSES)
        (line,) = dgl.status_lines(path=self.path)
        self.assertTrue(line.startswith(
            "lane liveness unreadable for /home/x/devel/proj since 2027-01-15T08:00:00Z"),
            line)
        self.assertIn("3 consecutive", line)

    def test_missing_or_corrupt_file_prints_nothing_and_never_raises(self):
        self.assertEqual(dgl.status_lines(path=self.path), [])
        self.path.write_text("{not json")
        self.assertEqual(dgl.status_lines(path=self.path), [])


# --------------------------------------------------------------------------- #
# the disk-guard rung records it; `status` prints it
# --------------------------------------------------------------------------- #

class TestRungAndStatus(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = self.home / "devel" / "proj"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("x\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")
        wt = self.repo / ".claude" / "worktrees" / "agent-abc"
        wt.parent.mkdir(parents=True)
        _git(self.repo, "worktree", "add", "-q", "-b", "worktree-agent-abc", str(wt))
        self.err = "subagents dir unlistable"

    def _evidence(self, *_a):
        return set(), self.err

    def _pass(self, now, wt_cache=True):
        with mock.patch.object(cli_lane_live_gate, "live_worker_agent_ids_checked",
                               self._evidence), \
                mock.patch("watchdog.disk_guard_worktrees._in_live_use",
                           return_value=False):
            return dg._plan_stale_agent_worktrees(str(self.home), now, wt_cache)

    def _lines(self):
        return dgl.status_lines(home=str(self.home))

    def test_rung_surfaces_after_n_passes_and_resets_on_readable(self):
        for i in range(dgl.ALERT_PASSES - 1):
            rows = self._pass(NOW + i)
            self.assertIn("lane liveness unknown", rows[0]["reason"])
            self.assertEqual(self._lines(), [])
        self._pass(NOW + 10)
        (line,) = self._lines()
        self.assertIn("lane liveness unreadable for %s since " % self.repo, line)
        self.err = None
        self._pass(NOW + 20)
        self.assertEqual(self._lines(), [])

    def test_dry_run_pass_records_nothing(self):
        for i in range(dgl.ALERT_PASSES + 1):
            self._pass(NOW + i, wt_cache=False)
        self.assertFalse((dg._guard_dir(str(self.home)) / dgl.STATE_NAME).exists())

    def test_cmd_status_prints_the_line_in_the_disk_guard_section(self):
        for i in range(dgl.ALERT_PASSES):
            self._pass(NOW + i)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                contextlib.redirect_stdout(buf):
            airuleset.cmd_status(argparse.Namespace(skill_parity=False))
        out = buf.getvalue()
        root = out.index("root disk-guard:")
        mine = out.index("lane liveness unreadable for %s since " % self.repo)
        self.assertEqual(out[root:mine].count("\n"), 1, out[root:mine + 80])


if __name__ == "__main__":
    unittest.main()
