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
flattened before matching). Four families:

  1. install(+confirm/works): nainštaluj(eš)/inštaluj(eš) near a
     confirm-verb (potvrď/potvrdíš/povedz/povieš/napíš/napíšeš/
     daj vedieť/daš vedieť/over(íš)) — OR the confirm-verb alone near an
     outcome word (funguje/ide), with no install context required (this
     is what catches the incident's own literal quote "over či ti to
     ide", which has no install verb in it at all).
  2. try-on-device: vyskúšaj/vyskúšaš/otestuj/otestuješ near
     telefón/mobil/zariaden-.
  3. write-what-you-see: napíš/napíšeš/povedz/povieš near "čo vidíš".
  4. when-you-verify-continue: over/overíš/vyskúšaj/vyskúšaš near a
     1st-person continue-verb (pokračuj-/spravím/urobím).

Escape is the SAME `UNVERIFIED:` marker the English branch already uses,
reused verbatim (never duplicated).
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
    """Each of the 4 word-family shapes named in #424, using the exact
    2nd-person imperative/future conjugations the ticket names
    (nainštaluješ, vyskúšaš, overíš, potvrdíš, daš vedieť)."""

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

    def test_confirm_near_works_without_install_context_blocked(self):
        # The incident's OWN literal quote shape — no install verb present
        # at all, just a bare "check whether it works for you".
        self.assertTrue(_run_stop_prose(
            "Over či ti to ide na tvojom zariadení."))

    def test_daj_vediet_confirm_variant_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Nainštaluj si build a daj vedieť, či funguje."))

    def test_das_vediet_future_confirm_variant_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Nainštaluješ si appku a daš mi vedieť, či ide?"))

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

    def test_bare_over_ci_imperative_blocked(self):
        self.assertTrue(_run_stop_prose(
            "Over, či to na tvojom telefóne funguje."))


class TestSlovakTesterHandoffFalsePositiveControls(TestCase):
    """The exact false-positive controls #424 names, plus a few this
    worker added while designing the regex boundaries."""

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


if __name__ == "__main__":
    main()
