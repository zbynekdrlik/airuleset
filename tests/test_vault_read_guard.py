"""hooks/block-vault-store-read.sh — the credential store is not read by hand.

#153 finding 1. #144 shipped a channel whose central claim is that a
credential's VALUE never reaches the session transcript, and then rested that
claim on the agent choosing `secret exec` over `cat`. The store is 0600 under
`~/.claude/secrets/`, owned by the very uid the agent's Bash runs as, and no
hook gated it — so one `cat ~/.claude/secrets/DB_PASS.secret` put the value in
the transcript permanently. That is #134's shape exactly: a guarantee deferring
to an unenforced action.

This hook is the artifact the guarantee now rests on. It matches the RAW
command text rather than parsed argv, because the interesting evasions all hide
the path inside a quoted string (`python3 -c 'open("…").read()'`, a `<`
redirection, `$(<file)`) where token parsing cannot see it. Deny-by-default on
any store reference, with a small allowlist of provably metadata-only heads.

Its honest limit is asserted here too (test_the_refusal_states_its_own_limit):
the agent's uid holds NOPASSWD sudo on these boxes, so this is a guardrail that
makes the unsafe path refused-by-default and leaves an audit artifact when it is
deliberately circumvented — never a boundary a determined process cannot cross.
No test in this file uses a real credential value.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-vault-store-read.sh"

STORE = "~/.claude/secrets"
ABS = "/home/newlevel/.claude/secrets"


def run(cmd, env_extra=None):
    payload = json.dumps({"tool_input": {"command": cmd},
                          "cwd": "/home/newlevel/devel/airuleset"})
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    if env_extra:
        env.update(env_extra)
    # /bin/bash by ABSOLUTE path: the interpreter is resolved through the env
    # we pass, so a test that empties PATH to break the CHECK would otherwise
    # just fail to find bash and never run the hook at all (127, not a verdict).
    return subprocess.run(["/bin/bash", str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


class Blocks(unittest.TestCase):
    def assertBlocked(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2,
                         "expected BLOCK for: %s\nstdout=%s\nstderr=%s"
                         % (cmd, r.stdout, r.stderr))

    # --- the one-command leak this ticket exists to close --------------------
    def test_the_incident_shape(self):
        self.assertBlocked("cat %s/DB_PASS.secret" % STORE)

    def test_absolute_path(self):
        self.assertBlocked("cat %s/DB_PASS.secret" % ABS)

    def test_home_variable_path(self):
        self.assertBlocked("cat $HOME/.claude/secrets/DB_PASS.secret")

    def test_relative_after_a_cd(self):
        # The store-dir prefix is gone; the `.secret` extension is what carries
        # the match here, which is why the second pattern exists.
        self.assertBlocked("cd %s && cat DB_PASS.secret" % STORE)

    # --- every reader the ticket names --------------------------------------
    def test_each_named_reader(self):
        for reader in ("cat", "less", "more", "head", "tail", "xxd", "od",
                       "strings", "base64", "cp", "bat", "nl", "tac", "rev",
                       "hexdump", "base32", "shasum", "md5sum", "awk", "sed",
                       "grep", "sort", "uniq", "cut", "diff", "tee", "gzip",
                       "scp", "rsync", "tar", "mv", "ln", "install", "dd"):
            with self.subTest(reader=reader):
                self.assertBlocked("%s %s/DB_PASS.secret" % (reader, STORE))

    def test_a_reader_this_hook_never_heard_of(self):
        # The point of deny-by-default: an unanticipated reader must fail
        # CLOSED. A blocklist of reader names would let this through silently.
        self.assertBlocked("some-future-pager %s/DB_PASS.secret" % STORE)

    # --- shapes that hide the path from argv parsing ------------------------
    def test_python_open(self):
        self.assertBlocked(
            "python3 -c 'print(open(\"%s/DB_PASS.secret\").read())'" % ABS)

    def test_python_pathlib_read_text(self):
        self.assertBlocked(
            'python3 -c "import pathlib;'
            'print(pathlib.Path(\'%s/DB_PASS.secret\').read_text())"' % ABS)

    def test_input_redirection(self):
        self.assertBlocked(
            "while read l; do echo \"$l\"; done < %s/DB_PASS.secret" % STORE)

    def test_dollar_paren_less_than(self):
        self.assertBlocked("echo \"$(<%s/DB_PASS.secret)\"" % STORE)

    def test_command_substitution_inside_an_allowlisted_head(self):
        # `ls` is allowlisted, but the substitution inside it is a read. If the
        # hook only looked at the segment head this would leak.
        self.assertBlocked("ls \"$(cat %s/DB_PASS.secret)\"" % STORE)

    def test_backtick_substitution_inside_an_allowlisted_head(self):
        self.assertBlocked("stat `cat %s/DB_PASS.secret`" % STORE)

    def test_glob_over_the_store(self):
        self.assertBlocked("cat %s/*" % STORE)

    def test_glob_with_the_extension(self):
        self.assertBlocked("cat %s/*.secret" % STORE)

    def test_the_whole_dir_as_an_archive(self):
        self.assertBlocked("tar cf - %s | base64" % STORE)

    # --- prefixes must not launder the read ---------------------------------
    def test_sudo_does_not_launder_it(self):
        self.assertBlocked("sudo cat %s/DB_PASS.secret" % STORE)

    def test_env_assignment_prefix_does_not_launder_it(self):
        self.assertBlocked("FOO=1 cat %s/DB_PASS.secret" % STORE)

    def test_a_later_segment_is_still_checked(self):
        self.assertBlocked("ls -l /tmp && cat %s/DB_PASS.secret" % STORE)

    def test_a_piped_segment_is_still_checked(self):
        self.assertBlocked("true | cat %s/DB_PASS.secret" % STORE)

    # --- writing a value by hand is the same leak, from the other side ------
    def test_writing_a_value_into_the_store_is_blocked(self):
        # Typing a credential into a shell command puts it in the transcript —
        # exactly what `secret request` exists to avoid.
        self.assertBlocked("echo NOT_A_REAL_VALUE > %s/DB_PASS.secret" % STORE)

    def test_appending_a_value_into_the_store_is_blocked(self):
        self.assertBlocked("printf x >> %s/DB_PASS.secret" % STORE)


class Allows(unittest.TestCase):
    def assertAllowed(self, cmd, **kw):
        r = run(cmd, **kw)
        self.assertEqual(r.returncode, 0,
                         "expected ALLOW for: %s\nstderr=%s" % (cmd, r.stderr))

    # --- the sanctioned CLI surface -----------------------------------------
    def test_secret_exec_is_the_whole_point(self):
        self.assertAllowed("python3 airuleset.py secret exec DB_PASS -- psql -c 'select 1'")

    def test_secret_request_status_list_forget(self):
        for action in ("request DB_PASS", "status DB_PASS", "list", "forget DB_PASS",
                       "purge"):
            with self.subTest(action=action):
                self.assertAllowed("python3 airuleset.py secret %s" % action)

    # --- metadata-only heads ------------------------------------------------
    def test_listing_the_store(self):
        self.assertAllowed("ls -l %s/" % STORE)
        self.assertAllowed("ls %s" % STORE)

    def test_stat_and_test_and_rm(self):
        self.assertAllowed("stat %s/DB_PASS.secret" % STORE)
        self.assertAllowed("test -f %s/DB_PASS.secret && echo present" % STORE)
        self.assertAllowed("rm -f %s/DB_PASS.secret" % STORE)
        self.assertAllowed("chmod 600 %s/DB_PASS.secret" % STORE)

    # --- unrelated traffic must not be caught -------------------------------
    def test_the_unrelated_ssh_key_dir(self):
        # `~/.secrets/` (plural, no .claude) is the gatekeeper access-key dir
        # from block-subdev-ssh-misuse.sh — a different thing entirely.
        self.assertAllowed("ssh -i ~/.secrets/gatekeeper_access_ed25519 marek@subdev")

    def test_grepping_the_word_secrets(self):
        self.assertAllowed("grep -rn secrets hooks/")

    def test_the_upload_log(self):
        self.assertAllowed("grep SAVED ~/.claude/upload-logs/upload-8788.log")

    def test_running_the_suite(self):
        self.assertAllowed("python3 -m pytest tests/ -q")

    def test_an_ordinary_dotfile_read(self):
        self.assertAllowed("cat ~/.claude/settings.json")

    def test_empty_and_unrelated(self):
        self.assertAllowed("git status --porcelain")


class BypassIsForTheUserNotTheAgent(unittest.TestCase):
    """The deliberate deviation from this repo's hook convention.

    Every other hook here takes an inline `# airuleset:<x>-ok <reason>` marker.
    This one must NOT: a marker the agent can append to its own command is the
    voluntary compliance #153 exists to remove. Only an env bypass remains, and
    it is logged.
    """

    def test_an_inline_marker_does_not_open_the_store(self):
        for marker in ("# airuleset:secret-read-ok testing",
                       "# airuleset:destructive-ok testing",
                       "# airuleset:scope-gate-ok testing",
                       "# airuleset:script-ok testing"):
            with self.subTest(marker=marker):
                r = run("cat %s/DB_PASS.secret  %s" % (STORE, marker))
                self.assertEqual(r.returncode, 2,
                                 "an inline marker must not open the store: %s"
                                 % marker)

    def test_the_env_bypass_works_and_is_logged(self):
        with tempfile.TemporaryDirectory() as td:
            r = run("cat %s/DB_PASS.secret" % STORE,
                    env_extra={"AIRULESET_ALLOW_VAULT_READ": "1",
                               "AIRULESET_VAULT_READ_AUDIT": str(Path(td) / "a.log")})
            self.assertEqual(r.returncode, 0, r.stderr)
            logged = (Path(td) / "a.log").read_text()
            self.assertIn("env-bypass", logged)
            self.assertIn("DB_PASS.secret", logged)


class RefusalQuality(unittest.TestCase):
    def test_the_refusal_points_at_secret_exec(self):
        r = run("cat %s/DB_PASS.secret" % STORE)
        self.assertEqual(r.returncode, 2)
        self.assertIn("secret exec", r.stderr)

    def test_the_refusal_states_its_own_limit(self):
        # The ticket's explicit instruction: do not imply a stronger guarantee
        # than is delivered. The agent's uid has NOPASSWD sudo on these boxes,
        # so uid separation is unachievable and this hook is a guardrail.
        r = run("cat %s/DB_PASS.secret" % STORE)
        self.assertRegex(r.stderr.lower(), r"guardrail|not a (security )?boundary")

    def test_it_fails_closed_when_the_check_itself_breaks(self):
        # A malfunctioning guard must not silently open the store. The failure
        # injected is the REAL one — python3 (which runs the matcher) missing
        # from PATH, with bash itself still reachable so the hook actually runs
        # and has to decide. Reading the payload with a shell builtin instead
        # of `cat` is what makes that distinguishable from "no payload".
        with tempfile.TemporaryDirectory() as empty:
            r = run("cat %s/DB_PASS.secret" % STORE,
                    env_extra={"PATH": empty})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("fail-closed", r.stderr)


class DocumentedGaps(unittest.TestCase):
    """Honest scope. These are NOT caught, and the hook says so rather than
    letting a reader assume the store is sealed."""

    def test_the_hook_documents_what_it_cannot_see(self):
        text = HOOK.read_text()
        self.assertIn("KNOWN GAPS", text)
        for gap in ("sudo", "computed", "settings.json"):
            with self.subTest(gap=gap):
                self.assertIn(gap, text)


class Registered(unittest.TestCase):
    def test_it_is_wired_into_the_pretooluse_bash_chain(self):
        cfg = json.loads((Path(__file__).resolve().parent.parent
                          / "settings" / "hooks.json").read_text())
        bash_hooks = [h["command"]
                      for entry in cfg["hooks"]["PreToolUse"]
                      if entry.get("matcher") == "Bash"
                      for h in entry["hooks"]]
        self.assertTrue(any("block-vault-store-read.sh" in c for c in bash_hooks),
                        "a hook nothing runs is not an artifact: %s" % bash_hooks)


if __name__ == "__main__":
    unittest.main()
