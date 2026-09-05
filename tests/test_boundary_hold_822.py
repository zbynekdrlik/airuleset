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
from tempfile import TemporaryDirectory

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
    def setUp(self):
        # Isolate the compact stores: `--self` runs the REAL
        # `_compact_sync_attempt`, whose `record_compact_request(path=None)`
        # would otherwise write the developer's real `~/.claude/compact-*.json`
        # (and leave a `sess-bh` entry the live watchdog churns) — the store the
        # 60s systemd watchdog reads. Patch the four path functions to a temp dir.
        d = TemporaryDirectory()
        self.addCleanup(d.cleanup)
        for name, fn in (("compact_requests_path", "compact-requests-test.json"),
                         ("compact_delivered_path", "compact-delivered-test.json"),
                         ("compact_sync_log_path", "compact-sync-test.log"),
                         ("compact_queued_path", "compact-queued-test.json")):
            p = m.patch.object(compact, name,
                               return_value=Path(d.name) / fn)
            p.start()
            self.addCleanup(p.stop)

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

    def test_self_prints_the_command_when_already_queued(self):
        # `already-queued` means a `/compact` row from a PRIOR delivery still sits
        # unexecuted in the pane — it equally needs the hold's accepted Stop to
        # drain, so --self prints the exact command at the boundary rather than
        # leaving the session to recover a turn later via --status (#822).
        out = self._self_output("already-queued")
        self.assertTrue(out.startswith("already-queued"), out)
        self.assertIn("sleep 45 && echo boundary-hold", out)
        self.assertIn("run_in_background", out)

    def test_self_prints_the_command_when_queued(self):
        out = self._self_output("queued")
        self.assertTrue(out.startswith("queued"), out)
        self.assertIn("sleep 45 && echo boundary-hold", out)

    def test_self_does_not_print_the_command_on_a_refuse_to_type_skip(self):
        # #822: there is NO `skip:goal-continuing` refuse-to-type word any more,
        # and an ordinary skip (busy pane, live task) is NOT a queued boundary —
        # it does not print the hold hint (the session retries via the sweep).
        out = self._self_output("skip:busy")
        self.assertEqual(out, "skip:busy")

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
        # #859 batch 4b: deep content in companion
        t = read(COMPLETION) + "\n" + read("skills/completion-report-deep/DEEP.md")
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
