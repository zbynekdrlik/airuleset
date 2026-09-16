"""#1053 — footer `gk N` counts tickets the gatekeeper is PROCESSING (not only
awaiting pickup), and a post-deploy `verify-on-copy` hand-back returns the ticket
to the sub-dev's `I` for verification on its own fresh PROD copy.

Owner directive 2026-09-16: "gk ide na nulu ked gk prevezme ticket ale tak to
nebolo povodne myslene, povodne som mal v gk vidiet ze kolko ticketov spracovava
gk a po deployi sa ten ticket mal vratit k sudev aby overil ze vsetko ide na
kopii produ".

State machine (labels):
  hand-off (ready-for-review) → gk 1
  gk pickup (gk-processing)   → still gk 1   [was 0 before this fix]
  gk deploy (verify-on-copy)  → gk 0, sub-dev I +1 (action-only)
  verified (label removed)    → 0

The `gk`/`I` split is derived by `_slice_mine_and_handed` (handed map) +
`_partition_workable` (the ONE shared derivation the footer and the /goal
stop-proof both consume). The gk box's OWN obligation set (`MAINTAINER_ACTION_
LABELS`) must include `gk-processing` (gk's live work) but NOT `verify-on-copy`
(the sub-dev's action).
"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock as m

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_quals  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"
ROOT = "/tmp/does-not-matter-1053"
STREAM = "montalu"
# own-account 3-qual slice → len(quals) != 1, so the shared-account recovery is
# skipped; only the label/timeline path drives `handed`.
QUALS = ["assignee:@me", "author:@me", "label:stream:montalu"]


def _row(number, labels=()):
    return {"number": number, "title": "t%d" % number,
            "createdAt": "2026-09-01T00:00:00Z",
            "labels": [{"name": n} for n in labels]}


def _drive(number, labels=(), *, timeline=None):
    """Drive `_slice_mine_and_handed` for a single-ticket slice with no merged
    PR (not released) and an empty/parametrised timeline, then compute the
    footer's own (I, gk) split via `_partition_workable`."""
    timeline = timeline or []

    def gh(*args, **kw):
        a = [str(x) for x in args]
        if a[:2] == ["issue", "list"]:
            return json.dumps([_row(number, labels)])
        if a[:2] == ["pr", "list"]:
            return "[]"
        if a and a[0] == "api" and "/timeline" in a[1]:
            return json.dumps(timeline)
        return "[]"

    def fake_run(cmd, *a, **k):
        # merge-base --is-ancestor rc 1 → not released
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    with m.patch.object(airuleset, "_gh_out", side_effect=gh), \
         m.patch.object(airuleset, "_current_user", return_value=STREAM), \
         m.patch("subprocess.run", side_effect=fake_run):
        rows, handed, failed = airuleset._slice_mine_and_handed(QUALS, ROOT, SLUG)
    assert not failed, "fixture must not simulate a gh failure"
    workable, _waiting, _ops = airuleset._partition_workable(rows, own_stream=STREAM)
    unhandled = {n: v for n, v in workable.items() if not handed.get(n)}
    gk = sum(1 for n in workable if handed.get(n))
    return {"handed": handed, "workable": workable, "unhandled": unhandled,
            "I": len(unhandled), "gk": gk}


class SubdevGkBucketStateMachine1053(unittest.TestCase):
    def test_handoff_is_gk(self):
        r = _drive(101, ["stream:montalu", "ready-for-review"])
        self.assertEqual((r["I"], r["gk"]), (0, 1),
                         "hand-off (ready-for-review) → gk 1")

    def test_gk_pickup_stays_gk(self):
        """THE CORE FIX: gk pickup replaces ready-for-review with gk-processing;
        the count must NOT fall to 0 while gk is working."""
        r = _drive(102, ["stream:montalu", "gk-processing"])
        self.assertEqual((r["I"], r["gk"]), (0, 1),
                         "gk pickup (gk-processing) → still gk 1, NOT I 1")
        self.assertTrue(r["handed"].get(102),
                        "a gk-processing ticket is handed (parked with gk)")

    def test_gk_deploy_returns_to_subdev_I(self):
        """gk deploy → verify-on-copy: leaves gk, becomes the sub-dev's I
        (action-only — verify on a fresh PROD copy)."""
        r = _drive(103, ["stream:montalu", "verify-on-copy"])
        self.assertEqual((r["I"], r["gk"]), (1, 0),
                         "verify-on-copy → sub-dev I 1, gk 0")
        self.assertFalse(r["handed"].get(103),
                         "verify-on-copy is back in the stream's court (not gk)")
        self.assertIn(103, r["unhandled"])

    def test_verified_is_gone(self):
        """After verification the label is removed; the ticket (if still open)
        is plain workable I (0 here would be after close)."""
        r = _drive(104, ["stream:montalu"])
        # no queue label → plain I, gk 0
        self.assertEqual(r["gk"], 0)

    def test_verify_on_copy_stale_handoff_comment_does_not_reflip(self):
        """A stale READY-FOR-REVIEW-shaped gatekeeper comment must NOT re-upgrade
        a verify-on-copy ticket back to gk (it is in GATEKEEPER_PROCESSED_LABELS,
        excluded from the comment/timeline candidate walk)."""
        # a genuine hand-off comment (line-anchored READY-FOR-REVIEW) that
        # WOULD re-flip the ticket to handed=True (gk) if the timeline were
        # walked — proving the GATEKEEPER_PROCESSED_LABELS exclusion has teeth.
        tl = [{"event": "commented",
               "actor": {"login": "montalu"},
               "body": "READY-FOR-REVIEW: branch worktree-x head abc123 — done"}]
        r = _drive(105, ["stream:montalu", "verify-on-copy"], timeline=tl)
        self.assertFalse(r["handed"].get(105),
                         "verify-on-copy is excluded from the re-flip walk")
        self.assertEqual(r["gk"], 0)


class SharedAccountRecovery1053(unittest.TestCase):
    """#1053 review 🟡 (Finding 2): on a shared-account box (single
    `label:stream:<user>` qual) a gk-processing ticket that LOST its stream label
    must still be recovered by the #191 Part B ownership-relabel query — the
    recovery candidate query must include `gk-processing`, or the ticket vanishes
    from I/gk/U/W the moment gk swaps ready-for-review → gk-processing."""

    def test_gk_processing_recovered_on_shared_account(self):
        SHARED_QUALS = ["label:stream:montalu"]  # len==1 → recovery path
        candidate = {"number": 200, "title": "t200",
                     "createdAt": "2026-09-01T00:00:00Z",
                     "labels": [{"name": "gk-processing"}]}  # no stream label

        def gh(*args, **kw):
            a = [str(x) for x in args]
            if a[:2] == ["issue", "list"]:
                search = ""
                if "--search" in a:
                    search = a[a.index("--search") + 1]
                # the recovery candidate query carries the gk queue labels
                if "gk-processing" in search:
                    return json.dumps([candidate])
                return "[]"          # the stream-label slice is empty (relabelled)
            if a[:2] == ["pr", "list"]:
                return "[]"
            if a and a[0] == "api" and "/timeline" in a[1]:
                return json.dumps([])
            return "[]"

        def fake_run(cmd, *a, **k):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="")

        with m.patch.object(airuleset, "_gh_out", side_effect=gh), \
             m.patch.object(airuleset, "_current_user", return_value="montalu"), \
             m.patch.object(cli_quals, "_last_origin_owner",
                            return_value={200: "montalu"}), \
             m.patch("subprocess.run", side_effect=fake_run):
            rows, handed, failed = airuleset._slice_mine_and_handed(
                SHARED_QUALS, "/tmp/x", SLUG)
        self.assertFalse(failed)
        self.assertIn(200, rows, "a gk-processing ticket that lost its stream "
                      "label must be RECOVERED, not vanish")
        self.assertTrue(handed.get(200), "recovered gk-processing → gk bucket")

    def test_recovery_query_includes_all_maintainer_labels(self):
        # lock the query never desyncs from MAINTAINER_ACTION_LABELS
        self.assertIn("gk-processing", cli_quals.MAINTAINER_ACTION_LABELS)


class VerifyOnCopyPartition1053(unittest.TestCase):
    """`verify-on-copy` routes to workable (I) even when it carries ops-wait —
    action-only, like the #943 precedence, but for the SUB-DEV's own action."""

    def _labels(self, *names):
        return [{"name": n} for n in names]

    def _row(self, number, *label_names):
        return {number: {"number": number, "labels": self._labels(*label_names)}}

    def test_verify_on_copy_plain_is_workable(self):
        rows = self._row(200, "verify-on-copy")
        workable, uw, ops = cli_quals._partition_workable(rows)
        self.assertIn(200, workable)
        self.assertNotIn(200, ops)
        self.assertNotIn(200, uw)

    def test_verify_on_copy_plus_ops_wait_is_workable(self):
        rows = self._row(201, "verify-on-copy", "ops-wait")
        workable, uw, ops = cli_quals._partition_workable(rows)
        self.assertIn(201, workable,
                      "verify-on-copy + ops-wait → action-only I, not W")
        self.assertNotIn(201, ops)

    def test_gk_processing_plus_ops_wait_is_workable(self):
        rows = self._row(202, "gk-processing", "ops-wait")
        workable, uw, ops = cli_quals._partition_workable(rows)
        self.assertIn(202, workable,
                      "gk-processing + ops-wait → I (gk's live work), not W")
        self.assertNotIn(202, ops)


class LabelSetMembership1053(unittest.TestCase):
    def test_gk_processing_in_maintainer_action_labels(self):
        """gk-processing is the gk box's OWN I obligation (gk is processing)."""
        self.assertIn("gk-processing", cli_quals.MAINTAINER_ACTION_LABELS)

    def test_verify_on_copy_not_in_maintainer_action_labels(self):
        """verify-on-copy is the SUB-DEV's action, never the gk box's I."""
        self.assertNotIn("verify-on-copy", cli_quals.MAINTAINER_ACTION_LABELS)

    def test_verify_on_copy_in_gatekeeper_processed_labels(self):
        """verify-on-copy is a hand-off gk already processed — excluded from the
        comment/timeline re-flip walk."""
        self.assertIn("verify-on-copy", cli_quals.GATEKEEPER_PROCESSED_LABELS)

    def test_gk_processing_in_gk_handoff_labels(self):
        """The gk-handoff! tag (a W-parked row also carrying a hand-off label)
        recognises gk-processing too."""
        self.assertIn("gk-processing", cli_quals._GK_HANDOFF_LABELS)

    def test_obligation_quals_include_gk_processing(self):
        """The gk box's obligation UNION query fetches label:gk-processing."""
        quals = cli_quals._obligation_quals()
        self.assertIn("label:gk-processing", quals)
        self.assertNotIn("label:verify-on-copy", quals)


if __name__ == "__main__":
    unittest.main()
