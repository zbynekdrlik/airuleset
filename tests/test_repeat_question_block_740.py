"""#740 RECIDÍVA (miva1@subdev, 2026-09-03) — a delivered, still-unanswered
question that a session RE-EMITS on a later turn (the `❓ ASKED:` line, the
full `**Otázka — projekt …:**` block, or the marker alongside `⏳`/`✅`) must be
BLOCKED (exit 2), not silently allowed. The #740 (2026-08-30) prose-only fix
narrowed the doctrine but left `stop-check-question-quality.sh`'s
verbatim-repeat bypass returning exit 0 for EVERY repeat whose bare marker line
matched `LASTQF` — so an ask-and-continue (`❓ ASKED` + `⏳ WORKING`) loop
re-emitted ONE question 27× in 8 h (delivery-side dedup held the phone to one
ping, but each turn still re-printed the text and burned a full turn). The
owner ruling: „točenie otázok dokolečka už nie je potrebné keďže mám v pätičke
U" — a question is emitted ONCE, the footer `U N` carries it after.

The ONE re-emission still allowed is the bare BLOCKED re-poke — a message whose
only ❓ marker is a trailing `❓ NEEDS YOU:` line matching `LASTQF`, with no
`❓ ASKED:`, no `**Otázka — projekt` block and no `⏳`/`✅` marker — because
`/goal` stop-condition (A) needs the marker as the turn's last line to hold a
genuinely blocked loop. Every OTHER shape is exit 2.

Fixtures (design §3):
  (i)   LASTQF=K, `❓ ASKED: K` + `⏳ WORKING`      -> exit 2 + stderr + log line
  (ii)  LASTQF=K, full block ending `❓ NEEDS YOU: K` -> exit 2
  (iii) LASTQF=K, ONLY `❓ NEEDS YOU: K`            -> exit 0 (#740 bare re-poke kept)
  (iv)  no LASTQF, full valid block                -> exit 0 (ordinary shape checks)
  (v)   LASTQF=K, `❓ ASKED: <different>` block     -> not a repeat, ordinary path
  (vi)  no ❓ marker, mentions a parked #N          -> exit 0 (Check 5 never fires)
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

# The plain question TEXT — this is exactly what `strip_md`/KEYLINE derive as
# the dedup key from a `❓ NEEDS YOU: <text>` or `❓ ASKED: <text>` marker line,
# and exactly what the pending hook writes into LASTQ. Seed LASTQF with THIS.
K = "schváliš odoslanie tejto prosby o test Alene?"

BRIEFING = (
    "**Otázka — projekt MIVA (klientsky Odoo systém):** Pri tickete o "
    "zákazníckych testoch potrebujem tvoje potvrdenie, kým rozbehnem ďalšie "
    "kroky.\n"
    "\n"
    "- Poslať prosbu Alene (odporúčam) — spustí sa test\n"
    "- Počkať — nič sa nestane\n"
    "\n")

FULL_BLOCK = BRIEFING + "❓ NEEDS YOU: " + K
ASKED_PLUS_WORKING = "Rozpracoval som iné tickety.\n\n❓ ASKED: " + K + "\n\n⏳ WORKING: robím ďalšie tickety"
BARE_NEEDS_YOU = "❓ NEEDS YOU: " + K
DIFFERENT = "úplne iná otázka o niečom inom?"
DIFFERENT_BLOCK = (
    "**Otázka — projekt MIVA (klientsky Odoo systém):** Toto je odlišná, nová "
    "otázka, ktorá s predošlou nesúvisí.\n"
    "\n"
    "- Možnosť A (odporúčam) — dôsledok A\n"
    "- Možnosť B — dôsledok B\n"
    "\n"
    "❓ ASKED: " + DIFFERENT + "\n\n⏳ WORKING: pokračujem")
NO_MARKER = (
    "Parkujem ticket #5908 (klientske testy) — čaká na odpoveď ownera, nesie ho "
    "footer U N.\n\n⏳ WORKING: robím iné tickety")


class _GateBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-740rep-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _sid(self):
        return new_hook_sid(self, "test-740-repeat", ["*test-740-repeat-*"])

    def _seed_lastq(self, sid, value):
        Path("/tmp/claude-discord-lastq-%s" % sid).write_text(value)

    def _run(self, msg, sid):
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=payload,
                              text=True, capture_output=True, env=env)

    def _delivery_log(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""


class RepeatOfDeliveredQuestionIsBlocked(_GateBase):
    def test_i_asked_plus_working_repeat_is_blocked_with_log_line(self):
        sid = self._sid()
        self._seed_lastq(sid, K)
        r = self._run(ASKED_PLUS_WORKING, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("NEOPAKUJ", r.stderr, r.stderr)
        log = self._delivery_log()
        self.assertIn("blocked", log, log)
        self.assertIn("repeat-asked-question", log, log)

    def test_ii_full_block_repeat_is_blocked(self):
        sid = self._sid()
        self._seed_lastq(sid, K)
        r = self._run(FULL_BLOCK, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("NEOPAKUJ", r.stderr, r.stderr)

    def test_stderr_guides_a_different_ticket_to_a_distinct_marker(self):
        # #740 review 🟡3: an unattended /goal loop whose LASTQF persists across
        # tickets can byte-match a genuinely NEW question for ticket B against
        # ticket A's key — the stderr must tell the session to make the marker
        # line DISTINCT (name #N) rather than silently treat it as a repeat.
        sid = self._sid()
        self._seed_lastq(sid, K)
        r = self._run(ASKED_PLUS_WORKING, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("INÉMU", r.stderr, r.stderr)
        self.assertIn("#N", r.stderr, r.stderr)

    def test_rolling_pushback_sequence_2_2_2_0_2(self):
        # #740 review 🟡2: the repeat path reuses the shape-check RETRY_FILE but
        # CLEARS it at the cap (unlike the shape path, which leaves it at the cap
        # and passes forever) — deliberate rolling pushback so a stubborn session
        # keeps getting pushed back. Firing the SAME non-bare repeat 5x on one
        # sid must yield exit codes [2,2,2,0,2].
        sid = self._sid()
        self._seed_lastq(sid, K)
        codes = [self._run(ASKED_PLUS_WORKING, sid).returncode for _ in range(5)]
        self.assertEqual(codes, [2, 2, 2, 0, 2], codes)


class BareBlockedRepokeStillPasses(_GateBase):
    def test_iii_bare_needs_you_repoke_passes(self):
        # #740: the ONE allowed re-emission — /goal cond (A) needs it.
        sid = self._sid()
        self._seed_lastq(sid, K)
        r = self._run(BARE_NEEDS_YOU, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)
        self.assertNotIn("repeat-asked-question", self._delivery_log())


class ProseStatusCharsDoNotBlockABareRepoke(_GateBase):
    def test_prose_checkmark_line_above_a_bare_repoke_still_passes(self):
        # #740 review 🔵a: an INCIDENTAL ✅ in prose ("✅ merged #5") must NOT
        # count as a status marker — only the `✅ DONE` / `⏳ WORKING` KEYWORD
        # does. A bare `❓ NEEDS YOU: K` re-poke with such a line stays exit 0.
        sid = self._sid()
        self._seed_lastq(sid, K)
        msg = "✅ merged #5 do main.\n\n❓ NEEDS YOU: " + K
        r = self._run(msg, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn("repeat-asked-question", self._delivery_log())

    def test_a_real_working_status_keyword_alongside_needs_you_is_blocked(self):
        # The genuine "marker alongside status" shape (the ❓ NEEDS YOU stays the
        # LAST line so it is detected as a question turn, with a `⏳ WORKING`
        # status keyword above it): non-bare -> exit 2. Proves the keyword check
        # still fires while an incidental prose ✅ (above) does not.
        sid = self._sid()
        self._seed_lastq(sid, K)
        msg = "⏳ WORKING: robím iné tickety\n\n❓ NEEDS YOU: " + K
        r = self._run(msg, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))


class NonRepeatsTakeTheOrdinaryPath(_GateBase):
    def test_iv_full_valid_block_with_no_lastq_passes_shape_checks(self):
        sid = self._sid()  # no LASTQF seeded
        r = self._run(FULL_BLOCK, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn("repeat-asked-question", self._delivery_log())

    def test_v_a_different_asked_question_is_not_a_repeat(self):
        sid = self._sid()
        self._seed_lastq(sid, K)   # a DIFFERENT question is asked this turn
        r = self._run(DIFFERENT_BLOCK, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)
        self.assertNotIn("repeat-asked-question", self._delivery_log())

    def test_vi_turn_without_a_marker_mentioning_a_parked_ticket_passes(self):
        # Check 5 (allusion) must never fire on a non-question turn that merely
        # MENTIONS a parked #N — it is not a question turn at all.
        sid = self._sid()
        self._seed_lastq(sid, K)
        r = self._run(NO_MARKER, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)


class DoctrineLock(unittest.TestCase):
    """The #740 recidíva doctrine: a delivered question is emitted ONCE and the
    soft allowance to re-show the full block is GONE."""

    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_message_status_marker_drops_the_soft_allowance(self):
        t = self._read("modules/core/message-status-marker.md")
        self.assertNotIn("re-show the full block", t)
        self.assertIn("emitted exactly ONCE", t)
        # the ONE blocked-re-poke exception survives
        self.assertIn("STILL blocked on the SAME unanswered", t)

    def test_user_questions_slovak_teaches_ask_once(self):
        # #859 batch 4b: stub + SKILL carry the full detail
        t = self._read("modules/core/user-questions-slovak.md") + "\n" + self._read("skills/user-questions-slovak/SKILL.md")
        self.assertNotIn("SOFT allowance to re-show", t)
        self.assertIn("emitted ONCE", t)


if __name__ == "__main__":
    unittest.main()
