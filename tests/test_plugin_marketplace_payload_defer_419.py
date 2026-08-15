"""#419 — private plugin marketplace payload decision lock.

The evaluation asked whether airuleset's skills/hooks/rules payload should
move OUT of the bespoke `airuleset.py install/push` deployer INTO a native
private plugin MARKETPLACE (audit #416's candidate verdict), and DEFERRED-KEPT
it: the native plugin framework has no declarative always-on-rules component,
no plugin-sourced path-scoped `.claude/rules/`, cannot set the managed
settings.json fleet-policy defaults (only `agent`+`subagentStatusLine`), and
cannot subset within one plugin — so the payload cannot move, the repo clone
survives regardless (making any plugin channel additive not a replacement),
and migration is net-negative fragility under the supervision FREEZE. Full
capability table + rejected HYBRID/MIGRATE approaches live on issue #419.

This test locks TWO things so a future native-now re-audit (#423) re-validates
against the recorded decision instead of silently re-litigating it:

  1. the DECISION is durably recorded in the path-scoped internals rule
     (`.claude/rules/airuleset-internals.md`) and cannot be silently dropped;
  2. the bespoke deployer the decision KEEPS still exists in code — the
     always-on CLAUDE.md generator, the path-scoped-rules symlinker, the
     per-box skill SUBSET function, and the managed settings.json defaults.
     If a future migration removes any of them while the doc still says
     "KEPT", this test fails and forces re-opening the #419 decision.

Written as a unittest.TestCase so `python3 -m unittest discover -s tests`
(cmd_push's gate) genuinely collects it — a bare `def test_x()` file with no
TestCase is silently skipped by that discovery mechanism.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INTERNALS = REPO / ".claude" / "rules" / "internals-hooks.md"  # #482: #419 decision moved here
AIRULESET = REPO / "airuleset.py"
# #433 L-A: skill_names_for_user was relocated VERBATIM into this leaf, still
# re-exported by airuleset.py — a relocation is not a deletion, so the #419
# skill-subset lock below checks the leaf owns the def AND airuleset.py re-exports it.
CLI_DEPLOYER_GLUE = REPO / "cli_deployer_glue.py"

# Distinctive substrings of the #419 decision bullet — sampled from its head,
# the decisive capability facts (no always-on component, settings-defaults not
# plugin-settable), the structural clincher, and the re-audit trigger, so the
# whole decision (not just a header) is proven present and verbatim. Every
# anchor is chosen to be #419-UNIQUE (occurs exactly once in the internals
# rule) — the generic "Re-audit trigger (per #423's ...)" header is shared
# verbatim with the sibling #418 bullet, so the re-audit anchor instead pins
# the #419-specific trigger CONDITION (per-installer skill-subset mechanism).
DECISION_ANCHORS = [
    "it is DEFERRED-KEEP and the bespoke `airuleset.py install/push` deployer "
    "is KEPT for the WHOLE payload (#419",
    "NO declarative always-on-rules component exists",
    "honors ONLY `agent`+`subagentStatusLine`, so the managed fleet-policy "
    "settings defaults",
    "a plugin channel is ADDITIVE, not a replacement — push never retires",
    "a per-installer skill-subset mechanism flips this KEEP into a real "
    "migration evaluation",
]


class TestMarketplacePayloadDeferRecorded(unittest.TestCase):
    def test_internals_rule_exists(self):
        self.assertTrue(
            INTERNALS.exists(),
            "the #419 decision needs a durable, path-scoped surface to live on",
        )

    def test_decision_recorded_verbatim(self):
        text = INTERNALS.read_text(encoding="utf-8")
        for anchor in DECISION_ANCHORS:
            # assertTrue (not assertIn) so a failure does NOT dump the whole
            # ~1MB internals file as the mismatch haystack.
            self.assertTrue(
                anchor in text,
                "#419 decision anchor missing from the internals rule "
                "(silently dropped?): %r" % anchor[:70],
            )


class TestKeptDeployerStillExists(unittest.TestCase):
    """The decision is KEEP-custom; lock that the bespoke deployer machinery
    it keeps is actually present, so 'KEPT' can never quietly become a lie.
    Each kept piece maps to one of the four capability facts that make the
    native plugin framework un-adoptable for this payload."""

    def _airuleset(self) -> str:
        return AIRULESET.read_text(encoding="utf-8")

    # assertTrue (not assertIn) throughout so a failure does NOT dump the
    # whole airuleset.py source as the mismatch haystack.

    def test_always_on_claude_md_generator_kept(self):
        # Fact 1: no declarative always-on-rules plugin component exists, so
        # the @import CLAUDE.md generator stays in the deployer.
        self.assertTrue(
            "def generate_claude_md" in self._airuleset(),
            "the always-on ~/.claude/CLAUDE.md generator the #419 decision "
            "keeps is gone from airuleset.py — re-open #419",
        )

    def test_path_scoped_rules_symlinker_kept(self):
        # Fact 2: no native plugin-sourced path-scoped .claude/rules/ — the
        # deployer's own symlinker stays.
        self.assertTrue(
            "def symlink_global_rules" in self._airuleset(),
            "the path-scoped-rules symlinker the #419 decision keeps is gone "
            "from airuleset.py — re-open #419",
        )

    def test_per_box_skill_subset_kept(self):
        # Fact 4: no per-box subset within one plugin — the deployer's own
        # per-box subset selection stays. #433 L-A relocated the function
        # VERBATIM into the cli_deployer_glue.py leaf, still re-exported by
        # airuleset.py; a relocation within the deployer is NOT the deletion
        # the #419 lock guards against, so the lock now checks the leaf owns
        # the def AND airuleset.py still re-exports it (both must hold — a
        # future migration deleting either re-opens #419).
        text = self._airuleset()
        leaf = CLI_DEPLOYER_GLUE.read_text(encoding="utf-8")
        self.assertTrue(
            "def skill_names_for_user" in leaf,
            "the per-box skill SUBSET function the #419 decision keeps is gone "
            "from the deployer (cli_deployer_glue.py) — re-open #419",
        )
        self.assertTrue(
            "skill_names_for_user as skill_names_for_user" in text,
            "airuleset.py no longer re-exports the per-box skill SUBSET "
            "function the #419 decision keeps — re-open #419",
        )
        for marker in ("SKILLS_MAINTAINER_ONLY", "SKILLS_FULL_AUTHORITY_ONLY"):
            self.assertTrue(
                marker in text,
                "the per-box subset markers (%s) the #419 decision keeps are "
                "gone — a single plugin cannot subset, re-open #419" % marker,
            )

    def test_managed_settings_defaults_kept(self):
        # Fact 3: a plugin's settings.json honors only agent+subagentStatusLine,
        # so the managed fleet-policy settings defaults stay in the deployer.
        self.assertTrue(
            "def apply_managed_settings_defaults" in self._airuleset(),
            "the managed settings.json defaults the #419 decision keeps are "
            "gone from airuleset.py — a plugin cannot set them, re-open #419",
        )


if __name__ == "__main__":
    unittest.main()
