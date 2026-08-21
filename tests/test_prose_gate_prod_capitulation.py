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
# #608-review MAJOR #4: the incident's own verb `potvrdiť` (confirm) must be
# covered (EN already had `confirm`).
SK_POTVRDIT = (
    "Neviem to na prode potvrdiť — netuším či prišli notifikácie klientovi."
)
# #608-review MAJOR #3: a capitulation that merely NAMES the RO channel it
# gave up on (after one 500) must STILL block — naming the channel is not a
# self-service SUCCESS.
SK_RO_GAVE_UP = (
    "RO read-only kanál vrátil 500, takže neviem overiť členstvo na prode."
)

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

    def test_sk_potvrdit_verb_is_blocked(self):
        # #608-review MAJOR #4: "neviem … na prode potvrdiť" must block.
        self.assertTrue(_blocked(_run(SK_POTVRDIT)))

    def test_ro_channel_named_but_gave_up_is_blocked(self):
        # #608-review MAJOR #3: naming the RO channel it surrendered on is NOT
        # self-service evidence — must still block.
        self.assertTrue(
            _blocked(_run(SK_RO_GAVE_UP)),
            "a capitulation that gave up after one 500 on the RO channel must "
            "block — naming the channel is not a successful self-service read")

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
# #608-review CRITICAL #1: "nevidím dôvod" + a DATA-noun substring
# (`stav`⊂`nastaviť`) + prod must PASS (the see-branch was dropped; without an
# unanchored substring match, these are ordinary prose).
NEVIDIM_DOVOD_NASTAVIT = "Nevidím dôvod to na prode nastaviť inak."
SUCCESS_WITH_DATA_NOUN = (
    "Nasadenie na prode prebehlo, notifikácie chodia, nevidím dôvod niečo "
    "meniť.\n\n✅ DONE: hotové."
)
PAST_TENSE_SUCCESS = (
    "Overil som na prode, že notifikácia odišla, ale nevidím dôvod na obavy."
)
# A pure see-based read with NO verify verb — now a documented RESIDUAL
# (dropped see-branch), must PASS.
PURE_SEE_RESIDUAL = "na prode nevidím kontrolné kópie mailov"
# #608-review MAJOR #2: EN metaphors ("can't see us shipping … on prod",
# `foresee`) must PASS — the idiom is right-anchored to "what's on prod".
EN_CANT_SEE_SHIPPING = "I can't see us shipping this on prod before Friday."
EN_FORESEE = "I can't foresee problems on prod."
# --- round-2 review controls ---------------------------------------------- #
# CRITICAL #1: `nevidím` + a present/infinitive verify verb + prod in a benign
# SUCCESS report must PASS (the SEE stem was removed from the CANT set).
NEVIDIM_SUCCESS_WITH_VERIFY = (
    "Na prode nevidím žiadny problém, všetko beží; ešte to idem overiť na dev."
)
NEVIDIM_NEW_VERSION = (
    "Na prode je nová verzia. Nevidím dôvod ju meniť. Ešte idem overiť logy."
)
# MAJOR #2: the doctrine's OWN self-service method names, phrased naturally,
# must DISARM (a fresh copy of production / the read-only handover account).
EN_FRESH_COPY_SELFSERVICE = (
    "I couldn't verify prod membership via the API, so I pulled a fresh copy "
    "of production and read the DB directly — the user IS in the group."
)
EN_RO_HANDOVER_SELFSERVICE = (
    "I can't verify on prod directly; I used the read-only handover account "
    "instead and confirmed it."
)
# MINOR #4: the genitive `čerstvej kópie` must disarm too.
SK_GENITIVE_FRESH_COPY = (
    "Neviem na prode overiť členstvo, tak čítam z čerstvej kópie produ "
    "res.partner."
)
# MAJOR #3: outage / bug reports whose subject is users/systems (not the
# agent) must PASS — `access` was dropped from the EN verb set.
EN_OUTAGE_ACCESS = "Customers cannot access production checkout — rolling back."
EN_USERS_ACCESS = (
    "Users can't access prod dashboards after the deploy — investigating the "
    "502."
)


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

    def test_nevidim_dovod_with_data_noun_substring_passes(self):
        # #608-review CRITICAL #1: "nevidím dôvod … nastaviť" (stav⊂nastaviť)
        # + prode must PASS — the metaphor-prone see-branch was dropped.
        self.assertFalse(
            _blocked(_run(NEVIDIM_DOVOD_NASTAVIT)),
            "an unanchored data-noun substring in ordinary prose must not "
            "false-block")

    def test_success_report_with_data_noun_passes(self):
        self.assertFalse(
            _blocked(_run(SUCCESS_WITH_DATA_NOUN)),
            "a legitimate success report mentioning notifikácie + prod must "
            "not false-block")

    def test_past_tense_success_report_passes(self):
        # "Overil som … na prode" — past tense is not a present/infinitive
        # verify verb, so no capitulation cluster fires.
        self.assertFalse(_blocked(_run(PAST_TENSE_SUCCESS)))

    def test_pure_see_no_verify_verb_is_residual_and_passes(self):
        # The dropped see-branch: a pure see-based read with no verify verb is
        # now a documented residual — must PASS.
        self.assertFalse(_blocked(_run(PURE_SEE_RESIDUAL)))

    def test_en_cant_see_shipping_metaphor_passes(self):
        # #608-review MAJOR #2: "can't see us shipping … on prod" is a
        # scheduling opinion, not a state read.
        self.assertFalse(_blocked(_run(EN_CANT_SEE_SHIPPING)))

    def test_en_foresee_passes(self):
        # `\bsee\b` must not match inside `foresee`.
        self.assertFalse(_blocked(_run(EN_FORESEE)))

    def test_nevidim_success_with_verify_verb_passes(self):
        # #608-review-2 CRITICAL #1: "Na prode nevidím žiadny problém … idem
        # overiť na dev" is a benign success report — the SEE stem was removed
        # from the CANT set so it must PASS.
        self.assertFalse(
            _blocked(_run(NEVIDIM_SUCCESS_WITH_VERIFY)),
            "a benign 'nevidím … overiť … prod' success report must not block")

    def test_nevidim_new_version_with_verify_passes(self):
        self.assertFalse(_blocked(_run(NEVIDIM_NEW_VERSION)))

    def test_en_fresh_copy_selfservice_disarms(self):
        # #608-review-2 MAJOR #2: the doctrine's OWN fresh-copy method must
        # disarm even without the REFRESH-DEV-BOX-FROM-PROD token.
        self.assertFalse(
            _blocked(_run(EN_FRESH_COPY_SELFSERVICE)),
            "'pulled a fresh copy of production' is the doctrine's own method "
            "— must disarm")

    def test_en_ro_handover_selfservice_disarms(self):
        self.assertFalse(
            _blocked(_run(EN_RO_HANDOVER_SELFSERVICE)),
            "'used the read-only handover account' is self-service — must "
            "disarm")

    def test_sk_genitive_fresh_copy_disarms(self):
        # #608-review-2 MINOR #4: čerstvej kópie (genitive) must disarm.
        self.assertFalse(_blocked(_run(SK_GENITIVE_FRESH_COPY)))

    def test_en_outage_access_report_passes(self):
        # #608-review-2 MAJOR #3: "customers cannot access production" is an
        # outage report — `access` was dropped from the verb set.
        self.assertFalse(
            _blocked(_run(EN_OUTAGE_ACCESS)),
            "an outage report about customers must not block")

    def test_en_users_access_report_passes(self):
        self.assertFalse(_blocked(_run(EN_USERS_ACCESS)))


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
