"""Unit tests for discuss_close_guard.py — the #627 close-time Discuss
closing-note gate (owner directive 2026-08-22).

Rule: a ticket that binds an Odoo Discuss thread (`Discuss-thread: <channel-id>`
on the ticket — body or a comment) may be closed ONLY once it ALSO carries a
disposition line — EITHER `Discuss-closed: <msg-id>` (the closing note was posted
into the thread; this is the LAST ticket for the thread) OR `Discuss-defer: <…>`
(the note is deferred to the LAST ticket because sibling tickets remain). The
target property (owner): the LAST message in the thread is always from the
sub-dev, written when the LAST ticket bound to that thread closes — regardless of
who owns/closes the ticket now (the obligation follows the CURRENT owner / the
closing hand, never authorship — the 2026-08-22 owner correction on #627).

The module is a PURE decision (no network): `evaluate_close(issue_json_text)`
returns None to ALLOW the close, or a short reason string to BLOCK. The `.sh`
fetches `gh issue view --json body,comments` and pipes that JSON to the module's
CLI (stdin → "OK"/"BLOCK" on stdout).

Fail-OPEN direction (documented, #514/#492): the gate's DEFAULT is ALLOW (most
closes are not thread-bound), so anything unverifiable — malformed JSON, a
non-object payload — must ALLOW, never block. Recognition itself over-fires SAFE
(a bound ticket with no disposition blocks). This is symmetric to the existing
close hook whose default is BLOCK: each keeps its own safe default when it cannot
verify.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import discuss_close_guard as g  # noqa: E402

MODULE = ROOT / "discuss_close_guard.py"


def _issue(body="", comments=()):
    """Shape a `gh issue view --json body,comments` payload."""
    return json.dumps({"body": body, "comments": [{"body": c} for c in comments]})


class TestRecognition(TestCase):
    def test_not_thread_bound_is_allowed(self):
        # No Discuss-thread marker anywhere → not our concern → ALLOW.
        self.assertIsNone(g.evaluate_close(_issue(body="ordinary ticket, no thread")))

    def test_thread_bound_without_disposition_is_blocked(self):
        # Bound to a thread, no closing-note evidence → BLOCK.
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="Fix X.\n\nDiscuss-thread: 257"))
        )

    def test_binding_in_a_comment_is_recognised(self):
        # The binding may live in a COMMENT (durable-decisions style), not the body.
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="Fix X", comments=["Discuss-thread: 257"]))
        )

    def test_empty_value_after_colon_is_not_a_binding(self):
        # A bare `Discuss-thread:` with no identifier is not a real binding.
        self.assertIsNone(g.evaluate_close(_issue(body="Discuss-thread:")))
        self.assertIsNone(g.evaluate_close(_issue(body="Discuss-thread:   ")))


class TestDisposition(TestCase):
    def test_thread_bound_with_closed_note_is_allowed(self):
        # Closing note posted (last ticket) → ALLOW.
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Discuss-thread: 257",
                    comments=["Discuss-closed: msg 1731999"],
                )
            )
        )

    def test_thread_bound_with_defer_is_allowed(self):
        # Deferred to the last ticket (siblings remain) → ALLOW.
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Discuss-thread: 257",
                    comments=["Discuss-defer: siblings #4811 #4812 still open"],
                )
            )
        )

    def test_defer_needs_a_value_too(self):
        # A bare `Discuss-defer:` with no reason does NOT satisfy the gate.
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="Discuss-thread: 257\nDiscuss-defer:"))
        )

    def test_closed_needs_a_value_too(self):
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="Discuss-thread: 257\nDiscuss-closed:  "))
        )

    def test_disposition_without_binding_is_irrelevant_and_allowed(self):
        # A disposition line but no binding → not thread-bound → ALLOW (the gate
        # only ever fires on a recognised binding).
        self.assertIsNone(g.evaluate_close(_issue(body="Discuss-closed: msg 1")))


class TestFormatting(TestCase):
    def test_markers_are_case_insensitive(self):
        # Recognition must not miss a lower-cased binding (fail toward MORE
        # scrutiny), and a lower-cased disposition still satisfies the gate.
        self.assertIsNotNone(g.evaluate_close(_issue(body="discuss-thread: 257")))
        self.assertIsNone(
            g.evaluate_close(_issue(body="discuss-thread: 257\ndiscuss-closed: msg 9"))
        )

    def test_markers_must_be_line_anchored(self):
        # A mention mid-sentence (not at line start) is not a marker — avoids a
        # prose sentence "the Discuss-thread: field" being read as a binding.
        self.assertIsNone(
            g.evaluate_close(_issue(body="see the Discuss-thread: convention below"))
        )

    def test_leading_whitespace_before_marker_is_tolerated(self):
        # A marker indented under a bullet/quote is still a marker.
        self.assertIsNotNone(g.evaluate_close(_issue(body="  - Discuss-thread: 257")))


class TestFailOpen(TestCase):
    def test_malformed_json_is_allowed(self):
        self.assertIsNone(g.evaluate_close("this is not json"))

    def test_non_object_json_is_allowed(self):
        self.assertIsNone(g.evaluate_close("[1, 2, 3]"))
        self.assertIsNone(g.evaluate_close("null"))

    def test_missing_fields_are_tolerated(self):
        self.assertIsNone(g.evaluate_close("{}"))


class TestCli(TestCase):
    def _run(self, payload):
        p = subprocess.run(
            [sys.executable, str(MODULE)],
            input=payload,
            capture_output=True,
            text=True,
        )
        return p.stdout.strip()

    def test_cli_blocks_bound_without_disposition(self):
        self.assertEqual(
            self._run(_issue(body="Discuss-thread: 257")), "BLOCK"
        )

    def test_cli_allows_bound_with_closed(self):
        self.assertEqual(
            self._run(_issue(body="Discuss-thread: 257\nDiscuss-closed: msg 9")),
            "OK",
        )

    def test_cli_allows_unbound(self):
        self.assertEqual(self._run(_issue(body="ordinary")), "OK")

    def test_cli_allows_garbage_stdin(self):
        # Fail-open at the CLI boundary too.
        self.assertEqual(self._run("not json at all"), "OK")


if __name__ == "__main__":
    main()
