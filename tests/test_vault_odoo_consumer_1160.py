"""#1160 — the odoo-erp posting scripts are sanctioned key consumers.

The Odoo stream contract posts owner-approved client messages with
`python3 scripts/odoo_post.py … --key-file <root>/<handover key>` (and
`scripts/odoo-task-sync.py` the same way). The #1153 guard counted a root path
only as an `ssh -i`-style key argument, so the documented form was refused and
no stream could post. The fix (design issuecomment-5848134350, Approach 1) is a
two-script REPO_KEY_CONSUMERS table: ONLY the value of `--key-file X` /
`--key-file=X` of those scripts is accounted, like `ssh -i`. Every other root
reference in the segment stays unaccounted, so each ALLOW below has a DENY
twin that differs in exactly one thing.

No test here touches a real key file: every path is built from pieces and the
hook only ever sees command TEXT.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-vault-store-read.sh"

DOT = "." + "sec" + "rets"            # never a literal in this file's source
R = "~/" + DOT
RA = "/home/miva1/" + DOT
KEY = R + "/miva_handover.env"
POST = "python3 scripts/odoo_post.py --channel 288 --body-file /tmp/m.html"
SYNC = "python3 scripts/odoo-task-sync.py --task 503 --stage verif"
REPO = "/home/miva1/devel/odoo-erp"


def run(cmd):
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": REPO}
    return subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


class Case(unittest.TestCase):
    def assertAllowed(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 0, "expected ALLOW for: %s\nstderr=%s"
                         % (cmd, r.stderr))

    def assertDenied(self, cmd):
        r = run(cmd)
        self.assertEqual(r.returncode, 2, "expected DENY for: %s\nstderr=%s"
                         % (cmd, r.stderr))
        return r


class Allowed(Case):
    def test_key_file_separate_value(self):
        self.assertAllowed("%s --key-file %s" % (POST, KEY))

    def test_key_file_inline_value(self):
        self.assertAllowed("%s --key-file=%s" % (POST, KEY))

    def test_key_file_before_other_options(self):
        self.assertAllowed("python3 scripts/odoo_post.py --key-file %s --channel 288"
                           % KEY)

    def test_task_sync(self):
        self.assertAllowed("%s --key-file %s" % (SYNC, KEY))
        self.assertAllowed("%s --key-file=%s/k" % (SYNC, RA))

    def test_cd_repo_prefix(self):
        self.assertAllowed("cd %s && %s --key-file %s" % (REPO, POST, KEY))

    def test_absolute_script_path(self):
        self.assertAllowed("python3 %s/scripts/odoo_post.py --key-file %s"
                           % (REPO, KEY))

    def test_script_as_head(self):
        self.assertAllowed("./scripts/odoo_post.py --key-file %s" % KEY)
        self.assertAllowed("%s/scripts/odoo-task-sync.py --key-file=%s" % (REPO, KEY))

    def test_interpreter_options_and_wrappers(self):
        self.assertAllowed("python3 -u scripts/odoo_post.py --key-file %s" % KEY)
        self.assertAllowed("python scripts/odoo_post.py --key-file %s" % KEY)
        self.assertAllowed("timeout 60 python3 scripts/odoo_post.py --key-file %s"
                           % KEY)
        self.assertAllowed("%s --key-file %s 2>&1" % (POST, KEY))


class Denied(Case):
    def test_another_script(self):
        self.assertDenied("python3 other.py --key-file %s" % KEY)
        self.assertDenied("python3 scripts/odoo_post_debug.py --key-file %s" % KEY)
        self.assertDenied("python3 myscripts/odoo_post.py --key-file %s" % KEY)

    def test_other_option_of_the_consumer(self):
        self.assertDenied("python3 scripts/odoo_post.py --body-file %s" % KEY)
        self.assertDenied("python3 scripts/odoo_post.py --key-file %s --body-file %s/x"
                          % (KEY, R))

    def test_abbreviation_and_positional(self):
        # argparse would take `--key` as an abbreviation; only the exact
        # option is in the table, so anything else stays unaccounted (deny).
        self.assertDenied("python3 scripts/odoo_post.py --key %s" % KEY)
        self.assertDenied("python3 scripts/odoo_post.py %s" % KEY)
        # After `--` every token is a positional, never an option value.
        self.assertDenied("python3 scripts/odoo_post.py -- --key-file %s" % KEY)

    def test_trailing_read(self):
        self.assertDenied("%s --key-file %s; cat %s" % (POST, KEY, KEY))
        self.assertDenied("%s --key-file %s && head -c 20 %s" % (POST, KEY, KEY))

    def test_python_c_open(self):
        self.assertDenied("python3 -c 'print(open(\"%s/k\").read())'" % RA)
        self.assertDenied("python3 -c 'import sys;print(open(sys.argv[2]).read())' "
                          "scripts/odoo_post.py --key-file %s" % KEY)

    def test_interpreter_modes_that_run_other_code(self):
        self.assertDenied("python3 -m pdb scripts/odoo_post.py --key-file %s" % KEY)
        self.assertDenied("python3 -i scripts/odoo_post.py --key-file %s" % KEY)
        self.assertDenied("PYTHONINSPECT=1 python3 scripts/odoo_post.py --key-file %s"
                          % KEY)
        self.assertDenied("env PYTHONPATH=/tmp/x python3 scripts/odoo_post.py "
                          "--key-file %s" % KEY)

    def test_stdin_or_pipe_into_the_consumer(self):
        # An interpreter that ends up reading stdin could run fed code.
        self.assertDenied("%s --key-file %s <<< 'print(1)'" % (POST, KEY))
        self.assertDenied("%s --key-file %s < /tmp/code.py" % (POST, KEY))
        self.assertDenied("cat /tmp/code.py | %s --key-file %s" % (POST, KEY))

    def test_redirect_glued_to_the_value(self):
        self.assertDenied("%s --key-file=%s>%s/x" % (POST, KEY, R))


class Message(Case):
    def test_block_message_names_the_consumer_form(self):
        r = self.assertDenied("cat %s" % KEY)
        self.assertIn("scripts/odoo_post.py", r.stderr)
        self.assertIn("--key-file", r.stderr)


if __name__ == "__main__":
    unittest.main()
