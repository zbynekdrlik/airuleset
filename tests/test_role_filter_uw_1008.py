"""#1045 CORRECTION of #1025 — the role filter narrows `I` (workable) AND `W`
(ops-wait); only owner-court `U` stays role-independent.

Owner ruling (issue #1045 body, 2026-09-16 06:3x): "Tu sme v gk flow a chcem
vidieť čísla týkajúce sa gk flow, nie mix kadečoho." — the `--role` partition
applies to `W` exactly as to `I`: a `--role review` W = only the FLOW window's
ops-wait members (stream-dependent + gk-owned WITHOUT `infra`), a `--role infra`
W = the infra ones. Only `U` (needs-answer/needs-decision/needs-owner-action)
stays role-INDEPENDENT — the #1025 exception (an owner question on ANY ticket,
infra included, is never dropped: odoo-erp #6883, footer `U 0` with a live
`❓ ASKED`).

Background: #1008 (CLI) and #998 (footer) role-filtered the WHOLE partition
(I/U/W). #1025 (`9be613e2`) correctly exempted `U` — but ALSO removed the filter
from `W`, which reintroduced the exact #1008 over-count for W on the gk box
(`core-quals --role review --ops-wait` == `--role infra --ops-wait`). #1045
restores the `W` filter while KEEPING the #1025 `U` exemption.

The W-filter classes below were REWRITTEN from their #1025 'W keeps infra'
assertions to the #1045 'W role-filtered' ruling; the U classes are unchanged
(the #1025 lock).
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
    100: ["infra", "ops-wait"],          # infra   W
    101: ["ops-wait"],                   # review  W
    102: ["infra", "needs-answer"],      # infra   U (the #6883 shape)
    103: ["needs-decision"],             # review  U
    104: ["infra"],                      # infra   I
    105: [],                             # review  I
    106: ["infra", "needs-owner-action"],  # infra U (owner action, #601)
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


def _reasons(out):
    """{number: reason} from a `--waiting` listing
    (`number<TAB>createdAt<TAB>action<TAB>reason<TAB>title`)."""
    d = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if parts and parts[0].strip().isdigit() and len(parts) >= 4:
            d[int(parts[0].strip())] = parts[3].strip()
    return d


def _drive_core(rows=None, capture_reasons=False, **flags):
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
    return _reasons(buf.getvalue()) if capture_reasons else _numbers(buf.getvalue())


class TestCoreQualsOpsWaitRoleFiltered(TestCase):
    """#1045: W (ops-wait) is role-filtered EXACTLY like I (owner ruling
    2026-09-16 — the FLOW window must not show infra members in W)."""

    def test_ops_wait_review_role_drops_infra_members(self):
        # 100 = infra W (dropped under review), 101 = independent W (kept).
        self.assertEqual(_drive_core(ops_wait=True, role="review"), {101})

    def test_ops_wait_infra_role_keeps_only_infra_members(self):
        self.assertEqual(_drive_core(ops_wait=True, role="infra"), {100})

    def test_ops_wait_no_role_keeps_the_whole_W_set(self):
        self.assertEqual(_drive_core(ops_wait=True, role=None), {100, 101})


class TestCoreQualsWaitingKeepsInfra(TestCase):
    """U (owner court) is global — NOT role-filtered; the #1025 core fix."""

    def test_waiting_review_role_keeps_infra_needs_answer(self):
        # The exact bug: the infra needs-answer/owner-action tickets MUST show
        # in the review (FLOW) window's U, alongside the review needs-decision.
        self.assertEqual(_drive_core(waiting=True, role="review"), {102, 103, 106})

    def test_waiting_infra_role_shows_all_owner_court(self):
        # infra questions still visible (#1025 c "infra questions visible there
        # too"); U is not partitioned, so it is the same full set for both roles.
        self.assertEqual(_drive_core(waiting=True, role="infra"), {102, 103, 106})

    def test_waiting_review_role_tags_the_infra_members_correctly(self):
        reasons = _drive_core(waiting=True, role="review", capture_reasons=True)
        self.assertEqual(reasons.get(102), "answer")     # infra + needs-answer
        self.assertEqual(reasons.get(103), "decision")   # needs-decision
        self.assertEqual(reasons.get(106), "action")     # infra + needs-owner-action


class TestCoreQualsCountStillRoleFiltered(TestCase):
    """`I`/workable role filter is UNCHANGED — only I is narrowed by role."""

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

    def test_count_infra_role(self):
        buf = io.StringIO()
        args = dict(count=True, list=False, waiting=False, ops_wait=False,
                    audit=False, dep_wait=False, count_dispatchable=False,
                    extra=None, role="infra")
        with m.patch.object(airuleset, "resolve_authority", return_value="full"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO), \
             m.patch.object(airuleset, "_repo_root", return_value="/root"), \
             m.patch.object(airuleset, "_gh_out", side_effect=_core_gh(CORE_ROWS)):
            with contextlib.redirect_stdout(buf):
                airuleset.cmd_core_quals(m.Mock(**args))
        self.assertEqual(buf.getvalue().strip(), "1")   # only 104 (infra I)

    def test_list_review_role_shows_only_review_workable(self):
        self.assertEqual(_drive_core(list=True, role="review"), {105})

    def test_list_infra_role_shows_only_infra_workable(self):
        self.assertEqual(_drive_core(list=True, role="infra"), {104})


# --------------------------------------------------------------------------- #
# slice-quals path — the reduced-authority CLI must ALSO keep U/W unfiltered.
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


class TestSliceQualsUKeptWRoleFiltered(TestCase):
    def test_slice_ops_wait_review_role(self):
        # #1045: W role-filtered — 200 = infra W (dropped), 201 = independent W.
        self.assertEqual(_drive_slice(ops_wait=True, role="review"), {201})

    def test_slice_ops_wait_infra_role(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="infra"), {200})

    def test_slice_waiting_review_role(self):
        self.assertEqual(_drive_slice(waiting=True, role="review"), {202, 203})

    def test_slice_waiting_infra_role(self):
        self.assertEqual(_drive_slice(waiting=True, role="infra"), {202, 203})


class TestFooterUUnfilteredWRoleFiltered(TestCase):
    """#1045: `_role_filter_footer` filters workable (I) AND ops-wait (W); only
    owner-court U stays role-independent. A regression that re-removes the W
    filter (the #1025 over-reach) fails here. U keeps the no-double-count
    property (both roles yield the same U); W is role-partitioned like I."""

    def _rows(self, *specs):
        return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                    "labels": [{"name": n} for n in labels]}
                for k, labels in specs}

    def test_review_role_filters_only_i(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})          # I filtered
        self.assertEqual(set(wa), {"c", "d"})    # U unfiltered (#1025 lock)
        self.assertEqual(set(o), {"f"})          # W filtered — infra dropped (#1045)

    def test_infra_role_filters_only_i(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"b"})          # I filtered
        self.assertEqual(set(wa), {"c", "d"})    # U unfiltered — same as review (#1025)
        self.assertEqual(set(o), {"e"})          # W filtered — only infra kept (#1045)

    def test_role_none_returns_unchanged(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]))
        ops = self._rows(("e", ["infra"]))
        with m.patch("cli_concurrency.resolve_role", return_value=None):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual((w, wa, o), (workable, waiting, ops))


if __name__ == "__main__":
    main()
