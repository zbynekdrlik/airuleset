"""#1394: a declared per-account switch for Claude Code BUILT-IN plugins.

`SERVICE_ACCOUNTS[acct]["builtin_plugins_off"]` makes `airuleset.py install`, run
AS that account, write `"<plugin>@builtin": false` into its ~/.claude/settings.json
`enabledPlugins` on every install. An account that declares nothing but carries a
`false` (newlevel@dev2's hand-set switch, from before the bazos account existed)
is reported as drift and KEPT, unless that `user@host` is named in the opt-in
`REMOVE_UNDECLARED_ON`: the supervisor removes newlevel's switch only after the
bazos cutover. (The repo-less verify half lives in test_repo_less_1394.)"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import airuleset
import cli_account_policy as policy
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
        out, drift = bp.reconcile(settings, "newlevel", "dev1")
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
        self.assertEqual(bp.REMOVE_UNDECLARED_ON, frozenset({"newlevel@dev2"}))   # #1394 cutover done 23.11.

    def test_an_undeclared_account_without_the_key_is_untouched(self):
        for settings in ({}, {"enabledPlugins": {KEY: True}}, {"enabledPlugins": {"a@b": False}}):
            out, drift = bp.reconcile(settings, "newlevel", "dev1")
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
        # the account's own home: the switch is applied only there (round 2)
        p = mock.patch.object(bp, "_passwd_home", return_value=os.path.expanduser("~"),
                              create=True)
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
        self.who("newlevel", "dev1")
        self.write({"enabledPlugins": {KEY: False}})
        out = io.StringIO()
        with redirect_stdout(out):
            cav._reconcile_settings_file()
        self.assertIs(self.read()["enabledPlugins"][KEY], False)
        self.assertIn("%s is false but newlevel@dev1 declares no builtin_plugins_off"
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


class TestRound2(_SettingsFile):
    """#1394 review round 2 (the 🔵 findings)."""

    def test_the_declaration_counts_only_on_its_declared_host(self):
        out, _ = bp.reconcile({}, "bazos", "dev2")      # bazos is declared on dev1
        self.assertEqual(out, {})
        out, _ = bp.reconcile({}, "bazos", "dev1")
        self.assertEqual(out, {"enabledPlugins": {KEY: False}})

    def test_an_unhashable_name_is_an_error_not_a_crash(self):
        try:
            errs = policy.validate_builtin_plugins_off({"builtin_plugins_off": [["x"]]})
        except TypeError:
            errs = None
        self.assertTrue(errs)

    def test_install_names_the_switch_it_applied(self):
        self.who("bazos", "dev1")
        self.write({"enabledPlugins": {}})
        out = io.StringIO()
        with redirect_stdout(out):
            cav._reconcile_settings_file()
        self.assertIn("%s: false (declared off for bazos@dev1" % KEY, out.getvalue())

    def test_a_home_that_is_not_the_passwd_home_is_refused(self):
        # A $HOME that is not the account's own (sudo -E, a test home) never
        # gets the account's switch, and status says why instead of guessing.
        self.who("bazos", "dev1")
        p = mock.patch.object(bp, "_passwd_home", return_value="/nonexistent/other", create=True)
        p.start()
        self.addCleanup(p.stop)
        out = io.StringIO()
        with redirect_stdout(out):
            got = bp.apply_for_this_account({"enabledPlugins": {}})
        self.assertEqual(got, {"enabledPlugins": {}})
        self.assertIn("not the passwd home", out.getvalue())
        self.assertIn("not the passwd home", bp.status_row() or "")

    def test_status_reads_the_passwd_home_settings(self):
        self.who("bazos", "dev1")
        home = self.path.parent / "acct"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))
        with mock.patch.object(bp, "_passwd_home", return_value=str(home), create=True), \
                mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.assertIn("not applied", bp.status_row() or "")

    def test_a_failing_switch_never_aborts_the_plugin_reconcile(self):
        self.who("bazos", "dev1")
        self.write({"enabledPlugins": {}})
        err = io.StringIO()
        with mock.patch.object(bp, "reconcile", side_effect=RuntimeError("boom")), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                status = cav._reconcile_settings_file()
            except RuntimeError:
                status = "raised"
        self.assertEqual(status, "wrote")
        self.assertIs(self.read()["enabledPlugins"]["superpowers@claude-plugins-official"], True)
        self.assertIn("builtin-plugins", err.getvalue())

    def test_the_docstring_says_removing_a_declaration_needs_the_opt_in(self):
        self.assertIn("dropping builtin_plugins_off", bp.__doc__)
        self.assertIn("REMOVE_UNDECLARED_ON", bp.__doc__)


if __name__ == "__main__":
    unittest.main()
