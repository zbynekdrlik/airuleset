"""Locks the autopilot authority profiles (issue #16, 2026-07-09).

Incident: David's fork stream (no merge rights) invoked /autopilot and got the
hardcoded full-authority /goal condition ("merged PR to main + main green") —
unsatisfiable, so the run correctly refused to arm. Sub-dev streams must run
/autopilot AS-IS: the authority profile is a property of the USER (streams are
separate linux users), resolved at runtime from AUTHORITY_BY_USER — no per-box
state to lose on a home-dir migration.
"""

import sys
from pathlib import Path
from unittest import TestCase, main
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import airuleset

ROOT = Path(__file__).resolve().parent.parent


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestAuthorityResolution(TestCase):
    def test_known_stream_users_map_to_their_profiles(self):
        self.assertEqual(airuleset.AUTHORITY_BY_USER["david"], "fork-no-merge")
        self.assertEqual(airuleset.AUTHORITY_BY_USER["marek"], "branch-merge")
        self.assertEqual(airuleset.AUTHORITY_BY_USER["montalu"], "branch-merge")

    def test_resolve_defaults_to_full_for_unknown_user(self):
        with m.patch.object(airuleset, "_current_user", return_value="newlevel"):
            self.assertEqual(airuleset.resolve_authority(), "full")

    def test_resolve_uses_the_map_for_stream_users(self):
        with m.patch.object(airuleset, "_current_user", return_value="david"):
            self.assertEqual(airuleset.resolve_authority(), "fork-no-merge")

    def test_cli_prints_the_profile(self):
        with m.patch.object(airuleset, "_current_user", return_value="marek"):
            with m.patch("builtins.print") as p:
                airuleset.cmd_authority(m.Mock(explain=False))
        p.assert_any_call("branch-merge")


class TestAutopilotSkillCarriesProfiles(TestCase):
    SKILL = "skills/autopilot/SKILL.md"

    def test_step1_detects_authority(self):
        t = read(self.SKILL)
        self.assertIn("airuleset.py authority", t)
        self.assertIn("airuleset:authority=", t)  # per-project override marker

    def test_three_goal_templates_exist(self):
        t = read(self.SKILL)
        for marker in ("AUTHORITY: full", "AUTHORITY: branch-merge",
                       "AUTHORITY: fork-no-merge"):
            self.assertIn(marker, t)

    def test_fork_profile_never_opens_prs_and_hands_off(self):
        t = read(self.SKILL)
        self.assertIn("ready-for-review", t)
        self.assertIn("NEVER open or merge a PR", t)

    def test_fork_handoff_is_comment_primary_label_best_effort(self):
        # #17: David's fork-derived GitHub role is `read` — CANNOT add labels
        # (needs triage+). The hand-off signal must work at pure read role:
        # a READY-FOR-REVIEW: comment is PRIMARY; the label is best-effort only,
        # and the /goal proof must NOT hinge on a label search.
        t = read(self.SKILL)
        self.assertIn("READY-FOR-REVIEW:", t)
        self.assertIn("best-effort", t)
        self.assertNotIn('-label:ready-for-review', t)

    def test_worker_handoff_is_comment_primary(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("READY-FOR-REVIEW:", w)
        self.assertIn("best-effort", w)

    def test_branch_merge_profile_stops_at_integration_branch(self):
        t = read(self.SKILL)
        self.assertIn("INTEGRATION branch", t)
        self.assertIn("never staging/main", t.lower())

    def test_reduced_authority_scopes_backlog_to_assigned(self):
        t = read(self.SKILL)
        self.assertIn("--assignee @me", t)


class TestWorkerCarriesProfiles(TestCase):
    def test_worker_has_authority_section(self):
        w = read("agents/autopilot-worker.md")
        self.assertIn("Authority profile", w)
        self.assertIn("fork-no-merge", w)
        self.assertIn("branch-merge", w)

    def test_merge_policy_notes_reduced_authority(self):
        self.assertIn("airuleset.py authority",
                      read("modules/core/pr-merge-policy.md"))


if __name__ == "__main__":
    main()
