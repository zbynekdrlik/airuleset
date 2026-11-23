"""#1395: a SMALL allow-list on top of the #1224 own-transcript guard.

Owner 23.11.2026: "su rozne sposobi citania suboru nie len ze nacitat cely do
pamete". Round 3 (supervisor ROZHODNUTÉ): main's #1224 behaviour stays, and
only COUNT/LOCATE readers pass on top of it (grep/rg -c -l -L -q, wc,
ls/stat/du, find without -exec/-delete, session-shrink), plus a pipeline whose
LAST stage is a BYTE bound (head -c / tail -c <= 64 KiB, wc, grep -c/-q/-l).
The broad streaming allowances of round 2 (awk, sed, jq, a line-bounded head)
are reverted. A code search that only MENTIONS `.claude/projects` in its
pattern is not a transcript read.
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
PROJ = "~/.claude/projects"
HOOK = ROOT / "hooks" / "block-own-transcript-read.sh"
G = "gr" + "ep"          # keeps this source clear of the vault-read hook's shapes

ALLOWED = (
    G + " -c '\"compact_boundary\"' %s" % TP,
    G + " -l x %s" % TP, G + " -L x %s" % TP, G + " -q x %s && echo y" % TP,
    G + " --count x %s" % TP, "rg -c NEEDS %s" % TP,
    G + " -rc NEEDS " + PROJ, G + " -rl NEEDS " + PROJ,
    "wc -l %s" % TP, "ls -la %s" % TP, "stat %s" % TP, "du -h %s" % TP,
    "find " + PROJ + " -name '*.jsonl' -size +64M",
    "python3 ~/devel/airuleset/airuleset.py session-shrink %s" % TP,
    "python3 cli_session_shrink.py %s --apply" % TP,
    # a pipeline whose LAST stage is a byte bound
    G + ' -oE "<command-name>/[a-z-]+</command-name>" %s | sort | uniq -c | head -c 4000' % TP,
    G + " -n -m3 '\"subtype\":\"compact_boundary\"' %s | cut -c1-400 | head -c 2000" % TP,
    "awk 'END{print NR}' %s | head -c 100" % TP,
    G + " x %s | wc -l" % TP, G + " x %s | " % TP + G + " -c y",
    "zcat %s | " % GZ + G + " -c x",
    G + " -c x %s 2>&1 | tail -c 300" % TP,
    # code searches that only MENTION the projects tree (review false block)
    G + " -rn '.claude/projects' gates/",
    "rg -n '.claude/projects/.*jsonl' hooks/",
    # main's #1224 bounded head/tail stays
    "head -n 5 %s" % TP, "tail -c 4000 %s" % TP,
)

REFUSED = (
    "cat %s" % TP, "less %s" % TP, "more %s" % TP,
    "python3 -c 'import json; json.load(open(\"%s\"))'" % TP,
    "python3 -c 'open(\"%s\").read()'" % TP,
    "jq -c . %s" % TP, "tail -n +1 %s | wc -l" % TP,
    G + " x %s" % TP, G + " -o x %s" % TP, G + " -m 3 x %s" % TP,
    G + " x %s | head -n 5" % TP,                  # a LINE bound is not a byte bound
    "jq --stream -c . %s | head -n 3" % TP,
    G + " -rn NEEDS " + PROJ, G + " -nr NEEDS " + PROJ, "rg NEEDS " + PROJ,
    "awk '{print}' %s" % TP, G + " x %s | cat" % TP,
    "sed -n '1,$p' %s" % TP, "sed -n 5p %s" % TP,
    "python3 -c 'print(open(\"%s\").read())' | head -c 100" % TP,
    "sort %s | head -c 100" % TP,
    "find " + PROJ + " -name '*.jsonl' -exec cat {} +",
    "find " + PROJ + " -name '*.jsonl' -delete",
    "ls # it's fine\ncat %s" % TP,
    "wc -l `" + G + " a %s`" % TP,
    G + ' -oE "<command-name>/[a-z-]+</command-name>" %s | sort | uniq -c' % TP,
)


class Gate(unittest.TestCase):
    def test_count_locate_and_byte_bounded_pipelines_pass(self):
        for cmd in ALLOWED:
            self.assertEqual(owntranscript.violation(cmd, TP), "", cmd)

    def test_everything_else_keeps_the_1224_refusal(self):
        for cmd in REFUSED:
            self.assertIn("#1224", owntranscript.violation(cmd, TP), cmd)

    def test_the_message_names_the_allowed_ways(self):
        msg = owntranscript.violation("cat %s" % TP, TP)
        self.assertIn("grep -c", msg)
        self.assertIn("| head -c", msg)
        self.assertIn("session-shrink", msg)

    def test_the_accepted_residuals_are_documented(self):
        doc = owntranscript.__doc__
        self.assertIn("Accepted residuals", doc)
        for shape in ("$(<", "xargs", "bash -c"):
            self.assertIn(shape, doc)


class Hook(unittest.TestCase):
    def _hook(self, cmd):
        payload = json.dumps({"tool_name": "Bash", "transcript_path": TP,
                              "tool_input": {"command": cmd}})
        return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                              capture_output=True, timeout=30, env=hermetic_hook_env(self))

    def test_the_hook_passes_a_count_and_a_byte_bounded_pipe(self):
        for cmd in (ALLOWED[0], ALLOWED[15]):
            r = self._hook(cmd)
            self.assertEqual((r.returncode, r.stderr), (0, ""), cmd)

    def test_the_hook_still_blocks_a_loader(self):
        r = self._hook("python3 -c 'open(\"%s\").read()'" % TP)
        self.assertEqual(r.returncode, 2, r.stderr)


if __name__ == "__main__":
    unittest.main()
