"""#998 item 4 — the footer/quals apply the pane's resolved ROLE + status row.

`airuleset._role_filter_footer` slices the workable (`I`) partition by the
pane's resolved role so the two gk windows show DIFFERENT `I` (review = core
minus infra/architecture-rework, infra = only those). #1045 CORRECTION of #1025:
it filters `I` AND the third-party `W` (ops_wait) — only the owner-court `U`
(waiting) is a parked state global to the box and NEVER role-filtered (an infra
ticket's needs-answer must stay in the review window's U — airuleset #1025).
#1025 also stopped filtering W, so the FLOW window's footer `W` showed infra
members (airuleset #1045). Fail-safe: role None = unchanged; an unresolvable
slug degrades to unfiltered, never a crash.
"""
import sys
import unittest.mock as m
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import airuleset  # noqa: E402
import cli_concurrency  # noqa: E402

ODOO = "zbynekdrlik/odoo-erp"


def _rows(*specs):
    return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                "labels": [{"name": n} for n in labels]}
            for k, labels in specs}


class TestRoleFilterFooter(TestCase):
    def setUp(self):
        self.workable = _rows(("a", []), ("b", ["infra"]),
                              ("c", ["architecture-rework"]))
        self.waiting = _rows(("d", ["infra"]), ("e", []))
        # #1045: two W members — infra "f" and independent "g" — so the role
        # filter on W is meaningful in BOTH directions (not coincidental).
        self.ops = _rows(("f", ["infra"]), ("g", []))

    def test_role_none_returns_unchanged(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value=None):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(w, self.workable)
        self.assertEqual(wa, self.waiting)
        self.assertEqual(o, self.ops)

    def test_review_role_drops_infra_and_arch_from_I_and_W(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})               # I filtered
        self.assertEqual(set(wa), {"d", "e"})         # #1025: U unfiltered
        self.assertEqual(set(o), {"g"})               # #1045: W filtered — infra "f" dropped

    def test_infra_role_keeps_only_infra_class_in_I(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(set(w), {"b", "c"})          # I filtered
        self.assertEqual(set(wa), {"d", "e"})         # #1025: U unfiltered (same as review)
        self.assertEqual(set(o), {"f"})               # #1045: W filtered — only infra "f" kept

    def test_unresolvable_slug_degrades_to_unfiltered(self):
        # _apply_role_filter sys.exit(1) on empty slug — footer must NOT crash.
        with m.patch.object(cli_concurrency, "resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=""):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(w, self.workable)  # unfiltered, no SystemExit escaped
        self.assertEqual(wa, self.waiting)

    def test_resolver_error_degrades_to_unfiltered(self):
        with m.patch.object(cli_concurrency, "resolve_role",
                            side_effect=RuntimeError("boom")):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(w, self.workable)


class TestStatusRow(TestCase):
    def test_concurrency_status_row_names_source_and_role(self):
        with m.patch.object(cli_concurrency, "resolve_concurrency",
                            return_value=("sequential", "infra", "role")):
            row = cli_concurrency.concurrency_status_row("/x")
        self.assertIn("concurrency: sequential (source: role)", row)
        self.assertIn("role=infra", row)


if __name__ == "__main__":
    main()
