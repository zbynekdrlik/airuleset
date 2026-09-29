"""#1177 (owner, montalu4, 29.9.2026) — an approval ❓ must carry ONE client
message; the #1006 bundle detector must see the WHOLE final message.

Owner verbatim: „zasa mi davas naraz spravy!!! … jedna sprava/jedna otazka a
potom dalsia, nie naraz!!!"

The 29.9 montalu4 shape slipped past `stop-check-question-quality.sh` two ways:
  1. the two client drafts sat ABOVE the `**Otázka — projekt …:**` head, so the
     #1006 counters (which only read the delivered `$BLOCK`, head..marker) never
     saw them — the block held only „môžem poslať Patrikovi tieto dve správy?";
  2. the drafts were signed with the stream-indexed signature `ZbynekAI 4`,
     which the old `ZbynekAI[[:space:]]*$` signature-line regex never matched.

Fix: when the ❓ block carries client-APPROVAL intent, the #1006 counters read
the whole message; signatures match `ZbynekAI( <N>)?`; and a per-draft target
count (a `>`-quote run whose header names a Discuss thread / Odoo task) catches
unsigned bundles. No false positive for ONE draft that carries its own task
URL + thread URL of the same conversation, or ONE draft whose briefing merely
mentions another thread.

Drives the REAL hook on stdin JSON (HOME=mktemp -d), fixtures in the same
shape as tests/test_question_bundle_1006.py so a single-draft message passes
every other shape check (exit 0, no block on stdout).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import new_hook_sid  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "hooks" / "stop-check-question-quality.sh"

ERP = "https://erp.montalu.cloud"
TASK_1192 = ERP + "/odoo/project/4/tasks/1192"
THREAD_783 = ERP + "/odoo/discuss?active_id=discuss.channel_783"
THREAD_700 = ERP + "/odoo/discuss?active_id=discuss.channel_700"

QBLOCK_TWO = (
    "**Otázka — projekt montalu4 (klientsky Odoo systém montalu):** Patrik sa "
    "pýtal na termín montáže a na cenník. Pripravil som odpovede vyššie.\n\n"
    "- Poslať (odporúčam) — odídu tak, ako sú\n"
    "- Upraviť — napíš zmeny\n\n"
    "❓ NEEDS YOU: môžem poslať Patrikovi tieto dve správy?")

# The 29.9 montalu4 shape: two drafts ABOVE the block, signed `ZbynekAI 4`,
# one aimed at a task (URL in its header), one at a Discuss thread.
MONTALU4_2909 = (
    "Návrh 1 — do úlohy „Montáž Patrik 4\" (" + TASK_1192 + "):\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n"
    "> ZbynekAI 4\n\n"
    "Návrh 2 — vlákno „Cenník Patrik 4\" (" + THREAD_783 + "):\n\n"
    "> Dobrý deň, cenník sme aktualizovali podľa vašej požiadavky.\n"
    "> ZbynekAI 4\n\n"
    + QBLOCK_TWO)

# Same bundle, but the drafts are UNSIGNED — only the per-draft target headers
# reveal two client messages.
UNSIGNED_TWO_TARGETS = (
    "Úloha: „Montáž Patrik 4\" — " + TASK_1192 + "\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n\n"
    "Vlákno: „Cenník Patrik 4\" — " + THREAD_783 + "\n\n"
    "> Dobrý deň, cenník sme aktualizovali podľa vašej požiadavky.\n\n"
    + QBLOCK_TWO)

QBLOCK_ONE = (
    "**Otázka — projekt montalu4 (klientsky Odoo systém montalu):** Patrik sa "
    "pýtal na termín montáže. Pripravil som odpoveď vyššie.\n\n"
    "- Poslať (odporúčam) — odíde tak, ako je\n"
    "- Upraviť — napíš zmeny\n\n"
    "❓ NEEDS YOU: môžem poslať Patrikovi túto správu?")

# ONE draft whose header carries BOTH the task URL and the thread URL of the
# SAME conversation — one message, must pass.
ONE_DRAFT_TASK_AND_THREAD = (
    "Úloha: „Montáž Patrik 4\" — " + TASK_1192 + "\n"
    "Vlákno: „Montáž Patrik 4\" — " + THREAD_783 + "\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n"
    "> ZbynekAI 4\n\n"
    + QBLOCK_ONE)

# ONE draft, plus free briefing prose that mentions ANOTHER thread (with its
# deep URL) — context, not a second message; must pass.
ONE_DRAFT_BRIEFING_OTHER_THREAD = (
    "Kontext: Patrik minulý týždeň písal aj vo vlákne „Cenník Patrik 4\" ("
    + THREAD_700 + "), tam je všetko vybavené.\n\n"
    "Vlákno: „Montáž Patrik 4\" — " + THREAD_783 + "\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n"
    "> ZbynekAI 4\n\n"
    + QBLOCK_ONE)

# The old #1006 case, now with the stream-indexed signature — two drafts INSIDE
# the block (after the options, as tests/test_question_bundle_1006.py places them).
IN_BLOCK_INDEXED_SIGS = (
    "**Otázka — projekt montalu4 (klientsky Odoo systém):** Mám pripravené "
    "klientske texty na odoslanie, potvrď prosím.\n\n"
    "- Odoslať (odporúčam) — pošle sa klientovi\n"
    "- Upraviť — napíš zmeny\n\n"
    "Návrh A:\n"
    "> Dobrý deň, dokončili sme fakturačný modul podľa zadania.\n"
    "> ZbynekAI 4\n\n"
    "Návrh B:\n"
    "> Dobrý deň, cenník bol aktualizovaný podľa vašej požiadavky.\n"
    "> ZbynekAI 4\n\n"
    "❓ NEEDS YOU: schváliš odoslanie oboch textov klientovi?")

# Unsigned bundle named only by explicit target LINES (markdown-decorated, no
# URL) and approved with „odoslať" — the per-draft target count must catch it.
UNSIGNED_TARGET_LINES_ONLY = (
    "**Úloha:** „Montáž Patrik 4\"\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n\n"
    "- Vlákno: „Cenník Patrik 4\"\n\n"
    "> Dobrý deň, cenník sme aktualizovali podľa vašej požiadavky.\n\n"
    + QBLOCK_TWO.replace("môžem poslať", "môžem odoslať"))

# The client's own (unsigned) message quoted under a header carrying the
# thread URL, then OUR one signed reply into the same thread — one message.
CLIENT_QUOTE_THEN_ONE_REPLY = (
    "Patrik napísal vo vlákne „Montáž Patrik 4\" (" + THREAD_783 + "):\n\n"
    "> Kedy príde montážna skupina? Potrebujem vedieť do štvrtka.\n\n"
    "Vlákno: „Montáž Patrik 4\" — " + THREAD_783 + "\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n"
    "> ZbynekAI 4\n\n"
    + QBLOCK_ONE)

# A NON-approval question whose message quotes two ALREADY-SENT signed replies
# as a report — no client-approval intent in the ❓ block, so the whole-message
# scope does not apply and it must pass.
REPORT_THEN_UNRELATED_QUESTION = (
    "Včera odišli tieto dve odpovede:\n\n"
    "> Dobrý deň Patrik, montáž je naplánovaná na piatok 3.10. od 8:00.\n"
    "> ZbynekAI 4\n\n"
    "Druhá:\n\n"
    "> Dobrý deň, cenník sme aktualizovali podľa vašej požiadavky.\n"
    "> ZbynekAI 4\n\n"
    "**Otázka — projekt montalu4 (klientsky Odoo systém):** Tester je zelený, "
    "čakám na rozhodnutie o nasadení.\n\n"
    "- Nasadiť teraz (odporúčam) — pôjde na prod\n"
    "- Počkať — nič sa nestane\n\n"
    "❓ NEEDS YOU: nasadiť verziu 0.4.2 na produkčný server teraz?")


class _GateBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1177bundle-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _sid(self):
        return new_hook_sid(self, "test-1177-bundle", ["*test-1177-bundle-*"])

    def _run(self, msg, sid):
        payload = {"last_assistant_message": msg, "session_id": sid}
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env)


class BundledDraftsBlocked(_GateBase):
    def test_montalu4_2909_drafts_above_block_blocked(self):
        r = self._run(MONTALU4_2909, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("JEDEN", r.stderr, r.stderr)

    def test_montalu4_2909_blocked_even_when_user_present(self):
        sid = self._sid()
        Path("/tmp/claude-user-active-%s" % sid).write_text("x")
        r = self._run(MONTALU4_2909, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))

    def test_unsigned_drafts_two_targets_blocked(self):
        r = self._run(UNSIGNED_TWO_TARGETS, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("JEDEN", r.stderr, r.stderr)

    def test_unsigned_target_lines_only_blocked(self):
        r = self._run(UNSIGNED_TARGET_LINES_ONLY, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("vlákno/úloha: 2", r.stderr, r.stderr)

    def test_in_block_indexed_signatures_blocked(self):
        r = self._run(IN_BLOCK_INDEXED_SIGS, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))


class SingleDraftPasses(_GateBase):
    def assertPasses(self, msg):
        r = self._run(msg, self._sid())
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)

    def test_one_draft_with_task_and_thread_url_passes(self):
        self.assertPasses(ONE_DRAFT_TASK_AND_THREAD)

    def test_one_draft_briefing_mentions_other_thread_passes(self):
        self.assertPasses(ONE_DRAFT_BRIEFING_OTHER_THREAD)

    def test_client_quote_then_one_reply_passes(self):
        self.assertPasses(CLIENT_QUOTE_THEN_ONE_REPLY)

    def test_report_of_sent_messages_with_unrelated_question_passes(self):
        self.assertPasses(REPORT_THEN_UNRELATED_QUESTION)


if __name__ == "__main__":
    unittest.main()
