"""tests/test_epic_rehearsal_review_1161.py -- #1161 part 2, the fresh-context
review findings on the epic-rehearsal gate and the post-release metric.

1. The compose path and the --body-file path are gated BEHAVIOURALLY (not only
   by a source count): enforce refuses with no receipt and no post, a passing
   rehearsal writes the epic fields into the receipt, and --body-file measures
   freshness against the remote branch tip.
2. A comment that merely carries the `Epic-rehearsal:` marker (a gk instruction,
   a run with a FAIL row) is not a passing rehearsal.
3. --sign-only measures freshness against the body's `HEAD:` line.
4. Parser gaps (headings, numbered items, link refs, ~~~ fences, cross-repo refs,
   quoted listings) and metric false positives are closed.
Every gh/git read goes through the ghread seam; nothing reaches real gh.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mk

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tests"))

import airuleset  # noqa: E402
import cli_handoff_template  # noqa: E402
import cli_post_release as pr  # noqa: E402
from gates import epic_rehearsal as er  # noqa: E402
from gates import ghread  # noqa: E402
from test_epic_rehearsal_1161 import (  # noqa: E402
    ENFORCE, EPIC, HEAD, NEWER, SLUG, TICKET, WARN, _comment, _rehearsal,
    _runner)
from test_post_release_1161 import NOW, _Seam, _issue, _population, com, lab  # noqa: E402

TABLE = "| lens | verdict | evidence |\n|---|---|---|\n" + "".join(
    "| %s | pass | a.py:1 |\n" % lens for lens in airuleset.HANDOFF_DEFAULT_LENSES)


class _FakeProc:
    """subprocess.run stand-in for the composer's own git/gh calls."""

    def __init__(self, branch):
        self.branch, self.posted = branch, []

    def __call__(self, argv, *a, **k):
        import subprocess
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, HEAD + "\n", "")
        if argv[:2] == ["git", "ls-remote"]:
            return subprocess.CompletedProcess(
                argv, 0, "%s\trefs/heads/%s\n" % (HEAD, self.branch), "")
        if argv[:3] == ["gh", "issue", "comment"]:
            self.posted.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


def _gated(td, env, comments, proc):
    """The shared patch set: gate dir/log into `td`, every sibling pre-flight
    neutral, a reduced-authority box, the ghread seam answering the epic."""
    home = os.path.expanduser("~")
    seam = _runner("Epic: #%d\n" % EPIC, comments)
    stack = contextlib.ExitStack()
    for target, kw in (
            ("HANDOFF_GATE_DIR", {"new": os.path.relpath(os.path.join(td, "g"), home)}),
            ("HANDOFF_GATE_LOG", {"new": os.path.relpath(os.path.join(td, "l"), home)}),
            ("_bounce_round", {"return_value": 1}),
            ("_stream_self_login", {"return_value": "x"}),
            ("_repo_root", {"return_value": _REPO}),
            ("gk_watch_issue", {"return_value": {"state": "no-gk-comment"}}),
            ("_handoff_guide_preflight", {"return_value": None}),
            ("_handoff_agent_eval_preflight", {"return_value": None}),
            ("_handoff_spec_preflight", {"return_value": None}),
            ("_handoff_prod_transfer_preflight", {"return_value": None})):
        stack.enter_context(mk.patch.object(airuleset, target, **kw))
    stack.enter_context(mk.patch("cli_quals.resolve_authority",
                                 return_value="fork-no-merge"))
    stack.enter_context(mk.patch.object(ghread, "_run",
                                        lambda argv, *a, **k: seam(argv)))
    stack.enter_context(mk.patch("subprocess.run", proc))
    stack.enter_context(mk.patch.dict(os.environ, env))
    return stack, seam


def _receipts(td):
    gate = os.path.join(td, "g")
    if not os.path.isdir(gate):
        return []
    out = []
    for fn in sorted(os.listdir(gate)):
        if fn.endswith(".json"):
            with open(os.path.join(gate, fn)) as f:
                out.append(json.load(f))
    return out


class TestComposePath(unittest.TestCase):
    def _compose(self, env, comments):
        proc = _FakeProc("lane-x")
        with tempfile.TemporaryDirectory() as td:
            table = os.path.join(td, "table.md")
            with open(table, "w") as f:
                f.write(TABLE)
            args = argparse.Namespace(
                repo=SLUG, issue=TICKET, branch="lane-x", self_review_file=table,
                self_review_model="claude-opus-4-8", root_cause=None,
                closes_finding=None, prevencia_read=None, sign_only=None,
                body_file=None, gate_dry_run=None)
            stack, seam = _gated(td, env, comments, proc)
            with stack, mk.patch.object(
                    cli_handoff_template, "compose_body",
                    return_value=("READY-FOR-REVIEW: lane-x\n", None)), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = airuleset.cmd_handoff(args)
            return rc, out.getvalue(), _receipts(td), proc.posted, seam

    def test_enforce_refuses_before_receipt_and_post(self):
        rc, out, receipts, posted, _ = self._compose(ENFORCE, [])
        self.assertEqual(rc, 1, out)
        self.assertIn("has no `Epic-rehearsal:` comment", out)
        self.assertEqual((receipts, posted), ([], []))

    def test_pass_records_epic_fields_and_posts(self):
        rc, out, receipts, posted, seam = self._compose(
            ENFORCE, [_comment(99, NEWER, _rehearsal())])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(posted), 1)
        self.assertEqual((receipts[0]["epic"], receipts[0]["epic_rehearsal"],
                          receipts[0]["epic_gate"]), (EPIC, 99, "pass"))
        self.assertIn(["git", "log", "-1", "--format=%ct", HEAD], seam.calls)

    def test_warn_posts_and_receipt_says_warn(self):
        rc, out, receipts, posted, _ = self._compose(WARN, [])
        self.assertEqual(rc, 0, out)
        self.assertIn("handoff WARN", out)
        self.assertEqual(receipts[0]["epic_gate"], "warn")
        self.assertEqual(len(posted), 1)


class TestBodyFilePath(unittest.TestCase):
    def _post(self, env, comments):
        proc = _FakeProc("lane-x")
        with tempfile.TemporaryDirectory() as td:
            body = os.path.join(td, "body.md")
            with open(body, "w") as f:
                f.write("READY-FOR-REVIEW: lane-x\nHEAD: %s\n" % HEAD[:12])
            stack, seam = _gated(td, env, comments, proc)
            with stack, mk.patch.object(cli_handoff_template,
                                        "validate_passthrough_body",
                                        return_value=None), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = airuleset._cmd_handoff_post_body_file(SLUG, TICKET, "lane-x",
                                                           body)
            return rc, out.getvalue(), _receipts(td), proc.posted, seam

    def test_enforce_refuses_before_receipt_and_post(self):
        rc, out, receipts, posted, _ = self._post(ENFORCE, [])
        self.assertEqual(rc, 1, out)
        self.assertIn("has no `Epic-rehearsal:` comment", out)
        self.assertEqual((receipts, posted), ([], []))

    def test_pass_uses_the_remote_tip_and_records_the_epic(self):
        rc, out, receipts, posted, seam = self._post(
            ENFORCE, [_comment(98, NEWER, _rehearsal())])
        self.assertEqual(rc, 0, out)
        self.assertIn(["git", "log", "-1", "--format=%ct", HEAD], seam.calls)
        self.assertEqual((receipts[0]["epic"], receipts[0]["epic_rehearsal"]),
                         (EPIC, 98))
        self.assertTrue(receipts[0]["body_file"])
        self.assertEqual(len(posted), 1)


class TestSignOnlyHead(unittest.TestCase):
    def test_freshness_is_measured_against_the_body_head_line(self):
        runner = _runner("Epic: #%d\n" % EPIC, [_comment(9, NEWER, _rehearsal())])
        blk, fields = er.composer_check(
            TICKET, SLUG, None, runner=runner, env=ENFORCE,
            authority="fork-no-merge", out=io.StringIO(),
            body="READY-FOR-REVIEW: x\n**HEAD:** `%s`\n" % HEAD[:12])
        self.assertIsNone(blk)
        self.assertIn(["git", "log", "-1", "--format=%ct", HEAD[:12]], runner.calls)
        self.assertNotIn(["git", "rev-parse", "HEAD"], runner.calls)

    def test_head_from_body_shapes(self):
        self.assertEqual(er.head_from_body("- HEAD: ABCDEF1234\n"), "abcdef1234")
        self.assertIsNone(er.head_from_body("HEAD: abc12\n"))
        self.assertIsNone(er.head_from_body(None))


class TestRehearsalEvidence(unittest.TestCase):
    def _check(self, body):
        return er.composer_check(
            TICKET, SLUG, HEAD, runner=_runner("Epic: #%d\n" % EPIC,
                                               [_comment(7, NEWER, body)]),
            env=ENFORCE, authority="fork-no-merge", out=io.StringIO())

    def test_gk_instruction_carrying_the_marker_is_not_a_rehearsal(self):
        blk, fields = self._check("Epic-rehearsal: still missing -- rehearse "
                                  "#42 #43 on a fresh copy")
        self.assertIn("is not a passing rehearsal", blk or "")
        self.assertIn("pass/fail table row", blk)
        self.assertEqual(fields["epic_rehearsal"], 7)

    def test_a_fail_row_fails_the_rehearsal(self):
        body = _rehearsal().replace("| stock per card == Money | pass |",
                                    "| stock per card == Money | FAIL (#42 still "
                                    "broken) |")
        blk, _ = self._check(body)
        self.assertIn("an acceptance row FAILED", blk or "")

    def test_header_pass_fail_and_mixed_cell_still_pass(self):
        body = ("Epic-rehearsal: copy erp-test refresh 8812\n"
                "| item | pass/fail |\n|---|---|\n"
                "| names | pass (was fail before the fix) |\n| subs | #42 |\n")
        blk, fields = self._check(body)
        self.assertIsNone(blk)
        self.assertEqual(fields["epic_gate"], "pass")

    def test_missing_copy_is_named(self):
        blk, _ = self._check(_rehearsal().replace(
            "copy erp-test refresh 8812", "copy"))
        self.assertIn("the fresh copy it ran on", blk or "")


class TestParserGaps(unittest.TestCase):
    def test_epic_ref_headings_numbered_links_and_tilde_fences(self):
        self.assertEqual(er.parse_epic_ref("## Epic: #77"), 77)
        self.assertEqual(er.parse_epic_ref("1. Epic: #77"), 77)
        self.assertEqual(er.parse_epic_ref("Epic: [#77](https://x/77)"), 77)
        self.assertIsNone(er.parse_epic_ref("~~~\nEpic: #77\n~~~\n"))

    def test_lists_ticket_skips_cross_repo_and_quoted_lines(self):
        self.assertFalse(er.lists_ticket("see other/repo#42", 42))
        self.assertFalse(er.lists_ticket("> included #42", 42))
        self.assertTrue(er.lists_ticket("included: #41, #42", 42))


class TestHookUnknownGate(unittest.TestCase):
    def test_receipt_match_logs_an_unknown_gate_without_an_epic(self):
        import hashlib
        import subprocess
        import time
        body = "READY-FOR-REVIEW: branch lane-x\nHEAD: abc12345\n"
        with tempfile.TemporaryDirectory() as td:
            home, proj = os.path.join(td, "home"), os.path.join(td, "proj")
            os.makedirs(os.path.join(home, ".claude", "handoff-gate"))
            os.makedirs(proj)
            with open(os.path.join(proj, "CLAUDE.md"), "w") as f:
                f.write("<!-- airuleset:authority=fork-no-merge -->\n")
            bpath = os.path.join(proj, "body.md")
            with open(bpath, "w") as f:
                f.write(body)
            with open(os.path.join(home, ".claude", "handoff-gate", "o-r-42.json"),
                      "w") as f:
                json.dump({"sha256": hashlib.sha256(body.encode()).hexdigest(),
                           "ts": time.time(), "issue": 42,
                           "epic_gate": "unknown"}, f)
            r = subprocess.run(
                ["bash", os.path.join(_REPO, "hooks",
                                      "block-handoff-without-composer.sh")],
                input=json.dumps({"tool_input": {
                    "command": "gh issue comment 42 --body-file %s" % bpath}}),
                capture_output=True, text=True, timeout=60, cwd=proj,
                env=dict(os.environ, HOME=home))
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(home, ".claude", "handoff-gate.log")) as f:
                self.assertIn("ALLOW receipt-match epic-gate=unknown", f.read())


class TestMetricReviewFixes(unittest.TestCase):
    def test_live_on_prod_needs_a_version_right_after(self):
        r = pr.classify_timeline([com(3, "live on PROD since 12.9"),
                                  com(4, "READY-FOR-REVIEW: x")])
        self.assertIsNone(r["released_at"])
        r = pr.classify_timeline([com(3, "Live on PROD: v2.349"),
                                  com(4, "READY-FOR-REVIEW: x")])
        self.assertEqual(r["rework"], 1)

    def test_cross_fork_marker_is_a_new_hand_off(self):
        r = pr.classify_timeline([lab(5, "verify-on-copy"),
                                  com(6, "Fix.\nReady for gatekeeper cross-fork review.")])
        self.assertEqual(r["rework"], 1)

    def test_chyba_without_diacritics_is_not_a_defect_word(self):
        rel = {100: 0.0}
        self.assertEqual(pr.follow_ups([_issue(9, 12, body="chyba este #100")], rel),
                         {})
        self.assertEqual(pr.follow_ups([_issue(9, 12, body="chybny vystup #100")],
                                       rel), {100: [9]})

    def test_non_positive_days_is_an_error(self):
        out = io.StringIO()
        self.assertEqual(pr.run(None, None, days=0, runner=_Seam({}, {}), now=NOW,
                                slug=SLUG, out=out), 2)
        self.assertIn("positive integer", out.getvalue())

    def test_truncated_population_is_marked_a_lower_bound(self):
        issues, timelines = _population()
        out = io.StringIO()
        rc = pr.run(None, None, runner=_Seam(issues, timelines), now=NOW,
                    slug=SLUG, out=out, limit=len(issues))
        self.assertEqual(rc, 0)
        self.assertIn("TOTAL\t5 (lower bound: population capped)", out.getvalue())

    def test_timelines_go_through_run_parallel(self):
        import cli_parallel
        issues, timelines = _population()
        real = cli_parallel.run_parallel
        with mk.patch.object(cli_parallel, "run_parallel",
                             side_effect=real) as spy:
            rc = pr.run(None, None, runner=_Seam(issues, timelines), now=NOW,
                        slug=SLUG, out=io.StringIO())
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(spy.call_args.args[0]), sorted(issues))


if __name__ == "__main__":
    unittest.main()
