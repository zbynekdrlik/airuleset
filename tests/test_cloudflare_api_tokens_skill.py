"""cloudflare-api-tokens skill (#443).

Captures the spinbike DNS incident's four lessons so no session repeats them:
the `/user/tokens/verify` 401-on-a-valid-zone-scoped-token trap, never
validating by length/shape, the naming convention, and immediate persistence
to `~/.secrets/cloudflare-<project>`. A model-loaded (user-invocable: false)
knowledge skill, deployed everywhere on demand — like its
deliver-files-as-urls / investigate-existing-first siblings.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "cloudflare-api-tokens" / "SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


class TestSkillRegisteredAndDeployedEverywhere(TestCase):
    def test_in_skill_names(self):
        # the inventory/lock: install/push deploys it, cmd_validate counts it.
        self.assertIn("cloudflare-api-tokens", airuleset.SKILL_NAMES)

    def test_skill_md_exists(self):
        self.assertTrue(SKILL.exists(), SKILL)

    def test_deploys_to_every_box_including_reduced_authority(self):
        # a hidden on-demand knowledge skill: no scoping exclusion, so every
        # user's set includes it (Cloudflare work can happen anywhere).
        self.assertNotIn("cloudflare-api-tokens", airuleset.SKILLS_MAINTAINER_ONLY)
        self.assertNotIn("cloudflare-api-tokens", airuleset.SKILLS_FULL_AUTHORITY_ONLY)
        for u in ("newlevel", "gatekeeper", "montalu", "david", "marek"):
            self.assertIn("cloudflare-api-tokens",
                          airuleset.skill_names_for_user(u), u)

    def test_hidden_from_slash_picker(self):
        # model-loaded by description, never a user-typed slash command — so it
        # stays out of the picker (matches deliver-files-as-urls et al.; #447).
        self.assertIn("user-invocable: false", read(SKILL))


class TestLoadBearingIncidentKnowledge(TestCase):
    """The four mistakes this skill exists to prevent must stay in the body."""

    def test_verify_endpoint_trap(self):
        t = read(SKILL)
        self.assertIn("/user/tokens/verify", t)
        # the load-bearing insight: it 401s on a VALID zone-scoped token
        self.assertIn("401", t)
        self.assertIn("for a perfectly VALID", t)

    def test_never_validate_by_length(self):
        self.assertIn("Never validate by length", read(SKILL))

    def test_which_token_for_which_job(self):
        t = read(SKILL)
        self.assertIn("Zone → DNS → Edit", t)
        self.assertIn("User → API Tokens → Edit", t)
        # the trap: that permission is under the USER group, not Account
        self.assertIn("under the **User** group", t)

    def test_naming_convention(self):
        self.assertIn("<project>-<purpose> · claude", read(SKILL))

    def test_persist_to_secrets_via_secret_channel(self):
        t = read(SKILL)
        self.assertIn("~/.secrets/cloudflare-", t)   # on-disk file keeps hyphens
        self.assertIn("secret request cloudflare_", t)  # vault NAME is underscore-only


def _description(path):
    """The `description:` line from the SKILL.md YAML frontmatter — the text
    that makes a session LOAD this skill. One physical line in the frontmatter."""
    for line in read(path).splitlines():
        if line.startswith("description:"):
            return line
    return ""


class TestCredentialTaxonomy480(TestCase):
    """The #480 additions: the four-artifact credential taxonomy and the
    account-owned `cfat_` trap (a VALID `cfat_` token rejected twice — the miva
    incident, 2026-08-15). Guards this load-bearing knowledge from silent
    deletion, exactly as TestLoadBearingIncidentKnowledge does for #443."""

    def test_description_triggers_on_credential_types(self):
        # the description is what makes a session LOAD this skill the moment a
        # cfat_ / account-owned token / Global API Key appears.
        desc = _description(SKILL)
        for needle in ("cfat_", "account-owned", "Global API Key"):
            self.assertIn(needle, desc, needle)

    def test_taxonomy_table_covers_all_four_artifacts(self):
        t = read(SKILL)
        self.assertIn("cfat_", t)            # account-owned token prefix
        self.assertIn("account-owned", t)
        self.assertIn("X-Auth-Email", t)     # Global API Key auth (NEVER Bearer)
        self.assertIn("X-Auth-Key", t)
        self.assertIn("Origin CA key", t)
        self.assertIn("v1.0-", t)            # Origin CA key shape

    def test_verify_endpoint_lies_for_account_tokens_by_design(self):
        # the core #480 trap: /user/tokens/verify returns `Invalid API Token`
        # for a VALID account-owned token, BY DESIGN.
        t = read(SKILL)
        self.assertIn("Invalid API Token", t)
        self.assertIn("BY DESIGN", t)
        self.assertIn("/accounts/{account_id}/tokens/verify", t)

    def test_capability_probe_is_the_correct_check(self):
        t = read(SKILL)
        self.assertIn("capability probe", t)
        self.assertIn("/client/v4/zones", t)
        self.assertIn("success:true", t)

    def test_never_reject_on_shape_never_escalate_to_global_key(self):
        t = read(SKILL)
        self.assertIn("never REJECT on shape", t)
        self.assertIn("Never escalate to a Global API Key", t)

    def test_trim_whitespace_before_use(self):
        self.assertIn("tr -d '[:space:]'", read(SKILL))


if __name__ == "__main__":
    main()
