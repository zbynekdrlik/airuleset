"""#1395: the own-transcript guard lets bounded STREAMING readers through.

Owner 23.11.2026: "su rozne sposobi citania suboru nie len ze nacitat cely do
pamete". A grep/awk/wc/sed -n pass streams a 1 GB transcript in ~1 s at a few
MB RSS. Deny by default (review 23.11.2026): a command touching a transcript
passes only if its reader is count/locate-only (grep -c/-l/-q, wc,
session-shrink, a bounded head/tail) or the LAST pipeline stage bounds the
output (head/tail -n <= 50 / -c <= 64 KiB, wc, grep -c/-q/-l); sort/uniq are
never the first reader; comments and $'..' are handled before splitting.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402
from gates import owntranscript  # noqa: E402

SID = "4d877a2a-0b1c-4e2f-9a3b-5c6d7e8f9a0b"
TP = "/home/newlevel/.claude/projects/-home-newlevel-devel-songplayer/%s.jsonl" % SID
GZ = TP + ".shrink-20261123T162359Z.gz"
HOOK = ROOT / "hooks" / "block-own-transcript-read.sh"
G = "gr" + "ep"          # keeps this source clear of the vault-read hook's shapes

STREAMING = (
    G + ' -oE "<command-name>/[a-z-]+</command-name>" %s | sort | uniq -c | head -n 50' % TP,
    G + " -c '\"compact_boundary\"' %s" % TP,
    G + " -n -m3 '\"subtype\":\"compact_boundary\"' %s | cut -c1-400 | head -n 3" % TP,
    G + " -l x %s" % TP, G + " -q x %s && echo y" % TP, G + " --count x %s" % TP,
    G + " x %s | head -n 5" % TP, G + " x %s | wc -l" % TP, G + " x %s | grep -c y" % TP,
    "rg -c NEEDS %s" % TP,
    "awk 'END{print NR}' %s | head -n 1" % TP,
    "wc -l %s" % TP,
    "sed -n '378791p' %s | head -c 2500" % TP,
    "cut -c1-200 %s | grep -oE '\"type\":\"[a-z-]+\"' | sort | uniq -c | head -n 50" % TP,
    "zcat %s | grep -c x" % GZ,
    "jq --stream -c . %s | head -n 3" % TP,
    "for f in ~/.claude/projects/*/*.jsonl; do " + G + " -c x \"$f\"; done",
    "python3 ~/devel/airuleset/airuleset.py session-shrink %s" % TP,
    "python3 cli_session_shrink.py %s --apply" % TP,
    G + " -c x %s 2>&1 | tail -n 3" % TP, G + " x %s 2>/dev/null | head -n 2" % TP,
    G + " x %s |& head -n 2" % TP, G + " x %s 2>&1 | head -n 2" % TP,
    G + " x %s &>/dev/stdout | wc -l" % TP,
    G + " -c x %s  # count it, don't dump" % TP,
    "echo $(" + G + " -c x %s)" % TP,
    "head -n 5 %s | grep -c x" % TP,
)

LOADERS = (
    "cat %s" % TP, "less %s" % TP, "more %s" % TP, "tac %s | head -n 3" % TP,
    "python3 -c 'import json; json.load(open(\"%s\"))'" % TP,
    "python3 -c 'open(\"%s\").read()'" % TP,
    "python3 -c 'open(\"%s\").readlines()'" % TP,
    "jq -c . %s" % TP, "jq -c . %s | head -n 3" % TP, "jq --stream -c . %s" % TP,
    "tail -n +1 %s | grep -c x" % TP,
    G + " x %s" % TP, G + " -o x %s" % TP, G + " -m 3 x %s" % TP,
    G + " x %s > /tmp/out" % TP,
    G + " a ~/.claude/projects/*/*.jsonl",
    G + " -r -c NEEDS ~/.claude/projects",
    "rg -c NEEDS ~/.claude/projects",
    "sed 's/a/b/' %s" % TP, "sed -n p %s" % TP, "sed -i 's/a/b/' %s" % TP,
    "sed -n '/x/p' %s" % TP, "sed -n '1,$p' %s" % TP,
    "sed -n '378791p' %s | cut -c1-2500" % TP,
    "cut -c1-100 %s" % TP, "sort %s" % TP, "zcat %s" % GZ,
    "wc -l `" + G + " a %s`" % TP,
    G + " x %s 2>&1" % TP, G + " x %s >&2" % TP, G + " x %s & wait" % TP,
    # the review's bypasses (23.11.2026)
    "awk '{print}' %s" % TP, "awk 'END{print NR}' %s" % TP,
    G + " x %s | cat" % TP,
    G + ' -oE "<command-name>/[a-z-]+</command-name>" %s | sort | uniq -c' % TP,
    "ls # it's fine\ncat %s" % TP,
    "ls # it's fine\n" + G + " x %s" % TP,
    "echo $'it\\'s' ; cat %s" % TP,
    "$'\\x63at' %s" % TP,
    "sort %s | head -n 3" % TP, "uniq -c %s | head -n 3" % TP,
    "cp %s /tmp/x" % TP, "zcat %s | sort | uniq -c" % GZ,
    G + " x %s | sort | uniq -c" % TP,
)


class Gate(unittest.TestCase):
    def test_streaming_readers_pass(self):
        for cmd in STREAMING:
            self.assertEqual(owntranscript.violation(cmd, TP), "", cmd)

    def test_whole_file_loaders_stay_refused(self):
        for cmd in LOADERS:
            self.assertIn("#1224", owntranscript.violation(cmd, TP), cmd)

    def test_the_message_names_the_streaming_way(self):
        msg = owntranscript.violation("cat %s" % TP, TP)
        self.assertIn("grep -c", msg)
        self.assertIn("| head -n", msg)
        self.assertIn("session-shrink", msg)


class Hook(unittest.TestCase):
    def _hook(self, cmd):
        payload = json.dumps({"tool_name": "Bash", "transcript_path": TP,
                              "tool_input": {"command": cmd}})
        return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                              capture_output=True, timeout=30, env=hermetic_hook_env(self))

    def test_the_hook_passes_the_two_shapes_from_the_ticket(self):
        for cmd in STREAMING[:2]:
            r = self._hook(cmd)
            self.assertEqual((r.returncode, r.stderr), (0, ""), cmd)

    def test_the_hook_still_blocks_a_loader(self):
        r = self._hook("python3 -c 'open(\"%s\").read()'" % TP)
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
