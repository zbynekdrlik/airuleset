"""#1321 — a hand-made gatekeeper drop-in `user-.slice.d/50-fairness.conf` (8.8.2026,
from the 4-core era: CPUQuota=300%, CPUWeight=100, TasksMax=8192) sorts AFTER
airuleset's `50-airuleset-resource-guard.conf`, so systemd lets it win on every
daemon-reload: the managed CPUQuota/CPUWeight/TasksMax never took effect, and after
the push the live set-property values were overwritten by the next reload.

The apply removes that file (only when it carries the gatekeeper marker, backup in
/root), before the daemon-reload. The managed TasksMax rises to 2048 (david3 peaked
at 501 tasks under the old 8192 that the stale file set).
"""
import os
import subprocess
import tempfile
import unittest

import cli_resource_guards as g

MARKER = "# Managed by gatekeeper (odoo-erp subdev VPS fairness, 2026-08-08).\n"


class TestStaleFairnessRemoval(unittest.TestCase):
    def _run(self, files):
        d = tempfile.mkdtemp()
        dropins, backup = os.path.join(d, "user-.slice.d"), os.path.join(d, "root")
        os.makedirs(dropins)
        os.makedirs(backup)
        for name, body in files.items():
            with open(os.path.join(dropins, name), "w") as fh:
                fh.write(body)
        script = g.render_stale_fairness_removal(
            path=os.path.join(dropins, "50-fairness.conf"), backup_dir=backup)
        res = subprocess.run(["bash", "-c", "set -euo pipefail\n" + script],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 0, res.stderr)
        return dropins, backup, res.stdout

    def test_marked_file_is_removed_with_a_backup(self):
        dropins, backup, out = self._run({"50-fairness.conf": MARKER + "[Slice]\nCPUQuota=300%\n"})
        self.assertFalse(os.path.exists(os.path.join(dropins, "50-fairness.conf")))
        self.assertEqual(len(os.listdir(backup)), 1)
        self.assertIn("removed", out)

    def test_unmarked_file_is_left_alone(self):
        dropins, backup, out = self._run({"50-fairness.conf": "[Slice]\nCPUQuota=300%\n"})
        self.assertTrue(os.path.exists(os.path.join(dropins, "50-fairness.conf")))
        self.assertEqual(os.listdir(backup), [])

    def test_absent_file_is_a_noop(self):
        _, backup, _ = self._run({})
        self.assertEqual(os.listdir(backup), [])

    def test_apply_removes_it_before_the_daemon_reload(self):
        s = g.build_apply_script()
        self.assertIn("/etc/systemd/system/user-.slice.d/50-fairness.conf", s)
        self.assertLess(s.index("50-fairness.conf"), s.index("systemctl daemon-reload"))


class TestTasksMax(unittest.TestCase):
    def test_tasks_max_fits_a_claude_with_subagents(self):
        self.assertEqual(g.TASKS_MAX, 2048)
        self.assertIn("TasksMax=2048", g.render_guard_dropin())


if __name__ == "__main__":
    unittest.main()
