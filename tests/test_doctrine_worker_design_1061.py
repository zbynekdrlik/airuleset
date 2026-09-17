"""#1061 item 5 -- cli_doctrine_audit.scan_worker_design_authoring flags any
agent/skill text that instructs a WORKER to author a design (owner #871: design
by the Fable main). Reads 0 on the real repo after the fix; has teeth on the
old anti-pattern.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import cli_doctrine_audit as da  # noqa: E402


class TestScanOnRealRepo(unittest.TestCase):
    def test_real_repo_has_zero_worker_design_findings(self):
        findings = da.scan_worker_design_authoring(str(REPO))
        self.assertEqual(
            findings, [],
            "the worker template / skills must not instruct a worker to author "
            "a design (#871/#1061): %r" % findings)


class TestScanHasTeeth(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "agents"))
        os.makedirs(os.path.join(self.tmp, "skills", "autopilot"))

    def _write_agent(self, body):
        p = os.path.join(self.tmp, "agents", "autopilot-worker.md")
        Path(p).write_text(body, encoding="utf-8")

    def _write_skill(self, body):
        p = os.path.join(self.tmp, "skills", "autopilot", "SKILL.md")
        Path(p).write_text(body, encoding="utf-8")

    def test_flags_worker_authoring_in_agent_file(self):
        self._write_agent(
            "2. The worker writes the final Triage: line + Architektúra: "
            "section itself before its first commit.\n")
        f = da.scan_worker_design_authoring(self.tmp)
        self.assertEqual(len(f), 1, f)
        self.assertIn("agents/autopilot-worker.md", f[0]["path"])

    def test_flags_worker_posts_design_comment(self):
        self._write_agent(
            "The worker posts the design comment itself — Triage, approaches — "
            "before implementing.\n")
        self.assertEqual(len(da.scan_worker_design_authoring(self.tmp)), 1)

    def test_negation_is_not_flagged(self):
        self._write_agent(
            "**NEVER author the design** — you do NOT write a Triage: / "
            "Architektúra: section yourself; READ the main's Design-by: main "
            "comment and confirm anchors.\n")
        self.assertEqual(da.scan_worker_design_authoring(self.tmp), [])

    def test_main_authoring_in_skill_not_flagged(self):
        self._write_skill(
            "YOU (the Fable MAIN) author the design comment yourself and post "
            "it with design-record — Triage: + Architektúra: — before dispatch.\n")
        self.assertEqual(da.scan_worker_design_authoring(self.tmp), [])

    def test_skill_flags_only_when_worker_named(self):
        # a skill line telling the WORKER to author a design IS flagged
        self._write_skill(
            "The worker composes the 2-3 considered approaches + Architektúra "
            "section in its own design comment.\n")
        f = da.scan_worker_design_authoring(self.tmp)
        self.assertEqual(len(f), 1, f)


class TestCliFlag(unittest.TestCase):
    def test_cli_worker_design_zero(self):
        import subprocess
        r = subprocess.run(
            ["python3", str(REPO / "airuleset.py"), "doctrine-audit",
             "--worker-design"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0 findings", r.stdout)


if __name__ == "__main__":
    unittest.main()
