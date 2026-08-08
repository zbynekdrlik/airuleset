"""#316 — `stop-check-prose-violations.sh`'s spec/plan-approval-pause detector
was English-only, so every question this repo ships to a real user (always
Slovak, per `user-questions-slovak.md`) sailed through it untouched. Live
incident: montalu2@subdev sent `❓ NEEDS YOU: schvaľuješ zapísaný design
spec?` — the exact "spec/plan/design review handoff" pre-answered class,
English-blocked, Slovak-invisible — and the user had to answer it himself
(odoo-erp#3265, 2026-08-08).

The fix is a NARROW, structurally-anchored Slovak detector: an approval
verb (schvaľuješ/schváliš/odsúhlasíš/potvrdíš/odobríš) attached to a
design-artifact noun (spec/plán/návrh/dizajn), found ON THE ❓ marker line
itself, gated on the absence of `•`/numbered option lines and on the user
NOT being present — `stop-check-question-quality.sh`'s own Check 4
mandates option formatting on a genuine design fork ONLY for an away
user, so a well-formed alternative-choice question is exempt by
construction even when it happens to use the SAME verb.

#316-review (adversarial): the first cut scanned the WHOLE message for
verb+noun (an unrelated approval mention anywhere + an unrelated real ❓
question elsewhere false-blocked), missed the numbered-list option form
Check 4 itself accepts, had no presence gate even though its own safety
argument rests on one, read raw MSG for the bullet exemption (a fenced
code block's `- old line` granted it), and had a tautological "no
marker" test (the fixture's verb form never matched the regex at all).
All five are fixed here and locked with a dedicated test each.
"""

import json
import os
import subprocess
import sys
import time
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

# Same genuine fork, NUMBERED options instead of bullets (Check 4's own
# OTHER accepted option shape, and the delivery layer's own rendering
# form per user-questions-slovak.md -- "1."/"2.").
NUMBERED_FORK_MSG = (
    "**Otázka — projekt montalu (peňažný denník):** "
    "Mám dva rôzne návrhy architektúry pre tento modul.\n\n"
    "1. Návrh A (odporúčam) — jednoduchší\n"
    "2. Návrh B — komplexnejší\n\n"
    "❓ NEEDS YOU: ktorý návrh schvaľuješ na implementáciu?"
)

# The verb+noun combo genuinely present (matches SK_APPROVAL_RX and
# SK_ARTIFACT_RX), but no ❓ marker at all -- ordinary prose mentioning
# approval in passing, not a real question turn.
NO_MARKER_MSG = (
    "Mimochodom, CEO uz predtym potvrdil, ze schvaľuješ podobny "
    "navrh zmeny procesu."
)


def _run(msg, sid=None):
    sid = sid or ("prosegate316-%s" % uuid.uuid4().hex[:10])
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

    def test_specifikaciu_with_diacritic_s_blocks(self):
        """#316-review: SK_ARTIFACT_RX's ASCII-only 'spec...' spelling
        missed the native Slovak 'š' AND the accusative case ending."""
        msg = "❓ NEEDS YOU: schvaľuješ zapísanú špecifikáciu?"
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

    def test_genuine_fork_with_numbered_options_is_never_blocked(self):
        """#316-review CRITICAL: the exemption used to only recognise
        `•`/`-` bullets, not the `1.`/`2.` numbered form
        stop-check-question-quality.sh's own Check 4 explicitly accepts
        (and the delivery layer itself renders options this way,
        user-questions-slovak.md) -- a compliant numbered fork was
        hard-blocked. Must pass clean now."""
        p = _run(NUMBERED_FORK_MSG)
        self.assertFalse(
            _blocked(p),
            "a genuine numbered-option design fork was wrongly blocked. "
            "stdout=%r" % p.stdout[:300])

    def test_approval_wording_with_no_question_marker_is_not_touched(self):
        """Ordinary prose mentioning approval in passing (no ❓ marker at
        all) is not a question turn -- must not be gated. The fixture's
        verb genuinely matches SK_APPROVAL_RX (#316-review: the original
        fixture used a non-matching verb form, making this assertion
        tautologically true regardless of the marker gate)."""
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

    def test_unrelated_approval_mention_plus_unrelated_real_question_is_not_blocked(self):
        """#316-review CRITICAL: the first cut scanned the WHOLE message
        for verb+noun, so an aside recalling a past preference
        ("obvykle schvaľuješ navrh...") combined with a genuine,
        UNRELATED real ❓ question elsewhere false-blocked it. The verb
        detector is scoped to the ❓ marker's OWN line now."""
        msg = (
            "Mimochodom, uz skor si povedal, ze obvykle schvaľuješ "
            "navrh len vtedy, ked ma testy.\n\n"
            "❓ NEEDS YOU: nasadit databazu dnes vecer alebo rano?"
        )
        self.assertFalse(
            _blocked(_run(msg)),
            "an unrelated approval-verb aside plus an unrelated real "
            "question was wrongly blocked")

    def test_negated_approval_is_not_blocked(self):
        """#316-review: Slovak negates a verb by gluing 'ne' directly onto
        it, with no space -- 'neschvaľuješ' contains the substring
        'schvaľuješ', so an unanchored regex would match a statement
        that explicitly does NOT approve. Word-boundary anchoring fixes
        this: there is no \\b transition inside 'neschvaľuješ'."""
        msg = "❓ NEEDS YOU: Ešte to neschvaľuješ ten návrh?"
        self.assertFalse(
            _blocked(_run(msg)),
            "a negated ('ne'-prefixed) approval verb was wrongly blocked")

    def test_noun_substring_inside_a_longer_word_does_not_match(self):
        """#316-review: 'n[áa]vrh' without boundary anchoring matched
        inside 'navrhujem'/'navrhol' (unrelated verb forms), and
        'design' inside 'designera'. Word-boundary anchoring on the
        artifact pattern closes this."""
        msg = "❓ NEEDS YOU: navrhujem fakturáciu pre designera, súhlasíš s prístupom?"
        self.assertFalse(
            _blocked(_run(msg)),
            "a noun matching only as a substring of a longer word was "
            "wrongly blocked")

    def test_present_user_is_never_gated_by_this_check(self):
        """#316-review IMPORTANT: the exemption's whole safety argument
        rests on stop-check-question-quality.sh's Check 4 -- but Check 4
        disables ALL shape enforcement for a user who is PRESENT
        (the camera-box "Hruza" incident, 2026-07-05). This check had no
        such gate, so the safety argument was false for a present user.
        Mirrors the SAME /tmp/claude-user-active-<sid> marker."""
        sid = "prosegate316-present-%s" % uuid.uuid4().hex[:8]
        active_marker = Path("/tmp/claude-user-active-%s" % sid)
        active_marker.write_text("")
        try:
            # A genuinely-matching, bare (no options) montalu2-shaped
            # fixture -- would block for an AWAY user (see
            # test_montalu2_fixture_is_blocked) but must NOT for a
            # present one.
            msg = "❓ NEEDS YOU: schvaľuješ zapísaný návrh?"
            self.assertFalse(
                _blocked(_run(msg, sid=sid)),
                "a present user's bare approval question was wrongly "
                "blocked -- Check 4 itself would not gate them either")
        finally:
            active_marker.unlink(missing_ok=True)

    def test_a_stale_presence_marker_does_not_exempt_an_away_user(self):
        """The 600s freshness window (mirrors Check 4's own) -- a marker
        older than that means the user is genuinely away again, and the
        check must fire."""
        sid = "prosegate316-stale-%s" % uuid.uuid4().hex[:8]
        active_marker = Path("/tmp/claude-user-active-%s" % sid)
        active_marker.write_text("")
        old = time.time() - 700
        os.utime(active_marker, (old, old))
        try:
            msg = "❓ NEEDS YOU: schvaľuješ zapísaný návrh?"
            self.assertTrue(_blocked(_run(msg, sid=sid)))
        finally:
            active_marker.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
