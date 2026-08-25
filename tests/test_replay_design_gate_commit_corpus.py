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
    classifier FALSE POSITIVE; a commit mentioning a `#N`-shaped pattern
    that issue_refs() extracts NOTHING from is a FALSE NEGATIVE. Locks both
    at zero against this repo's real, full history (cheap -- pure-Python
    regex work, no subprocess per commit).

    #122 (2026-07-29) narrowed the FALSE-NEGATIVE half's own definition of
    "issue-shaped" from `#N` / `issue N` / `GH-N` down to `#N` alone.
    `design_gate.ISSUE_REF_RE` is deliberately `#`-anchored (see its own
    comment in design_gate.py) -- and the #137-era playbook convention
    (`.claude/rules/airuleset-internals.md`) established "issue N" prose as
    the SANCTIONED way to mention a historical/context ticket in a commit
    message WITHOUT triggering block-commit-without-design.sh's marker
    requirement for it. Flagging "issue N" here as a missed reference
    directly contradicts that convention -- the first real commit to use
    the sanctioned phrasing (issue #122's own playbook entry) failed this
    test for using it correctly, not for a genuine classifier gap. See
    `TestIssueRefs.test_prose_issue_n_is_deliberately_not_a_ref` /
    `test_gh_dash_n_is_deliberately_not_a_ref` (test_design_gate.py) for the
    same decision locked at the unit level."""

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
            # #122 -- only the SAME `#`-anchored family ISSUE_REF_RE itself
            # targets; "issue N" / "GH-N" are the sanctioned non-triggering
            # prose forms (see the class docstring) and must never land
            # back in this check.
            # #692 -- and only the SAME 1..5-digit run ISSUE_REF_RE itself
            # caps: a 6+-digit all-digit token (`#333333`, an all-digit CSS
            # hex colour — now sanctioned in commit messages) is a
            # DELIBERATE exclusion, not a missed reference; without this
            # lockstep narrowing the first legitimate post-#692 subject
            # containing such a colour and no other ref would fail this
            # lock — the same audit-definition narrowing #122 itself did.
            if re.search(r'#\d{1,5}(?!\d)', line):
                missed.append(line)
        self.assertEqual(missed, [], "issue-shaped mention with zero extracted refs")


if __name__ == "__main__":
    main()
