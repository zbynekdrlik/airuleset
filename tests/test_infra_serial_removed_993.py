"""#993 round 2b (finisher, owner directive 4 + MAIN REVIEW kolo 2) — the
class-based live-infra-lane SERIALISATION mechanism (B) is REMOVED.

Owner directive 4 (2026-09-12): infra serialisation is achieved by ROUTING
(label `infra` → the infra role/target, which runs in the sequential mode of
round 3), NOT by class-gating a live infra lane inside a parallel target. So
mechanism B — `live_infra_lane`, `_gather_live_lanes`,
`lane_class_from_issue_classes`, the `infra_lane_live` dispatchable parameter,
and the `skip:infra-serial` / "infra units are SERIAL" nudge+paste clauses — is
DELETED. What stays is DEPENDENCY ordering (A: `Depends-on:` → `dep-wait`) and
`work_class` for `--role` slicing only.

This is the RED lock the finisher's GREEN deletion must satisfy.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_work_class as wc  # noqa: E402
import cli_lane_overlap as lo  # noqa: E402
import goal_registry as gr  # noqa: E402


class TestMechanismBSymbolsGone(TestCase):
    def test_live_infra_lane_removed(self):
        self.assertFalse(hasattr(wc, "live_infra_lane"))

    def test_gather_live_lanes_helper_removed(self):
        self.assertFalse(hasattr(wc, "_gather_live_lanes"))

    def test_lane_class_from_issue_classes_removed(self):
        self.assertFalse(hasattr(wc, "lane_class_from_issue_classes"))

    def test_labels_of_removed(self):
        # only live_infra_lane used it
        self.assertFalse(hasattr(wc, "labels_of"))


class TestDispatchableIsDepsOnly(TestCase):
    def test_dispatchable_takes_only_dep_wait(self):
        # dispatchable = workable ∧ deps satisfied — no work-class / infra-lane arg
        self.assertTrue(wc.dispatchable(False))
        self.assertFalse(wc.dispatchable(True))


class TestGatherLiveLanesHasNoIssuesKey(TestCase):
    """The `issues`-from-commit-subject emission existed ONLY for B's
    live_infra_lane; overlap detection uses only files/topic/ref."""

    def _run(self, cmd):
        class _CP:
            def __init__(s, out="", rc=0):
                s.stdout, s.returncode = out, rc
        j = " ".join(cmd)
        if "worktree" in j and "list" in j:
            return _CP("branch refs/heads/main\n"
                       "\nbranch refs/heads/worktree-agent-x\n")
        if "merge-base" in j:
            return _CP("basesha\n")
        if "diff" in j:
            return _CP("some/file.py\n")
        if "log" in j:
            return _CP("green(#42): fix the thing\n")
        return _CP("", 1)

    def test_lane_record_has_no_issues_key(self):
        lanes = lo.gather_live_lanes("/root", run=self._run)
        self.assertEqual(len(lanes), 1)
        self.assertNotIn("issues", lanes[0])

    def test_lane_issues_helper_removed(self):
        self.assertFalse(hasattr(lo, "_lane_issues"))


class TestGoalPasteLinesDropInfraSerial(TestCase):
    def test_saturation_core_clause_has_no_infra_serial_half(self):
        core = next(c for c in gr.CLAUSES if c.id == "saturation-core").text
        self.assertIn("refill ONLY with a DISPATCHABLE unit", core)
        self.assertNotIn("infra units are SERIAL", core)
        self.assertNotIn("one live infra lane", core)


if __name__ == "__main__":
    main()
