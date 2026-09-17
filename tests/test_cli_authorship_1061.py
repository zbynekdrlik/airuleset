"""#1061 item 1 -- cli_authorship: read the invoking session's OWN model from
its transcript (never a self-declared string) + its main/worker role from the
cwd, for truthful `Design-by:`/`Reviewed-by:` stamps.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_authorship  # noqa: E402
from watchdog.transcripts import encode_project_dir  # noqa: E402


def _write_transcript(dir_path, model, text="hi there"):
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / "sess.jsonl"
    lines = [
        {"type": "user", "message": {"role": "user", "content": "go"}},
        {"type": "assistant",
         "message": {"role": "assistant", "model": model,
                     "content": [{"type": "text", "text": text}]}},
    ]
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return p


class TestAuthorshipRole(unittest.TestCase):
    def test_worktree_cwd_is_worker(self):
        cwd = "/home/airuleset/devel/airuleset/.claude/worktrees/agent-xyz"
        self.assertEqual(cli_authorship.authorship_role(cwd), "worker")

    def test_main_checkout_cwd_is_main(self):
        cwd = "/home/airuleset/devel/airuleset"
        self.assertEqual(cli_authorship.authorship_role(cwd), "main")

    def test_empty_cwd_defaults_main(self):
        self.assertEqual(cli_authorship.authorship_role(""), "main")


class TestSessionModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pd = Path(self.tmp) / "projects"

    def test_reads_fable_from_main_cwd_transcript(self):
        cwd = "/home/airuleset/devel/airuleset"
        _write_transcript(self.pd / encode_project_dir(cwd), "claude-fable-5-1")
        self.assertEqual(
            cli_authorship.session_model(cwd, projects_dir=str(self.pd)),
            "claude-fable-5-1")

    def test_reads_opus_from_transcript(self):
        cwd = "/home/x/proj"
        _write_transcript(self.pd / encode_project_dir(cwd), "claude-opus-4-8")
        self.assertEqual(
            cli_authorship.session_model(cwd, projects_dir=str(self.pd)),
            "claude-opus-4-8")

    def test_missing_transcript_is_unknown(self):
        self.assertEqual(
            cli_authorship.session_model("/no/such/cwd",
                                         projects_dir=str(self.pd)),
            cli_authorship.UNKNOWN_MODEL)

    def test_worktree_cwd_reads_nested_subagent_transcript(self):
        # A worktree cwd has no top-level project dir; its transcript is nested
        # under the SUPERVISOR's session dir at
        #   <projects>/<enc-main>/<sess>/subagents/<worktree-basename>.jsonl
        main = "/home/airuleset/devel/airuleset"
        wt = main + "/.claude/worktrees/agent-a9aaf809f1ac9e3d4"
        sub_dir = (self.pd / encode_project_dir(main) / "2d02a127" / "subagents")
        sub_dir.mkdir(parents=True, exist_ok=True)
        p = sub_dir / "agent-a9aaf809f1ac9e3d4.jsonl"
        p.write_text(json.dumps(
            {"type": "assistant",
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [{"type": "text", "text": "work"}]}}) + "\n")
        self.assertEqual(
            cli_authorship.session_model(wt, projects_dir=str(self.pd)),
            "claude-opus-4-8")


class TestStampLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pd = Path(self.tmp) / "projects"

    def test_design_by_main_fable(self):
        cwd = "/home/airuleset/devel/airuleset"
        _write_transcript(self.pd / encode_project_dir(cwd), "claude-fable-5-1")
        self.assertEqual(
            cli_authorship.stamp_line("Design", cwd, projects_dir=str(self.pd)),
            "Design-by: main claude-fable-5-1")

    def test_reviewed_by_worker_when_lane(self):
        main = "/home/airuleset/devel/airuleset"
        wt = main + "/.claude/worktrees/agent-a9aaf809f1ac9e3d4"
        sub_dir = (self.pd / encode_project_dir(main) / "sess" / "subagents")
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "agent-a9aaf809f1ac9e3d4.jsonl").write_text(json.dumps(
            {"type": "assistant",
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [{"type": "text", "text": "work"}]}}) + "\n")
        self.assertEqual(
            cli_authorship.stamp_line("Reviewed", wt, projects_dir=str(self.pd)),
            "Reviewed-by: worker claude-opus-4-8")

    def test_unknown_model_still_labels_role(self):
        cwd = "/home/airuleset/devel/airuleset"  # main role, no transcript
        line = cli_authorship.stamp_line("Design", cwd, projects_dir=str(self.pd))
        self.assertEqual(line, "Design-by: main unknown")

    def test_authorship_value_is_role_and_model(self):
        cwd = "/home/airuleset/devel/airuleset"
        _write_transcript(self.pd / encode_project_dir(cwd), "claude-fable-5-1")
        self.assertEqual(
            cli_authorship.authorship_value(cwd, projects_dir=str(self.pd)),
            "main claude-fable-5-1")


if __name__ == "__main__":
    unittest.main()
