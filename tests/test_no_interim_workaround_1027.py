"""#1027 unit 3 — NO INTERIM WORKAROUND while a fix is in flight.

Owner directive (miva1, 2026-09-14): while a fix/feature lane for the client's
report is in flight, the stream sends NO interim "how to work around it" reply —
it replies ONCE after the fix is on PROD and verified (the exception is a yes/no
question the client explicitly asked that the fix does not answer).

Enforced by EXTENDING the existing `gates.questionscope` Stop gate (#1025/#1026,
gate-family #1020 — NOT a new hook, NOT a fork): a `❓ ASKED`/`❓ NEEDS YOU`
turn whose inline text carries workaround phrasing (zatiaľ / medzitým / dovtedy /
obísť / ručne / workaround) while a referenced same-repo `#N` still has an open
implementation lane (the ticket is OPEN — the lane closes it when the fix is on
PROD) is BLOCKED. Cache-first, ONE gh call at most, fail-OPEN on any gh error
(with a logged line), retry-capped by the bash hook.

Covers:
  * gates.questionscope.decide — the workaround branch (block/allow), its
    precedence over the #1025 U-membership check, and the one-gh-call budget.
  * gates.questionscope._client_report_lane_in_flight — cache fast-path +
    single-gh fallback + fail-open.
  * hooks/stop-check-question-quality.sh — the workaround rule is enforced via
    the SAME questionscope invocation (documented there), not a new hook.
"""
import json
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import gates.questionscope as qs  # noqa: E402

HOOK = REPO / "hooks" / "stop-check-question-quality.sh"


def _payload(msg, cwd="/repo", sid=None):
    return json.dumps({"last_assistant_message": msg,
                       "session_id": sid or ("qs-" + uuid.uuid4().hex[:8]),
                       "cwd": cwd})


# A client-message-approval ❓ proposing an interim manual workaround while a fix
# for #288 is in flight (the shape the owner banned).
WORKAROUND_ASKED = (
    "**Otázka — projekt odoo-erp (Odoo ERP):** Klientka Alena hlási chybu filtra "
    "vo vlákne „Zákaznícky portál\". Oprava beží ako #288.\n\n"
    "> Dobrý deň Alena, zatiaľ to viete obísť tak, že filter nastavíte ručne.\n\n"
    "❓ ASKED: schváliš túto správu klientke k #288?"
)


class TestWorkaroundGateDecision(TestCase):
    def test_workaround_phrasing_with_open_lane_blocks(self):
        block, reason = qs.decide(
            _payload(WORKAROUND_ASKED),
            lane_fn=lambda nums, cwd: "in_flight")
        self.assertTrue(block)
        self.assertIn("288", reason)
        self.assertIn("workaround", reason.lower())

    def test_workaround_phrasing_but_fix_shipped_allows(self):
        # #288 closed → the fix is on PROD → the how-to reply is now allowed.
        block, reason = qs.decide(
            _payload(WORKAROUND_ASKED),
            lane_fn=lambda nums, cwd: "shipped")
        self.assertFalse(block)

    def test_workaround_phrasing_unmeasurable_allows_fail_open(self):
        block, reason = qs.decide(
            _payload(WORKAROUND_ASKED),
            lane_fn=lambda nums, cwd: "unmeasurable")
        self.assertFalse(block)

    def test_bypass_token_allows_and_skips_lane_check(self):
        # The sanctioned exception: a yes/no question the client explicitly asked
        # that the fix does not answer → `airuleset:client-reply-ok`.
        called = []
        msg = WORKAROUND_ASKED + "\nairuleset:client-reply-ok"
        block, reason = qs.decide(
            _payload(msg),
            lane_fn=lambda nums, cwd: called.append(1) or "in_flight")
        self.assertFalse(block)
        self.assertEqual(called, [])          # bypass → no gh/lane call at all

    def test_no_workaround_phrasing_does_not_run_lane_check(self):
        called = []
        clean = (
            "**Otázka — projekt odoo-erp:** V tickete #288 je otvorená otázka.\n\n"
            "❓ ASKED: rozhodni A alebo B pre #288?")
        block, reason = qs.decide(
            _payload(clean),
            u_count_fn=lambda c: 3,           # U>0 → #1025 path allows
            lane_fn=lambda nums, cwd: called.append(1) or "in_flight")
        self.assertFalse(block)
        self.assertEqual(called, [])          # no workaround phrase → no lane call

    def test_each_workaround_token_is_detected(self):
        for tok in ("zatiaľ", "medzitým", "dovtedy", "obísť", "ručne",
                    "workaround"):
            msg = ("**Otázka — projekt odoo-erp:** klient #288.\n\n"
                   "> Dobrý deň, %s to vyriešime dočasne.\n\n"
                   "❓ ASKED: schváliš správu k #288?" % tok)
            block, reason = qs.decide(
                _payload(msg), lane_fn=lambda nums, cwd: "in_flight")
            self.assertTrue(block, "token %r must trip the workaround rule" % tok)

    def test_workaround_phrasing_no_ref_allows(self):
        # No same-repo #N → no lane to check → allow (nothing to block against).
        called = []
        msg = ("**Otázka — projekt odoo-erp:** všeobecná správa.\n\n"
               "> zatiaľ to obídeme ručne.\n\n"
               "❓ NEEDS YOU: schváliš správu?")
        block, reason = qs.decide(
            _payload(msg),
            lane_fn=lambda nums, cwd: called.append(1) or "in_flight")
        self.assertFalse(block)
        self.assertEqual(called, [])

    def test_no_marker_allows(self):
        msg = WORKAROUND_ASKED.replace("❓ ASKED:", "Poznámka:")
        block, reason = qs.decide(
            _payload(msg), lane_fn=lambda nums, cwd: "in_flight")
        self.assertFalse(block)

    def test_workaround_precedence_blocks_before_u_membership(self):
        # A workaround message that ALSO would trip #1025 must block with the
        # WORKAROUND reason, and must NOT also run the U-membership gh call
        # (one gh call at most per decide()).
        u_called = []
        block, reason = qs.decide(
            _payload(WORKAROUND_ASKED),
            u_count_fn=lambda c: 0,
            question_fn=lambda nums, cwd: u_called.append(1) or "not_in_u",
            lane_fn=lambda nums, cwd: "in_flight")
        self.assertTrue(block)
        self.assertIn("workaround", reason.lower())
        self.assertEqual(u_called, [])        # U-membership gh call NOT made


class TestLaneInFlightHelper(TestCase):
    # #1027-review 🟡1: the common ONE-ref case uses an EXACT `gh issue view <N>
    # --json state` (no list cap to under-match); multiple refs use a single
    # `gh issue list`.
    def test_single_ref_open_is_in_flight(self):
        v = qs._client_report_lane_in_flight(
            [288], "/repo",
            runner=lambda argv, cwd: json.dumps({"state": "OPEN"}),
            cache_fn=lambda c: None)
        self.assertEqual(v, "in_flight")

    def test_single_ref_closed_is_shipped(self):
        v = qs._client_report_lane_in_flight(
            [288], "/repo",
            runner=lambda argv, cwd: json.dumps({"state": "CLOSED"}),
            cache_fn=lambda c: None)
        self.assertEqual(v, "shipped")

    def test_single_ref_uses_issue_view_not_list(self):
        seen = {}
        qs._client_report_lane_in_flight(
            [288], "/repo",
            runner=lambda argv, cwd: seen.setdefault("argv", argv) and None
            or json.dumps({"state": "OPEN"}),
            cache_fn=lambda c: None)
        self.assertEqual(seen["argv"][:3], ["gh", "issue", "view"])
        self.assertIn("288", seen["argv"])

    def test_multi_ref_open_via_list_is_in_flight(self):
        out = json.dumps([{"number": 288}, {"number": 5}])
        v = qs._client_report_lane_in_flight(
            [288, 999], "/repo", runner=lambda argv, cwd: out,
            cache_fn=lambda c: None)
        self.assertEqual(v, "in_flight")

    def test_multi_ref_none_open_via_list_is_shipped(self):
        out = json.dumps([{"number": 5}])
        v = qs._client_report_lane_in_flight(
            [288, 999], "/repo", runner=lambda argv, cwd: out,
            cache_fn=lambda c: None)
        self.assertEqual(v, "shipped")

    def test_gh_error_is_unmeasurable_fail_open(self):
        v = qs._client_report_lane_in_flight(
            [288], "/repo", runner=lambda argv, cwd: None, cache_fn=lambda c: None)
        self.assertEqual(v, "unmeasurable")

    def test_malformed_gh_output_is_unmeasurable(self):
        v = qs._client_report_lane_in_flight(
            [288], "/repo", runner=lambda argv, cwd: "not json",
            cache_fn=lambda c: None)
        self.assertEqual(v, "unmeasurable")

    def test_cache_fast_path_in_u_is_in_flight_zero_gh(self):
        # A ref in the box's FRESH cached U set is open → in_flight, ZERO gh.
        called = []
        v = qs._client_report_lane_in_flight(
            [288], "/repo",
            runner=lambda argv, cwd: called.append(1) or "[]",
            cache_fn=lambda cwd: {288, 5})
        self.assertEqual(v, "in_flight")
        self.assertEqual(called, [])          # cache hit → no gh call

    def test_no_numbers_is_shipped(self):
        v = qs._client_report_lane_in_flight(
            [], "/repo", runner=lambda argv, cwd: None, cache_fn=lambda c: None)
        self.assertEqual(v, "shipped")


class TestHookWiring(TestCase):
    def test_hook_documents_workaround_rule(self):
        t = HOOK.read_text(encoding="utf-8")
        # The rule is enforced via the SAME questionscope invocation — the hook
        # must NAME it so a reader knows this gate also covers #1027.
        self.assertIn("1027", t)
        self.assertIn("workaround", t.lower())

    def test_hook_still_routes_to_questionscope(self):
        t = HOOK.read_text(encoding="utf-8")
        self.assertIn("gates.questionscope", t)


if __name__ == "__main__":
    main()
