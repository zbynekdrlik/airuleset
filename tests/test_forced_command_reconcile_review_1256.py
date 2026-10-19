"""#1256 second review round — the forced-command reconcile's file handling.

1. A symlinked `authorized_keys` is updated THROUGH the link (the link stays,
   the managed target gets the new line); replacing the link with a file would
   detach later revocations made on the target.
2. A non-UTF-8 byte (a latin-1 key comment) neither disables the reconcile nor
   is altered.
3. Each change gets its own backup, and no temp file is left behind.
4. A hand-baked tmux forced command that is not ours (`tmux new -A -s x`) is
   COUNTED, so install can report it instead of silently keeping it.
5. The install step never aborts install, even if the module fails to import.
"""
import inspect
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
sys.path.insert(0, str(TESTS))

import cli_forced_command_reconcile as fc  # noqa: E402
import test_forced_command_reconcile_1256 as base  # noqa: E402


class _Home(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="fcrev-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        (self.home / ".ssh").mkdir(mode=0o700)
        self.ak = self.home / ".ssh" / "authorized_keys"


class SymlinkedFile(_Home):
    def test_the_link_stays_and_its_target_is_updated(self):
        target = self.home / "managed_ak"
        target.write_text(base._old_line("zbynek", "devel/odoo") + "\n")
        os.chmod(target, 0o600)
        self.ak.symlink_to(target)
        res = fc.reconcile(self.home, user="montalu9")
        self.assertEqual(res["action"], "updated")
        self.assertTrue(self.ak.is_symlink())
        self.assertIn("H=", target.read_text())


class NonUtf8Bytes(_Home):
    def test_a_latin1_comment_survives(self):
        line = base._old_line("zbynek", "devel/odoo").encode() + b" caf\xe9\n"
        self.ak.write_bytes(line)
        os.chmod(self.ak, 0o600)
        res = fc.reconcile(self.home, user="montalu9")
        self.assertEqual(res["action"], "updated")
        self.assertTrue(self.ak.read_bytes().endswith(b" caf\xe9\n"))


class BackupsAndTemps(_Home):
    def test_two_changes_keep_two_backups_and_no_temp(self):
        for chain in ("devel/odoo", "devel/other"):
            self.ak.write_text(base._old_line("zbynek", chain) + "\n")
            os.chmod(self.ak, 0o600)
            self.assertEqual(fc.reconcile(self.home, user="montalu9")["action"], "updated")
        names = sorted(p.name for p in (self.home / ".ssh").iterdir())
        self.assertEqual(len([n for n in names if n.startswith("authorized_keys.airuleset-prev-")]), 2)
        self.assertFalse([n for n in names if "tmp" in n], names)


class ForeignTmuxCommands(unittest.TestCase):
    def test_hand_baked_tmux_command_is_counted(self):
        text = ('restrict,pty,command="%s" ssh-ed25519 AAAAfakehandbaked hand\n'
                % fc.encode("tmux new -A -s " + shlex.quote("montalu1")))
        self.assertEqual(fc.foreign_tmux_commands(text), 1)
        ours = base._old_line("zbynek", "devel/odoo") + "\n"
        self.assertEqual(fc.foreign_tmux_commands(ours), 0)


class InstallNeverAborts(unittest.TestCase):
    def test_the_step_is_guarded_in_cmd_install(self):
        import airuleset
        src = inspect.getsource(airuleset.cmd_install)
        i = src.index("cli_forced_command_reconcile")
        self.assertIn("try:", src[max(0, i - 200):i])


if __name__ == "__main__":
    unittest.main()
