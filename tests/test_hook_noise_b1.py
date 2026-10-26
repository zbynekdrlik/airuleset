"""Bundle B1 of issue 1137 (owner 26.10.2026): fewer hook blocks on the subdev streams.

Measured on the subdev stream transcripts since 19.10.: 50-260 PreToolUse
blocks per stream per week. Every ALLOW case below is a real blocked command
from those transcripts (paths and prose shortened, shape kept); every BLOCK
case is the protection that must stay.

  1. block-vault-store-read: a repo-relative `.claude` in a recursive grep is
     the repo's own directory, not the store's parent; a quoted heredoc body
     written to a file is data; the Odoo posting form may pipe into `tail`;
     a plain assignment is not a PATH rebind. The refusal names the exact
     working command for the file it refused.
  2. block-sensitive-staging: a generic 32+ char blob is not a secret; known
     credential formats and key/token/secret/password assignments still are.
  3. pre-write-script-check: scratch and work-product helpers are exempt.
  4. block-test-skips (issue 1255): the sanctioned notApplicable() literal at
     any indentation.
  5. block-root-recursive-grep: a scoped path under ~/.claude is not the
     transcript tree; the refusal names the root it refused.
  6/7. the W-drain and dispatch-design gates are gone (no-op stubs stay for
     sessions started before the install).
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
FIXTURES = REPO / "tests" / "fixtures" / "hooknoise_b1"
sys.path.insert(0, str(REPO))

from gates import prod_transfer, secrets  # noqa: E402

ODOO_REPO = "/home/u/devel/odoo/odoo-erp"
KEYS = "~/." + "secrets"


def _home(testcase):
    home = tempfile.mkdtemp(prefix="b1-home-")
    testcase.addCleanup(shutil.rmtree, home, True)
    return home


def _hook(testcase, name, payload, home=None, cwd=None):
    env = dict(os.environ, HOME=home or _home(testcase),
               AIRULESET_HOOK_BLOCK_LOG="/dev/null")
    return subprocess.run(["/bin/bash", str(HOOKS / name)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60,
                          cwd=cwd or tempfile.gettempdir())


def _bash(testcase, name, command, cwd=ODOO_REPO, home=None):
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "session_id": "b1"}
    if cwd is not None:
        payload["cwd"] = cwd
    return _hook(testcase, name, payload, home=home)


# --------------------------------------------------------------------------- #
# 1. block-vault-store-read
# --------------------------------------------------------------------------- #
class VaultRepoClaude(unittest.TestCase):
    HOOK = "block-vault-store-read.sh"

    def allowed(self, command, cwd=ODOO_REPO, home=None):
        r = _bash(self, self.HOOK, command, cwd=cwd, home=home)
        self.assertEqual(r.returncode, 0, "%s\n%s" % (command, r.stderr))

    def blocked(self, command, cwd=ODOO_REPO, home=None):
        r = _bash(self, self.HOOK, command, cwd=cwd, home=home)
        self.assertEqual(r.returncode, 2, "%s\n%s" % (command, r.stderr))
        return r

    def test_repo_claude_grep_allowed(self):
        for command in (
                'grep -rn "exactly where the server\\|that very rule" --include=*.ts '
                '--include=*.md --include=*.py frontline addons changelog.d .claude '
                'docs 2>/dev/null | head',
                'grep -rln "Spec-trace" .claude/ | head',
                'grep -rn "pg_advisory_xact_lock\\|REPEATABLE READ" --include=*.py '
                '--include=*.md addons .claude 2>/dev/null | head -30',
                'grep -rn "9274" .claude/ docs/ 2>/dev/null | head'):
            with self.subTest(command=command):
                self.allowed(command)

    def test_cd_into_repo_then_grep(self):
        home = _home(self)
        self.allowed('cd %s && grep -rln "B314" .claude/ docs/ 2>/dev/null | head' % ODOO_REPO,
                     cwd=home, home=home)
        self.allowed("cd /tmp/claude-1009/-home-u-devel-odoo-odoo-erp/1a95/scratchpad/dev"
                     " && grep -rln 'report_offline' addons/ frontline/ .claude/ 2>/dev/null",
                     cwd=home, home=home)

    def test_home_claude_still_blocked(self):
        home = _home(self)
        self.blocked("grep -rn password .claude", cwd=home, home=home)
        self.blocked("cd ~ && grep -rn password .claude", home=home)
        self.blocked("grep -rn password ../.claude", cwd=home + "/devel", home=home)
        self.blocked("grep -rn password ~/.claude", home=home)
        self.blocked("grep -rn password /home/other/.claude")
        self.blocked("grep -rn password .claude", cwd=None)
        self.blocked("tar czf /tmp/c.tgz .claude")

    def test_cd_into_parent_then_grep(self):
        # Before B1 a recursive grep of the cwd after `cd ~/.claude` read the
        # store and was allowed: no `.claude` token was left to see.
        self.blocked("cd ~/.claude && grep -rn password .")
        self.blocked("cd ~/.claude && grep -rn password")


class VaultHeredoc(unittest.TestCase):
    HOOK = "block-vault-store-read.sh"

    def test_script_written_by_cat_is_data(self):
        command = ("mkdir -p /tmp/s && cat > /tmp/s/prod_menu.py <<'EOF'\n"
                   "import os\n"
                   "KEY = open(os.path.expanduser(\"%s/slovnormal-handover-api-key.env\"))"
                   ".read().strip()\n"
                   "EOF" % KEYS)
        r = _bash(self, self.HOOK, command)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_body_apostrophe_keeps_next_command(self):
        # The body's apostrophe used to open a quote that swallowed the
        # sanctioned `secret exec` after the heredoc into one segment.
        command = ("cd %s && python3 - <<'PY'\n"
                   "p = \"tests/e2e/x.spec.ts\"\n"
                   "# Odoo's own create made the approver an officer\n"
                   "open(p, \"w\").write(open(p).read().replace(\"a\", \"b\"))\n"
                   "PY\n"
                   "python3 ~/devel/airuleset/airuleset.py secret exec --file "
                   "%s/miva-copy-ekonomika.env --env TEST_PASS -- npx playwright test "
                   "tests/e2e/x.spec.ts" % (ODOO_REPO, KEYS))
        r = _bash(self, self.HOOK, command)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_escaped_quote_keeps_next_command(self):
        # A backslash-escaped `\"` outside quotes opened a phantom string that
        # swallowed the sanctioned `secret exec` after it (david1 sample).
        command = ("cd %s && grep -n -A2 'id=\"group_rozvoz_manager\"' addons/x/security/*.xml"
                   " | grep name=\\\"name\\\"; python3 ~/devel/airuleset/airuleset.py secret exec"
                   " --file %s/slovnormal-handover-api-key.env --env KEY -- python3 /tmp/s/grp.py"
                   " 2>&1 | tail -3" % (ODOO_REPO, KEYS))
        r = _bash(self, self.HOOK, command)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _bash(self, self.HOOK, "echo name=\\\"x; cat %s/k.env; echo \\\"" % KEYS)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_executed_bodies_still_blocked(self):
        for command in (
                "python3 - <<'EOF'\nkey = open('%s/k.env').read()\nEOF" % KEYS,
                "cat <<'EOF' | bash\ncat %s/k.env\nEOF" % KEYS,
                "cat > /tmp/x <<EOF\n$(cat %s/k.env)\nEOF" % KEYS,
                "cat > /tmp/x <<'EOF'\nhello\nEOF\ncat %s/k.env" % KEYS):
            with self.subTest(command=command):
                r = _bash(self, self.HOOK, command)
                self.assertEqual(r.returncode, 2, r.stderr)


class VaultPipesAndAssigns(unittest.TestCase):
    HOOK = "block-vault-store-read.sh"

    def test_odoo_post_piped_to_tail(self):
        command = ("python3 scripts/odoo_post.py --model discuss.channel --res-id 31 "
                   "--key-file %s/miva-handover-api-key.env --partner-ids 73 72 "
                   "--body '<p>Ďakujeme za vysvetlenie</p>' 2>&1 | tail -20" % KEYS)
        r = _bash(self, self.HOOK, command)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_odoo_post_into_reader_blocked(self):
        command = ("python3 scripts/odoo_post.py --key-file %s/k.env --body x"
                   " | xargs cat" % KEYS)
        self.assertEqual(_bash(self, self.HOOK, command).returncode, 2)

    def test_plain_assignment_before_listing(self):
        command = ("W=/home/u/.claude/work-products; ls %s/ 2>/dev/null"
                   " | grep -i miva" % KEYS)
        r = _bash(self, self.HOOK, command)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_path_rebind_before_listing(self):
        command = "PATH=/tmp/evil; ls %s/ | grep -i miva" % KEYS
        self.assertEqual(_bash(self, self.HOOK, command).returncode, 2)


class VaultExactCommand(unittest.TestCase):
    HOOK = "block-vault-store-read.sh"

    def test_key_file_names_its_command(self):
        r = _bash(self, self.HOOK,
                  "grep -o '^[A-Z_]*=' %s/slovnormal-handover-api-key.env" % KEYS)
        self.assertEqual(r.returncode, 2)
        f = KEYS + "/slovnormal-handover-api-key.env"
        self.assertIn("airuleset.py secret inspect %s" % f, r.stderr)
        self.assertIn("airuleset.py secret exec --file %s --env "
                      "SLOVNORMAL_HANDOVER_API_KEY -- " % f, r.stderr)

    def test_bare_secret_gets_full_command(self):
        r = _bash(self, self.HOOK,
                  "secret exec --file %s/miva-handover-api-key.env --env MIVA_KEY -- "
                  "python3 -I ~/.claude/work-products/scripts/task_search.py" % KEYS)
        self.assertEqual(r.returncode, 2)
        self.assertIn("python3 ~/devel/airuleset/airuleset.py secret exec --file "
                      "%s/miva-handover-api-key.env" % KEYS, r.stderr)

    def test_store_value_names_its_command(self):
        r = _bash(self, self.HOOK, "cat ~/.claude/secrets/DB_PASS.secret")
        self.assertEqual(r.returncode, 2)
        self.assertIn("airuleset.py secret exec DB_PASS -- ", r.stderr)


# --------------------------------------------------------------------------- #
# 2. block-sensitive-staging (gates.secrets.scan_line)
# --------------------------------------------------------------------------- #
# Literals are assembled from <32-char pieces, so this file itself never
# carries a blob the staging scanner of an older install would flag.
RECEIPT = "O-7DBCDA8A56EE426D" + "BCDA8A56EE426D1A"
HEX40 = "1c0af61882cd10d4a984" + "fcca73e8a3b0d1e2f3a4"
RAND = "q7Zp2Lx9Vw4Rt8Ns" + "3Km6Jh1Gf5Dd0Ss8"


class StagingNoEntropy(unittest.TestCase):
    def test_generic_blobs_pass(self):
        for line in (
                "class TestMontaluMobileKiosk" + "Lockout8767(_MobileKioskMixin, HttpCase):",
                'UID = "%s"' % RECEIPT,
                '"referenceReceiptId": "%s"}' % RECEIPT,
                '"external_id": "dbj-' + "b254a45f6c31a2b6" + "b7815da564522aec" + '",',
                '"_source": "zbynekdrlik/devbridge tag v0.8.49 = %s",' % HEX40,
                '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAA' + 'BAAAAAQCAYAAAAf8">'):
            with self.subTest(line=line):
                self.assertIsNone(secrets.scan_line(line))

    def test_known_formats_still_block(self):
        jwt = ("eyJhbGciOiJIUzI1NiIs" + "InR5cCI6IkpXVCJ9." + "eyJzdWIiOiIxMjM0NTY3"
               + "ODkwIn0." + "SflKxwRJSMeKKF2QT4fw" + "pMeJf36POk6yJV_adQssw5c")
        discord = "MTA4NjY5NzQ4ODQ2MjM0" + "MTEyMA" + ".GxYz12." + "abcdefghijklmnopqrstu" + "vwxyz123456"
        for line in (
                "token = gh" + "p_" + "0123456789abcdefghij0123456789",
                "github_" + "pat_" + "11ABCDEFG0123456789_abcdefghijk",
                "sk-ant-" + "api03-" + "abcdefghijklmnopqrstuvwxyz",
                "AKIA" + "IOSFODNN7EXAMPLQ",
                "xox" + "b-1234567890-abcdefghij",
                "-----BEGIN RSA PRIVATE" + " KEY-----",
                "Authorization: " + jwt,
                "DISCORD_BOT = " + discord,
                'API_KEY = "%s"' % RAND,
                'password: "%s"' % RAND,
                'passwd = "%s"' % RAND,
                "export ODOO_API_KEY=%s" % HEX40,
                "SECRET_TOKEN=%s" % RAND):
            with self.subTest(line=line):
                self.assertIsNotNone(secrets.scan_line(line))

    def test_placeholders_still_pass(self):
        for line in ('API_KEY = "$ODOO_API_KEY"', "export ODOO_API_KEY=<your-key-here>",
                     "PASSWORD=${POKLADNA_PASS}", 'token = "YOUR_TOKEN_HERE_PLEASE"'):
            with self.subTest(line=line):
                self.assertIsNone(secrets.scan_line(line))

    def test_prod_transfer_manifest_receipt(self):
        line = "Prod-transfer: receipt %s — supplied by dev 24.10.2026" % RECEIPT
        self.assertIsNone(prod_transfer.line_has_secret_value(line))

    def test_hook_stages_receipt_fixture(self):
        root = tempfile.mkdtemp(prefix="b1-stage-")
        self.addCleanup(shutil.rmtree, root, True)

        def git(*a):
            subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, check=True)
        git("init", "-q", "-b", "main")
        rel = "addons/x/tests/test_receipt_return_9419.py"
        os.makedirs(os.path.join(root, os.path.dirname(rel)))
        with open(os.path.join(root, rel), "w") as fh:
            fh.write('UID = "%s"\n\n\nclass TestReceiptReturn' % RECEIPT
                     + 'RefundFlow9419Common(object):\n    pass\n')
        payload = {"tool_name": "Bash", "tool_input": {"command": "git add %s" % rel}}
        r = _hook(self, "block-sensitive-staging.sh", payload, cwd=root)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(root, "settings.py"), "w") as fh:
            fh.write('API_KEY = "%s"\n' % RAND)
        payload["tool_input"]["command"] = "git add settings.py"
        r = _hook(self, "block-sensitive-staging.sh", payload, cwd=root)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)


# --------------------------------------------------------------------------- #
# 3. pre-write-script-check
# --------------------------------------------------------------------------- #
NO_PIPEFAIL = "#!/usr/bin/env bash\necho hi\n"
EXCEPT_PASS = "try:\n    open('x')\nexcept FileNotFoundError:\n    pass\n"


class ScriptCheckScratch(unittest.TestCase):
    HOOK = "pre-write-script-check.sh"

    def write(self, path, content, home):
        return _hook(self, self.HOOK, {"tool_name": "Write", "tool_input": {
            "file_path": path, "content": content}}, home=home)

    def test_scratch_and_work_products_exempt(self):
        home = _home(self)
        for path, content in (
                ("/tmp/claude-1009/-home-u-devel-odoo-odoo-erp/1a95/scratchpad/"
                 "agent-9243/dev2-fl.sh", NO_PIPEFAIL),
                ("/tmp/b1-helper/gate_dry.sh", NO_PIPEFAIL),
                (home + "/.claude/work-products/9469/wait_agent.sh", NO_PIPEFAIL),
                (home + "/.claude/work-products/9263/wait-for.py", EXCEPT_PASS)):
            with self.subTest(path=path):
                r = self.write(path, content, home)
                self.assertEqual(r.returncode, 0, r.stderr)
        r = _hook(self, self.HOOK, {"tool_name": "Edit", "tool_input": {
            "file_path": "/tmp/b1-helper/probe.py", "old_string": "x",
            "new_string": EXCEPT_PASS}}, home=home)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_repo_files_still_checked(self):
        home = _home(self)
        for path, content in (
                (ODOO_REPO + "/scripts/run_gates.sh", NO_PIPEFAIL),
                (ODOO_REPO + "/addons/x/tests/test_race_9409.py", EXCEPT_PASS),
                ("/tmp/../home/u/devel/odoo/odoo-erp/scripts/x.sh", NO_PIPEFAIL)):
            with self.subTest(path=path):
                self.assertEqual(self.write(path, content, home).returncode, 2)


# --------------------------------------------------------------------------- #
# 4. block-test-skips (issue 1255)
# --------------------------------------------------------------------------- #
class TestSkipsIndented(unittest.TestCase):
    def _push(self, fixture):
        root = tempfile.mkdtemp(prefix="b1-skip-")
        self.addCleanup(shutil.rmtree, root, True)

        def git(*a):
            return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        rel = "tests/e2e/pokladna-catering-storno-9327.spec.ts"
        os.makedirs(os.path.join(root, "tests", "e2e"))
        with open(os.path.join(root, rel), "w") as fh:
            fh.write("")
        git("add", rel)
        git("commit", "-qm", "base")
        git("update-ref", "refs/remotes/origin/main", git("rev-parse", "HEAD").stdout.strip())
        with open(os.path.join(root, rel), "a") as fh:
            fh.write((FIXTURES / fixture).read_text())
        git("add", rel)
        git("commit", "-qm", "test: storno")
        payload = {"tool_input": {"command": "git push origin main"}}
        return _hook(self, "block-test-skips.sh", payload, cwd=root)

    def test_indented_sanctioned_literal(self):
        r = self._push("allow_indented.txt")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_indented_real_skips_block(self):
        for fixture in ("block_indented_append.txt", "block_indented_empty.txt",
                        "block_comment_then_real.txt"):
            with self.subTest(fixture=fixture):
                self.assertEqual(self._push(fixture).returncode, 2)


# --------------------------------------------------------------------------- #
# 5. block-root-recursive-grep
# --------------------------------------------------------------------------- #
class RootGrepScoped(unittest.TestCase):
    HOOK = "block-root-recursive-grep.sh"

    def test_scoped_claude_paths_allowed(self):
        for command in (
                'grep -rn "david3-u6a" ~/.claude/work-products/9258/ 2>/dev/null | head -5',
                'grep -rl "start_sink" /home/david3/.claude/work-products/ 2>/dev/null',
                'grep -rn "secret exec" /home/david3/.claude/projects/'
                '-home-david3-devel-odoo-odoo-erp/memory/*.md',
                "grep -rn -i -E 'status query|krok za krokom' "
                "~/.claude/skills/user-questions-slovak/ | head -20",
                'grep -rl "openpyxl" ~/.claude/work-products/scripts/*.py 2>/dev/null'):
            with self.subTest(command=command):
                r = _bash(self, self.HOOK, command)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_transcript_roots_still_blocked(self):
        for command, root in (
                ("grep -rn foo ~/.claude", "~/.claude"),
                ("grep -rn foo ~/.claude/projects", "~/.claude/projects"),
                ("grep -rn foo ~/.claude/projects/somerepo", "~/.claude/projects/somerepo"),
                ("grep -rn foo ~/.claude/projects/somerepo/*.jsonl",
                 "~/.claude/projects/somerepo/*.jsonl"),
                ("grep -rn foo /", "/")):
            with self.subTest(command=command):
                r = _bash(self, self.HOOK, command)
                self.assertEqual(r.returncode, 2)
                self.assertIn("Refused root: %s" % root, r.stderr)


# --------------------------------------------------------------------------- #
# 6/7. retired dispatch gates
# --------------------------------------------------------------------------- #
RETIRED = ("block-dispatch-over-wdrain.sh", "block-dispatch-without-main-design.sh")


class RetiredDispatchGates(unittest.TestCase):
    def test_not_registered(self):
        hooks_json = (REPO / "settings" / "hooks.json").read_text()
        for name in RETIRED:
            self.assertNotIn(name, hooks_json)

    def test_stubs_are_silent_noops(self):
        payload = {"tool_name": "Agent", "cwd": ODOO_REPO, "tool_input": {
            "subagent_type": "autopilot-worker", "prompt": "Work issue #1 in repo"}}
        for name in RETIRED:
            with self.subTest(name=name):
                text = (HOOKS / name).read_text()
                self.assertIn("set -euo pipefail", text)
                self.assertIn("retired by issue 1137", text)
                self.assertLess(len(text.splitlines()), 10)
                r = _hook(self, name, payload)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_receipt_cli_gone(self):
        self.assertFalse((REPO / "cli_wdrain.py").exists())
        self.assertNotIn('"wdrain-pass"', (REPO / "airuleset.py").read_text())

    def test_doctrine_names_no_retired_gate(self):
        for rel in ("skills/autopilot/SKILL.md", "agents/autopilot-worker.md",
                    "agents/implementer.md", "hooks/situational-triggers.conf",
                    ".claude/rules/internals-hooks.md", ".claude/rules/internals-gates.md"):
            text = (REPO / rel).read_text()
            for token in RETIRED + ("wdrain-pass", "WDRAIN-BYPASS"):
                with self.subTest(rel=rel, token=token):
                    self.assertNotIn(token, text)


class DesignBySpoof(unittest.TestCase):
    HOOK = "block-design-by-spoof.sh"
    WT = ODOO_REPO + "/.claude/worktrees/agent-x"

    def test_comment_read_is_not_a_post(self):
        command = ("gh api --paginate repos/o/r/issues/9263/comments --jq "
                   "'.[] | select(.body | test(\"Design-by: main|Anchors-confirmed\"))'")
        r = _bash(self, self.HOOK, command, cwd=self.WT)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_block_points_at_design_record(self):
        r = _bash(self, self.HOOK, 'gh issue comment 5 --body "Design-by: main x"',
                  cwd=self.WT)
        self.assertEqual(r.returncode, 2)
        head = [ln for ln in r.stderr.splitlines() if ln.strip()][:2]
        self.assertIn("airuleset.py design-record", " ".join(head))


if __name__ == "__main__":
    unittest.main()
