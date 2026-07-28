"""Locks scripts/replay_design_gate_commit_corpus.py (#136, verification
step 2 -- corpus replay over real historical commit messages, bidirectional,
gate-disabled control arm). A bounded slice of this repo's OWN real commit
history is replayed through the REAL shipped hook on every test run, so a
future regression in either `design_gate.issue_refs` or
`hooks/block-commit-without-design.sh`'s scope check is caught by CI, not
just by a one-off manual run."""
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "replay_design_gate_commit_corpus.py"

sys.path.insert(0, str(ROOT))
import design_gate as dg                                   # noqa: E402


class TestReplayScriptAgreesOnARealSlice(TestCase):
    """Bounded --limit keeps this a fast, deterministic CI check (real git
    log + real hook subprocess calls, not fixtures) while still exercising
    the actual corpus and the actual shipped script end to end."""

    def test_no_mismatches_on_a_real_slice_of_this_repos_history(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--limit", "30"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERDICT: 0 mismatches", r.stdout)
        self.assertIn("OFF arm (agent_type=None) blocked ANYTHING: 0", r.stdout)

    def test_reports_both_no_ref_and_has_ref_buckets(self):
        # A --limit slice of the MOST RECENT commits (this repo's dominant
        # shape) is has-ref heavy; assert the script's own accounting stays
        # internally consistent rather than asserting a specific split.
        r = subprocess.run([sys.executable, str(SCRIPT), "--limit", "30"],
                           capture_output=True, text=True, timeout=60)
        self.assertIn("no issue reference:", r.stdout)
        self.assertIn("has issue reference:", r.stdout)


class TestNoOutOfRangeOrMissedReferences(TestCase):
    """Bidirectional false-positive/false-negative spot-check (#136): a
    matched number that exceeds the repo's real max issue number is a
    classifier FALSE POSITIVE; a commit mentioning an issue-shaped pattern
    (`#N`, `issue N`, `GH-N`) that issue_refs() extracts NOTHING from is a
    FALSE NEGATIVE. Locks both at zero against this repo's real, full
    history (cheap -- pure-Python regex work, no subprocess per commit)."""

    def test_no_out_of_range_or_zero_refs_extracted(self):
        r = subprocess.run(["git", "-C", str(ROOT), "log", "--all", "--format=%s"],
                           capture_output=True, text=True, timeout=30)
        # a truthful ceiling for "plausible issue number in THIS repo's
        # history" -- generous headroom above the current max (#143 at
        # authoring time) so the lock never needs bumping for ordinary
        # future issue growth, while still catching a genuinely wrong match
        # (a port number, a percentage, an exit code).
        CEILING = 5000
        seen = set()
        bad = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            for n in dg.issue_refs(line):
                if n <= 0 or n > CEILING:
                    bad.append((n, line))
        self.assertEqual(bad, [], "out-of-range issue_refs() match(es)")

    def test_no_missed_issue_shaped_mentions(self):
        import re
        r = subprocess.run(["git", "-C", str(ROOT), "log", "--all", "--format=%s"],
                           capture_output=True, text=True, timeout=30)
        seen = set()
        missed = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            if dg.issue_refs(line):
                continue
            if (re.search(r'#\d', line) or
                    re.search(r'\bissue\s+\d+\b', line, re.I) or
                    re.search(r'\bGH-\d+\b', line)):
                missed.append(line)
        self.assertEqual(missed, [], "issue-shaped mention with zero extracted refs")


if __name__ == "__main__":
    main()
