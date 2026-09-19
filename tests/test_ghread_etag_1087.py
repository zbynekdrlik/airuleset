"""#1087 L1 item (b) -- gates.ghread ETag-conditional REST reads.

`rest_get_cached(path, params)` stores {etag, body, ts} per URL and sends
`If-None-Match`; a 304 returns the cached body (budget-free), a 200 refreshes,
any error fails open to a plain uncached GET. `list_open_issues_cached(slug)`
pages the REST issues endpoint through it, drops PR rows (the `pull_request`
key), and returns normalized rows. `search_client_side_ok` / `issue_matches_
search` replicate the label/@me `--search` qualifiers client-side so a caller
can filter a cached snapshot instead of spending one GraphQL search per qual.

Hermetic: a fake gh runner returns (rc, stdout, stderr) per argv; the ETag
cache is redirected to a tmp dir via AIRULESET_GH_ETAG_DIR. No network, no gh.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gates import ghread


def _include_response(status, etag=None, body="", reason="OK"):
    """A `gh api --include` style raw stdout: status line + headers + blank +
    body. gh returns rc0 for 200, rc1 for a 304 (an HTTP 'error')."""
    lines = ["HTTP/2.0 %d %s" % (status, reason),
             "Content-Type: application/json; charset=utf-8"]
    if etag is not None:
        lines.append("Etag: %s" % etag)
    lines.append("Date: Sat, 19 Sep 2026 16:28:30 GMT")
    lines.append("")               # blank line separates headers from body
    lines.append(body)
    return "\n".join(lines)


def _issue(n, labels=None, title=None, assignee=None, author=None, pr=False,
           created="2026-09-01T00:00:00Z", updated="2026-09-02T00:00:00Z"):
    it = {"number": n, "title": title or ("issue %d" % n),
          "created_at": created, "updated_at": updated,
          "labels": [{"name": nm} for nm in (labels or [])],
          "assignees": [{"login": a} for a in (assignee or [])],
          "user": {"login": author or "someone"}}
    if pr:
        it["pull_request"] = {"url": "https://x/pulls/%d" % n}
    return it


class _EtagTmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="ghetag-1087-")
        self._prev = os.environ.get("AIRULESET_GH_ETAG_DIR")
        os.environ["AIRULESET_GH_ETAG_DIR"] = self._d

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AIRULESET_GH_ETAG_DIR", None)
        else:
            os.environ["AIRULESET_GH_ETAG_DIR"] = self._prev


class RestGetCached(_EtagTmp):
    def test_200_returns_body_and_stores_etag(self):
        body = json.dumps([{"number": 1}])
        calls = []

        def runner(argv):
            calls.append(argv)
            return 0, _include_response(200, etag='W/"abc"', body=body), ""

        obj, err = ghread.rest_get_cached("repos/o/r/issues",
                                          {"state": "open"}, runner=runner)
        self.assertIsNone(err)
        self.assertEqual(obj, [{"number": 1}])
        # the first call must NOT have sent an If-None-Match (no cache yet)
        self.assertNotIn("If-None-Match: W/\"abc\"",
                         [" ".join(a) for a in calls][0])

    def test_304_returns_cached_body_budget_free(self):
        body = json.dumps([{"number": 7}])
        seq = []

        def runner(argv):
            joined = " ".join(argv)
            if "If-None-Match" in joined:
                seq.append("cond")
                return 1, _include_response(304, reason="Not Modified"), ""
            seq.append("full")
            return 0, _include_response(200, etag='W/"e1"', body=body), ""

        obj1, err1 = ghread.rest_get_cached("repos/o/r/issues",
                                            {"state": "open"}, runner=runner)
        self.assertIsNone(err1)
        self.assertEqual(obj1, [{"number": 7}])
        obj2, err2 = ghread.rest_get_cached("repos/o/r/issues",
                                            {"state": "open"}, runner=runner)
        self.assertIsNone(err2)
        self.assertEqual(obj2, [{"number": 7}])       # cached body, from the 304
        self.assertEqual(seq, ["full", "cond"])       # 2nd call was conditional

    def test_error_fails_open_to_plain_get(self):
        body = json.dumps([{"number": 9}])

        def runner(argv):
            if "--include" in argv:
                return 6, "", "gh: transport error"    # conditional path errored
            return 0, body, ""                          # plain GET succeeds

        obj, err = ghread.rest_get_cached("repos/o/r/issues", runner=runner)
        self.assertIsNone(err)
        self.assertEqual(obj, [{"number": 9}])

    def test_both_paths_fail_yields_gate_unavailable(self):
        def runner(argv):
            return 1, "", "GraphQL: API rate limit exceeded (rateLimit)"

        obj, err = ghread.rest_get_cached("repos/o/r/issues", runner=runner)
        self.assertIsNone(obj)
        self.assertTrue(err.startswith(ghread.GATE_UNAVAILABLE_PREFIX))

    def test_max_age_serves_cache_without_any_gh_call(self):
        body = json.dumps([{"number": 3}])
        n = {"calls": 0}

        def runner(argv):
            n["calls"] += 1
            return 0, _include_response(200, etag='W/"m"', body=body), ""

        # first populates the cache
        ghread.rest_get_cached("repos/o/r/issues", {"state": "open"},
                               runner=runner, now=1000.0)
        self.assertEqual(n["calls"], 1)
        # within max_age -> cache read, no gh call at all
        obj, err = ghread.rest_get_cached("repos/o/r/issues", {"state": "open"},
                                          runner=runner, now=1030.0, max_age=60)
        self.assertIsNone(err)
        self.assertEqual(obj, [{"number": 3}])
        self.assertEqual(n["calls"], 1)               # no second gh call


class ListOpenIssuesCached(_EtagTmp):
    def test_pagination_and_pr_filter(self):
        page1 = [_issue(i) for i in range(1, 101)] + []      # exactly per_page=...
        # make page1 exactly per_page long so pagination continues
        page1 = [_issue(i, labels=["needs-gatekeeper"]) for i in range(1, 101)]
        page1[50] = _issue(51, pr=True)                       # a PR row to drop
        page2 = [_issue(200), _issue(201)]

        def runner(argv):
            url = argv[-1]
            if "page=2" in url:
                return 0, _include_response(200, etag='W/"p2"',
                                            body=json.dumps(page2)), ""
            return 0, _include_response(200, etag='W/"p1"',
                                        body=json.dumps(page1)), ""

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(err)
        nums = {r["number"] for r in rows}
        self.assertNotIn(51, nums)                            # PR dropped
        self.assertIn(200, nums)                              # page 2 fetched
        self.assertIn(201, nums)
        self.assertEqual(len(rows), 100 + 2 - 1)              # 100 - 1 PR + 2
        # normalized shape
        r1 = next(r for r in rows if r["number"] == 1)
        self.assertEqual(r1["labels"], [{"name": "needs-gatekeeper"}])
        self.assertEqual(r1["createdAt"], "2026-09-01T00:00:00Z")

    def test_max_pages_exhausted_with_full_last_page_returns_none(self):
        # #1087 review 🟡: hitting max_pages with a STILL-FULL last page must be
        # treated as an error (None) — a truncated set read as complete would
        # silently reclassify rows (#1021), exactly what the docstring forbids.
        full = [_issue(i, labels=["needs-gatekeeper"]) for i in range(1, 101)]

        def runner(argv):   # every page returns exactly per_page items
            return 0, _include_response(200, etag='W/"p"',
                                        body=json.dumps(full)), ""

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner,
                                                   per_page=100, max_pages=3)
        self.assertIsNone(rows)
        self.assertTrue(err.startswith(ghread.GATE_UNAVAILABLE_PREFIX))

    def test_page_failure_returns_none_never_partial(self):
        page1 = [_issue(i) for i in range(1, 101)]

        def runner(argv):
            if "page=2" in argv[-1]:
                return 1, "", "GraphQL: API rate limit exceeded (rateLimit)"
            return 0, _include_response(200, etag='W/"p1"',
                                        body=json.dumps(page1)), ""

        rows, err = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(rows)                               # never a partial list
        self.assertTrue(err.startswith(ghread.GATE_UNAVAILABLE_PREFIX))


class ClientSideSearch(unittest.TestCase):
    def test_search_client_side_ok(self):
        self.assertTrue(ghread.search_client_side_ok(
            "-label:autopilot-skip -label:ops-channel label:needs-gatekeeper"))
        self.assertTrue(ghread.search_client_side_ok(
            "-label:stream:montalu1 -label:stream:david1"))
        # a free-text / in:title qualifier is NOT client-side
        self.assertFalse(ghread.search_client_side_ok(
            '"GATEKEEPER-ACTION:" in:title'))
        # @me needs a resolved login
        self.assertFalse(ghread.search_client_side_ok("author:@me"))
        self.assertTrue(ghread.search_client_side_ok("author:@me", me_login="dave"))

    def test_issue_matches_search_labels(self):
        row = ghread._normalize_issue(
            _issue(5, labels=["needs-gatekeeper", "prio:bounce"]))
        self.assertTrue(ghread.issue_matches_search(row, "label:needs-gatekeeper"))
        self.assertFalse(ghread.issue_matches_search(row, "label:ready-for-review"))
        # base exclusion + positive
        self.assertTrue(ghread.issue_matches_search(
            row, "-label:autopilot-skip label:needs-gatekeeper"))
        skip = ghread._normalize_issue(_issue(6, labels=["autopilot-skip",
                                                          "needs-gatekeeper"]))
        self.assertFalse(ghread.issue_matches_search(
            skip, "-label:autopilot-skip label:needs-gatekeeper"))

    def test_issue_matches_search_assignee_author_me(self):
        row = ghread._normalize_issue(
            _issue(8, assignee=["dave"], author="zed"))
        self.assertTrue(ghread.issue_matches_search(row, "assignee:@me",
                                                    me_login="dave"))
        self.assertFalse(ghread.issue_matches_search(row, "assignee:@me",
                                                     me_login="zed"))
        self.assertTrue(ghread.issue_matches_search(row, "author:@me",
                                                    me_login="zed"))


class ClientSideUnionParity(unittest.TestCase):
    """The client-side filter over a snapshot must reproduce the previous
    per-qual GraphQL union for the label-based obligation quals (item b)."""

    def test_obligation_union_parity_on_fixture(self):
        snapshot = [
            ghread._normalize_issue(_issue(1, labels=["needs-gatekeeper"])),
            ghread._normalize_issue(_issue(2, labels=["ready-for-review"])),
            ghread._normalize_issue(_issue(3, labels=["stream:montalu1"])),  # excl
            ghread._normalize_issue(_issue(4, labels=[])),                   # core
            ghread._normalize_issue(_issue(5, labels=["autopilot-skip"])),   # excl
            ghread._normalize_issue(_issue(6, labels=["gk-processing"])),
        ]
        base = "-label:autopilot-skip -label:ops-channel"
        core_excl = "-label:stream:montalu1 -label:stream:david1"
        quals = [core_excl, "label:needs-gatekeeper", "label:ready-for-review",
                 "label:gk-processing"]
        union = set()
        for qual in quals:
            search = (base + " " + qual).strip()
            self.assertTrue(ghread.search_client_side_ok(search))
            for r in snapshot:
                if ghread.issue_matches_search(r, search):
                    union.add(r["number"])
        # core partition = open, not skip/ops, not a reduced-stream label:
        #   1 (needs-gatekeeper), 2 (ready-for-review), 4 (unlabeled core),
        #   6 (gk-processing). #3 excluded (stream:montalu1), #5 excluded (skip).
        self.assertEqual(union, {1, 2, 4, 6})


if __name__ == "__main__":
    unittest.main()
