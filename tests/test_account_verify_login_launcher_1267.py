"""#1267 — two go-live gaps `accounts verify` passed green on dev1.

1. ``login``: songplayer and wall-bridge had no Claude login. claudy hands a
   login only to a box listed in BOTH ``hosts.json`` and ``auto_switch.json``,
   and nothing checked it. The check needs the credentials file and the
   ``oauthAccount`` email on the account, and the ``<acct>@<host>`` box in both
   claudy files (read on the controller as claudy).
2. ``tmux``: the panes were started by the root bootstrap BEFORE ``install``
   wrote the ``claude()`` launcher into ``~/.bashrc``, so ``claude`` ran the bare
   binary (auto mode, no ``-c``, no peer channel).

The fake world is the #1201 harness: the REAL rendered probe runs under a
fake home.
"""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_account_verify as verify      # noqa: E402
from test_accounts_verify_1201 import LAUNCHER_LINE, _Base   # noqa: E402

TOKEN_SENTINEL = "sk-ant-" + "oat01-" + "SENTINEL1267"


def _claude_child(*flags):
    """A login pane whose running child is argv[0] ``claude`` with ``flags``."""
    inner = " ".join(["exec -a claude bash -c 'sleep 60; :' x"] + list(flags))
    return ["bash", "-l", "-c", "(%s); :" % inner]


class TestLogin(_Base):

    def fails(self, check, needle):
        self.assertEqual(self.verify(), 1, self.out)
        self.assertIn(" FAIL — ", self.line(check))
        self.assertIn(needle, self.line(check))

    def test_login_is_a_go_live_check(self):
        self.assertIn("login", verify.CHECKS)

    def test_a_registered_logged_in_account_passes(self):
        self.assertEqual(self.verify(), 0, self.out + self.err)
        self.assertIn(" OK — ", self.line("login"))
        self.assertIn("acct@example.com", self.line("login"))

    def test_no_credentials_file_fails(self):
        (self.w.home / ".claude" / ".credentials.json").unlink()
        self.fails("login", ".credentials.json")

    def test_an_empty_credentials_file_fails(self):
        (self.w.home / ".claude" / ".credentials.json").write_text("")
        self.fails("login", ".credentials.json")

    def test_no_oauth_email_fails(self):
        (self.w.home / ".claude.json").write_text('{"oauthAccount": {}}\n')
        self.fails("login", "oauthAccount.emailAddress")

    def test_a_broken_claude_json_fails(self):
        (self.w.home / ".claude.json").write_text("[1, 2")
        self.fails("login", "oauthAccount.emailAddress")

    def test_token_contents_are_never_printed(self):
        (self.w.home / ".claude" / ".credentials.json").write_text(
            '{"claudeAiOauth": {"accessToken": "%s"}}\n' % TOKEN_SENTINEL)
        self.verify()
        self.assertNotIn("SENTINEL1267", self.out + self.err)

    def test_a_box_missing_from_hosts_json_fails_with_the_fix(self):
        self.claudy["hosts.json"] = [{"name": "varos@dev2", "host": "100.82.64.27",
                                      "user": "varos"}]
        self.fails("login", "hosts.json")
        self.assertIn("fohmixer@dev1", self.line("login"))
        self.assertIn("auto_switch.json", self.line("login"))   # the fix names both

    def test_a_box_missing_from_auto_switch_fails(self):
        self.claudy["auto_switch.json"] = {"boxes": [{"box": "dev1", "priority": 50}]}
        self.fails("login", "auto_switch.json")

    def test_a_hosts_entry_for_another_user_or_address_fails(self):
        self.claudy["hosts.json"][0]["user"] = "newlevel"
        self.fails("login", "hosts.json")
        self.claudy["hosts.json"][0].update(user="fohmixer", host="100.82.64.27")
        self.fails("login", "100.82.64.27")

    def test_unreadable_claudy_files_fail_never_pass(self):
        self.claudy = {}
        self.fails("login", "cannot read claudy")

    def test_a_claudy_file_of_the_wrong_shape_fails(self):
        self.claudy["auto_switch.json"] = ["fohmixer@dev1"]
        self.fails("login", "auto_switch.json")

    def test_an_ssh_failure_fails_login(self):
        self.w.probe_rc = 255
        self.assertEqual(self.verify(), 1)
        self.assertIn("ssh probe as fohmixer failed", self.line("login"))


class TestClaudyReader(unittest.TestCase):

    def test_reads_as_claudy_without_a_password_prompt(self):
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        self.assertEqual(verify.read_claudy_file("hosts.json", run=run), "[]")
        self.assertEqual(seen, [["sudo", "-n", "-u", "claudy", "cat",
                                 "/home/claudy/.config/claudy/hosts.json"]])

    def test_a_refused_read_raises(self):
        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "a password is required")
        with self.assertRaises(OSError) as e:
            verify.read_claudy_file("hosts.json", run=run)
        self.assertIn("a password is required", str(e.exception))


class TestLauncherPane(_Base):

    def fails(self, needle):
        self.assertEqual(self.verify(), 1, self.out)
        self.assertIn(" FAIL — ", self.line("tmux"))
        self.assertIn(needle, self.line("tmux"))

    def test_a_bashrc_without_the_launcher_fails(self):
        (self.w.home / ".bashrc").write_text("# no airuleset block yet\n")
        self.fails("claude() launcher")

    def test_an_idle_pane_older_than_the_bashrc_fails(self):
        # the live songplayer pane: bootstrap 15:36, install wrote .bashrc 16:55
        self.w.pane_cmd = self.w.idle_pane_cmd()
        future = time.time() + 3600
        os.utime(self.w.home / ".bashrc", (future, future))
        self.fails("tmux respawn-pane -k -t fohmixer -c /home/fohmixer/devel/fohmixer")

    def test_an_idle_pane_younger_than_the_bashrc_passes(self):
        self.w.pane_cmd = self.w.idle_pane_cmd()
        self.assertEqual(self.verify(), 0, self.out)
        self.assertIn(" OK — ", self.line("tmux"))

    def test_a_bare_claude_in_a_pane_fails(self):
        self.w.pane_cmd = _claude_child("-c")
        self.fails("not via the launcher")

    def test_a_launcher_claude_in_a_pane_passes(self):
        self.w.pane_cmd = _claude_child("--dangerously-skip-permissions", "-c")
        future = time.time() + 3600        # a busy pane: .bashrc age is moot
        os.utime(self.w.home / ".bashrc", (future, future))
        self.assertEqual(self.verify(), 0, self.out)
        self.assertIn(" OK — ", self.line("tmux"))

    def test_the_launcher_line_matches_what_install_writes(self):
        import cli_bashrc_appliers as appliers
        self.assertIn(LAUNCHER_LINE, appliers.ULTRACODE_BASHRC_BLOCK.splitlines())


if __name__ == "__main__":
    unittest.main()
