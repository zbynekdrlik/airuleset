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
import time
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


def hook_max_body():
    """Read MAX_BODY out of the injector — the per-BODY truncation cap (#576).
    A single injected body longer than this is silently TRUNCATED (its tail —
    here the register + reflection rules at the bottom — dropped)."""
    m = re.search(r"MAX_BODY\s*=\s*(\d+)", read(HOOK))
    assert m, "MAX_BODY not found in inject-situational-rule.sh"
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

    def test_both_bodies_actually_inject_on_a_message_post_write(self):
        """The REAL, wrapper-inclusive budget check (found live this ticket): the
        arithmetic guard above sums RAW bodies, but inject-situational-rule.sh
        (line 170) sums the ALREADY-WRAPPED chunks (`<project-rule>…</project-rule>`,
        ~250 B overhead each) + the raw next body against MAX_TOTAL — so raw-body
        arithmetic UNDER-counts, and a compact pointer that "fit" the raw budget
        still DEFERRED the odoo recipe. Drive the real hook: a .py write with
        message_post MUST inject BOTH the odoo recipe AND comprehensive-logging."""
        with tempfile.TemporaryDirectory() as td:
            payload = {"session_id": "cofit-real", "tool_name": "Write",
                       "tool_input": {"file_path": "/repo/importer.py",
                                      "content": "channel.message_post(body=h, body_is_html=True)"}}
            r = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                               capture_output=True, text=True,
                               env=dict(os.environ, TMPDIR=td))
        self.assertEqual(r.returncode, 0, "injector hook must never block: %r" % r.stderr)
        # both co-firing bodies must be present — neither deferred over the real budget
        self.assertIn("Odoo Discuss over XML-RPC", r.stdout,
                      "the odoo message_post recipe DEFERRED — a co-firing body over-grew MAX_TOTAL")
        self.assertIn("Comprehensive Logging", r.stdout,
                      "comprehensive-logging did not inject on a .py message_post write")


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


class TestHandoverBareFollowUpRecall(TestHandoverTriggerInjection):
    """#577 — a BARE follow-up prompt in an existing handover thread (no
    odovzd/handover word, and carrying only ONE of {client, thread} signal)
    must ALSO reach the composition rules. The #521/#573 recall arm required
    BOTH a klient AND a Discuss/vlákno signal (arm 3), or an odovzd/handover
    verb (arms 1-2); a follow-up like „odpovedz klientovi" (reply + client, no
    thread) or „napíš im do vlákna" (write + thread, no client) carried neither
    shape and silently missed handover-compose.md — exactly the scenario the
    #573 greeting-only-in-first-message rule governs.

    Fix (#577): four new proximity arms — a write/reply verb near a
    Discuss/vlákno signal (both orderings), and a REPLY verb near a client
    signal (both orderings). WRITE verbs still require a thread signal, so a
    write-to-client with no thread stays silent (the existing locked negative);
    deploy/prod prompts stay silent (no thread/client signal near a follow-up
    verb). Accepted residuals (a generic non-odoo „vlákno"/„discuss" reply, a
    reply-to-client out of a thread) are documented per the #319 convention in
    the ticket design comment.

    #577 review hardening (adversarial review, this ticket): English reply verbs
    (reply/respond) were DROPPED — the streams work in Slovak and reply/respond
    over-fired on daily English dev prose + substrings (correspond/respondent/
    no-reply); and every verb stem lost its trailing backtrackable \\w* (next to
    the bounded gap it is catastrophic-backtracking ReDoS on a repeated verb-token
    input, a vuln PRE-EXISTING in arms 1-3 too). Both are locked below.
    """

    # --- the gap cases: RED under the pre-#577 pattern ---
    def test_odpovedz_klientovi_injects(self):
        # reply verb + client, NO thread signal
        self.assertTrue(self._prompt("odpovedz klientovi", "r577a"))

    def test_napis_im_do_vlakna_injects(self):
        # write verb + thread, NO client word
        self.assertTrue(self._prompt(
            "napíš im do vlákna Augustová dochádzka", "r577b"))

    def test_odpovedz_do_vlakna_injects(self):
        # reply verb + thread, NO client word
        self.assertTrue(self._prompt(
            "odpovedz do vlákna Augustová dochádzka", "r577c"))

    def test_odpis_klientovi_do_vlakna_injects(self):
        self.assertTrue(self._prompt(
            "odpíš klientovi do IT-support vlákna že je hotovo", "r577d"))

    def test_reverse_thread_then_verb_injects(self):
        # „vo vlákne … napíš" — thread -> verb ordering
        self.assertTrue(self._prompt(
            "vo vlákne mu napíš že funkcia beží", "r577e"))

    def test_reverse_client_then_reply_injects(self):
        # „klientovi odpíš" — client -> reply ordering
        self.assertTrue(self._prompt("klientovi odpíš do vlákna", "r577f"))

    def test_ask_surface_bare_followup_injects(self):
        # the AskUserQuestion (owner-approval) surface, primed first so
        # user-questions-slovak's once-per-session marker is set (#521 residual)
        # — a REPLY-verb follow-up that the pre-#577 arm 3 (write-verb only)
        # would have missed even though it carries both signals.
        sid = "r577ask"
        self._ask("Ktorý variant zvolíme pre layout?", sid)
        self.assertTrue(self._ask(
            "Odpovedz klientovi do vlákna že chyba je opravená — schvaľuješ znenie?", sid))

    # --- negatives the widening must NOT break ---
    def test_deploy_prompt_stays_silent(self):
        self.assertFalse(self._prompt("odovzdaj na prod a reštartuj službu", "n577a"))

    def test_write_client_no_thread_stays_silent(self):
        # WRITE verb + client but NO thread signal — write verbs still require
        # a thread signal, so this stays silent (the locked negative class)
        self.assertFalse(self._prompt("napíš zákazníkovi faktúru mailom", "n577b"))

    def test_reply_to_pr_comment_stays_silent(self):
        self.assertFalse(self._prompt("odpovedz na PR komentár v issue #5", "n577c"))

    def test_write_changelog_stays_silent(self):
        self.assertFalse(self._prompt("napíš changelog a nasaď to na prod", "n577d"))

    def test_unrelated_reply_stays_silent(self):
        self.assertFalse(self._prompt("odpovedz mi kedy máš čas", "n577e"))

    def test_english_reply_respond_verbs_no_longer_fire(self):
        # review A M1 / B m2: English reply/respond were dropped — they over-fired
        # on daily English dev prose and on substrings (correspond / respondent /
        # no-reply). Slovak recall (odpoved/odpíš/reaguj) is unaffected.
        self.assertFalse(self._prompt(
            "reply to the client in the Discuss thread", "n577f"))
        self.assertFalse(self._prompt(
            "respond to the reviewer in the discussion", "n577g"))
        self.assertFalse(self._prompt(
            "correspondence with the client about the reagent order", "n577h"))

    def test_reaguj_stem_avoids_reagent_substring(self):
        # `reaguj` (not bare `reag`) keeps the Slovak imperative but no longer
        # matches the accounting/lab noun "reagent" next to a client signal.
        self.assertTrue(self._prompt("reaguj klientovi vo vlákne", "n577i"))
        self.assertFalse(self._prompt("objednaj reagent pre klienta", "n577j"))

    def test_pattern_is_redos_safe_on_repeated_verb_tokens(self):
        # review B M1: a \w* verb prefix next to the [^\n]{0,N} gap is
        # catastrophic-backtracking ReDoS on a repeated verb-token input
        # (measured ~9 s for "odpoved"*1000, ~14 s for "odovzdav"*1000 pre-fix).
        # Bare stems fix it (<10 ms). Load the REAL conf pattern and assert it
        # completes far under a generous bound even under heavy box load — the
        # fixed timing (~8 ms) vs the vulnerable (~13 s) leaves a ~1600x margin.
        pattern = next(
            (pat for topic, _t, pat, _b in load_conf_rows() if topic == HANDOVER_TOPIC),
            None)
        self.assertIsNotNone(pattern, "handover row not found in the conf")
        rx = re.compile(pattern)
        for tok in ("odpoved", "odovzdav", "napis", "informuj"):
            payload = tok * 1200
            start = time.perf_counter()
            rx.search(payload)
            elapsed = time.perf_counter() - start
            self.assertLess(
                elapsed, 2.0,
                "ReDoS regression: %r*1200 took %.3fs — a backtrackable \\w* verb "
                "prefix was reintroduced next to the [^\\n]{0,N} gap" % (tok, elapsed))


DELIVER = ROOT / "modules" / "core" / "deliver-files-as-urls.md"


class TestMessageSignatureRule(TestCase):
    """#598 — every message posted to a client Odoo Discuss thread ENDS with a
    stream-identity signature line `ZbynekAI <N>`, so the owner sees which
    stream sent it and where to go resolve what the thread discusses. The
    number derivation REUSES the #532 thread-suffix / the
    cli_aliases.short_target_alias family regexes, never a second one.

    Teeth per #498/#500/#532: the rule wraps across several physical lines, so
    bound a norm()-collapsed WINDOW to the new bullet (unique start anchor →
    next `- **`) and assert the operative tokens inside it; the coarse
    whole-file assertIn catches a full deletion, the window a PARTIAL revert."""

    START = "Every message ENDS with a stream-identity signature line"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "signature bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        self.assertIn(self.START, self.t)

    def test_signature_form_and_owner_request(self):
        w = self._bullet_window()
        self.assertIn("ZbynekAI <N>", w)
        self.assertIn("airuleset #598", w)
        self.assertIn("LAST line of", w)

    def test_number_derivation_reuses_not_reinvents(self):
        w = self._bullet_window()
        self.assertIn("montaluN → N", w)
        self.assertIn("UNNUMBERED base stream (montalu, david, simap)", w)
        # REUSE, never a second derivation — the canonical sources
        self.assertIn("cli_aliases.short_target_alias", w)
        self.assertIn("#532 thread-name suffix", w)
        self.assertIn("NEVER a second derivation", w)
        # provenance ACCURACY (both #598 reviews): base → 1 is the #532/#537
        # convention's own mapping, NOT derived from the \\d+ family regexes
        # (those never match a base montalu/simap) — the reuse claim must not
        # over-credit short_target_alias for the base case
        self.assertIn("#532/#537 convention's own mapping", w)
        self.assertIn("NOT derived from those", w)

    def test_signature_stays_in_the_copy_paste_template(self):
        # #598 review MINOR: a stream copies the reassurance template verbatim,
        # so the mandated `ZbynekAI <N>` signature line must appear IN it or the
        # template itself would violate the rule at the point of use.
        i = self.raw.index("Ak ju u seba nevidíte")
        j = self.raw.index("One thread = one topic", i)
        template = self.raw[i:j]
        self.assertIn("ZbynekAI `<N>`", template)

    def test_signature_on_every_message_and_is_poistka(self):
        w = self._bullet_window()
        # unlike the greeting, the signature is on EVERY message
        self.assertIn("never dropped the way the greeting is", w)
        # unambiguous per client Odoo instance
        self.assertIn("only ONE stream family posts", w)
        # a poistka even under a shared account, coexists with a per-stream rename
        self.assertIn("POISTKA", w)
        self.assertIn("odoo-erp #4624", w)


class TestEveryOpenableReferenceCarriesUrl(TestCase):
    """#595 — the message body's URL rule GENERALIZED: every openable reference
    (not only the handed-over feature) carries its direct functional URL,
    verified live, never only a menu path. Owner incident: montalu PROD msg
    1723308 described features by menu path with no URL and was rejected.

    Teeth: bound the window to the (extended) deep-link bullet; the pre-#595
    R2 tokens stay in the same bullet so a full revert also fails test_r2."""

    START = "The message body MUST carry a direct deep-link URL to the LIVE feature"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "deep-link bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_r2_core_still_present(self):
        # the pre-existing R2 tokens are preserved (keeps test_r2 green)
        w = self._bullet_window()
        self.assertIn("direct deep-link URL to the LIVE feature", w)
        self.assertIn("never a menu path", w)

    def test_generalized_to_every_openable_reference(self):
        w = self._bullet_window()
        self.assertIn("EVERY openable reference in the message", w)
        self.assertIn("verified live before sending", w)

    def test_names_the_incident_and_generalizes_report_rule(self):
        w = self._bullet_window()
        self.assertIn("airuleset #595", w)
        self.assertIn("msg 1723308", w)
        self.assertIn("generalizes completion-report.md's 🌐-line rule", w)


class TestModulesCoreUrlReferenceRule(TestCase):
    """#595 — the always-on modules/core rule (extends deliver-files-as-urls.md,
    the nearest 'always a URL' owner) so it reaches EVERY session incl. a
    dispatched worker (a skill body does NOT, #104). Single-physical-line
    paragraph → per-line teeth: the finder line carries every co-token, so a
    partial revert drops them together."""

    FINDER = "A message that REFERENCES an openable thing carries its functional URL"
    COTOKENS = ("#595", "menu path", "1723308", "generalizes",
                "completion-report.md", "Discord ping")

    def setUp(self):
        self.raw = read(DELIVER)

    def test_rule_present(self):
        self.assertIn(self.FINDER, norm(self.raw))

    def test_operative_line_has_teeth(self):
        lines = [ln for ln in self.raw.splitlines() if self.FINDER in ln]
        self.assertTrue(lines, "the #595 operative line is missing from deliver-files-as-urls.md")
        # one physical line carries the finder AND every co-token — a partial
        # revert of that line drops them together
        self.assertTrue(
            any(all(tok in ln for tok in self.COTOKENS) for ln in lines),
            "the #595 operative line must carry all co-tokens: %r" % (self.COTOKENS,))


class TestSkillPointsAtIdentityAndUrlRule(TestCase):
    """#598/#595 — the message-body identity signature + reference-URL rule live
    in the COMPANION, never the recipe. The recipe's #521 co-fit budget is REAL
    and wrapper-inclusive (TestCoFitSizeGuard's functional method) — growing
    SKILL.md even by a compact pointer DEFERRED the odoo recipe on a .py
    message_post write (the exact #521 failure, found live this ticket), so the
    recipe is NOT grown. It ALREADY points at handover-compose.md for the
    message body rules; `ZbynekAI` lives ONLY in the companion so it stays lean."""

    def setUp(self):
        self.raw = read(SKILL)
        self.t = norm(self.raw)

    def test_recipe_points_at_companion_for_body_rules(self):
        # the recipe already routes message-body composition to the companion —
        # no new (budget-consuming) pointer is added
        self.assertIn("handover-compose.md", self.t)
        self.assertIn("its message body must follow the canonical cross-stream rules", self.t)

    def test_recipe_does_not_restate_the_signature_form(self):
        # the operative `ZbynekAI <N>` form + its derivation live ONLY in the
        # companion — a recipe restatement would blow the #521 co-fit budget
        self.assertNotIn("ZbynekAI", self.raw)
        self.assertNotIn("montaluN", self.raw)


class TestApprovalOnEveryClientMessage(TestCase):
    """#628 — owner approval is required for EVERY client-facing Discuss message
    (the OPENING message AND every follow-up reply/question/reminder into an
    EXISTING thread), not just a handover proposal or a new thread — the exact
    mis-reading that caused the montalu6 PROD incident. The mechanical gate is
    tested in test_block_discuss_thread_name.py; here we lock the DOCTRINE.

    Teeth per #500/#532: the rule wraps across several physical lines, so bound a
    norm()-collapsed WINDOW to the bullet (unique start anchor → next `- **`) and
    assert the operative tokens inside it; the coarse whole-file assertIn catches
    a full deletion, the window a PARTIAL revert."""

    START = "The owner must APPROVE the exact text of EVERY client-facing"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "approval bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        self.assertIn(self.START, self.t)

    def test_applies_to_every_message_not_just_thread_creation(self):
        w = self._bullet_window()
        self.assertIn("OPENING message AND every follow-up reply / question / reminder", w)
        self.assertIn("without exception", w)
        # explicitly names + refutes the thread-creation-only mis-reading
        self.assertIn("applying only to thread CREATION is exactly what caused it", w)
        self.assertIn("it applies to every message", w)

    def test_names_the_incident_and_the_mechanical_gate(self):
        w = self._bullet_window()
        self.assertIn("airuleset #628", w)
        self.assertIn("thread 283", w)
        self.assertIn("hooks/block-discuss-thread-name.sh", w)

    def test_falsifiable_evidence_marker_and_bypass(self):
        w = self._bullet_window()
        self.assertIn("airuleset:owner-approved <ref>", w)
        self.assertIn("never a bare", w)
        self.assertIn("airuleset:discuss-approval-ok", w)


class TestReflectClientPreviousAnswerFirst(TestCase):
    """#625 — a new question/message into an EXISTING client thread must react to
    the client's last unreflected answer FIRST; stated so it cannot be read as
    applying only to thread creation (the same #628 mis-reading class).

    Teeth per #500/#532: norm()-collapsed WINDOW bound to the bullet."""

    START = "React to the client's previous answer FIRST"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "reflection bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        self.assertIn(self.START, self.t)

    def test_react_before_asking_and_applies_to_follow_ups(self):
        w = self._bullet_window()
        self.assertIn("never drop a new question", w)
        self.assertIn("only THEN ask the next thing", w)
        # explicitly a follow-up, not just opening — the anti-mis-reading clause
        self.assertIn("applies to a follow-up into an existing thread, not just to opening one", w)

    def test_names_the_incident(self):
        w = self._bullet_window()
        self.assertIn("airuleset #625", w)
        self.assertIn("Etapy zákaziek vo výrobe 1", w)


class TestAddressRegisterPerPerson(TestCase):
    """#625/#626 — the address register is PER PERSON: vykanie only for the CEO,
    tykanie for the other named contacts, VYKANIE by default for anyone not yet
    listed. The register is per-project and extends by one line.

    Teeth per #500/#532: norm()-collapsed WINDOW bound to the bullet — the
    nested `  - **montalu**` sub-bullet is INSIDE the window (an indented `  - **`
    does not match the top-level `\\n- **` window boundary)."""

    START = "Address register PER PERSON"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "register bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        self.assertIn(self.START, self.t)

    def test_per_person_registers_and_default(self):
        w = self._bullet_window()
        self.assertIn("vykanie only for the CEO", w)
        self.assertIn("tykanie for the other", w)
        self.assertIn("VYKANIE by default for anyone not yet listed", w)

    def test_montalu_register_row_names_the_contacts(self):
        w = self._bullet_window()
        # the per-project row (extend by one line), inside the window
        self.assertIn("VYKANIE: CEO Pavol Špetta", w)
        self.assertIn("TYKANIE: Patrik Javorský, Dominik Volek, Peter Hollý", w)
        self.assertIn("DEFAULT for anyone NOT listed here: VYKANIE", w)

    def test_new_person_defaults_to_vykanie(self):
        w = self._bullet_window()
        self.assertIn("A NEW client person you have not been told how to address is VYKANIE", w)
        self.assertIn("#626", w)

    def test_register_is_per_project_and_extends_by_one_line(self):
        w = self._bullet_window()
        self.assertIn("register is PER-PROJECT and grows by ONE line", w)
        self.assertIn("Before every `message_post`", w)


class TestCompanionUnderInjectorBodyCap(TestCase):
    """#628 review NIT: the companion injects on the odoo-discuss-handover
    trigger and is NOT co-fit-budgeted (that guard is only SKILL.md +
    comprehensive-logging), but it IS subject to the injector's per-BODY
    truncation cap (MAX_BODY, #576) — a body over the cap is silently truncated,
    dropping its TAIL (the register + reflection rules sit at the bottom). This
    locks the companion under MAX_BODY so future doctrine growth can never
    silently truncate the delivered rules. Measures what the injector measures
    (the frontmatter-stripped body; handover-compose.md has no frontmatter, so
    it is the whole file)."""

    def test_companion_stays_under_injector_max_body(self):
        body = strip_frontmatter(read(COMPOSE)).strip()
        cap = hook_max_body()
        self.assertLess(
            len(body), cap,
            "handover-compose.md injected body (%d) >= MAX_BODY (%d) — the injector "
            "would TRUNCATE it, dropping the register/reflection rules at the tail. "
            "Condense existing prose; never raise MAX_BODY (fleet-wide blast radius)."
            % (len(body), cap))


class TestApprovalQuestionNamesTargetThreadSeparately(TestCase):
    """#632 — the owner-approval question for a client Discuss message must name
    the target thread CLEARLY and SEPARATELY: the full human thread NAME on its
    OWN labelled line, never only the internal channel number and never only
    wrapped in prose. Applies to the approval question for EVERY message (the
    OPENING handover AND every follow-up), not just a new thread. Owner directive
    2026-08-22; incident montalu1: an approval question for a production-board
    notice referenced its target only as „vlákno 250" and the owner had to ask
    „do akého vlákna to má ísť?".

    Refines the EXISTING R2 (COMPLETE-proposal-in-chat) bullet in place — its
    pre-#632 tokens stay in the same bullet, so a full revert also fails test_r1.

    Teeth per #500/#532/#573: the rule wraps across several physical lines, so
    bound a norm()-collapsed WINDOW to the R2 bullet (its unique start anchor →
    the next `- **` marker) and assert the operative tokens inside it; the coarse
    whole-file assertIn catches a full deletion, the window a PARTIAL revert."""

    START = "The proposal you present to the owner is COMPLETE and lives IN THE CHAT"

    def setUp(self):
        self.raw = read(COMPOSE)
        self.t = norm(self.raw)

    def _bullet_window(self):
        i = self.raw.index(self.START)
        j = self.raw.find("\n- **", i + len(self.START))
        self.assertNotEqual(j, -1, "R2 bullet must be followed by another `- **` bullet")
        return norm(self.raw[i:j])

    def test_operative_rule_present_whole_file(self):
        # coarse full-deletion guard (clean assertIn message)
        self.assertIn(self.START, self.t)

    def test_thread_named_on_its_own_separate_line(self):
        w = self._bullet_window()
        self.assertIn("its OWN SEPARATE, clearly-shown line", w)
        self.assertIn("the full human NAME", w)

    def test_never_internal_number_or_prose_only(self):
        w = self._bullet_window()
        self.assertIn("NEVER only the internal channel number", w)
        self.assertIn("NEVER only wrapped in prose", w)

    def test_standard_vlakno_line_format(self):
        w = self._bullet_window()
        # ASCII-safe tokens of the owner's standard line (avoid fancy-quote
        # matching). Both occur ONCE in the R2 window and vanish together if the
        # `Vlákno:` example line is reverted, so the method keeps real teeth. (A
        # bare "Tabula objednavok 1" assertion was dropped — that token also
        # appears in the incident sentence, so it was toothless; #632 review.)
        self.assertIn("Vlákno:", w)
        self.assertIn("(pod IT-support, montalu PROD)", w)

    def test_applies_to_the_approval_question_for_every_message(self):
        w = self._bullet_window()
        self.assertIn("approval question for EVERY message", w)

    def test_names_the_incident(self):
        w = self._bullet_window()
        self.assertIn("airuleset #632", w)
        self.assertIn("vlákno 250", w)


if __name__ == "__main__":
    main()
