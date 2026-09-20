"""#1065 REVERSES #1025 — the role filter narrows ALL THREE buckets: `I`
(workable), `W` (ops-wait) AND `U` (owner-court waiting).

Owner ruling (issue #1065 body, 20.9.2026, verbatim): "dlho hlaseny bug. U 1 v
gk nie je gk ale gk infra stale ma to pletie ze obydva hlasia pocet otazok aj
ked sa tykaju druheho!!" — an owner question shows in the ONE window whose role
owns the ticket (`infra` → INFRA window), never both. This REVERSES the #1025
choice ("an owner question on ANY ticket, infra included, is never dropped").

Background: #1008 (CLI) and #998 (footer) role-filtered the WHOLE partition
(I/U/W). #1025 (`9be613e2`) exempted `U` (odoo-erp#6883) — but ALSO removed the
filter from `W`, which reintroduced the #1008 over-count for W on the gk box.
#1045 restored the `W` filter while KEEPING the #1025 `U` exemption. On the gk
box that exemption duplicated every owner question across BOTH windows: the
owner asked "U 1?" in the INFRA window and the FLOW session re-presented the
infra question (odoo-erp#7421 17.9., live on #7720). #1065 restores the `U`
filter too — the exactly-one-window invariant (U(FLOW)+U(INFRA)==U(unfiltered))
keeps "never lose a question".

The U classes below were REWRITTEN from their #1025 'U keeps infra' assertions
to the #1065 'U role-filtered' ruling; the W/I classes are unchanged (#1045).
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


class TestCoreQualsWaitingRoleFiltered(TestCase):
    """#1065: U (owner court) is role-filtered EXACTLY like I/W — an owner
    question shows in the ONE window whose role owns the ticket, never both
    (REVERSES the #1025 'U kept infra' lock)."""

    def test_waiting_review_role_drops_infra_owner_court(self):
        # #1065: the infra needs-answer/owner-action tickets (102, 106) leave the
        # review (FLOW) window's U; only the review needs-decision (103) remains.
        self.assertEqual(_drive_core(waiting=True, role="review"), {103})

    def test_waiting_infra_role_shows_only_infra_owner_court(self):
        # #1065: U is partitioned, so the INFRA window shows only its infra
        # questions (102, 106), never the review one (103).
        self.assertEqual(_drive_core(waiting=True, role="infra"), {102, 106})

    def test_waiting_invariant_flow_plus_infra_equals_unfiltered(self):
        # U(FLOW) + U(INFRA) == U(unfiltered): every question in exactly one window.
        self.assertEqual(
            _drive_core(waiting=True, role="review")
            | _drive_core(waiting=True, role="infra"),
            {102, 103, 106})

    def test_waiting_infra_role_tags_the_infra_members_correctly(self):
        # #1065: the reason tags still apply after the U role filter — in the
        # INFRA window the infra members are tagged answer/action; 103 is absent.
        reasons = _drive_core(waiting=True, role="infra", capture_reasons=True)
        self.assertEqual(reasons.get(102), "answer")     # infra + needs-answer
        self.assertEqual(reasons.get(106), "action")     # infra + needs-owner-action
        self.assertNotIn(103, reasons)                   # review U dropped from INFRA


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


class TestSliceQualsUWRoleFiltered(TestCase):
    def test_slice_ops_wait_review_role(self):
        # #1045: W role-filtered — 200 = infra W (dropped), 201 = independent W.
        self.assertEqual(_drive_slice(ops_wait=True, role="review"), {201})

    def test_slice_ops_wait_infra_role(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="infra"), {200})

    def test_slice_waiting_review_role(self):
        # #1065: U role-filtered — infra 202 dropped, only review 203 in FLOW.
        self.assertEqual(_drive_slice(waiting=True, role="review"), {203})

    def test_slice_waiting_infra_role(self):
        # #1065: only the infra owner-court member 202 in the INFRA window.
        self.assertEqual(_drive_slice(waiting=True, role="infra"), {202})


class TestFooterUWIRoleFiltered(TestCase):
    """#1065: `_role_filter_footer` filters ALL THREE — workable (I), ops-wait
    (W) AND owner-court U. A regression that re-exempts U (the #1025/#1045 lock)
    fails here. Every bucket is role-partitioned; U(FLOW)+U(INFRA) == U(all)."""

    def _rows(self, *specs):
        return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                    "labels": [{"name": n} for n in labels]}
                for k, labels in specs}

    def test_review_role_filters_i_u_and_w(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})          # I filtered
        self.assertEqual(set(wa), {"d"})         # U filtered — infra "c" dropped (#1065)
        self.assertEqual(set(o), {"f"})          # W filtered — infra dropped (#1045)

    def test_infra_role_filters_i_u_and_w(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"b"})          # I filtered
        self.assertEqual(set(wa), {"c"})         # U filtered — only infra "c" kept (#1065)
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
