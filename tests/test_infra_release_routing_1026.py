"""#1026 (airuleset half) — an INFRA-caused release block is NEVER an owner
question. A `❓ ASKED`/`❓ NEEDS YOU` turn that (a) NAMES a same-repo ticket
carrying the `infra` label, OR (b) whose TEXT names a release-block shape
(`deploy-prod`, `startup_failure`, `fast-track`, `hotfix-main`,
`release-fasttrack-exception`), is blocked with the infra-routing reason: open/
update the infra ticket + tag `GATEKEEPER-ACTION (INFRA)` on the hub (the #1029
rider wakes the INFRA session); the owner is only INFORMED, never ASKED.

Owner ruling (odoo-erp gk FLOW, 14.9.2026): "vydat marker by mal mat opravnenie
aj infra claude ... ak tu bol znova nejaky infra problem kvoli ktoremu to
nepreslo tak by sa to malo riesit s infra a nie so mnou". Item 1 (the marker
permission in odoo-erp `block-main-merge.sh`) is odoo-erp code (relayed to the
infra hub); THIS lane ships the airuleset half — the FLOW routing gate + the
release-lane-discipline doctrine.

Covers:
  * gates.questionscope.decide — the two infra triggers (text-shape + infra
    label), the byte-identical-to-today plain-question path, and gh-error
    fail-open.
  * cli_quals.question_ticket_in_u — the new `infra` verdict from the SAME
    single gh call the #1025 U-membership check already makes.
  * modules/core/release-lane-discipline.md — rule 5 content-lock (window-teeth
    on the operative negation, #799 style).
"""
import json
import subprocess
import sys
import time
import unittest.mock as m
import uuid
from pathlib import Path
from unittest import TestCase, main

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import cli_quals  # noqa: E402
import gates.questionscope as qs  # noqa: E402
import statusbar  # noqa: E402


def _payload(msg, cwd="/repo", sid=None):
    return json.dumps({"last_assistant_message": msg,
                       "session_id": sid or ("qs-" + uuid.uuid4().hex[:8]),
                       "cwd": cwd})


# A ❓ NEEDS YOU about a release block whose TEXT carries the shape keywords but
# names NO same-repo ticket (the odoo-erp refs are cross-repo → excluded).
RELEASE_BLOCK_TEXT = (
    "**Otázka — projekt odoo-erp (Odoo ERP fleet):** release 2.288 padol — "
    "`deploy-prod.yml` dispatch z main hodil `startup_failure` (rozbil to infra "
    "PR odoo-erp#7087), treba fast-track marker aby prešiel hotfix ref.\n\n"
    "• A (odporúčam) — vydaj marker\n• B — počkaj na 2.289\n\n"
    "❓ NEEDS YOU: vydáš fast-track marker pre release 2.288?"
)

# A ❓ ASKED naming a same-repo infra-labelled ticket (#6883), no shape keywords.
ASKED_INFRA_TICKET = (
    "**Otázka — projekt airuleset (fleet):** v tickete #6883 treba rozhodnúť "
    "ďalší krok.\n\n• A (odporúčam) — X\n• B — Y\n\n"
    "❓ ASKED: rozhodni A alebo B pre #6883?"
)

# A plain owner question on a NON-infra ticket (#500), no shape keywords.
PLAIN_OWNER_Q = (
    "**Otázka — projekt airuleset (fleet):** v tickete #500 je otvorená otázka "
    "na teba.\n\n• A (odporúčam) — X\n• B — Y\n\n"
    "❓ ASKED: rozhodni A alebo B pre #500?"
)


class TestInfraRoutingDecision(TestCase):
    # (a) infra-LABEL trigger — a named same-repo ticket carrying `infra`.
    def test_infra_labelled_ticket_blocks(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="infra"):
            block, reason = qs.decide(_payload(ASKED_INFRA_TICKET),
                                      u_count_fn=lambda c: 0)
        self.assertTrue(block)
        self.assertIn("GATEKEEPER-ACTION", reason)
        self.assertIn("infra", reason.lower())

    # (b) TEXT-shape trigger — release-block shape, no same-repo infra ticket,
    #     ZERO gh (the membership fn must never be consulted).
    def test_release_block_text_blocks_without_gh(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            side_effect=AssertionError("no gh on the text path")) as p:
            block, reason = qs.decide(_payload(RELEASE_BLOCK_TEXT),
                                      u_count_fn=lambda c: 0)
        self.assertTrue(block)
        self.assertIn("GATEKEEPER-ACTION", reason)
        p.assert_not_called()

    def test_release_block_text_blocks_regardless_of_u(self):
        # Infra routing is U-independent on the text path: even with U>0 (owner
        # has other visible questions) an infra-caused block is not an owner Q.
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            side_effect=AssertionError("no gh on the text path")):
            block, _ = qs.decide(_payload(RELEASE_BLOCK_TEXT),
                                 u_count_fn=lambda c: 5)
        self.assertTrue(block)

    def test_hotfix_main_keyword_blocks(self):
        msg = ("**Otázka — projekt odoo-erp:** treba spustiť z "
               "`gatekeeper/hotfix-main-2288` refu.\n\n"
               "❓ NEEDS YOU: schváliš hotfix-main dispatch?")
        block, _ = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertTrue(block)

    def test_release_fasttrack_exception_keyword_blocks(self):
        msg = ("**Otázka — projekt odoo-erp:** hook chce "
               "`release-fasttrack-exception.json`.\n\n"
               "❓ NEEDS YOU: vydáš exception?")
        block, _ = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertTrue(block)

    # (c) plain owner question on a non-infra ticket → allow, BYTE-IDENTICAL to
    #     today: the #1025 membership verdict decides, nothing new fires.
    def test_plain_non_infra_ticket_in_u_allows(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="in_u"):
            block, reason = qs.decide(_payload(PLAIN_OWNER_Q),
                                      u_count_fn=lambda c: 0)
        self.assertFalse(block)
        self.assertEqual(reason, "")

    def test_plain_non_infra_ticket_not_in_u_still_1025_blocks(self):
        # A non-infra ticket absent from an empty U still blocks with the #1025
        # reason (not the infra reason) — the two triggers do not cross-wire.
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="not_in_u"):
            block, reason = qs.decide(_payload(PLAIN_OWNER_Q),
                                      u_count_fn=lambda c: 0)
        self.assertTrue(block)
        self.assertNotIn("GATEKEEPER-ACTION", reason)
        self.assertIn("500", reason)

    # (d) gh error / unmeasurable → allow (fail-open).
    def test_gh_error_unmeasurable_allows(self):
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="unmeasurable"):
            block, reason = qs.decide(_payload(ASKED_INFRA_TICKET),
                                      u_count_fn=lambda c: 0)
        self.assertFalse(block)

    def test_non_question_with_shape_keyword_allows(self):
        # A ✅ DONE status turn that merely mentions deploy-prod is NOT a
        # question → never blocked.
        msg = "Nasadil som deploy-prod.yml, startup_failure vyriešený.\n✅ DONE"
        block, _ = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertFalse(block)

    # #1026 review 🟡1 — a BARE non-infra fast-track decision stays the owner's
    # (Item 1). The text-shape trigger must NOT fire on `fast-track` alone.
    def test_bare_non_infra_fasttrack_reaches_owner(self):
        msg = ("**Otázka — projekt airuleset (fleet):** feature v #500 je hotová.\n\n"
               "• A (odporúčam) — fast-track-ni ju do najbližšieho release\n"
               "• B — počkaj na normálny cyklus\n\n"
               "❓ NEEDS YOU: mám fast-track-núť #500 do najbližšieho release?")
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="in_u"):
            block, reason = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertFalse(block)          # owner decides a non-infra fast-track
        self.assertEqual(reason, "")

    def test_fasttrack_with_infra_word_blocks(self):
        # fast-track WITH an infra cause named → infra lane.
        msg = ("**Otázka — projekt odoo-erp:** kvôli infra problému release "
               "neprešiel.\n\n❓ NEEDS YOU: vydáš fast-track marker?")
        block, _ = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertTrue(block)

    def test_deploy_production_does_not_match_deploy_prod(self):
        # #1026 review 🔵2 — `\b` anchor: "deploy-production" must NOT trigger.
        msg = ("**Otázka — projekt airuleset:** nový deploy-production config v "
               "#500.\n\n❓ ASKED: schváliš zmenu?")
        with m.patch.object(cli_quals, "question_ticket_in_u",
                            return_value="in_u"):
            block, _ = qs.decide(_payload(msg), u_count_fn=lambda c: 0)
        self.assertFalse(block)


class TestReleaseBlockShapeHelper(TestCase):
    """`qs._is_release_block_shape` — the two-tier text trigger (#1026 🟡1/🔵2)."""

    def test_strong_token_alone_fires(self):
        for t in ("deploy-prod.yml failed", "startup_failure on main",
                  "gatekeeper/hotfix-main-2288", "release-fasttrack-exception.json"):
            self.assertTrue(qs._is_release_block_shape(t), t)

    def test_bare_fasttrack_alone_does_not_fire(self):
        for t in ("mám fast-track-núť tento feature?", "fasttrack this PR?",
                  "fast track the release of feature X"):
            self.assertFalse(qs._is_release_block_shape(t), t)

    def test_fasttrack_with_infra_cause_fires(self):
        self.assertTrue(qs._is_release_block_shape("infra blok — treba fast-track marker"))
        self.assertTrue(qs._is_release_block_shape("fast-track lebo deploy-prod padol"))

    def test_anchors_reject_substring_overmatch(self):
        for t in ("deploy-production rollout", "breakfast tracking app",
                  "steadfast tracker widget"):
            self.assertFalse(qs._is_release_block_shape(t), t)


class TestQuestionTicketInfraVerdict(TestCase):
    """cli_quals.question_ticket_in_u gains an `infra` verdict from the SAME
    single gh call (label:infra folded into the #1025 search)."""

    def _runner(self, items):
        def r(argv, cwd):
            return json.dumps(items)
        return r

    def test_named_ref_with_infra_label_is_infra(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(set(), time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo",
                runner=self._runner([{"number": 6883,
                                      "labels": [{"name": "infra"}]}]),
                now=time.time())
        self.assertEqual(v, "infra")

    def test_infra_wins_over_user_waiting_label(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(set(), time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo",
                runner=self._runner([{"number": 6883,
                                      "labels": [{"name": "needs-answer"},
                                                 {"name": "infra"}]}]),
                now=time.time())
        self.assertEqual(v, "infra")

    def test_user_waiting_only_still_in_u(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(set(), time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo",
                runner=self._runner([{"number": 6883,
                                      "labels": [{"name": "needs-answer"}]}]),
                now=time.time())
        self.assertEqual(v, "in_u")

    def test_absent_ref_is_not_in_u(self):
        with m.patch.object(statusbar, "user_waiting_numbers",
                            return_value=(set(), time.time())):
            v = cli_quals.question_ticket_in_u(
                [6883], "/repo",
                runner=self._runner([{"number": 500,
                                      "labels": [{"name": "infra"}]}]),
                now=time.time())
        self.assertEqual(v, "not_in_u")


# --------------------------------------------------------------------------- #
# Doctrine content-lock: release-lane-discipline.md rule 5 (window-teeth,
# #799/#500 style — an operative bullet bounded by its anchor and the next
# bullet marker, tokens asserted inside the window so a partial revert breaks).
# --------------------------------------------------------------------------- #
MODULE = REPO / "modules" / "core" / "release-lane-discipline.md"


def _norm(text):
    return " ".join(text.split())


def _rule5_window(text):
    """The rule-5 bullet: from its numbered anchor up to the trailing
    'Applies to all rewordings' closer (rule 5 is the last rule)."""
    normed = _norm(text)
    idx = normed.find("5.")
    # find the LAST '5.' that starts a bold rule (avoid an incidental '5.' in
    # earlier prose): search for '5. **'
    idx = normed.find("5. **")
    if idx < 0:
        return ""
    rest = normed[idx:]
    end = rest.find("Applies to all rewordings")
    return rest[:end] if end >= 0 else rest


# --------------------------------------------------------------------------- #
# hooks/stop-check-question-quality.sh wiring: the widened pre-check must run the
# subprocess on a release-block SHAPE even with NO `#N`, so the text-shape
# trigger fires; a plain no-ticket question still passes. (Membership needs no
# gh here — the text path blocks before any ref/U check.)
# --------------------------------------------------------------------------- #
HOOK = REPO / "hooks" / "stop-check-question-quality.sh"


class TestHookTextShapeWiring(TestCase):
    def _run(self, msg, home, cwd):
        import os
        sid = "qs1026-" + uuid.uuid4().hex[:10]
        for stem in ("airuleset-question-quality-block-", "claude-user-active-",
                     "claude-discord-lastq-", "claude-lastq-refs-"):
            self.addCleanup(lambda p="/tmp/" + stem + sid: Path(p).unlink(missing_ok=True))
        env = dict(os.environ)
        env["HOME"] = home
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg,
                              "session_id": sid, "cwd": cwd}),
            capture_output=True, text=True, timeout=40, env=env)

    def test_release_block_text_no_ref_blocks_exit_2(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            cwd = str(Path(home) / "repo")
            Path(cwd).mkdir(parents=True, exist_ok=True)
            r = self._run(RELEASE_BLOCK_TEXT, home, cwd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("GATEKEEPER-ACTION", r.stderr)

    def test_plain_no_ticket_question_exits_0(self):
        import tempfile
        msg = ("**Otázka — projekt airuleset (fleet):** všeobecná otázka bez "
               "ticketu.\n\n• A (odporúčam) — X\n• B — Y\n\n"
               "❓ ASKED: schváliš zmenu farby dashboardu?")
        with tempfile.TemporaryDirectory() as home:
            cwd = str(Path(home) / "repo")
            Path(cwd).mkdir(parents=True, exist_ok=True)
            r = self._run(msg, home, cwd)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestRule5DoctrineLock(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = MODULE.read_text(encoding="utf-8")
        cls.win = _rule5_window(cls.raw)

    def test_rule5_present(self):
        self.assertTrue(self.win, "rule 5 bullet ('5. **…') not found")

    def test_infra_caused_anchor(self):
        self.assertRegex(self.win.lower(), r"infra-caused|infra-vyvolan")

    def test_owner_informed_never_asked_negation(self):
        # The operative negation, as ONE ordered phrase (#1026 review 🔵3 — a
        # swap-inversion "asked, never informed" breaks the contiguous match,
        # so the lock has teeth on the negation itself, not just the nouns).
        low = self.win.lower()
        self.assertIn("informed (✅/⏳), never asked", low)

    def test_gatekeeper_action_hub_routing(self):
        self.assertIn("GATEKEEPER-ACTION", self.win)

    def test_rider_reference(self):
        self.assertIn("#1029", self.win)

    def test_ticket_cited(self):
        self.assertIn("#1026", self.raw)


if __name__ == "__main__":
    main()
