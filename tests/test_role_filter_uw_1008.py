"""#1008 — the CLI `--waiting` (U) and `--ops-wait` (W) outputs must obey the
window's `--role` partition, exactly like `--count`/`I` already does and like
the footer already does (`_role_filter_footer`, #998).

Before #1008: `cmd_core_quals`/`cmd_slice_quals` applied `_apply_role_filter`
to the WORKABLE set only; `--waiting`/`--ops-wait` printed the UNDIVIDED U/W
set, so a gk review window's `core-quals --ops-wait` showed infra-labelled
members that belong to the infra window (the ticket's `W 8 of which 7 are
infra`). The fix applies the ONE existing filter to waiting/ops_wait too.

The footer already filters all three (locked here so a regression that reverts
`_role_filter_footer` to filtering only `I` is caught in the SAME suite).
"""
import contextlib
import io
import sys
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_quals_cmd  # noqa: E402

ODOO = "zbynekdrlik/odoo-erp"

# rows: number -> label-name list. infra rows carry the `infra` label; review
# rows carry none of {infra, architecture-rework} so work_class == independent.
CORE_ROWS = {
    100: ["infra", "ops-wait"],        # infra   W
    101: ["ops-wait"],                 # review  W
    102: ["infra", "needs-answer"],    # infra   U
    103: ["needs-decision"],           # review  U
    104: ["infra"],                    # infra   I
    105: [],                           # review  I
}


def _core_gh(rows):
    """A `_gh_out` stand-in: any `--search` carrying the core partition qual
    (`-label:stream:`) returns every row (deduped by _union_open_issues);
    every other search returns []. Non-search calls return []."""
    import json as _json

    def gh(*a, **k):
        args = [str(x) for x in a]
        if "--search" not in args:
            return "[]"
        search = args[args.index("--search") + 1]
        if "-label:stream:" not in search:
            return "[]"
        return _json.dumps(
            [{"number": n, "title": "t%d" % n,
              "createdAt": "2026-09-%02dT00:00:00Z" % ((n % 27) + 1),
              "labels": [{"name": x} for x in labels]}
             for n, labels in rows.items()])

    return gh


def _numbers(out):
    nums = set()
    for line in out.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.isdigit():
            nums.add(int(head))
    return nums


def _drive_core(rows=None, **flags):
    rows = CORE_ROWS if rows is None else rows
    args = dict(count=False, list=False, waiting=False, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                extra=None, role=None)
    args.update(flags)
    buf = io.StringIO()
    empty9 = (set(), set(), set(), set(), set(), set(), set(), set(), set())
    with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
         m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
         m.patch.object(airuleset, "_repo_root", return_value="/root"), \
         m.patch.object(airuleset, "_gh_out", side_effect=_core_gh(rows)), \
         m.patch.object(cli_quals_cmd, "_ops_wait_flag_sets", return_value=empty9), \
         m.patch.object(cli_quals_cmd, "_ops_wait_summary_line", return_value=""), \
         m.patch.object(cli_quals_cmd, "_queued_acceptance_numbers", return_value=set()), \
         m.patch.object(cli_quals_cmd, "_waiting_ping_entries", return_value=[]), \
         m.patch.object(airuleset, "_no_question_flagged", return_value=set()):
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_core_quals(m.Mock(**args))
    return _numbers(buf.getvalue())


class TestCoreQualsOpsWaitObeysRole(TestCase):
    def test_ops_wait_review_role_drops_infra_members(self):
        self.assertEqual(_drive_core(ops_wait=True, role="review"), {101})

    def test_ops_wait_infra_role_keeps_only_infra_members(self):
        self.assertEqual(_drive_core(ops_wait=True, role="infra"), {100})

    def test_ops_wait_no_role_keeps_the_whole_W_set(self):
        # role None = byte-identical to today (no partition).
        self.assertEqual(_drive_core(ops_wait=True, role=None), {100, 101})


class TestCoreQualsWaitingObeysRole(TestCase):
    def test_waiting_review_role_drops_infra_members(self):
        self.assertEqual(_drive_core(waiting=True, role="review"), {103})

    def test_waiting_infra_role_keeps_only_infra_members(self):
        self.assertEqual(_drive_core(waiting=True, role="infra"), {102})


class TestCoreQualsCountStillRoleFiltered(TestCase):
    """The `I`/workable role filter already worked pre-#1008 — anchor it so
    the U/W fix does not disturb it."""

    def test_count_review_role(self):
        buf = io.StringIO()
        args = dict(count=True, list=False, waiting=False, ops_wait=False,
                    audit=False, dep_wait=False, count_dispatchable=False,
                    extra=None, role="review")
        with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
             m.patch.object(airuleset, "_repo_root", return_value="/root"), \
             m.patch.object(airuleset, "_gh_out", side_effect=_core_gh(CORE_ROWS)):
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_core_quals(m.Mock(**args))
        self.assertEqual(buf.getvalue().strip(), "1")   # only 105 (review I)

    def test_list_review_role_shows_only_review_workable(self):
        self.assertEqual(_drive_core(list=True, role="review"), {105})

    def test_list_infra_role_shows_only_infra_workable(self):
        self.assertEqual(_drive_core(list=True, role="infra"), {104})


# --------------------------------------------------------------------------- #
# slice-quals path — the reduced-authority CLI must role-filter U/W too.
# --------------------------------------------------------------------------- #
SLICE_ROWS = {
    200: {"number": 200, "title": "t200", "createdAt": "2026-09-01T00:00:00Z",
          "labels": [{"name": "infra"}, {"name": "ops-wait"}]},
    201: {"number": 201, "title": "t201", "createdAt": "2026-09-02T00:00:00Z",
          "labels": [{"name": "ops-wait"}]},
    202: {"number": 202, "title": "t202", "createdAt": "2026-09-03T00:00:00Z",
          "labels": [{"name": "infra"}, {"name": "needs-answer"}]},
    203: {"number": 203, "title": "t203", "createdAt": "2026-09-04T00:00:00Z",
          "labels": [{"name": "needs-decision"}]},
}


def _drive_slice(**flags):
    args = dict(count=False, list=False, waiting=False, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                bounces=False, extra=None, role=None)
    args.update(flags)
    buf = io.StringIO()
    empty9 = (set(), set(), set(), set(), set(), set(), set(), set(), set())
    with m.patch.object(airuleset, "resolve_authority", return_value="branch-merge"), \
         m.patch.object(airuleset, "_current_user", return_value="montalu"), \
         m.patch.object(airuleset, "_slice_quals", return_value=["label:stream:montalu"]), \
         m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
         m.patch.object(airuleset, "_repo_root", return_value="/root"), \
         m.patch.object(airuleset, "_slice_mine_and_handed",
                        return_value=(SLICE_ROWS, {}, False)), \
         m.patch.object(cli_quals_cmd, "_ops_wait_flag_sets", return_value=empty9), \
         m.patch.object(cli_quals_cmd, "_ops_wait_summary_line", return_value=""), \
         m.patch.object(cli_quals_cmd, "_queued_acceptance_numbers", return_value=set()), \
         m.patch.object(cli_quals_cmd, "_waiting_ping_entries", return_value=[]), \
         m.patch.object(airuleset, "_no_question_flagged", return_value=set()), \
         m.patch.object(airuleset, "_question_map_u_supplement", return_value={}):
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_slice_quals(m.Mock(**args))
    return _numbers(buf.getvalue())


class TestSliceQualsUWObeyRole(TestCase):
    def test_slice_ops_wait_review_role(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="review"), {201})

    def test_slice_ops_wait_infra_role(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="infra"), {200})

    def test_slice_waiting_review_role(self):
        self.assertEqual(_drive_slice(waiting=True, role="review"), {203})

    def test_slice_waiting_infra_role(self):
        self.assertEqual(_drive_slice(waiting=True, role="infra"), {202})


class TestFooterStillFiltersAllThree(TestCase):
    """Lock #998: the footer path filters workable AND waiting AND ops_wait by
    role (a regression that reverts `_role_filter_footer` to only `I` fails)."""

    def _rows(self, *specs):
        return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                    "labels": [{"name": n} for n in labels]}
                for k, labels in specs}

    def test_review_role_filters_u_and_w_not_just_i(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})
        self.assertEqual(set(wa), {"d"})   # U filtered, not just I
        self.assertEqual(set(o), {"f"})    # W filtered, not just I


if __name__ == "__main__":
    main()
