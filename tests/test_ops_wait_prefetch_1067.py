"""#1067 slice 1/1b — the batched W-member comment prefetch + the watchdog
cache's timeout backoff.

Approach 1 of the design (issue #1067 comment 5787085030, slice 1b):

  (a) `_ops_wait_flag_sets` / the footer's `_compute_net_stale_w` used to run one
      `gh issue view <n> --json comments` per W member (102 s for 74 members on
      montalu1). A single batched prefetch (`_ops_wait_prefetch_comments`) feeds
      the existing `ages_fn` seam via `ops_wait_ages_fn`; a member missing from
      the prefetch falls back to the per-issue call. The flag sets are
      byte-identical with and without the prefetch.

  (1b) The slice-1 prefetch used ONE `gh issue list --search "<qual>
      label:ops-wait,needs-acceptance" --json number,comments --limit 500`. That
      call HTTP-502s (`--limit 500`) / 504s (`--limit 100`) on the busy stream
      repo (odoo-erp), so the prefetch returned 0 rows there and slice 1 had no
      effect. Slice 1b replaces it with a paginated `gh api graphql` search
      (`search(type: ISSUE, first: 10)` + `comments(last: 100)`) that pages by
      cursor, carries only the newest 100 comments per issue, bounds each page
      with its own 20 s timeout, and keeps earlier pages when a later page fails.
      The `totalCount > 100` cap-fallback replaces slice 1's `len(comments) <
      100` rule (a fully-fetched `comments(last: 100)` list is always exactly
      len 100 at/over the cap, so length alone can no longer signal truncation).

  (b) `watchdog.ops_wait_recheck._cached_member_fetch`: a TIMEOUT result (the
      `FETCH_TIMEOUT` sentinel, distinct from a gh error / None) doubles its
      fail-TTL geometrically, capped at the full TTL, so a persistently-slow
      fetch can never fire every sweep again.
"""
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_quals_cmd
import watchdog.ops_wait_recheck as owr


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# member 41 → an OLD own comment (stale!); member 42 → a fresh own comment.
# The ticket `createdAt` is kept recent (< OPS_WAIT_CONVERGE_AGE_D=14d) so the
# #881 `converge!` age-ceiling never fires and subtracts these from `stale`
# (converge! suppresses stale!) — this test exercises the stale path, not converge.
_NOW = datetime.now(timezone.utc)
_OLD = _iso(_NOW - timedelta(days=6))      # > 24h WORKING time → stale!
_FRESH = _iso(_NOW - timedelta(hours=1))
_CREATED = _iso(_NOW - timedelta(days=10))  # < 14d → no converge! age-ceiling

COMMENTS = {
    41: [{"author": {"login": "me"}, "createdAt": _OLD, "body": "cakame"}],
    42: [{"author": {"login": "me"}, "createdAt": _FRESH, "body": "cakame"}],
}


def _ow(*nums):
    return {n: {"number": n, "title": "parked ticket %d" % n,
               "labels": [{"name": "ops-wait"}],
               "createdAt": _CREATED} for n in nums}


def _cursor_of(args):
    """Extract the `cursor=<c>` value from a `gh api graphql` argv, or None."""
    for a in args:
        if isinstance(a, str) and a.startswith("cursor="):
            return a[len("cursor="):]
    return None


class _GhRecorder:
    """A fake `airuleset._gh_out` that answers the slice-1b paginated GraphQL
    search prefetch AND per-issue `issue view` reads from the SAME `COMMENTS`
    fixture, and counts how many COMMENTS-bearing gh calls were made (so a test
    can prove the prefetch pages the members, the per-member path reads N).

    The GraphQL search serves `self.present` (an ordered member list) in pages
    of `page_size` by cursor. `list_comments` overrides the per-row comment
    nodes the SEARCH returns (defaults to `comments`); `total_counts` overrides
    the per-row `totalCount` (defaults to len of the served nodes) so a test can
    force the >100 cap-fallback. `fail_pages` is the set of 0-based page indices
    whose GraphQL call returns "" (a `_gh_out` failure)."""

    def __init__(self, comments, present=None, list_comments=None,
                 total_counts=None, page_size=10, fail_pages=None,
                 slug="owner/repo"):
        self.comments = comments
        self.present = list(comments) if present is None else list(present)
        self.list_comments = list_comments or comments
        self.total_counts = total_counts or {}
        self.page_size = page_size
        self.fail_pages = set(fail_pages or ())
        self.slug = slug
        self.graphql_calls = 0
        self.view_calls = []

    def __call__(self, *args, **kwargs):
        # repo slug resolution: gh repo view --json nameWithOwner -q .nameWithOwner
        if args[:2] == ("repo", "view") and "nameWithOwner" in args:
            return self.slug
        # slice-1b batched prefetch: gh api graphql ... search(...)
        if args[:2] == ("api", "graphql"):
            page_idx = 0
            cursor = _cursor_of(args)
            if cursor is not None and cursor.startswith("off:"):
                offset = int(cursor[len("off:"):])
                page_idx = offset // self.page_size
            else:
                offset = 0
            self.graphql_calls += 1
            if page_idx in self.fail_pages:
                return ""                        # page failure → _gh_out ""
            chunk = self.present[offset:offset + self.page_size]
            nodes = []
            for n in chunk:
                cnodes = self.list_comments.get(n, [])
                tc = self.total_counts.get(n, len(cnodes))
                nodes.append({"number": n,
                              "comments": {"totalCount": tc, "nodes": cnodes}})
            has_next = (offset + self.page_size) < len(self.present)
            end_cursor = "off:%d" % (offset + self.page_size)
            return json.dumps({"data": {"search": {
                "issueCount": len(self.present),
                "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                "nodes": nodes}}})
        # per-issue fallback: gh issue view <n> --json comments
        if args[:2] == ("issue", "view") and "comments" in args:
            n = int(args[2])
            self.view_calls.append(n)
            if n in self.comments:
                return json.dumps({"comments": self.comments[n]})
            return ""
        return ""

    @property
    def comment_calls(self):
        return self.graphql_calls + len(self.view_calls)


def _run_flag_sets(ow, member_quals, rec):
    with mock.patch.object(airuleset, "_gh_out", rec), \
            mock.patch.object(airuleset, "_stream_self_login", lambda: "me"), \
            mock.patch.object(airuleset, "resolve_authority",
                              lambda cwd=None: "full"), \
            mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                              lambda cwd: None):
        return cli_quals_cmd._ops_wait_flag_sets(ow, "/r", member_quals=member_quals)


class PrefetchBatchesCommentReads(unittest.TestCase):
    """(a) — the prefetch pages the members, ZERO per-issue views."""

    def test_one_page_serves_all_members(self):
        ow = _ow(41, 42)
        rec = _GhRecorder(COMMENTS)
        _run_flag_sets(ow, ["label:stream:x"], rec)
        # ONE GraphQL page (2 members < page_size), ZERO per-issue views.
        self.assertEqual(1, rec.graphql_calls)
        self.assertEqual([], rec.view_calls)
        self.assertEqual(1, rec.comment_calls)

    def test_one_search_per_member_qual(self):
        # The prefetch runs ONE paginated search per member-defining qual.
        ow = _ow(41, 42)
        rec = _GhRecorder(COMMENTS)
        _run_flag_sets(ow, ["q1", "q2"], rec)
        self.assertEqual(2, rec.graphql_calls)
        self.assertEqual([], rec.view_calls)


class PrefetchPaginates(unittest.TestCase):
    """(1b) — 2 GraphQL pages of 10 issues each → all prefetched in exactly 2
    calls, ZERO per-issue views (the odoo-erp busy-repo case slice 1 could not
    serve)."""

    def test_two_pages_of_ten_prefetched_in_two_calls(self):
        nums = list(range(101, 121))            # 20 members → 2 pages of 10
        c = {n: [{"author": {"login": "me"}, "createdAt": _FRESH,
                  "body": "cakame"}] for n in nums}
        ow = _ow(*nums)
        rec = _GhRecorder(c, page_size=10)
        _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(2, rec.graphql_calls)   # exactly ceil(20/10) pages
        self.assertEqual([], rec.view_calls)     # nothing fell back

    def test_pages_until_has_next_page_false(self):
        nums = list(range(101, 126))            # 25 members → 3 pages (10/10/5)
        c = {n: [{"author": {"login": "me"}, "createdAt": _FRESH,
                  "body": "cakame"}] for n in nums}
        ow = _ow(*nums)
        rec = _GhRecorder(c, page_size=10)
        _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(3, rec.graphql_calls)
        self.assertEqual([], rec.view_calls)


class PrefetchFlagParity(unittest.TestCase):
    """(a) — the flag sets are identical with and without the prefetch."""

    def test_identical_flag_sets_prefetch_vs_per_member(self):
        ow = _ow(41, 42)
        rec_pre = _GhRecorder(COMMENTS)
        with_prefetch = _run_flag_sets(ow, ["label:stream:x"], rec_pre)
        rec_per = _GhRecorder(COMMENTS)
        without = _run_flag_sets(ow, None, rec_per)
        self.assertEqual(without, with_prefetch)
        # meaningful: member 41 (old own comment) is stale! on BOTH paths.
        stale_pre = with_prefetch[0]
        stale_per = without[0]
        self.assertIn(41, stale_pre)
        self.assertIn(41, stale_per)
        self.assertNotIn(42, stale_pre)
        # per-member path made one view per member (>1 comments call),
        # prefetch path made exactly one (a single GraphQL page).
        self.assertEqual(1, rec_pre.comment_calls)
        self.assertGreater(len(rec_per.view_calls), 1)

    def test_full_flag_parity_across_categories(self):
        # A richer fixture exercising the flag categories named in the design
        # (stale!/recheck!/gk-handoff!/unpark?/acceptance-tacit): the WHOLE
        # returned tuple must match the per-issue path byte-for-byte.
        gk = _iso(_NOW - timedelta(hours=2))
        ow = {
            41: {"number": 41, "title": "t", "labels": [{"name": "ops-wait"}],
                 "createdAt": _CREATED},                       # stale!
            42: {"number": 42, "title": "t", "labels": [{"name": "ops-wait"}],
                 "createdAt": _CREATED},                       # fresh
            43: {"number": 43, "title": "t",
                 "labels": [{"name": "needs-acceptance"},
                            {"name": "ready-for-review"}],
                 "createdAt": _CREATED},                       # gk-handoff!
        }
        c = {
            41: [{"author": {"login": "me"}, "createdAt": _OLD, "body": "x"}],
            42: [{"author": {"login": "me"}, "createdAt": gk, "body": "x"}],
            43: [{"author": {"login": "me"}, "createdAt": _FRESH, "body": "x"}],
        }
        rec_pre = _GhRecorder(c)
        with_prefetch = _run_flag_sets(ow, ["label:stream:x"], rec_pre)
        rec_per = _GhRecorder(c)
        without = _run_flag_sets(ow, None, rec_per)
        self.assertEqual(without, with_prefetch)
        self.assertEqual(1, rec_pre.graphql_calls)
        self.assertEqual([], rec_pre.view_calls)


class PrefetchMissingMemberFallback(unittest.TestCase):
    """(a) — a member absent from the prefetch falls back to the per-issue call."""

    def test_missing_member_uses_per_issue_view(self):
        ow = _ow(41, 42, 43)
        c = dict(COMMENTS)
        c[43] = [{"author": {"login": "me"}, "createdAt": _OLD, "body": "cakame"}]
        # the search returns only 41,42 (43 label just changed / not indexed).
        rec = _GhRecorder(c, present=[41, 42])
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(1, rec.graphql_calls)
        self.assertIn(43, rec.view_calls)        # fell back to per-issue
        self.assertNotIn(41, rec.view_calls)     # served from the prefetch
        # 43 (old own comment, per-issue) is still correctly stale!
        self.assertIn(43, sets[0])


class PrefetchPageFailureFallback(unittest.TestCase):
    """(1b) — a failing page keeps the pages already read and leaves the
    unfetched members to the per-issue fallback."""

    def test_failed_page_keeps_earlier_pages_rest_fall_back(self):
        page0 = list(range(41, 51))             # served on page 0
        page1 = list(range(51, 61))             # page 1 fails
        nums = page0 + page1
        c = {n: [{"author": {"login": "me"}, "createdAt": _OLD, "body": "x"}]
             for n in nums}
        ow = _ow(*nums)
        rec = _GhRecorder(c, page_size=10, fail_pages={1})
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        # both page attempts were made (page 0 ok, page 1 failed).
        self.assertEqual(2, rec.graphql_calls)
        # page-0 members served from the prefetch (no per-issue view)…
        self.assertNotIn(41, rec.view_calls)
        # …page-1 members fell back to the per-issue read.
        self.assertIn(51, rec.view_calls)
        # correctness preserved on BOTH paths (old own comment → stale!).
        self.assertIn(41, sets[0])
        self.assertIn(51, sets[0])


class PrefetchTruncationFallback(unittest.TestCase):
    """(1b) — `comments(last: 100)` carries only the newest 100; a row whose
    `totalCount > 100` is EXCLUDED from the prefetch map and falls back to the
    fully-paginated `gh issue view` (never a false stale! from a missing OLD
    comment). This replaces slice 1's `len(comments) < 100` rule."""

    def test_over_cap_row_falls_back_and_avoids_false_stale(self):
        import cli_quals
        cap = cli_quals.OPS_WAIT_PREFETCH_COMMENT_CAP
        # member 44: the SEARCH returns 100 recent nodes but totalCount=cap+1
        # (the issue has more than 100 comments); the per-issue VIEW returns the
        # full history including the newest fresh CITED own push.
        recent_100 = [{"author": {"login": "other"}, "createdAt": _OLD,
                       "body": "cakame %d" % i} for i in range(cap)]
        full_view = recent_100 + [{"author": {"login": "me"},
                                   "createdAt": _FRESH,
                                   "body": "nasadene vo v1.2.3"}]
        c = {44: full_view}          # what `gh issue view` returns (full)
        lc = {44: recent_100}        # what the search returns (newest 100)
        ow = _ow(44)
        rec = _GhRecorder(c, list_comments=lc, total_counts={44: cap + 1})
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(1, rec.graphql_calls)
        self.assertIn(44, rec.view_calls)    # cap-excluded → per-issue fallback
        # served from the FULL per-issue view (fresh newest own) → NOT stale.
        self.assertNotIn(44, sets[0])

    def test_at_cap_totalcount_served_from_prefetch(self):
        # totalCount == cap (exactly 100, not over) is served from the prefetch:
        # `comments(last: 100)` carried the whole list, nothing is missing.
        import cli_quals
        cap = cli_quals.OPS_WAIT_PREFETCH_COMMENT_CAP
        nodes = ([{"author": {"login": "other"}, "createdAt": _OLD,
                   "body": "x %d" % i} for i in range(cap - 1)]
                 + [{"author": {"login": "me"}, "createdAt": _FRESH,
                     "body": "x"}])
        c = {45: nodes}
        ow = _ow(45)
        rec = _GhRecorder(c, total_counts={45: cap})
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual([], rec.view_calls)     # served from the prefetch
        self.assertNotIn(45, sets[0])            # fresh newest own → not stale


class PrefetchSkippedWhenNoWMembers(unittest.TestCase):
    """(a) — an EMPTY W set makes ZERO gh calls (the idle-box footer case): the
    prefetch never fires a per-qual search nobody will consume."""

    def test_empty_ops_wait_no_gh(self):
        rec = _GhRecorder(COMMENTS)
        with mock.patch.object(airuleset, "_gh_out", rec), \
                mock.patch.object(airuleset, "_stream_self_login", lambda: "me"):
            airuleset.ops_wait_ages_fn({}, "/r", ["q1", "q2"])
        self.assertEqual(0, rec.comment_calls)

    def test_compute_net_stale_w_empty_no_gh(self):
        rec = _GhRecorder(COMMENTS)
        with mock.patch.object(airuleset, "_gh_out", rec), \
                mock.patch.object(airuleset, "_stream_self_login", lambda: "me"):
            n = airuleset._compute_net_stale_w({}, cwd="/r",
                                               member_quals=["q1"])
        self.assertEqual(0, n)
        self.assertEqual(0, rec.comment_calls)


class PrefetchSlugFailureFallsBack(unittest.TestCase):
    """(1b) — an unresolvable repo slug makes the search unbuildable, so the
    prefetch contributes nothing and every member falls back per-issue
    (byte-identical to today's failure mode)."""

    def test_empty_slug_falls_back_to_per_issue(self):
        ow = _ow(41, 42)
        rec = _GhRecorder(COMMENTS, slug="")     # gh repo view → "" (no slug)
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        # no GraphQL page attempted (nothing to query), all members per-issue.
        self.assertEqual(0, rec.graphql_calls)
        self.assertIn(41, rec.view_calls)
        self.assertIn(42, rec.view_calls)
        self.assertIn(41, sets[0])               # still correctly stale!


class CacheTimeoutBackoff(unittest.TestCase):
    """(b) — a TIMEOUT sentinel doubles the fail-TTL, capped at the full TTL;
    a gh error / None keeps the base fail-TTL (transient re-checks soon)."""

    def test_sentinel_exists_and_is_distinct(self):
        self.assertIsNotNone(owr.FETCH_TIMEOUT)
        self.assertIsNot(owr.FETCH_TIMEOUT, None)

    def _fetch_timeout(self, cwd):
        return owr.FETCH_TIMEOUT

    def test_timeout_returns_none_to_caller(self):
        state = {}
        out = owr._cached_member_fetch(
            "/r", self._fetch_timeout, state, 1000.0, "ops_wait_cache",
            ttl=1800, fail_ttl=60)
        self.assertIsNone(out)

    def test_fail_ttl_doubles_and_caps_at_ttl(self):
        state = {}
        ttl = 500
        now = 1000.0
        seen = []
        for _ in range(6):
            owr._cached_member_fetch(
                "/r", self._fetch_timeout, state, now, "ops_wait_cache",
                ttl=ttl, fail_ttl=60)
            entry = state["ops_wait_cache"]["/r"]
            seen.append(entry["fail_ttl"])
            # advance past the current backoff so the next call re-fetches.
            now += entry["fail_ttl"] + 1
        # geometric doubling from the 60 s base, capped at ttl (500).
        self.assertEqual([120, 240, 480, 500, 500, 500], seen)

    def test_gh_error_none_does_not_back_off(self):
        state = {}
        owr._cached_member_fetch(
            "/r", lambda c: None, state, 1000.0, "ops_wait_cache",
            ttl=1800, fail_ttl=60)
        entry = state["ops_wait_cache"]["/r"]
        # a genuine None keeps the base fail-TTL (no escalation key), so it is
        # stale again after the base 60 s — a transient gh hiccup re-checks soon.
        self.assertFalse(owr._entry_is_fresh(entry, 1000.0 + 61, 1800, 60))

    def test_backed_off_entry_is_fresh_within_its_own_fail_ttl(self):
        state = {}
        owr._cached_member_fetch(
            "/r", self._fetch_timeout, state, 1000.0, "ops_wait_cache",
            ttl=1800, fail_ttl=60)
        entry = state["ops_wait_cache"]["/r"]
        # stored fail_ttl is 120 → still fresh at +100 s, stale at +121 s.
        self.assertTrue(owr._entry_is_fresh(entry, 1000.0 + 100, 1800, 60))
        self.assertFalse(owr._entry_is_fresh(entry, 1000.0 + 121, 1800, 60))


if __name__ == "__main__":
    unittest.main()
