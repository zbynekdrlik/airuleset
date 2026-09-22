"""#1101 — a status/answer that disowns items of the footer `I` obligation set
("17 patria gk-infra", "časť patria subdevu", "nie sú moje", "not mine") WITHOUT
moving the label the partition reads is BLOCKED, with the legend as the fix.

Owner escalation 21.9.2026: the gk FLOW session repeatedly told the owner that
of its footer `I 24`, "17 patria gk-infra, časť subdevu, gk s nimi nema nic
spolocne" — while the labels say 0 `infra`, 17 `gk-processing` (its OWN pickups).
The footer is right; the session narrates the obligation column away instead of
moving the label that the partition reads (`infra` → INFRA window, `prio:bounce`
→ back to the stream, `needs-answer`/`needs-decision` → U, `ops-wait` → W).

The gate fires ONLY when the message is BOTH about the footer/obligation set AND
carries a disowning phrase AND is MISSING a label-move evidence token in the same
turn — the box either ACTS, or MOVES the label, never explains the number away.
The exoneration follows the hook's #195 convention (checked as MISSING on raw
MSG, never as a disarming presence); a strip failure records UNDETERMINABLE
without fabricating a block on a clean message.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from _hook_state_cleanup import hermetic_hook_env  # noqa: E402  (#1046 hermetic HOME)

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"


class _HookCase(unittest.TestCase):

    def _run(self, msg, env=None):
        sid = "inotmine-%s" % uuid.uuid4().hex[:12]
        self.addCleanup(
            lambda: Path("/tmp/airuleset-stop-block-%s" % sid).unlink(
                missing_ok=True))
        # #1046: hook must never read the box's real ~/.claude. Default to a
        # hermetic HOME; a caller's own env keeps its vars but its HOME is
        # forced hermetic too.
        _hh = hermetic_hook_env(self)
        env = _hh if env is None else {**env, "HOME": _hh["HOME"]}
        return subprocess.run(
            ["bash", str(HOOK)],
            input=json.dumps({"last_assistant_message": msg,
                              "session_id": sid}),
            capture_output=True, text=True, env=env, timeout=300)

    def _blocked(self, r):
        return '"block"' in r.stdout

    def _reason(self, r):
        if not self._blocked(r):
            return ""
        return json.loads(r.stdout)["reason"].replace("\\n", "\n")

    def _python3_stub(self, exit_code=1):
        """A python3 on PATH that always errors — forces strip_mentions() to
        fail (it shells out to python3), so MSG_MENTION falls back to raw MSG
        and record_undet() fires. grep is untouched."""
        d = Path(tempfile.mkdtemp(prefix="airuleset-inotmine-py-"))
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        stub = d / "python3"
        stub.write_text("#!/bin/sh\nexit %d\n" % exit_code)
        stub.chmod(0o755)
        return {**os.environ, "PATH": "%s:%s" % (d, os.environ["PATH"])}


# The incident status line / chat answers, each with genuine footer context.
BLOCK_MESSAGES = {
    "polozky_I_su_infra":
        "Owner sa pýta prečo je v I 24 toľko. Zvyšné položky I sú infra "
        "(INFRA session) alebo čakajú na streamy/ownera.\n⏳ WORKING",
    "17_patria_gk_infra":
        "Footer I 24: z toho 17 patria gk-infra, gk s nimi nemá nič "
        "spoločné.\n⏳ WORKING",
    "patria_subdevu":
        "Prečo je v I 24 toľko? Časť patria subdevu — nie je to moja práca.\n"
        "⏳ WORKING",
    "nemA_nic_spolocne":
        "V I 24 je veľa ticketov, ale gk s nimi nemá nič spoločné (patria "
        "streamom).\n⏳ WORKING",
    "nie_su_moje":
        "Tie issues v I 24 nie sú moje — sú to cudzie stream tickety.\n"
        "⏳ WORKING",
    "cakaju_na_streamy":
        "Prečo core-quals --list vracia 24? Väčšina čaká na streamy, nie "
        "moja povinnosť.\n⏳ WORKING",
    "english_not_mine":
        "Why is the footer I 24 so high? 17 of them are not mine — they "
        "belong to infra.\n⏳ WORKING",
}


class TestDisowningItemsOfIIsBlocked(_HookCase):

    def test_each_incident_phrasing_blocks(self):
        for name, msg in BLOCK_MESSAGES.items():
            with self.subTest(phrasing=name):
                r = self._run(msg)
                self.assertTrue(
                    self._blocked(r),
                    "disowning items of I without a label move must BLOCK "
                    "(%s); stderr=%r" % (name, r.stderr[:300]))

    def test_block_reason_carries_the_legend(self):
        r = self._run(BLOCK_MESSAGES["17_patria_gk_infra"])
        reason = self._reason(r)
        # the obligation legend
        self.assertIn("action-only", reason)
        for tok in ("review", "merge", "release"):
            self.assertIn(tok, reason.lower())
        # the label-move routing legend
        for tok in ("infra", "prio:bounce", "needs-answer", "ops-wait"):
            self.assertIn(tok, reason)


class TestLabelMoveExonerates(_HookCase):
    """The same disowning message PASSES when it carries a label-move evidence
    token in the SAME turn — the box acted / moved the label, so the footer's
    truthful answer exists."""

    def test_add_label_infra_passes(self):
        msg = (BLOCK_MESSAGES["17_patria_gk_infra"]
               + "\nPresunul som ich: `gh issue edit 7773 --add-label infra`.")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "a disowning line WITH --add-label infra must PASS; "
                         "reason=%r" % self._reason(r))

    def test_add_label_prio_bounce_passes(self):
        msg = (BLOCK_MESSAGES["patria_subdevu"]
               + "\n`gh issue edit 7821 --add-label prio:bounce` — vraciam "
               "streamu.")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "a disowning line WITH --add-label prio:bounce must "
                         "PASS; reason=%r" % self._reason(r))

    def test_add_label_needs_answer_passes(self):
        msg = (BLOCK_MESSAGES["nie_su_moje"]
               + "\n`gh issue edit 7850 --add-label needs-answer` — otázka na "
               "ownera.")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "a disowning line WITH --add-label needs-answer must "
                         "PASS; reason=%r" % self._reason(r))

    def test_slovak_label_move_prose_passes(self):
        msg = (BLOCK_MESSAGES["polozky_I_su_infra"]
               + "\nLabel infra som presunul na #7773.")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "a disowning line WITH a Slovak label-move phrase must "
                         "PASS; reason=%r" % self._reason(r))


class TestNoFalsePositives(_HookCase):

    def test_infra_without_footer_context_is_unaffected(self):
        msg = ("Presunul som deploy skript do infra adresára a spustil "
               "infra pipeline; všetko zelené.\n✅ DONE")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "a message mentioning 'infra' with no footer/I "
                         "context must be unaffected; reason=%r"
                         % self._reason(r))

    def test_percentage_or_ixx_token_is_not_footer_context(self):
        # #1101 reviews A+B (MEDIUM): a bare `I <digits>` footer signal must not
        # false-match "I 100%" (a percentage) NOR "i18n"/"i7" (case-insensitive,
        # no space) when combined with a disowning phrase in a NON-footer report.
        cases = {
            "percentage": ("I 100% confirm the migration ran; these tables are "
                           "not mine to drop.\n✅ DONE"),
            "i18n": ("Fixed the i18n bug; the translation strings are not mine "
                     "to edit.\n✅ DONE"),
            "i7_box": ("Benchmarked on the i7 box, 24 threads; results not mine "
                       "to publish yet.\n✅ DONE"),
        }
        for name, msg in cases.items():
            with self.subTest(case=name):
                r = self._run(msg)
                self.assertFalse(
                    self._blocked(r),
                    "a %s token + a disowning phrase in a NON-footer message "
                    "must not fabricate a block (%s); reason=%r"
                    % ("percentage/ixx", name, self._reason(r)))

    def test_footer_context_without_disowning_passes(self):
        # footer/obligation context present, but NO disowning phrase — the
        # check must not fire on the mere presence of "I 24" / "core-quals".
        msg = ("Footer I 24: spustil som core-quals --list, pracujem na "
               "review a mergnem ich do ďalšej verzie.\n⏳ WORKING")
        r = self._run(msg)
        self.assertFalse(
            self._blocked(r),
            "footer context with no disowning phrase must not trip the "
            "I-not-mine check; reason=%r" % self._reason(r))

    def test_i_is_mine_explanation_passes(self):
        # The CORRECT answer: naming the 17 as own pickups is not disowning.
        msg = ("Footer I 24: 17 z nich sú moje gk-processing pickupy "
               "(review → merge → release), 4 sú core implement, 1 bounce. "
               "Všetky sú moja povinnosť.\n⏳ WORKING")
        r = self._run(msg)
        self.assertFalse(self._blocked(r),
                         "the truthful 'they are my pickups' answer must PASS; "
                         "reason=%r" % self._reason(r))


class TestUndeterminableNeverFabricatesABlock(_HookCase):

    def test_strip_failure_on_a_clean_message_records_undet_not_a_block(self):
        env = self._python3_stub()
        msg = ("Práca hotová, mergnuté a nasadené.\n✅ DONE")
        r = self._run(msg, env=env)
        self.assertFalse(
            self._blocked(r),
            "a strip failure on a clean message must NOT fabricate a block; "
            "reason=%r" % self._reason(r))
        self.assertIn("undeterminable", r.stderr.lower(),
                      "a strip failure must be recorded UNDETERMINABLE on "
                      "stderr")

    def test_disowning_inside_a_backtick_mention_is_not_a_bare_offer(self):
        # The session QUOTING the banned phrase while describing the rule —
        # strip_mentions removes the backtick span, so it is not read as a
        # bare disowning statement.
        msg = ("Pridal som do prose gate check: blokuje vetu typu "
               "`17 patria gk-infra` v I kontexte bez label move.\n✅ DONE")
        r = self._run(msg)
        self.assertFalse(
            self._blocked(r),
            "a backticked MENTION of the banned phrase (describing the rule) "
            "must not block; reason=%r" % self._reason(r))


if __name__ == "__main__":
    unittest.main()
