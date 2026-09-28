"""#1165 round 2 -- every dispatch/lane consumer reads tickets through ONE parser.

Round 1 moved the dispatch-prompt parser into `gates/ticketrefs.py` for the
design gate only. Two consumers still parsed ticket numbers their own way, so
a young repo (fohmixer, tickets 1-9) still slipped past them:

- `hooks/block-dispatch-over-wdrain.sh` matched the lane-overlap receipt only
  on an `issues? #N` line: the fleet's bare `Work issue 4` parsed to nothing
  (receipt check skipped = fail-open), and every `#N` on the line counted, so
  `rides PR #201` demanded a receipt for a PR (false block).
- `cli_lane_liveness._lane_ticket_numbers` required >= 2 digits, so a
  single-digit ticket's lane never carried its number and could never be
  classified `finished`.

And in the parser itself a `PR #201` on an earlier line took the lead line
away from the real `issue N` line (design gate fail-open: the PR is skipped,
the ticket is never design-checked). A `PR #N` / `pull #N` / `pull request #N`
reference is never a ticket, for every consumer.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designdispatch as dd  # noqa: E402
from gates import ticketrefs  # noqa: E402
import cli_lane_liveness as ll  # noqa: E402

HOOK = REPO / "hooks" / "block-dispatch-over-wdrain.sh"
FABLE = "claude-fable-5-1"

_ORIG_HOME = None
_TMP_HOME = None


def setUpModule():
    # dd._log writes ~/.claude/design-by-gate.log in-process -- keep it hermetic.
    global _ORIG_HOME, _TMP_HOME
    _ORIG_HOME = os.environ.get("HOME")
    _TMP_HOME = tempfile.mkdtemp(prefix="a1165r2-modhome-")
    os.environ["HOME"] = _TMP_HOME


def tearDownModule():
    if _ORIG_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _ORIG_HOME
    shutil.rmtree(_TMP_HOME, ignore_errors=True)


class TestParserPrRefsAreNeverTickets(unittest.TestCase):
    def test_pr_line_never_takes_the_lead(self):
        for first in ("Review PR #201 fixes first", "Rides pull #201.",
                      "Merge pull request #201 from x/dev", "See PR #7."):
            with self.subTest(first=first):
                self.assertEqual(
                    ticketrefs.issue_numbers(first + "\nWork issue 1165"), [1165])

    def test_pr_ref_on_the_lead_line_is_not_a_ticket(self):
        for prompt in ("Work issue 1165, rides PR #201",
                       "Work issue 1165 (pull #7 open)",
                       "Work issues #41 #43 (PRs #201, #202 open)",
                       "Work issue #4 - pull request #12 carries it"):
            with self.subTest(prompt=prompt):
                got = ticketrefs.issue_numbers(prompt)
                self.assertFalse({7, 12, 201, 202} & set(got), got)
                self.assertTrue(got, "the real ticket must still parse")

    def test_pr_only_prompt_names_no_ticket(self):
        self.assertEqual(ticketrefs.issue_numbers("Continue PR #201 in fohmixer"), [])

    def test_existing_hash_shapes_unchanged(self):
        self.assertEqual(ticketrefs.issue_numbers("Fix #4 and #7"), [4, 7])
        self.assertEqual(ticketrefs.issue_numbers("Work issue #1165 of o/r"), [1165])
        self.assertEqual(
            ticketrefs.issue_numbers("Work issues #41, #43 and #47"), [41, 43, 47])
        # a word merely STARTING with pr/pull is not a PR marker
        self.assertEqual(ticketrefs.issue_numbers("Work #45 (prod #46)"), [45, 46])


class TestDesignGateSeesPastAPrLead(unittest.TestCase):
    def test_pr_lead_no_longer_fail_opens_the_ticket(self):
        # pre-fix: lead = the PR line -> [201] -> skipped as a PR -> ALLOW with
        # issue 1165 never design-checked.
        payload = json.dumps({
            "tool_name": "Agent", "cwd": "/repo",
            "tool_input": {"subagent_type": "autopilot-worker",
                           "prompt": "Review PR #201 fixes first\nWork issue 1165"}})
        v, r = dd.evaluate(
            payload, fetch=lambda slug, n, cwd: {1165: ["no design"]}.get(n),
            resolve_slug=lambda cwd: "owner/repo",
            is_pr=lambda n, slug, cwd: (n == 201, None), fable_id=FABLE)
        self.assertEqual(v, "block")
        self.assertIn("#1165", r)


class TestWdrainReceiptUsesTheSharedParser(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="a1165r2-home-")
        self.cwd = tempfile.mkdtemp(prefix="a1165r2-cwd-")
        self.key = hashlib.sha1(self.cwd.encode()).hexdigest()[:12]

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.cwd, ignore_errors=True)

    def _seed(self, issues):
        d = Path(self.home) / ".claude" / "lane-overlap"
        d.mkdir(parents=True, exist_ok=True)
        (d / (self.key + ".json")).write_text(json.dumps(
            {"checked_issues": issues, "ts": time.time(),
             "verdict": "clear", "overlaps": []}))

    def _run(self, prompt):
        payload = json.dumps({
            "tool_name": "Agent", "cwd": self.cwd,
            "tool_input": {"subagent_type": "autopilot-worker", "prompt": prompt}})
        return subprocess.run(["bash", str(HOOK)], input=payload,
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "HOME": self.home})

    def test_bare_single_digit_issue_needs_a_receipt(self):
        r = self._run("Work issue 4 (S0 bootstrap) in fohmixer")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("overlap", r.stderr.lower())

    def test_bare_single_digit_issue_with_covering_receipt_allows(self):
        self._seed([4])
        r = self._run("Work issue 4 (S0 bootstrap) in fohmixer")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bare_issue_not_covered_blocks(self):
        self._seed([1060])
        r = self._run("Work airuleset issue 1061 now")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("1061", r.stderr)

    def test_pr_on_the_issue_line_is_not_demanded_in_the_receipt(self):
        self._seed([1165])
        for prompt in ("Work issue #1165, rides PR #201",
                       "Work issue #1165 (PR #7 open)"):
            with self.subTest(prompt=prompt):
                r = self._run(prompt)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_pr_on_an_earlier_line_does_not_hide_the_ticket(self):
        self._seed([201])
        r = self._run("Review PR #201 fixes first\nWork issue 1165")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("1165", r.stderr)


class _R:
    def __init__(self, out=""):
        self.returncode = 0
        self.stdout = out


class TestLaneLivenessSeesSingleDigitTickets(unittest.TestCase):
    def _nums(self, branch, subjects=""):
        return ll._lane_ticket_numbers("/repo", branch, lambda cmd: _R(subjects))

    def test_single_digit_branch_number(self):
        for branch, want in (("worktree-issue-4", 4), ("worktree-4", 4),
                             ("fix/4-foo", 4), ("fohmixer/7-bootstrap", 7)):
            with self.subTest(branch=branch):
                self.assertEqual(self._nums(branch), {want})

    def test_single_digit_subject_hash(self):
        self.assertEqual(self._nums("worktree-agent-ab12", "green(#4): fix it\n"), {4})

    def test_pr_merge_subject_is_not_a_ticket(self):
        self.assertEqual(
            self._nums("worktree-agent-ab12",
                       "Merge pull request #12 from x/dev\ngreen(#4): fix\n"), {4})

    def test_wide_numbers_unchanged(self):
        self.assertEqual(self._nums("montalu/7840-searchmore"), {7840})
        self.assertEqual(self._nums("worktree-issue-1234"), {1234})
        self.assertEqual(self._nums("diag/searchmore-x", "green(#2314): fix\n"), {2314})
        self.assertEqual(self._nums("worktree-agent-ab12", "no ticket here\n"), set())


if __name__ == "__main__":
    unittest.main()
