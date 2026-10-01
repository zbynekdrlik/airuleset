"""#1213: an owner question on a FOREIGN `stream:*` ticket that THIS
full-authority box asked (a ❓ ping naming the ticket in its question map)
counts in this box's U, not HIDDEN.

odoo-erp #8827 (1.10.2026): gk held PR 8833 for a direct owner confirmation and
asked once (`❓ ASKED`). The ticket was `stream:david` + `needs-answer`, so the
#1141 rule hid it on gk ("counted in U on that stream's box"), and the
ticket-referencing ping was not counted as ticketless either. gk showed U 0
for ~10 h while the fix waited on the owner's answer to gk.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli_ticket_route as route  # noqa: E402
import cli_ticket_state as ts  # noqa: E402

N = 8827
ROW = {"labels": [{"name": "stream:montalu1"}, {"name": "needs-answer"}], "title": "t"}


class TestClassify(unittest.TestCase):

    def test_without_an_asked_ref_it_stays_hidden(self):
        self.assertEqual(ts.classify(ROW, None, ts.Box(), number=N)[0], ts.HIDDEN)

    def test_asked_by_this_box_it_counts_in_u(self):
        bucket, reason = ts.classify(ROW, None, ts.Box(asked=frozenset({N})), number=N)
        self.assertEqual(bucket, "U")
        self.assertIn("#1213", reason)
        self.assertIn("this box asked", reason)

    def test_bucketize_passes_the_number(self):
        out = ts.bucketize({N: ROW}, None, ts.Box(asked=frozenset({N})))
        self.assertIn(N, out["U"])
        self.assertNotIn(N, out[ts.HIDDEN])

    def test_a_slice_box_without_an_asked_ref_keeps_its_own_route(self):
        box = ts.Box(own_stream="montalu2")
        self.assertEqual(ts.classify(ROW, None, box, number=N)[0], "I")


class TestAskedBeatsEveryBucket(unittest.TestCase):
    """Live gk finding (odoo-erp 8715): the asked ticket carried no owner label,
    so it was I, not HIDDEN, and the question was still counted nowhere."""

    HANDOFF = {"labels": [{"name": "stream:montalu1"}, {"name": "needs-gatekeeper"},
                          {"name": "gk-processing"}], "title": "t"}
    OPS_WAIT = {"labels": [{"name": "ops-wait"}], "title": "t"}

    def test_an_asked_hand_off_row_is_u_not_i(self):
        self.assertEqual(ts.classify(self.HANDOFF, None, ts.Box(), number=N)[0], "I")
        bucket, reason = ts.classify(self.HANDOFF, ts.TicketFacts().of(N),
                                     ts.Box(asked=frozenset({N})), number=N)
        self.assertEqual(bucket, "U")
        self.assertIn("#1213", reason)

    def test_an_asked_ops_wait_row_is_u_not_w(self):
        self.assertEqual(ts.classify(self.OPS_WAIT, None, ts.Box(asked=frozenset({N})),
                                     number=N)[0], "U")

    def test_a_stream_box_counts_its_own_asked_ticket(self):
        own = {"labels": [{"name": "stream:montalu2"}], "title": "t"}
        box = ts.Box(own_stream="montalu2", asked=frozenset({N}))
        self.assertEqual(ts.classify(own, ts.TicketFacts().of(N), box, number=N)[0], "U")

    def test_footer_fills_asked_on_a_stream_box_too(self):
        with mock.patch.object(route, "asked_refs", return_value=frozenset({N})), \
                mock.patch.object(route.cli_ticket_facts, "refresh",
                                  return_value=ts.TicketFacts()):
            buckets, _f = route.footer({N: self.HANDOFF}, "/r", "o/r", merged=[],
                                       own_stream="montalu2")
        self.assertIn(N, buckets["U"])


class TestAskedRefs(unittest.TestCase):

    def test_reads_the_question_map(self):
        with mock.patch("statusbar.question_map_ticket_refs", return_value={N, 5}):
            self.assertEqual(route.asked_refs("/r"), frozenset({N, 5}))

    def test_unreadable_or_broken_is_empty(self):
        with mock.patch("statusbar.question_map_ticket_refs", return_value=None):
            self.assertEqual(route.asked_refs("/r"), frozenset())
        with mock.patch("statusbar.question_map_ticket_refs", side_effect=OSError("x")):
            self.assertEqual(route.asked_refs("/r"), frozenset())


class TestRoutesFillTheBox(unittest.TestCase):

    def test_footer_counts_it_in_u_on_a_core_box(self):
        with mock.patch.object(route, "asked_refs", return_value=frozenset({N})), \
                mock.patch.object(route.cli_ticket_facts, "refresh",
                                  return_value=ts.TicketFacts()):
            buckets, _f = route.footer({N: ROW}, "/r", "o/r", merged=[])
        self.assertIn(N, buckets["U"])

    def test_quals_counts_it_in_u_on_a_core_box(self):
        import cli_quals_cmd
        import cli_release_state
        with mock.patch.object(route, "asked_refs", return_value=frozenset({N})), \
                mock.patch.object(route.cli_ticket_facts, "load", return_value=ts.TicketFacts()), \
                mock.patch.object(cli_quals_cmd, "_merged_unreleased", return_value=set()), \
                mock.patch.object(cli_release_state, "merged_unreleased_partial",
                                  return_value=None):
            buckets, _f = route.quals({N: ROW}, "/r", ts.Box())
        self.assertIn(N, buckets["U"])


class TestExplainCore(unittest.TestCase):

    def test_explain_names_the_reason_without_a_parity_break(self):
        import io
        from contextlib import redirect_stdout
        import cli_ticket_explain as explain
        facts = ts.TicketFacts()
        buckets = ts.bucketize({N: ROW}, facts, ts.Box(asked=frozenset({N})))
        out = io.StringIO()
        with mock.patch.object(route, "asked_refs", return_value=frozenset({N})), \
                redirect_stdout(out):
            explain.explain_core(None, buckets, facts, root="/r")
        text = out.getvalue()
        self.assertIn("#1213", text)
        self.assertNotIn("mismatch", text)


if __name__ == "__main__":
    unittest.main()
