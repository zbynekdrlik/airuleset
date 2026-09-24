"""Issue 1130: a FOREIGN stream's `needs-acceptance` ticket that carries
`gk-processing` must never land in the full-authority owner's `U`.

Observed on the gk box (odoo-erp#8002, labels `tenant:slovnormal,
needs-acceptance, stream:david4, gk-processing`): footer `U 1`,
`core-quals --waiting` tagged it `queued`. The row re-enters the core set via
the `label:gk-processing` obligation qual (#1053 added `gk-processing` to
MAINTAINER_ACTION_LABELS), and `_partition_workable` sent it to U because the
#507 needs-acceptance gk-override (NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS) still
listed only `ready-for-review`/`needs-gatekeeper` (+ `prio:bounce`).

Fix (Design-by: main, Approach 1): derive every copy of the gk hand-off label
set ONCE from MAINTAINER_ACTION_LABELS, so a future hand-off label cannot
drift the copies apart again.

#1141 slice 2 (owner ruling in the #1141 design comment: "an owner question
beats any hand-off label", and "on the full-authority box a FOREIGN stream's
question is hidden") re-pins the routing locks below: the foreign unsent
acceptance in a hand-off state is HIDDEN on the gk box (still never its U),
and an own unsent acceptance in a hand-off state is U. The label-set
derivation locks are unchanged.
"""
import json
import types
import unittest
from unittest import mock as m

import airuleset
import cli_quals

FOREIGN_8002 = ("tenant:slovnormal", "needs-acceptance", "stream:david4",
                "gk-processing")


def _row(number, labels=()):
    return {"number": number, "title": "t%d" % number,
            "createdAt": "2026-09-01T00:00:00Z",
            "labels": [{"name": n} for n in labels]}


def _full_box(labels):
    """The full-authority (gk) box partition: own_stream=None."""
    return cli_quals._partition_workable({8002: _row(8002, labels)},
                                         own_stream=None)


# A reduced-authority stream the fixture names, independent of the live fleet
# table (a future david4 decommission must not flip these tests).
_FLEET = m.patch.dict(airuleset.AUTHORITY_BY_USER, {"david4": "fork-no-merge"})


class FullBoxForeignAcceptance1130(unittest.TestCase):

    def test_foreign_acceptance_gk_processing_not_in_U(self):
        with _FLEET:
            workable, waiting, ops_wait = _full_box(FOREIGN_8002)
        self.assertNotIn(8002, waiting,
                         "a foreign stream's acceptance in gk-processing is "
                         "never the full-authority owner's U")
        # RE-PINNED by #1141 slice 2: hidden (it counts in stream david4's U)
        self.assertNotIn(8002, workable)
        self.assertNotIn(8002, ops_wait)

    def test_foreign_acceptance_ready_for_review_hidden(self):
        labels = ("tenant:slovnormal", "needs-acceptance", "stream:david4",
                  "ready-for-review")
        with _FLEET:
            workable, waiting, _ops = _full_box(labels)
        self.assertNotIn(8002, workable)   # RE-PINNED by #1141 slice 2
        self.assertNotIn(8002, waiting)

    def test_foreign_acceptance_gk_processing_with_ops_wait_stays_I(self):
        """A stale ops-wait never hides gk's live work in W either."""
        workable, waiting, ops_wait = _full_box(FOREIGN_8002 + ("ops-wait",))
        self.assertIn(8002, workable)
        self.assertNotIn(8002, waiting)
        self.assertNotIn(8002, ops_wait)

    def test_own_core_acceptance_in_gk_processing_is_U(self):
        """RE-PINNED by #1141 slice 2: the box's OWN unsent acceptance is an
        owner question, and it beats the gk-processing hand-off label → U."""
        workable, waiting, _o = _full_box(("stream:core", "needs-acceptance",
                                           "gk-processing"))
        self.assertIn(8002, waiting)
        self.assertNotIn(8002, workable)

    def test_own_core_row_with_gk_processing_unchanged(self):
        workable, waiting, ops_wait = _full_box(("stream:core",
                                                 "gk-processing"))
        self.assertIn(8002, workable)
        self.assertNotIn(8002, waiting)
        self.assertNotIn(8002, ops_wait)

    def test_bare_foreign_acceptance_still_search_excluded(self):
        """Without gk-processing the foreign acceptance never reaches the
        partition: the core slice search-excludes its stream label."""
        with _FLEET:
            self.assertIn("-label:stream:david4",
                          cli_quals._core_search_excl())

    def test_own_bare_acceptance_still_U(self):
        """#622 unchanged: the box's OWN bare needs-acceptance is the owner's
        approval queue → U."""
        _w, waiting, _o = _full_box(("stream:core", "needs-acceptance"))
        self.assertIn(8002, waiting)


class HandoffLabelSetsDeriveOnce1130(unittest.TestCase):
    """Every copy of the gk hand-off label set is derived from
    MAINTAINER_ACTION_LABELS — the drift guard the design asked for."""

    def test_override_covers_every_maintainer_label(self):
        for lb in cli_quals.MAINTAINER_ACTION_LABELS:
            self.assertIn(lb, cli_quals.NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS)
        self.assertIn("prio:bounce",
                      cli_quals.NEEDS_ACCEPTANCE_GK_OVERRIDE_LABELS)

    def test_gk_handoff_flag_covers_every_maintainer_label(self):
        """Behavioural drift guard: a W-parked row with ANY maintainer label
        is the `gk-handoff!` contradiction."""
        for lb in cli_quals.MAINTAINER_ACTION_LABELS:
            with self.subTest(label=lb):
                rows = {1: _row(1, ("ops-wait", lb))}
                self.assertEqual({1},
                                 cli_quals._gk_handoff_ops_wait_flagged(rows))

    def test_row_is_user_waiting_overridden_by_each_maintainer_label(self):
        for lb in cli_quals.MAINTAINER_ACTION_LABELS:
            with self.subTest(label=lb):
                labels = [{"name": "needs-acceptance"}, {"name": lb}]
                self.assertFalse(cli_quals._row_is_user_waiting(labels))
                self.assertEqual("ops-wait", cli_quals._ops_wait_reason(
                    labels + [{"name": "ops-wait"}]))


SLUG = "zbynekdrlik/odoo-erp"
ROOT = "/tmp/does-not-matter-1130"
STREAM = "montalu1"   # the canonical key `_stream_owner_of` returns (#1141)
QUALS = ["assignee:@me", "author:@me", "label:stream:montalu"]


def _drive_slice(number, labels):
    """The owning stream's slice box: `_slice_mine_and_handed` + partition,
    mirroring test_gk_processing_verify_copy_1053._drive."""
    def gh(*args, **kw):
        a = [str(x) for x in args]
        if a[:2] == ["issue", "list"]:
            return json.dumps([_row(number, labels)])
        return "[]"

    def fake_run(cmd, *a, **k):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    with m.patch.object(airuleset, "_gh_out", side_effect=gh), \
         m.patch.object(airuleset, "_current_user", return_value=STREAM), \
         m.patch("subprocess.run", side_effect=fake_run):
        rows, handed, failed = airuleset._slice_mine_and_handed(
            QUALS, ROOT, SLUG)
    assert not failed
    workable, waiting, ops_wait = airuleset._partition_workable(
        rows, own_stream=STREAM)
    gk = sum(1 for n in workable if handed.get(n))
    return {"workable": workable, "waiting": waiting, "gk": gk}


class OwningStreamSliceBox1130(unittest.TestCase):
    """On the owning stream's box, gk picking up a needs-acceptance
    re-hand-off (ready-for-review → gk-processing) treats both forms alike.

    RE-PINNED by #1141 slice 2 (owner ruling in the #1141 design comment: "an
    owner question beats any hand-off label"): an UNSENT acceptance in either
    hand-off state is the owner's court on its owning box → U, not gk. The
    box's own stream is its canonical key (`montalu1`, what `_stream_owner_of`
    resolves `stream:montalu` to): the former `montalu` spelling made the row
    read as FOREIGN, so these tests never exercised the owning box."""

    def test_acceptance_ready_for_review_is_U(self):
        r = _drive_slice(301, ["stream:montalu", "needs-acceptance",
                               "ready-for-review"])
        self.assertEqual(r["gk"], 0)
        self.assertIn(301, r["waiting"])

    def test_acceptance_gk_processing_is_U(self):
        r = _drive_slice(302, ["stream:montalu", "needs-acceptance",
                               "gk-processing"])
        self.assertEqual(r["gk"], 0)
        self.assertIn(302, r["waiting"])


if __name__ == "__main__":
    unittest.main()
