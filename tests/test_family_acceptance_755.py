"""#755 -- Rodinná (capability-group) akceptácia: jedno akceptačné vlákno je
akceptačný dôkaz pre N ticketov jednej dodanej capability rodiny + batchované
acceptance drafty v #606 fronte.

Owner-request (2026-08-30, odoo-erp montalu3 incident): ~20 needs-acceptance
ticketov jednej capability rodiny (#3315 SMS/e-mail komunikácia) čakalo KAŽDÝ
na vlastné akceptačné vlákno, hoci 3 ZDIEĽANÉ udalosti ich reálne pokryli;
#606 one-at-a-time doručovanie owner-otázok znamenalo, že fronta sa nikdy
nedrainovala (O(N) owner interakcií). Read-only audit našiel 14 ticketov,
ktorých akceptačný dôkaz UŽ existoval v zdieľanom vlákne, len nebol citovaný.

DOCS-ONLY ticket (vzor #728/#754): žiadna zmena kódu -- landuje ako doktrína
na dvoch vlastniacich surfaces + jeden behavioral regression-lock:

  1. `skills/odoo-discuss-xmlrpc/handover-compose.md` -- rodinná akceptácia +
     spätná citácia (`Acceptance-cited:` je DÔKAZ, nikdy dispozícia; rodinný
     close nesie VŽDY aj #627 `Discuss-defer:`/`Discuss-closed:`).
  2. `modules/core/statusline-vocabulary.md` -- rodinné batchovanie
     akceptačných draftov v #606 fronte (JEDEN návrh, deklaratívny zoznam,
     JEDNO ❓) + pointer na mechaniku v handover-compose.
  3. `discuss_close_guard.py` NEDOTKNUTÝ -- REGRESSION LOCK: close viazaný na
     vlákno, ktorý nesie len `Acceptance-cited:` bez #627 dispozície, MUSÍ
     ostať BLOKOVANÝ (pridanie `Acceptance-cited:` do `has_disposition` by
     porušilo #627 "posledná správa je vždy naša" -- design rozhodnutie (b)).

Content-lock pattern: window bound to the operative bullet's own start-anchor
(#498/#500) -> a PARTIAL revert still fails, not only a full deletion.
"""

import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"
VOCAB = ROOT / "modules" / "core" / "statusline-vocabulary.md"

sys.path.insert(0, str(ROOT))
import discuss_close_guard as dcg  # noqa: E402


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse whitespace runs (incl. a markdown line-wrap's newline+indent)
    to a single space, so a substring check survives prose re-wrapping."""
    return " ".join(text.split())


class FamilyAcceptanceBulletInHandoverCompose(TestCase):
    """Points 1 + 3 land as a new bullet in handover-compose.md, window-bound
    to its own `- **Rodinná (capability-group) akceptácia` start anchor so a
    partial revert (dropping one clause) still fails."""

    ANCHOR = "- **Rodinná (capability-group) akceptácia"

    def setUp(self):
        self.raw = read(COMPOSE)

    def _window(self):
        i = self.raw.index(self.ANCHOR)  # ValueError => full deletion => fail
        j = self.raw.find("\n- **", i + len(self.ANCHOR))
        if j == -1:
            j = len(self.raw)
        return norm(self.raw[i:j])

    def test_bullet_present(self):
        self.assertIn(self.ANCHOR, self.raw,
                      "family-acceptance bullet missing from handover-compose.md")

    def test_family_is_human_judgment_not_code_detection(self):
        w = self._window()
        for tok in ("#755", "capability rodiny", "JEDNO akceptačné vlákno"):
            self.assertIn(tok, w, "missing %r in family bullet" % tok)
        # family membership is a NAMED HUMAN JUDGMENT, never a code detector
        # (the #654 anti-heuristic line).
        self.assertIn("ĽUDSKÝ ÚSUDOK", w)
        self.assertIn("NIKDY kódová detekcia", w)

    def test_acceptance_cited_is_evidence_not_disposition(self):
        w = self._window()
        self.assertIn("Acceptance-cited:", w,
                      "the falsifiable citation format must be named")
        self.assertIn("DÔKAZ, NIKDY dispozícia", w,
                      "must state Acceptance-cited is evidence, never a disposition")
        # a family close STILL carries its #627 disposition; the last ticket
        # posts the closing note (Discuss-closed), non-last defers.
        self.assertIn("Discuss-defer:", w)
        self.assertIn("Discuss-closed:", w)
        self.assertIn("NEDOTKNUTÝ", w,
                      "must state discuss_close_guard.py stays untouched")

    def test_back_citation_same_cycle_close(self):
        w = self._window()
        # mandatory back-citation: cite on ALL family tickets in the SAME
        # cycle and close them, never a per-ticket wait (the 14x failure).
        self.assertIn("TOM ISTOM cykle", w)
        # strengthen past a bare token: lock the operative "never a per-ticket
        # wait" semantics so a meaning-inverting reword cannot survive.
        self.assertIn("nečaká na per-ticket", w)
        # a client emoji reaction is a valid acceptance event too (#745).
        self.assertIn("#745", w)


class FamilyBatchingClauseInStatuslineVocabulary(TestCase):
    """Point 2 (draft batching in the #606 queue) lands in statusline-
    vocabulary.md. Its bullets are single giant physical lines, so window by a
    bounded slice from the clause's own FINDER (a partial revert of a token
    inside the clause still fails; a full deletion makes .index raise)."""

    FINDER = "Rodinné batchovanie akceptačných draftov"

    def setUp(self):
        self.raw = read(VOCAB)

    def _window(self):
        i = self.raw.index(self.FINDER)  # ValueError => full deletion => fail
        return norm(self.raw[i:i + 1700])

    def test_clause_present(self):
        self.assertIn(self.FINDER, self.raw,
                      "#755 batching clause missing from statusline-vocabulary.md")

    def test_one_proposal_one_decision_declarative_list(self):
        w = self._window()
        for tok in ("#755", "#606", "JEDEN návrh", "DEKLARATÍVNE bullety"):
            self.assertIn(tok, w, "missing %r in batching clause" % tok)
        # the trap (d): a batched proposal must NOT be 3+ `#N ...?` per-ticket
        # asks (the #606 pile backstop) -- it is ONE decision.
        self.assertIn("#606 pile", w)
        self.assertIn("JEDNO `❓`", w)

    def test_pointer_to_handover_mechanics_and_wdrain(self):
        w = self._window()
        self.assertIn("Acceptance-cited:", w)
        self.assertIn("handover-compose.md", w,
                      "must point at the close-time mechanics")
        # consolidation actively pays down the #754 W-debt.
        self.assertIn("#754", w)


class CloseGuardTreatsAcceptanceCitedAsEvidenceNotDisposition(TestCase):
    """REGRESSION LOCK for design decision (b): `discuss_close_guard.py` stays
    untouched -- a thread-bound close carrying ONLY `Acceptance-cited:` (no
    #627 disposition) MUST still be BLOCKED. If someone ever added
    Acceptance-cited to `has_disposition`, a family could close every ticket
    by citation and never post the closing note, breaking the #627 "last
    message is always ours" invariant. This test has real behavioral teeth."""

    def _issue_json(self, body):
        return '{"body": %s, "comments": []}' % _jsonstr(body)

    def test_thread_bound_acceptance_cited_only_is_blocked(self):
        body = (
            "Rodinný close.\n"
            "Akceptačné vlákno: https://erp.montalu.cloud/odoo/discuss"
            "?active_id=discuss.channel_288\n"
            'Acceptance-cited: vlákno "Zákaznícke e-maily 1" '
            "(discuss.channel_288) / msg 1739648 / Špetta 2026-08-29\n"
        )
        # thread-bound via the deep-URL token, NO Discuss-closed/Discuss-defer.
        self.assertTrue(dcg.is_thread_bound(body))
        self.assertFalse(dcg.has_disposition(body),
                         "Acceptance-cited: must NOT count as a disposition")
        self.assertEqual(dcg.evaluate_close(self._issue_json(body)),
                         "thread-bound-no-closing-note",
                         "a family close with only Acceptance-cited must BLOCK")

    def test_family_close_with_defer_disposition_is_allowed(self):
        body = (
            "Ne-posledný rodinný ticket.\n"
            "Akceptačné vlákno: discuss.channel_288\n"
            'Acceptance-cited: vlákno "Zákaznícke e-maily 1" '
            "(discuss.channel_288) / msg 1739648 / Špetta 2026-08-29\n"
            "Discuss-defer: siblings #201 #202 still open — note goes at the last close\n"
        )
        self.assertTrue(dcg.is_thread_bound(body))
        self.assertTrue(dcg.has_disposition(body))
        self.assertIsNone(dcg.evaluate_close(self._issue_json(body)),
                          "a family close that ALSO carries Discuss-defer must ALLOW")


# NOTE: the handover-compose injection-body-cap lock (this #755 bullet grew the
# file toward MAX_BODY) lives in the SIBLING test
# `test_handover_proposal_rules.py::TestCompanionUnderInjectorBodyCap` (#628),
# which already reads MAX_BODY from the real hook — the #755 change only added a
# 300-char cushion there. Kept single to honour the repo's dedup discipline
# (adversarial-review NIT); do not re-add a duplicate here.


def _jsonstr(s):
    import json
    return json.dumps(s)


if __name__ == "__main__":
    main()
