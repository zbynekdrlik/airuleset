"""#788 + #799 — fleet close-flow: tacit-acceptance closure + TTL-hide disposition.

These two bundled tickets edit the SAME close-flow passage in
`skills/odoo-discuss-xmlrpc/handover-compose.md` and the `· W N` bullet of the
always-on `modules/core/statusline-vocabulary.md`.

  #788 — after the #627 closing note, the thread is SELF-HIDDEN on a TTL
         (odoo-erp#5630 `company_base_close_hide_at` primitive), NEVER archived
         (`active=False`); archival stays only as fallback/gk cleanup. A client
         reply mid-TTL DISARMS the hide.
  #799 — a delivered+reminded client thread that stays silent N=3 working days
         is closed TACITLY (`Acceptance-tacit:` citation) with the closing note
         as the last message; disposition is DEFERRED to #788's TTL-hide, never
         hardcoded "archív". Plus a standing `template:<type>` owner-approval
         grant for the two mechanical message types.

Window-teeth (#500/#532/#573): each operative bullet is located by a UNIQUE
start anchor and bounded at the next `- **`, so a partial revert of one bullet's
operative tokens fails its own lock.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"
STATUS = ROOT / "modules" / "core" / "statusline-vocabulary.md"


def _norm_window(text, start_token, end_marker="\n- **"):
    i = text.index(start_token)
    j = text.find(end_marker, i)
    return " ".join(text[i:(j if j > 0 else len(text))].split())


def _line_with(text, finder):
    for ln in text.splitlines():
        if finder in ln:
            return ln
    return ""


class Test799ClosureBullet(unittest.TestCase):
    ANCHOR = "- **Closure protokol"

    def setUp(self):
        self.win = _norm_window(COMPOSE.read_text(encoding="utf-8"), self.ANCHOR)

    def test_bullet_present(self):
        self.assertTrue(self.win, "the #799 closure-protokol bullet must exist")
        self.assertIn("airuleset #799", self.win)

    def test_one_reminder_then_tacit_close_tokens(self):
        for tok in ("JEDNA vecná pripomienka", "#607", "N = 3 PRACOVNÉ dni",
                    "Acceptance-tacit:", "TERMINÁLNY stav"):
            self.assertIn(tok, self.win,
                          "#799 closure bullet lost operative token %r" % tok)

    def test_disposition_deferred_to_788_never_hardcoded_archive(self):
        # the shared seam: closure defers disposition to #788, never "archív".
        self.assertIn("deferuj ju na #788 TTL-hide", self.win)
        self.assertIn("NEhardcoduj ako „archív\"", self.win)

    def test_stale_escalation_terminates_in_closure(self):
        self.assertIn("`stale!` eskalácia (#570) KONČÍ týmto closure", self.win)

    def test_mid_window_reply_cancels_tacit_close(self):
        # lock the operative negation: a client reply mid-window must NOT be
        # tacitly closed, and silence must exclude a #745 reaction (review 🟡).
        self.assertIn("NEuzatváraj tacitne", self.win)
        self.assertIn("#745", self.win)

    def test_acceptance_tacit_is_evidence_not_disposition(self):
        # must still carry a #627 disposition (mirrors #755 Acceptance-cited).
        self.assertIn("`Acceptance-tacit:` je DÔKAZ, nie dispozícia", self.win)
        self.assertIn("Discuss-defer:", self.win)
        self.assertIn("Discuss-closed:", self.win)

    def test_template_grant_scoped_to_two_types(self):
        self.assertIn("template:final-reminder", self.win)
        self.assertIn("template:closing-note", self.win)
        self.assertIn("template:<iný>", self.win)
        self.assertIn("STANDING template grant", self.win)

    def test_topic_hygiene_redirect_nuance(self):
        # the #728 redirect extension: peel a new topic during closure.
        self.assertIn("peeluj ju per #728", self.win)
        self.assertIn("redirect odpoveďou", self.win)


class Test788DispositionBullet(unittest.TestCase):
    ANCHOR = "- **Disposition po uzatváracej správe"

    def setUp(self):
        self.win = _norm_window(COMPOSE.read_text(encoding="utf-8"), self.ANCHOR)

    def test_bullet_present(self):
        self.assertTrue(self.win, "the #788 disposition bullet must exist")
        self.assertIn("airuleset #788", self.win)

    def test_ttl_hide_not_archive(self):
        for tok in ("SAMO-SCHOVANIE (TTL)", "NIKDY `active=False`",
                    "_company_base_schedule_close_hide", "mail.closed_thread_hide_hours",
                    "unpin_dt"):
            self.assertIn(tok, self.win,
                          "#788 disposition bullet lost token %r" % tok)

    def test_archive_is_fallback_only(self):
        self.assertIn("Archivácia (`active=False`) ostáva LEN ako fallback / gk cleanup",
                      self.win)

    def test_disarm_on_reply_decision(self):
        for tok in ("Disarm-on-reply", "DISARMuje hide",
                    "nikdy ticho nezmizne", "EXPLICITNE",
                    # lock the NEGATION itself so an inversion ("pokojne sa
                    # spoliehaj na the race") cannot pass the teeth (review 🟡).
                    "nikdy sa nespoliehaj na `last_interest_dt` race"):
            self.assertIn(tok, self.win,
                          "#788 disposition bullet lost disarm token %r" % tok)

    def test_mechanism_released_cited(self):
        self.assertIn("odoo-erp issue 5630", self.win)
        self.assertIn("19.0.2.230.0", self.win)


class TestStatuslineTacitTerminal(unittest.TestCase):
    def test_W_bullet_names_tacit_terminal(self):
        line = _line_with(STATUS.read_text(encoding="utf-8"), "`· W N` (#510")
        self.assertTrue(line, "the `· W N` bullet must be a single line")
        for tok in ("Tacit terminál", "#799", "Acceptance-tacit:",
                    "N=3 pracovné dni", "tacitným uzavretím",
                    "NIE JE donekonečna"):
            self.assertIn(tok, line,
                          "W bullet lost tacit-terminal token %r" % tok)

    def test_points_at_handover_compose_for_mechanics(self):
        line = _line_with(STATUS.read_text(encoding="utf-8"), "`· W N` (#510")
        self.assertIn("skills/odoo-discuss-xmlrpc/handover-compose.md", line)


if __name__ == "__main__":
    unittest.main()
