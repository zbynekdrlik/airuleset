"""#822 (d) — the boundary-hold turn that actually drains a queued `/compact`
under an armed `/goal`.

Under an armed `/goal` the goal Stop hook blocks every `✅` boundary
("◯ Goal not yet met… continuing"), so a queued `❯ /compact` never drains — CC
drains its type-ahead queue only at an ACCEPTED Stop. The lever is to give the
pane an accepted Stop: after `compact-request --self` the session launches ONE
short tracked background task (`sleep 45 && echo boundary-hold`, run_in_background)
and ends the turn `⏳ WORKING: boundary hold`. `compact-request --self` PRINTS the
exact command so the session never has to guess it. This locks:
  - the `COMPACT_BOUNDARY_HOLD_CMD` constant,
  - `--self` printing it when the compact could NOT execute (queued / gated),
  - the doctrine text in skills/autopilot/SKILL.md + modules/core/completion-report.md,
  - the HONEST live-verify-and-escalate caveat (the design's own hedge: if the
    goal re-fires even with a live task, escalate rather than stack a workaround).
"""

import sys
import types
import unittest
import unittest.mock as m
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
from watchdog import compact  # noqa: E402

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


class TestBoundaryHoldCommand(unittest.TestCase):
    def test_constant_is_the_boundary_hold_command(self):
        # The one command the session runs as a tracked background task so the
        # pane gets an accepted Stop that drains the queued /compact.
        self.assertEqual(getattr(compact, "COMPACT_BOUNDARY_HOLD_CMD", None),
                         "sleep 45 && echo boundary-hold")

    def _self_output(self, deliver_word):
        with m.patch.object(compact, "resolve_self_pane",
                            return_value=("%3", "/cwd", "sess-bh")):
            with m.patch.object(compact, "deliver_compact",
                                return_value=deliver_word):
                buf = []
                with m.patch("sys.stdout") as out:
                    out.write = lambda s: buf.append(s)
                    with m.patch("time.sleep", lambda *a, **k: None):
                        airuleset.cmd_compact_request(_args(self=True))
        return "".join(buf)

    def test_self_prints_the_command_when_goal_continuing(self):
        # skip:goal-continuing means the armed /goal blocked the type -> the
        # boundary needs the hold to drain, so --self prints the exact command.
        out = self._self_output("skip:goal-continuing")
        self.assertTrue(out.startswith("skip:goal-continuing"), out)
        self.assertIn("sleep 45 && echo boundary-hold", out)
        self.assertIn("run_in_background", out)

    def test_self_prints_the_command_when_queued(self):
        out = self._self_output("queued")
        self.assertTrue(out.startswith("queued"), out)
        self.assertIn("sleep 45 && echo boundary-hold", out)

    def test_self_does_not_print_the_command_on_a_clean_sent(self):
        # A compact that executed immediately needs no hold -> only the word.
        out = self._self_output("sent")
        self.assertEqual(out, "sent")


class TestBoundaryHoldDoctrine(unittest.TestCase):
    def test_skill_names_the_boundary_hold_mechanism(self):
        t = read(SKILL)
        self.assertIn("boundary hold", t)
        self.assertIn("sleep 45 && echo boundary-hold", t)
        self.assertIn("run_in_background", t)

    def test_skill_carries_the_live_verify_escalate_caveat(self):
        # The design's own hedge: if the goal re-fires even with a live task and
        # the /compact still does not drain, ESCALATE to the owner rather than
        # stack a further workaround. This is what keeps (d) honest.
        t = read(SKILL)
        idx = t.find("sleep 45 && echo boundary-hold")
        self.assertGreater(idx, 0)
        window = t[max(0, idx - 1600):idx + 1600]
        self.assertIn("ESCALATE", window)
        self.assertIn("LIVE-VERIFY", window)

    def test_completion_report_points_at_the_boundary_hold(self):
        t = read(COMPLETION)
        self.assertIn("sleep 45 && echo boundary-hold", t)

    def test_step5_anchors_stay_inside_the_locked_window(self):
        # Guard against the #527 class: the boundary-hold prose must NOT shove the
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
