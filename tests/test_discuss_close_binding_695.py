"""#695 — the Discuss closing-note gate is blind without the manual
`Discuss-thread:` mark; bind from the deep URL + require a `Discuss-ticket: #N`
binding marker on every stream message_post + a job-20 audit clause.

Incident (montalu5, 2026-08-25): odoo-erp tickets 4512/4077/4509/3125 were
closed with NO closing note in their bound client Discuss threads because the
#627 gate recognises ONLY the opt-in `Discuss-thread:` mark — the exact stream
that forgets the doctrine forgets the mark too. But the deep URL
`discuss.channel_<N>` IS mandatory on owner-facing ticket prose (#657), so the
durable binding signal already exists on the tickets; the gate just never read
it.

Three parts locked here (RED against the pre-#695 tree):
  1. `discuss_close_guard.is_thread_bound` recognises a `discuss.channel_<N>`
     deep-URL token (the `active_id=discuss.channel_<N>` form contains it as a
     substring) — then `Discuss-closed:`/`Discuss-defer:` are required equally.
  2. `discuss_thread_guard.evaluate_message_post_binding` — EVERY stream
     message_post to a discuss.channel must carry `Discuss-ticket: #N` (a
     falsifiable claim, the #628 model). Deliberate adaptation from the
     ticket's "first post" wording: first-post detection needs durable
     per-stream-per-channel state (stateless hook; lost /tmp state = silent
     skip, the fail-UNSAFE direction) — every-post is stateless and fail-safe.
  3. job-20 partition-audit nudge carries an odoo-erp-scoped DISCUSS-AUDIT
     clause naming the closed-thread-bound-without-disposition audit (the #607
     shape: the watchdog cannot read Discuss / closed tickets on the sweep
     path — #507/#550 — so the DUTY is named in the daily nudge and the
     session's own judgment + gh does the read).
"""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock as m
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_close_guard as cg  # noqa: E402
import discuss_thread_guard as g  # noqa: E402
from watchdog import ops_wait_recheck as owr  # noqa: E402

from _goal_arm_helpers import (  # noqa: E402
    DeliverGoalFakeTmux,
    GOAL_ARMED_CAP,
    _write_marker_transcript,
)

HOOK = ROOT / "hooks" / "block-discuss-thread-name.sh"

DEEP_URL = ("https://erp.montalu.cloud/odoo/discuss?"
            "active_id=discuss.channel_273")


def _issue(body="", comments=()):
    """Shape a `gh issue view --json body,comments` payload."""
    return json.dumps({"body": body, "comments": [{"body": c} for c in comments]})


# --------------------------------------------------------------------------- #
# Part 1 — deep-URL binding recognition in the close guard.
# --------------------------------------------------------------------------- #

class TestDeepUrlBinding(unittest.TestCase):
    def test_deep_url_in_body_binds_without_manual_mark(self):
        # The #657-mandated deep URL alone makes the ticket thread-bound —
        # closing it with no disposition must BLOCK (the montalu5 hole).
        self.assertIsNotNone(
            cg.evaluate_close(_issue(body='Vlákno: „Parkovanie zákaziek 5" — '
                                          + DEEP_URL)))

    def test_bare_deep_token_in_a_comment_binds(self):
        self.assertIsNotNone(
            cg.evaluate_close(_issue(body="Fix X",
                                     comments=["pozri discuss.channel_261"])))

    def test_deep_url_bound_with_closed_note_allows(self):
        self.assertIsNone(
            cg.evaluate_close(_issue(body=DEEP_URL,
                                     comments=["Discuss-closed: msg 1731999"])))

    def test_deep_url_bound_with_defer_allows(self):
        self.assertIsNone(
            cg.evaluate_close(_issue(
                body=DEEP_URL,
                comments=["Discuss-defer: siblings #4077 still open"])))

    def test_channel_field_name_without_digits_is_not_a_binding(self):
        # `discuss.channel_id` / `discuss.channel_member` are model FIELD names
        # (no digits after the underscore) — never a deep-link token.
        self.assertIsNone(
            cg.evaluate_close(_issue(body="code touches discuss.channel_id and "
                                          "discuss.channel_member")))

    def test_deep_token_is_case_insensitive(self):
        # A prose mention may capitalise; recognition over-fires SAFE (#514).
        self.assertIsNotNone(
            cg.evaluate_close(_issue(body="viď Discuss.Channel_273")))

    def test_manual_mark_recognition_is_unchanged(self):
        # The pre-#695 mark path stays byte-identical in behaviour.
        self.assertIsNotNone(
            cg.evaluate_close(_issue(body="Discuss-thread: 257")))
        self.assertIsNone(cg.evaluate_close(_issue(body="ordinary ticket")))


# --------------------------------------------------------------------------- #
# Part 2 — `Discuss-ticket: #N` binding marker on every stream message_post.
# --------------------------------------------------------------------------- #

MESSAGE_POST = ('models.execute_kw(db,uid,key,"discuss.channel","message_post",'
                '[cid],{"body":"<p>Ahoj, hotové.</p><p>ZbynekAI 2</p>"})')
APPROVAL = "airuleset:owner-approved owner odsúhlasil znenie 2026-08-25"
BINDING = "Discuss-ticket: #4512"
FULL_OK = MESSAGE_POST + "  # " + APPROVAL + "  " + BINDING
NO_BIND = MESSAGE_POST + "  # " + APPROVAL


class TestBindingPresent(unittest.TestCase):
    def test_marker_with_hash_number(self):
        self.assertTrue(g.binding_present("# Discuss-ticket: #4512"))

    def test_marker_no_space_after_colon(self):
        self.assertTrue(g.binding_present("Discuss-ticket:#4512"))

    def test_marker_case_insensitive(self):
        self.assertTrue(g.binding_present("discuss-ticket: #12"))

    def test_bare_marker_is_not_a_binding(self):
        self.assertFalse(g.binding_present("# Discuss-ticket:"))
        self.assertFalse(g.binding_present("# Discuss-ticket:   "))

    def test_number_without_hash_is_not_accepted(self):
        # The falsifiable claim is the exact `#N` ref form the block message
        # teaches — a bare number is too easy to satisfy accidentally.
        self.assertFalse(g.binding_present("Discuss-ticket: 4512"))

    def test_empty_content(self):
        self.assertFalse(g.binding_present(""))
        self.assertFalse(g.binding_present(None))


class TestEvaluateMessagePostBinding(unittest.TestCase):
    def test_unbound_stream_post_is_a_violation(self):
        v = g.evaluate_message_post_binding(NO_BIND, "montalu2")
        self.assertIsNotNone(v)
        self.assertEqual(v.number, "2")

    def test_bound_stream_post_passes(self):
        self.assertIsNone(g.evaluate_message_post_binding(FULL_OK, "montalu2"))

    def test_non_stream_user_is_silent(self):
        self.assertIsNone(g.evaluate_message_post_binding(NO_BIND, "newlevel"))

    def test_non_message_post_op_is_silent(self):
        create = "env['discuss.channel'].create({'name': 'Oprava filtra 2'})"
        self.assertIsNone(g.evaluate_message_post_binding(create, "montalu2"))

    def test_bind_bypass_marker_helper(self):
        self.assertTrue(g.has_bind_bypass_marker("x airuleset:discuss-bind-ok y"))
        self.assertFalse(g.has_bind_bypass_marker(NO_BIND))


class _HookBase(unittest.TestCase):
    def run_hook(self, *, command, user="montalu2"):
        payload = {"tool_input": {"command": command}, "cwd": "/some/repo",
                   "session_id": "b695-sess"}
        env = dict(os.environ)
        env["AIRULESET_DISCUSS_STREAM_USER"] = user
        return subprocess.run(["bash", str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)


class TestHookBinding(_HookBase):
    def test_signed_approved_but_unbound_post_blocks(self):
        # The incident shape: everything else compliant, no ticket binding —
        # nothing records `Discuss-thread:` on the ticket, so the close gate
        # stays blind. Must BLOCK and teach the marker + the ticket-side line.
        r = self.run_hook(command="python3 -c '" + NO_BIND + "'")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Discuss-ticket", r.stderr)
        self.assertIn("Discuss-thread", r.stderr)

    def test_bound_post_passes(self):
        r = self.run_hook(command="python3 -c '" + FULL_OK + "'")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_bind_bypass_passes_unbound_internal_post(self):
        cmd = ("python3 -c '" + MESSAGE_POST + "'  # " + APPROVAL
               + "  airuleset:discuss-bind-ok interny kanal bez tiketu")
        self.assertEqual(self.run_hook(command=cmd).returncode, 0)

    def test_non_stream_user_unbound_passes(self):
        r = self.run_hook(command="python3 -c '" + NO_BIND + "'",
                          user="newlevel")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_sig_and_approval_bypass_do_not_waive_binding(self):
        # Independence pin (#695/#696 adversarial reviews — the #609 mutant
        # class): the sibling bypasses must NOT waive the binding. Without
        # this fixture the mutant `if not bind_bypassed and not
        # approval_bypassed:` survives the whole suite, because every other
        # bypass fixture carries either all bypass markers or none.
        cmd = ("python3 -c '" + MESSAGE_POST + "'  # airuleset:discuss-sig-ok "
               "airuleset:discuss-approval-ok interny post, binding chyba")
        r = self.run_hook(command=cmd)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Discuss-ticket", r.stderr)

    def test_binding_does_not_waive_signature(self):
        # A bound but UNSIGNED post must still trip the #609 signature gate.
        unsigned = ('models.execute_kw(db,uid,key,"discuss.channel",'
                    '"message_post",[cid],{"body":"Ahoj"})  # ' + APPROVAL
                    + "  " + BINDING)
        r = self.run_hook(command="python3 -c '" + unsigned + "'")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("ZbynekAI 2", r.stderr)


# --------------------------------------------------------------------------- #
# Part 3 — job-20 DISCUSS-AUDIT clause (odoo-erp-scoped, doctrine-only).
# --------------------------------------------------------------------------- #

NOW = 1_000_000
DAY = 24 * 3600
CAD = 1000


class TestJob20DiscussClause(unittest.TestCase):
    def test_clause_present_when_discuss_scoped(self):
        text = owr._nudge_text(2, [41], NOW, {"41": NOW}, discuss_audit=True)
        self.assertIn("DISCUSS-AUDIT", text)
        self.assertIn("Discuss-closed:", text)
        # the clause carries the exact audit command the session runs itself
        self.assertIn("gh issue list", text)
        self.assertNotIn("\n", text)   # typed keystroke — never a raw newline

    def test_clause_absent_by_default(self):
        text = owr._nudge_text(2, [41], NOW, {"41": NOW})
        self.assertNotIn("DISCUSS-AUDIT", text)

    def test_scope_true_only_for_odoo_erp(self):
        with m.patch.object(owr, "_repo_name_resolver",
                            lambda cwd: "odoo-erp"):
            self.assertTrue(owr._discuss_audit_scope("/any/cwd"))
        with m.patch.object(owr, "_repo_name_resolver",
                            lambda cwd: "airuleset"):
            self.assertFalse(owr._discuss_audit_scope("/any/cwd"))

    def test_scope_fails_safe_on_resolver_error(self):
        def boom(cwd):
            raise RuntimeError("no git")
        with m.patch.object(owr, "_repo_name_resolver", boom):
            self.assertFalse(owr._discuss_audit_scope("/any/cwd"))
        with m.patch.object(owr, "_repo_name_resolver", None):
            self.assertFalse(owr._discuss_audit_scope("/any/cwd"))


class TestJob20OrchestratorWiring(unittest.TestCase):
    CWD = "/home/newlevel/devel/wrecheck695"

    def setUp(self):
        self._sdir = TemporaryDirectory()
        self.addCleanup(self._sdir.cleanup)
        p = m.patch.dict(os.environ,
                         {"AIRULESET_SESSION_STATUS_DIR": self._sdir.name})
        p.start()
        self.addCleanup(p.stop)
        self._proj = TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.tpath = _write_marker_transcript(self._proj.name, self.CWD,
                                              "sess-695-orch")
        self.sid = self.tpath.stem

    def _tmux(self):
        return DeliverGoalFakeTmux([("%9", "claude", self.CWD, "111")],
                                   GOAL_ARMED_CAP, model_type=True,
                                   transcript_path=self.tpath)

    def _run(self, tmux, wrecs):
        return owr.goal_ops_wait_recheck(
            NOW, tmux, wrecs, self.sid, self.CWD, "%9", self.tpath, "sess:0",
            False, set(), ops_wait_fetch=lambda cwd: [41], state={},
            sleep_fn=lambda *a, **k: None, cadence=CAD, i_count=0)

    def test_due_nudge_on_odoo_erp_carries_the_discuss_clause(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        with m.patch.object(owr, "_repo_name_resolver",
                            lambda cwd: "odoo-erp"):
            self._run(tmux, wrecs)
        # "".join — the fake tmux types in CHUNKS that split mid-word, so a
        # space-join would break a token at a chunk boundary.
        typed = "".join(tmux.typed_texts())
        self.assertIn("DISCUSS-AUDIT", typed)

    def test_due_nudge_elsewhere_has_no_discuss_clause(self):
        wrecs = {self.sid: {"first_seen": NOW - 5 * DAY, "last_nudge": None}}
        tmux = self._tmux()
        with m.patch.object(owr, "_repo_name_resolver",
                            lambda cwd: "airuleset"):
            self._run(tmux, wrecs)
        typed = "".join(tmux.typed_texts())
        self.assertIn("stuck-check:", typed)      # the nudge itself delivered
        self.assertNotIn("DISCUSS-AUDIT", typed)


if __name__ == "__main__":
    unittest.main()
