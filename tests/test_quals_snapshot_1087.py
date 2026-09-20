"""#1087 L1 item (b) -- `_union_open_issues` converges on ONE cached REST
snapshot + client-side qual filter, spending one GraphQL search per qual only
for a qual the client-side matcher can't represent (or when the snapshot can't
be read). The counts must be byte-identical to the old per-qual GraphQL union.

Hermetic: patch `gates.ghread.list_open_issues_cached` / `canonical_slug` and
`airuleset._gh_out` / `airuleset._gh_login`; no network, no gh.
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

import airuleset
import cli_quals
from gates import ghread


class _SnapshotEnabled(unittest.TestCase):
    """This file EXERCISES the snapshot path, so it must opt IN — conftest's
    autouse fixture and cmd_push's Pass B both set AIRULESET_QUALS_NO_SNAPSHOT=1
    to force the GraphQL fallback for every OTHER (hermetic) quals test."""

    def setUp(self):
        self._prev = os.environ.get("AIRULESET_QUALS_NO_SNAPSHOT")
        os.environ["AIRULESET_QUALS_NO_SNAPSHOT"] = "0"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("AIRULESET_QUALS_NO_SNAPSHOT", None)
        else:
            os.environ["AIRULESET_QUALS_NO_SNAPSHOT"] = self._prev


def _snap(n, labels=None, title=None, assignee=None, author=None):
    return ghread._normalize_issue({
        "number": n, "title": title or ("t%d" % n),
        "created_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-02T00:00:00Z",
        "labels": [{"name": nm} for nm in (labels or [])],
        "assignees": [{"login": a} for a in (assignee or [])],
        "user": {"login": author or "someone"}})


class UnionUsesSnapshot(_SnapshotEnabled):
    def test_label_quals_filter_snapshot_no_graphql(self):
        snapshot = [
            _snap(1, labels=["needs-gatekeeper"]),
            _snap(2, labels=["ready-for-review"]),
            _snap(3, labels=["stream:montalu1"]),
            _snap(4, labels=[]),
            _snap(5, labels=["autopilot-skip"]),
        ]
        base = "-label:autopilot-skip -label:ops-channel"
        quals = ["-label:stream:montalu1", "label:needs-gatekeeper",
                 "label:ready-for-review"]

        with mock.patch.object(ghread, "canonical_slug", return_value="o/r"), \
             mock.patch.object(ghread, "list_open_issues_cached",
                               return_value=(snapshot, None)) as m_snap, \
             mock.patch.object(airuleset, "_gh_out") as m_gh:
            seen, failed = cli_quals._union_open_issues(quals, base, cwd="/x")

        self.assertFalse(failed)
        m_gh.assert_not_called()                       # NO GraphQL search spent
        self.assertEqual(m_snap.call_count, 1)          # one snapshot fetch
        # core-excl qual (-label:stream:montalu1) matches 1,2,4 (not 3 stream,
        # not 5 skip); the two label quals add nothing new -> {1,2,4}
        self.assertEqual(set(seen), {1, 2, 4})

    def test_non_client_side_qual_falls_back_to_graphql(self):
        snapshot = [_snap(1, labels=["needs-gatekeeper"])]
        base = "-label:autopilot-skip"
        quals = ['"GATEKEEPER-ACTION:" in:title']       # free-text -> GraphQL

        def fake_gh(*args, **kw):
            return json.dumps([{"number": 99, "title": "GATEKEEPER-ACTION: x",
                                "createdAt": "2026-09-01T00:00:00Z",
                                "labels": []}])

        with mock.patch.object(ghread, "canonical_slug", return_value="o/r"), \
             mock.patch.object(ghread, "list_open_issues_cached",
                               return_value=(snapshot, None)), \
             mock.patch.object(airuleset, "_gh_out", side_effect=fake_gh) as m_gh:
            seen, failed = cli_quals._union_open_issues(quals, base, cwd="/x")

        self.assertFalse(failed)
        m_gh.assert_called_once()                        # fell back for this qual
        self.assertEqual(set(seen), {99})

    def test_snapshot_unavailable_uses_graphql_for_every_qual(self):
        base = "-label:autopilot-skip"
        quals = ["label:needs-gatekeeper", "label:ready-for-review"]

        def fake_gh(*args, **kw):
            # each qual returns its own number
            search = args[args.index("--search") + 1]
            n = 11 if "needs-gatekeeper" in search else 22
            return json.dumps([{"number": n, "title": "x",
                                "createdAt": "2026-09-01T00:00:00Z",
                                "labels": []}])

        with mock.patch.object(ghread, "canonical_slug", return_value="o/r"), \
             mock.patch.object(ghread, "list_open_issues_cached",
                               return_value=(None, "gate-unavailable: quota")), \
             mock.patch.object(airuleset, "_gh_out", side_effect=fake_gh) as m_gh:
            seen, failed = cli_quals._union_open_issues(quals, base, cwd="/x")

        self.assertFalse(failed)
        self.assertEqual(m_gh.call_count, 2)             # one per qual (fallback)
        self.assertEqual(set(seen), {11, 22})

    def test_me_quals_resolve_login_once_and_filter(self):
        snapshot = [
            _snap(1, assignee=["dave"]),
            _snap(2, author="dave"),
            _snap(3, author="zed", assignee=["zed"]),
            _snap(4, labels=["stream:david1"]),
        ]
        base = "-label:autopilot-skip"
        quals = ["assignee:@me", "author:@me", "label:stream:david1"]

        with mock.patch.object(ghread, "canonical_slug", return_value="o/r"), \
             mock.patch.object(ghread, "list_open_issues_cached",
                               return_value=(snapshot, None)), \
             mock.patch.object(airuleset, "_gh_login",
                               return_value="dave") as m_login, \
             mock.patch.object(airuleset, "_gh_out") as m_gh:
            seen, failed = cli_quals._union_open_issues(quals, base, cwd="/x")

        self.assertFalse(failed)
        m_gh.assert_not_called()
        self.assertLessEqual(m_login.call_count, 1)      # login resolved at most once
        self.assertEqual(set(seen), {1, 2, 4})           # dave-assigned, dave-authored, stream:david1

    def test_returned_rows_have_the_expected_shape(self):
        snapshot = [_snap(7, labels=["needs-gatekeeper"], title="hello")]
        with mock.patch.object(ghread, "canonical_slug", return_value="o/r"), \
             mock.patch.object(ghread, "list_open_issues_cached",
                               return_value=(snapshot, None)), \
             mock.patch.object(airuleset, "_gh_out"):
            seen, _ = cli_quals._union_open_issues(
                ["label:needs-gatekeeper"], "-label:autopilot-skip", cwd="/x")
        row = seen[7]
        self.assertEqual(set(row), {"number", "title", "createdAt", "labels"})
        self.assertEqual(row["title"], "hello")
        self.assertEqual(row["labels"], [{"name": "needs-gatekeeper"}])


if __name__ == "__main__":
    unittest.main()
