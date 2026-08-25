"""#696 — a client Discuss message may reference ONLY verified PAST events.

Owner ruling (2026-08-25, montalu5, verbatim): „Preco mu chces pisat o
zajtrajsom emaile, vzdy sa treba odvolavat na to co sa udialo nie na to co sa
udeje. Bud mu iniciuj email report teraz a posli ked si si ze mu odisiel a
obsahuje co si mu slubil alebo cakaj do zajtra!!!" — a stream proposed a
client handover (thread 263, montalu PROD) promising „od zajtrajšieho ranného
e-mailu…" while the promised artifact (the digest e-mail) did not yet exist.

Locked here (RED against the pre-#696 tree):
  1. `skills/odoo-discuss-xmlrpc/handover-compose.md` carries the doctrine
     section „Len minulé, overené udalosti" (run the artifact NOW + verify by
     read-back, or wait for the scheduled run — only then write, in the past
     tense).
  2. `discuss_thread_guard.evaluate_message_post_promise` BLOCKS a stream
     message_post whose content carries a Slovak future-promise pattern
     (`od zajtra / zajtrajš / bude (pri|v|obsahovať) / v ďalšom (e-maile|
     reporte) / od budúc / čoskoro / pripravujeme`) with no falsifiable
     `airuleset:artifact-verified <ref>` evidence marker (same-line non-empty
     ref — the #628 shape). Past-tense content passes untouched.
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
COMPOSE = ROOT / "skills" / "odoo-discuss-xmlrpc" / "handover-compose.md"

# The incident shape: signed, future-promise body ("Od zajtrajšieho…" carries
# BOTH the `zajtrajš` stem and `bude pri`).
PROMISE_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
              '[cid],{"body":"<p>Od zajtrajšieho ranného e-mailu bude pri '
              'každej položke aj kód dodávateľa.</p><p>ZbynekAI 2</p>"})')
# The compliant counterpart: past tense, verified content.
PAST_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
           '[cid],{"body":"<p>Dnešný ranný e-mail už obsahoval pri každej '
           'položke aj kód dodávateľa — obsah sme overili.</p>'
           '<p>ZbynekAI 2</p>"})')
ARTIFACT_EVID = ("airuleset:artifact-verified mail.mail id 8123 read-back "
                 "na čerstvej prod-kópii 2026-08-25")
APPROVAL = "airuleset:owner-approved owner odsúhlasil znenie 2026-08-25"
BINDING = "Discuss-ticket: #4600"


class TestPromisePatterns(unittest.TestCase):
    def _hits(self, body):
        return g.promise_phrases(
            'execute_kw(d,u,k,"discuss.channel","message_post",[c],'
            '{"body":"%s"})' % body)

    def test_every_ticket_pattern_fires(self):
        for body in ("platí to od zajtra",
                     "od zajtrajšieho ranného e-mailu",
                     "kód bude pri každej položke",
                     "bude v produkcii",
                     "report bude obsahovať kódy",
                     "v ďalšom e-maile pribudne",
                     "v ďalšom reporte pribudne",
                     "od budúceho týždňa",
                     "čoskoro to uvidíte",
                     "pripravujeme pre vás prehľad"):
            self.assertTrue(self._hits(body), body)

    def test_case_insensitive(self):
        self.assertTrue(self._hits("Od zajtra to uvidíte"))
        self.assertTrue(self._hits("ČOSKORO to uvidíte"))

    def test_ascii_transliterated_slovak_fires(self):
        # Both adversarial reviewers: this fleet demonstrably writes
        # diacritic-less Slovak (the owner's own #696 ruling is ASCII), and a
        # transliteration is the SAME listed phrase, not a rephrasing — the
        # stems fold the diacritic letters into two-char classes.
        for body in ("Od zajtrajsieho ranneho e-mailu dostanete kody",
                     "coskoro to uvidite v systeme",
                     "v dalsom reporte pribudne kod",
                     "v dalsom emaile pribudne kod",
                     "plati to od buduceho tyzdna",
                     "report bude obsahovat kody"):
            self.assertTrue(self._hits(body), body)

    def test_past_tense_and_neutral_bodies_do_not_fire(self):
        for body in ("e-mail už obsahoval kód dodávateľa",
                     "včera odišiel report s kódmi, obsah sme overili",
                     "funkcia je nasadená a overená na PROD",
                     "bude viac času na kontrolu",   # `bude v` needs a boundary
                     "Ahoj, hotové."):
            self.assertFalse(self._hits(body), body)

    def test_word_boundary_on_od(self):
        # "…hod zajtra…" must not read as the phrase "od zajtra".
        self.assertFalse(self._hits("o 15. hod zajtra voláme my vám? nie"))
        # NB "zajtra" alone is deliberately NOT a pattern (the ticket's list);
        # only `od zajtra` / the `zajtrajš` stem fire.


class TestArtifactVerifiedPresent(unittest.TestCase):
    def test_marker_with_same_line_ref(self):
        self.assertTrue(g.artifact_verified_present("# " + ARTIFACT_EVID))

    def test_bare_marker_is_not_evidence(self):
        self.assertFalse(g.artifact_verified_present(
            "# airuleset:artifact-verified"))
        self.assertFalse(g.artifact_verified_present(
            "# airuleset:artifact-verified   "))

    def test_ref_on_a_later_line_does_not_count(self):
        # The ref must sit on the marker's OWN line (the #628 review-MAJOR
        # shape): a bare marker followed by the call on the next line is NOT
        # a falsifiable claim.
        self.assertFalse(g.artifact_verified_present(
            "# airuleset:artifact-verified\n" + PROMISE_MP))

    def test_empty_content(self):
        self.assertFalse(g.artifact_verified_present(""))
        self.assertFalse(g.artifact_verified_present(None))


class TestEvaluateMessagePostPromise(unittest.TestCase):
    def test_future_promise_without_evidence_is_a_violation(self):
        v = g.evaluate_message_post_promise(PROMISE_MP, "montalu5")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "5")
        self.assertTrue(any("zajtrajš" in p for p in v.matched))

    def test_past_tense_passes(self):
        self.assertIsNone(g.evaluate_message_post_promise(PAST_MP, "montalu5"))

    def test_artifact_verified_evidence_passes(self):
        content = PROMISE_MP + "  # " + ARTIFACT_EVID
        self.assertIsNone(g.evaluate_message_post_promise(content, "montalu5"))

    def test_non_stream_user_is_silent(self):
        self.assertIsNone(g.evaluate_message_post_promise(PROMISE_MP, "newlevel"))

    def test_non_message_post_op_is_silent(self):
        create = ("env['discuss.channel'].create({'name': 'Oprava 2'}) "
                  "# od zajtra pripravujeme")
        self.assertIsNone(g.evaluate_message_post_promise(create, "montalu2"))


class _HookBase(unittest.TestCase):
    def run_hook(self, *, command, user="montalu5"):
        payload = {"tool_input": {"command": command}, "cwd": "/some/repo",
                   "session_id": "p696-sess"}
        env = dict(os.environ)
        env["AIRULESET_DISCUSS_STREAM_USER"] = user
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)


class TestHookPromise(_HookBase):
    # Full compliance on every OTHER gate (signature is in the body; approval
    # + binding markers appended), so the promise check alone decides.
    OK_TAIL = "'  # " + APPROVAL + "  " + BINDING

    def test_future_promise_post_blocks(self):
        r = self.run_hook(
            command="python3 -c '"
            + PROMISE_MP.replace("ZbynekAI 2", "ZbynekAI 5") + self.OK_TAIL)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset:artifact-verified", r.stderr)

    def test_future_promise_with_artifact_evidence_passes(self):
        r = self.run_hook(
            command="python3 -c '"
            + PROMISE_MP.replace("ZbynekAI 2", "ZbynekAI 5") + self.OK_TAIL
            + "  " + ARTIFACT_EVID)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_past_tense_post_passes(self):
        r = self.run_hook(
            command="python3 -c '"
            + PAST_MP.replace("ZbynekAI 2", "ZbynekAI 5") + self.OK_TAIL)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_all_sibling_bypasses_do_not_waive_promise(self):
        # Independence pin (#695/#696 adversarial reviews): name/sig/approval/
        # bind bypasses never waive the promise gate — its ONLY escape is the
        # artifact-verified evidence marker. Without this fixture a mutant
        # gating the promise check under any `if not *_bypassed:` survives.
        cmd = ("python3 -c '" + PROMISE_MP + "'  # airuleset:discuss-name-ok "
               "airuleset:discuss-sig-ok airuleset:discuss-approval-ok "
               "airuleset:discuss-bind-ok interny post")
        r = self.run_hook(command=cmd, user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("airuleset:artifact-verified", r.stderr)
        self.assertIn("#696", r.stderr)

    def test_non_stream_user_promise_passes(self):
        r = self.run_hook(command="python3 -c '" + PROMISE_MP + "'",
                          user="newlevel")
        self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctrineSection(unittest.TestCase):
    """#500/#532-style window teeth on the new handover-compose.md section."""

    def _section(self):
        text = COMPOSE.read_text(encoding="utf-8")
        idx = text.find("Len minulé, overené udalosti")
        self.assertGreater(idx, -1,
                           "handover-compose.md is missing the #696 section "
                           "„Len minulé, overené udalosti“")
        nxt = text.find("- **", idx)
        return text[idx:nxt if nxt != -1 else len(text)]

    def test_section_names_the_two_legal_paths(self):
        sec = self._section()
        # run NOW (own authority or a gk action) + verify by read-back …
        self.assertIn("GATEKEEPER-ACTION", sec)
        self.assertIn("read-back", sec)
        # … or wait for the scheduled run — then write in the PAST tense.
        self.assertRegex(sec, r"minulom\s+čase")

    def test_section_names_the_evidence_marker_and_hook(self):
        sec = self._section()
        self.assertIn("airuleset:artifact-verified", sec)
        self.assertIn("block-discuss-thread-name.sh", sec)

    def test_section_cites_the_owner_ruling_incident(self):
        sec = self._section()
        self.assertRegex(sec, re.compile(r"zajtraj", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
