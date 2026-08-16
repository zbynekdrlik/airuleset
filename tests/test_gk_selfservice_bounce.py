"""Behaviour test for the gk self-service auto-bounce (#516, watchdog job 31).

Side B of the two-sided self-service gate: the gatekeeper-side MECHANICAL
backstop. On a supervisor box, an open needs-gatekeeper ACTION request that
carries NO `Self-service-checked:` line and is attributable to a reduced sub-dev
stream (`handed-by:<stream>`) is AUTO-BOUNCED back to that stream (template
comment + prio:bounce + stream:<stream> + drop needs-gatekeeper). It is
deliberately mechanical/conservative — it NEVER classifies prose:
  - a request WITH a Self-service-checked line is left for the gatekeeper's
    manual triage (a line-present-but-pure-read judgement is not mechanized),
  - a code-review hand-off (stream:/ready-for-review) is never bounced (rule 8),
  - a request not attributable to a reduced stream is never bounced,
  - an already-bounced request is never re-bounced.
Every candidate's verdict is logged (the #486 explicit-decision-log direction).
"""

import unittest
from unittest import mock

import watchdog
import watchdog.cross_stream as cs


# --------------------------------------------------------------------------- #
# The PURE decision (facts-in / verdict-out).
# --------------------------------------------------------------------------- #
class DecisionPure(unittest.TestCase):
    def test_no_line_handed_by_stream_bounces(self):
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper", "handed-by:montalu"], has_line=False,
            origin_stream="montalu")
        self.assertTrue(should, reason)
        self.assertEqual(reason, "no-self-service-line")

    def test_line_present_is_left_for_manual_triage(self):
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper", "handed-by:montalu"], has_line=True,
            origin_stream="montalu")
        self.assertFalse(should)
        self.assertEqual(reason, "has-self-service-line")

    def test_review_handoff_stream_label_never_bounced(self):
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper", "stream:david2"], has_line=False,
            origin_stream=None)
        self.assertFalse(should)
        self.assertEqual(reason, "review-handoff:stream-label")

    def test_review_handoff_ready_for_review_never_bounced(self):
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper", "ready-for-review", "handed-by:montalu"],
            has_line=False, origin_stream="montalu")
        self.assertFalse(should)
        self.assertEqual(reason, "review-handoff:ready-for-review")

    def test_already_bounced_never_re_bounced(self):
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper", "handed-by:montalu", "prio:bounce"],
            has_line=False, origin_stream="montalu")
        self.assertFalse(should)
        self.assertEqual(reason, "already-bounced")

    def test_origin_not_attributable_never_bounced(self):
        # a bare needs-gatekeeper with no handed-by (e.g. the maintainer's own
        # test request, or a read-only-fork GATEKEEPER-ACTION request) is NOT
        # bounced — the safe direction (never touch the maintainer's own).
        should, reason = cs._selfservice_bounce_decide(
            ["needs-gatekeeper"], has_line=False, origin_stream=None)
        self.assertFalse(should)
        self.assertEqual(reason, "origin-not-attributable-to-a-reduced-stream")


class OriginResolution(unittest.TestCase):
    def test_handed_by_reduced_stream(self):
        self.assertEqual(
            cs._origin_reduced_stream(["needs-gatekeeper", "handed-by:david2"]),
            "david2")

    def test_handed_by_full_authority_is_not_a_stream(self):
        # handed-by:newlevel — not a reduced stream, so no attribution.
        self.assertIsNone(cs._origin_reduced_stream(["handed-by:newlevel"]))

    def test_stream_label_is_not_an_origin_marker(self):
        # stream:<x> is the REVIEW ownership primitive, NOT the action-request
        # origin marker; it must not be read as an origin here.
        self.assertIsNone(cs._origin_reduced_stream(["stream:montalu"]))


# --------------------------------------------------------------------------- #
# The JOB, driven with an injected fetch + a recording bounce_apply.
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, root, num, stream):
        self.calls.append((root, num, stream))
        return self.ok


def _run(candidates, apply_rec, roots=None, state=None, now=10 ** 9,
         seen=None):
    """Drive gk_selfservice_bounce with list_claude_panes/_cache_repo_roots
    patched to a single supervisor-box cross-stream repo, an injected fetch
    returning `candidates`, and an injected bounce_apply recorder."""
    roots = roots if roots is not None else {
        "/home/gatekeeper/devel/odoo-erp": "odoo-erp"}
    st = state if state is not None else {}
    if seen is not None:
        st["gk_selfservice_bounce"] = {"last_check": 0, "seen": dict(seen)}
    with mock.patch.object(watchdog, "list_claude_panes",
                           lambda *a, **k: []), \
         mock.patch.object(cs, "_cache_repo_roots", lambda *a, **k: roots):
        logs = cs.gk_selfservice_bounce(
            now, run=None, state=st, user="newlevel",
            gh_fetch=lambda root: candidates,
            bounce_apply=apply_rec)
    return logs, st


class JobBehaviour(unittest.TestCase):
    def test_no_line_request_is_bounced(self):
        rec = _Recorder()
        logs, st = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec)
        self.assertEqual(rec.calls,
                         [("/home/gatekeeper/devel/odoo-erp", 3316, "montalu")])
        self.assertTrue(any("gk-selfservice-bounce odoo-erp#3316 -> stream:montalu"
                            in ln for ln in logs), logs)
        # dedup recorded
        self.assertIn("odoo-erp#3316", st["gk_selfservice_bounce"]["seen"])

    def test_request_with_line_is_not_bounced(self):
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 7, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": True, "origin_stream": "montalu"}], rec)
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-selfservice-skip odoo-erp#7 (has-self-service-line)"
                            in ln for ln in logs), logs)

    def test_review_handoff_is_not_bounced(self):
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 9, "labels": ["needs-gatekeeper", "stream:david2"],
              "has_line": False, "origin_stream": None}], rec)
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("review-handoff:stream-label" in ln for ln in logs),
                        logs)

    def test_already_bounced_is_not_bounced(self):
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 5, "labels": ["needs-gatekeeper", "handed-by:montalu",
                                      "prio:bounce"],
              "has_line": False, "origin_stream": "montalu"}], rec)
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("already-bounced" in ln for ln in logs), logs)

    def test_dedup_does_not_re_bounce_a_seen_ticket(self):
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec,
            seen={"odoo-erp#3316": 1})
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-selfservice-already odoo-erp#3316" in ln
                            for ln in logs), logs)

    def test_reduced_stream_box_never_bounces(self):
        # a requester (reduced-stream) home is skipped entirely.
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec,
            roots={"/home/montalu/devel/odoo-erp": "odoo-erp"})
        self.assertEqual(rec.calls, [])

    def test_non_cross_stream_repo_never_bounces(self):
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 1, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec,
            roots={"/home/gatekeeper/devel/some-other-repo": "some-other-repo"})
        self.assertEqual(rec.calls, [])

    def test_apply_failure_undoes_dedup_for_retry(self):
        rec = _Recorder(ok=False)
        logs, st = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec)
        self.assertEqual(len(rec.calls), 1)                 # it TRIED to bounce
        self.assertNotIn("odoo-erp#3316", st["gk_selfservice_bounce"]["seen"])
        self.assertTrue(any("gk-selfservice-bounce-failed" in ln for ln in logs),
                        logs)

    def test_fetch_error_keeps_state(self):
        rec = _Recorder()
        logs, st = _run(None, rec)                           # fetch returned None
        self.assertEqual(rec.calls, [])

    def test_cadence_gate_skips_when_recently_checked(self):
        rec = _Recorder()
        st = {"gk_selfservice_bounce": {"last_check": 10 ** 9 - 5, "seen": {}}}
        logs, _ = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu"],
              "has_line": False, "origin_stream": "montalu"}], rec,
            state=st, now=10 ** 9)          # only 5s since last check < 1800
        self.assertEqual(rec.calls, [])
        self.assertEqual(logs, [])


# --------------------------------------------------------------------------- #
# _apply_selfservice_bounce: verify the concrete gh mutations it performs.
# --------------------------------------------------------------------------- #
class ApplyBounce(unittest.TestCase):
    def _fake_run(self, ok_comment=True):
        calls = []

        class _R:
            def __init__(self, rc):
                self.returncode = rc
                self.stdout = ""
                self.stderr = ""

        def run(argv, **kw):
            calls.append(argv)
            # the comment is calls[0]; make it succeed/fail per ok_comment.
            if "comment" in argv:
                return _R(0 if ok_comment else 1)
            return _R(0)
        return run, calls

    def test_posts_comment_then_labels_then_removes_needs_gatekeeper(self):
        run, calls = self._fake_run()
        with mock.patch.object(cs, "_gh_env", lambda *a, **k: {}), \
             mock.patch("subprocess.run", run):
            ok = cs._apply_selfservice_bounce(
                "/repo", 3316, "montalu", home=None, dry_run=False)
        self.assertTrue(ok)
        # comment first, carrying the template (with the stream filled in)
        self.assertIn("comment", calls[0])
        body = calls[0][calls[0].index("--body") + 1]
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD: montalu", body)
        self.assertIn("Self-service-checked", body)
        # the label ops: add prio:bounce, add stream:montalu, remove needs-gatekeeper
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("--add-label prio:bounce" in j for j in joined), joined)
        self.assertTrue(any("--add-label stream:montalu" in j for j in joined),
                        joined)
        self.assertTrue(
            any("--remove-label needs-gatekeeper" in j for j in joined), joined)
        # needs-gatekeeper removal is LAST (leaves the ticket discoverable on a
        # partial failure)
        self.assertIn("--remove-label", calls[-1])

    def test_comment_failure_returns_false_and_makes_no_label_edits(self):
        run, calls = self._fake_run(ok_comment=False)
        with mock.patch.object(cs, "_gh_env", lambda *a, **k: {}), \
             mock.patch("subprocess.run", run):
            ok = cs._apply_selfservice_bounce(
                "/repo", 3316, "montalu", home=None, dry_run=False)
        self.assertFalse(ok)
        self.assertEqual(len(calls), 1)          # only the failed comment attempt

    def test_dry_run_makes_no_calls(self):
        run, calls = self._fake_run()
        with mock.patch("subprocess.run", run):
            ok = cs._apply_selfservice_bounce(
                "/repo", 3316, "montalu", home=None, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
