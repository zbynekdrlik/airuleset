"""#570 bod 1 — comment-handoff backstop: a PROPER `GATEKEEPER-ACTION:` or
`READY-FOR-REVIEW:` hand-off marker COMMENT (created in the last ~48h) on an
open ticket that NEVER got its matching label (repo automation does not fire on
a stream-BOT comment, and a reduced stream cannot add labels) is reconciled
gk-side: the matching label is added + an evidence comment posted.

Complement of #551's `gk_orphan_marker_sweep` (which handles the MUTATED marker
and EXPLICITLY excludes the proper case). Shares the false-positive machinery:
the token is PERVASIVE (every processed gk-request leaves a proper marker
forever), so the gate is ANDed and biased HARD to SILENCE — the BOUNDED WINDOW
(marker comment createdAt within ~48h) + the NEVER-LABELED paginated timeline
are the two load-bearing defenses against re-labelling ~44 processed tickets.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog                      # noqa: E402
import watchdog.cross_stream as cs   # noqa: E402

DAY = 24 * 3600
NOW = 10 ** 9
WINDOW = cs.GK_COMMENT_HANDOFF_WINDOW_S


def _c(body, age_s):
    """A comment dict as `gh issue view --json comments` renders it."""
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(NOW - age_s, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return {"body": body, "createdAt": ts}


class ProperMarkerInWindow(unittest.TestCase):
    """`_gk_proper_marker_in_window(comments, marker_re, now, window_s)`."""

    def test_recent_proper_gatekeeper_action_is_in_window(self):
        cmts = [_c("GATEKEEPER-ACTION: over gate wiring", 3600)]
        self.assertTrue(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))

    def test_old_proper_marker_is_out_of_window(self):
        # a processed ticket's ancient marker (>48h) must NOT count
        cmts = [_c("GATEKEEPER-ACTION: handed off long ago", 5 * DAY)]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))

    def test_ready_for_review_marker_recognized(self):
        cmts = [_c("READY-FOR-REVIEW: branch worktree-x, tests green", 3600)]
        self.assertTrue(cs._gk_proper_marker_in_window(
            cmts, cs._GK_READY_MARKER_RE, NOW, WINDOW))

    def test_prose_mention_is_not_a_marker(self):
        cmts = [_c("The `GATEKEEPER-ACTION` items are below:", 3600)]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))

    def test_midline_token_with_colon_after_prose_is_not_a_marker(self):
        # ANCHORING TEETH (#570 review 🟡): the token WITH a colon but MID-LINE
        # (after prose) must NOT count — `_GK_*_MARKER_RE` uses `.match` (anchored
        # at line start), so a `.match`->`.search` mutation would open a
        # false-label-add class on any "see the GATEKEEPER-ACTION: convention"
        # prose. This line makes `.match`=False but `.search`=True, so the
        # mutation is caught (unlike the backtick-no-colon case above where both
        # are False).
        cmts = [_c("Please follow the GATEKEEPER-ACTION: convention here", 3600)]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))
        # same for READY-FOR-REVIEW
        cmts2 = [_c("As noted the READY-FOR-REVIEW: step comes later", 3600)]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts2, cs._GK_READY_MARKER_RE, NOW, WINDOW))

    def test_fenced_marker_paste_is_not_a_marker(self):
        cmts = [_c("```\nGATEKEEPER-ACTION: example\n```", 3600)]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))

    def test_unparsable_createdAt_is_not_in_window(self):
        cmts = [{"body": "GATEKEEPER-ACTION: x", "createdAt": "garbage"}]
        self.assertFalse(cs._gk_proper_marker_in_window(
            cmts, cs._GK_PROPER_MARKER_RE, NOW, WINDOW))


class HandoffDecide(unittest.TestCase):
    """`_gk_comment_handoff_decide(marker_in_window, currently_labeled,
    handoff_flow, ever_labeled)` — biased HARD to SILENCE."""

    def _d(self, **kw):
        base = dict(marker_in_window=True, currently_labeled=False,
                    handoff_flow=False, ever_labeled=False)
        base.update(kw)
        return cs._gk_comment_handoff_decide(**base)

    def test_recent_never_labeled_proper_marker_is_a_handoff(self):
        ok, reason = self._d()
        self.assertTrue(ok, reason)

    def test_no_marker_in_window_never_handoff(self):
        ok, reason = self._d(marker_in_window=False)
        self.assertFalse(ok)
        self.assertIn("window", reason)

    def test_currently_labeled_never_handoff(self):
        ok, reason = self._d(currently_labeled=True)
        self.assertFalse(ok)

    def test_downstream_flow_label_never_handoff(self):
        ok, reason = self._d(handoff_flow=True)
        self.assertFalse(ok)

    def test_ever_labeled_never_handoff(self):
        # processed <48h ago, label removed — the marker is fresh but the
        # timeline proves it WAS labeled → not an orphan
        ok, reason = self._d(ever_labeled=True)
        self.assertFalse(ok)

    def test_timeline_undeterminable_fails_safe(self):
        ok, reason = self._d(ever_labeled=None)
        self.assertFalse(ok)


class _Rec:
    def __init__(self, status="labeled"):
        self.calls = []
        self.status = status

    def __call__(self, root, num, label):
        self.calls.append((root, num, label))
        return self.status


class _Send:
    def __init__(self):
        self.calls = []

    def __call__(self, body, dedup_key=None, dry_run=False, project=None):
        self.calls.append((body, dedup_key, project))
        return True


def _run(handoffs, rec=None, send=None, roots=None, state=None, now=NOW,
         seen=None, dry_run=False):
    rec = rec if rec is not None else _Rec()
    send = send if send is not None else _Send()
    roots = roots if roots is not None else {
        "/home/gatekeeper/devel/odoo-erp": "odoo-erp"}
    st = state if state is not None else {}
    if seen is not None:
        st["gkorphan"] = {"last_check": 0, "seen": dict(seen)}
    with mock.patch.object(watchdog, "list_claude_panes", lambda *a, **k: []), \
         mock.patch.object(cs, "_cache_repo_roots", lambda *a, **k: roots), \
         mock.patch.object(cs, "_repo_in_cross_stream_flow", lambda *a, **k: True):
        logs = cs.gk_orphan_marker_sweep(
            now, run=None, state=st, send_fn=send, user="newlevel",
            dry_run=dry_run,
            gh_fetch=lambda root: [],                 # no MUTATED candidates
            handoff_fetch=lambda root: handoffs,
            handoff_apply=rec)
    return logs, st, rec, send


_HANDOFF = {"number": 4336, "target_label": "needs-gatekeeper",
            "marker_in_window": True, "currently_labeled": False,
            "handoff_flow": False, "ever_labeled": False}
_RFR = {"number": 3096, "target_label": "ready-for-review",
        "marker_in_window": True, "currently_labeled": False,
        "handoff_flow": False, "ever_labeled": False}
_PROCESSED = {"number": 10, "target_label": "needs-gatekeeper",
              "marker_in_window": True, "currently_labeled": False,
              "handoff_flow": False, "ever_labeled": True}


class SweepBehaviour(unittest.TestCase):
    def test_gatekeeper_action_handoff_is_reconciled_with_the_right_label(self):
        logs, st, rec, _ = _run([_HANDOFF])
        self.assertEqual(rec.calls,
                         [("/home/gatekeeper/devel/odoo-erp", 4336,
                           "needs-gatekeeper")])
        self.assertTrue(any("gk-handoff-reconcile handoff:odoo-erp#4336" in ln
                            for ln in logs), logs)

    def test_ready_for_review_handoff_uses_ready_for_review_label(self):
        logs, st, rec, _ = _run([_RFR])
        self.assertEqual(rec.calls,
                         [("/home/gatekeeper/devel/odoo-erp", 3096,
                           "ready-for-review")])

    def test_processed_then_cleared_is_not_reconciled(self):
        logs, _, rec, _ = _run([_PROCESSED])
        self.assertEqual(rec.calls, [])

    def test_dedup_does_not_re_reconcile(self):
        logs, _, rec, _ = _run([_HANDOFF], seen={"handoff:odoo-erp#4336": 1})
        self.assertEqual(rec.calls, [])

    def test_dry_run_does_not_latch_dedup(self):
        logs, st, rec, _ = _run([_HANDOFF], dry_run=True)
        self.assertEqual(rec.calls, [])
        self.assertNotIn("handoff:odoo-erp#4336",
                         (st.get("gkorphan") or {}).get("seen", {}))

    def test_label_failed_keeps_dedup_and_pings(self):
        rec = _Rec(status="label-failed")
        logs, st, _, send = _run([_HANDOFF], rec=rec)
        self.assertIn("handoff:odoo-erp#4336", st["gkorphan"]["seen"])
        self.assertEqual(len(send.calls), 1)

    def test_comment_failed_undoes_dedup(self):
        rec = _Rec(status="comment-failed")
        logs, st, _, send = _run([_HANDOFF], rec=rec)
        self.assertNotIn("handoff:odoo-erp#4336", st["gkorphan"]["seen"])
        self.assertEqual(send.calls, [])

    def test_no_handoff_fetch_skips_the_pass_entirely(self):
        # the wired=on convention: an existing #551-only caller (no
        # handoff_fetch) runs ONLY the mutated pass, zero new behaviour/network
        st = {}
        with mock.patch.object(watchdog, "list_claude_panes",
                               lambda *a, **k: []), \
             mock.patch.object(cs, "_cache_repo_roots",
                               lambda *a, **k: {"/home/gatekeeper/x": "x"}), \
             mock.patch.object(cs, "_repo_in_cross_stream_flow",
                               lambda *a, **k: True):
            logs = cs.gk_orphan_marker_sweep(
                NOW, run=None, state=st, send_fn=_Send(), user="newlevel",
                gh_fetch=lambda root: [])
        self.assertFalse(any("gk-handoff" in ln for ln in logs), logs)


class FetchParsing(unittest.TestCase):
    """`_fetch_gk_comment_handoffs` — search → per-candidate view → facts.
    Offline: `subprocess.run` mocked, NEVER real GitHub."""

    def test_fail_safe_None_on_search_error(self):
        cp = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        with mock.patch("subprocess.run", return_value=cp):
            self.assertIsNone(
                cs._fetch_gk_comment_handoffs("/r", None, NOW, WINDOW))

    def test_happy_path_builds_handoff_fact(self):
        import json
        proper = _c("GATEKEEPER-ACTION: over #4336", 3600)

        def fake_run(argv, **kw):
            joined = " ".join(argv)
            if "list" in argv and "--search" in argv:
                # the GATEKEEPER-ACTION search returns #4336; the
                # READY-FOR-REVIEW search returns nothing.
                if "GATEKEEPER-ACTION" in joined:
                    return subprocess.CompletedProcess(
                        argv, 0, json.dumps([{"number": 4336, "title": "t"}]), "")
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            if "view" in argv:
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(
                        {"number": 4336, "title": "t", "labels": [],
                         "comments": [proper]}), "")
            if "api" in argv:                 # timeline → never labeled
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.CompletedProcess(argv, 0, "[]", "")

        with mock.patch("subprocess.run", side_effect=fake_run):
            out = cs._fetch_gk_comment_handoffs("/r", None, NOW, WINDOW)
        by_num = {d["number"]: d for d in out}
        self.assertIn(4336, by_num)
        self.assertEqual(by_num[4336]["target_label"], "needs-gatekeeper")
        self.assertTrue(by_num[4336]["marker_in_window"])
        self.assertFalse(by_num[4336]["currently_labeled"])
        self.assertIs(by_num[4336]["ever_labeled"], False)


if __name__ == "__main__":
    unittest.main()
