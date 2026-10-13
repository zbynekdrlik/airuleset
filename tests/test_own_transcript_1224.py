"""#1224 asks 2-3.

Ask 2: the songplayer session froze dev1 for 19 min by loading its OWN 598 MB
transcript (`python3 -` 2.0 GB RSS + `grep -o 'Otázka — projekt …'` 622 MB) to
find the block of a question it had asked before. The block already lives in
`~/.claude/discord-questions.json`. So:
- `airuleset.py question-block` prints it;
- a PreToolUse guard refuses a content read of the session's own transcript
  (metadata and bounded head/tail stay allowed);
- the question gate's messages point at the command and carry the exact
  marker text.

Ask 3: a local mutation run on a workstation is capped (`--max-children 2`,
`nice -n 19 ionice -c3`); the diff gate belongs in CI.
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SID = "4d877a2a-0b1c-4e2f-9a3b-5c6d7e8f9a0b"
TP = "/home/newlevel/.claude/projects/-home-newlevel-devel-songplayer/%s.jsonl" % SID
HOOK = ROOT / "hooks" / "block-own-transcript-read.sh"


def _gate():
    from gates import owntranscript
    return owntranscript


def _hook(cmd, tp=TP):
    payload = json.dumps({"tool_name": "Bash", "transcript_path": tp,
                          "tool_input": {"command": cmd}})
    return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                          capture_output=True, timeout=30)


class TestGate(unittest.TestCase):

    def test_content_reads_of_the_own_transcript_are_refused(self):
        for cmd in ("grep -o 'Otázka — projekt songplayer[^\"]\\{0,1500\\}' %s" % TP,
                    "python3 - < %s" % TP,
                    "cat %s | grep NEEDS" % TP,
                    "jq -c . %s" % TP,
                    "cd ~/.claude/projects/x && python3 -c 'open(\"%s.jsonl\").read()'" % SID,
                    "env LC_ALL=C grep x %s" % TP):
            self.assertIn("question-block", _gate().violation(cmd, TP), cmd)

    def test_metadata_and_bounded_reads_pass(self):
        for cmd in ("ls -la %s" % TP, "stat -c %%s %s" % TP, "wc -c %s" % TP,
                    "du -h %s" % TP, "tail -c 4000 %s | python3 -c 'print(1)'" % TP,
                    "head -c 200 %s" % TP,
                    "grep x /home/newlevel/.claude/projects/k/other-session.jsonl",
                    "git status"):
            self.assertEqual(_gate().violation(cmd, TP), "", cmd)

    def test_a_marked_command_passes(self):
        cmd = "python3 repair.py %s  # airuleset:own-transcript-ok repairing a torn line" % TP
        self.assertEqual(_gate().violation(cmd, TP), "")

    def test_no_transcript_path_means_no_check(self):
        self.assertEqual(_gate().violation("grep x %s" % TP, ""), "")


class TestHook(unittest.TestCase):

    def test_the_hook_blocks_and_names_the_way_out(self):
        r = _hook("grep -c NEEDS %s" % TP)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset.py question-block", r.stderr)

    def test_the_hook_passes_everything_else(self):
        for cmd in ("ls %s" % TP, "pytest -q tests/", "echo hi"):
            self.assertEqual(_hook(cmd).returncode, 0, cmd)

    def test_the_hook_is_registered_for_bash(self):
        d = json.loads((ROOT / "settings" / "hooks.json").read_text())
        cmds = [h["command"] for e in d["hooks"]["PreToolUse"] if e["matcher"] == "Bash"
                for h in e["hooks"]]
        self.assertIn("bash ~/devel/airuleset/hooks/block-own-transcript-read.sh", cmds)


class TestQuestionBlock(unittest.TestCase):

    def _run(self, entries, *argv):
        import cli_question_block as qb
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "discord-questions.json")
            path.write_text(json.dumps(entries))
            out = io.StringIO()
            with redirect_stdout(out):
                rc = qb.main(list(argv) + ["--path", str(path)])
            return rc, out.getvalue()

    E = {"111": {"session": "s1", "cwd": "/w/a", "ts": 100, "block": "**Otázka — A old**",
                 "question": "A old"},
         "222": {"session": "s2", "cwd": "/w/a", "ts": 300, "block": "**Otázka — A new**\n\n• x",
                 "question": "A new"},
         "333": {"session": "s3", "cwd": "/w/b", "ts": 400, "block": "**Otázka — B**",
                 "question": "B"}}

    def test_the_newest_block_for_the_cwd(self):
        rc, out = self._run(self.E, "--cwd", "/w/a")
        self.assertEqual(rc, 0)
        self.assertIn("**Otázka — A new**\n\n• x", out)
        self.assertNotIn("A old", out)

    def test_a_session_filter(self):
        rc, out = self._run(self.E, "--cwd", "/w/a", "--session", "s1")
        self.assertIn("A old", out)

    def test_nothing_recorded_is_rc_1(self):
        rc, out = self._run(self.E, "--cwd", "/w/none")
        self.assertEqual(rc, 1)

    def test_it_is_a_subcommand(self):
        import airuleset
        self.assertIn("question-block", airuleset.SUBCOMMANDS)


class TestDoctrine(unittest.TestCase):

    def test_the_question_gate_points_at_the_command_and_the_exact_line(self):
        src = (ROOT / "hooks" / "stop-check-question-quality.sh").read_text()
        self.assertIn("airuleset.py question-block", src)
        self.assertIn("\\`❓ NEEDS YOU: ${KEYLINE}\\`", src)

    def test_local_mutation_runs_are_capped(self):
        s = (ROOT / "skills" / "mutation-testing" / "SKILL.md").read_text()
        self.assertIn("--max-children 2", s)
        self.assertIn("nice -n 19 ionice -c3", s)


if __name__ == "__main__":
    unittest.main()
