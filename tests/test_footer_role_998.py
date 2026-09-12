"""#998 item 4 — the footer/quals apply the pane's resolved ROLE + status row.

`airuleset._role_filter_footer` slices the workable/waiting/ops_wait partition
by the pane's resolved role so the two gk windows show DIFFERENT `I` (review =
core minus infra/architecture-rework, infra = only those). Fail-safe: role None
= unchanged; an unresolvable slug degrades to unfiltered, never a crash.
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
        self.ops = _rows(("f", ["infra"]))

    def test_role_none_returns_unchanged(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value=None):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(w, self.workable)
        self.assertEqual(wa, self.waiting)
        self.assertEqual(o, self.ops)

    def test_review_role_drops_infra_and_arch(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value="review"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(set(w), {"a"})
        self.assertEqual(set(wa), {"e"})
        self.assertEqual(set(o), set())

    def test_infra_role_keeps_only_infra_class(self):
        with m.patch.object(cli_concurrency, "resolve_role", return_value="infra"), \
             m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            w, wa, o = airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")
        self.assertEqual(set(w), {"b", "c"})
        self.assertEqual(set(wa), {"d"})
        self.assertEqual(set(o), {"f"})

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
