"""#1045 — the `--role` filter narrows `W` (ops-wait) exactly like `I`.

Regression of #1008: #1025 (`9be613e2`) removed `_apply_role_filter` from BOTH
`waiting` (U) AND `ops_wait` (W) at all three call sites (`cmd_slice_quals`,
`cmd_core_quals`, `_role_filter_footer`). Only `U` should have been exempted
(the odoo-erp issue 6883 owner-question case). Leaving `W` role-independent
reintroduced the exact #1008 over-count for W: on the gk box
`core-quals --role review --ops-wait` and `--role infra --ops-wait` returned an
IDENTICAL set (owner 2026-09-16: `W 15`, 11 of them `infra`, in the FLOW window).

Owner ruling (issue #1045 body, 2026-09-16 06:3x): "Tu sme v gk flow a chcem
vidieť čísla týkajúce sa gk flow, nie mix kadečoho." → the `--role` partition
applies to `W` exactly as to `I` (a `--role review` W = only the FLOW window's
ops-wait members: stream-dependent tickets + gk-owned tickets WITHOUT `infra`;
`--role infra` W = the infra ones).

#1065 UPDATE (20.9.2026): the #1025 `U` exemption is REVERSED — `U` now narrows
by role EXACTLY like `I` and `W` (an owner question shows in the ONE window
whose role owns the ticket, never both; odoo-erp#7720). So the W-filter locks
here are unchanged, and the former `U stays role-independent` assertions were
flipped to the #1065 `U role-filtered` ruling.

This locks: W role-filtered (CLI core + slice, the `# W-summary: total=` line,
and the statusline `_role_filter_footer`), and U role-filtered too (#1065).
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

# number -> label-name list. infra rows carry the `infra` label; review-class
# rows carry none of {infra, architecture-rework} so work_class == independent.
CORE_ROWS = {
    300: ["infra", "ops-wait"],          # infra   W
    301: ["stream:montalu", "ops-wait"],  # review  W (stream-dependent, non-infra)
    302: ["infra", "needs-answer"],      # infra   U (the #1025 / odoo-erp 6883 lock)
}


def _core_gh(rows):
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


def _wsummary_total(out):
    """The `total=N` int from the `# W-summary:` aggregate line, or None."""
    for line in out.splitlines():
        if line.startswith("# W-summary:") and "total=" in line:
            frag = line.split("total=", 1)[1].split()[0]
            return int(frag)
    return None


def _drive_core(rows=None, mock_summary=True, **flags):
    """Drive cmd_core_quals. mock_summary=False keeps the REAL
    `_ops_wait_summary_line` so a test can assert `# W-summary: total=`
    reflects the (role-filtered) W set — the single-derivation lock."""
    rows = CORE_ROWS if rows is None else rows
    args = dict(count=False, list=False, waiting=False, ops_wait=False,
                audit=False, dep_wait=False, count_dispatchable=False,
                extra=None, role=None)
    args.update(flags)
    buf = io.StringIO()
    empty9 = (set(), set(), set(), set(), set(), set(), set(), set(), set())
    ctx = [
        m.patch.object(airuleset, "resolve_authority", return_value="full"),
        m.patch.object(airuleset, "_repo_slug", return_value=ODOO),
        m.patch.object(airuleset, "_repo_root", return_value="/root"),
        m.patch.object(airuleset, "_gh_out", side_effect=_core_gh(rows)),
        m.patch.object(cli_quals_cmd, "_ops_wait_flag_sets", return_value=empty9),
        m.patch.object(cli_quals_cmd, "_queued_acceptance_numbers", return_value=set()),
        m.patch.object(cli_quals_cmd, "_waiting_ping_entries", return_value=[]),
        m.patch.object(airuleset, "_no_question_flagged", return_value=set()),
    ]
    if mock_summary:
        ctx.append(m.patch.object(cli_quals_cmd, "_ops_wait_summary_line",
                                  return_value=""))
    with contextlib.ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        with contextlib.redirect_stdout(buf):
            airuleset.cmd_core_quals(m.Mock(**args))
    return buf.getvalue()


class TestCoreOpsWaitRoleFiltered(TestCase):
    """#1045: W (ops-wait) is role-filtered EXACTLY like I."""

    def test_review_role_keeps_only_non_infra_W(self):
        self.assertEqual(_numbers(_drive_core(ops_wait=True, role="review")),
                         {301})

    def test_infra_role_keeps_only_infra_W(self):
        self.assertEqual(_numbers(_drive_core(ops_wait=True, role="infra")),
                         {300})

    def test_no_role_keeps_the_whole_W_set(self):
        self.assertEqual(_numbers(_drive_core(ops_wait=True, role=None)),
                         {300, 301})

    def test_review_and_infra_W_are_disjoint_not_identical(self):
        """The exact regression symptom: review W == infra W was True."""
        rev = _numbers(_drive_core(ops_wait=True, role="review"))
        inf = _numbers(_drive_core(ops_wait=True, role="infra"))
        self.assertNotEqual(rev, inf)
        self.assertEqual(rev & inf, set())


class TestCoreWSummaryReflectsRole(TestCase):
    """The `# W-summary: total=` line reads the SAME filtered W (one
    derivation, #367) — never the undivided set."""

    def test_summary_total_review(self):
        self.assertEqual(
            _wsummary_total(_drive_core(ops_wait=True, role="review",
                                        mock_summary=False)), 1)

    def test_summary_total_infra(self):
        self.assertEqual(
            _wsummary_total(_drive_core(ops_wait=True, role="infra",
                                        mock_summary=False)), 1)

    def test_summary_total_no_role(self):
        self.assertEqual(
            _wsummary_total(_drive_core(ops_wait=True, role=None,
                                        mock_summary=False)), 2)


class TestCoreWaitingRoleFiltered(TestCase):
    """#1065: U (owner court) is role-filtered EXACTLY like I/W — an infra
    ticket's needs-answer shows ONLY in the INFRA window, never the FLOW one
    (REVERSES the #1025 'U stays independent' lock)."""

    def test_infra_needs_answer_dropped_from_review_U(self):
        # #1065: infra 302 leaves the review (FLOW) window's U entirely.
        self.assertEqual(_numbers(_drive_core(waiting=True, role="review")),
                         set())

    def test_infra_needs_answer_in_infra_U(self):
        self.assertEqual(_numbers(_drive_core(waiting=True, role="infra")),
                         {302})


# --------------------------------------------------------------------------- #
# slice-quals path (reduced-authority CLI) — W role-filtered, U independent.
# --------------------------------------------------------------------------- #
SLICE_ROWS = {
    400: {"number": 400, "title": "t400", "createdAt": "2026-09-01T00:00:00Z",
          "labels": [{"name": "infra"}, {"name": "ops-wait"}]},
    401: {"number": 401, "title": "t401", "createdAt": "2026-09-02T00:00:00Z",
          "labels": [{"name": "stream:montalu"}, {"name": "ops-wait"}]},
    402: {"number": 402, "title": "t402", "createdAt": "2026-09-03T00:00:00Z",
          "labels": [{"name": "infra"}, {"name": "needs-answer"}]},
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


class TestSliceOpsWaitRoleFiltered(TestCase):
    def test_slice_review_role_keeps_only_non_infra_W(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="review"), {401})

    def test_slice_infra_role_keeps_only_infra_W(self):
        self.assertEqual(_drive_slice(ops_wait=True, role="infra"), {400})

    def test_slice_waiting_role_filtered(self):
        # #1065: U role-filtered — infra needs-answer 402 shows ONLY in INFRA.
        self.assertEqual(_drive_slice(waiting=True, role="review"), set())
        self.assertEqual(_drive_slice(waiting=True, role="infra"), {402})


# --------------------------------------------------------------------------- #
# statusline W renderer — `_role_filter_footer` filters I, W AND U (#1065).
# --------------------------------------------------------------------------- #
class TestFooterWRoleFiltered(TestCase):
    def _rows(self, *specs):
        return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                    "labels": [{"name": n} for n in labels]}
                for k, labels in specs}

    def test_review_role_filters_i_w_and_u(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})          # I filtered
        self.assertEqual(set(wa), {"d"})         # U filtered — infra "c" dropped (#1065)
        self.assertEqual(set(o), {"f"})          # W filtered — infra dropped

    def test_infra_role_filters_i_w_and_u(self):
        workable = self._rows(("a", []), ("b", ["infra"]))
        waiting = self._rows(("c", ["infra"]), ("d", []))
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                workable, waiting, ops, "/root", "/cwd")
        self.assertEqual(set(w), {"b"})          # I filtered
        self.assertEqual(set(wa), {"c"})         # U filtered — only infra "c" kept (#1065)
        self.assertEqual(set(o), {"e"})          # W filtered — only infra kept

    def test_footer_review_and_infra_W_disjoint(self):
        ops = self._rows(("e", ["infra"]), ("f", []))
        with m.patch("cli_concurrency.resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            _, _, o_rev = airuleset._role_filter_footer(
                {}, {}, dict(ops), "/root", "/cwd")
        with m.patch("cli_concurrency.resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            _, _, o_inf = airuleset._role_filter_footer(
                {}, {}, dict(ops), "/root", "/cwd")
        self.assertEqual(set(o_rev) & set(o_inf), set())

    def test_role_none_returns_unchanged(self):
        ops = self._rows(("e", ["infra"]))
        with m.patch("cli_concurrency.resolve_role", return_value=None):
            w, wa, o = airuleset._role_filter_footer(
                {}, {}, ops, "/root", "/cwd")
        self.assertEqual((w, wa, o), ({}, {}, ops))


if __name__ == "__main__":
    main()
