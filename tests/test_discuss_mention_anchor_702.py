"""#702 — every client Discuss message REALLY @mentions its addressees.

Owner ruling (2026-08-25, montalu2, verbatim): „extremne mi vadi ze posles
spravu a peta neoznacis takze ak ma notify na mention tak mu to vobec
nepipne!!! … toto musi byt tvrde pravidlo ludia do discussion oddo musia byt
realne oznaceny" — three approved client messages (montalu PROD threads
262/287, 24.–25.8.) were sent with `partner_ids` only, NO mention anchor in
the body; a client on mentions-only notifications got NO ping (fixed by
unlink+repost, msgs 1742837/1742838).

Locked here (RED against the pre-#702 tree):
  1. `discuss_thread_guard.evaluate_message_post_mention` BLOCKS a stream
     message_post whose content names `partner_ids` but carries NO mention
     anchor token (`o_mail_redirect` / `data-oe-model="res.partner"`); an
     anchored post passes, a post with no visible partner_ids fails OPEN,
     `airuleset:discuss-mention-ok` bypasses (logged, hook-side).
  2. Doctrine: `SKILL.md` makes the anchor MANDATORY for every addressee
     (keeping the verify-against-a-real-19.0-composer caveat, and the minimal
     example itself compliant — the #697 lesson), `handover-compose.md`
     revises the :178 „only where it genuinely belongs" contradiction in
     place and carries the incident bullet.
"""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_thread_guard as g  # noqa: E402

HOOK = ROOT / "hooks" / "block-discuss-thread-name.sh"
SKILL = ROOT / "skills" / "odoo-discuss-xmlrpc" / "SKILL.md"
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"


def norm(s):
    return re.sub(r"\s+", " ", s)


# The composer-emitted mention anchor (single-quoted HTML attrs so the fixture
# nests cleanly inside the double-quoted JSON body string).
ANCHOR = ("<a href='/odoo/res.partner/77' class='o_mail_redirect' "
          "data-oe-id='77' data-oe-model='res.partner'>@Peto</a>")

# The incident shape: signed, approved-shaped body, partner_ids named, NO anchor.
NOANCHOR_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
               '[cid],{"body":"<p>Tabula objednavok je nasadena.</p>'
               '<p>ZbynekAI 2</p>","body_is_html":True,"partner_ids":[7,77]})')
# The compliant counterpart: same post WITH the anchor for the addressee.
ANCHORED_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
               '[cid],{"body":"<p>' + ANCHOR + ' Tabula objednavok je nasadena.'
               '</p><p>ZbynekAI 2</p>","body_is_html":True,'
               '"partner_ids":[7,77]})')
# No partner_ids named at all -> addressees unmeasurable -> the gate fails OPEN.
NOPIDS_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
             '[cid],{"body":"<p>Tabula objednavok je nasadena.</p>'
             '<p>ZbynekAI 2</p>","body_is_html":True})')
# Unsigned variant (for the mention-bypass-does-not-waive-signature pin).
UNSIGNED_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
               '[cid],{"body":"<p>Tabula objednavok je nasadena.</p>",'
               '"body_is_html":True,"partner_ids":[7,77]})')

APPROVAL = "airuleset:owner-approved owner odsúhlasil znenie 2026-08-25"
BINDING = "Discuss-ticket: #4600"
OK_TAIL = "'  # " + APPROVAL + "  " + BINDING


class TestPartnerIdsPresent(unittest.TestCase):
    def test_json_key(self):
        self.assertTrue(g.partner_ids_present('{"partner_ids": [7, 77]}'))

    def test_python_kwarg(self):
        self.assertTrue(g.partner_ids_present("message_post(partner_ids=[7])"))

    def test_single_quoted_dict_key(self):
        self.assertTrue(g.partner_ids_present("{'partner_ids': ids}"))

    def test_absent(self):
        self.assertFalse(g.partner_ids_present('{"body": "<p>x</p>"}'))

    def test_bare_prose_mention_is_not_a_key(self):
        # naming the concept without USING it as a key is not an addressee claim
        self.assertFalse(g.partner_ids_present("the partner_ids list drives delivery"))

    def test_empty_content(self):
        self.assertFalse(g.partner_ids_present(""))
        self.assertFalse(g.partner_ids_present(None))


class TestMentionAnchorPresent(unittest.TestCase):
    def test_o_mail_redirect_class(self):
        self.assertTrue(g.mention_anchor_present("class='o_mail_redirect'"))

    def test_data_oe_model_double_quote(self):
        self.assertTrue(g.mention_anchor_present('data-oe-model="res.partner"'))

    def test_data_oe_model_single_quote(self):
        self.assertTrue(g.mention_anchor_present("data-oe-model='res.partner'"))

    def test_data_oe_model_escaped_quote(self):
        # inside a double-quoted JSON body string the HTML quotes are escaped
        self.assertTrue(g.mention_anchor_present(
            '{"body":"<a data-oe-model=\\"res.partner\\" data-oe-id=\\"7\\">@X</a>"}'))

    def test_record_link_to_another_model_is_not_a_mention(self):
        self.assertFalse(g.mention_anchor_present(
            'data-oe-model="product.product" data-oe-id="5"'))

    def test_plain_text_at_sign_is_not_an_anchor(self):
        # the incident shape: "@Peto" as TEXT pings nobody
        self.assertFalse(g.mention_anchor_present("<p>@Peto pozri prosim</p>"))

    def test_empty_content(self):
        self.assertFalse(g.mention_anchor_present(""))
        self.assertFalse(g.mention_anchor_present(None))


class TestMentionBypassMarker(unittest.TestCase):
    def test_marker_present(self):
        self.assertTrue(g.has_mention_bypass_marker(
            "x airuleset:discuss-mention-ok y"))

    def test_marker_absent(self):
        self.assertFalse(g.has_mention_bypass_marker(NOANCHOR_MP))


class TestEvaluateMessagePostMention(unittest.TestCase):
    def test_partner_ids_without_anchor_is_a_violation(self):
        v = g.evaluate_message_post_mention(NOANCHOR_MP, "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "2")

    def test_anchored_post_passes(self):
        self.assertIsNone(g.evaluate_message_post_mention(ANCHORED_MP, "montalu2"))

    def test_no_visible_partner_ids_fails_open(self):
        # addressees unmeasurable from the payload -> the family's
        # unmeasurable->allow bias (the delivery-half mandate stays doctrine's)
        self.assertIsNone(g.evaluate_message_post_mention(NOPIDS_MP, "montalu2"))

    def test_non_stream_user_is_silent(self):
        self.assertIsNone(g.evaluate_message_post_mention(NOANCHOR_MP, "newlevel"))

    def test_non_message_post_op_is_silent(self):
        create = ("env['discuss.channel'].create({'name': 'Oprava 2', "
                  "'partner_ids': [7]})")
        self.assertIsNone(g.evaluate_message_post_mention(create, "montalu2"))


class _HookBase(unittest.TestCase):
    def run_hook(self, *, command, user="montalu2"):
        payload = {"tool_input": {"command": command}, "cwd": "/some/repo",
                   "session_id": "p702-sess"}
        env = dict(os.environ)
        env["AIRULESET_DISCUSS_STREAM_USER"] = user
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)


class TestHookMention(_HookBase):
    def test_partner_ids_without_anchor_blocks(self):
        r = self.run_hook(command="python3 -c '" + NOANCHOR_MP + OK_TAIL)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#702", r.stderr)
        self.assertIn("o_mail_redirect", r.stderr)

    def test_anchored_post_passes(self):
        r = self.run_hook(command="python3 -c '" + ANCHORED_MP + OK_TAIL)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_partner_ids_post_fails_open(self):
        r = self.run_hook(command="python3 -c '" + NOPIDS_MP + OK_TAIL)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_mention_bypass_passes_internal_post(self):
        r = self.run_hook(command="python3 -c '" + NOANCHOR_MP + OK_TAIL
                          + "  airuleset:discuss-mention-ok interny post")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sibling_bypasses_do_not_waive_mention(self):
        # Independence pin (the #609/#628/#695/#696 review class): name/sig/
        # approval/bind bypasses never waive the mention check. Without this
        # fixture a mutant gating it under any other `if not *_bypassed:`
        # survives the whole suite.
        cmd = ("python3 -c '" + NOANCHOR_MP + "'  # airuleset:discuss-name-ok "
               "airuleset:discuss-sig-ok airuleset:discuss-approval-ok "
               "airuleset:discuss-bind-ok interny post")
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("#702", r.stderr)

    def test_mention_bypass_does_not_waive_signature(self):
        # the other direction: mention-ok never short-circuits the #609 gate
        cmd = ("python3 -c '" + UNSIGNED_MP + OK_TAIL
               + "  airuleset:discuss-mention-ok interny post")
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("signature", r.stderr)

    def test_non_stream_user_passes(self):
        r = self.run_hook(command="python3 -c '" + NOANCHOR_MP + "'",
                          user="newlevel")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctrineSkill(unittest.TestCase):
    """#500/#532-style window teeth on the SKILL.md mandate revision."""

    def _bullet(self):
        text = SKILL.read_text(encoding="utf-8")
        idx = text.find("Mention anchors are MANDATORY")
        self.assertGreater(idx, -1,
                           "SKILL.md is missing the #702 mandate bullet "
                           "(Mention anchors are MANDATORY)")
        nxt = text.find("\n## ", idx)
        return norm(text[idx:nxt if nxt != -1 else len(text)])

    def test_bullet_names_both_halves_and_the_mandate(self):
        b = self._bullet()
        self.assertIn("EVERY addressee", b)
        self.assertIn("#702", b)
        self.assertIn("partner_ids", b)
        self.assertIn('data-oe-model="res.partner"', b)

    def test_bullet_keeps_the_real_composer_caveat(self):
        b = self._bullet()
        self.assertIn("19.0", b)
        self.assertIn("Discuss composer", b)

    def test_minimal_example_is_itself_compliant(self):
        # the #697 lesson: the doctrine's own canonical example must pass the
        # gate it teaches — the code fence carries a real mention anchor
        text = SKILL.read_text(encoding="utf-8")
        idx = text.find("## Minimal correct example")
        self.assertGreater(idx, -1)
        nxt = text.find("\n## ", idx + 10)
        example = text[idx:nxt if nxt != -1 else len(text)]
        self.assertIn("o_mail_redirect", example)


class TestDoctrineCompose(unittest.TestCase):
    def _mention_bullet(self):
        text = COMPOSE.read_text(encoding="utf-8")
        idx = text.find("REÁLNE označený")
        self.assertGreater(idx, -1,
                           "handover-compose.md is missing the #702 bullet "
                           "(Každý adresát je REÁLNE označený)")
        nxt = text.find("\n- **", idx)
        return norm(text[idx:nxt if nxt != -1 else len(text)])

    def test_bullet_names_incident_hook_and_bypass(self):
        b = self._mention_bullet()
        self.assertIn("#702", b)
        self.assertIn("partner_ids", b)
        self.assertIn("o_mail_redirect", b)
        self.assertIn("1742837", b)
        self.assertIn("block-discuss-thread-name.sh", b)
        self.assertIn("airuleset:discuss-mention-ok", b)

    def _greeting_bullet(self):
        text = COMPOSE.read_text(encoding="utf-8")
        idx = text.find("The greeting (oslovenie")
        self.assertGreater(idx, -1)
        nxt = text.find("\n- **", idx)
        return norm(text[idx:nxt if nxt != -1 else len(text)])

    def test_178_contradiction_is_revised_in_place(self):
        gb = self._greeting_bullet()
        # the pre-#702 wording was the OPPOSITE of the mandate — it must be gone
        self.assertNotIn("only where it genuinely belongs", gb)
        self.assertIn("EVERY addressee", gb)
        # the #573-locked delivery clause stays intact
        self.assertIn("for delivery ALWAYS, on every message", gb)


if __name__ == "__main__":
    unittest.main()
