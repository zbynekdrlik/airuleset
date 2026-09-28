"""#1168 — a key consumer whose OTHER option is fed by a command substitution.

The Odoo stream contract posts with `python3 scripts/odoo_post.py … --key-file
<root>/<key> --body "$(cat <work-products html>)"` (the script has no
`--body-file`). #1160 accounted the `--key-file` value, yet this documented
form stayed refused: `split_segments` cut the text at `$(` / a backtick, so the
OUTER consumer became the fragment `python3 … --body "` — an unbalanced quote
that `shlex` cannot read, and `key_violation` fails closed on that. The inner
`cat` never named a key path.

The fix (design issuecomment-5868389831, Approach 1): the outer command stays
WHOLE with each substitution body replaced by an inert word, and each body is
judged as its own command by its OWN operands. So every ALLOW below has a DENY
twin where the substitution itself reads a key — that value would land in argv
and the transcript.

No test here touches a real key file: every path is built from pieces and the
hook only ever sees command TEXT.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK = HOOKS / "block-vault-store-read.sh"
sys.path.insert(0, str(HOOKS))
from vault_guard_shell import split_segments  # noqa: E402

DOT = "." + "sec" + "rets"            # never a literal in this file's source
R = "~/" + DOT
FLAT = R + "/miva_handover.env"
NESTED = R + "/miva/handover.key"
KEYS = (FLAT, NESTED)
STORE = "~/.cla" + "ude/sec" + "rets/DB.sec" + "ret"
WP = "~/.cla" + "ude/work-products/miva-4378.html"
REPO = "/home/miva1/devel/odoo/odoo-erp"
CD = "cd ~/devel/odoo/odoo-erp && "
POST = "python3 scripts/odoo_post.py"
DOCUMENTED = (
    POST + " --model discuss.channel --res-id 31 --partner-ids 73 72"
    ' --signature "ZbynekAI 1" --key-file {key} --base-url https://erp.miva.cloud'
    " --expect-identity claude-handover@miva.local"
    ' --approval-ref "owner 2026-09-28 msg 4378" --body "$(cat {body})"')


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


class Allowed(Case):
    def test_documented_command(self):
        for key in KEYS:
            for pre in ("", CD):
                self.assertAllowed(pre + DOCUMENTED.format(key=key, body=WP))

    def test_help(self):
        for key in KEYS:
            for pre in ("", CD):
                self.assertAllowed(pre + "%s --key-file %s --help" % (POST, key))

    def test_cat_of_a_plain_file(self):
        for key in KEYS:
            self.assertAllowed('%s --key-file %s --body "$(cat /tmp/m.html)"'
                               % (POST, key))
            self.assertAllowed("%s --key-file %s --body $(cat /tmp/m.html)"
                               % (POST, key))

    def test_backtick_twin(self):
        for key in KEYS:
            self.assertAllowed('%s --key-file %s --body "`cat /tmp/m.html`"'
                               % (POST, key))

    def test_key_file_after_the_substitution(self):
        self.assertAllowed('%s --body "$(cat %s)" --key-file %s' % (POST, WP, FLAT))
        self.assertAllowed('%s --body "$(cat %s)" --key-file=%s' % (POST, WP, NESTED))

    def test_input_redirect_substitution_of_a_plain_file(self):
        self.assertAllowed('%s --key-file %s --body "$(</tmp/m.html)"' % (POST, FLAT))

    def test_task_sync_with_substitution(self):
        self.assertAllowed('python3 scripts/odoo-task-sync.py --task "$(cat /tmp/t)"'
                           " --key-file %s" % FLAT)


class Denied(Case):
    """A substitution that reads a key puts the value into argv — refused."""

    def test_body_is_the_key(self):
        for key in KEYS:
            for pre in ("", CD):
                self.assertDenied(pre + DOCUMENTED.format(key=key, body=key))
            self.assertDenied('%s --key-file %s --body "$(cat %s)"' % (POST, key, key))
            self.assertDenied('%s --body "$(cat %s)" --key-file %s' % (POST, key, key))

    def test_echo_substitution(self):
        self.assertDenied("echo $(cat %s)" % FLAT)
        self.assertDenied('echo "$(head -c 20 %s)"' % NESTED)

    def test_backticks_anywhere(self):
        self.assertDenied("echo `cat %s`" % FLAT)
        self.assertDenied('%s --key-file %s --body "`cat %s`"' % (POST, FLAT, FLAT))
        self.assertDenied("ls `cat %s`" % NESTED)

    def test_nested_substitution(self):
        self.assertDenied('%s --key-file %s --body "$(echo "$(cat %s)")"'
                          % (POST, FLAT, FLAT))
        self.assertDenied('%s --key-file %s --body "$(echo `cat %s`)"'
                          % (POST, FLAT, FLAT))

    def test_redirect_read_inside_substitution(self):
        self.assertDenied('%s --key-file %s --body "$(<%s)"' % (POST, FLAT, FLAT))

    def test_process_substitution(self):
        self.assertDenied("%s --key-file %s --body-file <(cat %s)" % (POST, FLAT, FLAT))
        self.assertDenied('%s --key-file %s --body "$(cat <(cat %s))"'
                          % (POST, FLAT, FLAT))

    def test_heredoc_in_substitution(self):
        self.assertDenied('%s --key-file %s --body "$(cat <<EOF\n$(cat %s)\nEOF\n)"'
                          % (POST, FLAT, FLAT))

    def test_xargs_and_eval(self):
        self.assertDenied('%s --key-file %s --body "$(echo %s | xargs cat)"'
                          % (POST, FLAT, FLAT))
        self.assertDenied('eval "$(cat %s)"' % FLAT)
        self.assertDenied('eval "%s --key-file %s --body $(cat /tmp/m)"' % (POST, FLAT))

    def test_substitution_spliced_into_a_key_path(self):
        self.assertDenied('cat "%s/$(echo k)"' % R)
        self.assertDenied("cat %s$(true)" % FLAT)

    def test_substitution_computing_the_key_file_value(self):
        self.assertDenied('%s --key-file "$(cat %s/path)"' % (POST, R))

    def test_single_quoted_substitution_is_literal_text(self):
        self.assertDenied("%s --key-file %s --body '$(cat %s)'" % (POST, FLAT, FLAT))

    def test_store_read_inside_substitution(self):
        self.assertDenied('ls "$(cat %s)"' % STORE)
        self.assertDenied('%s --key-file %s --body "$(cat %s)"' % (POST, FLAT, STORE))


class Segments(unittest.TestCase):
    """The splitter keeps the OUTER command whole and judges each body alone."""

    def test_outer_consumer_is_one_balanced_segment(self):
        segs = split_segments('%s --key-file K --body "$(cat /tmp/m.html)" --x y'
                              % POST)
        outer = [s for s, _t in segs if s.startswith(POST)]
        self.assertEqual(len(outer), 1, segs)
        self.assertEqual(outer[0].count('"') % 2, 0, segs)
        self.assertIn("--x y", outer[0])
        self.assertIn(("cat /tmp/m.html", ")"), segs)

    def test_backtick_body_keeps_its_closer(self):
        segs = split_segments("echo `cat /tmp/x` done")
        self.assertIn(("cat /tmp/x", "`"), segs)
        self.assertTrue(any(s.startswith("echo ") and "done" in s for s, _t in segs))

    def test_unmatched_substitution_keeps_the_old_cut(self):
        segs = split_segments('echo "$(cat /tmp/x"')
        self.assertEqual(segs[0], ('echo "', "$("))


if __name__ == "__main__":
    unittest.main()
