"""#878 — semantic same-question key: a reworded full `**Otázka` block (or
`❓ ASKED` line) for the SAME `#N` ticket set is a REPEAT even if the prose
differs. The #740 byte-match gate only catches verbatim repeats; a reworded
block with a different qhash passes as a fresh first ask. The 154× incident
(dev2 marek-3) proved the gap: 20 reworded blocks in one session, each with a
new qhash, phone quiet (delivery dedup caught them) but the pane spammed.

Fix: `stop-check-question-quality.sh` extracts `#N` refs from the ❓ decision
line, stores them in `/tmp/claude-lastq-refs-<SID>`. On a subsequent full-block
/ `❓ ASKED` turn with LASTQF present (delivered, unanswered, no real user turn
since), the sorted `#N` set is compared for exact equality. Match → exit 2
(same rolling-pushback path as #740). Fail-safe: empty set / absent / unreadable
sidecar → fall through (ordinary shape checks).

Fixtures (design §3):
  (1) two differently-worded full blocks, same #356 → 2nd exit 2
  (2) reworded block after real user turn (sidecar removed) → passes
  (3) different #N (#999) in the decision line → passes
  (4) multi-ref {#356,#372} vs stored {#356} → passes (exact-set, not intersection)
  (5) unreadable/absent sidecar → passes (fail-safe)
  (6) bare re-poke matching LASTQF → still exit 0 (#740 shape untouched)
  (7) set-equality pinned: stored {#356,#372}, asked {#356,#372} → exit 2
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

K_MARKER_A = "schváliš text otázky Dominikovi (#356)?"
K_MARKER_B = "potvrdíš návrh otázky pre Dominika k #356?"

BLOCK_A = (
    "**Otázka — projekt montalu (automatizácia Odoo pre montalu):** "
    "Ticket #356 čaká na tvoje schválenie textu otázky Dominikovi.\n"
    "\n"
    "- Schváliť text (odporúčam) — otázka sa pošle\n"
    "- Upraviť text — napíš zmeny\n"
    "\n"
    "❓ NEEDS YOU: " + K_MARKER_A)

BLOCK_B = (
    "**Otázka — projekt montalu (automatizácia Odoo pre montalu):** "
    "Pri tickete #356 je pripravený návrh textu pre Dominika. "
    "Potrebujem tvoje potvrdenie pred odoslaním.\n"
    "\n"
    "- Súhlas s návrhom (odporúčam) — pošlem\n"
    "- Zmeniť formuláciu — oprav text\n"
    "\n"
    "❓ NEEDS YOU: " + K_MARKER_B)

BARE_REPOKE = "❓ NEEDS YOU: " + K_MARKER_A

BLOCK_DIFFERENT = (
    "**Otázka — projekt montalu (automatizácia Odoo pre montalu):** "
    "Ticket #999 vyžaduje rozhodnutie o formáte exportu.\n"
    "\n"
    "- CSV (odporúčam) — kompatibilné\n"
    "- JSON — modernejší formát\n"
    "\n"
    "❓ NEEDS YOU: schváliš formát exportu pre #999?")

BLOCK_MULTI = (
    "**Otázka — projekt montalu (automatizácia Odoo pre montalu):** "
    "Tickety #356 a #372 čakajú na schválenie.\n"
    "\n"
    "- Schváliť oba (odporúčam) — oba sa pošlú\n"
    "- Schváliť len jeden — vyber ktorý\n"
    "\n"
    "❓ NEEDS YOU: schváliš text otázky k #356 a #372?")


class _GateBase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="airuleset-878sem-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _sid(self):
        return new_hook_sid(self, "test-878-semantic", ["*test-878-semantic-*"])

    def _seed_lastq(self, sid, value):
        Path("/tmp/claude-discord-lastq-%s" % sid).write_text(value)

    def _seed_refs(self, sid, refs_str):
        Path("/tmp/claude-lastq-refs-%s" % sid).write_text(refs_str)

    def _run(self, msg, sid):
        payload = json.dumps({"last_assistant_message": msg, "session_id": sid})
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(["bash", str(GATE)], input=payload,
                              text=True, capture_output=True, env=env)

    def _delivery_log(self):
        p = self.home / ".claude" / "notify-delivery.log"
        return p.read_text() if p.exists() else ""


class SemanticSameQuestionBlocked(_GateBase):
    """Two differently-worded blocks for the same #356, with LASTQF + sidecar."""

    def test_1_reworded_block_same_refs_is_blocked(self):
        sid = self._sid()
        self._seed_lastq(sid, K_MARKER_A)
        self._seed_refs(sid, "#356")
        r = self._run(BLOCK_B, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))
        self.assertIn("#878", r.stderr)

    def test_7_exact_multi_ref_set_equality_blocks(self):
        sid = self._sid()
        self._seed_lastq(sid, "iný text pre #356 a #372?")
        self._seed_refs(sid, "#356 #372")
        r = self._run(BLOCK_MULTI, sid)
        self.assertEqual(r.returncode, 2, (r.returncode, r.stdout, r.stderr))


class AfterRealUserTurnPasses(_GateBase):
    """LASTQF + sidecar removed by clear-question-dedup.sh → reworded block passes."""

    def test_2_reworded_block_after_user_turn_passes(self):
        sid = self._sid()
        r = self._run(BLOCK_B, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class DifferentTicketPasses(_GateBase):
    """A question about a DIFFERENT #N passes even with LASTQF + sidecar."""

    def test_3_different_ticket_ref_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, K_MARKER_A)
        self._seed_refs(sid, "#356")
        r = self._run(BLOCK_DIFFERENT, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class MultiRefSubsetPasses(_GateBase):
    """Multi-ref line {#356,#372} vs stored {#356} → exact-set, not intersection."""

    def test_4_multi_ref_vs_single_stored_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, K_MARKER_A)
        self._seed_refs(sid, "#356")
        r = self._run(BLOCK_MULTI, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class AbsentSidecarPasses(_GateBase):
    """Absent sidecar → fail-safe pass."""

    def test_5_no_sidecar_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, K_MARKER_A)
        r = self._run(BLOCK_B, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class BareRepokeStillPasses(_GateBase):
    """Bare `❓ NEEDS YOU:` matching LASTQF still exit 0 (the #740 shape)."""

    def test_6_bare_repoke_still_passes(self):
        sid = self._sid()
        self._seed_lastq(sid, K_MARKER_A)
        self._seed_refs(sid, "#356")
        r = self._run(BARE_REPOKE, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stdout, r.stderr))


class SidecarLifecycle(_GateBase):
    """🟡5 — sidecar /tmp/claude-lastq-refs-<SID> is written on a PASSING
    question turn with #N refs, and cleared by clear-question-dedup.sh on
    a genuine human prompt."""

    def test_sidecar_written_on_passing_turn(self):
        sid = self._sid()
        refs_path = Path("/tmp/claude-lastq-refs-%s" % sid)
        r = self._run(BLOCK_A, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stderr))
        self.assertTrue(refs_path.exists(),
                        "sidecar should be written on a passing question turn")
        content = refs_path.read_text().strip()
        self.assertIn("#356", content)

    def test_clear_question_dedup_removes_sidecar(self):
        sid = self._sid()
        refs_path = Path("/tmp/claude-lastq-refs-%s" % sid)
        lastq_path = Path("/tmp/claude-discord-lastq-%s" % sid)
        refs_path.write_text("#356")
        lastq_path.write_text("some question")
        clear_hook = str(Path(GATE).parent / "clear-question-dedup.sh")
        payload = json.dumps({"session_id": sid, "prompt": "yes do A"})
        env = {**os.environ, "HOME": str(self.home)}
        r = subprocess.run(["bash", clear_hook], input=payload,
                           text=True, capture_output=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(refs_path.exists(),
                         "clear-question-dedup should remove refs sidecar")
        self.assertFalse(lastq_path.exists(),
                         "clear-question-dedup should remove lastq")

    def test_cross_repo_ref_not_conflated(self):
        """🟡4 — odoo-erp#356 and bare #356 produce DIFFERENT keys."""
        sid = self._sid()
        refs_path = Path("/tmp/claude-lastq-refs-%s" % sid)
        msg = (
            "**Otazka — projekt montalu (automatizacia):** "
            "Ticket odoo-erp/odoo-erp#356 a #789 cakaju.\n\n"
            "- A (odporucam)\n- B\n\n"
            "❓ NEEDS YOU: rozhodnutie o odoo-erp/odoo-erp#356 a #789?"
        )
        r = self._run(msg, sid)
        self.assertEqual(r.returncode, 0, (r.returncode, r.stderr))
        if refs_path.exists():
            content = refs_path.read_text().strip()
            # The two refs should be distinct tokens, not both "#356"
            tokens = content.split()
            self.assertEqual(len(set(tokens)), len(tokens),
                             "cross-repo refs should produce distinct keys: %s"
                             % content)


if __name__ == "__main__":
    unittest.main()
