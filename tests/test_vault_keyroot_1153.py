"""#1153 — plain key files under the home `.secrets` dir get the vault read guard.

odoo-erp 8236: a montalu1 lane printed a PROD Odoo API key into its session
transcript twice in one hour while "checking the format" of a legacy plain key
file (`cat`, `head`, `python3 -c 'print(open(...))'`). The airuleset store had
a deny-by-default guard (hooks/block-vault-store-read.sh, #153/#156); the
plain key-file root had none. This file locks the SECOND protected root on the
SAME engine: any reference is DENIED unless the head is provably non-printing
(metadata heads, or a consumer that takes the path as a KEY argument).

The ALLOW half is load-bearing too: this very fleet runs `ssh -i <root>/<key>`
thousands of times (2,279 of 2,532 real commands naming the root on the
controller, measured 2026-09-25), plus `timeout N ssh -i …`, loop bodies
(`do ssh -i …`) and `ssh … 'bash -s' < script`. A guard that refused those
would be ripped out within the hour.

No test here touches a real key file: every path is built from pieces and
the hook only ever sees command TEXT.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-vault-store-read.sh"

DOT = "." + "sec" + "rets"            # never a literal in this file's source
R = "~/" + DOT                        # the root as a person types it
RH = "$HOME/" + DOT
RA = "/home/montalu1/" + DOT
KEY = R + "/montalu_handover.env"
CLI = "python3 ~/devel/airuleset/airuleset.py secret"


def run_payload(payload_obj):
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    return subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload_obj),
                          capture_output=True, text=True, env=env)


def run(cmd):
    return run_payload({"tool_name": "Bash", "tool_input": {"command": cmd},
                        "cwd": "/home/newlevel/devel/airuleset"})


class Denied(unittest.TestCase):
    def assertDenied(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2, "expected DENY for: %s\nstdout=%s\nstderr=%s"
                         % (cmd, r.stdout, r.stderr))
        return r

    def test_the_incident_readers(self):
        for reader in ("cat", "head", "tail", "sed -n 1p", "xxd", "od -c",
                       "strings", "less", "nl", "bat", "tac", "awk 1",
                       "base64", "cut -c1-8", "grep -c .", "cp", "diff /dev/null",
                       "some-future-pager"):
            with self.subTest(reader=reader):
                self.assertDenied("%s %s" % (reader, KEY))

    def test_every_spelling_of_the_root(self):
        for root in (R, RH, "${HOME}/" + DOT, RA, "/root/" + DOT):
            with self.subTest(root=root):
                self.assertDenied("cat %s/k.env" % root)

    def test_python_open_and_print(self):
        # The exact 02:50Z shape: a "format check" that printed the value.
        self.assertDenied("python3 -c 'print(open(\"%s/k.env\").read())'" % RA)
        self.assertDenied(
            "python3 -c \"import pathlib;print(pathlib.Path('%s/k').read_text()[:6])\""
            % RA)

    def test_redirection_and_substitution(self):
        self.assertDenied("while read l; do echo \"$l\"; done < %s" % KEY)
        self.assertDenied("echo \"$(<%s)\"" % KEY)
        self.assertDenied("export K=$(tr -d '[:space:]' < %s)" % KEY)
        # A read nested inside an otherwise-allowed head is still seen.
        self.assertDenied("ls \"$(cat %s)\"" % KEY)

    def test_glob_brace_and_path_noise(self):
        for spelled in ("~/.sec*/k.env", "~/.secre?s/k.env", "~/.secr[e]ts/k.env",
                        "~/{" + DOT + ",x}/k.env", "~/{.sec,.y}rets/k.env",
                        "~/x/../" + DOT + "/k.env", "~/./" + DOT + "/k.env"):
            with self.subTest(spelled=spelled):
                self.assertDenied("cat %s" % spelled)

    def test_quote_splicing_inside_the_name(self):
        # `.secret"s"` is `.secrets` to the shell; the raw text is not.
        self.assertDenied("cat ~/.secret\"s\"/k.env")
        self.assertDenied("cat ~/'.sec'rets/k.env")

    def test_relative_after_a_cd_home(self):
        self.assertDenied("cd ~ && cat %s/k.env" % DOT)
        self.assertDenied("cd ~ && cat .sec*/k.env")

    def test_entering_the_root_is_refused(self):
        # The Bash tool's cwd persists across calls; a stateless hook cannot
        # see a bare `cat *` in the NEXT call, so the entry itself is refused.
        self.assertDenied("cd %s" % R)
        self.assertDenied("pushd %s" % R)

    def test_an_assignment_naming_the_root(self):
        self.assertDenied("K=%s; cat \"$K\"" % KEY)
        self.assertDenied("export K=%s" % KEY)
        # A variable holding an ssh call is allowed only when that call is
        # identity-only; one that also runs a remote read is not.
        self.assertDenied("S=\"ssh -i %s/k host cat %s/x\"; $S" % (R, R))
        self.assertDenied("O=\"-i %s/k host cat %s/x\"" % (R, R))

    def test_a_proxycommand_that_reads_is_not_an_identity(self):
        # ssh echoes a bad banner line back in its error text.
        self.assertDenied("ssh -o ProxyCommand='cat %s/x' host" % R)
        self.assertDenied(
            "ssh -o ProxyCommand='ssh -i %s/k jump cat %s/x' host" % (R, R))

    def test_a_line_continuation_does_not_launder_a_reader(self):
        self.assertDenied("cat \\\n  %s" % KEY)

    def test_ssh_remote_read_of_the_root(self):
        self.assertDenied("ssh host 'cat %s/x'" % R)
        self.assertDenied("ssh -i %s/k host 'cat %s/x'" % (R, R))
        self.assertDenied("ssh -i %s/k host cat %s/x" % (R, RA))
        # stdin fed FROM a key file reaches the remote shell's output.
        self.assertDenied("ssh -i %s/k host 'bash -s' < %s/x" % (R, R))
        self.assertDenied("ssh -o ProxyCommand='cat %s/x' -i %s/k host" % (R, R))

    def test_copying_a_key_elsewhere_is_not_a_key_argument(self):
        self.assertDenied("scp %s host:/tmp/" % KEY)
        self.assertDenied("scp -i %s/k %s host:/tmp/" % (R, KEY))
        self.assertDenied("sftp -b %s/batch host" % R)

    def test_rsync_rsh_that_is_not_an_identity_argument(self):
        self.assertDenied("rsync -e 'cat %s/k' src host:dst" % R)
        self.assertDenied("rsync -e 'ssh -i %s/k' %s host:dst" % (R, KEY))

    def test_a_wrapped_reader_is_still_a_reader(self):
        self.assertDenied("timeout 5 cat %s" % KEY)
        self.assertDenied("sudo cat %s" % KEY)
        self.assertDenied("for f in %s/*; do cat \"$f\"; done" % R)

    def test_metadata_heads_lose_their_exemption_when_piped(self):
        self.assertDenied("ls %s/* | xargs cat" % R)
        self.assertDenied("sha256sum %s | awk '{print $2}' | xargs cat" % KEY)

    def test_metadata_heads_with_a_content_echoing_option(self):
        # `--files0-from` / `-c` ingest the FILE as a list and echo its lines
        # back in their own error text — the #153 `file -f` lesson.
        self.assertDenied("wc --files0-from=%s" % KEY)
        self.assertDenied("sha256sum -c %s" % KEY)
        self.assertDenied("sha256sum --check %s" % KEY)
        self.assertDenied("wc -c --files0-from %s" % KEY)

    def test_secret_exec_does_not_launder_its_child(self):
        # `secret exec` redacts only the VAULT value it injects — a plain key
        # file read by the child would print unfiltered.
        self.assertDenied("%s exec N -- cat %s" % (CLI, KEY))
        self.assertDenied("%s exec N cat %s" % (CLI, KEY))
        self.assertDenied("%s exec N -- sh -c 'cat %s'" % (CLI, KEY))

    def test_a_lookalike_cli_invocation_is_not_the_cli(self):
        self.assertDenied("python3 evil.py airuleset.py secret inspect %s" % KEY)
        self.assertDenied("python3 -c 'print(1)' secret inspect %s" % KEY)

    def test_writes_are_refused_too(self):
        # Hand-writing a key means typing the value into a command.
        self.assertDenied("echo abc > %s" % KEY)
        self.assertDenied("tee %s < /dev/null" % KEY)

    def test_the_refusal_names_the_sanctioned_paths(self):
        r = self.assertDenied("cat %s" % KEY)
        self.assertIn("secret inspect", r.stderr)
        self.assertIn("secret exec", r.stderr)
        self.assertIn("script", r.stderr)


class Allowed(unittest.TestCase):
    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0, "expected ALLOW for: %s\nstdout=%s\nstderr=%s"
                         % (cmd, r.stdout, r.stderr))

    def test_ssh_identity_file_shapes_the_fleet_runs(self):
        for cmd in (
            "ssh -i %s/hetzner host" % R,
            "ssh -i %s/k -o BatchMode=yes -o ConnectTimeout=10 u@100.1.2.3 uptime" % R,
            "ssh -i %s/k host 'bash -s' < /tmp/x/script.sh" % R,
            "ssh -o StrictHostKeyChecking=no -i %s/k host 'cd ~/devel && ls'" % R,
            "ssh -i%s/k host true" % R,
            "ssh -4i %s/k host true" % R,
            "ssh -o IdentityFile=%s/k host true" % R,
            "ssh -oIdentityFile=%s/k host true" % R,
            "ssh -o 'IdentityFile %s/k' host true" % R,
            "ssh -i %s/k host 'ssh -i ~/.ssh/other -o X=y inner uptime'" % RA,
            "sudo ssh -i %s/k host true" % R,
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_the_wrappers_and_loops_the_fleet_uses(self):
        for cmd in (
            "timeout 60 ssh -i %s/k -o BatchMode=yes host uptime" % R,
            "timeout -k 5 30s ssh -i %s/k host true" % R,
            "for h in a b; do ssh -i %s/k \"$h\" uptime; done" % R,
            "if ssh -i %s/k host true; then echo up; fi" % R,
            "until ssh -i %s/k host true; do sleep 1; done" % R,
            "cat /tmp/x/script.sh | ssh -i %s/k host bash -s" % R,
            "R=$(ssh -i %s/k host 'grep -m1 x /tmp/y')" % R,
            "ssh -i %s/k host uptime | tail -1" % R,
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_measured_fleet_shapes_beyond_the_plain_call(self):
        # From the controller corpus replay: a line-continued ssh call, a
        # ProxyCommand that is itself an identity-only ssh, and the option
        # list / command kept in a variable. None of them names the root
        # anywhere but as an identity argument.
        for cmd in (
            "ssh -o BatchMode=yes \\\n  -i %s/k host uptime" % RA,
            "ssh -o ProxyCommand='ssh -i %s/k -W %%h:%%p jump' -i %s/k host true"
            % (R, R),
            "JUMP=\"ssh -i %s/k -o BatchMode=yes -W %%h:%%p\"" % R,
            "O=\"-i %s/k -o ConnectTimeout=5\"; ssh $O host true" % RA,
            "SSH=\"ssh -i %s/k -o BatchMode=yes\"; $SSH host uptime" % RH,
            "export GK=\"ssh -i %s/k gk@100.1.2.3\"" % RH,
            # The remote box using ITS OWN key as an identity, one hop on.
            "ssh -i %s/k hop 'ssh -i %s/k2 -o BatchMode=yes inner uptime'" % (R, R),
            "ssh -i %s/k hop ls -la %s/" % (R, R),
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_scp_sftp_rsync_with_an_identity_argument(self):
        for cmd in (
            "scp -i %s/k ./file host:/tmp/" % R,
            "scp -o IdentityFile=%s/k host:/tmp/x ." % R,
            "sftp -i %s/k host" % R,
            "rsync -avz -e 'ssh -i %s/k' ./src/ host:dst/" % R,
            "rsync -avz -e \"ssh -i %s/k -p 2222\" ./src/ host:dst/" % R,
            "rsync -avze 'ssh -i %s/k' ./src/ host:dst/" % R,
            "rsync --rsh='ssh -i %s/k' ./src/ host:dst/" % R,
            "rsync --rsh 'ssh -i %s/k' ./src/ host:dst/" % R,
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_metadata_heads(self):
        for cmd in ("ls -la %s" % R, "ls %s/" % RH, "stat %s" % KEY,
                    "stat -c '%%s %%a %%U' %s" % KEY, "test -f %s" % KEY,
                    "[ -r %s ] && echo yes" % KEY, "wc -c %s" % KEY,
                    "wc -l %s" % KEY, "sha256sum %s" % KEY,
                    "ls ~/.sec*"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_the_sanctioned_cli(self):
        for cmd in (
            "%s inspect %s" % (CLI, KEY),
            "python3 airuleset.py secret inspect %s" % KEY,
            "%s exec HETZNER --persist %s/hetzner -- curl -sf https://x" % (CLI, R),
            "%s exec HETZNER --persist=%s/hetzner -- curl -sf https://x" % (CLI, R),
            "%s exec N -- ssh -i %s/k host true" % (CLI, R),
            "%s request CF_TOKEN --persist %s/cloudflare" % (CLI, R),
            "%s request A B --persist-map A=%s/a,B=%s/b" % (CLI, R, R),
            "%s show --file %s/k" % (CLI, R),
        ):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)

    def test_a_script_invoked_by_path(self):
        # Its text never names the root — the sanctioned way stream code
        # consumes a key without printing it.
        self.assertAllowed("bash scripts/use-handover-key.sh")
        self.assertAllowed("python3 tools/sync.py --dry-run")

    def test_ordinary_names_that_merely_resemble_the_root(self):
        for cmd in ("cat ~/secrets.txt", "cat foo.secrets", "grep -rn secrets hooks/",
                    "ls ~/.secretsauce", "git log --oneline -3",
                    "cat ~/.ssh/config"):
            with self.subTest(cmd=cmd):
                self.assertAllowed(cmd)


class FileTools(unittest.TestCase):
    """Read/Grep/Glob/Write/Edit are matched by exact tool name already — the
    new root rides the same field scan (file_path/notebook_path/path/glob +
    Glob's pattern)."""

    def test_read_grep_glob_write_edit_are_refused(self):
        for tool, tin in (
            ("Read", {"file_path": RA + "/montalu_handover.env"}),
            ("Read", {"file_path": "/home/newlevel/.sec*/k"}),
            ("Grep", {"pattern": "KEY", "path": RA}),
            ("Glob", {"pattern": RA + "/*"}),
            ("Glob", {"pattern": "*.env", "path": RA}),
            ("Write", {"file_path": RA + "/k", "content": "x"}),
            ("Edit", {"file_path": RA + "/k", "old_string": "a", "new_string": "b"}),
        ):
            with self.subTest(tool=tool, tin=tin):
                r = run_payload({"tool_name": tool, "tool_input": tin})
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("secret inspect", r.stderr)

    def test_grep_pattern_naming_the_root_is_a_search_not_a_read(self):
        r = run_payload({"tool_name": "Grep",
                         "tool_input": {"pattern": "\\" + DOT, "path": "hooks"}})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ordinary_files_stay_readable(self):
        r = run_payload({"tool_name": "Read",
                         "tool_input": {"file_path": "/home/newlevel/.ssh/config"}})
        self.assertEqual(r.returncode, 0, r.stderr)


class MatcherFile(unittest.TestCase):
    """#1153 moved the matcher out of a heredoc into hooks/vault_read_guard.py."""

    def test_a_missing_matcher_fails_closed_not_open(self):
        # `python3 <absent file>` exits 2 — the SAME code a real hit uses — so
        # without an explicit presence check a missing matcher would read as a
        # hit, and the env bypass would turn "cannot check" into an ALLOW.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lone = Path(td) / HOOK.name
            lone.write_text(HOOK.read_text())
            payload = json.dumps({"tool_name": "Bash",
                                  "tool_input": {"command": "ls -la /tmp"}})
            bypass = {"AIRULESET_ALLOW_VAULT_READ": "1",
                      "AIRULESET_VAULT_READ_AUDIT": str(Path(td) / "a.log")}
            for extra in ({}, bypass):
                with self.subTest(env=sorted(extra)):
                    env = {"PATH": "/usr/bin:/bin", "HOME": td, **extra}
                    r = subprocess.run(["/bin/bash", str(lone)], input=payload,
                                       capture_output=True, text=True, env=env)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertIn("fail-closed", r.stderr)
                    self.assertIn("vault_read_guard.py", r.stderr)


class StoreRootUnchanged(unittest.TestCase):
    """The second root must not relax the first."""

    def test_store_reads_still_refused_with_store_guidance(self):
        r = run("cat ~/.claude/secrets/DB_PASS.secret")
        self.assertEqual(r.returncode, 2)
        self.assertIn("secret exec", r.stderr)

    def test_ssh_identity_exemption_does_not_extend_to_the_store(self):
        r = run("ssh -i ~/.claude/secrets/DB_PASS.secret host true")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
