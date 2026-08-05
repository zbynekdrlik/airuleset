"""Direct unit coverage for notify.py's #187/#220 repo-resolution helpers.

The hook-integration tests (test_block_commit_without_design.py,
test_subagent_stop_check_design.py, test_run_card_enforcement.py) drive the
real hooks end-to-end and are the authoritative regression proof. This file
covers pure-function edge cases a fresh-context adversarial review found
UNTESTED by mutation (M4/M6/M7 in its report): a GitHub URL's trailing
punctuation, a URL mentioned on a line that is NOT `pr:`, and a batch
evidence block carrying more than one `pr:` line.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import notify                                              # noqa: E402


class TestRepoFromPrLineTrailingPunctuation(unittest.TestCase):
    """WARNING 4 (adversarial review): a repo-ROOT URL (no /pull/N path
    segment following it) had its terminator absorb trailing punctuation
    into the repo name -- a parenthetical, a trailing comma, a sentence
    period, or backticks around the URL each corrupted the match. A
    /pull/N-suffixed URL (the realistic `pr:` line shape) was never
    affected, since the terminator "/" already stopped it correctly."""

    def test_paren_wrapped_repo_root_url(self):
        t = "pr: #63 (see https://github.com/zbynekdrlik/dantesync)"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_trailing_comma(self):
        t = "pr: #63 https://github.com/zbynekdrlik/dantesync, done"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_trailing_sentence_period(self):
        t = "pr: #63 https://github.com/zbynekdrlik/dantesync."
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_backtick_wrapped(self):
        t = "pr: #63 `https://github.com/zbynekdrlik/dantesync`"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_git_suffix_with_trailing_punctuation_still_strips_git(self):
        t = "pr: #63 git@github.com:zbynekdrlik/dantesync.git"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_pull_url_form_unaffected_control(self):
        t = "pr: #63 https://github.com/zbynekdrlik/dantesync/pull/63"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")


class TestRepoFromPrLineOnlyReadsTheDeclaredField(unittest.TestCase):
    """M6 (adversarial review, mutation-proven gap): a GitHub URL sitting
    on a line that is NOT `pr:` must never be picked up -- only the
    evidence block's own declared `pr:` field is trustworthy."""

    def test_a_url_on_an_issues_line_is_ignored(self):
        t = "issues: #61 see https://github.com/other/repo\nissue_state: #61=closed"
        self.assertEqual(notify.repo_from_pr_line(t), "")

    def test_a_url_on_a_cards_fired_line_is_ignored(self):
        t = "cards_fired: #61 https://github.com/other/repo confirmed"
        self.assertEqual(notify.repo_from_pr_line(t), "")

    def test_a_url_in_free_prose_with_no_field_prefix_is_ignored(self):
        t = "See https://github.com/other/repo for background."
        self.assertEqual(notify.repo_from_pr_line(t), "")

    def test_the_real_pr_line_among_decoys_is_still_found(self):
        t = ("issues: #61 see https://github.com/other/repo\n"
             "pr: #63 https://github.com/zbynekdrlik/dantesync/pull/63\n"
             "cards_fired: #61 https://github.com/other/repo confirmed")
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")


class TestRepoFromPrLineMultipleLines(unittest.TestCase):
    """M7 (adversarial review, mutation-proven gap): a batch evidence block
    carrying more than one `pr:` line resolves to the FIRST one -- proven
    directly since the two-repo (#220) test files only ever exercise a
    single `pr:` line."""

    def test_first_pr_line_wins(self):
        t = ("pr: #63 https://github.com/zbynekdrlik/dantesync/pull/63\n"
             "pr: #64 https://github.com/zbynekdrlik/other-repo/pull/64")
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_a_pr_line_with_no_url_is_skipped_in_favor_of_the_next(self):
        t = ("pr: #63 NOT MERGED\n"
             "pr: #64 https://github.com/zbynekdrlik/dantesync/pull/64")
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")


class TestRepoFromPrLineCaseAndHostForms(unittest.TestCase):

    def test_uppercase_field_label(self):
        t = "PR: #63 https://github.com/zbynekdrlik/dantesync/pull/63"
        self.assertEqual(notify.repo_from_pr_line(t), "dantesync")

    def test_no_such_line_returns_empty(self):
        self.assertEqual(notify.repo_from_pr_line("issue_state: #41=closed"), "")

    def test_non_string_returns_empty(self):
        self.assertEqual(notify.repo_from_pr_line(None), "")
        self.assertEqual(notify.repo_from_pr_line(""), "")


if __name__ == "__main__":
    unittest.main()
