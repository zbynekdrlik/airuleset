"""#718 — the per-owner questions thread (claude-<owner>-q) is provisioned at
INSTALL for the one question-delivery-enabled owner this box delivers AS, so a
david* subdev box stops falling back into the main claude-david thread.

Root cause the fix closes: `--provision-question-thread` WITH create is a
one-time explicit CLI action never wired into install (#296), and the automatic
per-❓ self-heal is `--find-only` (#330), so a `claude-<owner>-q` thread nobody
ever created stays uncreated forever on every david* box. The fix wires the
CREATE-capable provisioning into `cmd_install`, scoped by `resolve_owner()` and
gated by `question_ping_off` (#710: zbynek/marek suppressed → skipped) and
`bot_token`, and reports a LOUD machine-channel gap when it cannot provision.
"""
import ast
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock as m

import notify


def _anchor_then_create_http(new_id="davidq-new"):
    """A Discord double that has no existing thread to find, and creates one
    on POST — the same shape as test_airuleset's
    test_provision_question_thread_creates_and_persists."""
    def fake_http(token, method, path, payload=None):
        if method == "GET":
            return {"parent_id": "pchan"}
        return {"id": new_id}
    return fake_http


class TestProvisionOwnerQuestionThreadForInstall(unittest.TestCase):
    def _env(self, **extra):
        e = {"DISCORD_BOT_TOKEN": "tok",
             "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread"}
        e.update(extra)
        return e

    def test_enabled_owner_david_provisions_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n")
            r = notify.provision_owner_question_thread_for_install(
                env=self._env(), env_path=p, http=_anchor_then_create_http(),
                owner="david")
            self.assertEqual(r["status"], "provisioned")
            self.assertEqual(r["owner"], "david")
            self.assertEqual(r["thread"], "davidq-new")
            self.assertIn("DISCORD_NOTIFICATION_CHANNEL_DAVID_Q=davidq-new",
                          Path(p).read_text())

    def test_suppressed_owner_zbynek_is_skipped_no_http_no_write(self):
        # #710: zbynek's question delivery is OFF -> no -q thread provisioned,
        # NO Discord call, NO .env write. Locks "zbynek/marek unaffected".
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n")
            calls = []

            def spy_http(*a, **k):
                calls.append(a)
                return {"id": "should-not-happen"}

            r = notify.provision_owner_question_thread_for_install(
                env={"DISCORD_BOT_TOKEN": "tok",
                     "DISCORD_NOTIFICATION_CHANNEL_ZBYNEK": "zthread"},
                env_path=p, http=spy_http, owner="zbynek")
            self.assertEqual(r["status"], "skip-suppressed")
            self.assertEqual(r["thread"], "")
            self.assertEqual(calls, [], "suppressed owner must make ZERO Discord calls")
            self.assertNotIn("_Q=", Path(p).read_text())

    def test_suppressed_owner_marek_is_skipped(self):
        r = notify.provision_owner_question_thread_for_install(
            env={"DISCORD_BOT_TOKEN": "tok",
                 "DISCORD_NOTIFICATION_CHANNEL_MAREK": "mthread"},
            http=lambda *a, **k: self.fail("marek must make no Discord call"),
            owner="marek")
        self.assertEqual(r["status"], "skip-suppressed")

    def test_no_owner_is_skipped(self):
        r = notify.provision_owner_question_thread_for_install(
            env=self._env(), owner="")
        self.assertEqual(r["status"], "skip-no-owner")
        self.assertEqual(r["owner"], "")

    def test_no_bot_token_is_skipped_no_http(self):
        r = notify.provision_owner_question_thread_for_install(
            env={"DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread"},
            http=lambda *a, **k: self.fail("no token -> no Discord call"),
            owner="david")
        self.assertEqual(r["status"], "skip-no-token")

    def test_gap_when_thread_cannot_be_provisioned_logs_provision_gap(self):
        # enabled owner + token + anchor, but Discord cannot find/create ->
        # status "gap" AND a durable machine-channel provision-gap log line.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n")
            with m.patch.object(notify, "_claude_dir", return_value=d):
                r = notify.provision_owner_question_thread_for_install(
                    env=self._env(), env_path=p,
                    http=lambda *a, **k: None, owner="david")
                self.assertEqual(r["status"], "gap")
                self.assertEqual(r["thread"], "")
                log = Path(notify.delivery_log_path()).read_text()
            self.assertIn("provision-gap", log)
            self.assertIn("key=david", log)
            # never wrote a bogus _Q key
            self.assertNotIn("_Q=", Path(p).read_text())

    def test_already_provisioned_is_idempotent_no_http(self):
        r = notify.provision_owner_question_thread_for_install(
            env={"DISCORD_BOT_TOKEN": "tok",
                 "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread",
                 "DISCORD_NOTIFICATION_CHANNEL_DAVID_Q": "already"},
            http=lambda *a, **k: self.fail("already-provisioned -> no Discord call"),
            owner="david")
        self.assertEqual(r["status"], "provisioned")
        self.assertEqual(r["thread"], "already")

    def test_never_raises_on_unexpected_error_returns_gap(self):
        def boom(*a, **k):
            raise RuntimeError("discord exploded")
        with tempfile.TemporaryDirectory() as d:
            with m.patch.object(notify, "_claude_dir", return_value=d):
                r = notify.provision_owner_question_thread_for_install(
                    env=self._env(),
                    env_path=os.path.join(d, ".env"),
                    http=boom, owner="david")
        self.assertEqual(r["status"], "gap")   # never raised

    def test_owner_none_resolves_via_resolve_owner(self):
        with m.patch.object(notify, "resolve_owner", return_value="david"), \
             tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n")
            r = notify.provision_owner_question_thread_for_install(
                env=self._env(), env_path=p, http=_anchor_then_create_http())
            self.assertEqual(r["owner"], "david")
            self.assertEqual(r["status"], "provisioned")


class TestFormatQthreadInstallReport(unittest.TestCase):
    def test_provisioned_emits_one_quiet_confirmation_line(self):
        lines = notify.format_qthread_install_report(
            {"owner": "david", "status": "provisioned", "thread": "x"})
        self.assertEqual(len(lines), 1)
        self.assertIn("claude-david-q", lines[0])

    def test_gap_emits_loud_multiline_report_naming_the_anchor_key(self):
        lines = notify.format_qthread_install_report(
            {"owner": "david", "status": "gap", "thread": ""})
        blob = "\n".join(lines)
        self.assertGreaterEqual(len(lines), 2)
        self.assertIn("⚠", blob)                       # loud marker
        self.assertIn("claude-david-q", blob)
        self.assertIn("DISCORD_NOTIFICATION_CHANNEL_DAVID", blob)

    def test_skip_statuses_are_silent(self):
        for st in ("skip-no-owner", "skip-suppressed", "skip-no-token"):
            self.assertEqual(
                notify.format_qthread_install_report(
                    {"owner": "zbynek", "status": st, "thread": ""}),
                [], "status %r must print nothing" % st)


class TestCmdInstallWiresQuestionThreadProvisioning(unittest.TestCase):
    """AST lock (#718 review 🔵): pin that cmd_install's FunctionDef actually
    CALLS the provisioning + report functions. A raw substring check was
    satisfiable by a COMMENT (comment the wiring out, keep a mention within
    4000 chars of check_discord_notify_config, and it would still pass) — the
    AST walk of the function body cannot be fooled that way."""

    def _cmd_install_node(self):
        src = Path(__file__).resolve().parent.parent / "airuleset.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_install":
                return node
        self.fail("cmd_install FunctionDef not found in airuleset.py")
        return None

    def _called_names(self, fn):
        names = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call):
                name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                if name:
                    names.add(name)
        return names

    def test_cmd_install_calls_the_provisioning_functions(self):
        called = self._called_names(self._cmd_install_node())
        self.assertIn(
            "provision_owner_question_thread_for_install", called,
            "cmd_install must CALL the #718 provisioning step, not just mention it")
        self.assertIn(
            "format_qthread_install_report", called,
            "cmd_install must CALL the #718 report formatter")


class TestFindFailureIsNotAbsence(unittest.TestCase):
    """#718 review 🟡: a Discord lookup FAILURE must NOT read as 'thread
    absent' — a create-on-failure would fork a duplicate (the #330 hiccup),
    now re-armed at install frequency. `find_owner_question_thread` returns
    None on a lookup failure vs "" on a conclusive absence; neither
    `provision_question_thread(create=True)` nor the install wrapper creates
    on None."""

    def _env(self):
        return {"DISCORD_BOT_TOKEN": "tok",
                "DISCORD_NOTIFICATION_CHANNEL_DAVID": "dthread"}

    def test_find_returns_none_on_anchor_get_failure(self):
        self.assertIsNone(notify.find_owner_question_thread(
            self._env(), "david", http=lambda *a, **k: None))

    def test_find_returns_none_on_listing_failure(self):
        def http(token, method, path, payload=None):
            if path == "channels/dthread":
                return {"parent_id": "pchan"}   # anchor ok, no guild -> archived
            return None                          # archived listing FAILS
        self.assertIsNone(notify.find_owner_question_thread(
            self._env(), "david", http=http))

    def test_find_returns_empty_on_conclusive_absence(self):
        def http(token, method, path, payload=None):
            if path == "channels/dthread":
                return {"parent_id": "pchan"}
            return {"threads": []}               # every lookup succeeds, no match
        self.assertEqual(notify.find_owner_question_thread(
            self._env(), "david", http=http), "")

    def test_provision_create_true_never_creates_on_lookup_failure(self):
        posts = []

        def http(token, method, path, payload=None):
            if method == "POST":
                posts.append(path)
                return {"id": "should-not-happen"}
            return None                          # every GET fails
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n")
            r = notify.provision_question_thread(
                "david", env=self._env(), env_path=p, http=http, create=True)
        self.assertEqual(r, "")
        self.assertEqual(posts, [],
                         "must NOT POST a create thread on a lookup failure")

    def test_install_wrapper_reports_gap_on_lookup_failure_no_create(self):
        posts = []

        def http(token, method, path, payload=None):
            if method == "POST":
                posts.append(path)
                return {"id": "should-not-happen"}
            return None
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            Path(p).write_text("DISCORD_BOT_TOKEN=tok\n"
                               "DISCORD_NOTIFICATION_CHANNEL_DAVID=dthread\n")
            with m.patch.object(notify, "_claude_dir", return_value=d):
                r = notify.provision_owner_question_thread_for_install(
                    env=self._env(), env_path=p, http=http, owner="david")
        self.assertEqual(r["status"], "gap")
        self.assertEqual(posts, [],
                         "install must never create a thread on a lookup failure")


if __name__ == "__main__":
    unittest.main()
