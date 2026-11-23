"""#1395: the own-transcript guard lets STREAMING readers through.

Owner 23.11.2026: "su rozne sposobi citania suboru nie len ze nacitat cely do
pamete". A grep/awk/wc/sed -n pass streams a 1 GB transcript in ~1 s at a few
MB RSS, so it passes when its OUTPUT is bounded (a counting/matching flag, an
address, or a pipe onward). Whole-file loaders (cat/less, python json.load,
jq without --stream, tail -n +1) and recursive sweeps of `.claude/projects`
stay refused.
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
GZ = TP + ".pre-20261123T162359Z.jsonl.gz"
HOOK = ROOT / "hooks" / "block-own-transcript-read.sh"

STREAMING = (
    'grep -oE "<command-name>/[a-z-]+</command-name>" %s | sort | uniq -c' % TP,
    "grep -c '\"compact_boundary\"' %s" % TP,
    "grep -n -m3 '\"subtype\":\"compact_boundary\"' %s | cut -c1-400" % TP,
    "grep -o 'Otázka — projekt songplayer' %s" % TP,
    "grep -l x %s" % TP, "grep -q x %s && echo y" % TP, "grep --count x %s" % TP,
    "grep x %s | head -n 5" % TP, "grep x %s | wc -l" % TP,
    "rg -c NEEDS %s" % TP,
    "awk -F, 'NR==5{print length($0)}' %s" % TP,
    "awk 'END{print NR}' %s" % TP,
    "wc -l %s" % TP,
    "sed -n '378791p' %s | cut -c1-2500" % TP,
    "sed -n 10,20p %s" % TP, "sed -n '$p' %s | cut -c1-300" % TP,
    "cut -c1-200 %s | grep -oE '\"type\":\"[a-z-]+\"' | sort | uniq -c" % TP,
    "zcat %s | grep -c x" % GZ,
    "jq --stream -c . %s | head -n 3" % TP,
    "for f in ~/.claude/projects/*/*.jsonl; do grep -c x \"$f\"; done",
    "python3 ~/devel/airuleset/airuleset.py session-shrink %s" % TP,
    "python3 cli_session_shrink.py %s --apply" % TP,
)

LOADERS = (
    "cat %s" % TP, "less %s" % TP, "more %s" % TP, "tac %s | head -n 3" % TP,
    "python3 -c 'import json; json.load(open(\"%s\"))'" % TP,
    "python3 -c 'open(\"%s\").read()'" % TP,
    "python3 -c 'open(\"%s\").readlines()'" % TP,
    "jq -c . %s" % TP, "jq -c . %s | head -n 3" % TP,
    "tail -n +1 %s | grep -c x" % TP,
    "grep x %s" % TP,                                   # unbounded output
    "grep a ~/.claude/projects/*/*.jsonl",
    "grep -r -c NEEDS ~/.claude/projects",
    "rg -c NEEDS ~/.claude/projects",
    "sed 's/a/b/' %s" % TP, "sed -n p %s" % TP, "sed -i 's/a/b/' %s" % TP,
    "sed -n '/x/p' %s" % TP,
    "cut -c1-100 %s" % TP, "sort %s" % TP, "zcat %s" % GZ,
    "wc -l `grep a %s`" % TP,
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
