"""#1042 — Štartovací Introduction v produkte: the airuleset handover STANDARD.

Owner directive (montalu6 chat, 15.9.2026): every client handover of a
functional area must deliver ONE startup „Introduction / Začíname" page INSIDE
the product (the Návody section) from which a NEW person with zero context can
start — never an external document, never a step-by-step training thread (the
odoo-erp 4650/7307 incident: a Discuss training thread failed). This suite locks
three surfaces, all EXTENDING existing machinery (no new module, no new hook):

1. GATE — `hooks/stop-check-prose-violations.sh` blocks a `❓` acceptance block /
   a `needs-acceptance` labelling turn that carries no `https://` link to a
   Návody / Introduction / Začíname page (bypass `# airuleset:intro-link-ok`;
   fail-OPEN when the shape cannot be classified). RED both ways.
2. DOCTRINE — the standard lives as a new section „Štartovací Introduction v
   produkte" in `skills/odoo-client-messaging/handover-compose.md` (the companion
   every handover already loads), under the injector MAX_BODY cap.
3. AUDIT — `cli_doctrine_audit.py` ALLOWLIST gains an entry so a per-stream
   restatement of this standard is retired to a fleet pointer.

RED-before-GREEN: against the base tree the gate does not block the acceptance
fixtures, the doctrine section is absent, and the ALLOWLIST has no entry.
"""

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _hook_state_cleanup import MODULE_HOOK_HOME, sweep_session_files  # noqa: E402  (#1046 hermetic HOME)

import cli_doctrine_audit as da  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "stop-check-prose-violations.sh"
COMPOSE = ROOT / "skills" / "odoo-client-messaging" / "handover-compose.md"

# The four distinctive SK anchors the standard is written with (verbatim,
# whitespace-collapsed, lowercased — the shape `cli_doctrine_audit` matches).
SK_ANCHORS = [
    "štartovací introduction v produkte",
    "nová osoba bez kontextu rovno začať",
    "nikdy externý dokument, nikdy školiace vlákno",
    "akceptačné vlákno klientovi odkazuje na introduction",
]
HEADING = "Štartovací Introduction v produkte"


def _run(msg, cwd=None):
    sid = "intro1042-%s" % uuid.uuid4().hex[:10]
    obj = {"session_id": sid, "last_assistant_message": msg}
    if cwd is not None:
        obj["cwd"] = cwd
    payload = json.dumps(obj)
    p = subprocess.run(["bash", str(HOOK)], input=payload, capture_output=True,
                       text=True, timeout=300, env={**os.environ, "HOME": MODULE_HOOK_HOME})
    sweep_session_files(sid)
    return p


def _blocked(p):
    return '"decision"' in p.stdout and '"block"' in p.stdout


# --------------------------------------------------------------------------- #
# GATE fixtures. Each BLOCK fixture satisfies every OTHER prose-gate check
# (Check 6 thread deep URL, Check 7 Odoo task URL, Check 8 inlined > quote) so
# it is blocked ONLY by the #1042 intro-link check — verified against the base
# tree (probe_fixtures baseline: all unblocked).
# --------------------------------------------------------------------------- #
ACCEPT_BLOCK_NO_LINK = (
    "**Otázka — projekt odoo-erp (montalu, dochádzka):** Funkcia dochádzka je "
    "hotová a nasadená na PROD. Chcem ju odovzdať klientovi akceptačnou správou "
    "do vlákna „Dochádzka 1\" — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288 "
    "a označiť Odoo task https://erp.montalu.cloud/odoo/project/4/tasks/503 ako "
    "needs-acceptance.\n"
    "> Ahoj Pavol, dochádzka je nasadená na vašom systéme.\n>\n> ZbynekAI 1\n"
    "❓ NEEDS YOU: schváliš akceptačnú správu klientovi?"
)

LABEL_TURN_NO_LINK = (
    "Dochádzka je hotová a nasadená na PROD. Odoo task "
    "https://erp.montalu.cloud/odoo/project/4/tasks/503 som označil ako "
    "needs-acceptance a odovzdávam klientovi na akceptáciu do vlákna „Dochádzka 1\". "
    "⏳ WORKING"
)

ACCEPT_BLOCK_WITH_LINK = (
    "**Otázka — projekt odoo-erp (montalu, dochádzka):** Funkcia dochádzka je "
    "hotová a nasadená na PROD. Pripravil som akceptačnú správu do vlákna "
    "„Dochádzka 1\" — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288, Odoo "
    "task https://erp.montalu.cloud/odoo/project/4/tasks/503 (needs-acceptance). "
    "Správa odkazuje na štartovací Introduction/Začíname v Návodoch: "
    "https://erp.montalu.cloud/odoo/action-lunch.navody/12.\n"
    "> Ahoj Pavol, dochádzka je nasadená — návod Začíname: <odkaz>.\n>\n"
    "> ZbynekAI 1\n"
    "❓ NEEDS YOU: schváliš akceptačnú správu klientovi?"
)

ACCEPT_BLOCK_WITH_BYPASS = (
    "**Otázka — projekt odoo-erp (montalu):** API-only integrácia je hotová, "
    "nemá žiadnu produktovú stránku. Akceptačnú správu do vlákna „API 1\" — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288, Odoo "
    "task https://erp.montalu.cloud/odoo/project/4/tasks/503 (needs-acceptance).\n"
    "> Ahoj, integrácia beží.\n>\n> ZbynekAI 1\n"
    "# airuleset:intro-link-ok API-only feature, no product page\n"
    "❓ NEEDS YOU: schváliš akceptačnú správu?"
)

NORMAL_QUESTION = (
    "**Otázka — projekt camera-box:** Mám dve možnosti pre HDMI výstup.\n"
    "- (1) DRM master (odporúčam)\n- (2) v4l2 loopback\n"
    "❓ NEEDS YOU: ktorú cestu zvolím?"
)

MENTION_ONLY = (
    "Rozšíril som `stop-check-prose-violations.sh` o kontrolu, ktorá blokuje "
    "`needs-acceptance` odovzdávku bez odkazu na Návody/Introduction stránku. "
    "Bypass je `# airuleset:intro-link-ok`. ✅ DONE"
)

# #1042-review-1 🔴 — a legit NON-handover question that DOES carry acceptance +
# client words but proposes no client message must NOT be blocked (the loose
# co-occurrence over-fire the review reproduced). No strong signal (no
# needs-acceptance label, no ZbynekAI/MarekAI signature, no „akceptačná správa",
# no „odovzdávam klientovi"), so Trigger A must not fire.
FB1_DESIGN_ACCEPTANCE_TESTS = (
    "**Otázka — projekt montalu:** Mám pre klientský portál pridať acceptance "
    "testy do CI?\n- (1) áno, Playwright\n- (2) nie\n"
    "❓ NEEDS YOU: pridám acceptance testy?"
)

FB2_ENGLISH_ACCEPTANCE_DEMO = (
    "**Otázka — projekt montalu:** Should I run the acceptance tests before the "
    "client demo tomorrow?\n- (1) yes\n- (2) no\n"
    "❓ NEEDS YOU: run the acceptance tests?"
)

FB3_EXPLICITLY_NOT_ACCEPTANCE = (
    "**Otázka — projekt montalu:** Klientske vlákno „Dochádzka 1\" je vyriešené. "
    "Mám ho archivovať? Toto NIE JE akceptačná odovzdávka, len upratovanie.\n"
    "❓ NEEDS YOU: archivovať vlákno?"
)

# #1042-review-2 🔴 — DESIGN questions that CONTAIN a strong token as a FEATURE /
# label-mechanism word (not the act of proposing a client message) must NOT be
# blocked. None carries a stream signature or a discuss.channel_<N> deep URL, so
# Trigger A's PROPOSAL condition must not fire.
FP1_FEATURE_HANDOVER = (
    "**Otázka — projekt montalu:** Robím funkciu, aby používateľ mohol odovzdať "
    "súbor klientovi cez portál. Ktorú cestu zvolím?\n- (1) presigned URL\n- (2) proxy\n"
    "❓ NEEDS YOU: ktorú cestu?"
)

FP2_FEATURE_ACCEPTANCE_MSG = (
    "**Otázka — projekt montalu:** Mám pridať tlačidlo, ktoré vygeneruje "
    "akceptačnú správu automaticky?\n- (1) áno\n- (2) nie\n"
    "❓ NEEDS YOU: pridám tlačidlo?"
)

FP3_LABEL_MECHANISM = (
    "**Otázka — projekt airuleset:** Mám pre needs-acceptance label pridať "
    "automatiku do CI, ktorá ho po merge odstráni?\n- (1) áno\n- (2) nie\n"
    "❓ NEEDS YOU: pridám automatiku?"
)

# TRUE POSITIVE via the thread+acc path (no ZbynekAI signature): a genuine
# acceptance proposal that names the target client thread deep URL + an
# acceptance cue, with NO intro link -> must block.
ACCEPT_THREAD_NO_SIG_NO_LINK = (
    "**Otázka — projekt odoo-erp (montalu):** Chcem odoslať akceptačnú správu do "
    "vlákna „Dochádzka 1\" — "
    "https://erp.montalu.cloud/odoo/discuss?active_id=discuss.channel_288 a "
    "označiť Odoo task https://erp.montalu.cloud/odoo/project/4/tasks/503 ako "
    "needs-acceptance.\n"
    "❓ NEEDS YOU: schváliš text akceptačnej správy klientovi?"
)


class GateBlocksAcceptanceWithoutIntroLink(TestCase):
    def test_accept_block_without_intro_link_is_blocked(self):
        # RED on base (no check); GREEN after — a ❓ acceptance block with no
        # link to a Návody/Introduction/Začíname page is blocked.
        self.assertTrue(_blocked(_run(ACCEPT_BLOCK_NO_LINK)))

    def test_needs_acceptance_labelling_turn_without_link_is_blocked(self):
        # RED on base; GREEN after — a needs-acceptance labelling turn with no
        # intro link is blocked (the label is narrated in prose, not a code span).
        self.assertTrue(_blocked(_run(LABEL_TURN_NO_LINK)))

    def test_acceptance_proposal_via_thread_and_cue_without_link_is_blocked(self):
        # Locks the thread+acceptance-cue PROPOSAL path (no ZbynekAI signature):
        # a ❓ naming the client thread deep URL + a needs-acceptance cue, no
        # intro link -> blocked (#1042-review-2).
        self.assertTrue(_blocked(_run(ACCEPT_THREAD_NO_SIG_NO_LINK)))


class GateAllowsWhenLinkedOrBypassedOrIrrelevant(TestCase):
    def test_accept_block_with_intro_link_but_unknown_fact_is_blocked(self):
        # SUPERSEDED by #1073 (owner ruling 18.9.2026: „návody buduj a udržiavaj
        # a nech to je airuleset pravidlo"). The round-1 (#1042) contract this
        # method used to assert — "any https:// link + the word Návody satisfies
        # the gate" — is EXACTLY the word-pattern check that could not tell a
        # live guide from a fabricated URL and so forced a dead `/odoo/knowledge`
        # link into a client message (#1072). Under #1073 the gate reads the
        # per-tenant `navody_url` fact from `.claude/streams/<stream>.md` and
        # fails CLOSED on an UNKNOWN fact — so a Návody link with NO known guide
        # fact (this fixture runs with no stream file) now BLOCKS. The
        # live-200-link ALLOW path is covered in
        # tests/test_navody_gate_1073.py::TestStopEvaluate (fact + fake curl).
        # cwd = an empty dir so the UNKNOWN-fact state is DETERMINISTIC (no
        # `.claude/streams/<uid>.md` fact), never ambient on this repo (#1073
        # review 2).
        with TemporaryDirectory() as d:
            self.assertTrue(_blocked(_run(ACCEPT_BLOCK_WITH_LINK, cwd=d)))

    def test_accept_block_with_bypass_is_allowed(self):
        self.assertFalse(_blocked(_run(ACCEPT_BLOCK_WITH_BYPASS)))

    def test_normal_non_acceptance_question_is_not_blocked(self):
        # No false positive on an ordinary design ❓ with no acceptance context.
        self.assertFalse(_blocked(_run(NORMAL_QUESTION)))

    def test_rule_mention_is_not_blocked(self):
        # A message merely DESCRIBING the rule (backticked mentions) is not gated.
        self.assertFalse(_blocked(_run(MENTION_ONLY)))

    def test_design_question_with_acceptance_words_is_not_blocked(self):
        # #1042-review-1 🔴 — the over-fire the review reproduced: a design
        # question mentioning acceptance + client but proposing no client
        # message must NOT be blocked.
        self.assertFalse(_blocked(_run(FB1_DESIGN_ACCEPTANCE_TESTS)))

    def test_english_acceptance_demo_question_is_not_blocked(self):
        self.assertFalse(_blocked(_run(FB2_ENGLISH_ACCEPTANCE_DEMO)))

    def test_explicitly_not_acceptance_question_is_not_blocked(self):
        # An explicit „toto NIE JE akceptačná odovzdávka" question stays allowed.
        self.assertFalse(_blocked(_run(FB3_EXPLICITLY_NOT_ACCEPTANCE)))

    def test_feature_word_design_questions_are_not_blocked(self):
        # #1042-review-2 🔴 — a strong token used as a FEATURE / label-mechanism
        # word in a design question (no signature, no discuss.channel URL) must
        # NOT block. These go RED against the round-1 word-keyed detector.
        for name, msg in [("FP1", FP1_FEATURE_HANDOVER),
                          ("FP2", FP2_FEATURE_ACCEPTANCE_MSG),
                          ("FP3", FP3_LABEL_MECHANISM)]:
            with self.subTest(case=name):
                self.assertFalse(_blocked(_run(msg)))

    def test_empty_message_is_allowed(self):
        # Fail-open on an unclassifiable / empty turn.
        self.assertFalse(_blocked(_run("")))


# --------------------------------------------------------------------------- #
# DOCTRINE — the section exists in the companion, under the injector cap.
# --------------------------------------------------------------------------- #
class HandoverComposeCarriesTheStandard(TestCase):
    def setUp(self):
        self.raw = COMPOSE.read_text(encoding="utf-8")
        self.low = re.sub(r"\s+", " ", self.raw.lower())

    def test_section_heading_present(self):
        self.assertIn(HEADING, self.raw)

    def test_all_sk_anchors_present(self):
        for a in SK_ANCHORS:
            with self.subTest(anchor=a):
                self.assertIn(a, self.low)

    def test_gate_and_bypass_documented(self):
        self.assertIn("# airuleset:intro-link-ok", self.raw)
        # the (e) rule: needs-acceptance hand-off must link the Introduction
        self.assertIn("needs-acceptance", self.low)
        self.assertIn("nekompletná odovzdávka", self.low)

    def test_mandatory_elements_present(self):
        # screenshots, per-device sections (kiosk + Fully Kiosk; OTP), AI helper
        for token in ["screenshoty", "fully kiosk", "otp", "ai pomocník",
                      "findability proof"]:
            with self.subTest(token=token):
                self.assertIn(token, self.low)

    def test_body_under_injector_soft_cap(self):
        # Mirror the injector's per-BODY measure (frontmatter-stripped body;
        # this file has no frontmatter). SOFT cap = MAX_BODY(24000) - 300.
        self.assertLessEqual(len(self.raw.strip()), 23700)


# --------------------------------------------------------------------------- #
# AUDIT — the ALLOWLIST retires a per-stream restatement of the standard.
# --------------------------------------------------------------------------- #
RESTATEMENT = """\
---
name: client-handover-startup-introduction
description: "Client handover: startup Introduction page in the product, not a training thread"
metadata:
  node_type: memory
  type: project
---
# Client handover — startup Introduction page in the product

Každá klientska odovzdávka funkčnej oblasti má štartovací Introduction v produkte
(sekcia Návody), z ktorého vie nová osoba bez kontextu rovno začať — nikdy externý
dokument, nikdy školiace vlákno. Akceptačné vlákno klientovi odkazuje na
Introduction, nikdy neopisuje kroky.
"""

ONE_ANCHOR_NEG = """\
---
name: product-onboarding-note
description: "Internal onboarding note"
metadata:
  node_type: memory
  type: project
---
# Onboarding

We should add a startup introduction page in the product for new hires.
"""


def _by_name(matches):
    return {os.path.basename(m.path): m for m in matches}


class DoctrineAuditRetiresRestatement(TestCase):
    def _home_with(self, files):
        tmp = TemporaryDirectory()
        home = Path(tmp.name)
        mem = home / ".claude" / "projects" / "-home-montalu6-proj" / "memory"
        mem.mkdir(parents=True)
        for fn, text in files.items():
            (mem / fn).write_text(text, encoding="utf-8")
        return tmp, home

    def test_restatement_is_high_rewrite_to_the_new_entry(self):
        tmp, home = self._home_with({"startup-intro.md": RESTATEMENT})
        with tmp:
            m = _by_name(da.scan_home(str(home)))["startup-intro.md"]
            self.assertEqual(m.confidence, da.HIGH)
            self.assertEqual(m.action, da.ACTION_REWRITE)
            self.assertIn("handover-compose", m.fleet_source)
            self.assertEqual(m.heading, HEADING)
            self.assertGreaterEqual(m.anchors_hit, da.MIN_ANCHORS_HIGH)

    def test_one_anchor_generic_note_is_not_high(self):
        tmp, home = self._home_with({"onboarding.md": ONE_ANCHOR_NEG})
        with tmp:
            m = _by_name(da.scan_home(str(home))).get("onboarding.md")
            if m is not None:
                self.assertNotEqual(m.action, da.ACTION_REWRITE)
                self.assertLess(m.anchors_hit, da.MIN_ANCHORS_HIGH)

    def test_allowlist_has_the_entry(self):
        ids = {e["id"] for e in da.ALLOWLIST}
        self.assertIn("startup-introduction", ids)


if __name__ == "__main__":
    main()
