"""#1394: a declared per-account switch for Claude Code BUILT-IN plugins.

`SERVICE_ACCOUNTS[acct]["builtin_plugins_off"]` makes `airuleset.py install`, run
AS that account, write `"<plugin>@builtin": false` into its ~/.claude/settings.json
`enabledPlugins` on every install. An account that declares nothing but carries a
`false` (newlevel@dev2's hand-set switch, from before the bazos account existed)
is reported as drift and KEPT, unless that `user@host` is named in the opt-in
`REMOVE_UNDECLARED_ON`: the supervisor removes newlevel's switch only after the
bazos cutover. Plus `accounts verify` reads a repo-less declaration (no repo, no
github_app) as n/a on gh-token and secret-sync, never a FAIL forever."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import airuleset
import cli_account_bootstrap as bootstrap
import cli_account_verify as verify
import cli_builtin_plugins as bp
import cli_caveman_plugins as cav
import cli_config

KEY = "cc-plugin-privacy@builtin"


class TestReconcile(unittest.TestCase):

    def test_a_declaring_account_gets_the_switch_off(self):
        out, drift = bp.reconcile({"enabledPlugins": {"x@y": True}}, "bazos", "dev1")
        self.assertIs(out["enabledPlugins"][KEY], False)
        self.assertIs(out["enabledPlugins"]["x@y"], True)
        self.assertEqual(drift, [])

    def test_a_declaring_account_with_the_plugin_on_is_flipped(self):
        out, _ = bp.reconcile({"enabledPlugins": {KEY: True}}, "bazos", "dev1")
        self.assertIs(out["enabledPlugins"][KEY], False)

    def test_a_fresh_settings_file_gets_the_switch(self):
        out, _ = bp.reconcile({}, "bazos", "dev1")
        self.assertEqual(out, {"enabledPlugins": {KEY: False}})

    def test_an_undeclared_switch_is_kept_and_reported(self):
        settings = {"enabledPlugins": {KEY: False}}
        out, drift = bp.reconcile(settings, "newlevel", "dev2")
        self.assertEqual(out, settings)
        self.assertEqual(drift, [KEY])

    def test_the_opt_in_removes_an_undeclared_switch(self):
        with mock.patch.object(bp, "REMOVE_UNDECLARED_ON", frozenset({"newlevel@dev2"})):
            out, drift = bp.reconcile({"enabledPlugins": {KEY: False, "a@b": True}},
                                      "newlevel", "dev2")
            other, _ = bp.reconcile({"enabledPlugins": {KEY: False}}, "newlevel", "dev1")
        self.assertEqual(out, {"enabledPlugins": {"a@b": True}})
        self.assertEqual(drift, [])
        self.assertEqual(other, {"enabledPlugins": {KEY: False}})   # dev1 not opted in

    def test_the_removal_opt_in_starts_empty(self):
        self.assertEqual(bp.REMOVE_UNDECLARED_ON, frozenset())

    def test_an_undeclared_account_without_the_key_is_untouched(self):
        for settings in ({}, {"enabledPlugins": {KEY: True}}, {"enabledPlugins": {"a@b": False}}):
            out, drift = bp.reconcile(settings, "newlevel", "dev2")
            self.assertEqual(out, settings)
            self.assertEqual(drift, [])

    def test_the_input_is_never_mutated(self):
        settings = {"enabledPlugins": {"a@b": True}}
        bp.reconcile(settings, "bazos", "dev1")
        self.assertEqual(settings, {"enabledPlugins": {"a@b": True}})

    def test_the_settings_merges_keep_the_switch(self):
        # Every other install writer of settings.json preserves the key, and a
        # second reconcile is idempotent.
        out, _ = bp.reconcile({}, "bazos", "dev1")
        merged = cli_config.apply_managed_settings_defaults(
            cli_config.merge_hooks_into_settings(cli_config.load_hooks_json(), out))
        merged = cav.reconcile_managed_plugins(airuleset.reconcile_caveman_settings(merged))
        self.assertIs(merged["enabledPlugins"][KEY], False)
        again, _ = bp.reconcile(merged, "bazos", "dev1")
        self.assertEqual(again, merged)


class _SettingsFile(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "settings.json"
        p = mock.patch.object(airuleset, "SETTINGS_JSON", self.path)
        p.start()
        self.addCleanup(p.stop)

    def who(self, user, host):
        p = mock.patch.object(bp, "_identity", return_value=(user, host))
        p.start()
        self.addCleanup(p.stop)

    def write(self, data):
        self.path.write_text(json.dumps(data))

    def read(self):
        return json.loads(self.path.read_text())


class TestInstallApplies(_SettingsFile):

    def test_install_writes_the_switch_for_the_declaring_account(self):
        self.who("bazos", "dev1")
        self.write({"enabledPlugins": {}})
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cav._reconcile_settings_file(), "wrote")
            self.assertEqual(cav._reconcile_settings_file(), "unchanged")
        self.assertIs(self.read()["enabledPlugins"][KEY], False)

    def test_install_keeps_and_reports_an_undeclared_switch(self):
        self.who("newlevel", "dev2")
        self.write({"enabledPlugins": {KEY: False}})
        out = io.StringIO()
        with redirect_stdout(out):
            cav._reconcile_settings_file()
        self.assertIs(self.read()["enabledPlugins"][KEY], False)
        self.assertIn("%s is false but newlevel@dev2 declares no builtin_plugins_off"
                      % KEY, out.getvalue())

    def test_an_other_account_gets_no_switch(self):
        self.who("varos", "dev2")
        self.write({"enabledPlugins": {}})
        with redirect_stdout(io.StringIO()):
            cav._reconcile_settings_file()
        self.assertNotIn(KEY, self.read()["enabledPlugins"])


class TestStatusRow(_SettingsFile):

    def test_clean_box_prints_nothing(self):
        self.who("varos", "dev2")
        self.write({"enabledPlugins": {}})
        self.assertIsNone(bp.status_row(self.path))

    def test_undeclared_switch_is_drift(self):
        self.who("newlevel", "dev2")
        self.write({"enabledPlugins": {KEY: False}})
        row = bp.status_row(self.path)
        self.assertIn("builtin-plugins: drift", row)
        self.assertIn(KEY, row)

    def test_declared_but_not_applied_is_drift(self):
        self.who("bazos", "dev1")
        self.write({"enabledPlugins": {}})
        self.assertIn("not applied", bp.status_row(self.path))
        self.write({"enabledPlugins": {KEY: False}})
        self.assertIsNone(bp.status_row(self.path))

    def test_unreadable_settings_never_raise(self):
        self.who("bazos", "dev1")
        self.path.write_text("{not json")
        self.assertIsNone(bp.status_row(self.path))
        self.assertIsNone(bp.status_row(self.path.with_name("absent.json")))

    def test_status_prints_the_row(self):
        src = Path(airuleset.__file__).read_text(encoding="utf-8")
        self.assertIn("from cli_builtin_plugins import status_row", src)


class TestVerifyRepoLess(unittest.TestCase):

    def _results(self, spec):
        with mock.patch.dict(bootstrap.SERVICE_ACCOUNTS, {"bazos": spec}):
            rows = verify.evaluate("bazos", bootstrap.account_spec("bazos"), {}, gh_rc=None,
                                   claudy_read=lambda name: "")
        return {name: (ok, why) for name, ok, why in rows}

    def test_a_repo_less_account_is_na_on_gh_token_and_secret_sync(self):
        res = self._results(dict(bootstrap.SERVICE_ACCOUNTS["bazos"]))
        for check in ("gh-token", "secret-sync"):
            ok, why = res[check]
            self.assertTrue(ok, check)
            self.assertIn("n/a (no repo declared", why)

    def test_a_repo_without_github_app_still_fails(self):
        spec = dict(bootstrap.SERVICE_ACCOUNTS["bazos"], repo="zbynekdrlik/bazos")
        res = self._results(spec)
        self.assertFalse(res["gh-token"][0])
        self.assertFalse(res["secret-sync"][0])


if __name__ == "__main__":
    unittest.main()
