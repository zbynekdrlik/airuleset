"""#1097 — report-owed rider hardening: owed candidates must be verified against
the GitHub row (state + closed_at inside the window) and scoped to the window's
ROLE, and the `_CLOSES_RE` closes-regex must not fire on a hyphenated prose
mention (`Release-fix #4`).

Two coupled defects produced a false owner escalation on gk (2026-09-21): odoo-erp
#4 (closed 2026-01-17) was re-derived as freshly owed from the commit-body
sentence `Release-fix #4 wrapped the test body` in commit 1e9e1f4d0, and gk's two
roots of one repo (FLOW + INFRA windows) both owed the same ticket.

These tests drive the PURE verifier (`verify_owed`), the filter factory
(`make_verified_closed_filter`), the tightened `_CLOSES_RE`, and a source-lock on
the `run_once` wiring — all with injected seams, no network and no real git state.
"""

import inspect
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import watchdog as wd  # noqa: E402
import watchdog.cards as cards  # noqa: E402
import watchdog.owed_verify as ov  # noqa: E402


def _root():
    """A throwaway non-repo dir as `root` — `canonical_slug` returns None on it
    (no remote), which is harmless because every test injects `fetch`."""
    return tempfile.mkdtemp()


def _epoch(iso):
    return cards._iso_epoch(iso)


# The window used across the verifier tests: start 2026-09-19, so a close on
# 2026-09-20 is IN-window and the 2026-01-17 close of odoo-erp #4 is OUT.
WIN_START = _epoch("2026-09-19T00:00:00Z")
IN_WINDOW = "2026-09-20T21:53:00Z"
OUT_WINDOW = "2026-01-17T20:16:52Z"


def _row(state="closed", closed_at=IN_WINDOW, labels=None):
    """A REST issue row (the shape `rest_get_cached` returns for one issue)."""
    d = {"state": state, "closed_at": closed_at}
    if labels is not None:
        d["labels"] = labels
    return d


def _fetch(rows):
    """A `fetch(slug, n) -> (obj, err)` from a `{n: (obj, err)}` map, recording
    the issue numbers actually fetched (to prove memo / composition order)."""
    calls = []

    def f(slug, n):
        calls.append(n)
        return rows[n]

    f.calls = calls
    return f


# --------------------------------------------------------------------------- #
# (a) The tightened `_CLOSES_RE` — a hyphenated prose mention must NOT match,
#     GitHub's anywhere-in-message keyword rule must still match with the FULL
#     number captured.
# --------------------------------------------------------------------------- #

def _closes_in(text):
    return [int(m.group(1)) for m in cards._CLOSES_RE.finditer(text)]


class ClosesRegexLookbehind(unittest.TestCase):
    def test_hyphenated_fix_does_not_match_the_false_owed_case(self):
        # The exact commit-body sentence that re-owed odoo-erp #4.
        self.assertEqual([], _closes_in("Release-fix #4 wrapped the test body"))

    def test_hot_fix_hyphen_does_not_match(self):
        self.assertEqual([], _closes_in("hot-fix #12"))

    def test_release_fix_parenthesised_ref_does_not_match(self):
        # release-fix(#7710) — the keyword is hyphen-preceded, so no match.
        self.assertEqual([], _closes_in("release-fix(#7710) landed"))

    def test_plain_fixes_matches_full_number(self):
        self.assertEqual([4], _closes_in("Fixes #4"))

    def test_two_keywords_one_message_both_match(self):
        self.assertEqual([4, 5], _closes_in("fix #4 and closes #5"))

    def test_full_number_captured_not_truncated(self):
        self.assertEqual([4520], _closes_in("Closes #4520"))

    def test_resolved_keyword_still_matches(self):
        self.assertEqual([99], _closes_in("Resolved #99 today"))

    def test_mid_sentence_close_after_space_still_matches(self):
        # GitHub closes on the keyword ANYWHERE — a space-preceded mid-sentence
        # `fixes` must survive the lookbehind (only `[\w-]` before it is blocked).
        self.assertEqual([7], _closes_in("this change fixes #7 finally"))


# --------------------------------------------------------------------------- #
# (b) `verify_owed` — state + closed_at window; unreadable = KEEP.
# --------------------------------------------------------------------------- #

class VerifyOwedStateWindow(unittest.TestCase):
    def test_close_inside_window_is_kept(self):
        f = _fetch({4: (_row(closed_at=IN_WINDOW, labels=[]), None)})
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, f, 999.0)
        self.assertEqual({4: 111.0}, out)

    def test_close_before_window_is_dropped_and_journaled(self):
        f = _fetch({4: (_row(closed_at=OUT_WINDOW, labels=[]), None)})
        j = []
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, f, 999.0,
                             journal=j.append)
        self.assertEqual({}, out)
        line = "\n".join(j)
        self.assertIn("owed-verify drop #4", line)
        self.assertIn("closed_at 2026-01-17", line)
        self.assertIn("outside window", line)

    def test_open_issue_is_dropped_and_journaled(self):
        f = _fetch({4: (_row(state="open", closed_at=None, labels=[]), None)})
        j = []
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, f, 999.0,
                             journal=j.append)
        self.assertEqual({}, out)
        self.assertIn("owed-verify drop #4", "\n".join(j))
        self.assertIn("state=open", "\n".join(j))

    def test_unreadable_row_is_kept_and_journaled_keep_unreadable(self):
        f = _fetch({4: (None, "gate-unavailable: quota exhausted")})
        j = []
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, f, 999.0,
                             journal=j.append)
        self.assertEqual({4: 111.0}, out)
        line = "\n".join(j)
        self.assertIn("owed-verify keep-unreadable #4", line)
        self.assertIn("gate-unavailable", line)

    def test_fetch_that_raises_is_kept_unreadable(self):
        def boom(slug, n):
            raise RuntimeError("transport blew up")
        j = []
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, boom, 999.0,
                             journal=j.append)
        self.assertEqual({4: 111.0}, out)
        self.assertIn("owed-verify keep-unreadable #4", "\n".join(j))

    def test_closed_at_unparseable_is_kept_unreadable(self):
        f = _fetch({4: (_row(closed_at="not-a-date", labels=[]), None)})
        j = []
        out = ov.verify_owed(_root(), {4: 111.0}, WIN_START, None, f, 999.0,
                             journal=j.append)
        self.assertEqual({4: 111.0}, out)
        self.assertIn("owed-verify keep-unreadable #4", "\n".join(j))

    def test_empty_candidate_set_returns_empty_no_fetch(self):
        f = _fetch({})
        out = ov.verify_owed(_root(), {}, WIN_START, None, f, 999.0)
        self.assertEqual({}, out)
        self.assertEqual([], f.calls)

    def test_since_ts_none_disables_window_check(self):
        # A very old close with since_ts None -> not dropped for the window.
        f = _fetch({4: (_row(closed_at=OUT_WINDOW, labels=[]), None)})
        out = ov.verify_owed(_root(), {4: 111.0}, None, None, f, 999.0)
        self.assertEqual({4: 111.0}, out)


# --------------------------------------------------------------------------- #
# (c) Role scoping — INFRA window owes only infra-class, FLOW/review only
#     independent-class; role None owes everything; unreadable labels -> infra.
# --------------------------------------------------------------------------- #

class VerifyOwedRoleScoping(unittest.TestCase):
    def _run(self, labels, role, state="closed", closed_at=IN_WINDOW, err=None):
        obj = None if err else _row(state=state, closed_at=closed_at, labels=labels)
        f = _fetch({4: (obj, err)})
        j = []
        out = ov.verify_owed(_root(), {4: 7.0}, WIN_START, role, f, 999.0,
                             journal=j.append)
        return out, "\n".join(j)

    def test_infra_label_kept_on_infra_role(self):
        out, _ = self._run([{"name": "infra"}], "infra")
        self.assertEqual({4: 7.0}, out)

    def test_infra_label_dropped_on_review_role(self):
        out, j = self._run([{"name": "infra"}], "review")
        self.assertEqual({}, out)
        self.assertIn("role=review", j)
        self.assertIn("class=infra", j)

    def test_empty_labels_kept_on_review_role(self):
        out, _ = self._run([], "review")
        self.assertEqual({4: 7.0}, out)

    def test_empty_labels_dropped_on_infra_role(self):
        out, j = self._run([], "infra")
        self.assertEqual({}, out)
        self.assertIn("role=infra", j)
        self.assertIn("class=independent", j)

    def test_missing_labels_kept_on_infra_role(self):
        # labels key absent -> work_class fail-safe infra.
        out, _ = self._run(None, "infra")
        self.assertEqual({4: 7.0}, out)

    def test_missing_labels_dropped_on_review_role(self):
        out, j = self._run(None, "review")
        self.assertEqual({}, out)
        self.assertIn("class=infra", j)

    def test_role_none_keeps_everything(self):
        for labels in ([{"name": "infra"}], [], None):
            out, _ = self._run(labels, None)
            self.assertEqual({4: 7.0}, out, "role None keeps labels=%r" % (labels,))

    def test_unreadable_row_kept_on_infra_role(self):
        out, j = self._run(None, "infra", err="gate-unavailable: down")
        self.assertEqual({4: 7.0}, out)
        self.assertIn("owed-verify keep-unreadable #4", j)

    def test_unreadable_row_dropped_on_review_role_never_lost_on_both(self):
        # An unreadable candidate: work_class(None) -> infra, so it stays owed on
        # the INFRA root (above) and is dropped on the FLOW/review root here.
        out, j = self._run(None, "review", err="gate-unavailable: down")
        self.assertEqual({}, out)
        self.assertIn("role=review", j)
        # A role-drop supersedes the keep-unreadable note (one line per candidate).
        self.assertNotIn("keep-unreadable", j)


# --------------------------------------------------------------------------- #
# (d) `make_verified_closed_filter` composition — inner first, None/{}
#     passthrough, memo, `.logs`.
# --------------------------------------------------------------------------- #

class VerifiedClosedFilterComposition(unittest.TestCase):
    def test_inner_none_passes_through_without_verify(self):
        f = _fetch({4: (_row(), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: None, since_fn=lambda: WIN_START,
            fetch=f, role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        self.assertIsNone(flt("/r", {4: 1.0}))
        self.assertEqual([], f.calls, "None must short-circuit before any fetch")

    def test_inner_empty_passes_through_without_verify(self):
        f = _fetch({4: (_row(), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: {}, since_fn=lambda: WIN_START,
            fetch=f, role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        self.assertEqual({}, flt("/r", {4: 1.0}))
        self.assertEqual([], f.calls, "{} must short-circuit before any fetch")

    def test_composition_order_inner_first_then_verify(self):
        # inner drops #5; verify then sees ONLY #4 (out-of-window) and drops it.
        f = _fetch({4: (_row(closed_at=OUT_WINDOW, labels=[]), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: {4: closed[4]},   # inner keeps only #4
            since_fn=lambda: WIN_START, fetch=f,
            role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        out = flt("/r", {4: 1.0, 5: 2.0})
        self.assertEqual({}, out)
        self.assertEqual([4], f.calls, "verify must run on inner's output only")

    def test_logs_accumulate_on_the_filter(self):
        f = _fetch({4: (_row(closed_at=OUT_WINDOW, labels=[]), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: dict(closed), since_fn=lambda: WIN_START,
            fetch=f, role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        flt("/home/gatekeeper/devel/odoo/odoo-erp", {4: 1.0})
        line = "\n".join(flt.logs)
        self.assertIn("owed-verify drop #4 odoo-erp", line)
        self.assertIn("outside window", line)

    def test_memo_serves_second_identical_call_without_refetch(self):
        # The two jobs pass the identical (root, set) within a sweep.
        f = _fetch({4: (_row(labels=[]), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: dict(closed), since_fn=lambda: WIN_START,
            fetch=f, role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        a = flt("/r", {4: 1.0})
        b = flt("/r", {4: 1.0})
        self.assertEqual(a, b)
        self.assertEqual([4], f.calls, "memo must serve the 2nd call (no refetch)")

    def test_cwd_by_root_overrides_role_lookup_path(self):
        seen = []

        def role_fn(cwd):
            seen.append(cwd)
            return None
        f = _fetch({4: (_row(labels=[]), None)})
        flt = ov.make_verified_closed_filter(
            lambda root, closed: dict(closed), cwd_by_root={"/r": "/panes/cwd"},
            since_fn=lambda: WIN_START, fetch=f, role_fn=role_fn,
            now_fn=lambda: 999.0)
        flt("/r", {4: 1.0})
        self.assertEqual(["/panes/cwd"], seen)


# --------------------------------------------------------------------------- #
# Integration — the FULL composition run_once builds (a full-authority #534
# inner + the verifier) drops the false-owed #4 and keeps the real #7583.
# --------------------------------------------------------------------------- #

class ComposedWithOwnerFilter(unittest.TestCase):
    def test_full_authority_inner_then_verify_drops_stale_keeps_fresh(self):
        rows = {
            4: (_row(closed_at=OUT_WINDOW, labels=[]), None),      # stale close
            7583: (_row(closed_at=IN_WINDOW, labels=[]), None),    # real close
        }
        f = _fetch(rows)
        inner = cards.make_owned_closed_filter(
            current_user_fn=lambda: "gatekeeper",
            authority_fn=lambda root: "full")          # gk owns the whole set
        flt = ov.make_verified_closed_filter(
            inner, since_fn=lambda: WIN_START, fetch=f,
            role_fn=lambda cwd: None, now_fn=lambda: 999.0)
        out = flt("/home/gatekeeper/devel/odoo/odoo-erp", {4: 10.0, 7583: 20.0})
        self.assertEqual({7583: 20.0}, out)
        self.assertIn("owed-verify drop #4 odoo-erp", "\n".join(flt.logs))


# --------------------------------------------------------------------------- #
# (e) Source-lock — run_once wires the SAME composed callable into BOTH jobs.
# --------------------------------------------------------------------------- #

class RunOnceWiringSourceLock(unittest.TestCase):
    def setUp(self):
        self.src = inspect.getsource(wd.run_once)

    def test_composed_filter_is_built_from_the_534_owner_filter(self):
        self.assertIn("make_verified_closed_filter(", self.src)
        self.assertIn("make_owned_closed_filter()", self.src)
        self.assertIn("_owned_scope = make_verified_closed_filter(", self.src)

    def test_same_composed_callable_handed_to_both_jobs(self):
        self.assertEqual(
            2, self.src.count("owned_closed=_owned_scope"),
            "both card_reconcile and report_reconcile must receive the SAME "
            "composed callable via owned_closed=_owned_scope")

    def test_since_fn_uses_the_card_window(self):
        self.assertIn("CARD_WINDOW_S", self.src)

    def test_journal_logs_surface_into_the_job_output(self):
        self.assertIn("_owned_scope.logs", self.src)

    def test_make_verified_closed_filter_is_importable_from_watchdog(self):
        self.assertTrue(hasattr(wd, "make_verified_closed_filter"))
        self.assertTrue(hasattr(wd, "verify_owed"))


if __name__ == "__main__":
    unittest.main()
