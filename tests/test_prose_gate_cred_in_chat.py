"""#152 (point 3, user-decided 2026-08-08) — mechanically enforce the ban on
asking the user to paste a credential VALUE directly into chat.

`modules/core/receive-files-via-upload-url.md` already bans this in prose
(#256 shipped it): "asking the user to paste a password/key/token/PAT/
connection-string into chat ('send me the API key here', 'paste the token',
'čo je to heslo?')" — and documents the real channel, `airuleset.py secret
request`/`secret exec`. Point 3 of #152 was left open: whether a Stop-hook
should ALSO block a session that asks for a credential in prose. The user's
own decision (issue comment 5227370702): yes, mechanically, by EXTENDING
this existing hook (FREEZE forbids a new hook file) — narrow direct-request
patterns, English AND genuinely natural Slovak (never just the English
loanword, per the #316/#319 lesson elsewhere in this hook), with an escape
for a message that already references the sanctioned `secret request`
channel, and a hard requirement that ordinary talk ABOUT passwords/tokens
(documentation, code review, explaining) stays completely unblocked.

Slovak coverage is verified against dedicated fixtures here, empirically —
never assumed from the English side (#316/#319's own repeated lesson: an
English-only regex is blind on every away/stream box, since every real
question this repo ships is Slovak). `LC_ALL=C.UTF-8` is forced on the
Slovak grep call specifically, mirroring the SAME fix `stop-check-prose-
violations.sh` already needed twice for its OTHER Slovak detectors
(SK_DISPATCH_RX, SK_APPROVAL_RX, SK_MERGE_FLAT): `\b` immediately adjacent
to a diacritic is itself locale-dependent under a bare C/POSIX locale, and
converting bracket classes to plain alternation does NOT fix it by itself
(#316's own reproduction) — only a real UTF-8 locale on that one call does.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import sweep_session_files  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"


def _run(msg, sid=None, env=None):
    sid = sid or ("credreq152-%s" % uuid.uuid4().hex[:10])
    payload = json.dumps({"session_id": sid, "last_assistant_message": msg})
    p = subprocess.run(
        ["bash", str(HOOK)], input=payload, capture_output=True, text=True,
        timeout=300, env=env)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


class TestEnglishDirectCredentialRequestsAreBlocked(TestCase):
    """The exact English shapes `receive-files-via-upload-url.md` already
    bans in prose — must now be blocked mechanically."""

    def test_paste_the_api_key_here(self):
        p = _run("Sure, just paste the API key here and I'll continue.")
        self.assertTrue(
            _blocked(p),
            "'paste the API key here' was NOT blocked. rc=%s stdout=%r "
            "stderr=%r" % (p.returncode, p.stdout[:300],
                            p.stderr.strip()[-400:]))
        self.assertEqual(p.returncode, 0, "a block must exit 0, not error")

    def test_send_me_the_token(self):
        p = _run("Can you send me the token so I can test it?")
        self.assertTrue(_blocked(p))

    def test_whats_the_password(self):
        p = _run("What's the password?")
        self.assertTrue(_blocked(p))

    def test_noun_then_verb_reversed_order_blocks(self):
        p = _run("The token, please send it to me directly in chat.")
        self.assertTrue(_blocked(p))

    def test_give_me_the_connection_string(self):
        p = _run("Give me the connection string for the staging database.")
        self.assertTrue(_blocked(p))

    def test_share_the_login_credentials_with_me(self):
        """#152-review: plural "credentials" was added as a noun, paired
        with the SAME destination-marker gate as every other noun."""
        p = _run("Please share the login credentials with me.")
        self.assertTrue(_blocked(p))


class TestSlovakDirectCredentialRequestsAreBlocked(TestCase):
    """Genuinely natural Slovak — no English loanword needed. Includes the
    EXACT phrases the module already quotes as banned formulations."""

    def test_posli_mi_to_heslo_sem_exact_module_phrase(self):
        p = _run("Pošli mi to heslo sem.")
        self.assertTrue(
            _blocked(p),
            "'Pošli mi to heslo sem.' (the module's own quoted banned "
            "phrase) was NOT blocked. rc=%s stdout=%r stderr=%r"
            % (p.returncode, p.stdout[:300], p.stderr.strip()[-400:]))

    def test_napis_mi_token_exact_module_phrase(self):
        p = _run("Napíš mi token, prosím.")
        self.assertTrue(_blocked(p))

    def test_vloz_sem_api_kluc(self):
        p = _run("Vlož sem API kľúč.")
        self.assertTrue(_blocked(p))

    def test_ake_je_heslo(self):
        p = _run("Aké je heslo?")
        self.assertTrue(_blocked(p))

    def test_co_je_to_heslo_exact_module_phrase(self):
        p = _run("Čo je to heslo?")
        self.assertTrue(
            _blocked(p),
            "'Čo je to heslo?' (the module's own quoted banned phrase) "
            "was NOT blocked.")

    def test_noun_then_verb_reversed_order_blocks(self):
        p = _run("Heslo mi prosím pošli.")
        self.assertTrue(_blocked(p))

    def test_locale_does_not_disarm_the_slovak_check(self):
        """Reproduces the exact locale gotcha #316/#319 already document
        twice in this hook: \\b next to a diacritic is locale-dependent
        under a bare C/POSIX locale. Without the forced LC_ALL=C.UTF-8 on
        the Slovak grep call, this whole detector would go silently inert
        on any box with no locale configured."""
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        sid = "credreq152-locale-%s" % uuid.uuid4().hex[:8]
        p = _run("Pošli mi to heslo sem.", sid=sid, env=env)
        self.assertTrue(
            _blocked(p),
            "the Slovak credential-request was NOT blocked under a bare "
            "C/POSIX locale. rc=%s stdout=%r stderr=%r"
            % (p.returncode, p.stdout[:300], p.stderr.strip()[-400:]))


class TestTheSecretRequestChannelEscapesTheGate(TestCase):
    """A message that already points at the sanctioned channel is the
    CORRECT shape and must never be blocked, in either language."""

    def test_english_escape_via_secret_request(self):
        msg = (
            "Please run `python3 ~/devel/airuleset/airuleset.py secret "
            "request DB_PASSWORD` and share the printed URL — never paste "
            "the password itself here."
        )
        self.assertFalse(
            _blocked(_run(msg)),
            "a message referencing the secret-request channel was "
            "wrongly blocked")

    def test_english_escape_via_secret_exec(self):
        msg = (
            "I'll hand the token to the process via `secret exec "
            "API_TOKEN -- mycmd` — send it that way, not in chat."
        )
        self.assertFalse(_blocked(_run(msg)))

    def test_slovak_message_with_escape_reference_is_not_blocked(self):
        """This message's own wording would otherwise match the Slovak
        verb+noun banned shape ('napíš mi token') -- the secret-request
        reference elsewhere in the SAME message must disarm it."""
        msg = (
            "Napíš mi token cez `airuleset.py secret request API_TOKEN`, "
            "nikdy priamo do chatu."
        )
        self.assertFalse(
            _blocked(_run(msg)),
            "a Slovak credential request that also references the "
            "secret-request channel was wrongly blocked")


class TestSlovakCredentialsPluralAndDestinationGate(TestCase):
    """#152-review: the SAME two fixes as the English side, in Slovak."""

    def test_posli_mi_prihlasovacie_udaje_blocks(self):
        p = _run("Pošli mi prihlasovacie údaje.")
        self.assertTrue(_blocked(p))


class TestOrdinaryTalkAboutCredentialsStaysWelcome(TestCase):
    """The explicit precondition of the user's own decision: minimum false
    positives. Ordinary documentation / code-review / explanation sentences
    that merely MENTION a password/token/API key must never be gated."""

    def test_english_documentation_sentence(self):
        msg = ("The documentation explains how passwords are hashed "
               "before storage.")
        self.assertFalse(_blocked(_run(msg)))

    def test_english_code_review_sentence(self):
        msg = ("Please make sure the API key is not committed to git — "
               "it should stay in the .env file.")
        self.assertFalse(_blocked(_run(msg)))

    def test_english_code_review_sentence_about_token_logic(self):
        msg = ("I reviewed the code and the token refresh logic looks "
               "correct.")
        self.assertFalse(_blocked(_run(msg)))

    def test_english_interrogative_not_end_anchored_is_not_blocked(self):
        """A policy/format question ('what's the password REQUIREMENT')
        is a different question than 'what's the password' -- the
        end-of-line anchor keeps the interrogative form narrow."""
        msg = "What's the password requirement for this field?"
        self.assertFalse(_blocked(_run(msg)))

    def test_slovak_sentence_about_where_the_password_is_logged(self):
        msg = "Skontroloval som, že heslo sa nikde neloguje do konzoly."
        self.assertFalse(_blocked(_run(msg)))

    def test_slovak_sentence_about_token_expiry(self):
        msg = "Token má platnosť 24 hodín, potom treba obnoviť."
        self.assertFalse(_blocked(_run(msg)))

    def test_slovak_interrogative_not_end_anchored_is_not_blocked(self):
        msg = "Aké je heslo pre reset formulára?"
        self.assertFalse(_blocked(_run(msg)))

    def test_quoted_english_mention_of_the_banned_phrase_is_not_blocked(self):
        """A message merely QUOTING the banned phrase (documenting the
        rule, explaining what NOT to say) must not be gated — the mention
        must be stripped before the check runs."""
        msg = (
            "This rule bans phrases like `send me the API key here` — "
            "never ask for the value directly."
        )
        self.assertFalse(
            _blocked(_run(msg)),
            "a backtick-quoted MENTION of the banned phrase was wrongly "
            "blocked")

    def test_quoted_slovak_mention_of_the_banned_phrase_is_not_blocked(self):
        msg = (
            "Podľa pravidla je zakázané písať niečo ako `pošli mi to "
            "heslo sem`."
        )
        self.assertFalse(_blocked(_run(msg)))


class TestDestinationlessVerbNounProximityStaysWelcome(TestCase):
    """#152-review CRITICAL finding: verb+noun proximity ALONE (no
    destination marker — "me"/"here"/"in chat") false-blocked routine
    third-person technical prose. Every fixture here genuinely satisfies
    the OLD verb+noun-proximity pattern; none of them names chat/the
    assistant as the destination, so the new destination gate must keep
    every one of them unblocked."""

    def test_third_person_auth_description(self):
        msg = "The client must send the token in the Authorization header."
        self.assertFalse(
            _blocked(_run(msg)),
            "a third-person API-auth description ('must send the token "
            "in the header') was wrongly blocked")

    def test_llm_token_budget_prose(self):
        msg = "Give each Fable stage a token budget of about 50k tokens."
        self.assertFalse(
            _blocked(_run(msg)),
            "'token' in the LLM-budget sense was wrongly blocked")

    def test_instruction_to_store_in_github_secrets(self):
        """The SANCTIONED destination (GitHub Secrets, per
        security-basics.md) must never be confused with 'paste it in
        chat' just because the verb+noun pair matches."""
        msg = "Paste the token into the GitHub Secrets UI."
        self.assertFalse(
            _blocked(_run(msg)),
            "an instruction to store a token in GitHub Secrets (the "
            "SANCTIONED destination) was wrongly blocked")

    def test_cli_onboarding_narration(self):
        msg = ("Run gh auth login, press Enter, and copy the token from "
               "the browser.")
        self.assertFalse(_blocked(_run(msg)))

    def test_declarative_sentence_ending_in_the_noun_with_no_question_mark(self):
        """#152-review MINOR: the interrogative branch used to accept an
        OPTIONAL trailing '?' -- a declarative debugging sentence that
        merely happens to end in the noun, with no question mark at all,
        must not be read as a question."""
        msg = "Let me decode the JWT and see what's in the token"
        self.assertFalse(
            _blocked(_run(msg)),
            "a declarative sentence with no trailing '?' was wrongly "
            "read as an interrogative credential request")

    def test_verb_and_noun_far_apart_stays_unblocked(self):
        """#152-review: a distance/window control -- nothing in this repo
        had one. Verb and noun sit >20 characters apart in an unrelated
        sentence; the proximity window must keep this unblocked."""
        msg = (
            "Give the new intern a full guided tour of the whole office "
            "building today, and only once that is finished should we "
            "discuss the API token situation with the vendor."
        )
        self.assertFalse(_blocked(_run(msg)))

    def test_slovak_daj_mi_vediet_idiom_stays_unblocked(self):
        """#152-review: 'daj mi vedieť' ('let me know') is an extremely
        common Slovak idiom that pairs 'daj' with the dative 'mi' for a
        reason that has nothing to do with a credential. 'daj' was
        DROPPED from the Slovak verb list specifically for this.

        #424: the ORIGINAL fixture here ("...či token funguje.") now
        genuinely IS blocked -- by stop-check-prose-violations.sh's own
        NEW Slovak tester-handoff family (autonomous-verification.md),
        which keys on exactly "daj (mi) vedieť" near an outcome word like
        "funguje" -- a completely different, unrelated gate from the
        credential-request check this test is actually about. A genuine,
        welcome overlap (this really would be a tester-handoff nudge if
        an agent said it), not a bug -- but a bad negative-control
        fixture for THIS test's specific claim. Reworded to keep "daj mi
        vedieť" near "token" (this test's own point) while dropping the
        outcome word ("funguje"/"ide") the new detector needs."""
        msg = "Daj mi vedieť, či máš ešte ten token."
        self.assertFalse(
            _blocked(_run(msg)),
            "the 'daj mi vedieť' (let me know) idiom, merely co-occurring "
            "with 'token' in the same sentence, was wrongly blocked")

    def test_slovak_instruction_to_store_in_github_secrets(self):
        msg = "Skopíruj API kľúč do konfiguračného súboru GitHub Secrets."
        self.assertFalse(
            _blocked(_run(msg)),
            "a Slovak instruction to store the key in GitHub Secrets "
            "(no destination marker) was wrongly blocked")


if __name__ == "__main__":
    main()
