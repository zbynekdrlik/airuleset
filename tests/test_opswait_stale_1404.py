"""#1404 items 1+2: a W ticket re-proves its wait every 3 days, and a passed
`Ops-wait-target: <event> by <date>` sends it to I at once.

Owner 28.11.2026: "aspoň raz za 3 dni ak je niečo v W alebo na základe
nejakého časovača čo sám čaká na nejakú udalosť by sa W veci mali rozhýbať."
The read side is `gates.opswait.unbacked_reason` over the comment ages
(`cli_quals._ages_from_comments`: `waiting_on`, `own_cited`, `own_target`);
`cli_ticket_facts.read_wait` serves it to the footer from the W prefetch.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cli_quals  # noqa: E402
import cli_ticket_facts  # noqa: E402
from gates import opswait  # noqa: E402

DAY = 86400
NOW = 1795860000.0                       # 2026-11-28T09:20:00Z
GOOD = "Waiting-on: Hetzner support — reply on ticket 4711 — read the ticket"
ME = "me-bot"


def _c(iso, body, login=ME):
    return {"createdAt": iso, "author": {"login": login}, "body": body}


class Window(unittest.TestCase):
    def test_window_is_three_days(self):
        self.assertEqual(opswait.STALE_S, 3 * DAY)

    def test_the_three_day_boundary(self):
        self.assertEqual(opswait.unbacked_reason({"waiting_on": NOW - 3 * DAY}, NOW), "")
        self.assertIn("3 days old", opswait.unbacked_reason(
            {"waiting_on": NOW - 3 * DAY - 1}, NOW))

    def test_a_four_day_old_line_counts_in_i(self):
        self.assertTrue(opswait.unbacked_reason({"waiting_on": NOW - 4 * DAY}, NOW))

    def test_a_newer_cited_push_still_backs_it(self):
        ages = {"waiting_on": NOW - 5 * DAY, "own_cited": NOW - 1 * DAY}
        self.assertEqual(opswait.unbacked_reason(ages, NOW), "")


class Target(unittest.TestCase):
    def test_passed_target_counts_in_i_at_once(self):
        ages = {"waiting_on": NOW - 3600, "own_target": "2026-11-27"}
        r = opswait.unbacked_reason(ages, NOW)
        self.assertIn("2026-11-27", r)
        self.assertIn("passed", r)

    def test_target_today_or_later_keeps_w(self):
        for day in ("2026-11-28", "2026-12-05"):
            self.assertEqual(opswait.unbacked_reason(
                {"waiting_on": NOW - 3600, "own_target": day}, NOW), "", day)

    def test_future_target_never_extends_the_window(self):
        ages = {"waiting_on": NOW - 4 * DAY, "own_target": "2026-12-20"}
        self.assertTrue(opswait.unbacked_reason(ages, NOW))

    def test_a_garbage_target_is_ignored(self):
        for bad in (None, 20261127, "", "soon", True):
            self.assertEqual(opswait.unbacked_reason(
                {"waiting_on": NOW - 3600, "own_target": bad}, NOW), "", bad)

    def test_the_same_evidence_through_the_footer_read(self):
        """The fact the footer reads (`read_wait`) parses the SAME comments the
        triage notice reads: an own target comment that passed moves the row."""
        comments = [_c("2026-11-28T08:00:00Z", GOOD, login="hetzner-relay"),
                    _c("2026-11-20T08:00:00Z",
                       "Ops-wait-target: Hetzner reply by 2026-11-26")]
        ages = cli_quals._ages_from_comments(comments, ME)
        self.assertEqual(ages["own_target"], "2026-11-26")
        out = cli_ticket_facts.read_wait({7}, lambda n: ages, NOW)
        self.assertIn(7, out)
        self.assertIn("2026-11-26", out[7])


if __name__ == "__main__":
    unittest.main()
