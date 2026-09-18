"""#1070 W38 gate calibration -- integration tests across the seven items that
are pure-python testable here (the commitdesign live read and the AWAY cap live
in their own hook-driven suites: test_block_commit_without_design.py /
test_main_implementation_guard.py).

Items covered here:
  1. designdispatch surfaces a `gate-unavailable:<reason>` from the comment read
     as its block reason (never "no design") -- the GraphQL-exhaustion FP.
  2. designdispatch skips a PR `#N` (resolved via the injected is_pr seam).
  5. design-record `--help` prints the section template (+ collect-all pin).
  7. the filing net-drain ratchet applies to ATTENDED filings too.
  8. the ATTENDED filing classifier (classify_command) shares the item-3
     CREATE-shape helpers, so a REST comment POST / label PATCH is not a filing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gates.designdispatch as dd
from gates.filing.__main__ import classify_command


def _payload(prompt, cwd="/repo", tool="Agent", subagent="autopilot-worker"):
    import json
    return json.dumps({"tool_name": tool, "cwd": cwd,
                       "tool_input": {"subagent_type": subagent, "prompt": prompt}})


class Item1DispatchGateUnavailable(unittest.TestCase):
    def test_check_issue_surfaces_gate_unavailable_not_no_design(self):
        # fetch reports the read itself failed (quota) -> the block reason is the
        # gate-unavailable string, NEVER a "no Design-by:" / "no design" message.
        def fetch(slug, number, cwd):
            return None, "gate-unavailable: GitHub read unavailable (rate limit)"
        ok, reason = dd.check_issue(1061, "o/r", "/repo", fetch=fetch,
                                    fable_id="claude-fable-5-1")
        self.assertFalse(ok)
        self.assertIn("gate-unavailable", reason)
        self.assertNotIn("no `Design-by:`", reason)
        self.assertNotIn("has no", reason)

    def test_evaluate_blocks_with_gate_unavailable(self):
        def fetch(slug, number, cwd):
            return None, "gate-unavailable: rate limit"
        v, reason = dd.evaluate(_payload("Work issue #1061 in repo"),
                                fetch=fetch, resolve_slug=lambda cwd: "o/r",
                                fable_id="claude-fable-5-1")
        self.assertEqual(v, "block")
        self.assertIn("gate-unavailable", reason)

    def test_backward_compatible_none_fetch_still_generic_fail_closed(self):
        # the pre-#1070 fetch contract (a bare None on gh error) still blocks
        # with the generic fail-closed reason -- unchanged.
        def fetch(slug, number, cwd):
            return None
        ok, reason = dd.check_issue(1061, "o/r", "/repo", fetch=fetch,
                                    fable_id="claude-fable-5-1")
        self.assertFalse(ok)
        self.assertIn("could not read", reason)
        self.assertNotIn("gate-unavailable", reason)

    def test_success_tuple_shape_is_accepted(self):
        # the new (bodies, err) tuple contract works for the happy path too.
        def fetch(slug, number, cwd):
            return (["## design\nDesign-by: main claude-fable-5-1"], None)
        ok, reason = dd.check_issue(1061, "o/r", "/repo", fetch=fetch,
                                    fable_id="claude-fable-5-1")
        self.assertTrue(ok, reason)


class Item2SkipPullRequests(unittest.TestCase):
    def _design_fetch(self, ok=True):
        body = ("## root cause ... chosen approach ... rejected alternative\n"
                "Design-by: main claude-fable-5-1")
        return lambda slug, number, cwd: ([body], None)

    def test_pr_number_is_skipped_issue_is_checked(self):
        # prompt names PR #201 (rides it) AND issue #203; #201 must be skipped,
        # #203 must be design-checked and allowed.
        seen = []

        def is_pr(number, slug, cwd):
            return (number == 201, None)

        def fetch(slug, number, cwd):
            seen.append(number)
            return ([("## root cause ... chosen approach ... rejected "
                      "alternative\nDesign-by: main claude-fable-5-1")], None)

        v, reason = dd.evaluate(
            _payload("Work issue 203 riding PR #201 in repo"),
            fetch=fetch, resolve_slug=lambda cwd: "o/r", is_pr=is_pr,
            fable_id="claude-fable-5-1")
        self.assertEqual(v, "allow", reason)
        self.assertNotIn(201, seen)  # never design-checked a PR
        self.assertIn(203, seen)

    def test_prompt_naming_only_a_pr_allows(self):
        def is_pr(number, slug, cwd):
            return (True, None)

        def fetch(slug, number, cwd):
            raise AssertionError("a PR must never be design-checked")

        v, reason = dd.evaluate(
            _payload("update PR #201 title/body, do not gh pr create"),
            fetch=fetch, resolve_slug=lambda cwd: "o/r", is_pr=is_pr)
        self.assertEqual(v, "allow", reason)

    def test_unknown_pr_state_is_treated_as_issue(self):
        # is_pr None (gate-unavailable) -> treat as an issue; the comment read
        # then decides (here: a valid main design -> allow).
        def is_pr(number, slug, cwd):
            return (None, "gate-unavailable: rate limit")

        def fetch(slug, number, cwd):
            return ([("## root cause ... chosen approach ... rejected "
                      "alternative\nDesign-by: main claude-fable-5-1")], None)

        v, reason = dd.evaluate(
            _payload("Work issue 203 in repo"),
            fetch=fetch, resolve_slug=lambda cwd: "o/r", is_pr=is_pr,
            fable_id="claude-fable-5-1")
        self.assertEqual(v, "allow", reason)


class Item5DesignRecordHelp(unittest.TestCase):
    def test_help_template_lists_every_section(self):
        import cli_design_record as d
        tpl = d.design_record_help_template()
        for token in ("Triage:", "trivial", "non-trivial", "Root cause",
                      "Approach 1", "Trade-off", "Architektúra:",
                      "Shared-benefit:", "Design-by"):
            self.assertIn(token, tpl, token)

    def test_validate_body_collects_every_missing_requirement(self):
        # collect-all regression pin (already GREEN on main; must stay so).
        import cli_design_record as d
        ok, reasons = d.validate_body("nothing useful here at all")
        self.assertFalse(ok)
        self.assertGreaterEqual(len(reasons), 4)


class Item8AttendedFilingCreateOnly(unittest.TestCase):
    """#1070 item 8 -- classify_command (the ATTENDED/main path) shares the
    item-3 CREATE-shape helpers with _is_worker_filing, so a REST comment POST
    or a label/edit PATCH is NOT a filing while a genuine collection create
    still is. Subprocess-free: drives classify_command directly (the
    end-to-end oracle is test_scope_gate.AttendedFilingCreateOnly1070). Present
    (unattended=False), cwd /tmp -- the target-repo journal line is harmless."""

    def _blocks(self, cmd):
        res = classify_command(cmd, "sid", "/tmp", "/tmp", "/tmp/x.log", False)
        return any(r[0] == "BLOCK" for r in res)

    def test_rest_comment_post_is_not_a_filing(self):
        self.assertFalse(self._blocks(
            "gh api repos/o/r/issues/123/comments -X POST -f body=x"))

    def test_rest_issue_patch_is_not_a_filing(self):
        self.assertFalse(self._blocks(
            "gh api repos/o/r/issues/42 -X PATCH -f state=closed"))

    def test_collection_implicit_post_create_is_a_filing(self):
        self.assertTrue(self._blocks(
            "gh api repos/o/r/issues -f title=x -f body=y"))

    def test_collection_explicit_post_create_is_a_filing(self):
        self.assertTrue(self._blocks(
            "gh api repos/o/r/issues -X POST -f title=x -f body=y"))

    def test_gh_issue_create_is_a_filing(self):
        self.assertTrue(self._blocks("gh issue create -t x -b y -R o/r"))

    def test_explicit_get_read_is_not_a_filing(self):
        self.assertFalse(self._blocks(
            "gh api repos/o/r/issues -X GET -f state=open"))


if __name__ == "__main__":
    unittest.main()
