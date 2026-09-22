"""#1081 -- gk finding ids come ONLY from finding SHAPES, never bare prose tokens.

RED-first (dispatch): the bare `([A-Z]\\d+)` arm of `airuleset._GK_FINDING_ID_RE`
turned a gate check code (`B22-e trieda`) and the gk's mutation-probe labels
(`M1 clobber ... M7 write-by-code`) into "finding ids", so `cli_gk_watch.
watch_issue` reported `needs-disposition B22,M1,...,M8` and `airuleset.py handoff`
BLOCKED a legitimate RFR (odoo-erp 7599, 18.9.2026).

Approach 1: ids come from (a) emoji finding markers `(?:🔴|🟡|🔵)<n>`, (b) the
legacy `F<n>` form ONLY at a line/bullet boundary (never mid-word), cross-checked
against (c) the verdict COUNT line (`0 🔴 · 2 🟡 · 5 🔵`) so an emoji number
beyond its severity's open count is a prose mention and is dropped. Gate codes,
probe labels, and any other `[A-Z]\\d+` token are never ids.

These tests fail on the pre-#1081 parser (they assert the false ids are ABSENT
and the new `cli_gk_watch.severity_counts`/`parse_findings` helpers exist).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_gk_watch as gw

# A realistic odoo-erp 7599-shaped gk ACCEPT verdict: the per-severity count
# line, real finding bullets (🟡1/🟡2/🔵1..🔵5), plus the exact prose the old
# bare-letter arm mis-read -- a gate code (`B22-e`), the mutation-probe labels
# (`M1..M8`, no `M6`), a model fragment (`claude-opus-4-8`), a `PR<n>` token, and
# other gate codes (`E24`, `S3`). Also a SECOND `·`-triple in prose ("nový
# count") that must NOT be taken as the authoritative count line.
GK_7599_ACCEPT = (
    "## Gatekeeper review verdict: ACCEPT\n"
    "gk-state: ACCEPT @13aa61ad4\n"
    "Branch: montalu/7599-ai-eval-navody-sklad\n"
    "adv 🟡1 (apex blocklist) a 🔴1 (ratchet) sú v diffe reálne uzavreté "
    "(#6580 r1 / B22-e trieda). gk mutačné sondy M1 clobber, M2 default, "
    "M3 allowlist, M4 refusal, M5 revert, M7 write-by-code, M8 require_truthy "
    "(žiadny M6). model claude-opus-4-8, PR7599, gate E24, storage S3.\n"
    "\n"
    "**Počty (otvorené @ 13aa61ad4): 0 🔴 · 2 🟡 · 5 🔵**\n"
    "\n"
    "- 🟡1 (process) ADVISORY-UNDISPOSITIONED, batch 7 / B22-e trieda\n"
    "- 🟡2 (process) followup_candidates vs odložený deliverable\n"
    "- 🔵1 (security) `_assert_copy_target` matcher\n"
    "- 🔵2 (correctness) eskalácia agenta pod --live-instructions\n"
    "- 🔵3 (process) ratchet extract vs pack\n"
    "- 🔵4 (security) hostname vs URL substring\n"
    "- 🔵5 (evidence-integrity) json/2 wording\n"
    "nový count `0 🔴 · 0 🟡 · 5 🔵` po dispozícii 🟡1/🟡2.\n"
)

# A real BOUNCE whose ONE 🔴 finding is genuinely open (a count that INCLUDES it)
# -- the new parser must STILL surface it (never over-narrow), while dropping the
# `B22-c` gate note and the `M4` probe in the same bullet.
GK_BOUNCE_RED1 = (
    "## Gatekeeper review -- BOUNCE\n"
    "gk-state: BOUNCE @beef1234\n"
    "**Počty (otvorené @ beef1234): 1 🔴 · 0 🟡 · 0 🔵**\n"
    "- 🔴1 (security) token logged; B22-c gate note, mutačná sonda M4 revert\n"
)

# A bullet id BEYOND its severity's open count is a prose mention -> dropped.
CAP_BODY = (
    "**Počty (otvorené @ abc1234): 0 🔴 · 2 🟡 · 0 🔵**\n"
    "- 🟡1 real finding\n"
    "- 🟡2 real finding\n"
    "- 🟡3 (prose mention -- beyond the 2 🟡 count) nižšie ako 🟡1/🟡2\n"
)

GK = "zbynekdrlik"
STREAM = "odoo-erp-stream-tokens"


def _row(cid, login, body, created_at):
    return {"id": cid, "login": login, "body": body, "created_at": created_at}


class ParseFindingsShapesOnly(unittest.TestCase):
    def test_7599_ids_are_only_the_emoji_numbers(self):
        # 🟡1,🟡2 + 🔵1..🔵5 collapse (digit-only, first-seen) to 1..5; the 🔴1
        # prose mention (count 0 🔴) contributes nothing new.
        self.assertEqual(
            airuleset._parse_gk_findings(GK_7599_ACCEPT),
            ["1", "2", "3", "4", "5"])

    def test_7599_prose_tokens_are_never_ids(self):
        ids = airuleset._parse_gk_findings(GK_7599_ACCEPT)
        for bogus in ("B22", "M1", "M2", "M3", "M4", "M5", "M7", "M8",
                      "E24", "S3", "R7599", "PR7599"):
            self.assertNotIn(bogus, ids, "prose token %r must not be an id"
                             % bogus)

    def test_count_line_caps_per_severity(self):
        # 🟡3 exceeds the 2 🟡 open count -> dropped; no other severity carries 3.
        self.assertEqual(airuleset._parse_gk_findings(CAP_BODY), ["1", "2"])

    def test_legacy_f_id_at_line_start(self):
        self.assertIn("F3", airuleset._parse_gk_findings("F3 finding here"))
        self.assertIn("F7", airuleset._parse_gk_findings("- F7: still broken"))

    def test_legacy_f_id_never_mid_word(self):
        # F inside a word (or a token) is not a finding id.
        self.assertEqual(airuleset._parse_gk_findings("conF3ig midword"), [])
        self.assertEqual(airuleset._parse_gk_findings("utF8 encoding"), [])

    def test_bare_letter_tokens_alone_yield_nothing(self):
        for prose in ("E24 gate", "S3 storage", "PR7599 merged",
                      "claude-opus-4-8", "B22-e trieda", "M1 clobber"):
            self.assertEqual(airuleset._parse_gk_findings(prose), [],
                             "bare token prose %r must yield no ids" % prose)

    def test_emoji_ids_still_parse_without_a_count_line(self):
        # No count line -> no cap, every emoji finding kept.
        self.assertEqual(
            airuleset._parse_gk_findings("🔴 1 sec\n🟡 2 corr"), ["1", "2"])

    def test_empty_and_none(self):
        self.assertEqual(airuleset._parse_gk_findings(""), [])
        self.assertEqual(airuleset._parse_gk_findings(None), [])


class SeverityCounts(unittest.TestCase):
    def test_parses_the_authoritative_pocty_line(self):
        self.assertEqual(gw.severity_counts(GK_7599_ACCEPT),
                         {"🔴": 0, "🟡": 2, "🔵": 5})

    def test_ignores_a_non_authoritative_triple_in_prose(self):
        # The "nový count 0 🔴 · 0 🟡 · 5 🔵" prose line is NOT the count line;
        # only the `Počty (otvorené …)` line counts (else 🟡 would read 0).
        self.assertEqual(gw.severity_counts(GK_7599_ACCEPT)["🟡"], 2)

    def test_total_count_form_has_no_per_severity_caps(self):
        # The #1056 fixture's `**Počty (otvorené @ sha)**: 2` total form carries
        # no `<n> <emoji>` pair -> {} -> no cap applied.
        self.assertEqual(
            gw.severity_counts("**Počty (otvorené @ a1b2c3d4)**: 2"), {})

    def test_no_count_line(self):
        self.assertEqual(gw.severity_counts("just a note"), {})
        self.assertEqual(gw.severity_counts(""), {})


class ParseFindingsHelper(unittest.TestCase):
    def test_parse_findings_direct_output(self):
        # Assert the impl's OUTPUT directly (not merely equal-to-the-delegator,
        # which would be a tautology since _parse_gk_findings delegates to it).
        self.assertEqual(
            gw.parse_findings("- 🟡1 a\n- 🔵1 b\n- 🔵2 c",
                              airuleset._GK_FINDING_ID_RE),
            ["1", "2"])

    def test_public_parser_delegates_to_helper(self):
        # The delegation contract (the public entry produces the impl's result).
        self.assertEqual(
            airuleset._parse_gk_findings(GK_7599_ACCEPT),
            gw.parse_findings(GK_7599_ACCEPT, airuleset._GK_FINDING_ID_RE))

    def test_count_cap_keeps_all_contiguous_findings(self):
        # The real gk template numbers open findings 1..count per severity, so
        # count == bullet count and NOTHING real is dropped — the cap only ever
        # drops a prose number ABOVE the count (#1081 review, safe-case lock).
        body = ("**Počty (otvorené @ s): 0 🔴 · 0 🟡 · 3 🔵**\n"
                "- 🔵1 a\n- 🔵2 b\n- 🔵3 c\n")
        self.assertEqual(airuleset._parse_gk_findings(body), ["1", "2", "3"])

    def test_zero_is_never_a_finding_id(self):
        # An emoji-first count line (`🔴 0 · 🟡 2`) must not leak "0" (#1081
        # review): no finding is numbered 0.
        ids = airuleset._parse_gk_findings(
            "**Počty (otvorené @ s): 🔴 0 · 🟡 2 · 🔵 5**\n- 🟡1 x\n- 🟡2 y")
        self.assertNotIn("0", ids)


class WatchIssueNoFalseBlock(unittest.TestCase):
    def _watch(self, rows, now, **kw):
        return gw.watch_issue(7, fetch=lambda _i: rows, gk_login=GK,
                              self_login=STREAM, now=now, **kw)

    def test_7599_accept_then_real_dispositions_is_rfr_current(self):
        # old RFR, then gk ACCEPT (raises ids), then a stream disposition
        # comment naming every REAL id (1..5). The pre-#1081 parser also raised
        # B22,M1.. from the ACCEPT prose -> needs-disposition (the false block);
        # the new parser raises only 1..5, all dispositioned -> rfr-current.
        disp = (
            "Closes-finding: 🟡1 — fixed in `eval_navody.py:435`\n"
            "Closes-finding: 🟡2 — followup_candidates line added\n"
            "Closes-finding: 🔵1 — gk fix-forward\n"
            "Closes-finding: 🔵2 — Self-review-model line\n"
            "Closes-finding: 🔵3 — gk fix-forward\n"
            "Closes-finding: 🔵4 — gk fix-forward\n"
            "Closes-finding: 🔵5 — docstring wording\n"
        )
        rows = [
            _row(1, STREAM, "READY-FOR-REVIEW: branch x head old1234\n",
                 "2026-09-18T10:00:00Z"),
            _row(2, GK, GK_7599_ACCEPT, "2026-09-18T11:00:00Z"),
            _row(3, STREAM, disp, "2026-09-18T12:00:00Z"),
        ]
        r = self._watch(rows, now=gw._parse_iso("2026-09-18T13:00:00Z"))
        self.assertEqual(r["state"], "rfr-current")
        self.assertEqual(r["undispositioned_ids"], [])

    def test_real_bounce_finding_still_surfaces(self):
        rows = [_row(2, GK, GK_BOUNCE_RED1, "2026-09-18T11:00:00Z")]
        r = self._watch(rows, now=gw._parse_iso("2026-09-18T13:00:00Z"))
        self.assertEqual(r["state"], "bounce-unanswered")
        self.assertEqual(r["undispositioned_ids"], ["1"])


if __name__ == "__main__":
    unittest.main()
