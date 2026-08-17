"""#521 — the handover-PROPOSAL composition rules, unified for ALL sub-dev streams.

Problem: every sub-dev stream composed the client PROD Discuss handover threads
differently and the owner had to re-teach the same rules to each one (montalu5
2026-08-16: a thread proposal with no deep-link URL and no owner-membership
statement; earlier montalu / montalu2). Each stream kept private notes, so the
lesson never spread.

Fix (this ticket): the canonical composition rules live in ONE place — the
`odoo-discuss-xmlrpc` skill's "Composing the handover proposal" section — and
reach the model at PROPOSAL time via a new `hooks/situational-triggers.conf`
UserPromptSubmit binding (`odoo-discuss-handover`), earlier than the skill's
existing `message_post` Write|Edit row (which is code-time, too late for the
proposal shown to the owner). A one-line pointer in `process-subdev` covers the
gatekeeper's own release-tail handover.

These are CONTENT-LOCK tests (the repo's established shape for a SKILL.md / rules
content change): a short single-line anchor + normalize-then-check so a markdown
re-wrap can never silently break them, plus a functional injection test of the
new trigger (fires on a real handover-composition prompt, stays silent on a bare
deploy instruction and unrelated prompts).
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"
PROCESS_SUBDEV = ROOT / "skills" / "process-subdev" / "SKILL.md"
HOOK = ROOT / "hooks" / "inject-situational-rule.sh"
CONF = ROOT / "hooks" / "situational-triggers.conf"

NEEDLE = "Composing the handover proposal"
HANDOVER_TOPIC = "odoo-discuss-handover"
SKILL_BODY_REL = "skills/odoo-discuss-xmlrpc/SKILL.md"


def read(p):
    return p.read_text(encoding="utf-8")


def norm(text):
    """Collapse every run of whitespace (incl. a markdown line-wrap's
    newline+indent) to a single space — so a substring check survives the
    prose re-wrapping under it (internals-tests.md: single-line anchor +
    normalize-then-check)."""
    return " ".join(text.split())


def load_conf_rows():
    """Mirror test_situational_injection.load_conf — (topic, tool, pattern, body)."""
    rows = []
    for line in CONF.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p for p in line.split("\t") if p != ""]
        assert len(parts) in (4, 5), "malformed trigger row: %r" % line
        rows.append(tuple(parts[:4]))
    return rows


class TestCompositionSectionInSkill(TestCase):
    """The canonical composition rules (R1/R2/R3-state/R5-live + reassurance)
    live in the skill body. Each check anchors on a short phrase that sits on
    ONE physical line, run against the normalized whole-skill text."""

    def setUp(self):
        self.raw = read(SKILL)
        self.t = norm(self.raw)

    def test_section_header_present(self):
        self.assertIn("## " + NEEDLE, self.raw)

    def test_is_named_the_single_canonical_cross_stream_rule(self):
        self.assertIn(
            "SINGLE canonical handover-proposal rule for EVERY sub-dev stream", self.t
        )
        # the incident that motivates it, so a future edit can't quietly drop the why
        self.assertIn("montalu5 2026-08-16", self.t)

    def test_r1_complete_proposal_lives_in_the_chat(self):
        self.assertIn("COMPLETE and lives IN THE CHAT", self.t)
        self.assertIn("the exact thread name", self.t)
        self.assertIn("FULL message body verbatim", self.t)
        self.assertIn("the member list", self.t)
        # the explicit ban on "text is on the ticket"
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
        # the literal Slovak reassurance line the client message must end with
        self.assertIn("chyba je na našej strane a hneď to opravíme", self.t)

    def test_description_mentions_handover_composition(self):
        # the frontmatter description is what the model sees in the skill list;
        # it must advertise the composition half, not only the posting recipe
        self.assertIn("Composing the handover proposal", self.raw.split("---", 2)[1])
        self.assertIn("before drafting a handover Discuss thread", self.raw.split("---", 2)[1])


class TestHandoverTriggerRow(TestCase):
    """The new proposal-time load surface: a UserPromptSubmit trigger row
    pointing at the SAME skill body, with a topic distinct from the existing
    message_post row (topics must be unique)."""

    def setUp(self):
        self.rows = load_conf_rows()
        self.by_topic = {r[0]: r for r in self.rows}

    def test_handover_row_exists_and_points_at_the_skill(self):
        self.assertIn(HANDOVER_TOPIC, self.by_topic, "missing #521 handover trigger row")
        topic, tool, pattern, body = self.by_topic[HANDOVER_TOPIC]
        self.assertEqual(tool, "UserPromptSubmit")
        self.assertEqual(body, SKILL_BODY_REL)

    def test_message_post_row_still_present(self):
        # the code-time backstop row must NOT be removed by this change
        self.assertIn("odoo-discuss-xmlrpc", self.by_topic)
        self.assertEqual(self.by_topic["odoo-discuss-xmlrpc"][3], SKILL_BODY_REL)

    def test_topics_still_unique(self):
        topics = [r[0] for r in self.rows]
        self.assertEqual(len(topics), len(set(topics)), "duplicate topic in table")

    def test_body_file_exists(self):
        self.assertTrue((ROOT / self.by_topic[HANDOVER_TOPIC][3]).exists())


class TestHandoverTriggerInjection(TestCase):
    """Functional: the trigger actually loads the composition rules at proposal
    time, and stays silent on a bare deploy instruction / unrelated prompt."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _fires(self, prompt, session_id):
        payload = json.dumps(
            {"session_id": session_id, "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        )
        env = dict(os.environ, TMPDIR=self.tmpdir)
        r = subprocess.run(
            ["bash", str(HOOK)], input=payload, capture_output=True, text=True, env=env
        )
        return NEEDLE in r.stdout

    def test_sk_handover_prompt_injects(self):
        self.assertTrue(
            self._fires("Priprav odovzdávku pre klienta na PROD Discuss vlákno IT-support", "s1")
        )

    def test_sk_napis_prompt_injects(self):
        self.assertTrue(
            self._fires("Napíš odovzdávkovú správu do IT-support Discuss vlákna klientovi", "s2")
        )

    def test_en_handover_prompt_injects(self):
        self.assertTrue(
            self._fires("Compose the handover message to the client's Discuss thread", "s3")
        )

    def test_discuss_first_ordering_injects(self):
        self.assertTrue(
            self._fires("Do Discuss vlákna napíš klientovi odovzdávku hotovej funkcie", "s4")
        )

    def test_bare_deploy_instruction_does_not_inject(self):
        # "odovzdaj to na prod" is a deploy, not a Discuss handover — must stay silent
        self.assertFalse(self._fires("odovzdaj to na prod a sprav deploy", "s5"))

    def test_unrelated_prompt_does_not_inject(self):
        self.assertFalse(self._fires("Ako sa mas, aky je dnes den?", "s6"))

    def test_odovzdaj_report_does_not_inject(self):
        self.assertFalse(self._fires("odovzdaj mi report o CI behu", "s7"))


class TestProcessSubdevPointer(TestCase):
    """The gatekeeper's own release-tail handover points at the canonical section."""

    def test_release_tail_points_at_the_composition_section(self):
        t = norm(read(PROCESS_SUBDEV))
        self.assertIn("client-facing PROD Discuss handover", t)
        self.assertIn('"Composing the handover proposal" section', t)


if __name__ == "__main__":
    main()
