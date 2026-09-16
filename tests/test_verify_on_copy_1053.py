"""#1053 — verify-on-copy hand-back obligation: pure classifiers + persisted
status for the Stop-hook gate (cli_verify_on_copy)."""
import sys
import time
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import cli_verify_on_copy as voc  # noqa: E402

DAY = 24 * 3600


def _labeled(ts_iso, name="verify-on-copy"):
    return {"event": "labeled", "label": {"name": name}, "created_at": ts_iso}


def _commented(ts_iso, body):
    return {"event": "commented", "created_at": ts_iso, "body": body}


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


class VerifiedMarker(unittest.TestCase):
    def test_genuine_marker(self):
        self.assertTrue(voc.has_verified_marker(
            "Verified-on-copy: refresh r7 at 2026-09-16T10:00:00Z — checked X"))

    def test_marker_with_bullet_prefix(self):
        self.assertTrue(voc.has_verified_marker(
            "- **Verified-on-copy:** refresh r7 — ok"))

    def test_mid_sentence_mention_is_not_a_marker(self):
        self.assertFalse(voc.has_verified_marker(
            "I still need to post the Verified-on-copy: line later."))

    def test_quoted_is_not_a_marker(self):
        self.assertFalse(voc.has_verified_marker(
            "> Verified-on-copy: from an old deploy"))
        # a quoted '>' line IS allowed by the prefix class; ensure a plain
        # non-anchored mention is rejected
        self.assertFalse(voc.has_verified_marker("see Verified-on-copy note"))

    def test_empty(self):
        self.assertFalse(voc.has_verified_marker(""))
        self.assertFalse(voc.has_verified_marker(None))


class LabelAnchor(unittest.TestCase):
    def test_latest_label_add_wins(self):
        now = time.time()
        events = [_labeled(_iso(now - 5 * DAY)), _labeled(_iso(now - 2 * DAY))]
        anchor = voc.label_add_anchor(events)
        self.assertAlmostEqual(anchor, now - 2 * DAY, delta=2)

    def test_other_label_ignored(self):
        now = time.time()
        events = [_labeled(_iso(now - 2 * DAY), name="ready-for-review")]
        self.assertIsNone(voc.label_add_anchor(events))

    def test_no_label_event(self):
        self.assertIsNone(voc.label_add_anchor([_commented(_iso(1), "x")]))
        self.assertIsNone(voc.label_add_anchor([]))
        self.assertIsNone(voc.label_add_anchor("not a list"))


class Overdue(unittest.TestCase):
    def test_overdue_when_old_and_unverified(self):
        now = time.time()
        events = [_labeled(_iso(now - 2 * DAY))]
        rec = voc.timeline_overdue(42, "t42", events, now)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["number"], 42)
        self.assertGreaterEqual(rec["age_h"], 47)

    def test_not_overdue_when_recent(self):
        now = time.time()
        events = [_labeled(_iso(now - 3600))]
        self.assertIsNone(voc.timeline_overdue(42, "t42", events, now))

    def test_not_overdue_when_verified_after_anchor(self):
        now = time.time()
        events = [_labeled(_iso(now - 2 * DAY)),
                  _commented(_iso(now - 1 * DAY),
                             "Verified-on-copy: refresh r9 — ok")]
        self.assertIsNone(voc.timeline_overdue(42, "t42", events, now))

    def test_verification_BEFORE_anchor_does_not_count(self):
        """A verification for a PRIOR deploy (before the current label-add) does
        NOT satisfy the current hand-back."""
        now = time.time()
        events = [_commented(_iso(now - 3 * DAY),
                             "Verified-on-copy: old deploy — ok"),
                  _labeled(_iso(now - 2 * DAY))]
        rec = voc.timeline_overdue(42, "t42", events, now)
        self.assertIsNotNone(rec, "an old verification must not clear the new "
                             "hand-back")

    def test_no_anchor_never_overdue(self):
        now = time.time()
        self.assertIsNone(voc.timeline_overdue(42, "t42",
                          [_commented(_iso(now), "hi")], now))

    def test_compute_overdue_filters(self):
        now = time.time()
        items = [
            (1, "old-unverified", [_labeled(_iso(now - 2 * DAY))]),
            (2, "recent", [_labeled(_iso(now - 3600))]),
            (3, "verified", [_labeled(_iso(now - 2 * DAY)),
                             _commented(_iso(now - DAY),
                                        "Verified-on-copy: ok")]),
        ]
        out = voc.compute_overdue(items, now)
        self.assertEqual([r["number"] for r in out], [1])


class PersistRead(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="voc1053-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)

    def test_persist_and_read(self):
        overdue = [{"number": 7, "title": "t7", "age_h": 30}]
        voc.persist_status(overdue, home=self.home, now=123.0, repo="o/r")
        st = voc.read_status(home=self.home)
        self.assertEqual(st["overdue"], overdue)
        self.assertEqual(st["ts"], 123.0)
        self.assertEqual(st["repo"], "o/r")

    def test_read_absent_is_none(self):
        self.assertIsNone(voc.read_status(home=self.home))

    def test_read_corrupt_is_none(self):
        p = voc.status_path(self.home)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        self.assertIsNone(voc.read_status(home=self.home))

    def test_status_path_shape(self):
        p = voc.status_path(self.home)
        self.assertTrue(str(p).endswith(".claude/verify-on-copy/status.json"))


class WriterWiring(unittest.TestCase):
    """`airuleset._write_verify_on_copy_status` collects verify-on-copy rows from
    the footer slice, reads their timelines, and persists the overdue set."""

    def setUp(self):
        import os
        self.home = tempfile.mkdtemp(prefix="voc1053-writer-")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        self.addCleanup(self._restore_home)

    def _restore_home(self):
        import os
        if self._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._old_home

    def _row(self, number, labels):
        return {"number": number, "title": "t%d" % number,
                "createdAt": "2026-09-01T00:00:00Z",
                "labels": [{"name": n} for n in labels]}

    def test_overdue_verify_on_copy_persisted(self):
        import json as _json
        from unittest import mock as m
        import airuleset

        now = time.time()
        rows = {
            10: self._row(10, ["stream:montalu", "verify-on-copy"]),  # overdue
            11: self._row(11, ["stream:montalu"]),                    # not voc
        }

        def gh(*args, **kw):
            a = [str(x) for x in args]
            if a and a[0] == "api" and "/issues/10/timeline" in a[1]:
                return _json.dumps([_labeled(_iso(now - 2 * DAY))])
            return "[]"

        with m.patch.object(airuleset, "_gh_out", side_effect=gh):
            airuleset._write_verify_on_copy_status(
                rows, "zbynekdrlik/odoo-erp", "/tmp/x", now=now)
        st = voc.read_status(home=self.home)
        self.assertEqual([r["number"] for r in st["overdue"]], [10])

    def test_verified_not_persisted_and_status_is_fresh(self):
        import json as _json
        from unittest import mock as m
        import airuleset

        now = time.time()
        rows = {12: self._row(12, ["stream:montalu", "verify-on-copy"])}

        def gh(*args, **kw):
            a = [str(x) for x in args]
            if a and a[0] == "api" and "/issues/12/timeline" in a[1]:
                return _json.dumps([_labeled(_iso(now - 2 * DAY)),
                                    _commented(_iso(now - DAY),
                                               "Verified-on-copy: ok")])
            return "[]"

        with m.patch.object(airuleset, "_gh_out", side_effect=gh):
            airuleset._write_verify_on_copy_status(
                rows, "zbynekdrlik/odoo-erp", "/tmp/x", now=now)
        st = voc.read_status(home=self.home)
        self.assertEqual(st["overdue"], [])  # verified → not overdue
        self.assertEqual(st["ts"], now)      # written fresh

    def test_no_voc_rows_writes_empty_fresh(self):
        from unittest import mock as m
        import airuleset

        now = time.time()
        rows = {13: self._row(13, ["stream:montalu", "ready-for-review"])}
        with m.patch.object(airuleset, "_gh_out", return_value="[]"):
            airuleset._write_verify_on_copy_status(
                rows, "zbynekdrlik/odoo-erp", "/tmp/x", now=now)
        st = voc.read_status(home=self.home)
        self.assertEqual(st["overdue"], [])
        self.assertEqual(st["ts"], now)


if __name__ == "__main__":
    unittest.main()
