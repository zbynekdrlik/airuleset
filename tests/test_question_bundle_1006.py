"""#1006 (owner, montalu, repeated escalation 12.9.2026) — a ❓ block that
bundles MORE THAN ONE client text / decision must be BLOCKED (exit 2).

Trigger: a question block with ONE `❓ NEEDS YOU:` decision line but TWO quoted
client drafts (Text úloha 638 + Text úloha 881, each signed `ZbynekAI`) passed
`stop-check-question-quality.sh` (Check 2 catches only a `(1)/(2)` multi-
QUESTION pile, never multiple client TEXTS). Owner rule: JEDNA otázka = JEDEN
klientsky text — queue the rest.

Deterministic detectors on the delivered BLOCK (any signal ≥ 2 ⇒ bundle):
≥ 2 `ZbynekAI` signature lines, ≥ 2 `Text úloha`/`Text pre`-style draft
headers, or ≥ 2 `❓ (NEEDS YOU|ASKED)` decision markers. Runs BEFORE the
present-user bypass (like the #740 repeat-block) so it fires even when the owner
is present in the webterm — the exact montalu shape that slipped through.

The fixtures carry a proper shape (short briefing + option bullets) so a
single-text block genuinely PASSES every check (exit 0, no block on stdout) and
the two-text block genuinely PASSED the pre-fix hook — proving the #1006
detector is what blocks it, not Check 3/4.

Drives the REAL hook on stdin JSON (HOME=mktemp -d).
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

BRIEF = ("**Otázka — projekt montalu (klientsky Odoo systém):** Mám pripravené "
         "klientske texty na odoslanie, potvrď prosím.\n\n"
         "- Odoslať (odporúčam) — pošle sa klientovi\n"
         "- Upraviť — napíš zmeny\n\n")

TWO_TEXTS = (BRIEF +
    "Text úloha 638:\n"
    "> Dobrý deň, dokončili sme fakturačný modul podľa zadania.\n"
    "> ZbynekAI\n\n"
    "Text úloha 881:\n"
    "> Dobrý deň, cenník bol aktualizovaný podľa vašej požiadavky.\n"
    "> ZbynekAI\n\n"
    "❓ NEEDS YOU: schváliš odoslanie oboch textov klientovi?")

ONE_TEXT = (BRIEF +
    "Text úloha 638:\n"
    "> Dobrý deň, dokončili sme fakturačný modul podľa zadania.\n"
    "> ZbynekAI\n\n"
    "❓ NEEDS YOU: schváliš odoslanie tohto textu klientovi?")

TWO_DECISIONS = (BRIEF +
    "❓ NEEDS YOU: schváliš odoslanie textu pre úlohu 638 klientovi?\n"
    "❓ NEEDS YOU: schváliš odoslanie textu pre úlohu 881 klientovi?")

PLAIN_QUESTION = (
    "**Otázka — projekt airuleset (správa Claude pravidiel):** Verzia je zelená, "
    "čakám na potvrdenie.\n\n"
    "- Nasadiť teraz (odporúčam) — pôjde na prod\n"
    "- Počkať — nič sa nestane\n\n"
    "❓ NEEDS YOU: schváliš nasadenie verzie 0.29.24 na produkčný OBS teraz?")


class _GateBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-1006bundle-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _sid(self):
        return new_hook_sid(self, "test-1006-bundle", ["*test-1006-bundle-*"])

    def _mark_present(self, sid):
        Path("/tmp/claude-user-active-%s" % sid).write_text("x")

    def _run(self, msg, sid):
        payload = {"last_assistant_message": msg, "session_id": sid}
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=json.dumps(payload),
                              text=True, capture_output=True, env=env)


class BundledClientTextsBlocked(_GateBase):
    def test_two_client_texts_one_decision_blocked(self):
        r = self._run(TWO_TEXTS, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("JEDEN", r.stderr, r.stderr)

    def test_two_decision_markers_blocked(self):
        r = self._run(TWO_DECISIONS, self._sid())
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))

    def test_bundle_blocked_even_when_user_present(self):
        # the montalu shape: owner present in the webterm, so the present-user
        # bypass would let the bundle through — #1006 must fire BEFORE it.
        sid = self._sid()
        self._mark_present(sid)
        r = self._run(TWO_TEXTS, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))


class SingleTextBlockPasses(_GateBase):
    def test_one_client_text_one_decision_passes(self):
        r = self._run(ONE_TEXT, self._sid())
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)

    def test_plain_question_no_client_text_passes(self):
        r = self._run(PLAIN_QUESTION, self._sid())
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))
        self.assertNotIn('"block"', r.stdout, r.stdout)


if __name__ == "__main__":
    unittest.main()
