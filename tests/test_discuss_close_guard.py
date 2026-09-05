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

    def test_open_class_members_asterisk_gt_hash_are_recognised(self):
        # Every member of _MARK_OPEN's leading class must be tested — a mutant
        # narrowing it to `[ \t-]` would silently under-recognise these real
        # markdown forms (the dangerous under-block direction). #627 review.
        for prefix in ("* ", "> ", "## ", "*", ">"):
            with self.subTest(prefix=prefix):
                self.assertIsNotNone(
                    g.evaluate_close(_issue(body=prefix + "Discuss-thread: 257")),
                    "prefix %r must be recognised as a binding" % prefix,
                )

    def test_bold_label_with_colon_outside_emphasis_is_recognised(self):
        # `**Discuss-thread**: 257` — the common markdown bold-label form where
        # the colon sits OUTSIDE the `**` emphasis (#627 review R1). Missing it
        # would under-block a genuinely thread-bound ticket.
        self.assertIsNotNone(g.evaluate_close(_issue(body="**Discuss-thread**: 257")))
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="**Discuss-thread**: 257",
                    comments=["**Discuss-closed**: msg 9"],
                )
            )
        )


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


class TestHookIntegration(TestCase):
    """Drives the REAL hooks/block-fork-no-merge-issue-close.sh through its #627
    Discuss section, via the AIRULESET_DISCUSS_CLOSE_FIXTURE test seam (so no
    live `gh` / network). A `gh issue close ...` payload is only INSPECTED — a
    PreToolUse hook never runs the command. The gate is authority-INDEPENDENT
    and odoo-erp-scoped; these lock both."""

    HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"

    def _authority_cwd(self, profile):
        import tempfile
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "# proj\n<!-- airuleset:authority=%s -->\n" % profile
        )
        return d

    def _fixture(self, body="", comments=()):
        import tempfile
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        fd.write(_issue(body=body, comments=comments))
        fd.close()
        return fd.name

    def _run(self, cmd, cwd, fixture=None):
        import os
        payload = json.dumps({"tool_input": {"command": cmd}})
        env = dict(os.environ)
        if fixture is not None:
            env["AIRULESET_DISCUSS_CLOSE_FIXTURE"] = fixture
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )

    def test_blocks_thread_bound_close_without_disposition(self):
        fx = self._fixture(body="Fix X.\n\nDiscuss-thread: 257")
        r = self._run(
            "gh issue close 4811 -R zbynekdrlik/odoo-erp --comment done",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Discuss-thread", r.stderr)
        self.assertIn("handover-compose.md", r.stderr)
        # The load-bearing both-paths routing (#627 review R2): the OWNING stream
        # posts the note; the gatekeeper does NOT post to the client thread.
        self.assertIn("gatekeeper does NOT post", r.stderr)
        self.assertIn("OWNING stream posts", r.stderr)

    def test_gate_is_authority_independent_full_authority_still_blocks(self):
        # A FULL-authority close would sail through the authority guard (exit 0);
        # an exit 2 here proves the Discuss gate fired FIRST — the gatekeeper /
        # branch-merge-close path. The obligation follows the ticket, not authorship.
        fx = self._fixture(body="Discuss-thread: 257")
        r = self._run(
            "gh issue close 4811 -R zbynekdrlik/odoo-erp",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_close_with_closed_note(self):
        fx = self._fixture(
            body="Discuss-thread: 257",
            comments=["Discuss-closed: msg 1731999 (thread 257)"],
        )
        r = self._run(
            "gh issue close 4811 -R zbynekdrlik/odoo-erp --comment done",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allows_close_with_defer(self):
        fx = self._fixture(
            body="Discuss-thread: 257",
            comments=["Discuss-defer: siblings #4812 still open"],
        )
        r = self._run(
            "gh issue close 4811 -R zbynekdrlik/odoo-erp",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_repo_scope_skips_non_odoo_erp(self):
        # The SAME bound-no-disposition fixture on a NON-odoo-erp repo must NOT
        # engage the gate (Discuss threads are an odoo-erp/client thing) — kills
        # the cross-repo meta false-positive. Full authority → overall exit 0.
        fx = self._fixture(body="Discuss-thread: 257")
        r = self._run(
            "gh issue close 627 -R zbynekdrlik/airuleset --comment done",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Discuss-thread", r.stderr)

    def test_bypass_marker_skips_the_gate(self):
        fx = self._fixture(body="Discuss-thread: 257")
        r = self._run(
            "gh issue close 4811 -R zbynekdrlik/odoo-erp "
            "--comment 'closing, airuleset:discuss-close-ok meta ticket'",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_glued_repo_flag_still_engages_the_gate(self):
        # `-Rzbynekdrlik/odoo-erp` (no separator) must still resolve to odoo-erp
        # and engage the gate (#627 review R1 residual, now closed).
        fx = self._fixture(body="Discuss-thread: 257")
        r = self._run(
            "gh issue close 4811 -Rzbynekdrlik/odoo-erp",
            self._authority_cwd("full"),
            fixture=fx,
        )
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_repo_resolves_from_cwd_remote(self):
        # No -R flag: the odoo-erp scope must resolve from the cwd git remote
        # (the shared #760 _repo_owner_repo_of helper's cwd-remote branch on the
        # #627 side — the branch no explicit-R #627 test exercised). A bound,
        # note-less ticket then still BLOCKS. Characterisation: byte-for-byte
        # with the pre-#760 inline #627 parse, which had the same fallback.
        import subprocess as sp
        cwd = self._authority_cwd("full")
        sp.run(["git", "init", "-q"], cwd=cwd)
        sp.run(["git", "remote", "add", "origin",
                "https://github.com/zbynekdrlik/odoo-erp.git"], cwd=cwd)
        fx = self._fixture(body="Discuss-thread: 257")
        r = self._run("gh issue close 4811", cwd, fixture=fx)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Discuss-thread", r.stderr)


class TestHookIntegrationCompound(TestCase):
    """A COMPOUND that batch-closes sibling tickets of one thread (the likely
    N-tickets-one-thread flow) must have EVERY `gh issue close <N>` target
    checked, not just the first — else a bound sibling smuggles a note-less
    close past a head-1 check (#627 review MAJOR). Uses a per-number fake `gh`
    (the fixture seam is one file, so it cannot distinguish targets)."""

    HOOK = ROOT / "hooks" / "block-fork-no-merge-issue-close.sh"

    # Returns a bound-no-disposition ticket for $FAKE_BOUND_NUM, else an
    # ordinary (unbound) ticket. Only `gh issue view` is exercised.
    _FAKE_GH = (
        "#!/usr/bin/env bash\n"
        'if [ "$1 $2" = "issue view" ]; then\n'
        '  num=""\n'
        '  for a in "$@"; do\n'
        '    if [[ "$a" =~ ^[0-9]+$ ]]; then num="$a"; break; fi\n'
        "  done\n"
        '  if [ "$num" = "${FAKE_BOUND_NUM:-}" ]; then\n'
        "    printf '%s' '{\"body\":\"Discuss-thread: 257\",\"comments\":[]}'\n"
        "  else\n"
        "    printf '%s' '{\"body\":\"ordinary ticket\",\"comments\":[]}'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )

    def _authority_cwd(self, profile):
        import tempfile
        d = tempfile.mkdtemp()
        (Path(d) / "CLAUDE.md").write_text(
            "# proj\n<!-- airuleset:authority=%s -->\n" % profile
        )
        return d

    def _run(self, cmd, bound_num):
        import os
        import tempfile
        d = tempfile.mkdtemp()
        gh = Path(d) / "gh"
        gh.write_text(self._FAKE_GH)
        gh.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = d + os.pathsep + env.get("PATH", "")
        env["FAKE_BOUND_NUM"] = bound_num
        payload = json.dumps({"tool_input": {"command": cmd}})
        return subprocess.run(
            ["bash", str(self.HOOK)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=self._authority_cwd("full"),
            env=env,
        )

    def test_a_later_bound_sibling_is_checked_not_just_head_1(self):
        # 4811 (the SECOND close, bound) must be caught even though 4812 (first,
        # unbound) would pass — proves the loop, not head -1.
        r = self._run(
            "gh issue close 4812 -R zbynekdrlik/odoo-erp --comment a "
            "&& gh issue close 4811 -R zbynekdrlik/odoo-erp --comment b",
            bound_num="4811",
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("Discuss-thread", r.stderr)

    def test_the_blocking_number_is_named_in_the_message(self):
        r = self._run(
            "gh issue close 4812 -R zbynekdrlik/odoo-erp --comment a "
            "&& gh issue close 4811 -R zbynekdrlik/odoo-erp --comment b",
            bound_num="4811",
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("gh issue comment 4811", r.stderr)

    def test_compound_all_unbound_passes(self):
        r = self._run(
            "gh issue close 4812 -R zbynekdrlik/odoo-erp --comment a "
            "&& gh issue close 4811 -R zbynekdrlik/odoo-erp --comment b",
            bound_num="0",
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_compound_first_bound_blocks(self):
        r = self._run(
            "gh issue close 4812 -R zbynekdrlik/odoo-erp --comment a "
            "&& gh issue close 4811 -R zbynekdrlik/odoo-erp --comment b",
            bound_num="4812",
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("gh issue comment 4812", r.stderr)


class TestAcceptanceMarkers(TestCase):
    """#891 — channel-agnostic acceptance markers (Acceptance-thread/cited/defer)
    must be recognised alongside the legacy Discuss-* markers."""

    def test_acceptance_thread_binding_blocks_without_disposition(self):
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="Acceptance-thread: task-42"))
        )

    def test_acceptance_thread_plus_acceptance_cited_is_allowed(self):
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Acceptance-thread: task-42",
                    comments=["Acceptance-cited: msg 1731999 task 42"],
                )
            )
        )

    def test_acceptance_thread_plus_acceptance_defer_is_allowed(self):
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Acceptance-thread: task-42",
                    comments=["Acceptance-defer: siblings issue 4812 still open"],
                )
            )
        )

    def test_legacy_discuss_thread_plus_acceptance_cited_is_allowed(self):
        """A legacy Discuss-thread binding is satisfied by the new
        Acceptance-cited disposition — cross-compatibility."""
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Discuss-thread: 257",
                    comments=["Acceptance-cited: msg 1731999"],
                )
            )
        )

    def test_acceptance_thread_plus_legacy_discuss_closed_is_allowed(self):
        """The new binding is satisfied by a legacy disposition."""
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="Acceptance-thread: task-42",
                    comments=["Discuss-closed: msg 1731999"],
                )
            )
        )

    def test_acceptance_markers_are_case_insensitive(self):
        self.assertIsNotNone(
            g.evaluate_close(_issue(body="acceptance-thread: task-42"))
        )
        self.assertIsNone(
            g.evaluate_close(
                _issue(
                    body="acceptance-thread: task-42",
                    comments=["acceptance-cited: msg 9"],
                )
            )
        )

    def test_acceptance_cited_needs_a_value(self):
        self.assertIsNotNone(
            g.evaluate_close(
                _issue(body="Acceptance-thread: task-42\nAcceptance-cited:")
            )
        )

    def test_acceptance_defer_needs_a_value(self):
        self.assertIsNotNone(
            g.evaluate_close(
                _issue(body="Acceptance-thread: task-42\nAcceptance-defer:  ")
            )
        )


class TestMarkerExactSet(TestCase):
    """#891 recidivism guard — lock the exact set of markers the guard
    recognises so a future channel-named marker (Task-closed:, Whatsapp-closed:)
    forces a deliberate, reviewed edit."""

    def test_binding_marker_names(self):
        """The guard recognises exactly these binding markers (line-anchored)
        plus the discuss.channel deep-URL auto-bind."""
        # Recognised bindings
        for marker in ("Discuss-thread:", "Acceptance-thread:"):
            with self.subTest(marker=marker):
                self.assertIsNotNone(
                    g.evaluate_close(_issue(body=f"{marker} 42")),
                    f"{marker} must be recognised as a binding",
                )
        # NOT recognised (channel-specific — rejected by design)
        for marker in ("Task-thread:", "Whatsapp-thread:", "Email-thread:"):
            with self.subTest(marker=marker):
                self.assertIsNone(
                    g.evaluate_close(_issue(body=f"{marker} 42")),
                    f"{marker} must NOT be a binding (channel-specific)",
                )

    def test_disposition_marker_names(self):
        """The guard recognises exactly these disposition markers."""
        bound = "Discuss-thread: 257"
        for marker in (
            "Discuss-closed:",
            "Discuss-defer:",
            "Acceptance-cited:",
            "Acceptance-defer:",
        ):
            with self.subTest(marker=marker):
                self.assertIsNone(
                    g.evaluate_close(
                        _issue(body=bound, comments=[f"{marker} evidence"])
                    ),
                    f"{marker} must be recognised as a disposition",
                )
        # NOT recognised
        for marker in ("Task-closed:", "Whatsapp-closed:"):
            with self.subTest(marker=marker):
                self.assertIsNotNone(
                    g.evaluate_close(
                        _issue(body=bound, comments=[f"{marker} evidence"])
                    ),
                    f"{marker} must NOT be a disposition (channel-specific)",
                )


if __name__ == "__main__":
    main()
