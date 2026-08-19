"""#521 — the handover-PROPOSAL composition rules, unified for ALL sub-dev streams.

Problem: every sub-dev stream composed the client PROD Discuss handover threads
differently and the owner had to re-teach the same rules to each one (montalu5
2026-08-16: a thread proposal with no deep-link URL and no owner-membership
statement; earlier montalu / montalu2). Each stream kept private notes, so the
lesson never spread.

Fix (this ticket): the canonical composition rules live in ONE companion file,
skills/odoo-discuss-xmlrpc/handover-compose.md — kept SEPARATE from the skill's
SKILL.md posting recipe so the recipe stays lean and always co-fits with
comprehensive-logging when both inject at message_post-code time (the split-body
fix, adversarial review F2). The composition rules reach the model at PROPOSAL
time via a hooks/situational-triggers.conf trigger (`odoo-discuss-handover`) on
UserPromptSubmit (the owner/user instruction) AND AskUserQuestion (the owner-
approval moment), earlier than the skill's code-time message_post row. A one-line
pointer in process-subdev covers the gatekeeper's own release-tail handover.

These are CONTENT-LOCK tests (single-line anchor + normalize-then-check so a
markdown re-wrap can never silently break them) plus a functional injection test
of the new trigger (fires on real handover-composition prompts incl. the recall
arm, stays silent on a bare deploy instruction and unrelated prompts) and a
co-fit size guard so a future skill-body growth can never silently re-break the
message_post recipe's delivery.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"
COMP_LOGGING = ROOT / "skills" / "comprehensive-logging" / "SKILL.md"
PROCESS_SUBDEV = ROOT / "skills" / "process-subdev" / "SKILL.md"
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"

NEEDLE = "Composing the client handover PROD Discuss proposal"
HANDOVER_TOPIC = "odoo-discuss-handover"
COMPOSE_BODY_REL = "skills/odoo-discuss-xmlrpc/handover-compose.md"
SKILL_BODY_REL = "skills/odoo-discuss-xmlrpc/SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse every run of whitespace (incl. a markdown line-wrap's newline+
    indent) to a single space — so a substring check survives the prose
    re-wrapping under it (internals-tests.md: single-line anchor + normalize)."""
    return " ".join(text.split())


def strip_frontmatter(text):
    """Mirror inject-situational-rule.sh's own strip_frontmatter — what the
    injector actually injects, so the size guard measures the real body."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl != -1:
                return text[nl + 1:].lstrip("\n")
    return text


def load_conf_rows():
    rows = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p != ""]
        assert len(parts) in (4, 5), "malformed trigger row: %r" % line
        rows.append(tuple(parts[:4]))
    return rows


def hook_max_total():
    """Read MAX_TOTAL out of the injector so the co-fit guard tracks the real
    budget rather than a hardcoded copy that could silently drift."""
    m = re.search(r"MAX_TOTAL\s*=\s*(\d+)", read(HOOK))
    assert m, "MAX_TOTAL not found in inject-situational-rule.sh"
    return int(m.group(1))


class TestCompositionRulesInCompanionFile(TestCase):
    """The canonical composition rules (R1/R2/R3-state/R5-live + reassurance)
    live in the companion file — anchored on short phrases run against the
    normalized whole-file text."""

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def test_companion_file_exists_with_its_header(self):
        self.assertTrue(COMPOSE.exists())
        self.assertIn("# " + NEEDLE, self.raw)

    def test_named_the_single_canonical_cross_stream_rule(self):
        self.assertIn("SINGLE canonical handover-proposal rule for EVERY sub-dev stream", self.t)
        self.assertIn("montalu5 2026-08-16", self.t)

    def test_r1_complete_proposal_lives_in_the_chat(self):
        self.assertIn("COMPLETE and lives IN THE CHAT", self.t)
        self.assertIn("the exact thread name", self.t)
        self.assertIn("FULL message body verbatim", self.t)
        self.assertIn("the member list", self.t)
        self.assertIn("the owner does not read tickets", self.t)

    def test_r2_direct_deep_link_url_required(self):
        self.assertIn("direct deep-link URL to the LIVE feature", self.t)
        self.assertIn("never a menu path", self.t)

    def test_r3_owner_membership_stated_explicitly(self):
        self.assertIn("State the owner's thread membership EXPLICITLY in the proposal", self.t)

    def test_r5_only_functions_already_live_on_prod(self):
        self.assertIn("Announce ONLY functions that are ALREADY LIVE on the client's PROD", self.t)

    def test_reassurance_named_recipients_and_self_blame_template(self):
        self.assertIn("named recipients + the self-blame reassurance", self.t)
        self.assertIn("chyba je na našej strane a hneď to opravíme", self.t)


class TestSkillStaysLeanAndPoints(TestCase):
    """SKILL.md keeps the posting RECIPE + a pointer to the companion; it must
    NOT restate the composition rules (that would re-bloat the code-time body
    and split the source of truth)."""

    def setUp(self):
        self.raw = read(SKILL)
        self.t = norm(self.raw)

    def test_skill_points_at_the_companion(self):
        self.assertIn("handover-compose.md", self.t)

    def test_skill_body_does_not_restate_the_rules(self):
        # the operative rule phrases must live ONLY in the companion, not the recipe
        self.assertNotIn("named recipients + the self-blame reassurance", self.t)
        self.assertNotIn("State the owner's thread membership EXPLICITLY", self.t)

    def test_description_points_at_the_companion(self):
        front = self.raw.split("---", 2)[1]
        self.assertIn("companion handover-compose.md", front)


class TestHandoverTriggerRow(TestCase):
    def setUp(self):
        self.rows = load_conf_rows()
        self.by_topic = {r[0]: r for r in self.rows}

    def test_handover_row_points_at_companion_on_both_surfaces(self):
        self.assertIn(HANDOVER_TOPIC, self.by_topic, "missing #521 handover trigger row")
        topic, tool, pattern, body = self.by_topic[HANDOVER_TOPIC]
        # F1: proposal-time (UserPromptSubmit) AND owner-approval (AskUserQuestion)
        self.assertEqual(tool, "UserPromptSubmit|AskUserQuestion")
        self.assertEqual(body, COMPOSE_BODY_REL)

    def test_message_post_row_still_on_the_lean_recipe(self):
        self.assertIn("odoo-discuss-xmlrpc", self.by_topic)
        self.assertEqual(self.by_topic["odoo-discuss-xmlrpc"][3], SKILL_BODY_REL)

    def test_topics_still_unique(self):
        topics = [r[0] for r in self.rows]
        self.assertEqual(len(topics), len(set(topics)), "duplicate topic in table")

    def test_pattern_has_the_recall_arm(self):
        # F3: a klient×vlákno/Discuss proximity arm beyond the odovzd/handover family
        pattern = self.by_topic[HANDOVER_TOPIC][2]
        self.assertIn("informuj", pattern)
        self.assertIn("klient", pattern)


class TestCoFitSizeGuard(TestCase):
    """F2/F5: the message_post recipe (SKILL.md) and comprehensive-logging both
    fire on any .py write containing message_post; their injected bodies MUST
    co-fit under the injector's MAX_TOTAL or one silently defers. This guard
    fails the moment a future edit to either body would re-break that."""

    def test_recipe_plus_comprehensive_logging_cofit(self):
        recipe = len(strip_frontmatter(read(SKILL)).strip())
        comp = len(strip_frontmatter(read(COMP_LOGGING)).strip())
        budget = hook_max_total()
        self.assertLessEqual(
            recipe + comp, budget,
            "SKILL.md recipe (%d) + comprehensive-logging (%d) = %d exceeds MAX_TOTAL %d; "
            "the message_post recipe would defer on a .py write. Move content OUT of "
            "SKILL.md (e.g. into handover-compose.md), do not grow the recipe."
            % (recipe, comp, recipe + comp, budget),
        )


class TestHandoverTriggerInjection(TestCase):
    """Functional: the trigger loads the composition rules at proposal time, and
    stays silent on a bare deploy instruction / unrelated prompt."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _run(self, payload):
        r = subprocess.run(
            ["bash", str(HOOK)], input=json.dumps(payload),
            capture_output=True, text=True, env=dict(os.environ, TMPDIR=self.tmpdir),
        )
        # F7: the injector must never fail — assert clean exit + valid JSON output
        self.assertEqual(r.returncode, 0, "injector hook must never block: %r" % r.stderr)
        if r.stdout.strip():
            json.loads(r.stdout)  # raises if the hook emitted malformed JSON
        return NEEDLE in r.stdout

    def _prompt(self, text, sid):
        return self._run({"session_id": sid, "hook_event_name": "UserPromptSubmit", "prompt": text})

    def _ask(self, question, sid):
        ti = {"questions": [{"question": question, "header": "Q", "options": [{"label": "A"}]}]}
        return self._run({"session_id": sid, "tool_name": "AskUserQuestion", "tool_input": ti})

    def test_sk_odovzdav_prompt_injects(self):
        self.assertTrue(self._prompt(
            "Priprav odovzdávku pre klienta na PROD Discuss vlákno IT-support", "s1"))

    def test_en_handover_prompt_injects(self):
        self.assertTrue(self._prompt(
            "Compose the handover message to the client's Discuss thread", "s2"))

    def test_f3_napis_klient_vlakno_injects(self):
        # the recall arm: no odovzd/handover word at all
        self.assertTrue(self._prompt(
            "Napíš klientovi do IT-support vlákna že je funkcia hotová", "s3"))

    def test_f3_informuj_klienta_discuss_injects(self):
        self.assertTrue(self._prompt("Informuj klienta v Discuss o novej funkcii", "s4"))

    def test_askuserquestion_injects_after_user_questions_slovak_marked(self):
        # F1: on a session's FIRST AskUserQuestion the always-on user-questions-slovak
        # row consumes the budget and this defers; once that row is marked, a later
        # handover AskUserQuestion injects alone. Prime with a generic question first.
        sid = "sask"
        self._ask("Ktorý variant zvolíme pre layout?", sid)
        self.assertTrue(self._ask(
            "Toto je návrh odovzdávkového vlákna pre klienta na Discuss — schvaľuješ?", sid))

    def test_bare_deploy_instruction_does_not_inject(self):
        self.assertFalse(self._prompt("odovzdaj to na prod a sprav deploy", "s5"))

    def test_write_to_client_email_without_thread_does_not_inject(self):
        # the recall arm requires a Discuss/vlákno signal, not just a client
        self.assertFalse(self._prompt("Napíš klientovi email o faktúre", "s6"))

    def test_unrelated_prompt_does_not_inject(self):
        self.assertFalse(self._prompt("Ako sa mas, aky je dnes den?", "s7"))


class TestProcessSubdevPointer(TestCase):
    """F4: the gatekeeper's release-tail pointer is a POINTER, not a restatement."""

    def setUp(self):
        self.t = norm(read(PROCESS_SUBDEV))

    def test_points_at_the_companion(self):
        self.assertIn("client-facing PROD Discuss handover", self.t)
        self.assertIn("handover-compose.md", self.t)

    def test_does_not_restate_all_five_rules(self):
        self.assertNotIn("the self-blame reassurance", self.t)
        self.assertNotIn("only functions already live on PROD", self.t)


class TestThreadNameEndsWithStreamNumber(TestCase):
    """#532 — every client PROD handover thread title ENDS with the owning
    stream's number (montalu3 → "… 3"), so the owner sees at a glance which
    stream owns the thread. Locks the STABLE core (the numbered-stream rule +
    the owner's own canonical example) plus the base-stream suffix "1", which
    the owner CONFIRMED on #532 (2026-08-18) — base streams are being renamed
    to <name>1 (#537), so the numbering is uniform and no longer interim.

    Teeth per #498/#500: the new rule wraps across several physical lines, so
    a per-line _teeth mixin cannot apply — bound a norm()-collapsed WINDOW to
    the bullet (its unique start anchor → the next `- **` marker) and assert
    the operative tokens inside it, so a PARTIAL revert of the bullet (not
    just a full deletion) fails."""

    START = "The thread NAME ends with the owning stream's NUMBER"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        # The start anchor sits on ONE physical line (verified against the raw
        # file); index on it, then take the raw slice up to the NEXT bullet
        # marker and normalize so the markdown line-wrap collapses away.
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "new bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        # coarse full-deletion guard (clean assertIn message)
        self.assertIn(self.START, self.t)

    def test_numbered_stream_rule_has_teeth(self):
        # (the operative phrase is the window's START anchor — a no-teeth
        # tautology here; it is covered by test_operative_rule_present_whole_file)
        w = self._bullet_window()
        # the owner's own canonical example (montalu3 → "… 3")
        self.assertIn('montalu3 → "Kontrola zákazníckych e-mailov 3"', w)
        # NUMBERED stream = the trailing digits of the stream name
        self.assertIn("trailing digits of the stream name", w)
        self.assertIn("montalu2..8", w)

    def test_unnumbered_base_streams_named(self):
        w = self._bullet_window()
        self.assertIn("UNNUMBERED base stream (montalu, marek, david, simap)", w)

    def test_base_stream_suffix_confirmed(self):
        # owner decision on #532 (2026-08-18): base suffix = "1", renames in #537
        w = self._bullet_window()
        self.assertIn('the suffix is "1"', w)
        self.assertIn("CONFIRMED by the owner", w)
        self.assertIn("#537", w)


class TestGreetingOnlyInFirstMessage(TestCase):
    """#573 — the greeting (oslovenie „Dobrý deň…" / „Ahoj…") belongs ONLY in
    the FIRST (opening) message of a thread; a follow-up reply in the same
    thread carries NO greeting and continues straight at the content. Live
    trigger: the miva PROD thread „Augustová dochádzka" (discuss.channel 19)
    had three same-day follow-ups all reopening with „Dobrý deň…".

    Teeth per #498/#500: the operative rule wraps across several physical
    lines, so a per-line _teeth mixin cannot hold every token — bound a
    norm()-collapsed WINDOW to the new bullet (its unique start anchor → the
    next `- **` marker) and assert the operative tokens inside it, so a
    PARTIAL revert of the bullet (not just a full deletion) fails. The coarse
    whole-file assertIn catches a full deletion; the reassurance-note tokens
    lock the second half of the ticket (the template is the OPENING message)."""

    START = "The greeting (oslovenie"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        # The start anchor sits on ONE physical line (the bullet's first line);
        # index on it, take the raw slice up to the NEXT `- **` bullet marker
        # and normalize so the markdown line-wrap collapses away.
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "greeting bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        # coarse full-deletion guard (clean assertIn message)
        self.assertIn(self.START, self.t)

    def test_greeting_first_message_rule_has_teeth(self):
        w = self._bullet_window()
        self.assertIn("ONLY in the FIRST (opening) message of a thread", w)
        self.assertIn("follow-up reply in an existing thread carries NO greeting", w)

    def test_follow_up_keeps_delivery_and_names_the_incident(self):
        w = self._bullet_window()
        # delivery is unconditional even without a greeting; the mention anchor is not
        self.assertIn("partner_ids", w)
        self.assertIn("for delivery ALWAYS, on every message", w)
        # the live miva PROD incident is named so the rule carries its own why
        self.assertIn("#573", w)
        self.assertIn("Greet once, at the top of the thread", w)

    def test_reassurance_template_marked_as_opening_message(self):
        # the second half of the ticket: the „Ahoj <mená>…" template is the
        # OPENING message, and a follow-up drops the oslovenie
        self.assertIn("OPENING message of the thread", self.t)
        self.assertIn("a follow-up reply drops the oslovenie", self.t)


if __name__ == "__main__":
    main()
