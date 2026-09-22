"""#1067 slice 1 — the batched W-member comment prefetch + the watchdog
cache's timeout backoff.

Approach 1 of the design (issue #1067 comment 5785415886):

  (a) `_ops_wait_flag_sets` / the footer's `_compute_net_stale_w` used to run one
      `gh issue view <n> --json comments` per W member (102 s for 74 members on
      montalu1). A single batched prefetch (`_ops_wait_prefetch_comments`, ONE
      `gh issue list --search "<qual> label:ops-wait,needs-acceptance" --json
      number,comments` per member-defining qual) feeds the existing `ages_fn`
      seam via `ops_wait_ages_fn`; a member missing from the prefetch falls back
      to the per-issue call. The flag sets are byte-identical with and without
      the prefetch.

  (b) `watchdog.ops_wait_recheck._cached_member_fetch`: a TIMEOUT result (the
      new `FETCH_TIMEOUT` sentinel, distinct from a gh error / None) doubles its
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


class _GhRecorder:
    """A fake `airuleset._gh_out` that answers the batched `issue list` prefetch
    AND per-issue `issue view` reads from the SAME `COMMENTS` fixture, and counts
    how many COMMENTS-bearing gh calls were made (so the test can prove the
    prefetch makes ONE, the per-member path N)."""

    def __init__(self, comments, present=None, list_comments=None):
        self.comments = comments
        # `present` limits which members the batched list returns (a missing
        # member must fall back to its per-issue view). None → all.
        self.present = set(comments) if present is None else set(present)
        # `list_comments` overrides what the BATCHED list returns per member
        # (e.g. a gh-truncated 100-comment list) vs the per-issue `comments`
        # (the fully-paginated view). Defaults to `comments` (identical data).
        self.list_comments = list_comments or comments
        self.list_calls = 0
        self.view_calls = []

    def __call__(self, *args, **kwargs):
        # batched prefetch: gh issue list ... --json number,comments
        if args[:2] == ("issue", "list") and "number,comments" in args:
            self.list_calls += 1
            rows = [{"number": n, "comments": self.list_comments[n]}
                    for n in sorted(self.present) if n in self.list_comments]
            return json.dumps(rows)
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
        return self.list_calls + len(self.view_calls)


def _run_flag_sets(ow, member_quals, rec):
    with mock.patch.object(airuleset, "_gh_out", rec), \
            mock.patch.object(airuleset, "_stream_self_login", lambda: "me"), \
            mock.patch.object(airuleset, "resolve_authority",
                              lambda cwd=None: "full"), \
            mock.patch.object(airuleset, "_watchdog_release_state_fetch",
                              lambda cwd: None):
        return cli_quals_cmd._ops_wait_flag_sets(ow, "/r", member_quals=member_quals)


class PrefetchBatchesCommentReads(unittest.TestCase):
    """(a) — one comments-bearing gh call for N members, not N."""

    def test_exactly_one_comments_call_for_all_members(self):
        ow = _ow(41, 42)
        rec = _GhRecorder(COMMENTS)
        _run_flag_sets(ow, ["label:stream:x"], rec)
        # ONE batched list call, ZERO per-issue views.
        self.assertEqual(1, rec.list_calls)
        self.assertEqual([], rec.view_calls)
        self.assertEqual(1, rec.comment_calls)

    def test_one_call_per_member_qual(self):
        # The prefetch runs ONE gh issue list per member-defining qual.
        ow = _ow(41, 42)
        rec = _GhRecorder(COMMENTS)
        _run_flag_sets(ow, ["q1", "q2"], rec)
        self.assertEqual(2, rec.list_calls)
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
        # prefetch path made exactly one.
        self.assertEqual(1, rec_pre.comment_calls)
        self.assertGreater(len(rec_per.view_calls), 1)


class PrefetchMissingMemberFallback(unittest.TestCase):
    """(a) — a member absent from the prefetch falls back to the per-issue call."""

    def test_missing_member_uses_per_issue_view(self):
        ow = _ow(41, 42, 43)
        c = dict(COMMENTS)
        c[43] = [{"author": {"login": "me"}, "createdAt": _OLD, "body": "cakame"}]
        # the batched list returns only 41,42 (43 truncated/label just changed).
        rec = _GhRecorder(c, present={41, 42})
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(1, rec.list_calls)
        self.assertIn(43, rec.view_calls)        # fell back to per-issue
        self.assertNotIn(41, rec.view_calls)     # served from the prefetch
        # 43 (old own comment, per-issue) is still correctly stale!
        self.assertIn(43, sets[0])


class PrefetchTruncationFallback(unittest.TestCase):
    """(a) — gh `issue list --json comments` truncates the nested comments at 100
    (live-verified), while `gh issue view` paginates fully. A prefetch row at the
    cap is EXCLUDED → falls back to the per-issue read (never a false stale!)."""

    def test_over_cap_row_falls_back_and_avoids_false_stale(self):
        import cli_quals
        cap = cli_quals.OPS_WAIT_PREFETCH_COMMENT_CAP
        # member 44: the BATCHED list returns `cap` OLD third-party comments
        # (truncated — the stream's newest CITED push is beyond gh's 100 window);
        # the per-issue VIEW returns those PLUS the newest fresh CITED own push.
        old_batch = [{"author": {"login": "other"}, "createdAt": _OLD,
                      "body": "cakame %d" % i} for i in range(cap)]
        full_view = old_batch + [{"author": {"login": "me"},
                                  "createdAt": _FRESH,
                                  "body": "nasadene vo v1.2.3"}]
        c = {44: full_view}          # what `gh issue view` returns (full)
        lc = {44: old_batch}         # what the batched list returns (truncated)
        ow = _ow(44)
        rec = _GhRecorder(c, list_comments=lc)
        sets = _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual(1, rec.list_calls)
        self.assertIn(44, rec.view_calls)    # cap-excluded → per-issue fallback
        # served from the FULL per-issue view (fresh newest own) → NOT stale.
        self.assertNotIn(44, sets[0])

    def test_under_cap_row_served_from_prefetch(self):
        # a row with < cap comments is served from the prefetch (no per-issue).
        ow = _ow(41)
        rec = _GhRecorder(COMMENTS)
        _run_flag_sets(ow, ["label:stream:x"], rec)
        self.assertEqual([], rec.view_calls)


class PrefetchSkippedWhenNoWMembers(unittest.TestCase):
    """(a) — an EMPTY W set makes ZERO gh calls (the idle-box footer case): the
    prefetch never fires a per-qual `gh issue list` nobody will consume."""

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
