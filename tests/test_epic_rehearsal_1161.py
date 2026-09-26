"""tests/test_epic_rehearsal_1161.py -- #1161 part 2 (a)-(c): the epic-rehearsal
hand-off gate + the stream doctrine that feeds it.

A sub-ticket whose body declares `Epic: #N` may be handed off only when epic #N
carries an `Epic-rehearsal:` comment NEWER than the sub-ticket's HEAD commit that
LISTS the sub-ticket. WARN mode (the rollout default) prints what is missing and
allows; ENFORCE refuses. Non-epic tickets are unchanged. Every gh/git read goes
through the `gates.ghread` runner seam, so no test here reaches real gh.
"""
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mk
from datetime import datetime, timezone

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import airuleset  # noqa: E402
from gates import epic_rehearsal as er  # noqa: E402
from gates import ghread  # noqa: E402

SLUG = "zbynekdrlik/odoo-erp"
TICKET = 42
EPIC = 77
HEAD = "a" * 40
# HEAD commit time: 2026-09-20T12:00:00Z
HEAD_TS = int(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc).timestamp())
NEWER = "2026-09-21T08:00:00Z"
OLDER = "2026-09-19T08:00:00Z"
ENFORCE = {er.MODE_ENV: "enforce"}
WARN = {er.MODE_ENV: "warn"}


def _rehearsal(listed=(TICKET,), extra=""):
    subs = " ".join("#%d" % n for n in listed)
    return ("Epic-rehearsal: copy erp-test refresh 8812 at 2026-09-21T07:30Z\n"
            "- release-shaped run: deploy + one-shots 1-3 in order, re-run = 0 changes\n"
            "| acceptance item | result |\n|---|---|\n| stock per card == Money | pass |\n"
            "- included sub-tickets: %s\n%s" % (subs, extra))


def _runner(ticket_body, comments, head_ts=HEAD_TS, fail=()):
    """A fake `runner(argv) -> (rc, out, err)` for the ghread seam. `fail` names
    the call kinds that return a gh error ('issue', 'comments', 'git')."""
    calls = []

    def run(argv):
        calls.append(list(argv))
        if argv[:2] == ["gh", "api"] and argv[2].endswith("/comments"):
            if "comments" in fail:
                return 1, "", "HTTP 502 Bad Gateway"
            return 0, "\n".join(json.dumps(c) for c in comments), ""
        if argv[:2] == ["gh", "api"] and re.match(
                r"^repos/[^/]+/[^/]+/issues/\d+$", argv[2]):
            if "issue" in fail:
                return 1, "", "API rate limit exceeded"
            return 0, json.dumps({"number": TICKET, "body": ticket_body}), ""
        if argv[:2] == ["git", "log"]:
            if "git" in fail:
                return 128, "", "fatal: bad object"
            return 0, "%d\n" % head_ts, ""
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, HEAD + "\n", ""
        return 1, "", "unexpected argv %r" % (argv,)

    run.calls = calls
    return run


def _comment(cid, created, body):
    return {"id": cid, "created_at": created, "body": body}


def _check(ticket_body, comments, env, **kw):
    out = io.StringIO()
    runner = kw.pop("runner", None) or _runner(ticket_body, comments)
    blk, fields = er.composer_check(
        TICKET, SLUG, HEAD, runner=runner, env=env,
        authority=kw.pop("authority", "fork-no-merge"), out=out, **kw)
    return blk, fields, out.getvalue(), runner


class TestParsers(unittest.TestCase):
    def test_epic_ref_shapes(self):
        self.assertEqual(er.parse_epic_ref("Intro\nEpic: #77\n"), 77)
        self.assertEqual(er.parse_epic_ref("- **Epic:** #77 (sklad)"), 77)
        self.assertEqual(er.parse_epic_ref("epic: #77"), 77)

    def test_epic_ref_ignores_fence_quote_and_rehearsal_marker(self):
        self.assertIsNone(er.parse_epic_ref("```\nEpic: #77\n```\n"))
        self.assertIsNone(er.parse_epic_ref("> Epic: #77"))
        self.assertIsNone(er.parse_epic_ref("Epic-rehearsal: #77"))
        self.assertIsNone(er.parse_epic_ref("the epic: #77 is big"))
        self.assertIsNone(er.parse_epic_ref(None))

    def test_rehearsal_marker_line_anchored(self):
        self.assertTrue(er.is_rehearsal_comment(_rehearsal()))
        self.assertTrue(er.is_rehearsal_comment("## Epic-rehearsal: copy x"))
        self.assertFalse(er.is_rehearsal_comment("> Epic-rehearsal: quoted"))
        self.assertFalse(er.is_rehearsal_comment("```\nEpic-rehearsal: x\n```"))
        self.assertFalse(er.is_rehearsal_comment("post an Epic-rehearsal: later"))

    def test_lists_ticket_is_number_exact(self):
        self.assertTrue(er.lists_ticket("subs #42 #43", 42))
        self.assertFalse(er.lists_ticket("subs #420 #4", 42))

    def test_mode_defaults_to_warn(self):
        self.assertEqual(er.DEFAULT_MODE, "warn")
        self.assertEqual(er.gate_mode({}), "warn")
        self.assertEqual(er.gate_mode({er.MODE_ENV: "enforce"}), "enforce")
        self.assertEqual(er.gate_mode({er.MODE_ENV: " ENFORCE "}), "enforce")
        self.assertEqual(er.gate_mode({er.MODE_ENV: "bogus"}), "warn")


class TestComposerCheck(unittest.TestCase):
    BODY = "Fix card names.\n\nEpic: #77\n"

    def test_enforce_refuses_without_rehearsal(self):
        blk, fields, _, _ = _check(self.BODY, [_comment(1, NEWER, "progress")],
                                   ENFORCE)
        self.assertIsNotNone(blk)
        self.assertTrue(blk.startswith("handoff BLOCK"))
        self.assertIn("epic #77 has no `Epic-rehearsal:` comment", blk)
        self.assertIn("#42", blk)
        self.assertIn("REFRESH-DEV-BOX-FROM-PROD", blk)
        self.assertEqual(fields["epic"], 77)
        self.assertIsNone(fields["epic_rehearsal"])

    def test_warn_prints_and_allows(self):
        blk, fields, out, _ = _check(self.BODY, [], WARN)
        self.assertIsNone(blk)
        self.assertIn("handoff WARN", out)
        self.assertIn("epic #77 has no `Epic-rehearsal:` comment", out)
        self.assertEqual(fields["epic_gate"], "warn")
        self.assertEqual(fields["epic"], 77)

    def test_default_mode_is_warn_and_allows(self):
        blk, fields, out, _ = _check(self.BODY, [], {})
        self.assertIsNone(blk)
        self.assertIn("handoff WARN", out)

    def test_passes_with_newer_listing_rehearsal(self):
        comments = [_comment(10, OLDER, _rehearsal()),
                    _comment(11, NEWER, _rehearsal(listed=(41, 42, 43)))]
        blk, fields, out, _ = _check(self.BODY, comments, ENFORCE)
        self.assertIsNone(blk)
        self.assertEqual(out, "")
        self.assertEqual(fields, {"epic": 77, "epic_rehearsal": 11,
                                  "epic_gate": "pass"})

    def test_refuses_stale_rehearsal(self):
        blk, fields, _, _ = _check(
            self.BODY, [_comment(10, OLDER, _rehearsal())], ENFORCE)
        self.assertIsNotNone(blk)
        self.assertIn("older than HEAD aaaaaaaaaaaa", blk)
        self.assertIn("comment 10", blk)
        self.assertEqual(fields["epic_rehearsal"], 10)

    def test_refuses_rehearsal_that_does_not_list_the_ticket(self):
        blk, _, _, _ = _check(
            self.BODY, [_comment(12, NEWER, _rehearsal(listed=(420, 43)))],
            ENFORCE)
        self.assertIsNotNone(blk)
        self.assertIn("does not list #42", blk)

    def test_quoted_rehearsal_does_not_count(self):
        quoted = "\n".join("> " + ln for ln in _rehearsal().splitlines())
        blk, _, _, _ = _check(self.BODY, [_comment(13, NEWER, quoted)], ENFORCE)
        self.assertIn("has no `Epic-rehearsal:` comment", blk or "")

    def test_non_epic_ticket_unchanged_and_reads_no_epic(self):
        blk, fields, out, runner = _check("Plain ticket, no epic.", [], ENFORCE)
        self.assertIsNone(blk)
        self.assertEqual(fields, {})
        self.assertEqual(out, "")
        self.assertFalse(any(a[2].endswith("/comments") for a in runner.calls
                             if a[:2] == ["gh", "api"]))

    def test_self_referencing_epic_is_not_a_sub_ticket(self):
        blk, fields, _, _ = _check("Epic: #42\n", [], ENFORCE)
        self.assertIsNone(blk)
        self.assertEqual(fields, {})

    def test_full_authority_box_is_never_gated_and_reads_nothing(self):
        runner = _runner(self.BODY, [])
        blk, fields, _, _ = _check(self.BODY, [], ENFORCE, runner=runner,
                                   authority="full")
        self.assertIsNone(blk)
        self.assertEqual(fields, {})
        self.assertEqual(runner.calls, [])

    def test_unreadable_ticket_fails_open_with_notice(self):
        runner = _runner(self.BODY, [], fail=("issue",))
        blk, fields, out, _ = _check(self.BODY, [], ENFORCE, runner=runner)
        self.assertIsNone(blk)
        self.assertEqual(fields, {"epic_gate": "unknown"})
        self.assertIn("unknown", out)

    def test_unreadable_epic_comments_fail_open_with_notice(self):
        runner = _runner(self.BODY, [], fail=("comments",))
        blk, fields, out, _ = _check(self.BODY, [], ENFORCE, runner=runner)
        self.assertIsNone(blk)
        self.assertEqual(fields["epic"], 77)
        self.assertEqual(fields["epic_gate"], "unknown")
        self.assertIn("unknown", out)

    def test_unknown_head_time_still_requires_a_listing_rehearsal(self):
        runner = _runner(self.BODY, [], fail=("git",))
        blk, _, _, _ = _check(self.BODY, [], ENFORCE, runner=runner)
        self.assertIn("has no `Epic-rehearsal:` comment", blk or "")
        runner = _runner(self.BODY, [_comment(10, OLDER, _rehearsal())],
                         fail=("git",))
        blk, fields, out, _ = _check(self.BODY, [], ENFORCE, runner=runner)
        self.assertIsNone(blk)
        self.assertEqual(fields["epic_rehearsal"], 10)
        self.assertIn("freshness unverifiable", out)

    def test_head_defaults_to_local_rev_parse(self):
        runner = _runner(self.BODY, [_comment(11, NEWER, _rehearsal())])
        blk, fields = er.composer_check(TICKET, SLUG, None, runner=runner,
                                        env=ENFORCE, authority="fork-no-merge",
                                        out=io.StringIO())
        self.assertIsNone(blk)
        self.assertIn(["git", "rev-parse", "HEAD"], runner.calls)
        self.assertIn(["git", "log", "-1", "--format=%ct", HEAD], runner.calls)


class TestComposerWiring(unittest.TestCase):
    """The gate runs in ALL THREE composer paths and the receipt records it."""

    def _sign_only(self, env, comments):
        body = ("READY-FOR-REVIEW: branch lane-x\n\nSelf-review-model: "
                "claude-opus-4-8\nHEAD: %s\nReady for gatekeeper cross-fork "
                "review.\n" % HEAD[:12])
        seam = _runner("Epic: #77\n", comments)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "body.md")
            with open(path, "w") as f:
                f.write(body)
            home = os.path.expanduser("~")
            gate = os.path.join(td, "g")
            args = argparse.Namespace(
                repo=SLUG, issue=TICKET, branch=None, self_review_file=None,
                root_cause=None, closes_finding=None, prevencia_read=None,
                self_review_model=None, sign_only=path)
            with mk.patch.object(airuleset, "HANDOFF_GATE_DIR",
                                 os.path.relpath(gate, home)), \
                    mk.patch.object(airuleset, "HANDOFF_GATE_LOG",
                                    os.path.relpath(os.path.join(td, "l"), home)), \
                    mk.patch.object(airuleset, "_bounce_round", return_value=1), \
                    mk.patch.object(airuleset, "_stream_self_login",
                                    return_value="x"), \
                    mk.patch("cli_quals.resolve_authority",
                             return_value="fork-no-merge"), \
                    mk.patch.object(ghread, "_run",
                                    lambda argv, *a, **k: seam(argv)), \
                    mk.patch.dict(os.environ, env), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = airuleset.cmd_handoff(args)
            receipts = []
            if os.path.isdir(gate):
                for fn in os.listdir(gate):
                    with open(os.path.join(gate, fn)) as f:
                        receipts.append(json.load(f))
            return rc, out.getvalue(), receipts

    def test_sign_only_enforce_refuses_and_writes_no_receipt(self):
        rc, out, receipts = self._sign_only(ENFORCE, [])
        self.assertEqual(rc, 1, out)
        self.assertIn("has no `Epic-rehearsal:` comment", out)
        self.assertEqual(receipts, [])

    def test_sign_only_pass_receipt_records_epic_and_rehearsal(self):
        rc, out, receipts = self._sign_only(
            ENFORCE, [_comment(99, NEWER, _rehearsal())])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["epic"], 77)
        self.assertEqual(receipts[0]["epic_rehearsal"], 99)
        self.assertEqual(receipts[0]["epic_gate"], "pass")

    def test_sign_only_warn_allows_and_receipt_says_warn(self):
        rc, out, receipts = self._sign_only(WARN, [])
        self.assertEqual(rc, 0, out)
        self.assertIn("handoff WARN", out)
        self.assertEqual(receipts[0]["epic_gate"], "warn")

    def test_compose_and_body_file_paths_call_the_gate(self):
        import inspect
        self.assertEqual(
            inspect.getsource(airuleset.cmd_handoff).count(
                "_handoff_epic_preflight("), 2)
        self.assertIn("_handoff_epic_preflight(", inspect.getsource(
            airuleset._cmd_handoff_post_body_file))


class TestHookReceiptLog(unittest.TestCase):
    """The hook's receipt-match log line carries the receipt's epic fields."""

    def test_receipt_match_logs_epic_and_rehearsal(self):
        import hashlib
        import time
        body = "READY-FOR-REVIEW: branch lane-x\nHEAD: abc12345\n"
        with tempfile.TemporaryDirectory() as td:
            home = os.path.join(td, "home")
            proj = os.path.join(td, "proj")
            os.makedirs(os.path.join(home, ".claude", "handoff-gate"))
            os.makedirs(proj)
            with open(os.path.join(proj, "CLAUDE.md"), "w") as f:
                f.write("<!-- airuleset:authority=fork-no-merge -->\n")
            bpath = os.path.join(proj, "body.md")
            with open(bpath, "w") as f:
                f.write(body)
            with open(os.path.join(home, ".claude", "handoff-gate",
                                   "o-r-42.json"), "w") as f:
                json.dump({"sha256": hashlib.sha256(body.encode()).hexdigest(),
                           "ts": time.time(), "issue": 42, "epic": 77,
                           "epic_rehearsal": 99, "epic_gate": "pass"}, f)
            cmd = "gh issue comment 42 --body-file %s" % bpath
            env = dict(os.environ, HOME=home)
            r = subprocess.run(
                ["bash", os.path.join(_REPO, "hooks",
                                      "block-handoff-without-composer.sh")],
                input=json.dumps({"tool_input": {"command": cmd}}),
                capture_output=True, text=True, timeout=60, cwd=proj, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(home, ".claude", "handoff-gate.log")) as f:
                log = f.read()
        self.assertIn("ALLOW receipt-match epic=#77 rehearsal=99 gate=pass", log)


class TestDoctrineContentLock(unittest.TestCase):
    """#1161 (a): the cross-stream protocol carries the epic rule. The locks pin
    the NEGATION-bearing phrases (#799) and are mutation-verified in-suite with
    a raw-line-local token (#532)."""

    PATH = os.path.join(_REPO, "skills", "autopilot", "references",
                        "cross-stream-protocol.md")
    NEGATIONS = ("never hand off one sub-ticket alone",
                 "is a scope question to the owner, never an implementation")
    NOUNS = ("Epic: #N", "Epic-rehearsal:", "REFRESH-DEV-BOX-FROM-PROD",
             "pass/fail row per epic acceptance item", "Defect-of: #N",
             "AIRULESET_EPIC_REHEARSAL_GATE=enforce", "--post-release",
             "airuleset #1161")

    @staticmethod
    def _norm(s):
        return re.sub(r"\s+", " ", s)

    def _assert_locked(self, src):
        m = re.search(r"^11\. \*\*An epic is rehearsed as a WHOLE.*?(?=^\S|\Z)",
                      src, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "item 11 (epic rehearsal) missing")
        window = self._norm(m.group(0))
        for phrase in self.NEGATIONS + self.NOUNS:
            self.assertIn(phrase, window)

    def _src(self):
        with open(self.PATH, encoding="utf-8") as f:
            return f.read()

    def test_doctrine_locked(self):
        self._assert_locked(self._src())

    def test_negation_lock_has_teeth(self):
        src = self._src()
        for token, repl in (("never hand off one", "hand off one"),
                            ("owner, never an implementation",
                             "owner, or an implementation")):
            self.assertIn(token, src)   # raw-line-local (#532 tripwire)
            mutated = src.replace(token, repl)
            self.assertNotEqual(mutated, src)
            with self.assertRaises(AssertionError):
                self._assert_locked(mutated)


if __name__ == "__main__":
    unittest.main()
