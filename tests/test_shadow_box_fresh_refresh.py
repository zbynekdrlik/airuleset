"""#473 — a claim about current PROD data on a prod-snapshot shadow box
requires a FRESH refresh; a stale idle snapshot is not prod.

Origin: montalu subdev 2026-08-14 — a "the teams are called Team A/B/C"
claim was derived from a stale idle-snapshot shadow box; the client had
renamed the teams on the real prod since the last refresh, so the claim was
already false.

These lock the verification-discipline caveat on the three surfaces where a
session makes a claim about current PROD data — the `verify-issue-still-valid`
skill's reproduce-LIVE step, the `ticket-validator` agent's reproduce step,
and the `post-deploy-verification` skill. Teeth: each asserts the specific
fresh-refresh + source/time rule that a revert would remove, and the
RECREATE-only-for-code-iteration carve-out that keeps the rule from
over-broadening onto code-state checks.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERIFY_SKILL = ROOT / "skills" / "verify-issue-still-valid" / "SKILL.md"
VALIDATOR = ROOT / "agents" / "ticket-validator.md"
POST_DEPLOY = ROOT / "skills" / "post-deploy-verification" / "SKILL.md"

# every surface must carry the repo-parameterised refresh-trigger example, so
# the rule points at a concrete mechanism rather than an abstract "refresh".
REFRESH_TRIGGER = "REFRESH-DEV-BOX-FROM-PROD"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(s):
    return " ".join(s.split())


class TestVerifySkillShadowBoxCaveat(unittest.TestCase):
    def setUp(self):
        self.t = read(VERIFY_SKILL)
        self.n = norm(self.t)

    def test_reproduce_live_step_names_the_stale_snapshot_hazard(self):
        self.assertIn("A stale idle snapshot is NOT current prod", self.t)
        self.assertIn("prod-snapshot shadow box", self.n.lower())

    def test_fresh_refresh_required_before_a_prod_data_claim(self):
        self.assertIn("FRESH refresh from PROD right before", self.n)
        self.assertIn(REFRESH_TRIGGER, self.t)

    def test_recreate_carve_out_and_source_time_labelling(self):
        # a fast RECREATE is only for code iteration, never a prod-data claim
        self.assertIn("committed-HEAD code iteration", self.n)
        # every prod-state claim states its source + time
        self.assertIn("SOURCE + TIME", self.t)


class TestTicketValidatorShadowBoxCaveat(unittest.TestCase):
    def setUp(self):
        self.t = read(VALIDATOR)
        self.n = norm(self.t)

    def test_reproduce_step_carries_the_shadow_caveat(self):
        self.assertIn("On a prod-snapshot shadow box", self.t)
        # scope the fresh-refresh requirement to the reproduce-step region
        i = self.t.index("Reproduce the CURRENT behavior")
        region = norm(self.t[i:i + 900])
        self.assertIn("FRESH refresh from PROD", region)
        self.assertIn("SOURCE + TIME", region)
        self.assertIn(REFRESH_TRIGGER, self.t)


class TestPostDeployShadowBoxCaveat(unittest.TestCase):
    def setUp(self):
        self.t = read(POST_DEPLOY)
        self.n = norm(self.t)

    def test_verbatim_anchor_survives(self):
        # post-deploy-verification is a VERBATIM-converted skill; my added
        # paragraph must not disturb the locked three-layer contract.
        self.assertIn(
            "Verification has THREE mandatory layers", self.t)

    def test_shadow_data_claim_needs_refresh(self):
        self.assertIn(
            "prod-snapshot SHADOW box, not the real prod", self.t)
        self.assertIn("needs a fresh refresh", self.n.lower())
        self.assertIn(REFRESH_TRIGGER, self.t)

    def test_rule_is_scoped_to_data_state_not_code_state(self):
        # the caveat must NOT over-broaden onto the version-label / functional
        # (code-state) layers — those a shadow reflects correctly.
        self.assertIn("only about DATA-state claims", self.n)
        self.assertIn("need no data refresh", self.n)


if __name__ == "__main__":
    unittest.main()
