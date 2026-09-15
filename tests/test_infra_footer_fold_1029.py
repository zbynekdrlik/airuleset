"""#1029 item 3 — the pending infra stops belong in the INFRA window's `I`
count via the EXISTING role slice; NO sixth `S N` footer segment.

Design decision (statusline-vocabulary.md fixes the footer at exactly five
segments `I · U · W · gk · skip` under a hard width budget — a sixth segment
breaks the doctrine). On the gk-infra pane the pending stops are already
infra-actionable tickets (#6883 is `infra`-labelled, and every `infra` ticket the
FLOW session opens/comments a GATEKEEPER-ACTION (INFRA) onto), so they are ALREADY
counted in that window's `I` through `airuleset._role_filter_footer` — no fold, no
new counting machinery, no new segment. These locks pin the mechanism the
decision rests on, so a future change that stops counting infra stops in the infra
`I` (or that adds a sixth segment) fails.
"""
import sys
import unittest
import unittest.mock as m
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import airuleset  # noqa: E402
import cli_concurrency  # noqa: E402
import statusbar  # noqa: E402

ODOO = "zbynekdrlik/odoo-erp"


def _rows(*specs):
    return {k: {"createdAt": "2026-01-01T00:00:00Z", "title": str(k),
                "labels": [{"name": n} for n in labels]}
            for k, labels in specs}


class TestInfraStopsCountedInI(unittest.TestCase):
    def setUp(self):
        # the infra HUB #6883 (infra-labelled), one more infra ticket, one plain.
        self.workable = _rows((6883, ["infra"]), (7001, ["infra"]), (5000, []))
        self.waiting = _rows()
        self.ops = _rows()

    def _slice(self, role):
        with m.patch.object(cli_concurrency, "resolve_role", return_value=role), \
                m.patch.object(airuleset, "_repo_slug", return_value=ODOO):
            return airuleset._role_filter_footer(
                self.workable, self.waiting, self.ops, "/root", "/cwd")

    def test_infra_role_counts_the_infra_stops_in_I(self):
        # the gk-infra pane's `I` counts #6883 + the infra ticket, NOT the plain
        # one — so the pending infra stops are visible in the infra window's I,
        # no sixth segment needed.
        w, _wa, _o = self._slice("infra")
        self.assertIn(6883, w)
        self.assertIn(7001, w)
        self.assertNotIn(5000, w)

    def test_review_role_excludes_the_infra_stops(self):
        # the gk review pane's `I` does NOT count #6883/the infra ticket (they
        # route to the infra window) — the two windows show DIFFERENT I, which is
        # exactly why the infra stops need to be counted on the INFRA side.
        w, _wa, _o = self._slice("review")
        self.assertNotIn(6883, w)
        self.assertNotIn(7001, w)
        self.assertIn(5000, w)


class TestNoSixthFooterSegment(unittest.TestCase):
    def test_statusbar_has_no_stops_pending_segment(self):
        # the #1029 decision rejected item 3's literal `S N` (stops) segment;
        # prove no such segment renderer was introduced (the footer stays the
        # documented five: I · U · W · gk · skip).
        import inspect
        src = inspect.getsource(statusbar)
        self.assertNotIn("S N", src)
        self.assertNotIn("stops pending", src.lower())
        self.assertNotIn("stops_pending", src)


if __name__ == "__main__":
    unittest.main()
