"""Tests for erp-test-<stream> ssh config provisioning (#964).

Verifies:
  1. erp_test_deploy_user returns ddeploy for david-family, mdeploy for
     montalu/miva, None for non-stream users.
  2. render_erp_test_ssh_config_block produces the correct Host block with
     the right User.
  3. ensure_erp_test_ssh_config is idempotent: a stale block with the wrong
     User is REPLACED (never appended twice), a correct block is preserved,
     and a hand-corrected entry is not reverted.
"""
import tempfile
import textwrap
import unittest
from pathlib import Path

from cli_aliases import erp_test_deploy_user


class TestErpTestDeployUser(unittest.TestCase):
    """erp_test_deploy_user derives the erp-test deploy user from stream family."""

    def test_david_streams_use_ddeploy(self):
        for user in ("david1", "david2", "david3", "david4"):
            self.assertEqual(erp_test_deploy_user(user), "ddeploy",
                             f"{user} should use ddeploy")

    def test_montalu_streams_use_mdeploy(self):
        for user in ("montalu1", "montalu2", "montalu3", "montalu4",
                      "montalu5", "montalu6", "montalu7", "montalu8"):
            self.assertEqual(erp_test_deploy_user(user), "mdeploy",
                             f"{user} should use mdeploy")

    def test_miva_streams_use_mdeploy(self):
        self.assertEqual(erp_test_deploy_user("miva1"), "mdeploy")

    def test_non_stream_returns_none(self):
        for user in ("gatekeeper", "newlevel", "marek", "dominika", "", None):
            self.assertIsNone(erp_test_deploy_user(user),
                              f"{user!r} is not a stream, should return None")

    def test_simap_returns_none(self):
        # simap is paused (#851), no erp-test box
        self.assertIsNone(erp_test_deploy_user("simap1"))


class TestRenderErpTestSshConfigBlock(unittest.TestCase):
    """render_erp_test_ssh_config_block renders a correct Host block."""

    def test_david4_block_has_ddeploy(self):
        import airuleset
        block = airuleset.render_erp_test_ssh_config_block("david4")
        self.assertIn("Host erp-test-david4", block)
        self.assertIn("User ddeploy", block)
        self.assertNotIn("mdeploy", block)

    def test_montalu1_block_has_mdeploy(self):
        import airuleset
        block = airuleset.render_erp_test_ssh_config_block("montalu1")
        self.assertIn("Host erp-test-montalu1", block)
        self.assertIn("User mdeploy", block)
        self.assertNotIn("ddeploy", block)

    def test_non_stream_returns_none(self):
        import airuleset
        self.assertIsNone(airuleset.render_erp_test_ssh_config_block("gatekeeper"))


class TestEnsureErpTestSshConfig(unittest.TestCase):
    """ensure_erp_test_ssh_config idempotently manages the Host block."""

    def _run_ensure(self, ssh_config_text, user):
        """Helper: write ssh_config_text to a temp file, run ensure, return new text."""
        import airuleset
        home = tempfile.mkdtemp()
        ssh_dir = Path(home) / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        config_path = ssh_dir / "config"
        if ssh_config_text is not None:
            config_path.write_text(ssh_config_text)
        changed = airuleset.ensure_erp_test_ssh_config(
            user=user, ssh_config_path=config_path)
        new_text = config_path.read_text() if config_path.exists() else ""
        return new_text, changed

    def test_fresh_config_for_david4(self):
        """A fresh (no file) config gets the block with User ddeploy."""
        text, changed = self._run_ensure(None, "david4")
        self.assertTrue(changed)
        self.assertIn("User ddeploy", text)
        self.assertIn("Host erp-test-david4", text)
        self.assertEqual(text.count("Host erp-test-david4"), 1)

    def test_stale_mdeploy_replaced_for_david4(self):
        """An existing block with User mdeploy is REPLACED with User ddeploy."""
        stale = textwrap.dedent("""\
            # >>> airuleset: erp-test-ssh >>>
            Host erp-test-david4 erp-test-david4.newlevel.media
                HostName erp-test-david4.newlevel.media
                User mdeploy
                StrictHostKeyChecking no
            # <<< airuleset: erp-test-ssh <<<
        """)
        text, changed = self._run_ensure(stale, "david4")
        self.assertTrue(changed)
        self.assertIn("User ddeploy", text)
        self.assertNotIn("User mdeploy", text)
        self.assertEqual(text.count("Host erp-test-david4"), 1)

    def test_correct_block_preserved(self):
        """A correct block (User ddeploy for david4) is not changed."""
        import airuleset
        correct_block = airuleset.render_erp_test_ssh_config_block("david4")
        text, changed = self._run_ensure(correct_block + "\n", "david4")
        self.assertFalse(changed)
        self.assertIn("User ddeploy", text)

    def test_rerender_yields_single_block(self):
        """Re-running ensure over a stale mdeploy block yields exactly ONE block."""
        stale = textwrap.dedent("""\
            Host other-host
                User someone

            # >>> airuleset: erp-test-ssh >>>
            Host erp-test-david4 erp-test-david4.newlevel.media
                HostName erp-test-david4.newlevel.media
                User mdeploy
                StrictHostKeyChecking no
            # <<< airuleset: erp-test-ssh <<<

            Host another-host
                User another
        """)
        text, changed = self._run_ensure(stale, "david4")
        self.assertTrue(changed)
        self.assertEqual(text.count("# >>> airuleset: erp-test-ssh >>>"), 1)
        self.assertEqual(text.count("# <<< airuleset: erp-test-ssh <<<"), 1)
        self.assertIn("User ddeploy", text)
        # Surrounding content preserved
        self.assertIn("Host other-host", text)
        self.assertIn("Host another-host", text)

    def test_montalu1_uses_mdeploy(self):
        """montalu1 gets User mdeploy."""
        text, changed = self._run_ensure(None, "montalu1")
        self.assertTrue(changed)
        self.assertIn("User mdeploy", text)
        self.assertIn("Host erp-test-montalu1", text)

    def test_non_stream_is_noop(self):
        """Non-stream users get no block."""
        import airuleset
        home = tempfile.mkdtemp()
        ssh_dir = Path(home) / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        config_path = ssh_dir / "config"
        changed = airuleset.ensure_erp_test_ssh_config(
            user="gatekeeper", ssh_config_path=config_path)
        self.assertFalse(changed)
        self.assertFalse(config_path.exists())

    def test_unmarked_manual_stanza_replaced(self):
        """An unmarked manual stanza with User mdeploy is REPLACED by the
        managed block with User ddeploy -- ssh first-match means an unmarked
        stanza placed BEFORE the managed block would win, so the fix must
        take over the unmarked stanza, not just append after it."""
        manual = textwrap.dedent("""\
            Host erp-test-david4 erp-test-david4.newlevel.media
                HostName erp-test-david4.newlevel.media
                User mdeploy
                StrictHostKeyChecking no
        """)
        text, changed = self._run_ensure(manual, "david4")
        self.assertTrue(changed)
        # The managed block REPLACED the unmarked stanza.
        self.assertIn("# >>> airuleset: erp-test-ssh >>>", text)
        self.assertIn("User ddeploy", text)
        self.assertNotIn("User mdeploy", text)
        # Exactly ONE Host erp-test-david4 stanza.
        self.assertEqual(text.count("Host erp-test-david4"), 1)

    def test_unmarked_manual_stanza_with_surrounding_content(self):
        """An unmarked stanza is replaced while preserving foreign stanzas."""
        config = textwrap.dedent("""\
            Host other-host
                User someone

            Host erp-test-david4 erp-test-david4.newlevel.media
                HostName erp-test-david4.newlevel.media
                User mdeploy
                StrictHostKeyChecking no

            Host another-host
                User another
        """)
        text, changed = self._run_ensure(config, "david4")
        self.assertTrue(changed)
        self.assertIn("User ddeploy", text)
        self.assertNotIn("User mdeploy", text)
        self.assertEqual(text.count("Host erp-test-david4"), 1)
        # Foreign stanzas preserved.
        self.assertIn("Host other-host", text)
        self.assertIn("Host another-host", text)


if __name__ == "__main__":
    unittest.main()
