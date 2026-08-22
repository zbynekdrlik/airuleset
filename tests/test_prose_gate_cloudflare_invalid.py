"""#631 — `stop-check-prose-violations.sh` must block an OWNER-CHAT claim that
a Cloudflare credential is INVALID / non-functional when the SAME message shows
NO capability probe.

The owner deleted his master Cloudflare token from Bitwarden because a session
declared it invalid — and Cloudflare never shows a token value twice, so it is
gone. The claim was wrong: `GET /user/tokens/verify` returns `Invalid API
Token` for account-owned `cfat_` tokens BY DESIGN. The knowledge to avoid this
(skills/cloudflare-api-tokens §0/§2) existed and did not reach the moment of
decision. This detector makes the owner-chat claim mechanical, exactly the way
the #608 prod-read-capitulation sibling does.

Design (mirrors #608 with ONE deliberate difference):
  * MATCH runs on the MENTION-stripped message (MSG_MENTION), so a message that
    merely QUOTES the error string (`Invalid API Token` in backticks) or
    discusses the endpoint in the abstract is NOT blocked — the #96 use-vs-
    mention discipline.
  * ESCAPE (the capability-probe evidence) runs on the RAW message, because the
    probe is a curl that normally lives inside a code fence / quoted string,
    which MSG_MENTION would strip. This is the deliberate difference from #608
    (whose escape tokens are prose markers, so it runs on the stripped text).
  * The probe that disarms is a REAL capability probe — GET /zones or
    GET /accounts/{id}/tokens/verify, the phrase "capability probe", or an
    explicit UNVERIFIED: — never `/user/tokens/verify`, because treating that
    endpoint's answer as a verdict IS the error.
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
SKILL = ROOT / "skills" / "cloudflare-api-tokens" / "SKILL.md"


def _run(msg, sid=None):
    sid = sid or ("prosegate631-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# --- BLOCK fixtures: an invalid-credential claim with NO capability probe --- #

SK_NEFUNGUJE = "Ten Cloudflare token nefunguje, vyhlasujem ho za neplatný."
SK_NEPLATNY = "Cloudflare API token je neplatný, netreba ho skúšať ďalej — treba nový."
EN_INVALID = "The Cloudflare token is invalid — I'm going to ask you for a new one."
EN_DOESNT_WORK = "The Cloudflare API token doesn't work, we need a fresh one."
# THE EXACT INCIDENT: treating /user/tokens/verify's answer as the verdict.
INCIDENT_VERIFY = (
    "I checked the Cloudflare token with GET /user/tokens/verify and it "
    "returned Invalid API Token, so the token is invalid — please regenerate it."
)
SK_WRANGLER = "wrangler deploy zlyhal, ten API token je neplatný, potrebujem nový."
SK_CFAT = "Ten cfat_ token je nefunkčný, netuším prečo, asi ho treba vygenerovať znova."


class CloudflareInvalidIsBlocked(TestCase):
    def test_sk_nefunguje_is_blocked(self):
        p = _run(SK_NEFUNGUJE)
        self.assertTrue(
            _blocked(p),
            "'Cloudflare token nefunguje ... neplatný' with no probe must block. "
            "stdout=%r stderr=%r" % (p.stdout[:200], p.stderr.strip()[-400:]))
        self.assertEqual(p.returncode, 0, "a block must exit 0, not error")

    def test_sk_neplatny_is_blocked(self):
        self.assertTrue(_blocked(_run(SK_NEPLATNY)))

    def test_en_invalid_is_blocked(self):
        self.assertTrue(_blocked(_run(EN_INVALID)))

    def test_en_doesnt_work_is_blocked(self):
        self.assertTrue(_blocked(_run(EN_DOESNT_WORK)))

    def test_incident_verify_endpoint_as_verdict_is_blocked(self):
        # The exact loss: /user/tokens/verify used as a verdict. It is NOT a
        # capability probe, so it must NOT disarm — the claim must block.
        self.assertTrue(
            _blocked(_run(INCIDENT_VERIFY)),
            "treating /user/tokens/verify's answer as the invalid verdict must "
            "block — that endpoint is not a capability probe")

    def test_sk_wrangler_invalid_is_blocked(self):
        self.assertTrue(_blocked(_run(SK_WRANGLER)))

    def test_sk_cfat_nonfunctional_is_blocked(self):
        self.assertTrue(_blocked(_run(SK_CFAT)))


# --- PASS fixtures: probe-backed / honest / unrelated / quoted -------------- #

EN_PROBE_ZONES = (
    "The Cloudflare token is invalid — the capability probe "
    "GET https://api.cloudflare.com/client/v4/zones returned success:false."
)
SK_PROBE_ZONES = (
    "Cloudflare token je neplatný — capability probe na /client/v4/zones vrátil "
    "success:false, idem vyrobiť nový."
)
# The CORRECT account-verify endpoint IS a probe (unlike /user/tokens/verify).
EN_ACCOUNTS_VERIFY = (
    "The Cloudflare cfat_ token is invalid — GET /accounts/abc123def/tokens/verify "
    "returned error 1000 as well."
)
UNVERIFIED_HONEST = (
    "UNVERIFIED: I can't tell if the Cloudflare token is invalid — no network "
    "access to api.cloudflare.com from this session."
)
NON_CF_TOKEN_CLAIM = "The GitHub token is invalid, please regenerate the PAT."
CF_VALID = (
    "The Cloudflare token is valid — the /zones capability probe returned "
    "success:true and 3 zones."
)
CF_DEPLOY_OK = "Nasadil som cez wrangler, deploy prešiel, verzia je živá."
SK_DECISION = "Neviem, či ten Cloudflare token použijeme na spinbike alebo montalu?"
# The realistic ABSTRACT discussion: the endpoint response is backticked, so
# MSG_MENTION strips it and the invalid-near-token cluster never forms.
ABSTRACT_BACKTICKED = (
    "Pozor na pascu: `/user/tokens/verify` vracia `Invalid API Token` pre "
    "`cfat_` tokeny by design — jeho odpoveď nie je verdikt.\n\n✅ DONE: hotové."
)
# A message DESCRIBING this very gate (the #96 use-vs-mention case).
GATE_DESCRIPTION = (
    "Pridal som gate, ktorý blokuje tvrdenie `Cloudflare token je neplatný` bez "
    "doloženého capability probe.\n\n✅ DONE: hotové."
)


class CloudflareInvalidFalsePositiveControls(TestCase):
    def test_en_probe_zones_disarms(self):
        self.assertFalse(
            _blocked(_run(EN_PROBE_ZONES)),
            "an invalid claim BACKED by a /zones capability probe must pass")

    def test_sk_probe_zones_disarms(self):
        self.assertFalse(_blocked(_run(SK_PROBE_ZONES)))

    def test_accounts_verify_endpoint_disarms(self):
        # /accounts/{id}/tokens/verify is the CORRECT verify for a cfat_ token —
        # it counts as a probe (unlike /user/tokens/verify).
        self.assertFalse(
            _blocked(_run(EN_ACCOUNTS_VERIFY)),
            "the account-scoped verify endpoint is a legitimate probe")

    def test_unverified_honest_disarms(self):
        self.assertFalse(_blocked(_run(UNVERIFIED_HONEST)))

    def test_github_token_invalid_passes(self):
        self.assertFalse(
            _blocked(_run(NON_CF_TOKEN_CLAIM)),
            "a non-Cloudflare token claim must not fire this gate")

    def test_cloudflare_valid_passes(self):
        self.assertFalse(_blocked(_run(CF_VALID)))

    def test_cloudflare_deploy_prose_passes(self):
        self.assertFalse(_blocked(_run(CF_DEPLOY_OK)))

    def test_cloudflare_decision_question_passes(self):
        self.assertFalse(_blocked(_run(SK_DECISION)))

    def test_abstract_backticked_discussion_passes(self):
        # boundary: "discussing token validity in the abstract" must not block —
        # the realistic form backticks the error string / endpoint.
        self.assertFalse(
            _blocked(_run(ABSTRACT_BACKTICKED)),
            "an abstract discussion that backticks the error string must pass")

    def test_backticked_gate_description_passes(self):
        self.assertFalse(
            _blocked(_run(GATE_DESCRIPTION)),
            "a backticked MENTION describing the gate must not block")


# --- review r2 counterexamples: cross-service / unrelated-topic / benign ---- #
# These must PASS. Before the r2 fix (CF_SIGNAL decoupled from CF_NEAR) they
# WRONGLY BLOCKED. The MATCH now requires a CLOUDFLARE-QUALIFIED credential.
XSERVICE_GITHUB_PLUS_CF = (
    "The GitHub token is invalid, please regenerate the PAT. Separately, I "
    "finished the Cloudflare DNS setup and the site resolves."
)
UNRELATED_SORTKEY_PLUS_CF = (
    "Moved the site behind Cloudflare last week. Separately, the sort key on the "
    "events table is invalid after the migration, so I re-ran the index rebuild."
)
CF_KEY_METRIC = (
    "Cloudflare Analytics shows the key metric for cache-hit-ratio is invalid "
    "right now because the beacon script failed to load."
)
BENIGN_REVOKED = (
    "Cleanup: I revoked the old unused Cloudflare API token in the dashboard; "
    "the new scoped token is the only active one now and DNS works."
)
BENIGN_REJECTED = (
    "Cloudflare rejected the request because the API key was missing the "
    "Zone:Read scope. I added the scope and it works now."
)
BENIGN_EXPIRED_ROTATION = (
    "Rotated the Cloudflare API token: the old token was expired, so I generated "
    "a fresh one, stored it in ~/.secrets, and the DNS sync succeeded."
)
BENIGN_ODMIETOL = (
    "Cloudflare odmietol starý formát pri API, ale token funguje spravne a DNS "
    "zaznam je vytvoreny."
)


class CloudflareInvalidReviewControls(TestCase):
    """Fresh-context adversarial review (r2) counterexamples — all must PASS."""

    def test_cross_service_github_plus_cloudflare_passes(self):
        self.assertFalse(
            _blocked(_run(XSERVICE_GITHUB_PLUS_CF)),
            "a non-Cloudflare token claim + an UNRELATED cloudflare mention must "
            "NOT block (the NON_CF_TOKEN invariant must hold even WITH a "
            "cloudflare word present)")

    def test_unrelated_sortkey_plus_cloudflare_passes(self):
        self.assertFalse(
            _blocked(_run(UNRELATED_SORTKEY_PLUS_CF)),
            "'sort key ... is invalid' near a cloudflare mention must not block")

    def test_cloudflare_key_metric_passes(self):
        self.assertFalse(
            _blocked(_run(CF_KEY_METRIC)),
            "'key metric ... is invalid' is not a credential-invalid claim")

    def test_benign_revoked_hygiene_passes(self):
        self.assertFalse(
            _blocked(_run(BENIGN_REVOKED)),
            "'I revoked the old token' is a hygiene action, not an invalid verdict")

    def test_benign_rejected_request_passes(self):
        self.assertFalse(
            _blocked(_run(BENIGN_REJECTED)),
            "'Cloudflare rejected the request ... fixed' is a success report")

    def test_benign_expired_rotation_passes(self):
        self.assertFalse(
            _blocked(_run(BENIGN_EXPIRED_ROTATION)),
            "a rotation success report mentioning an expired old token must pass")

    def test_benign_sk_odmietol_passes(self):
        self.assertFalse(
            _blocked(_run(BENIGN_ODMIETOL)),
            "'Cloudflare odmietol ... ale token funguje' is benign")


class CloudflareInvalidHookContract(TestCase):
    def test_block_is_json_stdout_exit0_with_probe_named_on_stderr(self):
        p = _run(SK_NEFUNGUJE)
        self.assertEqual(p.returncode, 0)
        self.assertIn('"decision"', p.stdout)
        self.assertIn('"block"', p.stdout)
        # the refusal must name what is MISSING — a capability probe — in a way
        # the reader can act on, not a bare "forbidden".
        self.assertIn("capability probe", p.stderr.lower())
        self.assertIn("zones", p.stderr.lower())
        # and it must warn against the trap endpoint being used as a verdict.
        self.assertIn("/user/tokens/verify", p.stderr)


class CloudflareInvalidDoctrinePointer(TestCase):
    """The cloudflare skill must carry the pointer that the owner-chat
    invalid-claim is now hook-gated (a UNIQUE operative anchor, so a partial
    revert of that clause loses the test's teeth too)."""

    def test_skill_names_the_owner_chat_gate(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("#631", text, "the skill must reference #631")
        self.assertIn("owner-facing claim that a Cloudflare credential is invalid",
                      text, "the #631 owner-chat gate pointer must be present")
        self.assertIn("stop-check-prose-violations.sh", text,
                      "the pointer must name the enforcing hook")


if __name__ == "__main__":
    main()
