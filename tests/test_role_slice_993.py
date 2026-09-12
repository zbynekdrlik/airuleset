"""#993 r2b — `--role review|infra`, the ONE new CLI surface of the round.

`cli_quals_cmd._apply_role_filter` slices the workable/obligation rows by
`work_class`: `review` = rows whose class is NOT infra; `infra` = rows whose
class IS infra; `None` (no flag) = identity (byte-identical to today). It is how
the `infra` label ROUTES a ticket to the infra role. Fail-CLOSED when the repo
slug is unresolvable (a gh failure) rather than silently mis-slicing.
"""

import argparse
import sys
import unittest.mock as mk
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_quals_cmd  # noqa: E402
import airuleset  # noqa: E402


def _rows(*specs):
    # spec = (key, [label,...])  → row dict shaped like _partition_workable output
    return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": k,
                "labels": [{"name": n} for n in labels]}
            for k, labels in specs}


class TestApplyRoleFilter(TestCase):
    ODOO = "zbynekdrlik/odoo-erp"
    AR = "zbynekdrlik/airuleset"

    def _filter(self, rows, role, slug):
        with mk.patch("airuleset._repo_slug", return_value=slug):
            return cli_quals_cmd._apply_role_filter(rows, "/root", role)

    def test_review_keeps_non_infra_rows(self):
        rows = _rows(("1", ["bug"]), ("2", ["infra"]), ("3", ["architecture-rework"]))
        out = self._filter(rows, "review", self.ODOO)
        self.assertEqual(set(out), {"1"})

    def test_infra_keeps_infra_rows(self):
        rows = _rows(("1", ["bug"]), ("2", ["infra"]), ("3", ["architecture-rework"]))
        out = self._filter(rows, "infra", self.ODOO)
        self.assertEqual(set(out), {"2", "3"})

    def test_airuleset_repo_is_all_infra(self):
        rows = _rows(("1", ["bug"]), ("2", []))
        self.assertEqual(set(self._filter(rows, "infra", self.AR)), {"1", "2"})
        self.assertEqual(set(self._filter(rows, "review", self.AR)), set())

    def test_missing_and_malformed_labels_are_infra(self):
        rows = {"1": {"labels": None}, "2": {"labels": "garbage"}, "3": "not-a-dict"}
        out = self._filter(rows, "infra", self.ODOO)
        self.assertEqual(set(out), {"1", "2", "3"})   # fail-safe serial

    def test_none_role_is_identity_and_never_reads_slug(self):
        rows = _rows(("1", ["bug"]), ("2", ["infra"]))
        with mk.patch("airuleset._repo_slug",
                      side_effect=AssertionError("slug must not be read")) as m:
            out = cli_quals_cmd._apply_role_filter(rows, "/root", None)
        self.assertEqual(out, rows)      # byte-identical
        m.assert_not_called()

    def test_empty_slug_fails_closed(self):
        # #993 r2b review 🔴: an unresolvable slug (gh failure) must REFUSE, not
        # silently mis-slice (which would leak infra into --role review / print 0
        # for --role infra --count on airuleset).
        rows = _rows(("1", ["bug"]))
        with self.assertRaises(SystemExit) as cm:
            self._filter(rows, "infra", "")
        self.assertEqual(cm.exception.code, 1)


class TestRoleArgparse(TestCase):
    def _parser(self):
        p = argparse.ArgumentParser()
        airuleset._add_dispatch_flags(p)
        return p

    def test_role_accepted(self):
        for val in ("review", "infra"):
            self.assertEqual(self._parser().parse_args(["--role", val]).role, val)

    def test_role_defaults_none(self):
        self.assertIsNone(self._parser().parse_args([]).role)

    def test_role_rejects_other_values(self):
        with self.assertRaises(SystemExit):
            self._parser().parse_args(["--role", "bogus"])


if __name__ == "__main__":
    main()
