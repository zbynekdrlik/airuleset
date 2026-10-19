"""#1256 review round — a changed webterm attach body must reach every BAKED
forced command, not only the webterm-only accounts.

`_remote_command` is baked into each account's `~/.ssh/authorized_keys`
(`restrict,pty,command="..."`), and since #1202 nothing re-bakes those lines:
the #1256 revive fragment would have reached david1-4 (desired-set rewrite)
but not montalu1-8, miva1, marek, dominika, gk, newlevel, claudy, fohmixer,
varos, spinbike or admin@forestshop. `install` (run as every target account by
`push`) now re-renders every line whose command is OUR attach body, keeping
its session name (`P=`) and start-dir chain, and touches nothing else.
"""
import inspect
import os
import shlex
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_forced_command_reconcile as fc  # noqa: E402
import cli_webterm as w  # noqa: E402

OLD_BODY = (
    'C=""; for __r in %s; do if [ -e "$HOME/$__r/.git" ]; then C="$HOME/$__r"; '
    'break; fi; done; if [ -z "$C" ]; then C="$HOME"; fi; T=""; if tmux has-session '
    '-t "=$P" 2>/dev/null; then T="$P"; else T=$(tmux list-sessions -F '
    '"#{session_group}::#{session_name}" 2>/dev/null | awk -F \'::\' -v g="$P" '
    '\'$1==g{print $2; exit}\'); if [ -z "$T" ]; then N=$(tmux list-sessions -F '
    '"#{session_name}" 2>/dev/null); if [ "$(printf %%s "$N" | grep -c .)" = "1" ]; '
    'then T="$N"; fi; fi; fi; if [ -n "$T" ]; then tmux set-option -t "$T" mouse on; '
    'trap \'tmux set-option -u -t "$T" mouse 2>/dev/null || true\' EXIT HUP INT TERM; '
    'tmux attach-session -t "$T" -c "$C" -f ignore-size; exit; fi; '
    'exec tmux new-session -A -s "$P" -c "$C"')
KEY = "ssh-ed25519 AAAAfakelanekey lane-zbynek"   # digit-free fake (entropy hook)


def _old_line(preferred, chain):
    cmd = "P=%s; " % shlex.quote(preferred) + OLD_BODY % chain
    return 'restrict,pty,command="%s" %s' % (fc.encode(cmd), KEY)


class Plan(unittest.TestCase):
    def test_a_stale_line_is_rerendered_keeping_session_and_options(self):
        old = _old_line("zbynek", "devel/odoo/odoo-erp devel/odoo")
        new_text, n = fc.plan(old + "\n", user="montalu9")
        self.assertEqual(n, 1)
        want = w._remote_command("zbynek",
                                 start_dir_chain=["devel/odoo/odoo-erp", "devel/odoo"],
                                 user="montalu9")
        self.assertEqual(new_text,
                         'restrict,pty,command="%s" %s\n' % (fc.encode(want), KEY))

    def test_the_start_dir_chain_is_preserved(self):
        new_text, n = fc.plan(_old_line("marek", "devel/claudy") + "\n", user="claudy")
        self.assertEqual(n, 1)
        self.assertIn("for __r in devel/claudy;", fc.decode(fc.split_command(new_text)[1]))
        self.assertIn("P=marek;", new_text)

    def test_other_lines_are_untouched(self):
        lines = ["# a comment",
                 "ssh-ed25519 AAAAfakeownerkey owner@laptop",
                 'command="/usr/bin/rrsync -ro /srv" ssh-ed25519 AAAAfakebackupkey backup',
                 "",
                 'restrict,command="echo hi" ssh-ed25519 AAAAfakeotherkey other']
        text = "\n".join(lines) + "\n"
        self.assertEqual(fc.plan(text, user="montalu9"), (text, 0))

    def test_idempotent(self):
        once, n1 = fc.plan(_old_line("zbynek", "devel/odoo") + "\n", user="montalu9")
        twice, n2 = fc.plan(once, user="montalu9")
        self.assertEqual((n1, n2, twice), (1, 0, once))

    def test_encoding_round_trips_like_sshd(self):
        cmd = w._remote_command("zbynek", user="montalu9")
        self.assertNotIn("\\", cmd)
        self.assertEqual(fc.decode(fc.encode(cmd)), cmd)


class Reconcile(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="fcrec-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, True))
        (self.home / ".ssh").mkdir(mode=0o700)
        self.ak = self.home / ".ssh" / "authorized_keys"

    def test_updates_atomically_with_a_0600_backup(self):
        self.ak.write_text(_old_line("zbynek", "devel/odoo") + "\n")
        os.chmod(self.ak, 0o600)
        res = fc.reconcile(self.home, user="montalu9")
        self.assertEqual((res["action"], res["changed"]), ("updated", 1))
        self.assertEqual(stat.S_IMODE(self.ak.stat().st_mode), 0o600)
        backups = list((self.home / ".ssh").glob("authorized_keys.airuleset-prev-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
        self.assertEqual(fc.reconcile(self.home, user="montalu9")["action"], "unchanged")

    def test_missing_file_is_skipped(self):
        self.assertEqual(fc.reconcile(self.home, user="montalu9")["action"], "skipped")

    def test_unwritable_is_reported_not_raised(self):
        self.ak.write_text(_old_line("zbynek", "devel/odoo") + "\n")
        os.chmod(self.ak, 0o400)
        os.chmod(self.home / ".ssh", 0o500)
        self.addCleanup(os.chmod, self.home / ".ssh", 0o700)
        res = fc.reconcile(self.home, user="montalu9")
        self.assertEqual((res["action"], res["changed"]), ("unwritable", 1))


class InstallWiring(unittest.TestCase):
    def test_cmd_install_runs_the_reconcile(self):
        import airuleset
        self.assertIn("cli_forced_command_reconcile", inspect.getsource(airuleset.cmd_install))


if __name__ == "__main__":
    unittest.main()
