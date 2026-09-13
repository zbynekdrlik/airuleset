"""#1009 — a merged+released stream ticket must LEAVE the stream box's `I`.

After a stream PR merges into develop, the odoo-erp Sub-dev Handoff Gate re-runs
from `main` and STRIPS `ready-for-review`; GitHub does not auto-close (merge went
to develop, not the default branch), so the ticket stays OPEN with no hand-off
label. `_slice_mine_and_handed` then reads `handed=False` and the ticket falls
back into the stream's workable `I`, although the stream's authority ended at the
develop merge — release + close are the gk's. (montalu footer `I` grew to 22
incl. 9 such tickets.)

Fix: `_slice_mine_and_handed` detects the DONE state at the cause — the ticket's
stream PR is MERGED and its merge commit is an ancestor of origin/main (the SAME
`git merge-base --is-ancestor` predicate the /goal release proof uses) — and
marks `handed[n]="released"`, a distinct truthy state that leaves `I`/workable in
BOTH the footer and slice-quals and counts in `gk`. hand-off states
(needs-gatekeeper, prio:bounce) untouched; `--list` tags such a row `released`.
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
import cli_quals  # noqa: E402,F401

SLUG = "zbynekdrlik/odoo-erp"
ROOT = "/tmp/does-not-matter-1009"
STREAM = "montalu"
# An own-account 3-qual slice: len(quals) != 1, so the shared-account
# `_last_origin_owner` recovery is skipped — only the label/timeline/released
# path drives `handed`, exactly the path under test.
QUALS = ["assignee:@me", "author:@me", "label:stream:montalu"]


def _row(number, labels=()):
    return {"number": number, "title": "t%d" % number,
            "createdAt": "2026-09-01T00:00:00Z",
            "labels": [{"name": n} for n in labels]}


def _drive(number, labels=(), *, merged_oid=None, released=True,
           timeline=None):
    """Drive `_slice_mine_and_handed` for a single-ticket slice with a mocked
    `gh pr list` (merged PR + mergeCommit oid, or none) and a mocked
    `git merge-base --is-ancestor` (rc 0 = released, rc 1 = not)."""
    timeline = timeline or []

    def gh(*args, **kw):
        a = [str(x) for x in args]
        if a[:2] == ["issue", "list"]:
            return json.dumps([_row(number, labels)])
        if a[:2] == ["pr", "list"]:
            if merged_oid:
                return json.dumps([{
                    "mergeCommit": {"oid": merged_oid},
                    "headRefName": "%s/%d-fix" % (STREAM, number)}])
            return "[]"
        if a and a[0] == "api" and "/timeline" in a[1]:
            return json.dumps(timeline)
        return "[]"

    def fake_run(cmd, *a, **k):
        cmd = list(cmd)
        if "merge-base" in cmd:
            rc = 0 if released else 1
            return types.SimpleNamespace(returncode=rc, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    with m.patch.object(airuleset, "_gh_out", side_effect=gh), \
         m.patch.object(airuleset, "_current_user", return_value=STREAM), \
         m.patch("subprocess.run", side_effect=fake_run):
        rows, handed, failed = airuleset._slice_mine_and_handed(QUALS, ROOT, SLUG)
    assert not failed, "fixture must not simulate a gh failure"
    workable, _waiting, _ops = airuleset._partition_workable(rows, own_stream=STREAM)
    unhandled = {n: v for n, v in workable.items() if not handed.get(n)}
    gk = sum(1 for n in workable if handed.get(n))
    return rows, handed, workable, unhandled, gk


class MergedReleasedLeavesI(unittest.TestCase):
    def test_merged_and_released_ticket_is_not_in_I(self):
        # No hand-off label (gate stripped ready-for-review), PR merged+released.
        _rows, handed, _workable, unhandled, gk = _drive(
            6942, labels=[], merged_oid="abc1234")
        self.assertNotIn(6942, unhandled, "merged+released ticket must leave I")
        self.assertTrue(handed.get(6942), "must be handed (released), not workable")
        self.assertEqual(gk, 1, "a released ticket counts in gk (release/close)")

    def test_released_state_is_distinct(self):
        _rows, handed, *_ = _drive(6942, labels=[], merged_oid="abc1234")
        self.assertEqual(handed.get(6942), "released")

    def test_open_pr_ticket_stays_in_I(self):
        # No merged PR yet (open PR) → not released → stays workable.
        _rows, handed, _workable, unhandled, _gk = _drive(
            7000, labels=[], merged_oid=None)
        self.assertIn(7000, unhandled, "an open-PR ticket is still the stream's I")
        self.assertFalse(handed.get(7000))

    def test_merged_but_not_released_stays_in_I(self):
        # merged into develop but merge commit NOT yet an ancestor of main.
        _rows, handed, _workable, unhandled, _gk = _drive(
            7001, labels=[], merged_oid="def5678", released=False)
        self.assertIn(7001, unhandled, "merged-but-unreleased is not DONE yet")
        self.assertFalse(handed.get(7001))


class HandoffStatesUntouched(unittest.TestCase):
    def test_needs_gatekeeper_stays_handed_by_label_not_released(self):
        # A hand-off ticket is handed by label and never reaches the released
        # check — its handed value stays the label bool, gk unchanged.
        _rows, handed, _workable, unhandled, gk = _drive(
            6900, labels=["needs-gatekeeper"], merged_oid="abc1234")
        self.assertNotIn(6900, unhandled)
        self.assertIs(handed.get(6900), True)   # label-handed, not "released"
        self.assertEqual(gk, 1)

    def test_prio_bounce_is_never_marked_released(self):
        # A bounced ticket (gk returned it for rework) is the stream's own court
        # even if a stale merge exists — it must stay in I, never "released".
        _rows, handed, _workable, unhandled, _gk = _drive(
            6800, labels=["prio:bounce"], merged_oid="abc1234")
        self.assertIn(6800, unhandled, "a bounce stays workable (rework)")
        self.assertFalse(handed.get(6800))


class ListTagsReleasedRows(unittest.TestCase):
    """`slice-quals --list` surfaces a merged+released row tagged `released`
    (out of the workable count, visible so a misroute shows) while `--count`
    excludes it."""

    def _drive_list(self, count=False):
        import cli_quals_cmd
        rows = {
            700: {"number": 700, "title": "workable one",
                  "createdAt": "2026-09-01T00:00:00Z", "labels": []},
            701: {"number": 701, "title": "released one",
                  "createdAt": "2026-09-02T00:00:00Z", "labels": []},
        }
        handed = {701: "released"}   # 700 workable, 701 released
        args = dict(count=count, list=not count, waiting=False, ops_wait=False,
                    audit=False, dep_wait=False, count_dispatchable=False,
                    bounces=False, extra=None, role=None)
        buf = __import__("io").StringIO()
        import contextlib
        with m.patch.object(airuleset, "resolve_authority", return_value="branch-merge"), \
             m.patch.object(airuleset, "_current_user", return_value=STREAM), \
             m.patch.object(airuleset, "_slice_quals", return_value=["label:stream:montalu"]), \
             m.patch.object(airuleset, "_repo_slug", return_value=SLUG), \
             m.patch.object(airuleset, "_repo_root", return_value=ROOT), \
             m.patch.object(airuleset, "_slice_mine_and_handed",
                            return_value=(rows, handed, False)), \
             m.patch.object(cli_quals_cmd, "_dep_wait_map_for",
                            return_value=({}, SLUG, True)):
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_slice_quals(m.Mock(**args))
        return buf.getvalue()

    def test_list_shows_released_row_with_released_action(self):
        out = self._drive_list()
        rel = [ln for ln in out.splitlines() if ln.split("\t", 1)[0] == "701"]
        self.assertEqual(len(rel), 1)
        # number<TAB>createdAt<TAB>action<TAB>title
        self.assertEqual(rel[0].split("\t")[2], "released")

    def test_list_still_shows_workable_row(self):
        out = self._drive_list()
        work = [ln for ln in out.splitlines() if ln.split("\t", 1)[0] == "700"]
        self.assertEqual(len(work), 1)
        self.assertNotEqual(work[0].split("\t")[2], "released")

    def test_count_excludes_the_released_row(self):
        out = self._drive_list(count=True)
        self.assertEqual(out.strip(), "1")   # only 700, released 701 excluded


if __name__ == "__main__":
    unittest.main()
