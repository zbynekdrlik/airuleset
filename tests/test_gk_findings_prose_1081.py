"""#1081 -- gk finding ids come ONLY from finding SHAPES, never bare prose tokens.

The bare `([A-Z]\\d+)` arm of `airuleset._GK_FINDING_ID_RE` turned a gate check
code (`B22-e trieda`) and the gk's mutation-probe labels (`M1 clobber ... M7
write-by-code`) into "finding ids", so `cli_gk_watch.watch_issue` reported
`needs-disposition B22,M1,...,M8` and `airuleset.py handoff` BLOCKED a legitimate
RFR (odoo-erp 7599, 18.9.2026).

Fix (main ruling on the Design-question): ids come ONLY from (a) a BULLET-ANCHORED
emoji marker — line start + optional indent/bullet/bold, then `(🔴|🟡|🔵)<n>` — and
(b) the legacy `F<n>` form at a line/bullet boundary (never mid-word). A
mid-sentence emoji mention, the count line (`0 🔴 · 2 🟡 · 5 🔵`, number BEFORE the
emoji), and any bare `[A-Z]\\d+` token are all EXCLUDED positionally. There is NO
count-line cross-check: it was a silent fail-open under stable cross-round
numbering (a survivor's number > the reduced open count got dropped) and was not
load-bearing for the incident.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import airuleset
import cli_gk_watch as gw

# A realistic odoo-erp 7599-shaped gk ACCEPT verdict: the count line, real
# finding bullets (🟡1/🟡2/🔵1..🔵5), plus the exact prose the old bare-letter arm
# mis-read -- a gate code (`B22-e`), the mutation-probe labels (`M1..M8`, no
# `M6`), a model fragment (`claude-opus-4-8`), a `PR<n>` token, other gate codes
# (`E24`, `S3`), and mid-sentence emoji mentions (`adv 🟡1 ... 🔴1`).
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

# A real BOUNCE whose ONE 🔴 finding is genuinely open -- the new parser must
# STILL surface it (never over-narrow), while dropping the `B22-c` gate note and
# the `M4` probe in the same bullet.
GK_BOUNCE_RED1 = (
    "## Gatekeeper review -- BOUNCE\n"
    "gk-state: BOUNCE @beef1234\n"
    "**Počty (otvorené @ beef1234): 1 🔴 · 0 🟡 · 0 🔵**\n"
    "- 🔴1 (security) token logged; B22-c gate note, mutačná sonda M4 revert\n"
)

# A mid-sentence emoji mention is NOT a finding id (not at a bullet position).
MID_SENTENCE = (
    "Poznámka k procesu: viď 🟡3 vyššie v tomto komentári — to nie je nový "
    "nález, len odkaz na predchádzajúce kolo.\n"
)

# STABLE cross-round numbering: a survivor bullet `🟡3` under a `1 🟡` count line
# IS a real open finding (the old count-cap fail-open dropped it; the anchored
# shape keeps it — it is a genuine bullet).
BULLET_STABLE = (
    "**Počty (otvorené @ s): 0 🔴 · 1 🟡 · 0 🔵**\n"
    "- 🟡3 (survivor across rounds — its number exceeds the reduced open count)\n"
)

GK = "zbynekdrlik"
STREAM = "odoo-erp-stream-tokens"


def _row(cid, login, body, created_at):
    return {"id": cid, "login": login, "body": body, "created_at": created_at}


class ParseFindingsShapesOnly(unittest.TestCase):
    def test_7599_ids_are_only_the_emoji_numbers(self):
        # 🟡1,🟡2 + 🔵1..🔵5 bullets collapse (digit-only, first-seen) to 1..5;
        # the mid-sentence `🟡1`/`🔴1` mentions are NOT at bullet positions.
        self.assertEqual(
            airuleset._parse_gk_findings(GK_7599_ACCEPT),
            ["1", "2", "3", "4", "5"])

    def test_7599_prose_tokens_are_never_ids(self):
        ids = airuleset._parse_gk_findings(GK_7599_ACCEPT)
        for bogus in ("B22", "M1", "M2", "M3", "M4", "M5", "M7", "M8",
                      "E24", "S3", "R7599", "PR7599"):
            self.assertNotIn(bogus, ids, "prose token %r must not be an id"
                             % bogus)

    def test_mid_sentence_emoji_mention_is_not_an_id(self):
        # A `🟡3` in the middle of a sentence is a reference, not a finding.
        self.assertEqual(airuleset._parse_gk_findings(MID_SENTENCE), [])

    def test_bullet_survivor_above_count_is_still_an_id(self):
        # STABLE numbering: 🟡3 as a real bullet under a `1 🟡` count is kept
        # (the removed count-cap would have dropped it — the fail-open).
        self.assertEqual(airuleset._parse_gk_findings(BULLET_STABLE), ["3"])

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

    def test_line_start_emoji_findings_parse(self):
        # An emoji at line start (with or without a bullet marker) is a finding.
        self.assertEqual(
            airuleset._parse_gk_findings("🔴 1 sec\n🟡 2 corr"), ["1", "2"])

    def test_empty_and_none(self):
        self.assertEqual(airuleset._parse_gk_findings(""), [])
        self.assertEqual(airuleset._parse_gk_findings(None), [])


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

    def test_zero_is_never_a_finding_id(self):
        # A `🔴 0` bullet must not leak "0" (#1081 review): no finding is #0.
        ids = airuleset._parse_gk_findings("- 🔴 0 weird\n- 🟡 1 real")
        self.assertNotIn("0", ids)
        self.assertIn("1", ids)


class WatchIssueNoFalseBlock(unittest.TestCase):
    def _watch(self, rows, now, **kw):
        return gw.watch_issue(7, fetch=lambda _i: rows, gk_login=GK,
                              self_login=STREAM, now=now, **kw)

    def test_7599_accept_then_real_dispositions_is_rfr_current(self):
        # old RFR, then gk ACCEPT (raises ids 1..5 from bullets), then a stream
        # disposition comment naming every REAL id. The pre-#1081 parser also
        # raised B22,M1.. -> needs-disposition (the false block); the new parser
        # raises only 1..5, all dispositioned -> rfr-current.
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
