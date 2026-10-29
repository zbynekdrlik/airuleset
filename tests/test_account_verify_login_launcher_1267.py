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
import tempfile
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
        seen, real = [], self.w.run

        def run(argv, **kw):
            seen.append(real(argv, **kw))
            return seen[-1]
        self.w.run = run
        self.verify()
        self.assertNotIn("SENTINEL1267", self.out + self.err)
        self.assertNotIn("SENTINEL1267", seen[0].stdout + seen[0].stderr)  # never on the wire

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
        self.claudy["auto_switch.json"] = [{"box": "fohmixer@dev1"}]  # not {"boxes": [...]}
        self.fails("login", "auto_switch.json")

    def test_a_duplicate_hosts_entry_fails(self):
        self.claudy["hosts.json"].append({"name": "fohmixer@dev1", "host": "100.82.64.27",
                                          "user": "fohmixer"})
        self.fails("login", "duplicate")

    def test_a_host_outside_the_fleet_fails_instead_of_crashing(self):
        import cli_account_bootstrap as bootstrap
        from unittest import mock
        with mock.patch.object(bootstrap, "fleet_boxes", return_value={}):
            self.fails("login", "not a fleet box")

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

    def test_a_commented_out_launcher_line_fails(self):
        (self.w.home / ".bashrc").write_text("# " + LAUNCHER_LINE + "\n")
        self.fails("claude() launcher")

    def test_a_bash_profile_that_skips_the_bashrc_fails(self):
        (self.w.home / ".bash_profile").write_text("# . ~/.bashrc\nexport X=1\n")
        self.fails("~/.bash_profile")

    def test_a_bash_profile_that_sources_the_bashrc_passes(self):
        (self.w.home / ".bash_profile").write_text('[ -f ~/.bashrc ] && . ~/.bashrc\n')
        self.assertEqual(self.verify(), 0, self.out)

    def test_a_stale_pane_with_a_non_claude_child_fails(self):
        # reviews: a pane busy in vim/a dev server still runs a bare claude later
        future = time.time() + 3600
        os.utime(self.w.home / ".bashrc", (future, future))
        self.fails("tmux respawn-pane -k")

    def test_a_bashrc_rewrite_after_the_launcher_landed_keeps_a_pane_fresh(self):
        # install rewrote ~/.bashrc for another block; the claude() line is old
        self.w.pane_cmd = self.w.idle_pane_cmd()
        since = self.w.home / ".claude" / "airuleset-launcher-since"
        since.touch()
        old = time.time() - 3600
        os.utime(since, (old, old))
        future = time.time() + 3600
        os.utime(self.w.home / ".bashrc", (future, future))
        self.assertEqual(self.verify(), 0, self.out)

    def test_a_pane_older_than_the_launcher_marker_fails(self):
        self.w.pane_cmd = self.w.idle_pane_cmd()
        since = self.w.home / ".claude" / "airuleset-launcher-since"
        future = time.time() + 3600
        since.touch()
        os.utime(since, (future, future))
        self.fails("tmux respawn-pane -k")

    def test_a_launcher_claude_in_a_pane_passes(self):
        self.w.pane_cmd = _claude_child("--dangerously-skip-permissions", "-c")
        future = time.time() + 3600        # a busy pane: .bashrc age is moot
        os.utime(self.w.home / ".bashrc", (future, future))
        self.assertEqual(self.verify(), 0, self.out)
        self.assertIn(" OK — ", self.line("tmux"))

    def test_the_launcher_line_matches_what_install_writes(self):
        import cli_bashrc_appliers as appliers
        self.assertIn(LAUNCHER_LINE, appliers.ULTRACODE_BASHRC_BLOCK.splitlines())



class TestPaneEvidence(unittest.TestCase):
    """Each pane is judged on its own evidence (no fake world needed)."""

    R = "respawn: X"

    def sections(self, *panes, launcher_age="100"):
        return {"tmux": {
            "pane": ["%s %s -bash" % (pid, age) for pid, age, _ in panes],
            "paneenv": ["%s child /opt/rust/rustup:1" % pid for pid, _, _ in panes],
            "panechild": ["%s %s" % (pid, c) for pid, _, c in panes if c],
            "profile_age": ["99999"], "launcher_age": [launcher_age]}}

    def test_one_stale_pane_among_healthy_ones(self):
        out = verify._pane_problems(self.sections(
            ("11", "50", ""), ("22", "500", ""),
            ("33", "500", "claude --dangerously-skip-permissions -c")), self.R)
        self.assertEqual(len(out), 1, out)
        self.assertIn("pane 22 ", out[0])

    def test_a_node_shim_claude_is_still_a_claude(self):
        out = verify._pane_problems(self.sections(
            ("11", "50", "node /usr/lib/node_modules/@anthropic-ai/claude-code/cli.js -c")),
            self.R)
        self.assertEqual(len(out), 1, out)
        self.assertIn("not via the launcher", out[0])

    def test_claude_plain_is_named_in_the_failure(self):
        out = verify._pane_problems(self.sections(("11", "50", "claude")), self.R)
        self.assertIn("claude-plain", out[0])


class TestLauncherSinceMarker(unittest.TestCase):
    """install stamps when the claude() line first reached ~/.bashrc, so a later
    rewrite of ~/.bashrc for another block never makes a healthy pane stale."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="since-1267-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.d)])
        c = self.d / ".claude"
        self.kw = dict(bashrc_path=self.d / ".bashrc",
                       script_path=c / "airuleset-claude-launch.sh",
                       history_script_path=c / "h.py", popup_script_path=c / "p.sh",
                       impl_script_path=c / "i.sh", impl_prompt_path=c / "i.md",
                       user="fohmixer")
        self.since = c / "airuleset-launcher-since"

    def apply(self):
        import cli_bashrc_appliers as appliers
        appliers.apply_ultracode_launcher(**self.kw)

    def test_a_fresh_launcher_stamps_now(self):
        (self.d / ".bashrc").write_text("# rc\n")
        self.apply()
        self.assertTrue(self.since.exists())
        self.assertLess(time.time() - self.since.stat().st_mtime, 60)

    def test_an_existing_launcher_keeps_the_bashrc_age_before_the_rewrite(self):
        bashrc = self.d / ".bashrc"
        bashrc.write_text("# rc\n")
        self.apply()
        self.since.unlink()                         # an account installed pre-1267
        bashrc.write_text(bashrc.read_text().replace("claude-new()", "BROKEN()"))
        old = time.time() - 86400
        os.utime(bashrc, (old, old))
        self.apply()                                # rewrites the block, claude() kept
        self.assertIn("claude-new()", bashrc.read_text())
        self.assertAlmostEqual(self.since.stat().st_mtime, old, delta=2)

    def test_a_later_run_never_moves_the_stamp(self):
        (self.d / ".bashrc").write_text("# rc\n")
        self.apply()
        old = time.time() - 86400
        os.utime(self.since, (old, old))
        (self.d / ".bashrc").write_text((self.d / ".bashrc").read_text() + "# x\n")
        self.apply()
        self.assertAlmostEqual(self.since.stat().st_mtime, old, delta=2)


if __name__ == "__main__":
    unittest.main()
