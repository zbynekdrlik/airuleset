"""#1007 (owner report miva1 ×5, 2026-09-12) — `stop-check-question-quality.sh`
must block a `❓ NEEDS YOU:` turn while a background lane is LIVE, and must
refuse a degenerate decision line that names nothing (`voľba 1/2/3?`).

Two gaps the existing hook had:
  (A) NEEDS YOU ("I stopped, nothing else workable") while the harness lists a
      RUNNING `background_tasks` entry is a state lie — with lanes live the
      correct form is ASK-AND-CONTINUE (`❓ ASKED` + `⏳ WORKING`). The hook
      never read `background_tasks`, so the #740 bare-re-poke bypass waved the
      miva1 spam through on every task-completion wake.
  (C) a bare `❓ NEEDS YOU:` / `❓ ASKED:` decision line must NAME the decision
      (≥ 25 codepoints, not a bare `voľba/option/choice/<digit>` start) —
      ONLY for a bare-marker turn; inside a full `**Otázka — projekt` block the
      mandatory briefing already carries the subject, so a short full-block
      decision ("schváliš merge PR #5?") stays valid.

Drives the REAL hook on stdin JSON (HOME=mktemp -d), same harness as
test_repeat_question_block_740.py.
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

GOODQ = "schváliš nasadenie verzie 0.29.24 na produkčný OBS teraz?"
BRIEFING = (
    "**Otázka — projekt airuleset (správa Claude pravidiel):** Pri nasadení "
    "novej verzie potrebujem tvoje potvrdenie, kým pokračujem.\n"
    "\n"
    "- Nasadiť teraz (odporúčam) — verzia pôjde na prod\n"
    "- Počkať — nič sa nestane\n"
    "\n")
FULL_BLOCK = BRIEFING + "❓ NEEDS YOU: " + GOODQ
ASK_AND_CONTINUE = BRIEFING + "❓ ASKED: " + GOODQ + "\n\n⏳ WORKING: robím ďalšie tickety"
BARE_GOOD = "❓ NEEDS YOU: " + GOODQ
BARE_VOLBA = "❓ NEEDS YOU: voľba 1/2/3?"
BARE_DIGIT = "❓ NEEDS YOU: 1/2/3?"
SHORT_MERGE = "schváliš merge PR #5?"          # 21 chars, < 25 — valid in a full block
FULL_BLOCK_SHORT = (
    "**Otázka — projekt airuleset (správa Claude pravidiel):** PR #5 je celý "
    "zelený, čakám na tvoje schválenie merge.\n"
    "\n"
    "- Áno, mergni (odporúčam) — pôjde do main\n"
    "- Nie — počkáme\n"
    "\n"
    "❓ NEEDS YOU: " + SHORT_MERGE)

RUNNING_BT = [{"id": "a1", "type": "subagent", "status": "running",
               "description": "worker #x"}]
COMPLETED_BT = [{"id": "a1", "type": "subagent", "status": "completed",
                 "description": "worker #x"}]


class _GateBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1007ll-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _sid(self):
        return new_hook_sid(self, "test-1007-livelane", ["*test-1007-livelane-*"])

    def _seed_lastq(self, sid, value):
        Path("/tmp/claude-discord-lastq-%s" % sid).write_text(value)

    def _run(self, msg, sid, background_tasks=None):
        payload = {"last_assistant_message": msg, "session_id": sid}
        if background_tasks is not None:
            payload["background_tasks"] = background_tasks
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env)


class CheckA_LiveLaneNeedsYouIsBlocked(_GateBase):
    def test_full_block_needs_you_with_running_agent_blocked(self):
        sid = self._sid()
        r = self._run(FULL_BLOCK, sid, background_tasks=RUNNING_BT)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("ASKED", r.stderr, r.stderr)
        self.assertIn("WORKING", r.stderr, r.stderr)

    def test_bare_repoke_needs_you_with_running_agent_blocked(self):
        # the exact miva1 shape: a bare re-poke that matches LASTQF would pass
        # the #740 bypass, but a live lane must block it first.
        sid = self._sid()
        self._seed_lastq(sid, GOODQ)
        r = self._run(BARE_GOOD, sid, background_tasks=RUNNING_BT)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("ASKED", r.stderr, r.stderr)

    def test_ask_and_continue_with_running_agent_passes(self):
        # ASK-AND-CONTINUE (ends ⏳ WORKING, not NEEDS YOU) is the CORRECT form
        # with lanes live — never blocked by check A.
        sid = self._sid()
        r = self._run(ASK_AND_CONTINUE, sid, background_tasks=RUNNING_BT)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_needs_you_with_only_completed_task_not_blocked_by_check_a(self):
        # a lingering completed (non-running) entry is not a LIVE lane — check A
        # keys on status=="running" exactly like stop-check-working-liveness.sh.
        sid = self._sid()
        r = self._run(FULL_BLOCK, sid, background_tasks=COMPLETED_BT)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class CheckB_BareRepokeStillAllowedWhenNoLaneLive(_GateBase):
    def test_needs_you_empty_tasks_identical_prev_line_passes(self):
        # dispatch case: NEEDS YOU + empty tasks + identical previous line → pass
        sid = self._sid()
        self._seed_lastq(sid, GOODQ)
        r = self._run(BARE_GOOD, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_needs_you_no_bt_key_identical_prev_line_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, GOODQ)
        r = self._run(BARE_GOOD, sid)  # no background_tasks key at all
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class CheckC_DecisionLineMustNameTheDecision(_GateBase):
    def test_bare_volba_1_2_3_blocked(self):
        sid = self._sid()
        self._seed_lastq(sid, "voľba 1/2/3?")
        r = self._run(BARE_VOLBA, sid, background_tasks=[])
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("footer", r.stderr.lower(), r.stderr)

    def test_bare_digit_start_blocked(self):
        sid = self._sid()
        self._seed_lastq(sid, "1/2/3?")
        r = self._run(BARE_DIGIT, sid, background_tasks=[])
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))

    def test_full_block_short_decision_passes(self):
        # a full block's briefing carries the subject → a short decision line
        # ("schváliš merge PR #5?", 21 chars) stays valid. No regression.
        sid = self._sid()
        r = self._run(FULL_BLOCK_SHORT, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_full_question_block_passes(self):
        # dispatch case: a full question block → pass
        sid = self._sid()
        r = self._run(FULL_BLOCK, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_bare_good_decision_repoke_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, GOODQ)
        r = self._run(BARE_GOOD, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_bare_digit_start_descriptive_decision_passes(self):
        # #1007 review 🟡: a decision that merely STARTS with a digit but names
        # the decision ("3 verzie … ktorú nasadiť?") must NOT be blocked — only
        # a bare numeric enumeration ("1/2/3?") is degenerate.
        sid = self._sid()
        q = "3 verzie sú hotové — ktorú nasadiť na produkčný OBS teraz?"
        self._seed_lastq(sid, q)
        r = self._run("❓ NEEDS YOU: " + q, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))

    def test_bare_2fa_style_decision_passes(self):
        # "2FA chceš zapnúť…?" starts with a digit but is a real question.
        sid = self._sid()
        q = "2FA chceš zapnúť na produkčnom serveri ešte dnes?"
        self._seed_lastq(sid, q)
        r = self._run("❓ NEEDS YOU: " + q, sid, background_tasks=[])
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


if __name__ == "__main__":
    unittest.main()
