"""#1197 — W is never the owner: owner-dependent real-world completion is U.

Doctrine lock. `approval-scope.md` bans gating on events / "needs you present";
read literally, a session that drained its code backlog parked tickets whose
completion is an OWNER-run real-world step (an event switch, owner-at-PC
tuning, a consent) in `W` (`ops-wait`) instead of `U` (`needs-owner-action`),
and the owner never works W, so they rot (iemmixer, 2026-09-30). The fix
states the boundary where a session reads it:

(a) statusline-vocabulary.md — the W bullet says W is NEVER the owner;
(b) approval-scope.md — the reconciliation sentence: the ban covers event
    TIMING + deferring rig/prod work Claude can do itself, never parking
    owner-only completion in W;
(c) skills/autopilot/SKILL.md — the ops-wait park step carries the check;
(d) the Job 20 partition-audit nudge `_W_TRIGGER` says an ops-wait ticket
    waiting on the OWNER belongs in U, and it still fits the core budget.
"""
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from watchdog import ops_wait_recheck  # noqa: E402


def _flat(path):
    return " ".join((ROOT / path).read_text(encoding="utf-8").split())


def _w_bullet(text):
    m = re.search(r"- `· W N`(.*?)(?= - `· gk N`)", text)
    assert m, "W bullet not found in statusline-vocabulary.md"
    return m.group(1)


class StatuslineVocabularyWNeverOwner(unittest.TestCase):
    def test_w_bullet_says_w_is_never_the_owner(self):
        w = _w_bullet(_flat("modules/core/statusline-vocabulary.md"))
        self.assertIn("W is NEVER the owner (#1197)", w)
        self.assertIn("THIRD party", w)
        self.assertIn("`U` (`needs-owner-action`)", w)
        for need in ("an answer", "an approval", "a physical/manual step",
                     "a real event the owner runs"):
            self.assertIn(need, w, need)


class ApprovalScopeReconciliation(unittest.TestCase):
    def test_reconciliation_sentence_present(self):
        t = _flat("modules/quality/approval-scope.md")
        self.assertIn("Reconciliation with U (#1197)", t)
        self.assertIn("event TIMING", t)
        self.assertIn("never authorizes parking owner-only real-world "
                      "completion", t)
        self.assertIn("`U` (`needs-owner-action`) per #601", t)


class AutopilotParkStepCheck(unittest.TestCase):
    def test_park_step_carries_owner_check(self):
        t = _flat("skills/autopilot/SKILL.md")
        self.assertIn("completion needs the owner → U (`needs-owner-action`), "
                      "not W (#1197)", t)


class Job20NudgeOwnerLine(unittest.TestCase):
    def test_w_trigger_routes_owner_waiting_to_u(self):
        c = ops_wait_recheck._W_TRIGGER
        self.assertIn("čaká na OWNERA", c)
        self.assertIn("nikdy W", c)
        self.assertIn("#1197", c)
        self.assertIn("needs-owner-action", c)   # #601 lock kept
        self.assertIn("#601", c)

    def test_composed_nudge_carries_owner_line(self):
        t = ops_wait_recheck._nudge_text(0, [41], now=1000.0)
        self.assertIn("čaká na OWNERA", t)
        self.assertIn("nikdy W", t)

    def test_mandatory_core_keeps_headroom(self):
        # the owner line must not starve the greedy optional detail: the
        # mandatory core (worst-case counts) stays well under the cap.
        m = ops_wait_recheck
        core = (m._NUDGE_HEAD + (m._I_TRIGGER % 999) + " "
                + (m._W_TRIGGER % 99) + m._NUDGE_TAIL)
        self.assertLessEqual(len(core), m.NUDGE_MAX_CHARS - 150)


if __name__ == "__main__":
    unittest.main()
