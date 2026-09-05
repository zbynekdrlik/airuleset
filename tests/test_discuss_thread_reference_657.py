"""#657 — `stop-check-prose-violations.sh` must BLOCK an OWNER-FACING message
that references an Odoo Discuss thread by a BARE numeric id (status / narration,
NOT only a ❓ question) unless it carries the canonical clickable deep URL
`…/odoo/discuss?active_id=discuss.channel_<N>`.

Owner escalation (montalu3 session, 2026-08-24, verbatim): "vazne airuleset
stale nedefinuje ako mas jednotne mi davat vediet v akom vlakne pracujes!!!??
co ja mam akoze robit s 'vlakno 288'?!" — a BARE channel id in owner-facing
narration, undecodable across many client threads. Same rationale as the
governing "`#N` always carries its title" doctrine.

This EXTENDS, never contradicts, the two governing designs the validator named:
  * #650 — `stop-check-question-quality.sh` Check 6 already hook-enforces the
    thread NAME, but ONLY on ❓ CLIENT-POSTING approval questions. It does not
    touch status/narration (the actual "vlakno 288" complaint), and it requires
    only a name, not a clickable URL.
  * #595 — deep-link URLs for every openable reference in a client message.
This ticket adds the WIDER owner-facing surface (any Stop message) + the THREAD's
OWN deep URL as the exemption, on the prose-violations Stop surface (where
localhost-URL / tester-handoff already HARD-block).

Design (see the #657 design comment): a sibling HARD check gated on an
Odoo-Discuss ANCHOR (`discuss|message_post|odoo|active_id`) so a bare sentence in
a non-Odoo repo (a concurrency "vlákno 12", a Wi-Fi "kanál 36") NEVER trips it;
EXEMPT the moment the canonical `discuss.channel_<N>` deep URL is present; else a
tight thread-word-adjacent bare-2+digit shape is the violation. Accepted
residuals are documented in the hook.

FUNCTIONAL tests (feed the hook a real Stop payload, read its
{"decision":"block"} verdict) plus content-locks on the hook implementation and
the doctrine files.
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
DOCTRINE = ROOT / "modules" / "core" / "issue-reference-context.md"
COMPOSE = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"


def _run(msg):
    sid = "discuss657-%s" % uuid.uuid4().hex[:12]
    p = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"session_id": sid, "last_assistant_message": msg}),
        capture_output=True, text=True, timeout=30)
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


def _reason(p):
    if not _blocked(p):
        return ""
    return json.loads(p.stdout).get("reason", "")


# ---- BLOCK cases: bare Discuss id in owner-facing narration, no clickable URL --

# The exact montalu3 failure shape: a STATUS/NARRATION line (not a ❓ question)
# with an Odoo-Discuss context anchor and a bare channel id, no clickable URL.
NARRATION_BARE_ID = (
    "Pracoval som na Odoo Discuss integrácii pre klienta a poslal som mu "
    "odpoveď do vlákna 288 na PROD. Notifikácie chodia."
)

# "vo vlákne 288" wording variant — also bare, must BLOCK.
NARRATION_LOCATIVE_BARE = (
    "Status: v Odoo Discuss vlákne 288 sme dnes vyriešili dve pripomienky "
    "od klienta; ostáva posledná."
)

# "kanál 293" wording + an explicit Discuss anchor — must BLOCK.
KANAL_BARE = (
    "Zhrnutie: na Odoo Discuss som presunul tému do kanála 293 a tam "
    "pokračujeme s klientom."
)

# English "channel 288" + odoo anchor — must BLOCK (not the URL form channel_288).
CHANNEL_ENGLISH_BARE = (
    "Update: moved the odoo Discuss conversation into channel 288, client "
    "replied there."
)

# "ch275" shorthand + odoo anchor — must BLOCK.
CH_SHORTHAND_BARE = (
    "Poznámka: klientska téma beží v odoo Discuss ako ch275, tam sú všetky "
    "správy."
)


# ---- PASS cases: compliant, or no Odoo context, or no bare id ----------------

# The canonical GOOD form (issue body): thread NAME + the deep clickable URL.
# Must PASS.
GOOD_NAME_AND_URL = (
    "Pracujem vo vlákne „Zakaznicky portal 3“ na Odoo Discuss — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288 — "
    "poslal som klientovi odpoveď."
)

# Bare id BUT the deep URL is present → the owner can click → EXEMPT, PASS.
BARE_ID_BUT_URL_PRESENT = (
    "Poslal som odpoveď do vlákna 288 na Odoo Discuss — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288"
)

# A CONCURRENCY thread reference in a NON-Odoo repo (no discuss/odoo anchor).
# Must PASS — the anchor gate keeps ordinary sentences out.
CONCURRENCY_NO_ANCHOR = (
    "Spustil som render na 16 vláknach a vlákno 12 spadlo na out-of-memory; "
    "reštartoval som ho a beží."
)

# A Wi-Fi "kanál 36" networking sentence, no Odoo anchor — must PASS.
WIFI_CHANNEL_NO_ANCHOR = (
    "Prepol som router na kanál 36 v pásme 5 GHz a latencia klesla."
)

# Discuss anchor present, but the thread number is a single digit (a concurrency
# thread, not a channel id) — must PASS (the 2+digit floor, under an ACTIVE anchor).
SINGLE_DIGIT_THREAD = (
    "V Odoo Discuss teste som spustil vlákno 2 na paralelný import; prešlo bez chyby."
)

# Discuss anchor present, NO thread reference at all — must PASS (no bare shape).
ORDINARY_ODOO_NO_THREAD = (
    "Na Odoo Discuss PROD som nasadil opravu filtra rozmerov a overil ju v UI."
)

# Discuss anchor + a thread NAME (quoted) with no bare id and no URL — the tight
# adjacency window must NOT match the name's own trailing stream digit.
NAME_ONLY_NO_BARE = (
    "Otvoril som na Odoo Discuss nové vlákno „Kontrola e-mailov 3“ pre klienta."
)

# #657 review 🔴 — MENTION-BLINDNESS: a message that merely QUOTES the banned
# form (backticks / fenced block / ASCII-double-quotes) while an anchor is present
# and no bare UNQUOTED id exists must PASS (strip_mentions exempts it — the same
# discipline the credential check applies). A completion report / playbook capture
# that cites the rule is the real recurring case (incl. THIS ticket's own report).
MENTION_BACKTICK = (
    "V Odoo Discuss doktríne som opravil pravidlo — spomínam v ňom `vlákno 288` "
    "ako príklad zlého tvaru; commit hotový, testy zelené."
)
MENTION_FENCED = (
    "Report k Odoo Discuss doktríne. Príklad zlého tvaru:\n"
    "```\nvlákno 288\n```\nHotové, doktrína pridaná."
)
MENTION_ASCII_QUOTE = (
    'Do Odoo Discuss doktríny som pridal zákaz tvaru "vlákno 288"; commit hotový.'
)
# GUILLEMET mention — the doctrine's OWN canonical Slovak quote „…“. strip_mentions
# does NOT strip guillemets, so the check strips them locally into $MSG_BARE.
MENTION_GUILLEMET = (
    "Doktrína pre Odoo Discuss: zakázali sme bare id tvaru „vlákno 288“ bez URL; "
    "test zelený."
)

# A Sales-Channel note in an Odoo context that never says "Discuss" — must PASS
# (the anchor is discuss-specific, not bare "odoo"; #657 review 🟡 fix).
SALES_CHANNEL_NO_DISCUSS = (
    "Na odoo PROD som nastavil sales channel 01 pre e-shop a overil poradie."
)


class TestBareDiscussIdBlocked(TestCase):
    """The wider surface: bare Discuss id in owner-facing narration/status."""

    def test_montalu3_narration_bare_id_is_blocked(self):
        p = _run(NARRATION_BARE_ID)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(
            _blocked(p),
            "a bare 'vlákno 288' in owner-facing Odoo narration (no clickable "
            "URL) was NOT blocked — the montalu3 regression: %r" % p.stdout)
        self.assertIn("discuss.channel_", _reason(p))

    def test_locative_bare_id_is_blocked(self):
        self.assertTrue(_blocked(_run(NARRATION_LOCATIVE_BARE)))

    def test_kanal_bare_id_is_blocked(self):
        self.assertTrue(_blocked(_run(KANAL_BARE)))

    def test_english_channel_bare_id_is_blocked(self):
        self.assertTrue(_blocked(_run(CHANNEL_ENGLISH_BARE)))

    def test_ch_shorthand_is_blocked(self):
        self.assertTrue(_blocked(_run(CH_SHORTHAND_BARE)))


class TestCompliantAndOrdinaryPass(TestCase):

    def test_good_name_and_url_passes(self):
        p = _run(GOOD_NAME_AND_URL)
        self.assertFalse(
            _blocked(p),
            "the canonical name+URL form was wrongly blocked: %r" % _reason(p))

    def test_bare_id_with_url_present_passes(self):
        p = _run(BARE_ID_BUT_URL_PRESENT)
        self.assertFalse(
            _blocked(p),
            "a bare id accompanied by the clickable deep URL was wrongly "
            "blocked (the URL is the exemption): %r" % _reason(p))

    def test_concurrency_thread_no_anchor_passes(self):
        p = _run(CONCURRENCY_NO_ANCHOR)
        self.assertFalse(
            _blocked(p),
            "a concurrency 'vlákno 12' with no Odoo anchor was falsely "
            "blocked: %r" % _reason(p))

    def test_wifi_channel_no_anchor_passes(self):
        p = _run(WIFI_CHANNEL_NO_ANCHOR)
        self.assertFalse(
            _blocked(p),
            "a Wi-Fi 'kanál 36' with no Odoo anchor was falsely blocked: %r"
            % _reason(p))

    def test_single_digit_thread_passes(self):
        p = _run(SINGLE_DIGIT_THREAD)
        self.assertFalse(
            _blocked(p),
            "a single-digit 'vlákno 2' (concurrency) was falsely blocked: %r"
            % _reason(p))

    def test_ordinary_odoo_no_thread_passes(self):
        self.assertFalse(_blocked(_run(ORDINARY_ODOO_NO_THREAD)))

    def test_name_only_no_bare_passes(self):
        p = _run(NAME_ONLY_NO_BARE)
        self.assertFalse(
            _blocked(p),
            "a quoted thread NAME (no bare id) was falsely blocked by the "
            "adjacency window matching the name's stream digit: %r" % _reason(p))


class TestMentionAndDomainCollisionsPass(TestCase):
    """#657 review fixes — the BARE detection runs on $MSG_MENTION so a QUOTED
    mention of the banned form is exempt (🔴 mention-blindness), and the anchor
    is discuss-specific so Odoo's own 'Sales Channel' vocabulary is not caught
    (🟡)."""

    def test_backtick_mention_passes(self):
        p = _run(MENTION_BACKTICK)
        self.assertFalse(
            _blocked(p),
            "a backticked `vlákno 288` mention (a report/playbook citing the "
            "rule) was false-blocked — mention-blindness 🔴: %r" % _reason(p))

    def test_fenced_mention_passes(self):
        p = _run(MENTION_FENCED)
        self.assertFalse(
            _blocked(p),
            "a fenced-code-block 'vlákno 288' mention was false-blocked: %r"
            % _reason(p))

    def test_ascii_quoted_mention_passes(self):
        p = _run(MENTION_ASCII_QUOTE)
        self.assertFalse(
            _blocked(p),
            "an ASCII-double-quoted \"vlákno 288\" mention was false-blocked: %r"
            % _reason(p))

    def test_guillemet_mention_passes(self):
        # #657 review 🟡#1: the doctrine's OWN „…“ delimiter must be mention-exempt.
        p = _run(MENTION_GUILLEMET)
        self.assertFalse(
            _blocked(p),
            "a Slovak guillemet „vlákno 288“ mention (the doctrine's own quote) "
            "was false-blocked — the exemption must strip guillemets too: %r"
            % _reason(p))

    def test_sales_channel_no_discuss_passes(self):
        p = _run(SALES_CHANNEL_NO_DISCUSS)
        self.assertFalse(
            _blocked(p),
            "an Odoo 'sales channel 01' note that never says Discuss was "
            "false-blocked — the anchor must be discuss-specific 🟡: %r"
            % _reason(p))


class TestCheckImplementedInHook(TestCase):
    """Content-lock: the hook actually implements the #657 check and documents
    its accepted residuals (repo convention for a word-family heuristic)."""

    def setUp(self):
        self.h = HOOK.read_text(encoding="utf-8")

    def test_check_wired(self):
        # the anchor gate, the exemption, and the bare-shape operative vars
        self.assertIn("ODOO_ANCHOR_RX", self.h)
        self.assertIn("BARE_THREAD_RX", self.h)
        self.assertIn("discuss.channel_", self.h)
        # repo #319 convention on diacritic greps
        self.assertIn("LC_ALL=C.UTF-8", self.h)

    def test_residuals_documented(self):
        self.assertIn("Accepted residuals", self.h)
        self.assertIn("#657", self.h)


class TestDoctrineText(TestCase):
    """#657 still_to_do (3): doctrine in issue-reference-context.md + the URL
    mandate added to handover-compose.md's name-only rule."""

    def test_issue_reference_context_has_discuss_doctrine(self):
        t = DOCTRINE.read_text(encoding="utf-8")
        self.assertIn("Discuss", t)
        self.assertIn("active_id=discuss.channel", t)
        self.assertIn("#657", t)

    def test_handover_compose_adds_thread_url_mandate(self):
        t = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("active_id=discuss.channel", t)
        self.assertIn("#657", t)


if __name__ == "__main__":
    main()
