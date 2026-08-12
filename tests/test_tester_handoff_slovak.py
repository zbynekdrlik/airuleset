"""#424: the tester-handoff family in `hooks/stop-check-prose-violations.sh`
(`TestTesterHandoffHook` in test_airuleset.py, autonomous-verification.md)
was ENGLISH-ONLY — the SAME recurrence pattern #316 (EN-only coverage
audit) and #319 (Slovak detectors for 3 other pre-answered-table families:
dispatch-now-or-hold, admin-merge, merge-despite) already found and fixed,
just never applied to this family. Live incident: montalu3 (odoo-erp
sub-dev stream) was made to hand-install an APK on their OWN phone 10x over
2 hours, with an agreed emulator sitting unused the whole time — the agent
later admitted it never needed the human at all
("...vlastne mna vobec nepotreboval..."). Because montalu3's every
message is Slovak (user-questions-slovak.md), the English-only regex never
saw the request at all.

STEP 0 (posted to the ticket): fed 4 real montalu3-shaped Slovak sentences
through the CURRENT, un-patched hook — all 4 passed through unblocked.

Fix: a Slovak word-family extension to the SAME hook (FREEZE — no new hook
file), mirroring #319's own established pattern shape verbatim (bounded
`.{0,N}` windows, `\\b` anchors on diacritic alternation inside bracket
classes, LC_ALL=C.UTF-8 forced on the one grep call that needs it, newlines
flattened before matching). Shapes:

  1. install(+confirm/works): nainštaluj(eš)/inštaluj(eš) near a
     confirm-verb (potvrď/potvrdíš/povedz/povieš/napíš/napíšeš/
     daj/dáš (mi) vedieť/overíš) — OR the confirm-verb alone near an
     outcome word (funguje / "či (ti) to ide").
  1b. over-či: "over(,)? (si)? či ..." standalone (no WORKS word needed) —
     catches the incident's own literal quote "over či ti to ide".
  2. try-on-device: vyskúšaj/vyskúšaš/otestuj/otestuješ near
     telefón/mobil/zariaden-.
  2b. modal-request: môžeš/vieš near vyskúšať/otestovať/nainštalovať/
     inštalovať — the Slovak counterpart of the English hook's own
     "(can|could|would) you...test" shape.
  3. write-what-you-see: napíš/napíšeš/povedz/povieš near "čo vidíš".
  4. when-you-verify-continue: overíš/vyskúšaj/vyskúšaš near a
     1st-person continue-verb (pokračuj-/spravím/urobím).

Escape is the SAME `UNVERIFIED:` marker the English branch already uses,
reused verbatim (never duplicated).

#424-review (fresh-context adversarial, model: fable, gate OPEN) round 1
found 2 CRITICAL + 2 MAJOR, all fixed in the SAME branch before this
docstring was written:

  - C1: the FIRST cut let BARE "over" and 3rd-person "overí" stand in for
    both CONFIRM and VERIFY — live false positives against "Coverage je
    over 90 %", "Migrated over 40 files" (pure English text blocked by
    the SLOVAK gate), and — worst — the repo's OWN mandated design-
    comment phrasing "test overí, že X funguje". Fixed: only the
    unambiguous 2nd-person-future "overíš" survives in CONFIRM/VERIFY;
    bare "over"/"overí" alone are refused. The incident's own "over či"
    shape moved to its own standalone trigger (1b) instead.
  - C2: bare `\bide\b` (case-insensitive) matched the "IDE" acronym and
    the unrelated "ide o X" idiom ("this concerns/is about X" — which
    the MANDATED Slovak question template routinely uses: "povedz mi,
    či ide o produkčnú databázu"). Fixed: "ide" as a WORKS word now
    requires the fixed phrase "či (ti)? to ide" (the "to" is mandatory,
    which "ide o X" never has), and "či" is spelled WITHOUT its usual
    ASCII-fallback alternation (`či`, never `[čc]i`) because the bare-c
    form collides with this repo's own "CI" acronym under
    case-insensitive matching.
  - M1: the real 2nd-person-future "dáš (mi) vedieť" (long á) was
    missing — only the non-standard ASCII hybrid "daš" matched, so the
    incident's own most natural confirm idiom escaped. Fixed: daj/dáš/
    daš all accepted.
  - M2: no modal+infinitive shape existed at all ("Môžeš to vyskúšať na
    telefóne?") — the direct Slovak counterpart of the English hook's
    OWN primary trigger shape, and its absence materially defeated the
    ticket's real-world purpose. Added as shape 2b.

Row 15 of `test_ask_before_assuming_dropped_rows.py`'s
`REVERTED_SLOVAK_PHRASES` ("can you test it on your end") was REMOVED —
mirroring #319's own removal of rows 7-9 — because every natural Slovak
rendering of that intent is now genuinely covered here.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import airuleset  # noqa: E402
from _hook_state_cleanup import sweep_session_files  # noqa: E402

HOOK = airuleset.REPO_DIR / "hooks" / "stop-check-prose-violations.sh"


def _run_stop_prose(text, env=None):
    """Stop hook — {"decision":"block"} on stdout means the message is
    hard-blocked. Fresh session id per call (the hook's own retry cap is
    per-session), swept immediately after (#202 leftover-counter class)."""
    sid = f"test-424-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"session_id": sid, "last_assistant_message": text})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        env=env,
    )
    sweep_session_files(sid)
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestSlovakTesterHandoffTruePositives(TestCase):
    """Each of the shapes named in #424, using the exact 2nd-person
    imperative/future conjugations the ticket names (nainštaluješ,
    vyskúšaš, overíš, potvrdíš, dáš vedieť), plus the two shapes the
    #424-review round added (standalone over-či, modal-request)."""

    def test_install_then_confirm_works_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Nainštaluj si prosím APK na svojom telefóne a potvrď, "
            "či to funguje."))

    def test_install_future_form_blocked(self):
        # #424: "2nd person sg imperative + 2nd person future" — the
        # future/present conjugation (nainštaluješ), not just the bare
        # imperative.
        self.assertTrue(_run_stop_prose(
            "Nainštaluješ si APK na telefóne a napíšeš mi, či to funguje?"))

    def test_over_ci_standalone_no_works_word_needed_blocked(self):
        # The incident's OWN literal quote shape — no install verb AND no
        # unambiguous outcome word ("ide" alone was removed in the
        # #424-review round, see C2) — "over(,)? (si)? či" alone is the
        # trigger (shape 1b).
        self.assertTrue(_run_stop_prose(
            "Over či ti to ide na tvojom zariadení."))

    def test_over_ci_ticket_own_fixture_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Over, či to na tvojom telefóne funguje."))

    def test_confirm_ci_to_ide_variant_blocked(self):
        # The "to ide" (mandatory "to") outcome-word variant, without
        # "over" — a confirm-verb near "či to ide" (the shape the hook's
        # own SK_TH_WORKS_RX requires: "to" immediately before "ide").
        self.assertTrue(_run_stop_prose(
            "Napíš mi, či to ide na telefóne."))

    def test_daj_vediet_confirm_variant_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Nainštaluj si build a daj vedieť, či funguje."))

    def test_das_vediet_future_confirm_variant_ascii_blocked(self):
        # The ASCII-degraded hybrid form ("daš", missing the long á) —
        # kept as a typo-tolerance regression lock alongside the real
        # form below.
        self.assertTrue(_run_stop_prose(
            "Nainštaluješ si appku a daš mi vedieť, či ide?"))

    def test_das_vediet_real_diacritic_form_blocked(self):
        # #424-review MAJOR M1: the REAL Slovak 2nd-person future (long
        # á) — this is the incident's own most natural confirm idiom, and
        # it was missing entirely before the review-fix round.
        self.assertTrue(_run_stop_prose(
            "Nainštaluj si novú verziu APK a dáš mi vedieť, "
            "či všetko funguje."))
        self.assertTrue(_run_stop_prose("Dáš mi vedieť, či to funguje?"))

    def test_try_on_phone_imperative_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Vyskúšaj to na svojom telefóne a povedz mi, či to ide."))

    def test_try_on_phone_future_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Vyskúšaš to na svojom telefóne, prosím?"))

    def test_otestuj_on_mobile_blocked(self):
        self.assertTrue(_run_stop_prose("Otestuj appku na svojom mobile."))

    def test_otestujes_on_device_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Otestuješ appku na svojom zariadení?"))

    def test_modal_request_can_you_test_it_blocked(self):
        # #424-review MAJOR M2: the modal+infinitive polite-request form
        # — the direct Slovak counterpart of the English hook's own
        # primary "(can|could|would) you...test" shape. Also the direct
        # positive-coverage proof for row 15 of
        # test_ask_before_assuming_dropped_rows.py's REVERTED_SLOVAK_
        # PHRASES ("can you test it on your end"), which was REMOVED from
        # that list because of this coverage.
        self.assertTrue(_run_stop_prose(
            "Môžeš to vyskúšať na svojom telefóne?"))

    def test_modal_request_vies_otestovat_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Vieš mi to otestovať na mobile?"))

    def test_write_what_you_see_imperative_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Napíš mi, čo vidíš na obrazovke po spustení."))

    def test_write_what_you_see_future_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Napíšeš mi, čo vidíš na obrazovke?"))

    def test_say_what_you_see_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Povedz mi, čo vidíš po otvorení appky."))

    def test_when_you_verify_continue_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Keď to overíš na telefóne, pokračujem ďalej."))

    def test_when_you_try_i_will_do_it_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Keď to vyskúšaš, spravím ďalší krok."))


class TestSlovakTesterHandoffFalsePositiveControls(TestCase):
    """The exact false-positive controls #424 names, plus every CRITICAL/
    MAJOR finding the #424-review round demonstrated as a real, live
    false positive before it was fixed."""

    def test_agent_self_report_past_tense_not_blocked(self):
        # #424's own named control: the agent testing on ITS OWN emulator,
        # reported in 1st person PAST tense — must never block. The
        # imperative/future stems (nainštaluj-, otestuj-) are
        # grammatically distinct from the past-tense forms
        # (nainštalova-l, otestova-l), not just a different suffix on the
        # same stem.
        self.assertFalse(_run_stop_prose(
            "Nainštaloval som APK na emulátore a otestoval, "
            "appka funguje správne."))

    def test_apk_download_report_line_not_blocked(self):
        # #424's own named control: a completion-report 📱 artifact line.
        self.assertFalse(_run_stop_prose(
            "\U0001F4F1 APK: https://example.com/build/app-release.apk"))

    def test_unverified_escape_disarms_it(self):
        # #424's own named control: a genuine trigger phrase, followed by
        # a real UNVERIFIED: line — must not block, same escape the
        # English branch already uses.
        self.assertFalse(_run_stop_prose(
            "Vyskúšaj to na svojom telefóne a napíš mi, čo vidíš. "
            "UNVERIFIED: nemám prístup k fyzickému zariadeniu, "
            "user must test manually."))

    def test_completion_report_third_person_past_not_blocked(self):
        # "Tests confirmed the build works" — 3rd-person-plural past
        # tense ("potvrdili"), not an imperative/future demand.
        self.assertFalse(_run_stop_prose(
            "Testy potvrdili, že build funguje. CI je zelené."))

    def test_agent_offering_to_write_1st_person_not_blocked(self):
        # "I'll write an update to the ticket" — 1st-person future
        # ("napíšem"), the agent's own offer, not a 2nd-person demand.
        self.assertFalse(_run_stop_prose(
            "Napíšem update do ticketu, keď to dokončím."))

    def test_unrelated_hardware_timing_question_not_blocked(self):
        # Shares "hneď"/"alebo"/"počkať" tokens the SIBLING dispatch-now-
        # or-hold detector keys on, but no tester-handoff verb at all.
        self.assertFalse(_run_stop_prose(
            "Mám hneď reštartovať OBS, alebo počkať do konca prenosu?"))

    def test_deploy_status_despite_not_blocked(self):
        # Shares "napriek" with the SIBLING merge-despite detector, but no
        # tester-handoff verb nearby.
        self.assertFalse(_run_stop_prose(
            "Nasadenie prebehlo úspešne napriek tomu, že testy boli "
            "pomalé."))

    def test_verified_past_tense_not_blocked(self):
        # "Overil som, že build funguje" — 1st-person PAST of "overiť"
        # (verify), the agent's own completed check, not a 2nd-person
        # imperative/future demand.
        self.assertFalse(_run_stop_prose(
            "Overil som, že build na emulátore funguje."))

    def test_confirmation_noun_not_blocked(self):
        # "potvrdenie" (confirmation, a noun) must not be mistaken for
        # the imperative "potvrď".
        self.assertFalse(_run_stop_prose(
            "Čakám na potvrdenie, že deploy prebehol."))

    # --- #424-review CRITICAL C1: bare "over" / 3rd-person "overí" ---

    def test_third_person_overi_describing_own_test_not_blocked(self):
        # THE repo's own mandated design-comment phrasing ("test overí,
        # že X funguje") — must never block. 3rd person, not 2nd.
        self.assertFalse(_run_stop_prose(
            "Pridal som regresný test, ktorý overí, že endpoint po "
            "deployi funguje."))
        self.assertFalse(_run_stop_prose(
            "Test test_deploy overí, že healthcheck funguje — "
            "pridaný v commite abc123."))
        self.assertFalse(_run_stop_prose(
            "CI job overí, či build ide zostaviť aj na ARM."))
        self.assertFalse(_run_stop_prose(
            "Watchdog každú minútu overí, či služba beží, a "
            "pokračuje ďalším jobom."))

    def test_bare_english_over_word_not_blocked(self):
        # Bare "over" is also an ordinary English word ("over 90%",
        # "migrated over 40 files") — must never block, incl. when the
        # rest of the sentence is pure English (blocked by a SLOVAK gate
        # would be a genuinely confusing false positive).
        self.assertFalse(_run_stop_prose(
            "Coverage je over 90 %, pokračujem ďalším ticketom."))
        self.assertFalse(_run_stop_prose(
            "Zvýšil som limit over 100 MB a upload teraz funguje."))
        self.assertFalse(_run_stop_prose(
            "Migrated over 40 files; IDE integration is unchanged."))
        self.assertFalse(_run_stop_prose(
            "Prešiel som over 200 riadkov konfigurácie IDE a je to "
            "čisté."))

    def test_playbook_step_bare_over_not_a_ci_idiom_not_blocked(self):
        # Bare "over X" with no "či" following it — a routine playbook
        # step ("1. over verziu, 2. spusti testy...") — must not block.
        self.assertFalse(_run_stop_prose(
            "Plán: 1. over verziu, 2. spusti testy, "
            "3. pokračuj deployom."))

    # --- #424-review CRITICAL C2: bare `\bide\b` / "ide o X" idiom ---

    def test_mandated_question_template_ide_o_idiom_not_blocked(self):
        # The MANDATED Slovak question template (user-questions-
        # slovak.md) routinely uses "či ide o X" ("whether this concerns
        # X") — a completely unrelated idiom to "does it work". Must
        # never block.
        self.assertFalse(_run_stop_prose(
            "**Otázka — projekt bakerion (mobilná appka):** Povedz mi "
            "prosím, či ide o produkčný Firebase projekt alebo o "
            "testovací. ❓ NEEDS YOU: ktorý Firebase projekt?"))
        self.assertFalse(_run_stop_prose(
            "Napíš mi prosím, či ide o produkčnú databázu."))

    def test_ide_acronym_not_blocked(self):
        # "IDE" (the acronym) matches `\bide\b` case-insensitively unless
        # explicitly guarded against.
        self.assertFalse(_run_stop_prose(
            "Povedz mi, ktoré IDE používaš na Windows stroji."))

    def test_ci_acronym_does_not_collide_with_ci_ascii_fallback_not_blocked(self):
        # #424-review: the usual ASCII-fallback alternation for "či"
        # (č -> c) would make "CI" (continuous integration, this repo's
        # own extremely common acronym) match under case-insensitive
        # grep — deliberately NOT applied to this one word for exactly
        # this reason.
        self.assertFalse(_run_stop_prose(
            "CI teraz ide zelené, pokračujem ďalším ticketom."))

    # --- proximity/window controls ---

    def test_confirm_works_across_unrelated_clause_documented_residual(self):
        # #424-review MINOR (documented accepted residual, NOT fixed —
        # ERE has no real grammatical parsing): a conditional sentence
        # where the confirm-verb and works-word are proximity-adjacent
        # but grammatically unrelated. This test documents the residual
        # rather than asserting a specific direction, so a future change
        # narrowing it further is free to do so without breaking this
        # lock; skipped here deliberately (see the hook's own "Accepted
        # residuals" comment for the full statement).
        pass


class TestSlovakTesterHandoffShape4_NegativeControl(TestCase):
    """#424-review M4: shape 4 (verify-then-continue) had ZERO negative
    controls before this round — a mutation collapsing its CONTINUE_RX to
    "any letter" survived the whole suite untouched. This class exists
    specifically so that mutation can never survive silently again."""

    def test_verify_word_near_unrelated_continuation_not_blocked(self):
        # "overíš" (a genuine VERIFY-shape word) appears, but the
        # following clause is not a 1st-person continue-verb at all —
        # must not block.
        self.assertFalse(_run_stop_prose(
            "Ak to overíš, uvidíme ďalší krok neskôr budúci týždeň."))

    def test_continue_verb_near_unrelated_verify_shaped_word_not_blocked(self):
        # "pokračujem" (a genuine CONTINUE-shape word) appears, but
        # nothing resembling "overíš"/"vyskúšaš" is anywhere near it.
        self.assertFalse(_run_stop_prose(
            "Pokračujem ďalším ticketom, build je zelený a stabilný."))


class TestSlovakTesterHandoffSurvivesABareLocale(TestCase):
    """#316-review's own CRITICAL finding, reproduced again for these new
    detectors: `\\b` immediately adjacent to a diacritic is itself
    locale-dependent under a bare C/POSIX locale. Without the LC_ALL=C.UTF-8
    forcing, the SAME positive fixtures above silently miss under
    LC_ALL=C LANG=C."""

    def _run_bare_locale(self, text):
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        return _run_stop_prose(text, env=env)

    def test_install_confirm_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Nainštaluj si prosím APK na svojom telefóne a potvrď, "
            "či to funguje."),
            "SK tester-handoff install+confirm went inert under a bare "
            "C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or broken")

    def test_try_on_device_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Vyskúšaj to na svojom telefóne a povedz mi, či to ide."),
            "SK tester-handoff try-on-device went inert under a bare "
            "C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or broken")

    def test_write_what_you_see_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Napíš mi, čo vidíš na obrazovke po spustení."),
            "SK tester-handoff write-what-you-see went inert under a "
            "bare C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or "
            "broken")

    def test_verify_then_continue_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Keď to overíš na telefóne, pokračujem ďalej."),
            "SK tester-handoff verify-then-continue went inert under a "
            "bare C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or "
            "broken")

    def test_over_ci_standalone_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Over či ti to ide na tvojom zariadení."),
            "SK tester-handoff over-ci standalone went inert under a "
            "bare C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or "
            "broken")

    def test_modal_request_blocked_under_bare_c_locale(self):
        self.assertTrue(self._run_bare_locale(
            "Môžeš to vyskúšať na svojom telefóne?"),
            "SK tester-handoff modal-request went inert under a bare "
            "C/POSIX locale — LC_ALL=C.UTF-8 forcing is missing or "
            "broken")


if __name__ == "__main__":
    main()
