"""#1061 item 1 -- `airuleset.py design-record`: compose + post a design comment
stamped `Design-by: <role> <model>` (transcript-derived), validate the design
shape, and write the local design marker so the worker's commit gate passes.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_design_record as dr  # noqa: E402
from gates import design as dg  # noqa: E402
from watchdog.transcripts import encode_project_dir  # noqa: E402


VALID_DESIGN = """Triage: trivial -- a one-line scoped fix.

Root cause: the widget crashes because the config key was renamed and the old
reader still looks it up (traced in reader.py:42). Chosen approach: read the new
key with a fallback. Rejected alternative: a migration shim -- overkill for one
key, instead of a two-line fallback.

Shared-benefit: single-client -- only the MIVA widget uses this key.
"""


def _write_transcript(pd, cwd, model):
    d = pd / encode_project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    (d / "s.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"type": "assistant",
         "message": {"role": "assistant", "model": model,
                     "content": [{"type": "text", "text": "hello"}]}}]) + "\n")


class TestComposeBody(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.pd = Path(self.tmp) / "projects"
        self.cwd = "/home/airuleset/devel/airuleset"
        _write_transcript(self.pd, self.cwd, "claude-fable-5-1")

    def test_appends_design_by_main(self):
        out = dr.compose_body("some design body", self.cwd,
                              projects_dir=str(self.pd))
        self.assertIn("Design-by: main claude-fable-5-1", out)
        self.assertTrue(out.rstrip().endswith("Design-by: main claude-fable-5-1"))

    def test_idempotent_replaces_existing_stamp(self):
        raw = "body text\n\nDesign-by: worker claude-opus-4-8\n"
        out = dr.compose_body(raw, self.cwd, projects_dir=str(self.pd))
        self.assertEqual(out.count("Design-by:"), 1)
        self.assertIn("Design-by: main claude-fable-5-1", out)
        self.assertNotIn("worker claude-opus-4-8", out)


class TestValidateBody(unittest.TestCase):
    def test_valid_design_passes(self):
        ok, reasons = dr.validate_body(VALID_DESIGN)
        self.assertTrue(ok, reasons)

    def test_missing_shared_benefit_fails(self):
        body = VALID_DESIGN.replace(
            "Shared-benefit: single-client -- only the MIVA widget uses this key.",
            "")
        ok, reasons = dr.validate_body(body)
        self.assertFalse(ok)
        self.assertIn("Shared-benefit", " ".join(reasons))

    def test_missing_triage_fails(self):
        body = VALID_DESIGN.replace("Triage: trivial -- a one-line scoped fix.",
                                    "")
        ok, reasons = dr.validate_body(body)
        self.assertFalse(ok)


class TestPostAndRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.pd = Path(self.tmp) / "projects"
        self.cwd = "/home/airuleset/devel/airuleset"
        _write_transcript(self.pd, self.cwd, "claude-fable-5-1")
        self._orig_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        if self._orig_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._orig_home

    def test_posts_and_writes_marker(self):
        posted = {}

        def fake_runner(argv, body):
            posted["argv"] = argv
            posted["body"] = body
            return (0, "https://github.com/zbynekdrlik/airuleset/issues/9#c1", "")

        ok, url, stamp = dr.post_and_record(
            issue=9, repo="zbynekdrlik/airuleset", raw_body=VALID_DESIGN,
            cwd=self.cwd, runner=fake_runner, projects_dir=str(self.pd),
            home=self.home)
        self.assertTrue(ok, (url, stamp))
        self.assertIn("Design-by: main claude-fable-5-1", posted["body"])
        # marker written under the repo key -> worker commit gate will pass.
        self.assertTrue(dg.marker_exists("airuleset", 9, "design"),
                        "design marker must be written after a successful post")

    def test_invalid_design_refuses_and_does_not_post(self):
        posted = {}

        def fake_runner(argv, body):
            posted["called"] = True
            return (0, "url", "")

        ok, reason, _ = dr.post_and_record(
            issue=10, repo="zbynekdrlik/airuleset",
            raw_body="not a design at all", cwd=self.cwd,
            runner=fake_runner, projects_dir=str(self.pd), home=self.home)
        self.assertFalse(ok)
        self.assertNotIn("called", posted,
                         "must not post an invalid design")
        self.assertFalse(dg.marker_exists("airuleset", 10, "design"))

    def test_post_failure_does_not_write_marker(self):
        # #1061 review MUT3: the marker must be written ONLY after a SUCCESSFUL
        # post — a gh failure (rc != 0) must leave no marker (else a worker's
        # commit gate would pass on a design that never actually landed).
        def failing_runner(argv, body):
            return (1, "", "boom: gh failed")

        ok, reason, stamp = dr.post_and_record(
            issue=12, repo="zbynekdrlik/airuleset", raw_body=VALID_DESIGN,
            cwd=self.cwd, runner=failing_runner, projects_dir=str(self.pd),
            home=self.home)
        self.assertFalse(ok)
        self.assertIsNone(stamp)
        self.assertFalse(dg.marker_exists("airuleset", 12, "design"),
                         "no marker may be written when the post failed")

    def test_worker_cwd_stamps_worker_not_main(self):
        wt = self.cwd + "/.claude/worktrees/agent-zzz"
        sub = (self.pd / encode_project_dir(self.cwd) / "sess" / "subagents")
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-zzz.jsonl").write_text(json.dumps(
            {"type": "assistant",
             "message": {"role": "assistant", "model": "claude-opus-4-8",
                         "content": [{"type": "text", "text": "x"}]}}) + "\n")
        posted = {}

        def fake_runner(argv, body):
            posted["body"] = body
            return (0, "url#c", "")

        ok, url, stamp = dr.post_and_record(
            issue=11, repo="zbynekdrlik/airuleset", raw_body=VALID_DESIGN,
            cwd=wt, runner=fake_runner, projects_dir=str(self.pd),
            home=self.home)
        self.assertTrue(ok, (url, stamp))
        self.assertIn("Design-by: worker claude-opus-4-8", posted["body"])


if __name__ == "__main__":
    unittest.main()
