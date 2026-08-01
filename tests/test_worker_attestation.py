"""#215 -- two attestation gaps in the worker prompt chain.

(1) `plan-check` was entirely absent from `agents/autopilot-worker.md` while
the SUPERVISOR's own Stop gate (`stop-check-prose-violations.sh`) hard-
requires the completion report to assert `✅ /plan-check: N/N fulfilled`
-- the supervisor was mechanically forced to attest to a check no worker was
ever told to run. Fix: a `plan:` evidence-block field (a per-issue
self-audit) the supervisor's line now genuinely relays.

(2) `agents/autopilot-worker.md` claimed playbook-review is "enforced by the
Stop gate `stop-check-playbook-review.sh`" -- that hook is registered under
`Stop` only (`settings/hooks.json`), and `Stop` never fires for a dispatched
subagent (`SubagentStop` does), so the claim was structurally false for the
exact surface it was written on. Fix: state honestly where the check fires
(the SUPERVISOR's stop, after relay) and what the worker's real obligation
is (running the Skill call, populating the line).

This file locks the doc fix; #216 locks the GENERAL reachability guarantee
this bug is one instance of.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKER_MD = ROOT / "agents" / "autopilot-worker.md"
SKILL_MD = ROOT / "skills" / "autopilot" / "SKILL.md"


class TestPlanCheckIsNoLongerAnUnbackedAttestation(unittest.TestCase):

    def setUp(self):
        self.worker_text = WORKER_MD.read_text(encoding="utf-8")
        self.skill_text = SKILL_MD.read_text(encoding="utf-8")

    def test_worker_prompt_mentions_plan_check_or_a_plan_field(self):
        self.assertRegex(self.worker_text, r"plan-check|`plan:`|^plan:",
                         "the worker prompt never mentions plan-check or a "
                         "plan: field -- the supervisor's forced attestation "
                         "still points at nothing the worker was told to do")

    def test_both_evidence_block_templates_carry_a_plan_line(self):
        # Both fenced templates under "FINAL MESSAGE" must each start a
        # line with "plan:" (the full-authority AND fork-no-merge variants).
        fences = re.findall(r"```\n(.*?)\n```", self.worker_text, re.S)
        self.assertGreaterEqual(len(fences), 2, "expected at least two "
                                "evidence-block templates (full + "
                                "fork-no-merge)")
        for fence in fences[:2]:
            self.assertRegex(fence, r"(?m)^plan:",
                             "template missing a plan: line:\n" + fence[:200])

    def test_supervisor_plan_check_line_relays_the_workers_own_field(self):
        # The supervisor's SKILL.md text must say it RELAYS the worker's
        # plan: field, never that it independently re-runs a plan-check
        # skill call itself -- that was the exact unbacked-attestation bug.
        idx = self.skill_text.find("/plan-check")
        self.assertNotEqual(idx, -1, "SKILL.md no longer mentions /plan-check")
        window = self.skill_text[idx:idx + 500]
        self.assertIn("RELAYS", window)
        self.assertIn("plan:", window)


class TestPlaybookEnforcementClaimIsHonest(unittest.TestCase):

    def setUp(self):
        self.text = WORKER_MD.read_text(encoding="utf-8")

    def test_the_false_claim_wording_is_gone(self):
        # The OLD, false claim: playbook-review is "enforced by the Stop
        # gate `stop-check-playbook-review.sh`" -- written as if that hook
        # checks the WORKER's own turn. It does not: Stop never fires for a
        # subagent. This exact phrase must not appear any more.
        self.assertNotIn(
            "MUST carry the `\U0001F4D4 Playbook:` line (enforced by the "
            "Stop gate `stop-check-playbook-review.sh`)", self.text)

    def test_the_corrected_text_names_the_real_firing_surface(self):
        idx = self.text.find("stop-check-playbook-review.sh")
        self.assertNotEqual(idx, -1,
                            "the hook should still be named, just accurately")
        window = self.text[max(0, idx - 400):idx + 400]
        # The corrected text must say the check fires at the SUPERVISOR's
        # stop, not the worker's own -- and must say why (Stop vs
        # SubagentStop), not just assert a bare correction with no reason.
        self.assertIn("SubagentStop", window)
        self.assertIn("Stop", window)

    def test_the_corrected_text_states_the_workers_real_obligation(self):
        idx = self.text.find("stop-check-playbook-review.sh")
        self.assertNotEqual(idx, -1)
        window = self.text[idx:idx + 400]
        self.assertRegex(window, r"[Ss]kill call")


if __name__ == "__main__":
    unittest.main()
