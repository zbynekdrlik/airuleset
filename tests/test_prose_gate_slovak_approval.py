"""#316 — `stop-check-prose-violations.sh`'s spec/plan-approval-pause detector
was English-only, so every question this repo ships to a real user (always
Slovak, per `user-questions-slovak.md`) sailed through it untouched. Live
incident: montalu2@subdev sent `❓ NEEDS YOU: schvaľuješ zapísaný design
spec?` — the exact "spec/plan/design review handoff" pre-answered class,
English-blocked, Slovak-invisible — and the user had to answer it himself
(odoo-erp#3265, 2026-08-08).

The fix is a NARROW, structurally-anchored Slovak detector: an approval
verb (schvaľuješ/schváliš/odsúhlasíš/potvrdíš/odobríš) attached to a
design-artifact noun (spec/plán/návrh/dizajn), gated on BOTH the ❓ marker
(this repo's own structural anchor for a real question turn) AND the
absence of bullet-option lines — `stop-check-question-quality.sh`'s own
Check 4 mandates bullet options on a genuine design fork, so a well-formed
alternative-choice question is exempt by construction even when it happens
to use the SAME verb.
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

# The montalu2 fixture VERBATIM (the exact phrase quoted in the issue body),
# reconstructed as the real ❓ NEEDS YOU shape it was actually delivered in.
MONTALU2_MSG = (
    "Dokoncil som design spec pre penazny denik podla nasho brainstormu.\n\n"
    "❓ NEEDS YOU: schvaľuješ zapísaný design spec?"
)

# A genuine Slovak design fork: real alternatives, real consequences, the
# MANDATORY bullet-option shape -- and deliberately reuses the SAME
# "schvaľuješ" + "návrh" wording as the banned shape, to prove the bullet
# exemption discriminates on STRUCTURE, not on avoiding the verb.
GENUINE_FORK_MSG = (
    "**Otázka — projekt montalu (peňažný denník):** "
    "Mám dva rôzne návrhy architektúry pre tento modul, "
    "oba fungujú, líšia sa v údržbe a výkone.\n\n"
    "• Návrh A (odporúčam) — jednoduchší, "
    "miernejie pomalší\n"
    "• Návrh B — komplexnejší, rýchlejší\n\n"
    "❓ NEEDS YOU: ktorý návrh schvaľuješ na implementáciu?"
)

# The verb+noun combo present, but no ❓ marker at all -- ordinary prose
# mentioning approval in passing, not a real question turn.
NO_MARKER_MSG = (
    "Mimochodom, CEO uz predtym schvaloval podobny navrh zmeny procesu."
)


def _run(msg):
    sid = "prosegate316-%s" % uuid.uuid4().hex[:10]
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestSlovakApprovalPauseIsBlocked(TestCase):
    """The montalu2 fixture, VERBATIM -- must be blocked."""

    def test_montalu2_fixture_is_blocked(self):
        p = _run(MONTALU2_MSG)
        self.assertTrue(
            _blocked(p),
            "montalu2's exact question ('schvaľuješ zapísaný design "
            "spec?') was NOT blocked. rc=%s stdout=%r stderr=%r"
            % (p.returncode, p.stdout[:300], p.stderr.strip()[-300:]))
        self.assertEqual(p.returncode, 0, "a block must exit 0, not error")

    def test_a_plain_variant_without_the_incident_wording_also_blocks(self):
        msg = (
            "Zapisal som navrh do docs/spec.md.\n\n"
            "❓ NEEDS YOU: odobríš tento návrh?"
        )
        self.assertTrue(_blocked(_run(msg)))

    def test_the_noun_verb_order_reversed_also_blocks(self):
        msg = "❓ NEEDS YOU: návrh potvrdíš?"
        self.assertTrue(_blocked(_run(msg)))


class TestGenuineDesignQuestionsStayWelcome(TestCase):
    """The false-positive guard the ticket explicitly demands."""

    def test_genuine_fork_with_options_is_never_blocked(self):
        """Same verb+noun as the banned shape, but WITH real alternatives
        and bullet options -- must pass clean."""
        p = _run(GENUINE_FORK_MSG)
        self.assertFalse(
            _blocked(p),
            "a genuine design fork with bullet options and real "
            "consequences was wrongly blocked. stdout=%r" % p.stdout[:300])

    def test_approval_wording_with_no_question_marker_is_not_touched(self):
        """Ordinary prose mentioning approval in passing (no ❓ marker at
        all) is not a question turn -- must not be gated."""
        self.assertFalse(_blocked(_run(NO_MARKER_MSG)))

    def test_bullets_anywhere_in_the_message_exempt_it(self):
        """The exemption is message-scoped (mirrors the existing
        box-drawing check's own AND-of-flags shape) -- any real option
        bullets present anywhere disarm the approval-pause check."""
        msg = (
            "• Poznámka: nesúvisiaci zoznam\n"
            "Schvaľuješ tento plán?\n"
            "❓ NEEDS YOU: potvrdenie"
        )
        self.assertFalse(_blocked(_run(msg)))


if __name__ == "__main__":
    main()
