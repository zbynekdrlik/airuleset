"""#1394 (owner 23.11.2026): the owner's private Bazos.sk / Bazar.sk sales scripts
leave the shared `newlevel@dev2` account for their own project account `bazos` on
dev1 (owner's target change, 23.11.), so
the built-in privacy plugin can go off for that account ONLY (Claude Code reads its
switch from trusted settings alone, never from a project's settings). No GitHub
repo: the ads data stays off GitHub. Declared PENDING until its root bootstrap ran
(#1267): no tab yet, no registry row (the project was never registered), and the
drop lane on its fixed port (#1237)."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import cli_account_bootstrap as bootstrap
import cli_account_policy as policy
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_ci_sync as ci_sync
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles

ACCT = "bazos"


class TestBazosAccount(unittest.TestCase):
    def setUp(self):
        self.spec = bootstrap.account_spec(ACCT)

    def test_declaration_is_valid_on_dev1_without_sudo_or_grants(self):
        self.assertEqual(bootstrap.validate_account(ACCT, self.spec), [])
        self.assertIs(self.spec["sudo"], False)
        self.assertEqual(self.spec["host"], "dev1")
        self.assertEqual(self.spec["secrets"], [])
        self.assertEqual(self.spec["project_dir"], "devel/bazos")
        self.assertEqual(self.spec["tmux_session"], "bazos")
        for grant in ("devices", "rtprio", "userns", "system_packages", "sudo_commands"):
            self.assertFalse(self.spec.get(grant), grant)

    def test_no_repo_and_no_github_app(self):
        # The owner's private sales data never goes to GitHub.
        for key in ("repo", "github_app", "extra_repos", "repo_secrets", "code_only"):
            self.assertNotIn(key, self.spec, key)
        self.assertNotIn(ACCT, pgt.github_app_accounts())

    def test_privacy_plugin_off_for_this_account_only(self):
        self.assertEqual(self.spec["builtin_plugins_off"], ["cc-plugin-privacy"])
        others = [a for a, raw in bootstrap.SERVICE_ACCOUNTS.items()
                  if a != ACCT and raw.get("builtin_plugins_off")]
        self.assertEqual(others, [])

    def test_playwright_pin_matches_the_scripts_python_package(self):
        # newlevel@dev2 ~/.local: pip playwright 1.62.0 (chromium build 1234), the
        # same pin claudy declares; the scripts launch full chromium.
        self.assertEqual(self.spec["tools"], ["playwright:1.62.0/chromium"])

    def test_tabs_for_the_owner_and_marek_like_the_other_dev1_accounts(self):
        self.assertEqual(self.spec["webterm_sessions"], {
            h: {"preferred": "bazos", "start_dir_chain": ["devel/bazos"]}
            for h in ("zbynek", "marek")})

    def test_pending_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "bazos@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], True)
        self.assertTrue(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def test_unattended_paths_skip_it(self):
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
            ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        self.assertNotIn(ACCT, [c.args[0] for c in m.call_args_list])
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertNotIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_no_dashboard_tab_while_pending(self):
        for human, tabs in w.WEBTERM_DASHBOARD_TABS.items():
            self.assertNotIn(ACCT, tabs, human)
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            self.assertEqual([e for e in inv if e["id"] == ACCT], [])

    def test_drop_port_is_fixed_and_free(self):
        # #1237: secret request / upload / share work for the account too.
        self.assertEqual(drop._GENERATED_DROP_PORTS[("dev1", ACCT)], 8918)
        self.assertEqual(list(drop._GENERATED_DROP_PORTS.values()).count(8918), 1)
        self.assertNotIn(8918, drop._RESERVED_PORTS)


class TestBuiltinPluginsOffDeclaration(unittest.TestCase):
    def _errs(self, value):
        return policy.validate_builtin_plugins_off({"builtin_plugins_off": value})

    def test_only_known_switchable_names(self):
        self.assertEqual(policy.SWITCHABLE_BUILTIN_PLUGINS, frozenset({"cc-plugin-privacy"}))
        self.assertEqual(self._errs(["cc-plugin-privacy"]), [])
        self.assertEqual(policy.validate_builtin_plugins_off({}), [])
        for bad in (["cc-plugin-sec-default"], ["caveman"], ["cc-plugin-privacy@builtin"]):
            self.assertNotEqual(self._errs(bad), [], bad)

    def test_shape_errors(self):
        self.assertNotEqual(self._errs("cc-plugin-privacy"), [])
        self.assertIn("builtin_plugins_off lists a plugin twice",
                      self._errs(["cc-plugin-privacy", "cc-plugin-privacy"]))

    def test_validate_account_rejects_an_unknown_name(self):
        raw = dict(bootstrap.SERVICE_ACCOUNTS[ACCT], builtin_plugins_off=["cc-plugin-sec-default"])
        errs = bootstrap.validate_account(ACCT, raw)
        self.assertTrue(any("cc-plugin-sec-default" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
