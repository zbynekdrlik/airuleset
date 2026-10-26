"""#1061 item 2 -- gates.designdispatch: `check_issue` refuses unless the newest
`Design-by:` comment on the issue is `Design-by: main <managed main id>`
(fail-closed on an unreadable thread). Its dispatch-time `evaluate` was retired
by issue 1137 B1; the implementer runs `--issue N` itself.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gates import designdispatch as dd  # noqa: E402


# #1046: dd.* runs IN-PROCESS and reads model_lineup / airuleset via
# expanduser("~"). Point HOME at a fresh empty dir for the whole module
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

FABLE = "claude-fable-5-1"


def _ci(comments):
    """check_issue() on issue 1061 with fetch/fable_id injected."""
    return dd.check_issue(1061, "owner/repo", "/repo",
                          fetch=lambda slug, number, cwd: comments, fable_id=FABLE)


class TestNewestDesignBy(unittest.TestCase):
    def test_none_when_no_design_by(self):
        self.assertIsNone(dd.newest_design_by(["hello", "world"]))

    def test_newest_wins(self):
        bodies = ["Design-by: worker claude-opus-4-8",
                  "some later comment",
                  "Design-by: main claude-fable-5-1"]
        self.assertEqual(dd.newest_design_by(bodies), ("main", "claude-fable-5-1"))

    def test_later_worker_overrides_earlier_main(self):
        bodies = ["Design-by: main claude-fable-5-1",
                  "Design-by: worker claude-opus-4-8"]
        self.assertEqual(dd.newest_design_by(bodies),
                         ("worker", "claude-opus-4-8"))

    def test_bullet_and_bold_tolerant(self):
        self.assertEqual(
            dd.newest_design_by(["- **Design-by:** main claude-fable-5-1[1m]"]),
            ("main", "claude-fable-5-1[1m]"))


class TestIssueNumbers(unittest.TestCase):
    """#1061 review-3: the fleet's real prompts write the ticket WITHOUT `#`
    (the lane-overlap hook refuses `#N` outside its receipt). Parse `#N` AND bare
    `issue N` / `issues N, M` / `issue #N` / `issue-N`, guarding versions/dates,
    scoped to the lead line so a folded body ref never over-checks."""

    def test_bare_issue_word(self):
        self.assertEqual(
            dd.issue_numbers("Work airuleset issue 1061 (owner escalation)"),
            [1061])

    def test_batch_two_bare_issue_words(self):
        self.assertEqual(
            dd.issue_numbers(
                "Work airuleset issue 1056 lane L1 (with issue 1057 items 1, 2, 3, 5)"),
            [1056, 1057])

    def test_hash_form(self):
        self.assertEqual(dd.issue_numbers("Work issue #1048 FIX-FORWARD"), [1048])

    def test_comma_list_after_issues(self):
        self.assertEqual(dd.issue_numbers("Work issues 1056, 1057"), [1056, 1057])

    def test_hyphen_form(self):
        self.assertEqual(dd.issue_numbers("resume issue-1061 lane"), [1061])

    def test_version_and_date_are_not_tickets(self):
        self.assertEqual(
            dd.issue_numbers("bump to 0.1.326 on 2026-09-17, sha 44fc0045"), [])

    def test_no_ticket(self):
        self.assertEqual(dd.issue_numbers("do some infra work"), [])

    def test_folded_ref_on_a_later_line_is_ignored(self):
        # first-line scoping: a folded "issue 1046" on a body line must not
        # be checked as a worked ticket.
        self.assertEqual(
            dd.issue_numbers(
                "Work airuleset issue 1061 (owner escalation)\n"
                "5b. Stale-base wedge (folded from issue 1046, hit 3 lanes)"),
            [1061])

    def test_items_singledigits_not_captured(self):
        self.assertEqual(
            dd.issue_numbers("Work issue 1061 with items 1, 2, 3, 5"), [1061])


class TestCheckIssue(unittest.TestCase):
    def test_main_fable_allows(self):
        self.assertTrue(_ci(["Design-by: main claude-fable-5-1"])[0])

    def test_main_fable_1m_tag_allows(self):
        self.assertTrue(_ci(["Design-by: main claude-fable-5-1[1m]"])[0])

    def test_worker_design_blocks(self):
        ok, r = _ci(["Design-by: worker claude-opus-4-8"])
        self.assertFalse(ok)
        self.assertIn("worker", r)

    def test_wrong_model_blocks(self):
        # #1119: claude-opus-4-8 stays an allowed dispatch id but is NOT a
        # managed MAIN model, so a `Design-by: main claude-opus-4-8` stamp is
        # still refused; the reason names the accepted managed MAIN models.
        ok, r = _ci(["Design-by: main claude-opus-4-8"])
        self.assertFalse(ok)
        self.assertIn("managed MAIN model", r)

    def test_opus_5_5_main_allows(self):
        # #1119: the new managed main authors designs as claude-opus-5-5.
        self.assertTrue(_ci(["Design-by: main claude-opus-5-5"])[0])

    def test_no_design_by_blocks(self):
        ok, r = _ci(["just a normal comment"])
        self.assertFalse(ok)
        self.assertIn("no `Design-by:`", r)

    def test_gh_error_blocks_fail_closed(self):
        ok, r = _ci(None)  # fetch returns None
        self.assertFalse(ok)
        self.assertIn("fail-closed", r)

    def test_retired_dispatch_parts_are_gone(self):
        for name in ("evaluate", "_block_message", "_is_pull_request"):
            self.assertFalse(hasattr(dd, name), name)


if __name__ == "__main__":
    unittest.main()
