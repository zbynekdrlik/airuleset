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
sys.path.insert(0, str(ROOT / "tests"))

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402

SID = "4d877a2a-0b1c-4e2f-9a3b-5c6d7e8f9a0b"
TP = "/home/newlevel/.claude/projects/-home-newlevel-devel-songplayer/%s.jsonl" % SID
HOOK = ROOT / "hooks" / "block-own-transcript-read.sh"


def _gate():
    from gates import owntranscript
    return owntranscript


def _hook(testcase, cmd, tp=TP):
    payload = json.dumps({"tool_name": "Bash", "transcript_path": tp,
                          "tool_input": {"command": cmd}})
    return subprocess.run(["bash", str(HOOK)], input=payload, text=True,
                          capture_output=True, timeout=30,
                          env=hermetic_hook_env(testcase))


class TestGate(unittest.TestCase):

    def test_content_reads_of_the_own_transcript_are_refused(self):
        for cmd in ("grep -o 'Otázka — projekt songplayer[^\"]\\{0,1500\\}' %s" % TP,
                    "python3 - < %s" % TP,
                    "cat %s | grep NEEDS" % TP,
                    "jq -c . %s" % TP,
                    "cd ~/.claude/projects/x && python3 -c 'open(\"%s.jsonl\").read()'" % SID,
                    "env LC_ALL=C grep x %s" % TP,
                    "ls /tmp && cat %s" % TP, "ls /tmp || cat %s" % TP,
                    "sudo cat %s" % TP, "nice -n 19 cat %s" % TP, "timeout 5 cat %s" % TP,
                    "nohup cat %s" % TP, "time cat %s" % TP, "FOO=1 cat %s" % TP,
                    "ls $(cat %s)" % TP, "wc -l `grep a %s`" % TP,
                    "tail -n +1 %s" % TP, "tail -f %s" % TP, "head -n 99999999 %s" % TP,
                    "head -c 999999999 %s" % TP, "tail %s" % TP,
                    # any transcript under .claude/projects, by glob or recursion
                    "grep -r Otázka ~/.claude/projects/-home-x/",
                    "grep a ~/.claude/projects/*/*.jsonl",
                    "cd ~/.claude/projects/-home-x && grep foo *.jsonl",
                    "rg NEEDS ~/.claude/projects"):
            self.assertIn("question-block", _gate().violation(cmd, TP), cmd)

    def test_metadata_and_bounded_reads_pass(self):
        for cmd in ("ls -la %s" % TP, "stat -c %%s %s" % TP, "wc -c %s" % TP,
                    "du -h %s" % TP, "tail -c 4000 %s | python3 -c 'print(1)'" % TP,
                    "head -c 200 %s" % TP,
                    "git status", "nice -n 19 ls %s" % TP,
                    "grep a f | tail -c 100 %s" % TP, "ls %s && echo ok" % TP,
                    "python3 /tmp/claude-1000/-home-x/%s/scratchpad/x.py" % SID,
                    "cat /tmp/claude-1000/-home-x/%s/tasks/b1.output" % SID,
                    "grep a ~/.claude/projects/-home-x/%s/tool-results/hook-1.txt" % SID,
                    "echo %s" % SID, "git commit -m 'about %s'" % SID,
                    "tail -n 20 %s" % TP):
            self.assertEqual(_gate().violation(cmd, TP), "", cmd)

    def test_text_that_merely_names_a_transcript_passes(self):
        # live 2026-10-13: the shipped guard blocked this very ticket's evidence
        # comment (a heredoc .md quoting `grep … .jsonl` in backticks)
        for cmd in ("cat > /tmp/c.md <<'EOF'\n| `grep NEEDS %s` | 2 |\nEOF\n"
                    "gh issue comment 1 -F /tmp/c.md" % TP,
                    "gh issue comment 1 --body 'see %s'" % TP,
                    "git commit -m 'path ~/.claude/projects/x/a.jsonl'",
                    "echo %s" % TP, "printf '%%s\\n' ~/.claude/projects/*/*.jsonl",
                    "python3 - <<'EOF'\nprint('docs mention ~/.claude/projects')\nEOF"):
            self.assertEqual(_gate().violation(cmd, TP), "", cmd)

    def test_an_interpreter_heredoc_that_opens_a_transcript_is_refused(self):
        for cmd in ("python3 - <<'EOF'\nprint(len(open('%s').read()))\nEOF" % TP,
                    "bash <<EOF\ngrep a %s\nEOF" % TP):
            self.assertIn("question-block", _gate().violation(cmd, TP), cmd)

    def test_a_marked_command_passes(self):
        cmd = "python3 repair.py %s  # airuleset:own-transcript-ok repairing a torn line" % TP
        self.assertEqual(_gate().violation(cmd, TP), "")

    def test_no_transcript_path_checks_only_the_projects_tree(self):
        self.assertEqual(_gate().violation("grep x /tmp/a.jsonl", ""), "")
        self.assertIn("question-block", _gate().violation("grep x %s" % TP, ""))


class TestHook(unittest.TestCase):

    def test_the_hook_blocks_and_names_the_way_out(self):
        r = _hook(self, "grep -c NEEDS %s" % TP)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset.py question-block", r.stderr)

    def test_the_hook_passes_everything_else(self):
        for cmd in ("ls %s" % TP, "pytest -q tests/", "echo hi",
                    "cat %s  # airuleset:own-transcript-ok repair" % TP):
            r = _hook(self, cmd)
            self.assertEqual((r.returncode, r.stderr), (0, ""), cmd)

    def _shimmed(self, payload, with_jq=True):
        """Run the hook with a `python3` shim that records each call."""
        with tempfile.TemporaryDirectory() as d:
            b = Path(d, "bin")
            b.mkdir()
            (b / "python3").write_text("#!/bin/sh\n: > %s/called\nexit 0\n" % d)
            (b / "python3").chmod(0o755)
            tools = ["bash", "cat", "dirname", "env"] + (["jq"] if with_jq else [])
            for t in tools:
                src = subprocess.run(["bash", "-c", "command -v " + t],
                                     capture_output=True, text=True).stdout.strip()
                (b / t).symlink_to(src)
            r = subprocess.run([str(b / "bash"), str(HOOK)], input=payload, text=True,
                               capture_output=True, timeout=30,
                               env={"PATH": str(b), "HOME": d})       # own HOME (#1046)
            return r, Path(d, "called").exists()

    def test_python_starts_only_when_the_command_can_touch_a_transcript(self):
        for cmd, called in (("echo hi", False), ("cat %s" % TP, True),
                            ("grep -r a ~/.claude/projects", True)):
            r, ran = self._shimmed(json.dumps({"transcript_path": TP,
                                               "tool_input": {"command": cmd}}))
            self.assertEqual((r.returncode, ran), (0, called), cmd)

    def test_no_transcript_path_garbage_or_no_jq_fails_open(self):
        for payload, jq in ((json.dumps({"tool_input": {"command": "echo hi"}}), True),
                            ("not json at all", True),
                            (json.dumps({"transcript_path": TP,
                                         "tool_input": {"command": "cat %s" % TP}}), False)):
            r, _ = self._shimmed(payload, with_jq=jq)
            self.assertEqual(r.returncode, 0, payload)

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

    def test_the_real_cli_takes_its_options(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "q.json")
            path.write_text(json.dumps(self.E))
            r = subprocess.run([sys.executable, str(ROOT / "airuleset.py"), "question-block",
                                "--cwd", "/w/a/", "--session", "s2", "--path", str(path)],
                               capture_output=True, text=True, timeout=60,
                               env=hermetic_hook_env(self))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("A new", r.stdout)

    def test_the_ask_time_orders_and_the_question_is_the_fallback(self):
        e = {"1": {"cwd": "/w/a", "ts": 100, "asked": 50, "question": "older ask"},
             "2": {"cwd": "/w/a", "ts": 90, "asked": 90, "question": "newer ask"}}
        rc, out = self._run(e, "--cwd", "/w/a")
        self.assertEqual(out.strip(), "newer ask")


class TestDoctrine(unittest.TestCase):

    def test_the_question_gate_points_at_the_command_and_the_exact_line(self):
        lines = (ROOT / "hooks" / "stop-check-question-quality.sh").read_text().splitlines()
        (repoke,) = [ln for ln in lines if "Otázka už bola doručená" in ln]
        self.assertIn("\\`❓ NEEDS YOU: ${KEYLINE}\\`", repoke)
        self.assertIn("airuleset.py question-block", repoke)
        (allusion,) = [ln for ln in lines if "references an OLD question by allusion" in ln]
        self.assertIn("airuleset.py question-block", allusion)

    def test_local_mutation_runs_are_capped(self):
        s = (ROOT / "skills" / "mutation-testing" / "SKILL.md").read_text()
        i = s.index("#### A local run on a workstation")
        para = s[i:s.index("#### ", i + 5)]
        self.assertIn("--max-children 2", para)
        self.assertIn("nice -n 19 ionice -c3", para)


if __name__ == "__main__":
    unittest.main()
