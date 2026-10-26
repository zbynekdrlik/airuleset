"""Review round of issue 1137 B1 (fewer false-positive hook blocks).

Two fresh-context reviews compared main with the B1 branch. Part A lists the
protections B1 lost — each command below was refused on main and must be
refused again. Part B lists cheap false positives B1 left — each command below
is ordinary work that must pass. Every literal that looks like a credential
or a key-file path is assembled from pieces, so this file never trips the
staging scanner or the vault guard it tests.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "hooks"
sys.path.insert(0, str(REPO))

from gates import prod_transfer, secrets  # noqa: E402

KD = "." + "secrets"
KEYS = "~/" + KD
CL = "~/.cla" + "ude"
DOT_CL = ".cla" + "ude"
VAULT = "block-vault-store-read.sh"


def _home(testcase):
    home = tempfile.mkdtemp(prefix="rv-home-")
    testcase.addCleanup(shutil.rmtree, home, True)
    for d in ("devel/repo/.claude/rules", ".claude/skills", "devel/air/hooks",
              KD, ".claude/" + "secrets"):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    return home


def _run(testcase, name, data, home=None, cwd=None):
    env = dict(os.environ, HOME=home or _home(testcase),
               AIRULESET_HOOK_BLOCK_LOG="/dev/null")
    if not isinstance(data, str):
        data = json.dumps(data)
    return subprocess.run(["/bin/bash", str(HOOKS / name)], input=data,
                          capture_output=True, text=True, env=env, timeout=60,
                          cwd=cwd or tempfile.gettempdir())


def _bash(testcase, name, command, cwd=None, home=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "session_id": "rv"}
    if cwd is not None:
        payload["cwd"] = cwd
    return _run(testcase, name, payload, home=home)


class _Vault(unittest.TestCase):
    def check(self, rc, commands, where="repo"):
        home = _home(self)
        cwd = {"repo": home + "/devel/repo", "home": home, "devel": home + "/devel",
               "claude": home + "/" + DOT_CL}[where]
        for command in commands:
            command = command.replace("/HOMEDIR", home)
            with self.subTest(command=command, cwd=where):
                r = _bash(self, VAULT, command, cwd=cwd, home=home)
                self.assertEqual(r.returncode, rc, r.stderr)
                self.assertNotIn("Traceback", r.stderr)


# --------------------------------------------------------------------------- #
# Part A — protections that must hold again
# --------------------------------------------------------------------------- #
class A1GrepGlobParent(_Vault):
    """A recursive grep over a glob straight under ~/.claude reads the store."""

    def test_globbed_operands_refused(self):
        self.check(2, [
            "grep -rn . %s/*" % CL,
            "grep -rh token $HOME/%s/*" % DOT_CL,
            "grep -r token /HOMEDIR/%s/*" % DOT_CL,
            "grep -r password %s/*/" % CL,
            "grep -r password %s/../%s/*" % (CL, DOT_CL),
            "grep -r password %s/[s]ecrets" % CL,
            "grep -r password %s/[s]ecrets/" % CL,
            "grep -r password %s/se*" % CL,
            "grep -rl password %s/*/*" % CL,
            "egrep -r . %s/*" % CL,
            "ugrep -r . %s/*" % CL,
            "grep -rl . %s/* | xargs cat" % CL,
        ])

    def test_relative_globs_from_home(self):
        self.check(2, ["grep -r password %s/*" % DOT_CL,
                       "grep -r password %s/*/" % DOT_CL], where="home")
        self.check(2, ["cd ~ && grep -r password %s/*" % DOT_CL])
        self.check(2, ["grep -r password ../%s/*" % DOT_CL], where="devel")


class A2HeredocExecuted(_Vault):
    """A heredoc body that gets executed is code, not data."""

    def test_executed_bodies_refused(self):
        body = "cat %s/x.env" % KEYS
        py = "print(open('/home/u/%s/x.env').read())" % KD
        self.check(2, [
            "(cat <<'EOF'\n%s\nEOF\n) | bash" % body,
            "(cat <<'EOF'\n%s\nEOF\n)|python3" % body,
            "bash <(cat <<'EOF'\n%s\nEOF\n)" % body,
            "bash < <(cat <<'EOF'\n%s\nEOF\n)" % body,
            "source <(cat <<'EOF'\n%s\nEOF\n)" % body,
            "python3 <(cat <<'EOF'\n%s\nEOF\n)" % py,
            "for i in 1; do\ncat <<'EOF'\n%s\nEOF\ndone | bash" % body,
            "{\ncat <<'EOF'\n%s\nEOF\n} | sh" % body,
        ])


class A3WriteThenRun(_Vault):
    """A file written from a heredoc and executed in the same command."""

    def test_written_then_run_refused(self):
        body = "cat %s/x.env" % KEYS
        py = "print(open('/home/u/%s/x.env').read())" % KD
        self.check(2, [
            "cat <<'EOF' > /tmp/s.sh\n%s\nEOF\nbash /tmp/s.sh" % body,
            "cat <<'EOF' > /tmp/s.sh && bash /tmp/s.sh\n%s\nEOF" % body,
            "cat > /tmp/s.sh <<'EOF'; bash /tmp/s.sh\n%s\nEOF" % body,
            "tee /tmp/s.sh <<'EOF'\n%s\nEOF\nsh /tmp/s.sh" % body,
            "cat <<'EOF' > /tmp/x\n%s\nEOF\n. /tmp/x" % body,
            "cat <<'EOF' > /tmp/x\n%s\nEOF\nsource /tmp/x" % body,
            "cat <<'EOF' >/tmp/x.py\n%s\nEOF\npython3 /tmp/x.py" % py,
            "cat <<'EOF' > /tmp/s.sh\n%s\nEOF\nchmod +x /tmp/s.sh && /tmp/s.sh" % body,
        ])

    def test_written_then_posted_is_data(self):
        self.check(0, ["cat > /tmp/b.md <<'EOF'\nit's at %s/x.env\nEOF\n"
                       "gh issue comment 5 -F /tmp/b.md" % KEYS])


class A4CdTracking(_Vault):
    """A bare `cd` goes HOME; a relative `cd` builds on the previous one."""

    def test_cd_chains_refused(self):
        self.check(2, [
            "cd && grep -rn password %s" % DOT_CL,
            "cd; grep -rn password %s" % DOT_CL,
            "cd ~/devel && cd .. && grep -rn password %s" % DOT_CL,
            "cd ~ && cd %s && grep -rn password ." % DOT_CL,
        ])


class A5RootGrepDots(unittest.TestCase):
    HOOK = "block-root-recursive-grep.sh"

    def test_dot_segments_normalised(self):
        for command in ("grep -r x %s/.." % CL, "grep -r x %s/./projects" % CL,
                        "grep -r x %s/projects/x/../.." % CL,
                        "grep -r x %s/projects/../.." % CL):
            with self.subTest(command=command):
                self.assertEqual(_bash(self, self.HOOK, command).returncode, 2)

    def test_scoped_paths_still_pass(self):
        for command in ("grep -rn x %s/work-products/9258/" % CL,
                        "grep -rn x %s/projects/x/../../work-products/a" % CL):
            with self.subTest(command=command):
                self.assertEqual(_bash(self, self.HOOK, command).returncode, 0)


# Credential-shaped values, assembled at runtime from short pieces.
HEX40 = "3f9a0c1d2e3f4a5b6c7d" + "8e9f0a1b2c3d4e5f6071"
B64 = "aB3xK9pQ2vT4" + "wR8nL5jH3bF6" + "dS1aG0zCq7Y"
PW = "Zq9vX2ab" + "cd1234"
WH_TAIL = "aB3xK9pQ2vT4wR8nL5jH3bF6" + "dS1aG0zCq7Ya-B_c9d8e7f6g" + "5h4i3j2k1l0mNoPqRs"


class A6KnownFormats(unittest.TestCase):
    def test_formats_block(self):
        for line in (
                "url = https://discord.com/api/web" + "hooks/123456789012345678/" + WH_TAIL,
                "https://discordapp.com/api/web" + "hooks/123456789012345678/" + WH_TAIL,
                "CF = cf" + "at_" + B64[:20] + HEX40[:20],
                "BOT = 123456789" + ":AA" + B64[:33],
                "DATABASE_URL = postgres://odoo:" + "S3cr" + "etPass99@db:5432/odoo",
                "https://user:" + "S3cr" + "etPass99@host/x",
                "password: " + B64[:32],
                "api_key: " + HEX40,
                "token: " + B64[:30],
                "secret: " + PW,
                "ODOO_" + "KEY=" + HEX40,
                "export MIVA_" + "KEY=" + HEX40,
                "SMTP_" + "PASS=" + PW,
                "DB_" + "PASSWD=" + PW,
                "KEY=" + HEX40,
                "curl -H 'X-API-" + "KEY: " + B64 + "' https://x",
                "Authorization: Bearer " + B64):
            with self.subTest(line=line):
                self.assertIsNotNone(secrets.scan_line(line))

    def test_placeholders_and_words_pass(self):
        for line in (
                "MONKEY=" + HEX40,
                "postgres://odoo:<password>@db/odoo",
                "postgres://odoo:${DB_PASS}@db/odoo",
                "https://user:xxxxxxxxxx@host/x",
                "https://user:changeme123@host/x",
                "https://user:example-pass@host/x",
                "https://user:********@host/x",
                "password: process.env.EKONOMIKA_PASS",
                "password: ${{ secrets.DB_PASSWORD }}",
                "SMTP_" + "PASS=$SMTP_PASS",
                "ODOO_" + "KEY=${ODOO_KEY}",
                "KEY=<your-key-here>",
                "X-API-" + "KEY: <your-key>",
                "Authorization: Bearer $TOKEN",
                "TOKEN_URL=https://oauth2.googleapis.com/token",
                "GIT_ASKPASS=/usr/bin/true"):
            with self.subTest(line=line):
                self.assertIsNone(secrets.scan_line(line))

    def test_prod_transfer_reuses_formats(self):
        line = "Prod-transfer: ODOO_" + "KEY=" + HEX40 + " — sensitivity: secret"
        self.assertIsNotNone(prod_transfer.line_has_secret_value(line))


class A7DesignByContinuation(unittest.TestCase):
    HOOK = "block-design-by-spoof.sh"
    WT = "/home/u/devel/repo/.claude/worktrees/agent-x"

    def test_continued_post_refused(self):
        for command in (
                "gh api repos/o/r/issues/5/comments \\\n  -f body='Design-by: main x'",
                "gh api \\\n repos/o/r/issues/5/comments -f body='Design-by: main x'"):
            with self.subTest(command=command):
                r = _bash(self, self.HOOK, command, cwd=self.WT)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_continued_read_passes(self):
        command = ("gh api repos/o/r/issues/5/comments \\\n --jq "
                   "'.[] | select(.body | test(\"Design-by: main\"))'")
        self.assertEqual(_bash(self, self.HOOK, command, cwd=self.WT).returncode, 0)


# --------------------------------------------------------------------------- #
# Part B — false positives that must pass
# --------------------------------------------------------------------------- #
class B1CwdClaude(_Vault):
    def test_explicit_operands_outside_store(self):
        self.check(0, ["grep -rn x /HOMEDIR/devel/air/hooks", "grep -rn x skills/"],
                   where="claude")

    def test_cwd_itself_still_refused(self):
        self.check(2, ["grep -rn x .", "grep -rn x", "grep -rn x *"], where="claude")


class B2ProseHeredoc(_Vault):
    def test_message_from_stdin_is_prose(self):
        self.check(0, [
            "git commit -q -F - <<'EOF'\nfix: thing\n\nit's done; see %s/x.env\nEOF" % KEYS,
            "gh issue comment 5 -F - <<'EOF'\nit's at %s/x.env\nEOF" % KEYS,
            "gh pr create --title t --body-file - <<'EOF'\nsee %s/x.env\nEOF" % KEYS,
            "gh issue create -t t -F - <<'EOF'\nsee %s/x.env\nEOF" % KEYS,
        ])

    def test_expanded_or_piped_message_refused(self):
        self.check(2, [
            "git commit -F - <<EOF\n$(cat %s/x.env)\nEOF" % KEYS,
            "git commit -F - <<'EOF' | xargs cat\n%s/x.env\nEOF" % KEYS,
        ])


class B3GrepPattern(_Vault):
    def test_pattern_is_not_a_path(self):
        self.check(0, [
            "grep -rn 'gates\\.%s' gates/" % KD[1:],
            "grep -rn \"scan_line\\|gates\\.%s\" --include=*.py . | grep -v tests/"
            " | head" % KD[1:],
            "grep -rn -e '\\.%s' gates/" % KD[1:],
        ])

    def test_key_file_operands_refused(self):
        self.check(2, ["grep -f %s/x.env gates/" % KEYS, "grep -e x %s/x.env" % KEYS,
                       "grep x %s/x.env" % KEYS])


class B4MetadataPipes(_Vault):
    def test_listing_and_digest_pipes_pass(self):
        self.check(0, [
            "ls -la %s/ | awk '{print $1, $9}' | grep miva" % KEYS,
            "ls -la %s/ | awk '{print $NF}'" % KEYS,
            "sha256sum %s/x.env | cut -c1-12" % KEYS,
            "wc -c %s/x.env | awk '{print $1}'" % KEYS,
        ])

    def test_reading_awk_and_consumers_refused(self):
        self.check(2, [
            "ls %s/ | awk '{system(\"cat \" $0)}'" % KEYS,
            "ls %s/ | awk '{while ((getline l < $0) > 0) print l}'" % KEYS,
            "ls %s/ | awk '{print > \"/tmp/o\"}'" % KEYS,
            "ls %s/* | awk -f /tmp/prog.awk" % KEYS,
            "sha256sum %s/x.env | xargs cat" % KEYS,
        ])


class B5DesignByEditPost(unittest.TestCase):
    def test_message_says_split_the_calls(self):
        command = ("sed -i 's/(Design-by: main)/(main design)/' /tmp/a.md"
                   " && gh issue comment 5 -F /tmp/a.md")
        r = _bash(self, "block-design-by-spoof.sh", command,
                  cwd="/home/u/devel/repo/.claude/worktrees/agent-x")
        self.assertEqual(r.returncode, 2)
        self.assertIn("edit the draft in one call, post it in the next", r.stderr)


class B6ScriptCheckTests(unittest.TestCase):
    HOOK = "pre-write-script-check.sh"
    PATH = "/home/u/devel/repo/tests/test_race_9409.py"

    def write(self, path, content):
        return _run(self, self.HOOK, {"tool_name": "Write", "tool_input": {
            "file_path": path, "content": content}})

    def test_named_exception_in_test_passes(self):
        for exc in ("FileNotFoundError", "(KeyError, ValueError)", "OSError as e"):
            with self.subTest(exc=exc):
                r = self.write(self.PATH, "try:\n    x()\nexcept %s:\n    pass\n" % exc)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_broad_exception_in_test_blocks(self):
        for exc in ("", " Exception", " BaseException", " (ValueError, Exception)"):
            with self.subTest(exc=exc):
                r = self.write(self.PATH, "try:\n    x()\nexcept%s:\n    pass\n" % exc)
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_named_exception_outside_tests_blocks(self):
        r = self.write("/home/u/devel/repo/lib/race.py",
                       "try:\n    x()\nexcept FileNotFoundError:\n    pass\n")
        self.assertEqual(r.returncode, 2, r.stderr)


class B7PayloadShapes(unittest.TestCase):
    SECURITY = ("block-vault-store-read.sh", "block-sensitive-staging.sh")
    OTHERS = ("pre-write-script-check.sh", "block-test-skips.sh",
              "block-design-by-spoof.sh", "block-root-recursive-grep.sh")
    PAYLOADS = (
        {"tool_name": "Bash", "tool_input": {"command": 5}, "cwd": "/tmp"},
        {"tool_name": "Bash", "tool_input": {"command": ["git", "add", "x"]}, "cwd": "/tmp"},
        {"tool_name": "Write", "tool_input": {"file_path": 5, "content": 7}},
        [1, 2],
    )

    def test_no_traceback(self):
        for name in self.SECURITY + self.OTHERS:
            for payload in self.PAYLOADS:
                with self.subTest(hook=name, payload=payload):
                    r = _run(self, name, payload)
                    self.assertNotIn("Traceback", r.stderr + r.stdout)
                    if name in self.OTHERS:
                        self.assertEqual(r.returncode, 0, r.stderr)

    def test_security_hooks_refuse_in_one_line(self):
        for name in self.SECURITY:
            for payload in (self.PAYLOADS[0], self.PAYLOADS[1], self.PAYLOADS[3]):
                with self.subTest(hook=name, payload=payload):
                    r = _run(self, name, payload)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
                    self.assertEqual(len(lines), 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
