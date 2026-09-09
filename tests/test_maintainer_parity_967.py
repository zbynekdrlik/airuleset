"""#967: MAINTAINER_USERS parity — every member gets SKILLS_MAINTAINER_ONLY,
cli_aliases._OWNER_ALIAS_USERS tracks MAINTAINER_USERS, and status --skill-parity
reports missing/extra skills against skill_names_for_user().

RED tests — written BEFORE the fix; they MUST fail against the current code."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_aliases  # noqa: E402


class TestMaintainerUsersGetAllMaintainerSkills(unittest.TestCase):
    """Parametrized: EVERY member of MAINTAINER_USERS must receive the
    whole SKILLS_MAINTAINER_ONLY set from skill_names_for_user(user)."""

    def test_every_maintainer_user_gets_all_maintainer_only_skills(self):
        for user in airuleset.MAINTAINER_USERS:
            result = airuleset.skill_names_for_user(user)
            for skill in airuleset.SKILLS_MAINTAINER_ONLY:
                self.assertIn(
                    skill, result,
                    f"MAINTAINER_USERS member {user!r} is missing "
                    f"SKILLS_MAINTAINER_ONLY skill {skill!r}")

    def test_airuleset_user_is_in_maintainer_users(self):
        """The controller account 'airuleset' must be in MAINTAINER_USERS."""
        self.assertIn("airuleset", airuleset.MAINTAINER_USERS)


class TestNonMaintainerExcluded(unittest.TestCase):
    """Non-maintainer users must NOT receive SKILLS_MAINTAINER_ONLY
    (except SKILLS_EXTRA_BY_USER re-grants)."""

    def test_non_maintainer_excluded(self):
        for user in ("montalu1", "david1", "miva1"):
            result = airuleset.skill_names_for_user(user)
            extras = airuleset.SKILLS_EXTRA_BY_USER.get(user, set())
            for skill in airuleset.SKILLS_MAINTAINER_ONLY:
                if skill in extras:
                    self.assertIn(skill, result,
                                  f"{user!r} has {skill!r} in EXTRA but missing from result")
                else:
                    self.assertNotIn(skill, result,
                                     f"non-maintainer {user!r} should NOT get {skill!r}")


class TestOwnerAliasUsersParity(unittest.TestCase):
    """cli_aliases._OWNER_ALIAS_USERS must equal MAINTAINER_USERS."""

    def test_owner_alias_users_equals_maintainer_users(self):
        self.assertTrue(
            hasattr(cli_aliases, "_OWNER_ALIAS_USERS"),
            "cli_aliases must define _OWNER_ALIAS_USERS")
        self.assertEqual(
            set(cli_aliases._OWNER_ALIAS_USERS),
            set(airuleset.MAINTAINER_USERS),
            "_OWNER_ALIAS_USERS must equal MAINTAINER_USERS")

    def test_airuleset_user_gets_box_name_alias(self):
        """The 'airuleset' user should get box-name-based alias (owner branch)."""
        alias = cli_aliases.short_target_alias("airuleset", "controller-box")
        self.assertEqual(alias, "controll",
                         "'airuleset' user should key alias on box name")


class TestStatusSkillParity(unittest.TestCase):
    """status --skill-parity must compare installed vs expected skills."""

    def test_skill_parity_missing(self):
        """When a skill from skill_names_for_user() is not installed,
        --skill-parity reports it as missing."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skills_dir = home / ".claude" / "skills"
            skills_dir.mkdir(parents=True)
            # Install only one skill
            (skills_dir / "some-skill").mkdir()
            with m.patch.object(airuleset, "SKILLS_DIR", skills_dir), \
                    m.patch("airuleset.skill_names_for_user",
                            return_value=["some-skill", "missing-skill"]):
                result = airuleset.check_skill_parity()
            self.assertIn("missing-skill", result["missing"])
            self.assertNotIn("some-skill", result["missing"])

    def test_skill_parity_extra(self):
        """When a skill exists in ~/.claude/skills but is NOT in
        skill_names_for_user(), --skill-parity reports it as extra."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skills_dir = home / ".claude" / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "expected-skill").mkdir()
            (skills_dir / "unmanaged-skill").mkdir()
            with m.patch.object(airuleset, "SKILLS_DIR", skills_dir), \
                    m.patch("airuleset.skill_names_for_user",
                            return_value=["expected-skill"]):
                result = airuleset.check_skill_parity()
            self.assertIn("unmanaged-skill", result["extra"])
            self.assertNotIn("expected-skill", result["extra"])


if __name__ == "__main__":
    unittest.main()
