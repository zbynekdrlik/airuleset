"""Behaviour test for the orphaned gk hand-off marker backstop (#551, job 36).

The miva1 incident (odoo-erp #3244): a stream HAND-WROTE a MUTATED
`GATEKEEPER-ACTION (spresnenie …):` hand-off marker COMMENT (a parenthetical
before the colon) instead of using `airuleset.py gk-request`. The repo
auto-label workflow matches only line-start `GATEKEEPER-ACTION:`, so no
`needs-gatekeeper` label landed; job 11's queries never scan comments; the
hand-off was invisible to every layer and the stream parked for hours.

The backstop is DELIBERATELY narrow and biased to SILENCE (never a false
accusation — the ticket's hard requirement): a candidate is an orphan only
when a MUTATED marker exists AND no PROPER `GATEKEEPER-ACTION:` sibling exists
AND `needs-gatekeeper` is not currently a label AND was NEVER in the timeline
AND the title carries no GA prefix. The measured live corpus (60 candidates,
all containing the token in comment history) proves a naive rule would
false-accuse ~44 processed tickets — hence the ANDed gate.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog                     # noqa: E402
import watchdog.cross_stream as cs  # noqa: E402


# --------------------------------------------------------------------------- #
# The PURE marker classifier.
# --------------------------------------------------------------------------- #
class MarkerKinds(unittest.TestCase):
    def test_miva1_mutated_parenthetical_is_mutated_not_proper(self):
        # the exact incident shape: parenthetical BEFORE the colon.
        body = "GATEKEEPER-ACTION (spresnenie k #3244): prosím over gate wiring"
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertTrue(has_mutated)
        self.assertFalse(has_proper)

    def test_proper_marker_is_proper_not_mutated(self):
        body = "GATEKEEPER-ACTION: review the ready hand-off branch"
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)
        self.assertTrue(has_proper)

    def test_trailing_space_before_colon_counts_as_proper(self):
        # generous proper detection biases toward SILENCE.
        has_mutated, has_proper = cs._gk_marker_kinds(["GATEKEEPER-ACTION : x"])
        self.assertTrue(has_proper)
        self.assertFalse(has_mutated)

    def test_backtick_prose_mention_is_neither(self):
        # the #4128 false-positive class: a prose mention, NOT a line-start
        # marker — must be ignored (token not at a genuine line start).
        body = ("Implemented #4128; the outstanding `GATEKEEPER-ACTION`s "
                "(agent + MCP deploy) are below:")
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)
        self.assertFalse(has_proper)

    def test_mid_sentence_token_is_not_a_marker(self):
        body = "see the GATEKEEPER-ACTION request above: it needs a label"
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)
        self.assertFalse(has_proper)

    def test_newline_does_not_span_the_marker_across_lines(self):
        # regression for the `\s`-matches-newline bug: a token on its own that
        # is NOT a line-start marker must not be joined to a colon on a LATER
        # line. Here line 1 mentions the token mid-prose, line 3 ends in a
        # colon — a `\s`-spanning regex falsely matched; `[ \t]` must not.
        body = "we discussed GATEKEEPER-ACTION handling\n\nnext steps below:"
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)
        self.assertFalse(has_proper)

    def test_both_kinds_across_different_comments(self):
        # a proper marker anywhere means has_proper (a processed hand-off);
        # the mutated one on another line is still flagged has_mutated.
        bodies = ["GATEKEEPER-ACTION: proper request",
                  "GATEKEEPER-ACTION (spresnenie): follow-up"]
        has_mutated, has_proper = cs._gk_marker_kinds(bodies)
        self.assertTrue(has_mutated)
        self.assertTrue(has_proper)

    def test_indented_mutated_marker_still_matches(self):
        has_mutated, has_proper = cs._gk_marker_kinds(
            ["   GATEKEEPER-ACTION (x): indented"])
        self.assertTrue(has_mutated)

    def test_no_marker_empty(self):
        has_mutated, has_proper = cs._gk_marker_kinds(
            ["nothing here", "", None])
        self.assertFalse(has_mutated)
        self.assertFalse(has_proper)

    def test_col0_prose_status_line_is_not_a_marker(self):
        # adversarial review F1: a benign col-0 status note that opens with the
        # token and merely CONTAINS a colon (no bracketed annotation) must NOT
        # read as a mutated marker — else it is a reachable false accusation.
        for prose in ("GATEKEEPER-ACTION tasks remaining: agent deploy, MCP",
                      "GATEKEEPER-ACTION items to finish before release: two"):
            has_mutated, has_proper = cs._gk_marker_kinds([prose])
            self.assertFalse(has_mutated, prose)
            self.assertFalse(has_proper, prose)

    def test_bracket_variant_is_mutated(self):
        # a square-bracket annotation is the same hand-off shape as a paren.
        has_mutated, has_proper = cs._gk_marker_kinds(
            ["GATEKEEPER-ACTION [urgent]: restart the stuck queue"])
        self.assertTrue(has_mutated)
        self.assertFalse(has_proper)

    def test_fenced_marker_paste_is_not_a_marker(self):
        # adversarial review F2: a marker pasted inside a ```-fenced block is a
        # documentation/incident write-up, not a live hand-off.
        body = ("Toto je incident writeup:\n\n```\n"
                "GATEKEEPER-ACTION (spresnenie): deploy X\n```\n\nhotovo")
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)
        self.assertFalse(has_proper)

    def test_tilde_fenced_marker_paste_is_not_a_marker(self):
        body = "~~~\nGATEKEEPER-ACTION (x): do it\n~~~"
        has_mutated, has_proper = cs._gk_marker_kinds([body])
        self.assertFalse(has_mutated)


# --------------------------------------------------------------------------- #
# The PURE decider (facts-in / verdict-out).
# --------------------------------------------------------------------------- #
class OrphanDecide(unittest.TestCase):
    def test_synthetic_orphan_is_flagged(self):
        # the miva1 signature: mutated marker, no proper sibling, not labeled,
        # never labeled, no GA-title.
        is_orphan, reason = cs._gk_orphan_decide(
            has_mutated=True, has_proper=False, currently_labeled=False,
            ga_title=False, ever_labeled=False)
        self.assertTrue(is_orphan, reason)
        self.assertEqual(reason, "orphaned-mutated-marker")

    def test_no_mutated_marker_never_orphan(self):
        is_orphan, reason = cs._gk_orphan_decide(
            False, False, False, False, False)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "no-mutated-marker")

    def test_has_proper_marker_never_orphan(self):
        # a proper marker means the workflow WOULD have labeled it (processed).
        is_orphan, reason = cs._gk_orphan_decide(
            True, True, False, False, False)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "has-proper-marker")

    def test_handoff_flow_never_orphan(self):
        # #4128: a ready-for-review / needs-acceptance ticket has already
        # engaged the gatekeeper via the review/acceptance flow.
        is_orphan, reason = cs._gk_orphan_decide(
            True, False, False, False, False, handoff_flow=True)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "in-gatekeeper-flow")

    def test_ga_title_never_orphan(self):
        is_orphan, reason = cs._gk_orphan_decide(
            True, False, False, True, False)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "ga-title-already-discoverable")

    def test_currently_labeled_never_orphan(self):
        # the resolved #3244 fixture shape (it now carries needs-gatekeeper).
        is_orphan, reason = cs._gk_orphan_decide(
            True, False, True, False, True)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "already-labeled")

    def test_ever_labeled_never_orphan(self):
        # a mutated-marker ticket gk manually labeled then cleared is NOT an
        # orphan (the paginated-timeline discriminator).
        is_orphan, reason = cs._gk_orphan_decide(
            True, False, False, False, True)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "was-labeled-then-cleared")

    def test_timeline_undeterminable_fails_safe_to_not_orphan(self):
        # ever_labeled=None (timeline unreadable) → never accuse.
        is_orphan, reason = cs._gk_orphan_decide(
            True, False, False, False, None)
        self.assertFalse(is_orphan)
        self.assertEqual(reason, "timeline-undeterminable")


# --------------------------------------------------------------------------- #
# The JOB, driven with an injected fetch + a recording reconcile.
# --------------------------------------------------------------------------- #
class _Recorder:
    """Injected reconcile stub — returns the TRI-STATE the real
    `_apply_gk_orphan_reconcile` returns ("labeled"/"label-failed"/
    "comment-failed")."""
    def __init__(self, status="labeled"):
        self.calls = []
        self.status = status

    def __call__(self, root, num):
        self.calls.append((root, num))
        return self.status


class _SendRec:
    def __init__(self):
        self.calls = []

    def __call__(self, body, dedup_key=None, dry_run=False, project=None):
        self.calls.append((body, dedup_key, project))
        return True


_ORPHAN = {"number": 3244, "has_mutated": True, "has_proper": False,
           "handoff_flow": False, "currently_labeled": False,
           "ga_title": False, "ever_labeled": False}
_PROCESSED = {"number": 10, "has_mutated": True, "has_proper": True,
              "handoff_flow": False, "currently_labeled": False,
              "ga_title": False, "ever_labeled": True}
# The #4128 live false-positive shape: a GENUINE mutated marker on a ticket
# already worked via the review/acceptance flow (needs-acceptance) — must be
# SILENT ("in-gatekeeper-flow"), never reconciled.
_FLOW = {"number": 4128, "has_mutated": True, "has_proper": False,
         "handoff_flow": True, "currently_labeled": False,
         "ga_title": False, "ever_labeled": False}


def _run(candidates, rec=None, send=None, roots=None, state=None, now=10 ** 9,
         seen=None, dry_run=False):
    rec = rec if rec is not None else _Recorder()
    send = send if send is not None else _SendRec()
    roots = roots if roots is not None else {
        "/home/gatekeeper/devel/odoo-erp": "odoo-erp"}
    st = state if state is not None else {}
    if seen is not None:
        st["gkorphan"] = {"last_check": 0, "seen": dict(seen)}
    with mock.patch.object(watchdog, "list_claude_panes", lambda *a, **k: []), \
         mock.patch.object(cs, "_cache_repo_roots", lambda *a, **k: roots):
        logs = cs.gk_orphan_marker_sweep(
            now, run=None, state=st, send_fn=send, user="newlevel",
            dry_run=dry_run, gh_fetch=lambda root: candidates, apply_fn=rec)
    return logs, st, rec, send


class JobBehaviour(unittest.TestCase):
    def test_synthetic_orphan_is_reconciled(self):
        logs, st, rec, _ = _run([_ORPHAN])
        self.assertEqual(rec.calls, [("/home/gatekeeper/devel/odoo-erp", 3244)])
        self.assertTrue(any("gk-orphan-reconcile odoo-erp#3244 (labeled" in ln
                            for ln in logs), logs)
        self.assertIn("odoo-erp#3244", st["gkorphan"]["seen"])

    def test_resolved_fixture_stays_silent(self):
        # #3244 as it is NOW (has proper markers + labeled + ever-labeled) →
        # SILENT, no reconcile.
        resolved = {"number": 3244, "has_mutated": True, "has_proper": True,
                    "currently_labeled": True, "ga_title": False,
                    "ever_labeled": True}
        logs, _, rec, _ = _run([resolved])
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-orphan-skip odoo-erp#3244 (has-proper-marker)"
                            in ln for ln in logs), logs)

    def test_processed_then_cleared_is_not_reconciled(self):
        logs, _, rec, _ = _run([_PROCESSED])
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-orphan-skip odoo-erp#10 (has-proper-marker)"
                            in ln for ln in logs), logs)

    def test_dedup_does_not_re_reconcile(self):
        logs, _, rec, _ = _run([_ORPHAN], seen={"odoo-erp#3244": 1})
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-orphan-already odoo-erp#3244" in ln
                            for ln in logs), logs)

    def test_flow_ticket_is_not_reconciled(self):
        # #4128: a genuine mutated marker on a review/acceptance-flow ticket →
        # SILENT (the gatekeeper already engaged).
        logs, _, rec, _ = _run([_FLOW])
        self.assertEqual(rec.calls, [])
        self.assertTrue(any("gk-orphan-skip odoo-erp#4128 (in-gatekeeper-flow)"
                            in ln for ln in logs), logs)

    def test_label_failed_keeps_dedup_pings_and_does_not_repost(self):
        # F2: comment posted (durable record) but label failed → KEEP the dedup
        # (never re-post the comment) + ping once.
        rec = _Recorder(status="label-failed")
        logs, st, _, send = _run([_ORPHAN], rec=rec)
        self.assertEqual(len(rec.calls), 1)
        self.assertIn("odoo-erp#3244", st["gkorphan"]["seen"])   # KEPT (no re-post)
        self.assertEqual(len(send.calls), 1)                     # pinged once
        self.assertIn("orphaned gk hand-off", send.calls[0][0])
        self.assertTrue(any("label add failed" in ln for ln in logs), logs)
        # a SECOND sweep does NOT re-reconcile (would re-post the comment)
        logs2, _, rec2, send2 = _run(
            [_ORPHAN], seen={"odoo-erp#3244": 1})
        self.assertEqual(rec2.calls, [])
        self.assertEqual(send2.calls, [])

    def test_comment_failed_undoes_dedup_for_retry_no_ping(self):
        # nothing posted → safe to undo the dedup and retry; no ping (nothing
        # to surface yet).
        rec = _Recorder(status="comment-failed")
        logs, st, _, send = _run([_ORPHAN], rec=rec)
        self.assertEqual(len(rec.calls), 1)
        self.assertNotIn("odoo-erp#3244", st["gkorphan"]["seen"])  # undone
        self.assertEqual(send.calls, [])
        self.assertTrue(any("gk-orphan-reconcile-failed" in ln
                            and "comment did not post" in ln for ln in logs),
                        logs)

    def test_reduced_stream_box_never_reconciles(self):
        # montalu1 (renamed from montalu, #537 flip — the bare name left
        # _REDUCED_STREAM_USERS, so the OLD path would now read as a
        # full-authority box and reconcile, inverting this test's intent).
        logs, _, rec, _ = _run([_ORPHAN],
                               roots={"/home/montalu1/devel/odoo-erp": "odoo-erp"})
        self.assertEqual(rec.calls, [])

    def test_non_cross_stream_repo_never_reconciles(self):
        logs, _, rec, _ = _run(
            [_ORPHAN],
            roots={"/home/gatekeeper/devel/some-other-repo": "some-other-repo"})
        self.assertEqual(rec.calls, [])

    def test_fetch_error_keeps_state(self):
        logs, _, rec, _ = _run(None)                 # fetch returned None
        self.assertEqual(rec.calls, [])

    def test_cadence_gate_skips_when_recently_checked(self):
        st = {"gkorphan": {"last_check": 10 ** 9 - 5, "seen": {}}}
        logs, _, rec, _ = _run([_ORPHAN], state=st, now=10 ** 9)
        self.assertEqual(rec.calls, [])
        self.assertEqual(logs, [])

    def test_dry_run_does_not_latch_dedup(self):
        # #516 F1: a one-shot latch must NOT persist under a dry run, else the
        # real reconcile is suppressed forever on later timer sweeps.
        logs, st, rec, _ = _run([_ORPHAN], dry_run=True)
        self.assertEqual(rec.calls, [])              # nothing mutated
        self.assertNotIn("odoo-erp#3244", st["gkorphan"].get("seen", {}))
        self.assertTrue(any("gk-orphan-reconcile odoo-erp#3244 (dry-run)" in ln
                            for ln in logs), logs)

    def test_every_candidate_verdict_is_logged(self):
        # #486 explicit-decision-log: even a silent candidate gets a line.
        logs, _, _, _ = _run([_PROCESSED, _ORPHAN])
        self.assertTrue(any("gk-orphan-skip odoo-erp#10" in ln for ln in logs))
        self.assertTrue(any("gk-orphan-reconcile odoo-erp#3244" in ln
                            for ln in logs))


# --------------------------------------------------------------------------- #
# The REAL reconcile apply — the comment-then-label tri-state (F2 was invisible
# to the injected-stub job tests: the real comment-then-label split must be
# exercised so a persistent label failure is proven to post the comment ONCE).
# --------------------------------------------------------------------------- #
class RealApply(unittest.TestCase):
    def _run_apply(self, returncodes, dry_run=False):
        calls = []
        seq = iter(returncodes)

        def fake_run(argv, **kw):
            calls.append(list(argv))
            return SimpleNamespace(returncode=next(seq), stdout="", stderr="")

        with mock.patch.object(cs, "_gh_env", lambda *a, **k: {}), \
             mock.patch("subprocess.run", fake_run):
            result = cs._apply_gk_orphan_reconcile("/root", 42, dry_run=dry_run)
        return result, calls

    def test_comment_and_label_ok_returns_labeled(self):
        result, calls = self._run_apply([0, 0])
        self.assertEqual(result, "labeled")
        self.assertEqual(len(calls), 2)
        self.assertIn("comment", calls[0])
        self.assertIn("--add-label", calls[1])

    def test_label_fail_returns_label_failed_and_posts_comment_once(self):
        result, calls = self._run_apply([0, 1])   # comment ok, label fails
        self.assertEqual(result, "label-failed")
        self.assertEqual(sum(1 for c in calls if "comment" in c), 1)  # ONCE (F2)

    def test_comment_fail_returns_comment_failed_no_label_attempt(self):
        result, calls = self._run_apply([1])       # comment fails
        self.assertEqual(result, "comment-failed")
        self.assertEqual(len(calls), 1)            # no label edit attempted
        self.assertNotIn("--add-label",
                         [x for c in calls for x in c])

    def test_dry_run_mutates_nothing(self):
        result, calls = self._run_apply([], dry_run=True)
        self.assertEqual(result, "labeled")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
