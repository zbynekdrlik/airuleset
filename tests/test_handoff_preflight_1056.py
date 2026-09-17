"""#1056 L2 (f) — the composer hand-off pre-flight.

Before posting a READY-FOR-REVIEW comment, `cmd_handoff` /
`_cmd_handoff_post_body_file` call the gk-watch classifier and REFUSE when:
  * gk comments newer than the previous RFR carry finding ids the body does not
    disposition (exact-id match against Closes-finding: rows / a disposition
    row per id) — `handoff BLOCK: needs-disposition <ids>`; or
  * the newest gk comment is a BOUNCE and Evidence-HEAD is not newer than it —
    `handoff BLOCK: no new commit since BOUNCE <comment-id> @<sha>`.
`unknown` from gk-watch → fail-OPEN (a printed notice, never a wrong block).

The disposition SHAPE check is one primitive, `cli_gk_watch.missing_dispositions`,
shared by the pre-flight and by `validate_passthrough_body` (the pass-through
mirror). RED-first: the pre-flight helper, the primitive, and the
`required_disposition_ids` param do not exist on the base tree.
"""
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_gk_watch
import cli_handoff_template as ht


def _watch(state, *, ids=None, gk_ts=None, head_ts=None, gk_id="C1", sha="deadbee"):
    return {
        "issue": 1, "state": state, "gk_login": "zbynekdrlik",
        "gk_latest": {"id": gk_id, "verdict": "BOUNCE", "created_at": gk_ts,
                      "sha": sha, "ids": list(ids or [])},
        "rfr": None, "head_ts": head_ts, "age_seconds": 0.0,
        "undispositioned_ids": list(ids or []),
    }


class MissingDispositions(unittest.TestCase):
    def test_reports_ids_the_body_does_not_disposition(self):
        body = ("READY-FOR-REVIEW: x\n"
                "Closes-finding: 1 — fixed in abc1234\n"
                "Disposition: 2 wontfix (out of scope)\n")
        # 1 and 2 dispositioned; 3 is not.
        self.assertEqual(
            cli_gk_watch.missing_dispositions(body, ["1", "2", "3"]), ["3"])

    def test_all_dispositioned_is_empty(self):
        body = "🔴 1 fixed\n🟡 2 finding addressed\n"
        self.assertEqual(cli_gk_watch.missing_dispositions(body, ["1", "2"]), [])

    def test_empty_ids_is_empty(self):
        self.assertEqual(cli_gk_watch.missing_dispositions("anything", []), [])
        self.assertEqual(cli_gk_watch.missing_dispositions("anything", None), [])

    def test_casual_mention_does_not_disposition(self):
        # "fixed 2 typos" must NOT count as dispositioning finding 2.
        self.assertEqual(
            cli_gk_watch.missing_dispositions("fixed 2 typos in the readme",
                                              ["2"]), ["2"])


class PreflightBlocks(unittest.TestCase):
    def _pf(self, body, watch_result):
        return airuleset._handoff_gk_preflight(
            1, "owner/repo", body, watch_result=watch_result)

    def test_bounce_unanswered_no_new_commit_blocks(self):
        # newest gk comment is a BOUNCE, head is NOT newer than it.
        w = _watch("bounce-unanswered", ids=["1"], gk_ts=2000.0, head_ts=1000.0)
        msg = self._pf("READY-FOR-REVIEW: nothing new\n🔴 1 fixed", w)
        self.assertIsNotNone(msg)
        self.assertIn("no new commit since BOUNCE", msg)
        self.assertIn("C1", msg)

    def test_bounce_unanswered_new_commit_but_undispositioned_blocks(self):
        # a commit landed since the BOUNCE, but the body dispositions none of
        # the BOUNCE's finding ids.
        w = _watch("bounce-unanswered", ids=["1", "2"], gk_ts=1000.0,
                   head_ts=2000.0)
        msg = self._pf("READY-FOR-REVIEW: reworked", w)
        self.assertIsNotNone(msg)
        self.assertIn("needs-disposition", msg)
        self.assertIn("1", msg)
        self.assertIn("2", msg)

    def test_bounce_unanswered_new_commit_and_dispositioned_passes(self):
        w = _watch("bounce-unanswered", ids=["1", "2"], gk_ts=1000.0,
                   head_ts=2000.0)
        body = ("READY-FOR-REVIEW: reworked\n"
                "Closes-finding: 1 — fixed in abc1234\n"
                "Closes-finding: 2 — fixed in def5678\n")
        self.assertIsNone(self._pf(body, w))

    def test_needs_disposition_state_blocks_when_body_missing_ids(self):
        w = _watch("needs-disposition", ids=["3"], gk_ts=1000.0, head_ts=2000.0)
        msg = self._pf("READY-FOR-REVIEW: no disposition here", w)
        self.assertIsNotNone(msg)
        self.assertIn("needs-disposition", msg)
        self.assertIn("3", msg)

    def test_needs_disposition_state_passes_when_body_dispositions(self):
        w = _watch("needs-disposition", ids=["3"], gk_ts=1000.0, head_ts=2000.0)
        body = "READY-FOR-REVIEW: x\nCloses-finding: 3 — fixed in abc1234\n"
        self.assertIsNone(self._pf(body, w))

    def test_rfr_current_passes(self):
        w = _watch("rfr-current", ids=[], gk_ts=1000.0, head_ts=2000.0)
        self.assertIsNone(self._pf("READY-FOR-REVIEW: x", w))

    def test_no_gk_comment_passes(self):
        w = _watch("no-gk-comment", ids=[], gk_ts=None, head_ts=None)
        self.assertIsNone(self._pf("READY-FOR-REVIEW: x", w))

    def test_unknown_fails_open_with_notice(self):
        w = _watch("unknown", ids=[], gk_ts=None, head_ts=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            msg = self._pf("READY-FOR-REVIEW: x", w)
        self.assertIsNone(msg, "unknown must fail OPEN")
        self.assertIn("gk-watch", buf.getvalue().lower())


class PassthroughMirror(unittest.TestCase):
    """validate_passthrough_body mirrors the disposition shape check via
    required_disposition_ids."""

    _BODY = ("READY-FOR-REVIEW: x\n"
             "Self-review-model: claude-opus-4-8\n"
             "Closes-finding: 1 — fixed in abc1234\n")

    def test_missing_disposition_blocks(self):
        err = ht.validate_passthrough_body(
            self._BODY, bounce_round=1, required_disposition_ids=["1", "2"])
        self.assertIsNotNone(err)
        self.assertIn("needs-disposition", err)
        self.assertIn("2", err)

    def test_all_dispositioned_passes(self):
        err = ht.validate_passthrough_body(
            self._BODY, bounce_round=1, required_disposition_ids=["1"])
        self.assertIsNone(err)

    def test_no_ids_is_the_existing_behaviour(self):
        # Backward-compatible: no required ids → the pre-#1056 shape check only.
        self.assertIsNone(
            ht.validate_passthrough_body(self._BODY, bounce_round=1))


if __name__ == "__main__":
    unittest.main()
