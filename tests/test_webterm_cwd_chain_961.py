"""Tests for #961: webterm forced command cwd chain.

These tests verify the fix for webterm tabs opening tmux sessions in $HOME
after reboot instead of the project directory. They test:
1. _remote_command output contains -c for both new-session and attach-session
2. The chain fallback logic works (first existing dir wins, else $HOME)
3. The ar tab uses devel/airuleset chain
4. Drift-lock: _ATTACH_BODY chain matches STREAM_DEV_CWD_CHAIN
5. Behavioral: the shell snippet correctly resolves chain dirs via fake tmux
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestForcedCommandCarriesCwd(unittest.TestCase):
    """Layer 1: the forced command must carry -c with the chain fallback."""

    def test_new_session_carries_dash_c(self):
        """The fresh-create path (new-session -A -s ...) must include -c."""
        import cli_webterm as w
        cmd = w._remote_command("david1")
        # The new-session must have -c "$C"
        self.assertIn('new-session -A -s "$P" -c "$C"', cmd,
                      "new-session must carry -c for cwd chain (#961)")

    def test_attach_session_carries_dash_c(self):
        """The attach path must include -c to set the default cwd."""
        import cli_webterm as w
        cmd = w._remote_command("david1")
        self.assertIn('attach-session -t "$T" -c "$C"', cmd,
                      "attach-session must carry -c for cwd chain (#961)")

    def test_chain_computation_present(self):
        """The command must compute C from the chain dirs."""
        import cli_webterm as w
        cmd = w._remote_command("david1")
        # Must have C= assignment with HOME-relative dir check
        self.assertIn('C=', cmd)
        self.assertIn('$HOME/', cmd)

    def test_default_chain_matches_stream_dev_cwd_chain(self):
        """Default chain must match STREAM_DEV_CWD_CHAIN (drift-lock)."""
        import cli_webterm as w
        from cli_bashrc_appliers import STREAM_DEV_CWD_CHAIN
        # When no chain is given, the command should reference the dirs
        # from STREAM_DEV_CWD_CHAIN
        cmd = w._remote_command("david1")
        for rel in STREAM_DEV_CWD_CHAIN:
            self.assertIn(rel, cmd,
                          "default chain must include %s from STREAM_DEV_CWD_CHAIN" % rel)

    def test_custom_chain_used(self):
        """A custom start_dir_chain should be used instead of default."""
        import cli_webterm as w
        cmd = w._remote_command("zbynek", start_dir_chain=["devel/airuleset"])
        self.assertIn("devel/airuleset", cmd)

    def test_ar_entry_has_start_dir_chain(self):
        """The ar tab entry in zbynek_inventory must have a start_dir_chain."""
        import cli_webterm_profiles as profiles
        inv = profiles.zbynek_inventory()
        ar = next(e for e in inv if e["id"] == "ar")
        self.assertIn("start_dir_chain", ar,
                      "ar entry must carry start_dir_chain for devel/airuleset")
        self.assertEqual(ar["start_dir_chain"], ["devel/airuleset"])


class TestBuildConnectArgvPassesChain(unittest.TestCase):
    """build_connect_argv must pass the entry's start_dir_chain."""

    def test_entry_chain_reaches_command(self):
        """When entry has start_dir_chain, it must appear in the command."""
        import cli_webterm as w
        entry = {
            "id": "test",
            "local": True,
            "host": None,
            "user": None,
            "preferred": "testuser",
            "start_dir_chain": ("devel/myproject",),
        }
        argv = w.build_connect_argv(entry)
        cmd_str = " ".join(argv)
        self.assertIn("devel/myproject", cmd_str)


class TestForcedCommandInAuthorizedKeys(unittest.TestCase):
    """The forced command baked into authorized_keys must carry -c."""

    def test_controller_lane_key_line_carries_dash_c(self):
        """The authorized_keys forced command for a webterm-only user
        must include -c in new-session."""
        import re
        from cli_webterm_only import _controller_lane_key_line
        line = _controller_lane_key_line("david1", "ssh-ed25519 AAAA testkey")
        self.assertIn("new-session", line)
        # After the fix, new-session must carry -c
        self.assertTrue(
            re.search(r'new-session.*-c', line),
            "new-session in authorized_keys must carry -c (#961)")


class TestChainFallbackBehavior(unittest.TestCase):
    """Behavioral tests: run the chain computation snippet with a fake HOME
    and verify the C variable resolves correctly.

    We extract ONLY the chain-computation prefix (P=...; C=...; for ...; done;)
    from _remote_command's output and run it in a real shell, printing C
    at the end. This avoids the _ATTACH_BODY's exec tmux call."""

    def _eval_chain(self, home, chain):
        """Run the chain computation snippet in a real shell and return C."""
        import cli_webterm as w
        cmd = w._remote_command("test", start_dir_chain=chain)
        # The chain snippet is everything up to the first T=
        # (T="" is the start of _ATTACH_BODY). Extract it.
        idx = cmd.index('T=""')
        chain_snippet = cmd[:idx]
        script = chain_snippet + 'echo "CHAIN_RESULT=$C"'
        env = dict(os.environ, HOME=home)
        r = subprocess.run(
            ["sh", "-c", script], capture_output=True, text=True,
            env=env, timeout=10)
        for line in r.stdout.splitlines():
            if line.startswith("CHAIN_RESULT="):
                return line.split("=", 1)[1]
        return None

    def test_first_existing_dir_wins(self):
        """When only the second chain dir exists, C points to it."""
        with tempfile.TemporaryDirectory() as home:
            # Create only devel/odoo (second in default chain)
            (Path(home) / "devel" / "odoo").mkdir(parents=True)
            c = self._eval_chain(home, ("devel/odoo/odoo-erp", "devel/odoo"))
            self.assertEqual(c, os.path.join(home, "devel/odoo"))

    def test_fallback_to_home(self):
        """When no chain dir exists, C falls back to $HOME."""
        with tempfile.TemporaryDirectory() as home:
            c = self._eval_chain(home, ("nonexistent/a", "nonexistent/b"))
            self.assertEqual(c, home)

    def test_ar_chain_resolves_airuleset(self):
        """The ar tab's chain resolves to devel/airuleset when it exists."""
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "devel" / "airuleset").mkdir(parents=True)
            c = self._eval_chain(home, ["devel/airuleset"])
            self.assertEqual(c, os.path.join(home, "devel/airuleset"))

    def test_primary_chain_entry_takes_precedence(self):
        """When both chain dirs exist, the first one wins."""
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / "devel" / "odoo" / "odoo-erp").mkdir(parents=True)
            (Path(home) / "devel" / "odoo").mkdir(parents=True, exist_ok=True)
            c = self._eval_chain(home, ("devel/odoo/odoo-erp", "devel/odoo"))
            self.assertEqual(c, os.path.join(home, "devel/odoo/odoo-erp"))


if __name__ == "__main__":
    unittest.main()
