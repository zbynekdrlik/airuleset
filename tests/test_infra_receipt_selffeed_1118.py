"""#1118 — the queue-arrival relay re-detected its OWN hub receipts (and ACKs that
quoted the marker) as new `GATEKEEPER-ACTION (INFRA)` arrivals.

Incident (odoo-erp hub #6883, 23.9.2026): `_post_hub_receipts` posted
`delivered <t> → gk-infra (<pane>) — arrival #<id> (GATEKEEPER-ACTION (INFRA))`
onto the same hub, and the arrival scan matched the LITERAL marker anywhere in a
comment body — so the receipt, a receipt of that receipt, and any ACK that merely
QUOTED the marker became NEW arrivals. INFRA nudge counts grew 106 → 111 with
nothing new addressed to INFRA.

Approach 1 (main design comment 5791160954):
  (a) the receipt renders a NEUTRAL kind token, never the literal marker;
  (b) ONE shared marker-match helper — the marker counts as a request only at the
      start of the body or a line (after optional leading `**`/whitespace), never
      mid-sentence, in backticks, or quoted;
  (c) the same helper excludes the receipt SHAPE (`^delivered … → gk-infra`) so
      receipts already on #6883 stop counting after deploy.
"""
import unittest
import unittest.mock as m
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
from watchdog import queue_arrival_recheck as qa  # noqa: E402

NOW = 1_700_000_000.0

# the four shapes from the ticket body — only the FIRST is a genuine request.
_REQUEST = "**GATEKEEPER-ACTION (INFRA)** — pool broke, rebuild the runner"
_RECEIPT_OF_REQUEST = ("delivered 05:30 CEST → gk-infra (zbynek:1.0) — "
                       "arrival #5788081735 (GATEKEEPER-ACTION (INFRA))")
_RECEIPT_OF_RECEIPT = ("delivered 05:50 CEST → gk-infra (zbynek:1.0) — "
                       "arrival #5788486854 (GATEKEEPER-ACTION (INFRA))")
_MIDSENTENCE_ACK = ("Thanks, I saw your GATEKEEPER-ACTION (INFRA) note and I am "
                    "on it now.")


class TestMarkerMatchHelper(unittest.TestCase):
    """(b)+(c): the ONE shared body→request classifier."""

    def test_scan_over_the_four_shapes_yields_exactly_one_request(self):
        bodies = [_REQUEST, _RECEIPT_OF_REQUEST, _RECEIPT_OF_RECEIPT,
                  _MIDSENTENCE_ACK]
        tags = [qa.infra_request_tag(b) for b in bodies]
        self.assertEqual([t for t in tags if t], ["GATEKEEPER-ACTION (INFRA)"])

    def test_bold_line_start_request_is_a_request(self):
        self.assertEqual(qa.infra_request_tag(_REQUEST),
                         "GATEKEEPER-ACTION (INFRA)")

    def test_plain_line_start_request_is_a_request(self):
        self.assertEqual(
            qa.infra_request_tag("GATEKEEPER-ACTION (INFRA): pool broke"),
            "GATEKEEPER-ACTION (INFRA)")

    def test_stop_line_start_request_is_a_request(self):
        self.assertEqual(qa.infra_request_tag("STOP: release is broken"),
                         "STOP:")

    def test_marker_on_a_later_line_start_is_a_request(self):
        body = "context line\n**GATEKEEPER-ACTION (INFRA)** — do the thing"
        self.assertEqual(qa.infra_request_tag(body),
                         "GATEKEEPER-ACTION (INFRA)")

    def test_receipt_of_request_is_not_a_request(self):
        self.assertIsNone(qa.infra_request_tag(_RECEIPT_OF_REQUEST))

    def test_receipt_of_receipt_is_not_a_request(self):
        self.assertIsNone(qa.infra_request_tag(_RECEIPT_OF_RECEIPT))

    def test_mid_sentence_marker_is_not_a_request(self):
        self.assertIsNone(qa.infra_request_tag(_MIDSENTENCE_ACK))

    def test_backticked_marker_is_not_a_request(self):
        self.assertIsNone(qa.infra_request_tag(
            "See `GATEKEEPER-ACTION (INFRA)` above for the convention."))

    def test_markdown_quoted_marker_is_not_a_request(self):
        # #818 class — a `> …` quoted reply echo must NOT register (preserved).
        self.assertIsNone(qa.infra_request_tag(
            "> GATEKEEPER-ACTION (INFRA): echoed reply"))

    def test_empty_or_nonstring_body_is_none(self):
        self.assertIsNone(qa.infra_request_tag(""))
        self.assertIsNone(qa.infra_request_tag(None))


class TestReceiptRendersNeutralToken(unittest.TestCase):
    """(a): a rendered receipt never carries the literal marker."""

    def _rec(self, cid, tag="GATEKEEPER-ACTION (INFRA)", num=6883):
        return {"id": cid, "kind": "comment", "num": num, "tag": tag}

    def test_gatekeeper_receipt_has_no_marker_literal(self):
        posts = []
        qa._post_hub_receipts({}, [self._rec(5771)], NOW, "gk-infra:0",
                              lambda num, text: posts.append((num, text)))
        self.assertEqual(len(posts), 1)
        _num, text = posts[0]
        self.assertNotIn("GATEKEEPER-ACTION (INFRA)", text)
        self.assertIn("infra-action", text)
        # and the rendered receipt is itself NOT re-detectable as a request.
        self.assertIsNone(qa.infra_request_tag(text))

    def test_stop_receipt_renders_stop_token(self):
        posts = []
        qa._post_hub_receipts({}, [self._rec(5772, tag="STOP:")], NOW,
                              "gk-infra:0",
                              lambda num, text: posts.append((num, text)))
        _num, text = posts[0]
        self.assertNotIn("STOP:", text)
        self.assertIn("stop", text)
        self.assertIsNone(qa.infra_request_tag(text))


class TestFetchExcludesReceiptsEndToEnd(unittest.TestCase):
    """End-to-end through the real fetch: the four shapes yield exactly ONE
    comment arrival (the request)."""

    def test_fetch_over_the_four_shapes_yields_one_arrival(self):
        def issue_list(*a, **k):
            class R:
                returncode = 0
                stderr = ""
                stdout = "[]"      # no infra tickets, only the hub comments
            return R()

        def fake_comments(number, *a, **k):
            if number == 6883:
                return [
                    {"id": 1, "body": _REQUEST, "html_url": "a"},
                    {"id": 2, "body": _RECEIPT_OF_REQUEST, "html_url": "b"},
                    {"id": 3, "body": _RECEIPT_OF_RECEIPT, "html_url": "c"},
                    {"id": 4, "body": _MIDSENTENCE_ACK, "html_url": "d"},
                ]
            return []

        with m.patch("airuleset._repo_root", return_value="/r"), \
                m.patch("airuleset._repo_slug",
                        return_value="zbynekdrlik/odoo-erp"), \
                m.patch("airuleset.resolve_authority", return_value="full"), \
                m.patch("subprocess.run", side_effect=issue_list), \
                m.patch("airuleset._infra_ticket_comments",
                        side_effect=fake_comments):
            out = airuleset._watchdog_infra_queue_fetch("/r")
        comment_ids = [r["id"] for r in out if r.get("kind") == "comment"]
        self.assertEqual(comment_ids, [1])
        self.assertEqual(out[[r["id"] for r in out].index(1)]["tag"],
                         "GATEKEEPER-ACTION (INFRA)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
