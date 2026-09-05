"""#606 — `stop-check-prose-violations.sh` must block an OWNER-FACING PILE of
per-ticket asks in one turn. U tickets must be delivered STEP-BY-STEP, one
full `**Otázka — projekt …:**` block at a time — never a summary list.

Owner directive (2026-08-21, verbatim): "nikdy nemam dostavat sumarne
informacie u vsetkych U vzdy musis ist step by step". Incident: asked "U 7?",
the session answered with a summary LIST of all 7 U tickets (one line each +
a short ask). The owner cannot decode ticket-by-ticket asks from a compressed
list.

The mechanical backstop is NARROW on purpose (the ticket explicitly warns
against false positives on legitimate STATUS REPORTS): it blocks only a turn
that (a) is an owner-facing QUESTION turn (carries a `❓ NEEDS YOU`/`❓ ASKED`
marker) AND (b) packs 3+ physical lines each carrying a `#N …?` per-ticket
ask. A completion report ends with `✅`/`❓ Question:` (never NEEDS YOU/ASKED)
and its `Closes #N` lines carry no `?`, so it never trips; a single compliant
one-ticket question block has at most one `#N …?` line. The doctrine (the
primary fix) lives in the always-on modules + the autopilot skill.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"
UQS = ROOT / "modules" / "core" / "user-questions-slovak.md"
VOCAB = ROOT / "modules" / "core" / "statusline-vocabulary.md"
# #859 batch 3: the deep U state-machine detail (incl. the #606 step-by-step
# clause this class locks) moved to this companion.
VOCAB_DEEP1 = ROOT / "skills" / "statusline-vocabulary-deep" / "DEEP-1.md"
SKILL = ROOT / "skills" / "autopilot" / "SKILL.md"


def _run(msg, sid=None):
    sid = sid or ("prosegate606-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# The incident VERBATIM in shape: 4 U tickets, one line each, each with its
# own '?' ask, ending with the ❓ NEEDS YOU marker.
INCIDENT_PILE = (
    "Na tvojej strane (U 7) — zhrnutie:\n"
    "- #606 (step-by-step U doručenie) — mám to spraviť hneď alebo počkať?\n"
    "- #608 (prod-kapitulácia gate) — potvrdíš znenie hlášky?\n"
    "- #4619 (kontrolné kópie mailov) — schváliš návrh mechanizmu?\n"
    "- #500 (prod-read doktrína) — chceš to rozšíriť aj na Money?\n\n"
    "❓ NEEDS YOU: odpovedz prosím na tieto štyri."
)
INCIDENT_PILE_ASKED = INCIDENT_PILE.replace(
    "❓ NEEDS YOU:", "❓ ASKED:")

# Exactly 3 separate #N…? ask-lines + a ❓ marker — pins the `-ge 3` boundary
# from below (#606-review MAJOR #5).
THREE_PILE = (
    "Na tvojej strane (U 3):\n"
    "- #606 (U doručenie) — spraviť hneď?\n"
    "- #608 (prod gate) — potvrdíš znenie?\n"
    "- #4619 (mail kópie) — schváliš návrh?\n\n"
    "❓ NEEDS YOU: odpovedz na tieto tri."
)


class USummaryPileIsBlocked(TestCase):
    """The incident pile — an owner-facing question turn with 3+ #N …? asks —
    must be blocked."""

    def test_incident_pile_needs_you_is_blocked(self):
        p = _run(INCIDENT_PILE)
        self.assertTrue(
            _blocked(p),
            "a pile of 4 per-ticket asks in a NEEDS YOU turn must block. "
            "stdout=%r stderr=%r" % (p.stdout[:200], p.stderr.strip()[-400:]))
        self.assertEqual(p.returncode, 0, "a block must exit 0, not error")

    def test_incident_pile_asked_marker_is_blocked(self):
        # The ❓ ASKED marker (ask-and-continue) is equally an owner-facing
        # question turn.
        self.assertTrue(_blocked(_run(INCIDENT_PILE_ASKED)))

    def test_exactly_three_ask_lines_is_blocked(self):
        # Pins the `-ge 3` boundary FROM BELOW: a `-ge 4` mutant would let a
        # 3-line pile through.
        self.assertTrue(
            _blocked(_run(THREE_PILE)),
            "exactly 3 separate #N…? ask-lines in a question turn must block")

    def test_stderr_names_the_step_by_step_remedy(self):
        p = _run(INCIDENT_PILE)
        self.assertIn("step by step", p.stderr.lower())
        self.assertIn("Otázka — projekt", p.stderr)


# --- False-positive controls: these must PASS (never block) --------------- #

SINGLE_BLOCK = (
    "**Otázka — projekt airuleset (governance pravidlá pre Claude fleet):** "
    "V tikete #606 ide o to, aby sa otázky na teba doručovali po jednej ako "
    "celé bloky, nie ako zhrnutý zoznam. Potrebujem od teba jedno "
    "rozhodnutie.\n\n"
    "• Prísne (odporúčam) — blokni aj status list bez otáznikov\n"
    "• Voľné — blokni len keď je otáznik za každým tiketom\n\n"
    "❓ NEEDS YOU: ktorú prísnosť zvolíme?"
)
# A plain STATUS list of tickets (no per-ticket '?', ends with ✅ DONE, not a
# ❓ marker) — the exact shape a legitimate backlog enumeration takes, which
# the ticket explicitly warns must NOT be false-blocked.
STATUS_LIST = (
    "Stav backlogu (hotové dnes):\n"
    "- #606 (U step-by-step doručenie) — zmergnuté\n"
    "- #608 (prod-kapitulácia gate) — zmergnuté\n"
    "- #4619 (kontrolné kópie mailov) — ešte v review\n\n"
    "✅ DONE: dva tickety hotové, tretí v review."
)
NO_QMARKER_REASONING = (
    "Interné uvažovanie: máme riešiť #606 (U doručenie)? A čo #608 (prod "
    "gate)? A ešte #4619 (kópie)? Zatiaľ poďme najprv doriešiť dizajn.\n\n"
    "✅ DONE: rozmyslené."
)
# Only 2 per-ticket ask-lines + a ❓ marker — pins the `-ge 3` threshold from
# ABOVE: a `-ge 2` mutant would wrongly block this (#606-review MAJOR #5).
TWO_PILE = (
    "**Otázka — projekt airuleset:** Dve súvisiace rozhodnutia:\n"
    "- #606 (U doručenie) — spraviť hneď?\n"
    "- #608 (prod gate) — potvrdíš znenie?\n\n"
    "❓ NEEDS YOU: ktoré vezmeme prvé?"
)
# 3 SEPARATE per-ticket ask-lines but NO ❓ NEEDS YOU/ASKED marker (ends
# ✅ DONE) — pins the HAS_QMARKER gate: an `if true` mutant (detector runs on
# every message) would wrongly block this (#606-review MAJOR #6).
THREE_NO_MARKER = (
    "Zvažoval som tri veci:\n"
    "- #606 (U doručenie) — spraviť hneď?\n"
    "- #608 (prod gate) — potvrdiť znenie?\n"
    "- #4619 (mail kópie) — poslať dnes?\n\n"
    "✅ DONE: rozmyslené, idem na to."
)
GENUINE_FORK = (
    "**Otázka — projekt airuleset:** V #606 sa rozhodujeme medzi dvoma "
    "prahmi detekcie, oba fungujú, líšia sa v riziku falošných blokov.\n\n"
    "• Prah 3 (odporúčam) — blokne 3+ ask riadkov\n"
    "• Prah 2 — agresívnejšie, viac falošných blokov\n\n"
    "❓ NEEDS YOU: prah 3 alebo prah 2?"
)


class USummaryFalsePositiveControls(TestCase):
    """Legitimate owner-facing turns must never block."""

    def test_single_compliant_question_block_passes(self):
        self.assertFalse(
            _blocked(_run(SINGLE_BLOCK)),
            "a single compliant one-ticket question block (the CORRECT #606 "
            "delivery shape) must pass")

    def test_status_list_passes(self):
        # A plain status list of 3+ tickets with no per-ticket '?' and ending
        # with ✅ DONE (not NEEDS YOU/ASKED) → never trips.
        self.assertFalse(
            _blocked(_run(STATUS_LIST)),
            "a plain status list enumerating tickets must not trip #606")

    def test_reasoning_without_qmarker_passes(self):
        # #N with '?' but NO ❓ NEEDS YOU/ASKED marker → not an owner-facing
        # question turn → must pass (this fixture crams them on one line, so
        # its per-line count is low; THREE_NO_MARKER below is the one that
        # actually reaches count 3 and pins the marker gate).
        self.assertFalse(
            _blocked(_run(NO_QMARKER_REASONING)),
            "internal reasoning without a ❓ marker must not trip #606")

    def test_two_ask_lines_with_marker_passes(self):
        # Pins the `-ge 3` threshold from ABOVE — a `-ge 2` mutant blocks this.
        self.assertFalse(
            _blocked(_run(TWO_PILE)),
            "only 2 per-ticket ask-lines must not trip #606 (threshold is 3)")

    def test_three_ask_lines_without_marker_passes(self):
        # Pins the HAS_QMARKER gate — 3 separate #N…? lines but NO ❓ marker;
        # an `if true` mutant (no marker gate) would wrongly block this.
        self.assertFalse(
            _blocked(_run(THREE_NO_MARKER)),
            "3 per-ticket ask-lines with NO ❓ marker must not trip #606")

    def test_genuine_two_option_fork_passes(self):
        # A real design fork about ONE ticket, with bullet options and one ❓
        # line — at most one #N …? line → must pass.
        self.assertFalse(
            _blocked(_run(GENUINE_FORK)),
            "a genuine 2-option fork about one ticket must not trip #606")


class USummaryDoctrine(TestCase):
    """#606 doctrine on the three surfaces (unique operative anchors, so a
    partial revert of the clause loses the test's teeth too)."""

    def test_user_questions_slovak_carries_the_step_by_step_rule(self):
        # #859 batch 4b: deep content in SKILL; stub keeps the enforcement pointer
        text = UQS.read_text(encoding="utf-8") + "\n" + (ROOT / "skills" / "user-questions-slovak" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("#606", text)
        self.assertIn("STATUS query", text)
        self.assertIn("step-by-step", text)
        self.assertIn("MACHINE context", text,
                      "the --waiting table must be named machine context")

    def test_statusline_vocabulary_U_bullet_carries_the_rule(self):
        # #859 batch 3: the U bullet's deep #606 clause moved to companion
        text = VOCAB_DEEP1.read_text(encoding="utf-8")
        # find the physical line carrying the #606 step-by-step clause
        # (it is one physical line in the companion).
        u_line = next(
            (ln for ln in text.splitlines()
             if "KROK-ZA-KROKOM" in ln), "")
        self.assertTrue(u_line, "could not locate the U bullet line")
        self.assertIn("#606", u_line,
                      "the U bullet must carry the #606 clause")
        self.assertIn("KROK-ZA-KROKOM", u_line,
                      "the U bullet must state step-by-step delivery")

    def test_autopilot_skill_527_bullet_carries_the_pointer(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("#606", text,
                      "autopilot SKILL.md must reference #606")
        self.assertIn("status query is itself answered by STARTING this "
                      "step-by-step delivery", text,
                      "the #527 bullet must carry the #606 step-by-step "
                      "pointer")


if __name__ == "__main__":
    main()
