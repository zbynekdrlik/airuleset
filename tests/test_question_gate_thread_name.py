"""#650 — `stop-check-question-quality.sh` must BLOCK a ❓ question that carries
CLIENT-POSTING intent (send/approve a client message into an Odoo Discuss
thread, or a closing/handover message) unless it NAMES the exact target thread.

Owner incident (montalu1 stream, 2026-08-24): a schvaľovacia otázka na odoslanie
klientskej Discuss správy do an EXISTING thread named its target only by a
GENERIC description ("výrobné vlákno") — not the exact quoted thread name. The
prose rule (skills/odoo-discuss-xmlrpc/handover-compose.md #632: an approval
question names the exact target thread on its own `Vlákno:` line, quoted name +
stream-number suffix, INCLUDING existing threads) failed AGAIN — owner: "napriek
pokynu … to tu znova nie je!!!". #650 is the prose→hook escalation, in the same
family as the tool-call gate's own #596/#609/#628 escalations.

The gate SURFACE is deliberately `stop-check-question-quality.sh` Check 6, NOT
stop-check-prose-violations.sh:
  * it already extracts EXACTLY the `$BLOCK` delivered to the owner's phone
    (mirrors notify-discord-pending.sh), so it validates the very text the owner
    reads on mobile;
  * it fires ONLY on ❓ question turns, the narrowest possible false-positive
    surface for a heuristic client-posting-intent detector;
  * the away-user autonomous approval ping (a /goal stream) never stamps the
    present-user ACTIVE file, so the inherited present-user (~10 min) bypass does
    NOT apply in the montalu1 scenario.

These are FUNCTIONAL tests (feed the hook a real Stop payload on stdin, read its
{"decision":"block"} verdict) plus content-locks on the Check-6 implementation
and the handover-compose.md hook-enforcement note.
"""

import json
import subprocess
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-question-quality.sh"
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"


class _HookCase(unittest.TestCase):
    """Feed the Stop hook a payload and read its block/pass verdict — the same
    invocation shape as tests/test_question_gate_pipeline_race.py."""

    def _run(self, msg):
        sid = "qthread-%s" % uuid.uuid4().hex[:12]
        for f in (
            "/tmp/airuleset-question-quality-block-" + sid,
            "/tmp/claude-discord-lastq-" + sid,
            "/tmp/claude-user-active-" + sid,
        ):
            self.addCleanup(lambda p=f: Path(p).unlink(missing_ok=True))
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg, "session_id": sid}),
            capture_output=True, text=True, timeout=30)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"]


# The exact montalu1 failure shape: a client-posting approval ping that is
# otherwise template-compliant (briefing + option bullets + one decision) but
# names its target only by a GENERIC description ("výrobné vlákno"), never the
# exact quoted thread name. It passes Checks 1-5; only the new Check 6 catches
# it. This is the RED-capable regression test.
MONTALU1_NO_NAME = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď do výrobného vlákna na PROD a chcem ju tam poslať. "
    "Schváliš odoslanie tejto správy?\n"
    "\n"
    "• Poslať teraz (odporúčam) — klient dostane odpoveď hneď\n"
    "• Počkať a ešte upraviť znenie — pomalšie, ale istejšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať túto správu klientovi do výrobného vlákna?\n"
)

# The SAME question, correctly naming the exact target thread on its own
# `Vlákno:` line (quoted name + stream-number suffix) — must PASS.
MONTALU1_WITH_NAME = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď do existujúceho vlákna na PROD a chcem ju poslať. "
    "Schváliš odoslanie tejto správy?\n"
    "\n"
    "Vlákno: „Tabula objednavok 1“ (pod IT-support, montalu PROD)\n"
    "\n"
    "• Poslať teraz (odporúčam) — klient dostane odpoveď hneď\n"
    "• Počkať a ešte upraviť znenie — pomalšie, ale istejšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať túto správu klientovi do vlákna "
    "„Tabula objednavok 1“?\n"
)

# A closing/handover client message approval WITHOUT a named thread — must BLOCK.
CLOSING_NO_NAME = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som znenie uzavieracej správy pre klienta k dokončenej úlohe a rád by som "
    "ju zverejnil. Schváliš toto znenie?\n"
    "\n"
    "• Zverejniť znenie (odporúčam) — klient uvidí uzavretie hneď\n"
    "• Ešte doladiť text — pomalšie, ale presnejšie\n"
    "\n"
    "❓ NEEDS YOU: schváliš znenie uzavieracej správy pre klienta?\n"
)

# Ordinary design question — no client-posting intent at all — must PASS.
ORDINARY_DESIGN = (
    "**Otázka — projekt airuleset (nástroj na správu Claude Code pravidiel):** "
    "Robím na tickete #650 a potrebujem rozhodnutie o resete EQ. Má sa reset "
    "nastaviť na 0 dB alebo na posledný uložený preset?\n"
    "\n"
    "• Reset na 0 dB (odporúčam) — čistý východiskový stav\n"
    "• Reset na posledný preset — zachová poslednú konfiguráciu\n"
    "\n"
    "❓ NEEDS YOU: na akú hodnotu má reset EQ ísť?\n"
)

# A question that MENTIONS a Discuss thread but carries NO posting intent
# (the thread already runs; the decision is unrelated) — must PASS. Guards the
# intent detector's narrowness INSIDE a question turn.
DISCUSS_MENTION_NO_INTENT = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Klientske "
    "Discuss vlákno na PROD už beží a notifikácie chodia správne. Zvažujem, či "
    "doň pridať aj denný súhrn.\n"
    "\n"
    "• Zapnúť denný súhrn (odporúčam) — klient dostane prehľad raz denne\n"
    "• Nechať bez súhrnu — menej šumu\n"
    "\n"
    "❓ NEEDS YOU: mám zapnúť denný súhrn?\n"
)


class TestClientPostingQuestionMustNameThread(_HookCase):

    def test_montalu1_no_thread_name_is_blocked(self):
        """RED-capable: the montalu1 failure shape (client-posting approval,
        generic thread description, no exact name) MUST block. Pre-Check-6 it
        sails through all five existing checks."""
        r = self._run(MONTALU1_NO_NAME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            self._blocked(r),
            "a client-posting approval question with no exact thread name was "
            "NOT blocked (the montalu1 regression): stdout=%s" % r.stdout)
        # The block reason must be the thread-name one, not an unrelated check.
        self.assertIn("Vlákno", self._reason(r))

    def test_closing_message_without_thread_name_is_blocked(self):
        r = self._run(CLOSING_NO_NAME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            self._blocked(r),
            "a closing/handover client-message approval with no thread name "
            "was not blocked: stdout=%s" % r.stdout)


class TestNamedAndOrdinaryQuestionsPass(_HookCase):

    def test_question_with_proper_vlakno_line_passes(self):
        r = self._run(MONTALU1_WITH_NAME)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a client-posting question that DOES name the exact thread on a "
            "Vlákno: line was wrongly blocked: %s" % self._reason(r))

    def test_ordinary_design_question_passes(self):
        r = self._run(ORDINARY_DESIGN)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "an ordinary design question was falsely blocked by the "
            "client-posting gate: %s" % self._reason(r))

    def test_discuss_mention_without_posting_intent_passes(self):
        r = self._run(DISCUSS_MENTION_NO_INTENT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a question that merely MENTIONS a Discuss thread (no posting "
            "intent) was falsely blocked: %s" % self._reason(r))


# FALSE-POSITIVE SURFACE (reviewer #650-A): montalu's client comms run through
# Discuss, so away-user ❓ questions constantly MENTION a thread ("v/vo Discuss
# vlákne …") in an ordinary status/design context. The broad first cut over-blocked
# these because a noun-colliding stem (informácia/oznámenie/nápis/zaslané) sat within
# 60 chars of "vlákno". The directional-"do … vlákna" discriminator must let them ALL
# pass — a locative mention is not a posting. Each is a full template-compliant block
# so only Check 6 could (wrongly) fire.
def _q(brief, dec):
    return ("**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** " + brief
            + "\n\n• Áno (odporúčam) — pokračujem\n• Nie — nechám tak\n\n"
            + "❓ NEEDS YOU: " + dec + "\n")

FP_INFORM = _q("V klientskom Discuss vlákne je veľa informácií o objednávke a "
               "informačný prehľad chce klient rozšíriť.",
               "mám ten prehľad rozšíriť?")
FP_OZNAM = _q("Vo vlákne riešime nastavenie oznámení pre klienta a jeho "
              "notifikácie.", "mám tie oznámenia zapnúť?")
FP_NAPIS = _q("Klient v Discuss vlákne chce iný nápis na tlačidle Objednať.",
              "mám ten nápis zmeniť?")
FP_ODOSLANIE = _q("Skontroloval som Discuss vlákno; e-mail o odoslaní objednávky "
                  "klientovi nedorazil a zaslané faktúry mu chýbajú.",
                  "mám ten e-mail poslať znova?")

# DODGE-1 (reviewer #650-A): a generic `Vlákno:` LABEL must NOT satisfy the check.
DODGE_GENERIC_LABEL = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď a chcem ju poslať do výrobného vlákna na PROD.\n"
    "\n"
    "Vlákno: výrobné vlákno na PROD\n"
    "\n"
    "• Poslať teraz (odporúčam) — hneď\n"
    "• Počkať — pomalšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať správu klientovi do výrobného vlákna?\n"
)


class TestFalsePositiveSurface(_HookCase):
    """The directional discriminator: an ordinary montalu question that merely
    MENTIONS a Discuss thread (locative) must PASS; only "do … vlákna" (posting
    INTO it) with a send verb, or a closing message, is intent."""

    def test_inform_status_question_passes(self):
        r = self._run(FP_INFORM)
        self.assertFalse(self._blocked(r),
                         "'informácie … Discuss vlákne' status Q false-blocked: %s"
                         % self._reason(r))

    def test_oznamenia_design_question_passes(self):
        r = self._run(FP_OZNAM)
        self.assertFalse(self._blocked(r),
                         "'oznámení … vo vlákne' design Q false-blocked: %s"
                         % self._reason(r))

    def test_napis_ui_question_passes(self):
        r = self._run(FP_NAPIS)
        self.assertFalse(self._blocked(r),
                         "'nápis … Discuss vlákne' UI Q false-blocked: %s"
                         % self._reason(r))

    def test_email_odoslanie_bug_question_passes(self):
        r = self._run(FP_ODOSLANIE)
        self.assertFalse(self._blocked(r),
                         "'o odoslaní … Discuss vlákno' bug Q false-blocked: %s"
                         % self._reason(r))

    def test_generic_vlakno_label_still_blocks(self):
        r = self._run(DODGE_GENERIC_LABEL)
        self.assertTrue(self._blocked(r),
                        "a generic 'Vlákno: výrobné vlákno' label defeated the "
                        "check (DODGE-1): %s" % r.stdout)
        self.assertIn("Vlákno", self._reason(r))


class TestCheck6ImplementedInHook(unittest.TestCase):
    """Content-lock: the hook must actually implement Check 6, and document its
    accepted residuals (repo convention for a word-family heuristic)."""

    def setUp(self):
        self.h = HOOK.read_text(encoding="utf-8")

    def test_thread_violation_wired(self):
        self.assertIn('VIOLATION="thread"', self.h)
        # a `thread)` reason case must exist in the case-statement
        self.assertIn("thread)", self.h)

    def test_intent_and_named_heuristics_present(self):
        # operative variables that live in the Check-6 code (not just prose):
        # the send-verb, the directional-thread and the named-thread regexes.
        self.assertIn("SEND_VERB_RX", self.h)
        self.assertIn("DIR_THREAD_RX", self.h)
        self.assertIn("THREAD_NAMED_RX", self.h)
        # the closing/handover-message intent stem (operative, in CLOSING_RX)
        self.assertIn("uzavierac", self.h)
        # repo #319 convention: LC_ALL=C.UTF-8 on the greps
        self.assertIn("LC_ALL=C.UTF-8", self.h)

    def test_accepted_residuals_documented(self):
        self.assertIn("Accepted residuals", self.h)


# --------------------------------------------------------------------------- #
# #697 — satisfying-evidence forms aligned with the #657 doctrine (owner-facing
# thread references carry name + deep URL `discuss.channel_<N>`). Check 6's
# ONLY accepted evidence was the QUOTED „name N" form (THREAD_NAMED_RX), so a
# fully-specific approval block identifying its target by the machine-exact
# deep URL, or by an explicit `Vlákno: <name> <N>` line without typographic
# quotes, was over-blocked (live-probed 2026-08-25, montalu1 follow-up). The
# new forms only ADD acceptance — every blocking fixture above must KEEP
# blocking (no-name, DODGE-1, and the ticket-ref dodge below).
# --------------------------------------------------------------------------- #

# Target named ONLY by the #657 deep URL — no quoted name, no `Vlákno:` line —
# must PASS (RED before the #697 fix: Check 6 blocked it).
DEEP_URL_ONLY = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď a chcem ju poslať do existujúceho vlákna "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288 "
    "na PROD. Schváliš odoslanie tejto správy?\n"
    "\n"
    "• Poslať teraz (odporúčam) — klient dostane odpoveď hneď\n"
    "• Počkať a ešte upraviť znenie — pomalšie, ale istejšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať túto správu klientovi do vlákna "
    "discuss.channel_288?\n"
)

# Target named on an explicit `Vlákno:` line carrying the stream-number digit,
# but WITHOUT typographic quotes (the natural way a model writes the label) —
# must PASS (RED before the #697 fix). No quotes and no deep URL anywhere, so
# this can only pass via the `Vlákno:`+digit form (mutation discipline).
VLAKNO_UNQUOTED = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď do existujúceho vlákna na PROD a chcem ju poslať. "
    "Schváliš odoslanie tejto správy?\n"
    "\n"
    "Vlákno: Tabula objednavok 1 (pod IT-support, montalu PROD)\n"
    "\n"
    "• Poslať teraz (odporúčam) — klient dostane odpoveď hneď\n"
    "• Počkať a ešte upraviť znenie — pomalšie, ale istejšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať túto správu klientovi do toho vlákna?\n"
)

# DODGE (forward guard, pins the `[^#]` hardening of the Vlákno: form): a
# `Vlákno:` line whose only digits are a ticket reference (`#650`) is NOT a
# thread name — must keep BLOCKING.
TICKET_REF_DODGE = (
    "**Otázka — projekt montalu (Odoo ERP pre klienta montalu):** Pripravil "
    "som klientovi odpoveď a chcem ju poslať do výrobného vlákna na PROD.\n"
    "\n"
    "Vlákno: pozri ticket #650\n"
    "\n"
    "• Poslať teraz (odporúčam) — hneď\n"
    "• Počkať — pomalšie\n"
    "\n"
    "❓ NEEDS YOU: mám poslať správu klientovi do výrobného vlákna?\n"
)


class TestEvidenceFormsAlignedWith657(_HookCase):
    """#697: the deep URL and the digit-bearing `Vlákno:` line are satisfying
    evidence; a digit-less or ticket-ref label still is not."""

    def test_deep_url_only_passes(self):
        r = self._run(DEEP_URL_ONLY)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a client-posting question naming its target by the #657 deep URL "
            "(discuss.channel_<N>) was wrongly blocked: %s" % self._reason(r))

    def test_unquoted_vlakno_line_with_stream_number_passes(self):
        r = self._run(VLAKNO_UNQUOTED)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(
            self._blocked(r),
            "a client-posting question with an unquoted 'Vlákno: <name> <N>' "
            "line was wrongly blocked: %s" % self._reason(r))

    def test_ticket_ref_digits_do_not_satisfy(self):
        r = self._run(TICKET_REF_DODGE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(
            self._blocked(r),
            "a 'Vlákno: pozri ticket #650' label (digits only in a ticket "
            "ref) defeated the check: %s" % r.stdout)
        self.assertIn("Vlákno", self._reason(r))


class TestCheck697EvidenceRxWired(unittest.TestCase):
    """Content-lock: the two #697 evidence regexes exist as operative
    variables in Check 6 (not just prose), same convention as the #650
    locks above."""

    def setUp(self):
        self.h = HOOK.read_text(encoding="utf-8")

    def test_evidence_rx_present(self):
        self.assertIn("THREAD_DEEPURL_RX", self.h)
        self.assertIn("THREAD_VLAKNO_RX", self.h)
        # the machine-exact deep-URL stem (the #657 form) is in the regex
        self.assertIn("discuss\\.channel_[0-9]+", self.h)


class TestHandoverComposeNotesHookEnforcement(unittest.TestCase):
    """The prose rule (#632) is now hook-enforced at the ❓ approval-question
    surface — keep prose↔hook in sync the way #596/#609/#628 lines do."""

    def test_compose_cites_the_hook(self):
        t = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("stop-check-question-quality.sh", t)
        self.assertIn("#650", t)


if __name__ == "__main__":
    unittest.main()
