"""#608 — `stop-check-prose-violations.sh` must block an OWNER-CHAT
prod-read CAPITULATION: a claim to the owner that prod state cannot be
read/verified/seen, with NO evidence of a self-service attempt in the same
message. Third recurrence of the #500 class (montalu3 2026-08-21 told the
owner it could not verify prod notifications, though it had
`REFRESH-DEV-BOX-FROM-PROD`).

#516 gated only the `gk-request` FILING path
(`block-gk-request-without-selfservice.sh`); the OWNER-CHAT path had no gate,
so the prose in `autonomous-verification.md` ("What's on PROD?" is a
self-service question) failed a third time. This detector makes the owner-chat
claim mechanical, following the #319 empirical methodology: fixtures in BOTH
languages proven blocked, false-positive controls proven passing, accepted
residuals documented at the hook.

The detector is DISARMED (owner did the right thing) when the message shows a
self-service attempt (REFRESH-DEV-BOX-FROM-PROD / a fresh prod copy /
Self-service-checked: / an RO-channel read / has_group/search_read) OR an
explicit `UNVERIFIED:` line — the same escape family the sibling
tester-handoff detector already uses.
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
AV = ROOT / "modules" / "core" / "autonomous-verification.md"


def _run(msg, sid=None):
    sid = sid or ("prosegate608-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# --- Incident-shaped BLOCK fixtures (both languages) ---------------------- #

# The montalu3 formulation VERBATIM in shape: a first-reaction capitulation to
# the owner, no self-service, no UNVERIFIED.
SK_INCIDENT = (
    "Neviem to na prode zistiť — nevidím tam za notifikácie, "
    "či owner naozaj dostal kontrolnú kópiu e-mailu."
)
SK_NEDA_SA = (
    "Na prode sa to nedá overiť, netuším či prišla kontrolná kópia mailu."
)
SK_SEE_DATA = "na prode nevidím kontrolné kópie mailov"

# The #500 formulation: "membership on PROD I cannot verify".
EN_500 = "The membership on PROD I cannot verify — no read-back available."
EN_SEE_IDIOM = "I can't see what's on prod right now."
EN_CANNOT_VERIFY = "Cannot verify on PROD whether the notification was sent."


class ProdCapitulationIsBlocked(TestCase):
    """The incident-shaped claims — SK + EN — must be blocked."""

    def test_sk_incident_is_blocked(self):
        p = _run(SK_INCIDENT)
        self.assertTrue(
            _blocked(p),
            "the montalu3 SK capitulation ('neviem na prode zistiť', no "
            "self-service) must block. stdout=%r stderr=%r"
            % (p.stdout[:200], p.stderr.strip()[-400:]))
        self.assertEqual(p.returncode, 0, "a block must exit 0, not error")

    def test_sk_detached_reflexive_nedasa_is_blocked(self):
        # "sa to nedá overiť" — the reflexive clitic is DETACHED from "nedá";
        # the detector must still fire on the standalone negation.
        self.assertTrue(_blocked(_run(SK_NEDA_SA)))

    def test_sk_see_data_noun_is_blocked(self):
        # "nevidím … kópie mailov" — the see-negation + a DATA noun, no
        # explicit verify verb, still a prod-state-read capitulation.
        self.assertTrue(_blocked(_run(SK_SEE_DATA)))

    def test_en_500_formulation_is_blocked(self):
        self.assertTrue(
            _blocked(_run(EN_500)),
            "the #500 EN formulation ('membership on PROD I cannot verify') "
            "must block")

    def test_en_see_idiom_is_blocked(self):
        self.assertTrue(_blocked(_run(EN_SEE_IDIOM)))

    def test_en_cannot_verify_on_prod_is_blocked(self):
        self.assertTrue(_blocked(_run(EN_CANNOT_VERIFY)))


# --- False-positive controls: these must PASS (never block) --------------- #

REFRESH_PRESENT = (
    "Neviem to na prode zistiť priamo, tak si spravím čerstvú kópiu produ "
    "cez REFRESH-DEV-BOX-FROM-PROD a prečítam mail_mail."
)
UNVERIFIED_HONEST = (
    "UNVERIFIED: notifikácie na prode neviem overiť — RO kanál aj čerstvá "
    "kópia produ zlyhali (500 na oboch, telo som prečítal)."
)
EN_SELF_SERVICE = (
    "I cannot verify prod membership directly, so I refreshed a fresh copy of "
    "prod via REFRESH-DEV-BOX-FROM-PROD and read has_group."
)
ORDINARY_PROSE = (
    "Nasadil som na prod, verzia 1.2.3 je živá, notifikácie fungujú.\n\n"
    "✅ DONE: nasadené a overené."
)
EN_ORDINARY = "I can see prod is deployed and the version label reads v1.2.3."
NEVIDIM_DOVOD = "Nevidím dôvod meniť to na prode.\n\n✅ DONE: hotové."
EN_CANT_SEE_WHY = "I can't see why we'd change prod deployment right now."
SK_DECISION = "Neviem, či to na prode necháme alebo zmeníme neskôr?"
EN_CANT_REPRODUCE = "I can't reproduce the crash on prod, but the fix is committed."


class ProdCapitulationFalsePositiveControls(TestCase):
    """Legitimate messages about prod must never block."""

    def test_refresh_mention_disarms(self):
        self.assertFalse(
            _blocked(_run(REFRESH_PRESENT)),
            "a message that ATTEMPTS a fresh prod copy (REFRESH-DEV-BOX-FROM-"
            "PROD) must pass — it is doing the right thing")

    def test_honest_unverified_disarms(self):
        self.assertFalse(
            _blocked(_run(UNVERIFIED_HONEST)),
            "an explicit UNVERIFIED after exhausting the self-service path "
            "must pass")

    def test_en_self_service_disarms(self):
        self.assertFalse(_blocked(_run(EN_SELF_SERVICE)))

    def test_ordinary_prod_prose_passes(self):
        self.assertFalse(
            _blocked(_run(ORDINARY_PROSE)),
            "ordinary deploy prose about prod (no capitulation) must pass")

    def test_en_ordinary_prod_prose_passes(self):
        self.assertFalse(_blocked(_run(EN_ORDINARY)))

    def test_metaphorical_nevidim_dovod_passes(self):
        # "Nevidím dôvod … na prode" — see-negation + prod, but the object is
        # "dôvod" (reason), not a data noun → must NOT block.
        self.assertFalse(
            _blocked(_run(NEVIDIM_DOVOD)),
            "'Nevidím dôvod … na prode' is metaphorical, not a prod-state read")

    def test_en_cant_see_why_passes(self):
        # "can't see why … prod" — the on-prod idiom is absent → must pass.
        self.assertFalse(_blocked(_run(EN_CANT_SEE_WHY)))

    def test_sk_decision_question_passes(self):
        # a decision question about prod, no verify/read verb → must pass.
        self.assertFalse(_blocked(_run(SK_DECISION)))

    def test_en_cant_reproduce_passes(self):
        # "can't reproduce … on prod" — "reproduce" is not a read/verify verb.
        self.assertFalse(_blocked(_run(EN_CANT_REPRODUCE)))


class ProdCapitulationHookContract(TestCase):
    """The hook-contract shape: a block is JSON on stdout + exit 0, with the
    human-readable VIOLATION + decision tree on stderr."""

    def test_block_is_json_stdout_exit0_with_stderr_detail(self):
        p = _run(SK_INCIDENT)
        self.assertEqual(p.returncode, 0)
        self.assertIn('"decision"', p.stdout)
        self.assertIn('"block"', p.stdout)
        self.assertIn("self-service", p.stderr.lower())
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD", p.stderr)

    def test_mention_in_backticks_does_not_block(self):
        # A message that merely QUOTES the banned phrase (documenting the rule)
        # is a MENTION, stripped before matching — must not block.
        msg = (
            "Pridal som detektor, ktorý blokuje frázu `neviem na prode "
            "zistiť` keď chýba self-service dôkaz.\n\n✅ DONE: hotové."
        )
        self.assertFalse(
            _blocked(_run(msg)),
            "a backticked MENTION of the banned phrase must not block")


class ProdCapitulationDoctrinePointer(TestCase):
    """#608 doctrine: autonomous-verification.md must carry the pointer that
    the owner-chat claim is now hook-gated (a UNIQUE operative anchor, so a
    partial revert of that clause loses the test's teeth too)."""

    def test_autonomous_verification_names_the_owner_chat_gate(self):
        text = AV.read_text(encoding="utf-8")
        self.assertIn("#608", text,
                      "autonomous-verification.md must reference #608")
        # a unique operative phrase from the pointer clause
        self.assertIn("OWNER-CHAT path is now ALSO hook-gated", text,
                      "the #608 owner-chat gate pointer must be present")
        self.assertIn("stop-check-prose-violations.sh", text,
                      "the pointer must name the enforcing hook")


if __name__ == "__main__":
    main()
