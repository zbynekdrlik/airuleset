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


if __name__ == "__main__":
    unittest.main()
