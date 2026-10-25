"""#1137 (owner 25.10.2026): the dev1 "don't work locally" doctrine is retired.

The new dev1 (16 threads, 31 GB, 879 GB) removed the reason for Tier-0, the
local-builds / fast-iterate escape hatches, the install-time target/ purge, the
tier-0 bypass auto-filer and the dev1 session memory scope. GitHub runners stay
the merge gate through pr-merge-policy / ci-monitoring, with no forcing rule.
The shared-stream (subdev) guards stay: they are gated on the box class.

Also locked here: the one addition of this round. On a full-authority box the
gk review lenses are excluded from auto-load in every declared window whose
role is not "review", by absolute path; a reduced account keeps the glob.
"""
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import airuleset  # noqa: E402
import cli_context_excludes  # noqa: E402
import cli_fleet  # noqa: E402
import cli_lane_target_reclaim  # noqa: E402
import cli_session_scope  # noqa: E402


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


class TestModule(unittest.TestCase):

    def test_module_file_is_gone(self):
        self.assertFalse((ROOT / "modules/quality/no-local-builds.md").exists())

    def test_no_profile_imports_it(self):
        for prof in (ROOT / "profiles").glob("*.profile"):
            self.assertNotIn("no-local-builds", prof.read_text(), prof.name)

    def test_ci_push_stub_says_local_work_is_allowed(self):
        self.assertIn(
            "CI on GitHub runners stays the merge gate; local builds and tests "
            "are allowed.", read("modules/core/ci-push-discipline.md"))

    def test_tooling_module_drops_the_guard_pointer(self):
        self.assertNotIn("(`cli_resource_guards`)",
                         read("modules/core/claude-code-tooling.md"))


class TestHook(unittest.TestCase):
    HOOK = "block-tier0-local-build.sh"

    def test_hook_file_is_gone(self):
        self.assertFalse((ROOT / "hooks" / self.HOOK).exists())

    def test_hook_is_not_registered(self):
        self.assertNotIn(self.HOOK, read("settings/hooks.json"))

    def test_mdreview_pairs_drop_it(self):
        import cli_mdreview_audit
        names = [p[0] for p in cli_mdreview_audit._HOOK_PROSE_PAIRS]
        self.assertNotIn(self.HOOK, names)


class TestSkills(unittest.TestCase):
    GONE = ("local-builds", "fast-iterate")

    def test_skill_dirs_are_gone(self):
        for name in self.GONE:
            self.assertFalse((ROOT / "skills" / name).exists(), name)

    def test_no_trigger_rows(self):
        topics = [ln.split("\t", 1)[0]
                  for ln in read("hooks/situational-triggers.conf").splitlines()
                  if ln and not ln.startswith("#")]
        for name in self.GONE:
            self.assertNotIn(name, topics)

    def test_not_in_the_managed_skill_sets(self):
        for name in self.GONE:
            self.assertNotIn(name, airuleset.SKILL_NAMES)
            self.assertNotIn(name, airuleset.SKILLS_MAINTAINER_ONLY)


class TestInstall(unittest.TestCase):

    def test_install_never_purges_targets(self):
        src = inspect.getsource(airuleset.cmd_install)
        self.assertNotIn("purge_stale_tier0_targets", src)
        self.assertNotIn("tier0_bypass", src)

    def test_purge_entry_points_are_gone(self):
        self.assertFalse(hasattr(airuleset, "purge_stale_tier0_targets"))
        self.assertNotIn("purge-targets", airuleset.SUBCOMMANDS)

    def test_lane_reclaim_has_no_bypass_filer(self):
        for name in ("_default_tier0_bypass_filer", "_tier0_flip_epoch",
                     "_record_tier0_bypass", "LANE_TARGET_TIER0_BYPASS_STATE_PATH"):
            self.assertFalse(hasattr(cli_lane_target_reclaim, name), name)
        params = inspect.signature(
            cli_lane_target_reclaim.purge_merged_lane_targets).parameters
        for name in ("issue_filer", "tier0_fn", "flip_epoch"):
            self.assertNotIn(name, params)

    def test_lane_reclaim_still_exists(self):
        self.assertTrue(callable(airuleset.purge_merged_lane_targets))
        self.assertIn("purge_merged_lane_targets",
                      inspect.getsource(airuleset.cmd_install))


class TestScope(unittest.TestCase):

    def test_dev1_is_not_session_scoped(self):
        self.assertNotIn("dev1", cli_session_scope.SCOPE_HOSTS)
        self.assertIn("dev2", cli_session_scope.SCOPE_HOSTS)


class TestLiveness(unittest.TestCase):

    def test_reap_clause_is_gone(self):
        for rel in ("modules/quality/verify-launched-work-liveness.md",
                    "skills/ci-monitoring-deep/DEEP.md"):
            self.assertNotIn("memoryPressure", read(rel), rel)


GK_WINDOWS = [
    {"name": "gk", "cwd": "~/devel/odoo/odoo-erp", "role": "review"},
    {"name": "gk-infra", "cwd": "~/devel/odoo/odoo-erp-infra", "role": "infra"},
    {"name": "gk-quality", "cwd": "~/devel/odoo/odoo-erp-quality", "role": "quality"},
]
INFRA = "/home/gatekeeper/devel/odoo/odoo-erp-infra/.claude/rules/gk-review-lenses*.md"
QUALITY = "/home/gatekeeper/devel/odoo/odoo-erp-quality/.claude/rules/gk-review-lenses*.md"


class TestLensWindows(unittest.TestCase):

    def _apply(self, settings, reduced=False, windows=GK_WINDOWS):
        with mock.patch.object(cli_fleet, "box_windows", return_value=windows), \
                mock.patch.object(cli_context_excludes, "_reduced_account",
                                  return_value=reduced), \
                mock.patch.dict(os.environ, {"HOME": "/home/gatekeeper"}):
            return cli_context_excludes.apply(json.loads(json.dumps(settings)))

    def test_full_account_excludes_the_non_review_windows(self):
        self.assertEqual(self._apply({})["claudeMdExcludes"], [INFRA, QUALITY])

    def test_foreign_entry_kept_and_idempotent(self):
        out = self._apply({"claudeMdExcludes": ["/keep/me"]})
        again = self._apply(out)
        self.assertEqual(again["claudeMdExcludes"], ["/keep/me", INFRA, QUALITY])

    def test_stale_managed_entry_removed(self):
        stale = "/home/gatekeeper/devel/old/.claude/rules/gk-review-lenses*.md"
        out = self._apply({"claudeMdExcludes": [stale, INFRA]})
        self.assertEqual(out["claudeMdExcludes"], [INFRA, QUALITY])

    def test_reduced_account_keeps_the_glob(self):
        out = self._apply({"claudeMdExcludes": [INFRA]}, reduced=True)
        self.assertEqual(out["claudeMdExcludes"],
                         [cli_context_excludes.LENS_EXCLUDE])

    def test_no_windows_no_entries(self):
        out = self._apply({"claudeMdExcludes": [INFRA]}, windows=[])
        self.assertNotIn("claudeMdExcludes", out)


if __name__ == "__main__":
    unittest.main()
