"""#822 boundary-hold turn — REMOVED (#1084).

#822 built a boundary-hold turn (`compact-request --self` printing
`COMPACT_BOUNDARY_HOLD_CMD`, an armed `/goal` launching `sleep 45 && echo
boundary-hold` to give the pane an accepted Stop so a queued `/compact` drains).
#1084 (owner ROZHODNUTÉ 2026-09-19) removes machine-triggered compacts for good,
so the whole boundary-hold mechanism is gone: `cmd_compact_request` is a removed
stub (no hint), and the doctrine names no boundary-hold.

This is the L1 inverted lock; the still-valid #527 window guard is kept. L2 (a
later lane) deletes this file and the `COMPACT_BOUNDARY_HOLD_CMD` constant.
"""

import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SKILL = "skills/autopilot/SKILL.md"
COMPLETION = "modules/core/completion-report.md"


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _args(**kw):
    kw.setdefault("self", False)
    kw.setdefault("record", False)
    kw.setdefault("status", False)
    kw.setdefault("session", "")
    kw.setdefault("cwd", "")
    kw.setdefault("origin", "")
    return types.SimpleNamespace(**kw)


class TestBoundaryHoldRemoved(unittest.TestCase):
    def test_self_prints_no_boundary_hold_command(self):
        # #1084: --self is a removed stub — it prints the removed line, never a
        # boundary-hold command.
        buf = []
        with m.patch("sys.stdout") as out:
            out.write = lambda s: buf.append(s)
            airuleset.cmd_compact_request(_args(self=True))
        txt = "".join(buf)
        self.assertIn("machine compacts removed", txt)
        self.assertNotIn("boundary-hold", txt)
        self.assertNotIn("sleep 45", txt)

    def test_skill_names_no_boundary_hold_mechanism(self):
        t = read(SKILL)
        self.assertNotIn("boundary-hold task", t)
        self.assertNotIn("compact-request --status", t)
        self.assertIn("machine compacts are REMOVED", t)

    def test_completion_report_names_no_boundary_hold(self):
        t = read(COMPLETION)
        self.assertNotIn("boundary-hold", t)
        # (completion-report.md still references `compact-request` only as a
        # situational-trigger load keyword, never as a boundary-hold instruction.)


class TestStep5WindowGuardStillHolds(unittest.TestCase):
    def test_step5_anchors_stay_inside_the_locked_window(self):
        # Guard against the #527 class: the Step-5 prose must NOT shove the
        # reduced-authority anchors past the t[idx:idx+3600] window that
        # test_goal_turn_boundary.py locks.
        t = read(SKILL)
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        for anchor in ("branch-merge", "fork-no-merge", "READY-FOR-REVIEW",
                       "Lokálne overenie", "2129"):
            rel = t.find(anchor, idx) - idx
            self.assertLess(rel, 3540, "%s at rel=%d too close to the 3600 window"
                            % (anchor, rel))


if __name__ == "__main__":
    unittest.main()
