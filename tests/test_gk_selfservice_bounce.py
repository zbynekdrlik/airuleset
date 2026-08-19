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

import json as _json
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
         seen=None, dry_run=False):
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
            now, run=None, state=st, user="newlevel", dry_run=dry_run,
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
        # a requester (reduced-stream) home is skipped entirely. #537: the
        # montalu box now runs as montalu1 (/home/montalu1), which _bounce_quals
        # / _gkreq_supervisor_root recognise as a reduced stream via its home.
        rec = _Recorder()
        logs, _ = _run(
            [{"number": 3316, "labels": ["needs-gatekeeper", "handed-by:montalu1"],
              "has_line": False, "origin_stream": "montalu1"}], rec,
            roots={"/home/montalu1/devel/odoo-erp": "odoo-erp"})
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

    def test_dry_run_does_not_latch_dedup_so_a_later_real_sweep_bounces(self):
        # #516 review F1: a diagnostic --dry-run must NOT persist the one-shot
        # `seen` latch, else the next real timer sweep skips the ticket forever.
        cand = [{"number": 3316,
                 "labels": ["needs-gatekeeper", "handed-by:montalu"],
                 "has_line": False, "origin_stream": "montalu"}]
        st = {}
        rec_dry = _Recorder()
        _run(cand, rec_dry, state=st, now=10 ** 9, dry_run=True)
        self.assertEqual(rec_dry.calls, [])                      # nothing bounced
        # the dry run must NOT have latched the dedup
        self.assertNotIn("odoo-erp#3316",
                         st.get("gk_selfservice_bounce", {}).get("seen", {}))
        # a REAL sweep later (past the cadence interval) DOES bounce it
        rec_real = _Recorder()
        logs, st = _run(cand, rec_real, state=st, now=10 ** 9 + 2000)
        self.assertEqual(rec_real.calls,
                         [("/home/gatekeeper/devel/odoo-erp", 3316, "montalu")])



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


# --------------------------------------------------------------------------- #
# _fetch_gk_action_requests: the REAL fetch's parsing (#516 review F5 coverage).
# subprocess.run is patched to return canned gh JSON per argv.
# --------------------------------------------------------------------------- #
class _R:
    def __init__(self, rc, stdout=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


class RealFetch(unittest.TestCase):
    def _fake_gh(self, view_by_num, nglist=None, ga_list=None, fail=None):
        def run(argv, **kw):
            if fail == "list" and "list" in argv:
                return _R(1)
            if "list" in argv:
                # distinguish the two list queries by their --search arg
                search = argv[argv.index("--search") + 1]
                if "GATEKEEPER-ACTION" in search:
                    return _R(0, _json.dumps(ga_list or []))
                return _R(0, _json.dumps(nglist or []))
            if "view" in argv:
                num = argv[argv.index("view") + 1]
                if fail == "view":
                    return _R(1)
                return _R(0, _json.dumps(view_by_num.get(num, {})))
            return _R(1)
        return run

    def test_parses_labels_body_and_line_in_a_COMMENT(self):
        # has_line must be True when the Self-service-checked line is in a
        # COMMENT (not the body) — the --issue-mode gk-request shape.
        # #537: montalu renamed to montalu1, so a current handed-by carries the
        # numbered name (_origin_reduced_stream reads it from _REDUCED_STREAM_USERS).
        view = {"3316": {"number": 3316,
                         "labels": [{"name": "needs-gatekeeper"},
                                    {"name": "handed-by:montalu1"}],
                         "body": "Zaseknutá fronta.",
                         "comments": [{"body": "GATEKEEPER-ACTION: reštart. "
                                       "Self-service-checked: čítal som z PROD "
                                       "kópie; potrebujem živý reštart."}]}}
        with mock.patch("subprocess.run",
                        self._fake_gh(view, nglist=[{"number": 3316}])), \
             mock.patch.object(cs, "_gh_env", lambda *a, **k: {}):
            out = cs._fetch_gk_action_requests("/repo")
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]["has_line"])
        self.assertEqual(out[0]["origin_stream"], "montalu1")

    def test_no_line_anywhere_is_has_line_false(self):
        view = {"5": {"number": 5,
                      "labels": [{"name": "needs-gatekeeper"},
                                 {"name": "handed-by:david2"}],
                      "body": "Prečítaj počet riadkov.",
                      "comments": [{"body": "ešte raz prosím"}]}}
        with mock.patch("subprocess.run",
                        self._fake_gh(view, nglist=[{"number": 5}])), \
             mock.patch.object(cs, "_gh_env", lambda *a, **k: {}):
            out = cs._fetch_gk_action_requests("/repo")
        self.assertFalse(out[0]["has_line"])
        self.assertEqual(out[0]["origin_stream"], "david2")

    def test_gatekeeper_action_title_filter_excludes_mere_mention(self):
        # the in:title fallback client-side keeps ONLY titles that literally
        # START with GATEKEEPER-ACTION: (GitHub search tokenizes, #1768).
        view = {"9": {"number": 9, "labels": [{"name": "handed-by:montalu"}],
                      "body": "x", "comments": []}}
        ga = [{"number": 9, "title": "GATEKEEPER-ACTION: do X"},
              {"number": 99, "title": "a gatekeeper action runner note"}]
        with mock.patch("subprocess.run", self._fake_gh(view, ga_list=ga)), \
             mock.patch.object(cs, "_gh_env", lambda *a, **k: {}):
            out = cs._fetch_gk_action_requests("/repo")
        nums = {c["number"] for c in out}
        self.assertIn(9, nums)
        self.assertNotIn(99, nums)          # mere-mention title excluded

    def test_fail_safe_none_on_gh_error(self):
        with mock.patch("subprocess.run", self._fake_gh({}, fail="list")), \
             mock.patch.object(cs, "_gh_env", lambda *a, **k: {}):
            self.assertIsNone(cs._fetch_gk_action_requests("/repo"))

    def test_candidate_cap_bounds_the_per_sweep_view_fetches(self):
        # F3: the fetch only VIEWs the first _SELFSERVICE_MAX_CANDIDATES.
        cap = cs._SELFSERVICE_MAX_CANDIDATES
        nglist = [{"number": n} for n in range(1, cap + 6)]
        view = {str(n): {"number": n, "labels": [{"name": "needs-gatekeeper"}],
                         "body": "x", "comments": []}
                for n in range(1, cap + 6)}
        viewed = []
        fake = self._fake_gh(view, nglist=nglist)

        def counting(argv, **kw):
            if "view" in argv:
                viewed.append(argv[argv.index("view") + 1])
            return fake(argv, **kw)
        with mock.patch("subprocess.run", counting), \
             mock.patch.object(cs, "_gh_env", lambda *a, **k: {}):
            out = cs._fetch_gk_action_requests("/repo")
        self.assertEqual(len(viewed), cap)          # never more than the cap
        self.assertEqual(len(out), cap)


if __name__ == "__main__":
    unittest.main()
