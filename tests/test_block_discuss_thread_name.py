"""Behaviour test for hooks/block-discuss-thread-name.sh + discuss_thread_guard.py
+ cli_aliases.stream_number (#596 + #597 -- hook-enforced client Discuss thread
naming for sub-dev streams).

A sub-dev stream (montaluN, davidN, simapN, mivaN) creating a client Odoo Discuss
channel / sub-thread MUST name it so the name (a) ENDS with the stream's number
(#596 -- montalu2 -> "... 2") and (b) is <= 30 CHARACTERS including that number
(#597 -- the owner keeps hand-shortening long names that hide the number behind
the Odoo sidebar's first page). montalu2 shipped the un-numbered form TWICE on
PROD; the owner escalated to a hook ("nemal dovolene robit taku chybu").

Three layers, mirroring test_block_worker_close_trigger.py:
  * cli_aliases.stream_number -- the single stream-number derivation reused, not
    reinvented (montaluN -> N, base stream -> 1, non-stream -> None);
  * pure-python unit tests of discuss_thread_guard.py (create detection, name
    extraction, compliance, evaluate);
  * stdin-contract hook tests (payload on STDIN, exit 2 + reason on STDERR),
    incl. the fail-safe carve-outs (message_post to an EXISTING channel, a
    non-stream user, the bypass marker) that must NOT block.
"""
import json
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "hooks" / "block-discuss-thread-name.sh"

sys.path.insert(0, str(ROOT))
import discuss_thread_guard as g                                # noqa: E402
import cli_aliases                                              # noqa: E402


# --------------------------------------------------------------------------- #
# Layer 0 -- cli_aliases.stream_number (single derivation, reused not reinvented)
# --------------------------------------------------------------------------- #

class TestStreamNumber(TestCase):
    def test_numbered_montalu_family(self):
        self.assertEqual(cli_aliases.stream_number("montalu2"), "2")
        self.assertEqual(cli_aliases.stream_number("montalu8"), "8")

    def test_numbered_other_families(self):
        self.assertEqual(cli_aliases.stream_number("david4"), "4")
        self.assertEqual(cli_aliases.stream_number("simap1"), "1")
        self.assertEqual(cli_aliases.stream_number("miva2"), "2")

    def test_unnumbered_base_streams_map_to_1(self):
        # #532/#537 convention -- base streams are renamed to <name>1; the suffix
        # is "1". NOT derivable from the \d+ family regexes, so it's the one case
        # short_target_alias's trailing digit cannot supply (miva -> "miva").
        self.assertEqual(cli_aliases.stream_number("montalu"), "1")
        self.assertEqual(cli_aliases.stream_number("david"), "1")
        self.assertEqual(cli_aliases.stream_number("simap"), "1")
        self.assertEqual(cli_aliases.stream_number("miva"), "1")

    def test_non_stream_users_return_none(self):
        for u in ("marek", "gatekeeper", "newlevel", "", None, "bob2", "root"):
            self.assertIsNone(cli_aliases.stream_number(u), u)

    def test_agrees_with_short_target_alias_trailing_digit(self):
        # Drift guard: for a NUMBERED stream whose alias carries a trailing digit,
        # stream_number MUST equal that digit -- the two derivations reuse the
        # same family map and can never diverge. (miva1/base excepted: the alias
        # drops the digit, which is exactly why base->1 is a separate convention.)
        import re
        for u in ("montalu2", "montalu8", "david4", "simap1", "miva2"):
            alias = cli_aliases.short_target_alias(u, "")
            m = re.search(r"(\d+)$", alias)
            self.assertIsNotNone(m, "alias %r has no trailing digit" % alias)
            self.assertEqual(cli_aliases.stream_number(u), m.group(1), u)


# --------------------------------------------------------------------------- #
# Layer 1 -- discuss_thread_guard create detection / name extraction / compliance
# --------------------------------------------------------------------------- #

RPC_CREATE = ('models.execute_kw(db, uid, key, "discuss.channel", "create", '
              '[{"name": "%s"}])')
ORM_CREATE = "env['discuss.channel'].create({'name': '%s'})"
PARENT_CREATE = ('execute_kw(db,uid,k,"discuss.channel","create",'
                 '[{"name":"%s","parent_channel_id":206}])')
MESSAGE_POST = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
                '[cid],{"body":"Ahoj"})')
CHANNEL_WRITE = ('models.execute_kw(db,uid,key,"discuss.channel","write",'
                 '[[cid]],{"name":"%s"})')
# #609 -- message_post signature fixtures. UNSIGNED_MP is the incident shape
# (a client Discuss post with no `ZbynekAI <N>` last line); SIGNED_MP carries
# the mandatory signature for stream 2.
UNSIGNED_MP = MESSAGE_POST                                    # {"body":"Ahoj"}
SIGNED_MP = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
             '[cid],{"body":"<p>Ahoj, hotové.</p><p>ZbynekAI 2</p>"})')
# #628 -- owner-approval fixtures. APPROVAL_EVID is the falsifiable evidence
# marker (a NON-EMPTY reference after airuleset:owner-approved -- never a bare
# assertion). APPROVED_MP is a fully valid client post: signed AND owner-approved.
APPROVAL_EVID = "airuleset:owner-approved owner odsúhlasil znenie 2026-08-22"
APPROVED_MP = SIGNED_MP + "  # " + APPROVAL_EVID


class TestIsChannelCreate(TestCase):
    def test_rpc_create_adjacency(self):
        self.assertTrue(g.is_channel_create(RPC_CREATE % "X 2"))

    def test_orm_create(self):
        self.assertTrue(g.is_channel_create(ORM_CREATE % "X 2"))

    def test_parent_channel_id_with_model(self):
        self.assertTrue(g.is_channel_create(PARENT_CREATE % "X 2"))

    def test_message_post_is_not_create(self):
        self.assertFalse(g.is_channel_create(MESSAGE_POST))

    def test_write_rename_is_not_create(self):
        # the name-correction path MUST stay possible (the owner renamed 274)
        self.assertFalse(g.is_channel_create(CHANNEL_WRITE % "Fixed name 2"))

    def test_bare_model_mention_without_create_signal_is_not_create(self):
        self.assertFalse(g.is_channel_create('rec = env["discuss.channel"].search([])'))

    def test_other_model_create_plus_channel_message_post_is_not_a_channel_create(self):
        # res.partner create adjacency + a discuss.channel message_post must NOT
        # be read as a CHANNEL create (the adjacency guard, false-positive class).
        content = ('execute_kw(d,u,k,"res.partner","create",[{"name":"John"}]); '
                   'execute_kw(d,u,k,"discuss.channel","message_post",[cid],{"body":"x"})')
        self.assertFalse(g.is_channel_create(content))

    def test_create_date_field_is_not_a_create_signal(self):
        self.assertFalse(g.is_channel_create('v = rec["create_date"]; "discuss.channel"'))


class TestChannelNames(TestCase):
    def test_json_name(self):
        self.assertEqual(g.channel_names('{"name": "Foo 2", "parent_channel_id": 3}'),
                         ["Foo 2"])

    def test_kwarg_name_single_quote(self):
        self.assertEqual(g.channel_names("create(name='Bar 2')"), ["Bar 2"])

    def test_excludes_partner_name(self):
        self.assertEqual(g.channel_names('{"partner_name": "John"}'), [])

    def test_excludes_attribute_dot_name(self):
        self.assertEqual(g.channel_names('rec.name = "Y 2"'), [])

    def test_excludes_display_name_and_create_uid(self):
        self.assertEqual(g.channel_names('{"display_name": "Y 2", "create_uid": 1}'), [])


class TestIsCompliant(TestCase):
    def test_owner_good_example_passes(self):
        self.assertTrue(g.is_compliant("Oprava filtra rozmerov 2", "2"))

    def test_shortened_good_example_passes(self):
        self.assertTrue(g.is_compliant("Viditeľnosť leadov 2", "2"))

    def test_missing_number_fails(self):
        self.assertFalse(g.is_compliant("Viditeľnosť leadov pre obchodníkov", "2"))

    def test_owner_bad_example_36_chars_fails_on_length(self):
        # ends with " 2" but is 36 chars -> blocked by the #597 length cap
        name = "Viditeľnosť leadov pre obchodníkov 2"
        self.assertEqual(len(unicodedata.normalize("NFC", name)), 36)
        self.assertFalse(g.is_compliant(name, "2"))

    def test_wrong_trailing_token_fails(self):
        # "22" ends in "2" but the trailing standalone token is "22", not "2"
        self.assertFalse(g.is_compliant("Oprava 22", "2"))

    def test_glued_number_fails(self):
        # the format is "name<space>N"; a glued "X2" is not that
        self.assertFalse(g.is_compliant("X2", "2"))

    def test_length_boundary_exactly_30_passes(self):
        self.assertTrue(g.is_compliant("a" * 28 + " 2", "2"))   # 28 + " 2" == 30

    def test_length_boundary_31_fails(self):
        self.assertFalse(g.is_compliant("a" * 29 + " 2", "2"))  # 31

    def test_diacritics_count_as_one_char_each(self):
        # 30 visible chars of Slovak diacritics + " 2" would be 32 -> fail; a
        # 27-diacritic base + " 2" (== 29) passes. Proves char (not byte) count.
        self.assertTrue(g.is_compliant("ľ" * 27 + " 2", "2"))
        self.assertFalse(g.is_compliant("ľ" * 29 + " 2", "2"))


class TestSuggestName(TestCase):
    def test_suggestion_is_itself_compliant(self):
        for bad in ("Viditeľnosť leadov pre obchodníkov",
                    "Viditeľnosť leadov pre obchodníkov 2",
                    "Nejaký veľmi dlhý názov vlákna ktorý treba skrátiť"):
            s = g.suggest_name(bad, "2")
            self.assertTrue(g.is_compliant(s, "2"), "%r -> %r" % (bad, s))

    def test_suggestion_ends_with_the_number(self):
        self.assertTrue(g.suggest_name("Krátky", "5").endswith(" 5"))


class TestEvaluate(TestCase):
    def test_incident_montalu2_bad_name_is_a_violation(self):
        content = RPC_CREATE % "Viditeľnosť leadov pre obchodníkov"
        v = g.evaluate(content, "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "2")
        self.assertTrue(v.suggestion.endswith(" 2"))

    def test_over_length_named_create_is_a_violation(self):
        content = PARENT_CREATE % "Viditeľnosť leadov pre obchodníkov 2"
        self.assertIsNotNone(g.evaluate(content, "montalu2"))

    def test_compliant_create_passes(self):
        self.assertIsNone(g.evaluate(RPC_CREATE % "Oprava filtra 2", "montalu2"))

    def test_message_post_passes(self):
        self.assertIsNone(g.evaluate(MESSAGE_POST, "montalu2"))

    def test_write_rename_passes(self):
        self.assertIsNone(g.evaluate(CHANNEL_WRITE % "Zlé meno", "montalu2"))

    def test_non_stream_user_is_silent(self):
        content = RPC_CREATE % "Zlé meno bez čísla"
        self.assertIsNone(g.evaluate(content, "newlevel"))
        self.assertIsNone(g.evaluate(content, "marek"))
        self.assertIsNone(g.evaluate(content, ""))
        self.assertIsNone(g.evaluate(content, None))

    def test_create_with_no_literal_name_is_silent(self):
        # a dynamically-named create (name = a variable) cannot be checked; the
        # fleet's unmeasurable->allow bias, documented residual.
        content = ('vals = build_vals()\n'
                   'execute_kw(d,u,k,"discuss.channel","create",[vals])')
        self.assertIsNone(g.evaluate(content, "montalu2"))

    def test_base_stream_requires_1(self):
        self.assertIsNotNone(g.evaluate(RPC_CREATE % "Niečo bez čísla", "montalu"))
        self.assertIsNone(g.evaluate(RPC_CREATE % "Niečo 1", "montalu"))


class TestBypassMarker(TestCase):
    def test_marker_present(self):
        self.assertTrue(g.has_bypass_marker("# airuleset:discuss-name-ok deliberate"))

    def test_marker_absent(self):
        self.assertFalse(g.has_bypass_marker('create(name="X")'))


# --------------------------------------------------------------------------- #
# Layer 2 -- adversarial hardening: ReDoS + false-positive/false-negative bounds
# --------------------------------------------------------------------------- #

class TestReDoSAndBounds(TestCase):
    def test_no_redos_on_repeated_stem_inputs(self):
        # A `\w*`-verb-prefix-next-to-a-gap style ReDoS (the #577 lesson) must not
        # be reintroduced: every detector regex stays linear on repeated stems.
        payloads = ("discuss.channel" * 3000, "create" * 3000, "name" * 3000,
                    "x" * 20000, ('{"name":"' + "a" * 20000 + '"}'))
        start = time.perf_counter()
        for p in payloads:
            g.is_channel_create(p)
            g.channel_names(p)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0, "ReDoS regression: %.3fs" % elapsed)

    def test_compliant_check_is_linear_on_whitespace(self):
        start = time.perf_counter()
        g.is_compliant(" " * 20000 + "2", "2")
        self.assertLess(time.perf_counter() - start, 1.0)


# --------------------------------------------------------------------------- #
# Layer 3 -- the hook end-to-end (stdin JSON contract, exit 2 + STDERR reason)
# --------------------------------------------------------------------------- #

class _HookBase(TestCase):
    def run_hook(self, *, command=None, content=None, new_string=None,
                 user="montalu2"):
        tool_input = {}
        if command is not None:
            tool_input["command"] = command
        if content is not None:
            tool_input["content"] = content
            tool_input["file_path"] = "/tmp/scratch/post.py"
        if new_string is not None:
            tool_input["new_string"] = new_string
            tool_input["file_path"] = "/tmp/scratch/post.py"
        payload = {"tool_input": tool_input, "cwd": "/some/repo",
                   "session_id": "dtn-sess"}
        import os
        env = dict(os.environ)
        if user is None:
            env.pop("AIRULESET_DISCUSS_STREAM_USER", None)
            # force a definitely-non-stream identity for the whoami fallback
            env["AIRULESET_DISCUSS_STREAM_USER"] = "newlevel"
        else:
            env["AIRULESET_DISCUSS_STREAM_USER"] = user
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)


class TestHookBlocks(_HookBase):
    def test_inline_bash_create_missing_number_is_blocked(self):
        cmd = ("python3 -c '" + (RPC_CREATE % "Viditeľnosť leadov pre obchodníkov")
               + "'")
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("596", r.stderr)

    def test_inline_bash_create_over_length_is_blocked(self):
        cmd = "python3 -c '" + (PARENT_CREATE % "Viditeľnosť leadov pre obchodníkov 2") + "'"
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_write_scratch_script_bad_name_is_blocked(self):
        script = ("import xmlrpc.client\n" + (ORM_CREATE % "Nový problém") + "\n")
        r = self.run_hook(content=script)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_edit_inserting_a_bad_create_is_blocked(self):
        r = self.run_hook(new_string=(RPC_CREATE % "Bez čísla vlákno"))
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_heredoc_create_bad_name_is_blocked(self):
        cmd = ("python3 - <<'PY'\n" + (RPC_CREATE % "Zlé meno vlákna") + "\nPY")
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_block_message_names_a_compliant_suggestion(self):
        cmd = "python3 -c '" + (RPC_CREATE % "Viditeľnosť leadov pre obchodníkov") + "'"
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        # the suggestion the block offers ends with the stream number
        self.assertIn(" 2", r.stderr)


class TestHookPasses(_HookBase):
    def test_compliant_create_passes(self):
        cmd = "python3 -c '" + (RPC_CREATE % "Oprava filtra 2") + "'"
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_message_post_to_existing_channel_passes(self):
        # #609: a message_post is not a create (the name guard stays silent) AND,
        # for a stream, it must carry the ZbynekAI <N> signature; #628: it must
        # ALSO carry the owner-approval evidence -- APPROVED_MP has both.
        cmd = "python3 -c '" + APPROVED_MP + "'"
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_channel_rename_write_passes(self):
        cmd = "python3 -c '" + (CHANNEL_WRITE % "Oprava filtra rozmerov 2") + "'"
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_non_stream_user_is_silent(self):
        cmd = "python3 -c '" + (RPC_CREATE % "Zlé meno bez čísla") + "'"
        self.assertEqual(self.run_hook(command=cmd, user=None).returncode, 0)

    def test_bypass_marker_passes(self):
        cmd = ("python3 -c '" + (RPC_CREATE % "Zlé meno")
               + "'  # airuleset:discuss-name-ok legacy thread")
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_non_discuss_command_passes(self):
        self.assertEqual(self.run_hook(command='echo "hello world"').returncode, 0)

    def test_unrelated_odoo_message_post_python_file_passes(self):
        # #609+#628: a SIGNED + owner-APPROVED message_post script passes (not a
        # create; signature present; approval evidence present)
        script = ("import xmlrpc.client\n" + APPROVED_MP + "\n")
        self.assertEqual(self.run_hook(content=script).returncode, 0)


# --------------------------------------------------------------------------- #
# Layer 4 -- adversarial-review fixes (2x fresh-context reviewers)
# --------------------------------------------------------------------------- #

class TestReviewFixMajorA_OrmChain(TestCase):
    """MAJOR-A: an ORM create via the near-universal `.sudo()`/`.with_context()`
    method chain before `.create(` must be detected."""
    def test_sudo_create_detected(self):
        self.assertTrue(g.is_channel_create("env['discuss.channel'].sudo().create({'name':'Bad'})"))

    def test_with_context_create_detected(self):
        self.assertTrue(g.is_channel_create(
            "self.env['discuss.channel'].with_context(x=1).create({'name':'Bad'})"))

    def test_newline_chain_create_detected(self):
        self.assertTrue(g.is_channel_create(
            "env['discuss.channel']\n  .sudo()\n  .create({'name':'Bad'})"))

    def test_chain_ending_in_non_create_is_not_a_create(self):
        for s in ("env['discuss.channel'].sudo().write({'name':'X'})",
                  "env['discuss.channel'].sudo().search([])",
                  "env['discuss.channel'].browse(1).message_post(body='x')"):
            self.assertFalse(g.is_channel_create(s), s)

    def test_sudo_chain_bad_name_evaluates_to_violation(self):
        v = g.evaluate("env['discuss.channel'].sudo().create({'name':'Zle bez cisla'})",
                       "montalu2")
        self.assertIsNotNone(v)


class TestReviewFixMajorB_WriteWithParentNotCreate(TestCase):
    """MAJOR-B: a `write` that sets `parent_channel_id` (a re-parent + rename)
    is NOT a create and must NEVER be blocked -- the name-correction path stays
    open. Keying on the field alone (the pre-fix bug) false-blocked it."""
    def test_write_with_parent_channel_id_is_not_a_create(self):
        content = ('execute_kw(d,u,k,"discuss.channel","write",[[cid]],'
                   '{"name":"Zle dlhe meno bez cisla","parent_channel_id":5})')
        self.assertFalse(g.is_channel_create(content))
        self.assertIsNone(g.evaluate(content, "montalu2"))

    def test_genuine_create_with_parent_is_still_caught(self):
        content = ('execute_kw(d,u,k,"discuss.channel","create",'
                   '[{"name":"Zle bez cisla","parent_channel_id":5}])')
        self.assertIsNotNone(g.evaluate(content, "montalu2"))


class TestReviewFixMajorC_JsonRpc(TestCase):
    """MAJOR-C: a JSON-RPC / call_kw create ("model"+"method":"create") must be
    detected (the header claims JSON-RPC coverage); a JSON-RPC write must not."""
    def test_jsonrpc_create_detected(self):
        self.assertTrue(g.is_channel_create(
            '{"model":"discuss.channel","method":"create","args":[[{"name":"Bad"}]]}'))

    def test_jsonrpc_create_bad_name_is_a_violation(self):
        v = g.evaluate(
            '{"model":"discuss.channel","method":"create","args":[[{"name":"Zle bez cisla"}]]}',
            "montalu2")
        self.assertIsNotNone(v)

    def test_jsonrpc_write_is_not_a_create(self):
        self.assertFalse(g.is_channel_create(
            '{"model":"discuss.channel","method":"write","args":[[cid,{"name":"X"}]]}'))

    def test_jsonrpc_message_post_is_not_a_create(self):
        self.assertFalse(g.is_channel_create(
            '{"model":"discuss.channel","method":"message_post"}'))


class TestReviewFixMultiNameAnySemantics(TestCase):
    """R2 test-gap: the "block iff NO name compliant" decision (`any`, not `all`)
    was unpinned, so a refactor to `all` survived every test. This PINS `any`:
    a create where an UNRELATED name literal is compliant does NOT block, even
    though the channel name is non-compliant. This is the DELIBERATE choice
    (documented residual): `all` would false-block a legit channel create merely
    because an unrelated name (a foreign-model create in the same script) is
    non-compliant -- exactly the "must not false-block unrelated Odoo work" the
    ticket forbids. The realistic single-name thread-create script is unaffected
    (both `any` and `all` block a lone non-compliant channel name)."""
    def test_unrelated_compliant_name_masks_per_any_semantics(self):
        content = ('execute_kw(d,u,k,"res.partner","create",[{"name":"Firma 2"}]); '
                   'execute_kw(d,u,k,"discuss.channel","create",[{"name":"Zle bez cisla"}])')
        # `any(compliant)` -> "Firma 2" is compliant -> allow. `all` would block.
        self.assertIsNone(g.evaluate(content, "montalu2"))

    def test_all_names_non_compliant_still_blocks(self):
        content = ('execute_kw(d,u,k,"res.partner","create",[{"name":"Firma bez"}]); '
                   'execute_kw(d,u,k,"discuss.channel","create",[{"name":"Zle bez cisla"}])')
        self.assertIsNotNone(g.evaluate(content, "montalu2"))


class TestReviewFixHookEndToEnd(_HookBase):
    def test_sudo_chain_create_bad_name_blocked(self):
        cmd = "python3 -c \"env['discuss.channel'].sudo().create({'name':'Zle bez cisla'})\""
        self.assertEqual(self.run_hook(command=cmd).returncode, 2)

    def test_write_with_parent_channel_id_not_blocked(self):
        cmd = ('python3 -c \'execute_kw(d,u,k,"discuss.channel","write",[[cid]],'
               '{"name":"Zle dlhe meno bez cisla","parent_channel_id":5})\'')
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_jsonrpc_create_bad_name_blocked(self):
        script = ('import requests\n'
                  'body = {"model":"discuss.channel","method":"create",'
                  '"args":[[{"name":"Zle bez cisla"}]]}\n')
        self.assertEqual(self.run_hook(content=script).returncode, 2)


# --------------------------------------------------------------------------- #
# Layer 5 -- #609: message_post STREAM-SIGNATURE gate (ZbynekAI <N> last line)
# --------------------------------------------------------------------------- #

class TestIsChannelMessagePost(TestCase):
    def test_rpc_message_post_adjacency(self):
        self.assertTrue(g.is_channel_message_post(UNSIGNED_MP))

    def test_create_is_not_a_message_post(self):
        self.assertFalse(g.is_channel_message_post(RPC_CREATE % "X 2"))

    def test_write_is_not_a_message_post(self):
        self.assertFalse(g.is_channel_message_post(CHANNEL_WRITE % "X 2"))

    def test_orm_chain_message_post_detected(self):
        self.assertTrue(g.is_channel_message_post(
            "env['discuss.channel'].browse(cid).message_post(body='x')"))

    def test_orm_sudo_chain_message_post_detected(self):
        self.assertTrue(g.is_channel_message_post(
            "self.env['discuss.channel'].browse(cid).sudo().message_post(body='x')"))

    def test_jsonrpc_message_post_detected(self):
        self.assertTrue(g.is_channel_message_post(
            '{"model":"discuss.channel","method":"message_post","args":[[cid],{"body":"x"}]}'))

    def test_jsonrpc_create_is_not_a_message_post(self):
        self.assertFalse(g.is_channel_message_post(
            '{"model":"discuss.channel","method":"create"}'))

    def test_message_post_on_other_model_without_discuss_channel_not_detected(self):
        # a sale.order chatter log carries no discuss.channel model string
        self.assertFalse(g.is_channel_message_post(
            'execute_kw(d,u,k,"sale.order","message_post",[oid],{"body":"log"})'))

    def test_jsonrpc_message_post_on_other_model_not_detected(self):
        # review B: the JSON-RPC branch requires BOTH the discuss.channel model
        # AND the message_post method -- a message_post on a NON-discuss model
        # (discuss.channel appearing only in a body string, never as "model")
        # must NOT fire. Pins the `_JSONRPC_MODEL_RE and` requirement (a mutant
        # dropping it survived every other test).
        self.assertFalse(g.is_channel_message_post(
            '{"model":"sale.order","method":"message_post",'
            '"args":[[oid],{"body":"posted about the discuss.channel feature"}]}'))


class TestSignaturePresent(TestCase):
    def test_exact_signature_present(self):
        self.assertTrue(g.signature_present("<p>Hotovo</p><p>ZbynekAI 2</p>", "2"))

    def test_case_insensitive_word(self):
        self.assertTrue(g.signature_present("koniec zbynekai 2", "2"))

    def test_missing_signature(self):
        self.assertFalse(g.signature_present("Ahoj, hotové bez podpisu.", "2"))

    def test_wrong_number_fails(self):
        self.assertFalse(g.signature_present("ZbynekAI 3", "2"))

    def test_no_number_fails(self):
        self.assertFalse(g.signature_present("ZbynekAI", "2"))

    def test_unsubstituted_placeholder_fails(self):
        # a copy-pasted template that never substituted the number is NOT signed
        self.assertFalse(g.signature_present("ZbynekAI <N>", "2"))

    def test_glued_higher_number_not_matched(self):
        # "ZbynekAI 22" is stream 22, never a valid signature for stream 2
        self.assertFalse(g.signature_present("ZbynekAI 22", "2"))

    def test_multichar_number_boundary(self):
        self.assertTrue(g.signature_present("ZbynekAI 10", "10"))
        self.assertFalse(g.signature_present("ZbynekAI 100", "10"))


class TestEvaluateMessagePost(TestCase):
    def test_incident_unsigned_post_is_a_violation(self):
        v = g.evaluate_message_post(UNSIGNED_MP, "montalu6")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "6")
        self.assertEqual(v.expected, "ZbynekAI 6")

    def test_signed_post_passes(self):
        self.assertIsNone(g.evaluate_message_post(SIGNED_MP, "montalu2"))

    def test_wrong_number_signature_is_a_violation(self):
        wrong = ('execute_kw(d,u,k,"discuss.channel","message_post",[cid],'
                 '{"body":"Ahoj ZbynekAI 3"})')
        self.assertIsNotNone(g.evaluate_message_post(wrong, "montalu6"))

    def test_create_is_not_a_message_post_violation(self):
        self.assertIsNone(g.evaluate_message_post(RPC_CREATE % "Zle bez cisla", "montalu2"))

    def test_write_rename_is_not_a_message_post_violation(self):
        self.assertIsNone(g.evaluate_message_post(CHANNEL_WRITE % "Zle", "montalu2"))

    def test_non_stream_user_is_silent(self):
        for u in ("newlevel", "marek", "gatekeeper", "", None):
            self.assertIsNone(g.evaluate_message_post(UNSIGNED_MP, u), u)

    def test_signature_as_variable_elsewhere_in_script_passes(self):
        # the body is a variable but the signature literal is in the same script
        script = ('body = "<p>Hotovo</p><p>ZbynekAI 2</p>"\n'
                  'execute_kw(d,u,k,"discuss.channel","message_post",[cid],{"body":body})')
        self.assertIsNone(g.evaluate_message_post(script, "montalu2"))

    def test_base_stream_requires_signature_1(self):
        self.assertIsNotNone(g.evaluate_message_post(UNSIGNED_MP, "montalu"))
        signed1 = ('execute_kw(d,u,k,"discuss.channel","message_post",[cid],'
                   '{"body":"Ahoj ZbynekAI 1"})')
        self.assertIsNone(g.evaluate_message_post(signed1, "montalu"))


class TestMessagePostReDoSAndBounds(TestCase):
    def test_no_redos_on_repeated_stem_inputs(self):
        payloads = ("message_post" * 3000, "ZbynekAI " * 3000,
                    ('{"body":"' + "a" * 20000 + '"}'), "discuss.channel" * 3000)
        start = time.perf_counter()
        for p in payloads:
            g.is_channel_message_post(p)
            g.signature_present(p, "2")
        self.assertLess(time.perf_counter() - start, 2.0)


class TestHookBlocksSignature(_HookBase):
    def test_unsigned_message_post_by_stream_is_blocked(self):
        r = self.run_hook(command="python3 -c '" + UNSIGNED_MP + "'", user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("598", r.stderr)
        self.assertIn("ZbynekAI 6", r.stderr)

    def test_unsigned_message_post_write_script_blocked(self):
        script = "import xmlrpc.client\n" + UNSIGNED_MP + "\n"
        r = self.run_hook(content=script, user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unsigned_message_post_edit_insert_blocked(self):
        r = self.run_hook(new_string=UNSIGNED_MP, user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_wrong_number_signature_blocked(self):
        wrong = ('execute_kw(d,u,k,"discuss.channel","message_post",[cid],'
                 '{"body":"Ahoj ZbynekAI 3"})')
        r = self.run_hook(command="python3 -c '" + wrong + "'", user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_name_bypass_does_not_waive_signature(self):
        # review A MINOR: the NAME bypass (airuleset:discuss-name-ok) must NOT
        # waive the SIGNATURE check -- an unsigned message_post carrying it still
        # blocks. Pins the load-bearing hook control-flow (a refactor making the
        # name-bypass short-circuit the sig check would otherwise pass silently).
        cmd = ("python3 -c '" + UNSIGNED_MP + "'  # airuleset:discuss-name-ok legacy")
        r = self.run_hook(command=cmd, user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ZbynekAI 6", r.stderr)


class TestHookPassesSignature(_HookBase):
    def test_signed_and_approved_message_post_passes(self):
        # #628: SIGNED_MP alone no longer passes the full hook (it lacks the
        # owner-approval evidence); APPROVED_MP carries ZbynekAI 2 AND the
        # airuleset:owner-approved marker -> passes for the default montalu2 user.
        self.assertEqual(self.run_hook(command="python3 -c '" + APPROVED_MP + "'").returncode, 0)

    def test_non_stream_user_unsigned_post_passes(self):
        self.assertEqual(
            self.run_hook(command="python3 -c '" + UNSIGNED_MP + "'", user=None).returncode, 0)

    def test_sig_bypass_marker_passes(self):
        # a genuine internal channel post bypasses BOTH the signature (#609) and
        # the owner-approval (#628) checks -- it needs both bypass markers.
        cmd = ("python3 -c '" + UNSIGNED_MP + "'  # airuleset:discuss-sig-ok "
               "airuleset:discuss-approval-ok internal channel")
        self.assertEqual(self.run_hook(command=cmd, user="montalu6").returncode, 0)

    def test_non_discuss_message_post_passes(self):
        cmd = ('python3 -c \'execute_kw(d,u,k,"sale.order","message_post",'
               '[oid],{"body":"internal log"})\'')
        self.assertEqual(self.run_hook(command=cmd, user="montalu6").returncode, 0)

    def test_create_still_blocks_bad_name_alongside_signature_gate(self):
        cmd = "python3 -c '" + (RPC_CREATE % "Viditeľnosť leadov pre obchodníkov") + "'"
        self.assertEqual(self.run_hook(command=cmd, user="montalu2").returncode, 2)

    def test_compliant_create_still_passes(self):
        cmd = "python3 -c '" + (RPC_CREATE % "Oprava filtra 2") + "'"
        self.assertEqual(self.run_hook(command=cmd, user="montalu2").returncode, 0)


# --------------------------------------------------------------------------- #
# Layer 6 -- #628: message_post OWNER-APPROVAL gate. A sub-dev stream may not
# post ANY body to a client Discuss channel without recording a falsifiable
# owner-approval claim (`airuleset:owner-approved <ref>`). Sibling of the #609
# signature gate on the SAME message_post path; bypass `airuleset:discuss-
# approval-ok` for a genuine internal/non-client post.
# --------------------------------------------------------------------------- #

class TestApprovalPresent(TestCase):
    def test_marker_with_reference_present(self):
        self.assertTrue(g.approval_present(
            "body=h  # airuleset:owner-approved owner odsúhlasil 2026-08-22"))

    def test_bare_marker_without_reference_is_not_accepted(self):
        # a falsifiable CLAIM needs a non-empty reference, never a bare assertion
        self.assertFalse(g.approval_present("# airuleset:owner-approved"))
        self.assertFalse(g.approval_present("# airuleset:owner-approved   "))
        self.assertFalse(g.approval_present("# airuleset:owner-approved\n"))

    def test_bare_marker_then_later_script_line_is_not_accepted(self):
        # #628 review MAJOR: the reference must be on the marker's OWN line. A
        # `\s+\S` regex spans the newline, so a BARE marker on its own line
        # followed by the message_post call on a LATER line wrongly satisfied
        # `\S` from the call — the common multi-line-script shape. The reference
        # must be same-line horizontal whitespace + a non-whitespace char.
        self.assertFalse(g.approval_present("# airuleset:owner-approved\n" + SIGNED_MP))
        self.assertFalse(g.approval_present("# airuleset:owner-approved\nowner ok"))
        # a genuine SAME-LINE reference still passes
        self.assertTrue(g.approval_present("# airuleset:owner-approved owner ok\n" + SIGNED_MP))

    def test_missing_marker(self):
        self.assertFalse(g.approval_present('{"body":"Ahoj, hotové."}'))

    def test_approval_evidence_in_fixtures(self):
        self.assertTrue(g.approval_present(APPROVED_MP))
        self.assertFalse(g.approval_present(SIGNED_MP))

    def test_bypass_marker_is_not_an_approval_marker(self):
        # the bypass token must NOT satisfy approval_present (distinct concepts)
        self.assertFalse(g.approval_present("# airuleset:discuss-approval-ok"))


class TestApprovalBypassMarker(TestCase):
    def test_present(self):
        self.assertTrue(g.has_approval_bypass_marker(
            "# airuleset:discuss-approval-ok internal"))

    def test_absent(self):
        self.assertFalse(g.has_approval_bypass_marker(SIGNED_MP))
        self.assertFalse(g.has_approval_bypass_marker(APPROVED_MP))


class TestEvaluateMessagePostApproval(TestCase):
    def test_signed_unapproved_post_is_a_violation(self):
        # signature present but NO owner-approval evidence -> a violation
        v = g.evaluate_message_post_approval(SIGNED_MP, "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "2")

    def test_incident_unapproved_post_is_a_violation(self):
        v = g.evaluate_message_post_approval(UNSIGNED_MP, "montalu6")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "6")

    def test_approved_post_passes(self):
        self.assertIsNone(g.evaluate_message_post_approval(APPROVED_MP, "montalu2"))

    def test_create_is_not_a_message_post_approval_violation(self):
        self.assertIsNone(
            g.evaluate_message_post_approval(RPC_CREATE % "Zle bez cisla", "montalu2"))

    def test_write_rename_is_not_a_message_post_approval_violation(self):
        self.assertIsNone(
            g.evaluate_message_post_approval(CHANNEL_WRITE % "Zle", "montalu2"))

    def test_non_stream_user_is_silent(self):
        for u in ("newlevel", "marek", "gatekeeper", "", None):
            self.assertIsNone(g.evaluate_message_post_approval(SIGNED_MP, u), u)

    def test_base_stream_requires_approval(self):
        self.assertIsNotNone(g.evaluate_message_post_approval(SIGNED_MP, "montalu"))
        approved = SIGNED_MP + "  # airuleset:owner-approved ok"
        self.assertIsNone(g.evaluate_message_post_approval(approved, "montalu"))

    def test_approval_evidence_as_variable_elsewhere_passes(self):
        # the marker can live in a comment/variable anywhere in the content
        script = ("# airuleset:owner-approved owner ok 2026-08-22\n" + SIGNED_MP)
        self.assertIsNone(g.evaluate_message_post_approval(script, "montalu2"))


class TestApprovalReDoSAndBounds(TestCase):
    def test_no_redos_on_repeated_inputs(self):
        payloads = ("airuleset:owner-approved" * 3000, "message_post" * 3000,
                    ('{"body":"' + "a" * 20000 + '"}'))
        start = time.perf_counter()
        for p in payloads:
            g.approval_present(p)
            g.is_channel_message_post(p)
        self.assertLess(time.perf_counter() - start, 2.0)


class TestHookBlocksApproval(_HookBase):
    def test_signed_but_unapproved_message_post_is_blocked(self):
        # #628: a SIGNED post with no owner-approval evidence is blocked
        r = self.run_hook(command="python3 -c '" + SIGNED_MP + "'", user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("628", r.stderr)

    def test_bare_marker_on_own_line_in_multiline_script_still_blocks(self):
        # #628 review MAJOR: a bare `airuleset:owner-approved` on its OWN line,
        # then the signed message_post call on a later line, must STILL block —
        # the reference has to be on the marker's own line, never smuggled from
        # the call below it.
        script = "# airuleset:owner-approved\n" + SIGNED_MP + "\n"
        r = self.run_hook(content=script, user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("628", r.stderr)

    def test_signed_but_unapproved_write_script_blocked(self):
        script = "import xmlrpc.client\n" + SIGNED_MP + "\n"
        r = self.run_hook(content=script, user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_signed_but_unapproved_edit_insert_blocked(self):
        r = self.run_hook(new_string=SIGNED_MP, user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_unsigned_unapproved_blocks_for_signature_first(self):
        # precedence: an UNSIGNED + UNAPPROVED post blocks for the SIGNATURE
        # first (the #609 gate runs before the #628 gate), so #609 behaviour is
        # preserved unchanged.
        r = self.run_hook(command="python3 -c '" + UNSIGNED_MP + "'", user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("598", r.stderr)

    def test_sig_bypass_does_not_waive_approval(self):
        # a sig bypass must NOT waive the owner-approval check -- an UNSIGNED
        # post carrying ONLY the sig bypass still blocks for approval (#628).
        cmd = ("python3 -c '" + UNSIGNED_MP + "'  # airuleset:discuss-sig-ok internal")
        r = self.run_hook(command=cmd, user="montalu6")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("628", r.stderr)

    def test_name_bypass_does_not_waive_approval(self):
        # a name bypass must NOT waive the owner-approval check -- a SIGNED
        # (sig-passing) post carrying only the name bypass still blocks (#628).
        cmd = ("python3 -c '" + SIGNED_MP + "'  # airuleset:discuss-name-ok legacy")
        r = self.run_hook(command=cmd, user="montalu2")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("628", r.stderr)


class TestHookPassesApproval(_HookBase):
    def test_signed_and_approved_passes(self):
        cmd = "python3 -c '" + APPROVED_MP + "'"
        self.assertEqual(self.run_hook(command=cmd, user="montalu2").returncode, 0)

    def test_approval_bypass_marker_passes(self):
        # a genuine internal post: unsigned + BOTH bypass markers
        cmd = ("python3 -c '" + UNSIGNED_MP + "'  # airuleset:discuss-sig-ok "
               "airuleset:discuss-approval-ok internal channel")
        self.assertEqual(self.run_hook(command=cmd, user="montalu6").returncode, 0)

    def test_non_stream_user_unapproved_passes(self):
        cmd = "python3 -c '" + SIGNED_MP + "'"
        self.assertEqual(self.run_hook(command=cmd, user=None).returncode, 0)

    def test_non_discuss_message_post_passes(self):
        cmd = ('python3 -c \'execute_kw(d,u,k,"sale.order","message_post",'
               '[oid],{"body":"internal log"})\'')
        self.assertEqual(self.run_hook(command=cmd, user="montalu6").returncode, 0)


# --------------------------------------------------------------------------- #
# Layer 7 -- #641: IDENTITY-AWARE signature word. The mandatory signature token
# is derived from the posting stream's OWNER (notify.STREAM_NOTIFY_OWNER, reused
# via notify.stream_redirect -- NEVER a second stream->owner map): a marek-owned
# stream (montalu4) signs "MarekAI <N>"; every other owner (zbynek, david, ...)
# signs the default "ZbynekAI <N>". Since odoo-erp #3864 montalu4 posts via
# Marek's own handover account (client-visible name "Marek AI - odovzdávky",
# compact "MarekAI"); the old hardcoded ZbynekAI both BLOCKED the truthful
# MarekAI post AND ACCEPTED the wrong-identity ZbynekAI post (live PROD
# contradiction, montalu thread 291 msg 1731669). #609's mechanical teeth stay
# intact in BOTH directions: the wrong identity must keep BLOCKING for a marek
# stream AND for a zbynek stream.
# --------------------------------------------------------------------------- #

# a montalu4 (marek-owned) message_post signed with the CORRECT MarekAI identity
MAREK_MP_MARKEAI = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
                    '[cid],{"body":"<p>Ahoj, hotové.</p><p>MarekAI 4</p>"})')
# the WRONG identity for a marek stream: signed ZbynekAI 4 instead of MarekAI 4
MAREK_MP_WRONG_ZBYNEK = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
                         '[cid],{"body":"<p>Ahoj, hotové.</p><p>ZbynekAI 4</p>"})')
# a fully valid marek client post: MarekAI signature AND owner-approval evidence
MAREK_MP_APPROVED = MAREK_MP_MARKEAI + "  # " + APPROVAL_EVID


class TestSignatureWord(TestCase):
    def test_marek_stream_signs_marekai(self):
        # montalu4 -> owner marek -> "MarekAI" (odoo-erp #3864 handover account)
        self.assertEqual(g.signature_word("montalu4"), "MarekAI")

    def test_zbynek_streams_sign_zbynekai(self):
        for u in ("montalu2", "montalu6", "montalu"):
            self.assertEqual(g.signature_word(u), "ZbynekAI", u)

    def test_default_owner_signs_zbynekai(self):
        # david1 -> owner david (NOT marek) -> the default ZbynekAI word
        self.assertEqual(g.signature_word("david1"), "ZbynekAI")

    def test_unknown_or_empty_user_defaults_to_zbynekai(self):
        for u in ("bob", "", None, "newlevel"):
            self.assertEqual(g.signature_word(u), "ZbynekAI", u)


class TestSignaturePresentIdentityAware(TestCase):
    def test_default_word_backcompat(self):
        # the 2-arg form still defaults to ZbynekAI (unchanged behaviour)
        self.assertTrue(g.signature_present("<p>Hotovo</p><p>ZbynekAI 2</p>", "2"))

    def test_explicit_marek_word_accepts_marekai(self):
        self.assertTrue(g.signature_present("koniec MarekAI 4", "4", "MarekAI"))

    def test_explicit_marek_word_rejects_zbynekai(self):
        # wrong identity for the demanded word -> not present
        self.assertFalse(g.signature_present("koniec ZbynekAI 4", "4", "MarekAI"))

    def test_explicit_default_word_rejects_marekai(self):
        self.assertFalse(g.signature_present("koniec MarekAI 2", "2", "ZbynekAI"))


class TestEvaluateMessagePostIdentity(TestCase):
    def test_marek_stream_expected_is_marekai(self):
        v = g.evaluate_message_post(UNSIGNED_MP, "montalu4")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "4")
        self.assertEqual(v.expected, "MarekAI 4")

    def test_marek_stream_correct_marekai_passes(self):
        self.assertIsNone(g.evaluate_message_post(MAREK_MP_MARKEAI, "montalu4"))

    def test_marek_stream_wrong_zbynekai_is_a_violation(self):
        v = g.evaluate_message_post(MAREK_MP_WRONG_ZBYNEK, "montalu4")
        self.assertIsNotNone(v)
        self.assertEqual(v.expected, "MarekAI 4")

    def test_zbynek_stream_wrong_marekai_is_a_violation(self):
        # the REVERSE direction: a zbynek stream signing MarekAI must still BLOCK
        v = g.evaluate_message_post(
            'execute_kw(d,u,k,"discuss.channel","message_post",'
            '[cid],{"body":"Ahoj MarekAI 2"})', "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.expected, "ZbynekAI 2")

    def test_zbynek_stream_correct_zbynekai_still_passes(self):
        self.assertIsNone(g.evaluate_message_post(SIGNED_MP, "montalu2"))


class TestHookSignatureIdentity(_HookBase):
    def test_marek_stream_wrong_zbynekai_blocked_with_marekai_expected(self):
        r = self.run_hook(command="python3 -c '" + MAREK_MP_WRONG_ZBYNEK + "'",
                          user="montalu4")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("MarekAI 4", r.stderr)
        self.assertNotIn("ZbynekAI 4", r.stderr)

    def test_marek_stream_unsigned_blocked_with_marekai_expected(self):
        r = self.run_hook(command="python3 -c '" + UNSIGNED_MP + "'", user="montalu4")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("MarekAI 4", r.stderr)

    def test_marek_stream_signed_and_approved_passes(self):
        cmd = "python3 -c '" + MAREK_MP_APPROVED + "'"
        self.assertEqual(self.run_hook(command=cmd, user="montalu4").returncode, 0)


if __name__ == "__main__":
    main()
