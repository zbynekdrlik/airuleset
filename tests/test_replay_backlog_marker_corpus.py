"""Locks scripts/replay_backlog_marker_corpus.py (#166, Acceptance bullet 2 --
corpus replay, bidirectional, mention-vs-use). Only the REPO corpus (git-
tracked files) is exercised here: it is reproducible on any clone/CI-less
box, unlike the LOCAL transcript corpus which is private, machine-local data
the script itself already treats as best-effort/optional (see its own
module docstring) -- a pytest test must never assume that data exists.
"""
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "replay_backlog_marker_corpus.py"


class TestReplayScriptOnThisRepoOwnFiles(TestCase):
    def _run(self, extra=()):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--projects-dir", "/nonexistent-for-test", *extra],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_runs_clean_and_reports_both_required_numbers(self):
        out = self._run()
        self.assertIn("=== REPO (git-tracked files) corpus ===", out)
        self.assertIn("no-longer-blocked (naive mention, careful correctly excludes):", out)
        self.assertIn("newly-blocked (careful flags something naive missed):", out)

    def test_never_newly_blocks_anything_on_this_repos_own_corpus(self):
        # careful is a strict subset of naive (TestNaiveVsCarefulDelta) --
        # a nonzero "newly-blocked" count would be a genuine classifier bug.
        out = self._run()
        self.assertIn("newly-blocked (careful flags something naive missed): 0", out)

    def test_finds_the_known_self_tripping_mentions_in_this_repo(self):
        # skills/autopilot/SKILL.md and tests/test_goal_backlog_proof.py both
        # genuinely mention the marker (backtick-wrapped prose / a Python
        # string-literal assignment) without ever emitting a real claim --
        # exactly the risk #166's issue comment named. A naive substring
        # scan would flag both; the careful classifier must not.
        out = self._run()
        self.assertIn("no-longer-blocked: skills/autopilot/SKILL.md", out)

    def test_absent_local_projects_dir_yields_an_empty_local_corpus_not_a_crash(self):
        out = self._run()
        self.assertIn("=== LOCAL (real Claude Code transcripts, best-effort) corpus ===", out)
        self.assertIn("total items: 0", out)

    def test_limit_is_honored_and_stays_deterministic(self):
        # Bounded, portable: just prove the flag is accepted and the run
        # still succeeds (exact count depends on git ls-files ordering,
        # which this test deliberately does not pin down).
        out = self._run(("--limit", "5"))
        self.assertIn("total items: 5", out)


if __name__ == "__main__":
    main()
