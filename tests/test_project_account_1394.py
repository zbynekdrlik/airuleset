"""#1394 (owner 23.11.2026): the owner's private Bazos.sk / Bazar.sk sales scripts
leave the shared `newlevel@dev2` account for their own project account `bazos` on
dev1 (owner's target change, 23.11.), so
the built-in privacy plugin can go off for that account ONLY (Claude Code reads its
switch from trusted settings alone, never from a project's settings). No GitHub
repo: the ads data stays off GitHub. Declared pending, then flipped live once the
root bootstrap ran on dev1 and `install` as the account returned 0 (23.11.), with
the owner + marek tab (alias `bazos`, no custom one needed), a registry row born
on the bazos account (`no-git`: no repo, so no peer-repo token for anyone) and the
airuleset infra peer link. The drop lane keeps its fixed port (#1237). The
pending path keeps its coverage by re-marking the account pending through a
REMOTE_HOSTS patch."""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import cli_account_bootstrap as bootstrap
import cli_account_policy as policy
import cli_accounts
import cli_drop_lanes as drop
import cli_fleet
import cli_peer_channel as cpc
import cli_project_ci_sync as ci_sync
import cli_project_gh_token as pgt
import cli_webterm as w
import cli_webterm_profiles as profiles
from cli_aliases import short_target_alias

ROOT = Path(__file__).resolve().parent.parent
ACCT = "bazos"
INFRA_TOPIC = ("airuleset infra: accounts, permissions, peer links, tooling. Ask or "
               "report here; airuleset decides and a ticket alone wakes no session")


def _json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _pending():
    """REMOTE_HOSTS with bazos marked pending again (the state before its root
    bootstrap), so the skip paths keep their coverage."""
    return mock.patch.object(cli_fleet, "REMOTE_HOSTS", [
        dict(h, pending=True) if h.get("user") == ACCT else h
        for h in cli_fleet.REMOTE_HOSTS])


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

    def test_repo_less_with_only_its_issues_tokens(self):
        # The owner's private sales data never goes to GitHub; the App token
        # is the airuleset issues-only one (#1199), never a repo token.
        for key in ("repo", "extra_repos", "repo_secrets", "code_only"):
            self.assertNotIn(key, self.spec, key)
        self.assertIs(self.spec.get("repo_less"), True)
        self.assertIs(self.spec.get("github_app"), True)
        self.assertIn(ACCT, pgt.github_app_accounts())

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

    def test_live_full_authority_fleet_entry_on_dev1(self):
        (entry,) = [h for h in cli_fleet.REMOTE_HOSTS if h.get("user") == ACCT]
        self.assertEqual(entry["name"], "bazos@dev1")
        self.assertEqual(entry["host"], "100.104.8.125")
        self.assertIs(entry["pending"], False)
        self.assertFalse(bootstrap.is_pending(ACCT))
        self.assertIn(ACCT, cli_fleet.FULL_AUTHORITY_USERS)

    def _mint_all(self):
        out = io.StringIO()
        with mock.patch.object(pgt, "mint_account", return_value=0) as m, \
                mock.patch.object(ci_sync, "run_after_mint", return_value=0), \
                redirect_stdout(out):
            rc = pgt.cmd_project_gh_token(mock.Mock(
                action="mint", account=None, all=True, dry_run=False, key=None))
        return rc, [c.args[0] for c in m.call_args_list], out.getvalue()

    def test_mint_all_reaches_it_live_but_the_secret_sync_never_does(self):
        # Live: mint --all mints its airuleset/peer issues tokens (repo-less,
        # no repo token); the CI secret sync has no repo to write to.
        rc, minted, out = self._mint_all()
        with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
            ci_sync.sync_all(run=lambda *a, **k: None)
        self.assertEqual(rc, 0)
        self.assertIn(ACCT, minted)
        self.assertNotIn("%s: SKIPPED" % ACCT, out)
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertIn(ACCT, cpc.accounts_for_host("dev1"))

    def test_unattended_paths_skip_it_while_pending(self):
        with _pending():
            self.assertTrue(bootstrap.is_pending(ACCT))
            rc, minted, out = self._mint_all()
            with mock.patch.object(ci_sync, "sync_account", return_value=0) as s:
                ci_sync.sync_all(run=lambda *a, **k: None)
            accts = cpc.accounts_for_host("dev1")
        self.assertEqual(rc, 0)
        self.assertIn("project-gh-token: %s: SKIPPED — pending" % ACCT, out)
        self.assertNotIn(ACCT, minted)
        self.assertNotIn(ACCT, [c.args[0] for c in s.call_args_list])
        self.assertNotIn(ACCT, accts)

    def test_the_tab_opens_the_project_chat_for_marek_and_owner_only(self):
        for human in ("zbynek", "marek"):
            tabs = w.WEBTERM_DASHBOARD_TABS[human]
            self.assertEqual(tabs.index(ACCT), tabs.index("wall-songs") + 1)
        for human in ("david", "dominika", "timo"):
            self.assertNotIn(ACCT, w.WEBTERM_DASHBOARD_TABS[human])
        for inv in (profiles.marek_inventory(), profiles.zbynek_inventory()):
            (entry,) = [e for e in inv if e["id"] == ACCT]
            self.assertEqual(entry["user"], ACCT)

    def test_tab_alias_is_bazos_without_a_clash(self):
        # "bazos" fits the 8-char cut, so no named alias is needed.
        self.assertEqual(short_target_alias(ACCT, "bazos-dev1"), "bazos")
        users = set(bootstrap.SERVICE_ACCOUNTS) | {
            h.get("user", "") for h in cli_fleet.REMOTE_HOSTS}
        clash = [u for u in users - {ACCT} if short_target_alias(u, u) == "bazos"]
        self.assertEqual(clash, [])

    def test_registry_row_on_the_account_without_a_repo(self):
        # Born on the account (never newlevel, so LEGACY_CEILING stays); `no-git`
        # like mikrotik: no repo exists, so a project linked to bazos later never
        # tries to mint an issues token for zbynekdrlik/bazos (#1270).
        reg = _json("projects-registry.json")
        (row,) = [r for r in reg if r["name"] == ACCT]
        self.assertEqual(row, {
            "name": ACCT, "host": "dev1", "path": "/home/bazos/devel/bazos",
            "branch_model": "2-branch", "default_branch": "main", "work_branch": "dev",
            "overrides": ["no-git"], "onboarded": "2026-11-23", "account": ACCT,
            "stack": "python-tooling"})
        self.assertFalse(cli_accounts.is_legacy_account(ACCT))
        self.assertNotIn(ACCT, [r["name"] for r in cli_accounts.legacy_inventory(reg)])
        links = {"hosts": {"dev1": {"links": [[ACCT, "wall-songs"]], "topics": {}}}}
        self.assertEqual(cpc.peer_repos("wall-songs", links, reg), [])

    def test_peer_link_to_airuleset_with_the_infra_topic(self):
        dev1 = _json("peer-links.json")["hosts"]["dev1"]
        pairs = sorted(sorted(p) for p in dev1["links"] if ACCT in p)
        self.assertEqual(pairs, [["airuleset", ACCT]])
        self.assertEqual(dev1["topics"]["airuleset|bazos"], INFRA_TOPIC)
        self.assertEqual(cpc.validate_links(_json("peer-links.json"),
                                            _json("projects-registry.json")), [])
        # airuleset itself is no registry row: its issues token is the #1199 one.
        self.assertEqual(cpc.peer_repos(ACCT), [])

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
