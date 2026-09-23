"""Issue 1130 (review finding, same drift class): the self-close guard
`hooks/block-fork-no-merge-issue-close.sh` keeps its OWN copies of the gk
queue-label set in two carve-outs, and both missed #1053's `gk-processing`:

  - #533 acceptance close: a stream could self-close its own
    `needs-acceptance` ticket while the gatekeeper is PROCESSING a
    re-hand-off (`gk-processing` replaced `ready-for-review` at pickup);
  - #756 verdict close: a stream could self-close a ticket carrying an old gk
    verdict while gk is processing it again.

Both must BLOCK exactly as they already do for `ready-for-review` /
`needs-gatekeeper`, and every label in MAINTAINER_ACTION_LABELS must be
covered (drift guard). Hermetic: the shared `run` helper uses the module's
isolated HOME (`MODULE_HOOK_HOME`) and a fake `gh` on PATH.
"""
import unittest

import airuleset
import cli_quals
from test_fork_no_merge_close_guard import _cwd_with_authority, run


class AcceptanceCloseBlockedWhileGkProcessing(unittest.TestCase):

    def setUp(self):
        self.branch = _cwd_with_authority("branch-merge")
        self.stream = "stream:%s" % airuleset._current_user()
        self.M = airuleset.MAINTAINER_GH_LOGIN

    def _close(self, labels):
        return run("gh issue close 3313 -R zbynekdrlik/odoo-erp --comment done",
                   self.branch, me=self.M, author=self.M, labels=labels)

    def test_blocks_acceptance_close_when_gk_processing_present(self):
        r = self._close("%s needs-acceptance gk-processing" % self.stream)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_every_maintainer_label_blocks_acceptance_close(self):
        for lb in cli_quals.MAINTAINER_ACTION_LABELS:
            with self.subTest(label=lb):
                r = self._close("%s needs-acceptance %s" % (self.stream, lb))
                self.assertEqual(r.returncode, 2, r.stderr)

    def test_plain_acceptance_close_still_allowed(self):
        r = self._close("%s needs-acceptance" % self.stream)
        self.assertEqual(r.returncode, 0, r.stderr)


class VerdictCloseBlockedWhileGkProcessing(unittest.TestCase):

    HEADING = "## Gatekeeper cross-fork review - CLEAN\n\nDiff read, CI green."

    def setUp(self):
        self.branch = _cwd_with_authority("branch-merge")
        self.M = airuleset.MAINTAINER_GH_LOGIN

    def _close(self, labels):
        return run("gh issue close 5345 -R zbynekdrlik/odoo-erp --comment ok",
                   self.branch, me=self.M, author=self.M, labels=labels,
                   comments=self.HEADING)

    def test_blocks_verdict_close_when_gk_processing_present_and_names_it(self):
        r = self._close("gk-processing")
        self.assertEqual(r.returncode, 2, r.stderr)
        first = (r.stderr.splitlines() or [""])[0]
        self.assertIn("gk-processing", first, "first line: %r" % first)

    def test_every_maintainer_label_blocks_verdict_close(self):
        for lb in cli_quals.MAINTAINER_ACTION_LABELS:
            with self.subTest(label=lb):
                self.assertEqual(self._close(lb).returncode, 2)

    def test_verdict_close_without_queue_label_still_allowed(self):
        self.assertEqual(self._close("").returncode, 0)


if __name__ == "__main__":
    unittest.main()
