"""#589 — the footer `gk N` comment-fallback END CONDITION.

The #313-pt-2 comment fallback in `_slice_mine_and_handed` counts a ticket as
handed-off (gk) off a `READY-FOR-REVIEW:` comment when the queue LABEL is
absent (a fork-no-merge collaborator cannot self-label; the repo auto-labeller
can be broken). Before #589 that fallback had NO end condition: a ticket whose
gk hand-off is long DONE (reviewed + merged + released, queue labels removed,
NO `**GATEKEEPER` finding comment left behind — the live odoo-erp #4502 shape)
kept reading `True` off its stale, permanent hand-off comment forever, so it
was counted in `gk` permanently AND, because the label query said it was NOT
handed, it read as "in both I and gk" — the #391 `I = mine - gk` violation the
owner ruled unacceptable ("footer must be TRUE, not known-inflated").

The fix rides the SAME per-candidate gh call: the fallback reads the issue
TIMELINE (`/issues/N/timeline?per_page=100`) instead of `/comments`, and a
gk-RESOLUTION event (queue-label removal / close) AFTER the last hand-off
comment flips the verdict to unhandled — while a later comment-only RE-hand-off
(the legitimate fresh-hand-off case) flips it back, via the existing
last-signal-wins walk. Zero added gh calls (timeline REPLACES comments).
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock as m

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"
ROOT = "/tmp/does-not-matter-589"
# An own-account 3-qual slice: `len(quals) != 1`, so the shared-account
# `_last_origin_owner` recovery block is skipped and only the comment/timeline
# fallback drives `handed` — exactly the path under test.
QUALS = ["assignee:@me", "author:@me", "label:stream:miva1"]


def _row(number, labels=()):
    return {
        "number": number,
        "title": "MIVA schvalovatelia volna",
        "createdAt": "2026-08-01T00:00:00Z",
        "labels": [{"name": n} for n in labels],
    }


def _commented(body, ts):
    return {"event": "commented", "body": body, "created_at": ts,
            "user": {"login": "stream-bot"}}


def _unlabeled(name, ts):
    return {"event": "unlabeled", "label": {"name": name}, "created_at": ts,
            "actor": {"login": "zbynekdrlik"}}


def _closed(ts):
    return {"event": "closed", "created_at": ts, "actor": {"login": "zbynekdrlik"}}


def _gk_count(number, *, timeline, comments, labels=()):
    """Drive `_slice_mine_and_handed` for a single-ticket slice and return the
    footer's `gk` count for it (handed subset of the workable partition — the
    exact derivation `airuleset.py`'s footer branch and `cmd_slice_quals` use).

    Both endpoints are answered so the SAME fixture exercises the pre-fix
    (comments-walk) and post-fix (timeline-walk) code: pre-fix reads
    `comments`, post-fix reads `timeline`.
    """
    def gh(*args, **kw):
        if args[:2] == ("issue", "list"):
            return json.dumps([_row(number, labels)])
        if args[0] == "api" and "/timeline" in args[1]:
            return json.dumps(timeline)
        if args[0] == "api" and args[1].endswith("/comments"):
            return json.dumps(comments)
        return "[]"

    with m.patch.object(airuleset, "_gh_out", side_effect=gh):
        rows, handed, failed = airuleset._slice_mine_and_handed(QUALS, ROOT, SLUG)
    assert not failed, "fixture must not simulate a gh failure"
    workable, _waiting, _ops = airuleset._partition_workable(rows)
    gk = sum(1 for n in workable if handed.get(n))
    return gk, rows, handed, workable


class ResolvedHandoffEndCondition(unittest.TestCase):
    """Acceptance #3 (RED-on-revert): an old READY-FOR-REVIEW comment + labels
    already removed by gk must give gk=0; the unfixed comments-walk gives gk=1."""

    def test_label_removal_after_handoff_comment_ends_the_fallback(self):
        # odoo-erp #4502 shape: hand-off comment early, gk removed the queue
        # label later (no `**GATEKEEPER` finding comment), ticket still OPEN.
        timeline = [
            _commented("READY-FOR-REVIEW: branch worktree-x, tests green",
                       "2026-08-05T10:00:00Z"),
            _unlabeled("ready-for-review", "2026-08-06T12:00:00Z"),
        ]
        comments = [
            {"body": "READY-FOR-REVIEW: branch worktree-x, tests green"},
        ]
        gk, _rows, _handed, _workable = _gk_count(
            4502, timeline=timeline, comments=comments)
        self.assertEqual(gk, 0, "a resolved (label-removed) hand-off must NOT "
                                "count as gk")

    def test_needs_gatekeeper_removal_also_ends_the_fallback(self):
        timeline = [
            _commented("READY-FOR-REVIEW: handing off", "2026-08-05T10:00:00Z"),
            _unlabeled("needs-gatekeeper", "2026-08-06T12:00:00Z"),
        ]
        comments = [{"body": "READY-FOR-REVIEW: handing off"}]
        gk, *_ = _gk_count(4600, timeline=timeline, comments=comments)
        self.assertEqual(gk, 0)

    def test_close_after_handoff_comment_ends_the_fallback(self):
        # a closed-then-reopened ticket that is currently OPEN but whose gk
        # resolution (close) came after the hand-off, with no re-hand-off.
        timeline = [
            _commented("READY-FOR-REVIEW: handing off", "2026-08-05T10:00:00Z"),
            _closed("2026-08-06T12:00:00Z"),
        ]
        comments = [{"body": "READY-FOR-REVIEW: handing off"}]
        gk, *_ = _gk_count(4700, timeline=timeline, comments=comments)
        self.assertEqual(gk, 0)

    def test_one_ticket_never_in_both_I_and_gk(self):
        # Acceptance #2: the resolved ticket lands in EXACTLY ONE bucket.
        # handed=False -> gk drops it, so I = workable - gk counts it once.
        timeline = [
            _commented("READY-FOR-REVIEW: x", "2026-08-05T10:00:00Z"),
            _unlabeled("ready-for-review", "2026-08-06T12:00:00Z"),
        ]
        comments = [{"body": "READY-FOR-REVIEW: x"}]
        gk, _rows, handed, workable = _gk_count(
            4502, timeline=timeline, comments=comments)
        i_bucket = sum(1 for n in workable if not handed.get(n))
        self.assertEqual(gk, 0)
        self.assertEqual(i_bucket, 1)
        # gk (handed subset) and I (not-handed subset) PARTITION workable
        # exactly — no ticket is dropped or double-counted (the #391 invariant
        # the derivation `gk = sum(handed); I = len - gk` enforces).
        self.assertEqual(gk + i_bucket, len(workable))


class LegitimateFreshHandoffStillCounts(unittest.TestCase):
    """The end condition must NOT re-break the fresh-hand-off case the fallback
    exists for (fork-no-merge 403 / broken auto-labeller = no label event)."""

    def test_handoff_comment_with_no_resolution_still_counts_as_gk(self):
        timeline = [
            _commented("READY-FOR-REVIEW: fresh hand-off, please review",
                       "2026-08-19T10:00:00Z"),
        ]
        comments = [{"body": "READY-FOR-REVIEW: fresh hand-off, please review"}]
        gk, *_ = _gk_count(4800, timeline=timeline, comments=comments)
        self.assertEqual(gk, 1, "a fresh comment-only hand-off must still count")

    def test_re_handoff_after_resolution_counts_again(self):
        # comment (True) -> gk cleared label (resolution) -> stream RE-hands-off
        # with a NEW comment (True): last signal wins -> handed again.
        timeline = [
            _commented("READY-FOR-REVIEW: first hand-off", "2026-08-05T10:00:00Z"),
            _unlabeled("ready-for-review", "2026-08-06T12:00:00Z"),
            _commented("READY-FOR-REVIEW: re-submitting after fixes",
                       "2026-08-07T09:00:00Z"),
        ]
        comments = [
            {"body": "READY-FOR-REVIEW: first hand-off"},
            {"body": "READY-FOR-REVIEW: re-submitting after fixes"},
        ]
        gk, *_ = _gk_count(4900, timeline=timeline, comments=comments)
        self.assertEqual(gk, 1, "a genuine comment-only re-hand-off after a "
                                "resolution must count again")

    def test_gatekeeper_finding_comment_after_handoff_still_unhandled(self):
        # the pre-existing negative signal is preserved: a `**GATEKEEPER`
        # finding comment after the hand-off flips it to unhandled.
        timeline = [
            _commented("READY-FOR-REVIEW: hand-off", "2026-08-05T10:00:00Z"),
            _commented("**GATEKEEPER review — BOUNCE**\nfix the failing test",
                       "2026-08-06T12:00:00Z"),
        ]
        comments = [
            {"body": "READY-FOR-REVIEW: hand-off"},
            {"body": "**GATEKEEPER review — BOUNCE**\nfix the failing test"},
        ]
        gk, *_ = _gk_count(4950, timeline=timeline, comments=comments)
        self.assertEqual(gk, 0)


class TimelineHandoffSignalUnit(unittest.TestCase):
    """Unit-test the pure per-event classifier `_timeline_handoff_signal`
    (verdict_signal, is_gatekeeper_comment)."""

    def sig(self, ev):
        return airuleset._timeline_handoff_signal(ev)

    def test_readiness_comment_is_true(self):
        self.assertEqual(
            self.sig(_commented("READY-FOR-REVIEW: x", "t")), (True, False))

    def test_gatekeeper_comment_is_false_and_flagged(self):
        self.assertEqual(
            self.sig(_commented("**GATEKEEPER — BOUNCE**\nnope", "t")),
            (False, True))

    def test_neutral_comment_is_none(self):
        self.assertEqual(
            self.sig(_commented("still working on it", "t")), (None, False))

    def test_unlabeled_queue_label_is_resolution_false(self):
        self.assertEqual(self.sig(_unlabeled("ready-for-review", "t")),
                         (False, False))
        self.assertEqual(self.sig(_unlabeled("needs-gatekeeper", "t")),
                         (False, False))

    def test_unlabeled_other_label_is_none(self):
        self.assertEqual(self.sig(_unlabeled("bug", "t")), (None, False))
        self.assertEqual(self.sig(_unlabeled("prio:bounce", "t")), (None, False))

    def test_closed_is_resolution_false(self):
        self.assertEqual(self.sig(_closed("t")), (False, False))

    def test_unrelated_event_is_none(self):
        self.assertEqual(self.sig({"event": "renamed"}), (None, False))
        self.assertEqual(self.sig({"event": "cross-referenced"}), (None, False))

    def test_non_dict_is_none(self):
        self.assertEqual(self.sig(None), (None, False))
        self.assertEqual(self.sig(42), (None, False))


class TimelineEndpointIsTheFallbackDataSource(unittest.TestCase):
    """The fallback must query the TIMELINE (which carries label/close events),
    not `/comments` (which does not) — zero added gh calls."""

    def test_fallback_calls_timeline_not_comments(self):
        seen = []

        def gh(*args, **kw):
            if args[:2] == ("issue", "list"):
                return json.dumps([_row(4502)])
            if args[0] == "api":
                seen.append(args[1])
                return json.dumps([_commented("READY-FOR-REVIEW: x", "t")])
            return "[]"

        with m.patch.object(airuleset, "_gh_out", side_effect=gh):
            airuleset._slice_mine_and_handed(QUALS, ROOT, SLUG)
        api_paths = [p for p in seen]
        self.assertTrue(any("/timeline" in p for p in api_paths),
                        "fallback must read the issue timeline")
        self.assertFalse(any(p.endswith("/comments") for p in api_paths),
                         "fallback must not read /comments (no label events)")
        # exactly ONE gh api call per candidate — no per-ticket explosion
        self.assertEqual(len(api_paths), 1)


class QueueLabelSetParity(unittest.TestCase):
    """#589 both-reviews 🔵: `airuleset._HANDOFF_QUEUE_LABELS` (the resolution
    signal's queue-label set) duplicates `cli_quals.MAINTAINER_ACTION_LABELS`
    with no import between them; a future THIRD queue label added to one but not
    the other would silently desync the end condition. Lock them equal."""

    def test_handoff_queue_labels_match_maintainer_action_labels(self):
        self.assertEqual(
            set(airuleset._HANDOFF_QUEUE_LABELS),
            set(cli_quals.MAINTAINER_ACTION_LABELS),
            "the timeline resolution queue-label set must stay in sync with "
            "cli_quals.MAINTAINER_ACTION_LABELS (#589)")


if __name__ == "__main__":
    unittest.main()
