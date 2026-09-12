"""#993 — the lane picker sorts `architecture-rework` FIRST.

Lane ordering (design): architecture-rework -> prio:bounce -> infra (serial) ->
independent units. The seed the /goal loop reads (`core-quals --list` /
`slice-quals --list`, both via `_print_issue_rows`) is OLDEST-first; a ticket
carrying the `architecture-rework` label must jump to the FRONT regardless of
age, so the loop's oldest-picks-first selection takes it before any other lane.
"""

import io
from contextlib import redirect_stdout
from unittest import TestCase, main

import cli_quals_cmd


def _row(created, title, labels):
    return {"createdAt": created, "title": title,
            "labels": [{"name": n} for n in labels]}


class TestArchitectureReworkPickerOrder(TestCase):
    def _emit(self, rows):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_quals_cmd._print_issue_rows(rows)
        return [ln.split("\t", 1)[0] for ln in buf.getvalue().splitlines() if ln]

    def test_architecture_rework_sorts_before_older_plain_ticket(self):
        rows = {
            "10": _row("2026-01-01T00:00:00Z", "old plain ticket", []),
            "20": _row("2026-09-01T00:00:00Z", "newer rework", ["architecture-rework"]),
            "30": _row("2026-05-01T00:00:00Z", "mid plain", ["bug"]),
        }
        order = self._emit(rows)
        self.assertEqual(order[0], "20",
                         "architecture-rework must lead despite being newest")

    def test_multiple_rework_tickets_are_oldest_first_among_themselves(self):
        rows = {
            "10": _row("2026-01-01T00:00:00Z", "old plain", []),
            "20": _row("2026-09-01T00:00:00Z", "newer rework", ["architecture-rework"]),
            "21": _row("2026-03-01T00:00:00Z", "older rework", ["architecture-rework"]),
        }
        order = self._emit(rows)
        self.assertEqual(order[:2], ["21", "20"],
                         "rework tickets lead, oldest-first among themselves")
        self.assertEqual(order[2], "10")

    def test_no_rework_label_is_plain_oldest_first(self):
        rows = {
            "10": _row("2026-05-01T00:00:00Z", "b", ["bug"]),
            "20": _row("2026-01-01T00:00:00Z", "a", []),
        }
        order = self._emit(rows)
        self.assertEqual(order, ["20", "10"])


if __name__ == "__main__":
    main()
