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
import tempfile
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
        cmd = "python3 -c '" + MESSAGE_POST + "'"
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
        script = ("import xmlrpc.client\n" + MESSAGE_POST + "\n")
        self.assertEqual(self.run_hook(content=script).returncode, 0)


if __name__ == "__main__":
    main()
