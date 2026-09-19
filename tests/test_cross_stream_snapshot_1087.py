"""#1087 L1 item (b) — the watchdog cross_stream backstops read ONE cached
open-issue snapshot per sweep instead of N `gh issue list` calls.

- `_fetch_gkreq_tickets` collapses its 3 per-query GraphQL calls to ONE snapshot
  fetch (client-side filtered 3 ways).
- `_fetch_bounce_tickets` + `_fetch_gkreq_tickets` on the SAME repo SHARE the
  ETag cache, so the second fetch re-polls for free (a 304), proven with a fake
  runner that counts real 200s.

Hermetic: fake `gates.ghread` runner / seam; no network, no gh.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock as m

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import watchdog as wd
from gates import ghread


def _row(num, labels=(), title=None, updated="2026-09-02T00:00:00Z"):
    it = {"number": num, "title": title or ("t%d" % num),
          "created_at": "2026-09-01T00:00:00Z", "updated_at": updated,
          "labels": [{"name": n} for n in labels], "assignees": [],
          "user": {"login": "someone"}}
    return it


def _include_response(status, etag=None, body="", reason="OK"):
    lines = ["HTTP/2.0 %d %s" % (status, reason),
             "Content-Type: application/json"]
    if etag is not None:
        lines.append("Etag: %s" % etag)
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


class OneFetchPerCall(unittest.TestCase):
    def test_gkreq_makes_one_snapshot_fetch_not_three(self):
        calls = {"n": 0}
        snapshot = [ghread._normalize_issue(_row(5, labels=("needs-gatekeeper",))),
                    ghread._normalize_issue(_row(12, labels=("ready-for-review",))),
                    ghread._normalize_issue(_row(9, title="GATEKEEPER-ACTION: x"))]

        def fake_list(slug, **kw):
            calls["n"] += 1
            return snapshot, None

        with m.patch.object(ghread, "resolve_slug", return_value="o/r"), \
             m.patch.object(ghread, "list_open_issues_cached",
                            side_effect=fake_list):
            got = wd._fetch_gkreq_tickets("/tmp/x")
        self.assertEqual(calls["n"], 1)              # 3 queries -> ONE fetch
        self.assertEqual(got["tickets"], [5, 9])
        self.assertEqual(sorted(got["handoffs"]), [5, 9, 12])


class SharedEtagCacheAcrossFetches(unittest.TestCase):
    """bounce then gkreq on the SAME repo: the second read is a budget-free 304
    off the ETag cache the first populated (with a real fake runner)."""

    def setUp(self):
        self._d = tempfile.mkdtemp(prefix="cs-snap-1087-")
        self._prev = os.environ.get("AIRULESET_GH_ETAG_DIR")
        os.environ["AIRULESET_GH_ETAG_DIR"] = self._d

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AIRULESET_GH_ETAG_DIR", None)
        else:
            os.environ["AIRULESET_GH_ETAG_DIR"] = self._prev

    def test_second_read_is_a_free_304(self):
        body = json.dumps([_row(1, labels=("prio:bounce",))])
        paid = {"n": 0}

        def runner(argv):
            joined = " ".join(argv)
            if "If-None-Match" in joined:
                return 1, _include_response(304, reason="Not Modified"), ""
            paid["n"] += 1                              # a real (200) fetch
            return 0, _include_response(200, etag='W/"e1"', body=body), ""

        r1, e1 = ghread.list_open_issues_cached("o/r", runner=runner)
        r2, e2 = ghread.list_open_issues_cached("o/r", runner=runner)
        self.assertIsNone(e1)
        self.assertIsNone(e2)
        self.assertEqual([x["number"] for x in r1], [1])
        self.assertEqual([x["number"] for x in r2], [1])   # cached body via 304
        self.assertEqual(paid["n"], 1)                     # only ONE paid fetch


if __name__ == "__main__":
    unittest.main()
