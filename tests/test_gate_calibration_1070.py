"""#1070 W38 gate calibration -- integration tests across the seven items that
are pure-python testable here (the commitdesign live read and the AWAY cap live
in their own hook-driven suites: test_block_commit_without_design.py /
test_main_implementation_guard.py).

Items covered here:
  1. designdispatch surfaces a `gate-unavailable:<reason>` from the comment read
     as its block reason (never "no design") -- the GraphQL-exhaustion FP.
  2. [retired with the dispatch-time `evaluate`, issue 1137 B1]
  5. design-record `--help` prints the section template (+ collect-all pin).
  7. the filing net-drain ratchet applies to ATTENDED filings too.
  8. the ATTENDED filing classifier (classify_command) shares the item-3
     CREATE-shape helpers, so a REST comment POST / label PATCH is not a filing.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gates.designdispatch as dd
from gates.filing.__main__ import classify_command

# #1046: some items call dd.* IN-PROCESS, which reads model_lineup / airuleset
# via expanduser("~"). Point HOME at a fresh empty dir for the whole module
# (module-scoped save+restore — batch-31-safe).
_A1046_ORIG_HOME = None
_A1046_HOME = None


def setUpModule():
    global _A1046_ORIG_HOME, _A1046_HOME
    _A1046_ORIG_HOME = os.environ.get("HOME")
    _A1046_HOME = tempfile.mkdtemp(prefix="a1046-modhome-")
    os.environ["HOME"] = _A1046_HOME


def tearDownModule():
    if _A1046_ORIG_HOME is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = _A1046_ORIG_HOME
    shutil.rmtree(_A1046_HOME, ignore_errors=True)


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
