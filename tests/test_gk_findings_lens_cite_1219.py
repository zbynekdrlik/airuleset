"""#1219: a lens citation such as `(#5244 F1)` inside a gk verdict sentence is
NOT an open finding id. The legacy `F<n>` arm of `_GK_FINDING_ID_RE` must be
line/bullet anchored like the emoji arm (#1081), else the composer pre-flight
raises a false `needs-disposition F1` on an ACCEPT that only cites a lens.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset  # noqa: E402
import cli_gk_watch  # noqa: E402


class TestLensCitationIsNotAFinding(unittest.TestCase):

    def test_mid_sentence_lens_citation_is_not_an_id(self):
        self.assertEqual(
            airuleset._parse_gk_findings("ACCEPT. Lens per (#5244 F1) applied"),
            [])

    def test_mid_sentence_space_prefixed_f_is_not_an_id(self):
        self.assertEqual(airuleset._parse_gk_findings("see lens F2 in prior"), [])
        self.assertEqual(airuleset._parse_gk_findings("as in [F3] before"), [])

    def test_line_anchored_legacy_forms_still_parse(self):
        self.assertEqual(airuleset._parse_gk_findings("F3 finding here"), ["F3"])
        self.assertEqual(airuleset._parse_gk_findings("- F7: still broken"), ["F7"])
        self.assertEqual(airuleset._parse_gk_findings("  * **F4** bad"), ["F4"])
        self.assertEqual(airuleset._parse_gk_findings("• F5 x"), ["F5"])

    def test_accept_citing_a_lens_is_not_needs_disposition(self):
        rows = [
            {"id": 1, "login": "stream", "created_at": "2026-11-01T10:00:00Z",
             "body": "READY-FOR-REVIEW\nBranch: lane-a\nHEAD: abcdef1234"},
            {"id": 2, "login": "gk", "created_at": "2026-11-01T11:00:00Z",
             "body": "ACCEPT. Lens per (#5244 F1) applied @abcdef1234"},
        ]
        st = cli_gk_watch.watch_issue(1, fetch=lambda i: rows, gk_login="gk",
                                      self_login="stream", now=1.9e9)
        self.assertEqual(st["state"], "rfr-current")
        self.assertEqual(st["undispositioned_ids"], [])


if __name__ == "__main__":
    unittest.main()
