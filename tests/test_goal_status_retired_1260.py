"""#1260 verification review: `airuleset.py status` must not read a retired-origin
request as "arming".

On a box with the owner goal kill-switch (david3), `run_once` never calls
`goal_sweep`, so a leftover `declared-virgin` request recorded before the deploy
stays on disk; `goal_status_probe` then printed `goal: arming … (request pending)`
for a session the owner never armed. RED against 53b7022f.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import watchdog as wd  # noqa: E402
from watchdog import compact, goal  # noqa: E402

from tests._goal_arm_helpers import GOAL_IDLE_CAP  # noqa: E402

CWD = "/home/david3/devel/odoo/odoo-erp"
SID = "128486e9-status"


def _row(origin):
    req = {SID: {"cwd": CWD, "ts": 1000, "origin": origin,
                 "authority": "fork-no-merge", "text": "/goal X"}}
    with m.patch.object(compact, "resolve_self_pane", return_value=("%0", CWD, SID)), \
         m.patch.object(wd, "capture_pane", return_value=GOAL_IDLE_CAP), \
         m.patch.object(goal, "load_goal_requests", return_value=req):
        return airuleset.goal_status_probe(CWD, run=lambda *a, **k: "", pane_env="%0")


class TestStatusRetired1260(unittest.TestCase):
    def test_retired_request_is_not_arming(self):
        row = _row("declared-virgin")
        self.assertNotIn("arming", row, row)

    def test_owner_request_still_reads_arming(self):
        self.assertIn("arming", _row("self-callback"))


if __name__ == "__main__":
    unittest.main()
