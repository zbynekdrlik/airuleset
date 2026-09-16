"""#1018 — `stop-check-prose-violations.sh` must block a turn that REPORTS
moving a client task to the Verifikácia / Na overenie stage and posting the
handover note WITHOUT the four mandatory sections (Čo / Kde / Čo skúsiť /
stačí 👍 — client-board-tasks.md rule 3).

Same information space + self-report shape as the #916/#978 checks: the Stop
hook reads ONLY last_assistant_message, so it fires on the assistant's own
report of the post and requires the distinctive section markers to be present
(or an UNVERIFIED: escape). NARROW: an Odoo/task anchor + a past-tense move/post
verb near the stage name — a message that merely MENTIONS the stage is not gated.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"


def _run(msg, sid=None):
    sid = sid or ("verifshape1018-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# ---- BLOCK: a Verifikácia post report WITHOUT the four sections ---- #
# Each carries read-back evidence ("0 escaped") so the sibling #916 check is
# satisfied and the ONLY possible blocker is the missing Verifikácia sections —
# isolating THIS check.

SK_MOVED_NO_SECTIONS = (
    "Presunul som úlohu na klientskom project.task boarde do stage Verifikácia "
    "a odoslal som handover poznámku klientovi na montalu PROD. "
    "Read-back: 0 escaped správ, body_is_html verified."
)

SK_NA_OVERENIE_NO_SECTIONS = (
    "Posunul som úlohu do Na overenie a napísal som poznámku do chatteru "
    "project.task na slovnormal boarde. Read-back: 0 escaped."
)


# ---- ALLOW: the report carries the full four-section shape ---- #

SK_MOVED_WITH_SECTIONS = (
    "Presunul som úlohu do Verifikácia na project.task boarde a odoslal handover "
    "poznámku. Read-back: 0 escaped správ.\n"
    "Čo: mapa trás je nasadená.\n"
    "Kde: Predaj ▸ Trasy — https://erp.montalu.cloud/odoo/action-123/45\n"
    "Čo skúsiť: otvorte trasu a skúste vyhľadávanie vpravo.\n"
    "stačí 👍"
)

SK_MOVED_UNVERIFIED = (
    "Presunul som úlohu do Verifikácia a odoslal poznámku. "
    "UNVERIFIED: nemám prístup na PROD na read-back obsahu poznámky."
)


# ---- not gated: merely mentioning the stage, no post report ---- #

SK_JUST_MENTIONS_STAGE = (
    "Ďalej presuniem úlohu do Verifikácia keď klient potvrdí. Zatiaľ je vo fáze "
    "Realizácia na project.task boarde."
)

SK_NON_ODOO = (
    "Moved the deployment task to the verification stage in our internal CI "
    "pipeline and posted the result to the build log."
)


class TestVerifikaciaShapeGate(TestCase):

    def test_moved_without_sections_blocked(self):
        self.assertTrue(_blocked(_run(SK_MOVED_NO_SECTIONS)))

    def test_na_overenie_without_sections_blocked(self):
        self.assertTrue(_blocked(_run(SK_NA_OVERENIE_NO_SECTIONS)))

    def test_moved_with_sections_not_blocked_for_verif(self):
        p = _run(SK_MOVED_WITH_SECTIONS)
        self.assertNotIn("Verifik", p.stdout + p.stderr if _blocked(p) else "")
        self.assertFalse(_blocked(p), p.stdout)

    def test_unverified_escape_allowed(self):
        self.assertFalse(_blocked(_run(SK_MOVED_UNVERIFIED)), )

    def test_mere_mention_not_gated(self):
        self.assertFalse(_blocked(_run(SK_JUST_MENTIONS_STAGE)))

    def test_non_odoo_verification_not_gated(self):
        self.assertFalse(_blocked(_run(SK_NON_ODOO)))


if __name__ == "__main__":
    main()
