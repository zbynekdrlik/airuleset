"""#1153 slice 5 — a metadata head (`ls`, `stat`) may pipe into text filters.

Live papercut (design issuecomment-5836170974): `ls <root>/ | grep -iE
"eleven|gemini"` was refused, while the unpiped `ls <root>/` was allowed. A
metadata head prints file NAMES and metadata, never a value, but piped it is a
NAME source for whatever consumes it (`ls <root> | xargs cat` reads every
key). So `ls`/`stat` join the ONE inert-pipeline path of slices 2/3
(`pipeline_is_inert`) as sources: bare name, no assignment in front, piped
only into the existing text filters (INSPECT_SINKS), and into nothing else,
not even another source. `wc -c`, `sha256sum` and `test` stay unpiped. The
slice-4 per-pipeline split and its grouping/rebinding fallback are unchanged.

No test touches a real key file: every path is built from pieces and the hook
only ever sees command TEXT.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "block-vault-store-read.sh"

DOT = "." + "sec" + "rets"            # never a literal in this file's source
R = "~/" + DOT
KEY = R + "/cloudflare-newlevel"


def run(cmd):
    env = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/home/newlevel")}
    payload = {"tool_name": "Bash", "tool_input": {"command": cmd},
               "cwd": "/home/newlevel/devel/airuleset"}
    return subprocess.run(["/bin/bash", str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, check=False)


class Base(unittest.TestCase):
    def both(self, allowed, denied):
        for cmd in allowed:
            with self.subTest(allow=cmd):
                r = run(cmd)
                self.assertEqual(r.returncode, 0, "expected ALLOW for: %s\n%s%s"
                                 % (cmd, r.stdout, r.stderr))
        for cmd in denied:
            with self.subTest(deny=cmd):
                r = run(cmd)
                self.assertEqual(r.returncode, 2, "expected DENY for: %s\n%s%s"
                                 % (cmd, r.stdout, r.stderr))


class MetaSource(Base):
    def test_the_design_acceptance(self):
        self.both(
            allowed=["ls %s/ | grep x" % R,
                     "ls -la %s | head" % R,
                     "stat %s | grep Size" % KEY],
            denied=["ls %s | xargs cat" % R,
                    "ls %s | sh" % R,
                    'ls %s | while read f; do cat "$f"; done' % R])

    def test_the_live_shape_and_its_neighbours(self):
        self.both(
            allowed=['ls %s/ | grep -iE "eleven|gemini"' % R,
                     'cd /tmp && ls %s/ | grep -iE "eleven|gemini"' % R,
                     "ls -la %s/ | grep -v total | wc -l" % R,
                     "ls %s/ 2>/dev/null | sort | head -5" % R,
                     "ls -l %s 2>&1 | tail -3" % R,
                     "stat -c '%%s %%n' %s | cut -d' ' -f1" % KEY,
                     "stat %s/* | grep -E 'File|Size'" % R],
            denied=[])

    def test_a_meta_source_feeds_only_text_filters(self):
        # Every consumer that can turn a NAME into a read or an action.
        self.both(
            allowed=[],
            denied=["stat -c %%n %s/* | xargs cat" % R,
                    "ls -d %s/* | xargs -I{} head {}" % R,
                    "ls %s | bash" % R,
                    "ls %s | tee /tmp/names" % R,
                    "ls %s | awk '{print}'" % R,
                    "ls %s | sed -n p" % R,
                    "ls %s | python3 -c 'import sys; print(sys.stdin.read())'" % R,
                    "ls %s | while read f; do head \"$f\"; done" % R,
                    "for f in $(ls %s); do cat \"$f\"; done" % R])

    def test_no_second_source_downstream(self):
        # A prose command or another metadata head is a source, not a sink:
        # `git commit --pathspec-from-file=-` would take the listed names.
        self.both(
            allowed=[],
            denied=["ls -d %s/* | git commit --pathspec-from-file=- -m x" % R,
                    "ls %s | gh issue comment 1 -R o/r --body-file -" % R,
                    "ls %s | ls" % R,
                    "stat %s | stat -" % KEY])

    def test_only_a_bare_unassigned_ls_or_stat(self):
        self.both(
            allowed=[],
            denied=["/tmp/x/ls %s | grep x" % R,
                    "PATH=/tmp/x ls %s | grep x" % R,
                    "sudo ls %s | grep x" % R,
                    "wc -c %s | grep x" % KEY,
                    "sha256sum %s | cut -c1-8" % KEY,
                    "test -f %s | cat" % KEY])

    def test_grouping_and_rebinding_keep_the_whole_command_rule(self):
        self.both(
            allowed=[],
            denied=["{ ls %s; } | xargs cat" % R,
                    "( ls %s ) | xargs cat" % R,
                    "alias grep='xargs cat'; ls %s | grep x" % R,
                    "ls %s | grep x > >(xargs cat)" % R,
                    "ls %s | grep x | cat %s" % (R, KEY),
                    "ls %s | grep -f %s" % (R, KEY)])


if __name__ == "__main__":
    unittest.main()
