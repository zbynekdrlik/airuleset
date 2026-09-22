"""Spec anchoring (#1106, owner directive 21.9.2026).

On david1–4 the developer writes a detailed initial plan/spec, tickets are cut
from it, and over weeks the stream drifts — re-asks questions the spec settled,
implements behaviour the plan excluded — because no gate ever reads the spec
again. This locks the durable link ticket → spec section enforced at four
existing gates (filing, design, review, question) plus a per-cycle
reconciliation line, so the spec ticket stays the truth and a deviation has
exactly one path (owner decision → spec change).

Every classifier here is a SHAPE check (bilingual token families), the same
contract as gates/design/classifiers.py — never a proof of correctness.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gates.spec as spec  # noqa: E402


# --------------------------------------------------------------------------- #
# Parsing — the `Spec:` line on a ticket body
# --------------------------------------------------------------------------- #
class TestSpecLineParsing(unittest.TestCase):
    def test_single_ref(self):
        kind, val = spec.parse_spec_line("blah\nSpec: #123 §2\nmore")
        self.assertEqual(kind, "ref")
        self.assertEqual(val, [(123, "§2")])

    def test_multi_ref(self):
        kind, val = spec.parse_spec_line("Spec: #123 §2, #124 §3")
        self.assertEqual(kind, "ref")
        self.assertEqual(val, [(123, "§2"), (124, "§3")])

    def test_section_ascii_form(self):
        # `Spec: #7 s2` / `Spec: #7 section 2` also parse (ASCII fallback for §).
        kind, val = spec.parse_spec_line("Spec: #7 s2")
        self.assertEqual(kind, "ref")
        self.assertEqual(val[0][0], 7)

    def test_none_with_reason(self):
        kind, val = spec.parse_spec_line("Spec: none — foundational, no initiative")
        self.assertEqual(kind, "none")
        self.assertIn("foundational", val)

    def test_no_spec_line(self):
        self.assertIsNone(spec.parse_spec_line("just a ticket body, no spec"))

    def test_ticket_has_spec(self):
        self.assertTrue(spec.ticket_has_spec("Spec: #5 §1"))
        self.assertFalse(spec.ticket_has_spec("Spec: none — x"))
        self.assertFalse(spec.ticket_has_spec("no spec line here"))

    def test_spec_refs(self):
        self.assertEqual(spec.spec_refs("Spec: #12 §4, #13 §5"),
                         [(12, "§4"), (13, "§5")])
        self.assertEqual(spec.spec_refs("no spec"), [])


# --------------------------------------------------------------------------- #
# Stream fact reader — `specs: #N, #M` in .claude/streams/<stream>.md
# --------------------------------------------------------------------------- #
class TestSpecsForStream(unittest.TestCase):
    def test_reads_specs_fact(self):
        txt = "navody_url: https://x\nspecs: #501, #502\n"
        got = spec.specs_for_stream("/repo", "david3",
                                    read_text=lambda p: txt)
        self.assertEqual(got, [501, 502])

    def test_no_specs_fact(self):
        got = spec.specs_for_stream("/repo", "david3",
                                    read_text=lambda p: "navody_url: https://x\n")
        self.assertEqual(got, [])

    def test_unreadable_stream_file(self):
        got = spec.specs_for_stream("/repo", "david3",
                                    read_text=lambda p: None)
        self.assertEqual(got, [])

    def test_no_stream(self):
        self.assertEqual(spec.specs_for_stream("/repo", None), [])


# --------------------------------------------------------------------------- #
# (a) FILING GATE — a stream ticket while the stream has an open spec must
# carry a `Spec:` line.
# --------------------------------------------------------------------------- #
class TestFilingSpecGate(unittest.TestCase):
    STREAM_FILE = "navody_url: https://x\nspecs: #501\n"

    def _block(self, body):
        return spec.filing_spec_block_reason(
            ["stream:david3"], body, "/repo",
            read_text=lambda p: self.STREAM_FILE)

    def test_missing_spec_blocks_and_names_the_open_spec(self):
        r = self._block("A stream ticket body with no Spec line.")
        self.assertIsNotNone(r)
        self.assertIn("#501", r)

    def test_spec_ref_passes(self):
        self.assertIsNone(self._block("Body\nSpec: #501 §2\n"))

    def test_spec_none_passes(self):
        self.assertIsNone(self._block("Body\nSpec: none — infra, no initiative\n"))

    def test_no_specs_fact_unaffected(self):
        r = spec.filing_spec_block_reason(
            ["stream:david3"], "no spec line", "/repo",
            read_text=lambda p: "navody_url: https://x\n")
        self.assertIsNone(r)

    def test_no_stream_label_unaffected(self):
        r = spec.filing_spec_block_reason(
            [], "no spec line", "/repo",
            read_text=lambda p: self.STREAM_FILE)
        self.assertIsNone(r)


# --------------------------------------------------------------------------- #
# (b) DESIGN GATE — a `Spec:` ticket's design must cite the spec.
# --------------------------------------------------------------------------- #
class TestDesignSpecGate(unittest.TestCase):
    TICKET = "Owner ask ...\nSpec: #501 §2\nmore"
    NO_SPEC_TICKET = "Owner ask, foundational.\nSpec: none — x"

    def test_no_spec_on_ticket_is_unchanged(self):
        ok, _ = spec.classify_spec_design("any design body", self.NO_SPEC_TICKET)
        self.assertTrue(ok)
        ok2, _ = spec.classify_spec_design("any design body", "no spec at all")
        self.assertTrue(ok2)

    def test_missing_spec_ref_blocks(self):
        ok, reason = spec.classify_spec_design(
            "Root cause ... chosen approach ...", self.TICKET)
        self.assertFalse(ok)
        self.assertIn("Spec-ref", reason)

    def test_ref_plus_conform_passes(self):
        ok, _ = spec.classify_spec_design(
            "Spec-ref: #501 §2\nSpec-conform: yes\ndesign detail", self.TICKET)
        self.assertTrue(ok)

    def test_ref_without_conform_or_deviation_blocks(self):
        ok, reason = spec.classify_spec_design(
            "Spec-ref: #501 §2\ndesign detail no verdict", self.TICKET)
        self.assertFalse(ok)
        self.assertIn("conform", reason.lower())

    def test_deviation_without_needs_decision_blocks(self):
        ok, reason = spec.classify_spec_design(
            "Spec-ref: #501 §2\nSpec-deviation: the plan said A but B is needed",
            self.TICKET)
        self.assertFalse(ok)
        self.assertIn("needs-decision", reason)

    def test_deviation_with_needs_decision_passes(self):
        ok, _ = spec.classify_spec_design(
            "Spec-ref: #501 §2\nSpec-deviation: plan said A, B needed\n"
            "This is a needs-decision question to the owner.", self.TICKET)
        self.assertTrue(ok)


# --------------------------------------------------------------------------- #
# design-record validator learns the same tokens (cli_design_record).
# --------------------------------------------------------------------------- #
class TestDesignRecordValidatorSpec(unittest.TestCase):
    # A fully gate-valid design body (root cause + approach + rejected alt +
    # triage + architektúra + shared-benefit), reused for both cases.
    GOOD_DESIGN = (
        "## Root cause\nThe underlying cause is that the code caches X because "
        "the loop never invalidates it.\n\n"
        "## Approaches\nApproach 1 (chosen) — I will implement a TTL. "
        "Approach 2 — rejected alternative: full rewrite, instead of a "
        "targeted fix. Trade-off comparison: 1 is cheaper.\n\n"
        "Triage: trivial\n"
        "Architektúra: one helper in cache.py + the stdlib ttl framework.\n"
        "Shared-benefit: every stream hitting the same cache path.\n")

    def test_spec_ticket_requires_spec_ref(self):
        import cli_design_record as cdr
        ok, reasons = cdr.validate_body(self.GOOD_DESIGN,
                                        ticket_body="Spec: #501 §2")
        self.assertFalse(ok)
        self.assertTrue(any("spec" in r.lower() for r in reasons))

    def test_spec_ticket_with_ref_passes(self):
        import cli_design_record as cdr
        body = self.GOOD_DESIGN + "\nSpec-ref: #501 §2\nSpec-conform: yes\n"
        ok, reasons = cdr.validate_body(body, ticket_body="Spec: #501 §2")
        self.assertTrue(ok, reasons)

    def test_no_ticket_body_is_unchanged(self):
        import cli_design_record as cdr
        ok, reasons = cdr.validate_body(self.GOOD_DESIGN)
        self.assertTrue(ok, reasons)


# --------------------------------------------------------------------------- #
# (c) REVIEW GATE — a `Spec:` ticket's review/RFR must carry `Spec-check:`.
# --------------------------------------------------------------------------- #
class TestReviewSpecGate(unittest.TestCase):
    def test_classify_spec_check_shape(self):
        ok, _ = spec.classify_spec_check("Spec-check: §2 — conform")
        self.assertTrue(ok)
        ok2, _ = spec.classify_spec_check(
            "Spec-check: §2 — deviation #123")
        self.assertTrue(ok2)
        ok3, reason = spec.classify_spec_check("no spec check here")
        self.assertFalse(ok3)

    # A review body long enough to clear the pre-existing MIN_LEN_REVIEW=60
    # floor (the spec check is what this class exercises, not the length gate).
    REVIEW = ("Ran /review and requesting-code-review on this branch, "
              "result clean, 0 🔴 0 🟡 0 🔵, fixed in commit deadbeef1")

    def test_review_comment_on_spec_ticket_requires_spec_check(self):
        from gates import design as dg
        # No ticket body -> unchanged (backward compatible).
        ok, _ = dg.classify_review_comment(self.REVIEW)
        self.assertTrue(ok)
        # Spec ticket, no Spec-check -> block.
        ok2, reason = dg.classify_review_comment(
            self.REVIEW, ticket_body="Spec: #501 §2")
        self.assertFalse(ok2)
        self.assertIn("Spec-check", reason)
        # Spec ticket, with Spec-check -> pass.
        ok3, _ = dg.classify_review_comment(
            self.REVIEW + "\nSpec-check: §2 — conform",
            ticket_body="Spec: #501 §2")
        self.assertTrue(ok3)

    def test_review_comment_no_spec_ticket_is_unchanged(self):
        from gates import design as dg
        ok, _ = dg.classify_review_comment(self.REVIEW, ticket_body="no spec here")
        self.assertTrue(ok)


class TestHandoffSpecPreflight(unittest.TestCase):
    def _fake_read_issue(self, body):
        def _r(number, slug, **kw):
            return {"body": body}, None
        return _r

    def test_preflight_blocks_rfr_without_spec_check_on_spec_ticket(self):
        import airuleset
        blk = airuleset._handoff_spec_preflight(
            "READY-FOR-REVIEW: branch x\nself-review table...",
            issue=42, repo="o/r", cwd="/repo",
            read_issue=self._fake_read_issue("Spec: #501 §2"))
        self.assertIsNotNone(blk)
        self.assertIn("Spec-check", blk)

    def test_preflight_passes_rfr_with_spec_check(self):
        import airuleset
        blk = airuleset._handoff_spec_preflight(
            "READY-FOR-REVIEW: branch x\nSpec-check: §2 — conform",
            issue=42, repo="o/r", cwd="/repo",
            read_issue=self._fake_read_issue("Spec: #501 §2"))
        self.assertIsNone(blk)

    def test_preflight_no_spec_ticket_passes(self):
        import airuleset
        blk = airuleset._handoff_spec_preflight(
            "READY-FOR-REVIEW: branch x",
            issue=42, repo="o/r", cwd="/repo",
            read_issue=self._fake_read_issue("no spec line"))
        self.assertIsNone(blk)

    def test_preflight_fail_open_on_read_error(self):
        import airuleset

        def _err(number, slug, **kw):
            return None, "gate-unavailable: quota"
        blk = airuleset._handoff_spec_preflight(
            "READY-FOR-REVIEW: branch x", issue=42, repo="o/r", cwd="/repo",
            read_issue=_err)
        self.assertIsNone(blk)


# --------------------------------------------------------------------------- #
# (d) QUESTION GATE — Check 10: a settled question is not re-asked.
# --------------------------------------------------------------------------- #
class TestSettledQuestionsCache(unittest.TestCase):
    SPEC_BODY = (
        "## §1 Money access\nMoney is reached via the prod proxy.\n\n"
        "## Settled questions\n"
        "- Q: How do we reach Money from the shadow box? "
        "→ A: Through the prod proxy on port 9000. (12.9.2026)\n"
        "- Q: Which currency does the invoice render in? "
        "→ A: Always the customer's billing currency. (13.9.2026)\n\n"
        "## §2 Something else\nbody\n")

    def test_parse_settled_questions(self):
        entries = spec.parse_settled_questions(self.SPEC_BODY, spec_number=501)
        self.assertEqual(len(entries), 2)
        self.assertIn("Money", entries[0]["q"])
        self.assertIn("prod proxy", entries[0]["a"])
        self.assertEqual(entries[0]["spec"], 501)

    def test_build_settled_cache(self):
        cache = spec.build_settled_cache(
            [{"number": 501, "body": self.SPEC_BODY}])
        self.assertIn(501, cache["specs"])
        self.assertEqual(len(cache["entries"]), 2)

    def test_content_tokens_drops_stopwords_and_casefolds(self):
        toks = spec.content_tokens("How do we reach the Money from a box?")
        self.assertIn("money", toks)
        self.assertIn("reach", toks)
        self.assertNotIn("do", toks)   # stop-word
        self.assertNotIn("the", toks)  # stop-word

    def test_settled_conflict_matches_reask(self):
        entries = spec.parse_settled_questions(self.SPEC_BODY, spec_number=501)
        # A near-verbatim re-ask of the first settled question.
        hit = spec.settled_conflict(
            "How do we reach Money from the shadow box again?", entries)
        self.assertIsNotNone(hit)
        self.assertIn("prod proxy", hit["a"])

    def test_settled_conflict_new_question_passes(self):
        entries = spec.parse_settled_questions(self.SPEC_BODY, spec_number=501)
        hit = spec.settled_conflict(
            "Should we upgrade the postgres cluster to version 17?", entries)
        self.assertIsNone(hit)

    def test_check_question_against_cache_blocks(self):
        with tempfile.TemporaryDirectory() as home:
            cache = spec.build_settled_cache(
                [{"number": 501, "body": self.SPEC_BODY}])
            os.makedirs(os.path.join(home, ".claude", "spec-settled"))
            with open(spec.settled_cache_path("odoo-erp", home=home), "w") as fh:
                json.dump(cache, fh)
            block, reason = spec.check_question_against_cache(
                "How do we reach Money from the shadow box?",
                "odoo-erp", home=home)
            self.assertTrue(block)
            self.assertIn("prod proxy", reason)
            self.assertIn("#501", reason)

    def test_check_question_against_cache_new_question_passes(self):
        with tempfile.TemporaryDirectory() as home:
            cache = spec.build_settled_cache(
                [{"number": 501, "body": self.SPEC_BODY}])
            os.makedirs(os.path.join(home, ".claude", "spec-settled"))
            with open(spec.settled_cache_path("odoo-erp", home=home), "w") as fh:
                json.dump(cache, fh)
            block, _ = spec.check_question_against_cache(
                "Should we upgrade postgres to version 17?",
                "odoo-erp", home=home)
            self.assertFalse(block)

    def test_check_question_against_cache_absent_passes(self):
        with tempfile.TemporaryDirectory() as home:
            block, _ = spec.check_question_against_cache(
                "anything", "odoo-erp", home=home)
            self.assertFalse(block)


class TestCheck10Runner(unittest.TestCase):
    """gates/spec_question.py — the Stop-hook Check 10 runner (fail-open)."""
    SPEC_BODY = TestSettledQuestionsCache.SPEC_BODY

    def _run(self, block_text, home, cwd):
        env = dict(os.environ)
        env["HOME"] = home
        env["PYTHONPATH"] = str(ROOT)
        # Force the slug resolution to a known value.
        env["AIRULESET_SPEC_QUESTION_SLUG"] = "odoo-erp"
        return subprocess.run(
            [sys.executable, "-P", "-m", "gates.spec_question", "--cwd", cwd],
            input=block_text, capture_output=True, text=True, env=env,
            cwd=str(ROOT))

    def test_runner_blocks_settled_reask(self):
        with tempfile.TemporaryDirectory() as home:
            cache = spec.build_settled_cache(
                [{"number": 501, "body": self.SPEC_BODY}])
            os.makedirs(os.path.join(home, ".claude", "spec-settled"))
            with open(spec.settled_cache_path("odoo-erp", home=home), "w") as fh:
                json.dump(cache, fh)
            r = self._run("How do we reach Money from the shadow box?", home,
                          "/repo")
            self.assertEqual(r.returncode, 2, r.stderr + r.stdout)
            self.assertIn("prod proxy", r.stdout + r.stderr)

    def test_runner_cache_absent_passes(self):
        with tempfile.TemporaryDirectory() as home:
            r = self._run("anything at all", home, "/repo")
            self.assertEqual(r.returncode, 0, r.stderr)


# --------------------------------------------------------------------------- #
# (e) CLI — `airuleset.py spec-change` posts the comment + edits the section.
# --------------------------------------------------------------------------- #
class TestSpecChangeCLI(unittest.TestCase):
    SPEC_BODY = (
        "## §1 First\noriginal first text\n\n"
        "## §2 Second\noriginal second text\n\n"
        "## §3 Third\noriginal third text\n")

    def test_edit_section_replaces_only_that_section(self):
        import cli_spec_change as csc
        out = csc.edit_section(self.SPEC_BODY, "2", "REPLACED second body")
        self.assertIn("REPLACED second body", out)
        self.assertNotIn("original second text", out)
        # Siblings untouched.
        self.assertIn("original first text", out)
        self.assertIn("original third text", out)
        # The header line is preserved.
        self.assertIn("## §2 Second", out)

    def test_edit_section_missing_section_raises(self):
        import cli_spec_change as csc
        with self.assertRaises(KeyError):
            csc.edit_section(self.SPEC_BODY, "9", "x")

    def test_cmd_spec_change_posts_comment_and_edits_body(self):
        import cli_spec_change as csc
        calls = []

        def runner(argv, body=None):
            calls.append((argv, body))
            # `gh issue view ... --json body -q .body` returns the spec body.
            if "view" in argv:
                return 0, self.SPEC_BODY, ""
            return 0, "https://github.com/o/r/issues/501#c1", ""

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write("REPLACED §2 via CLI")
            body_file = fh.name
        try:
            rc = csc.cmd_spec_change(_Args(
                spec=501, section="2", repo="o/r", body_file=body_file),
                runner=runner)
        finally:
            os.unlink(body_file)
        self.assertEqual(rc, 0)
        joined = " ".join(" ".join(a) for a, _ in calls)
        self.assertIn("comment", joined)   # posted a Spec-change: comment
        self.assertIn("edit", joined)      # edited the spec body
        # A Spec-change: comment body was posted.
        self.assertTrue(any(b and "Spec-change" in b for _, b in calls))


# --------------------------------------------------------------------------- #
# (f) RECONCILIATION — the Step-5 line renderer + partition-audit clause.
# --------------------------------------------------------------------------- #
class TestReconciliation(unittest.TestCase):
    def test_spec_status_line(self):
        line = spec.spec_status_line(
            501, done=["§1", "§2"], pending=["§3"], deviations=["§4 (#123)"])
        self.assertTrue(line.startswith("Spec-status #501:"))
        self.assertIn("done §1 §2", line)
        self.assertIn("pending §3", line)
        self.assertIn("§4 (#123)", line)

    def test_spec_status_line_no_deviations(self):
        line = spec.spec_status_line(7, done=["§1"], pending=[], deviations=[])
        self.assertIn("Spec-status #7:", line)
        self.assertIn("deviations", line)  # explicit "deviations none"/"—"

    def test_partition_audit_clause_present_when_missing(self):
        clause = spec.spec_partition_audit_clause([501], 3)
        self.assertIsNotNone(clause)
        self.assertIn("#501", clause)
        self.assertIn("3", clause)

    def test_partition_audit_clause_none_when_nothing_missing(self):
        self.assertIsNone(spec.spec_partition_audit_clause([501], 0))
        self.assertIsNone(spec.spec_partition_audit_clause([], 3))

    def test_partition_audit_clause_under_max(self):
        clause = spec.spec_partition_audit_clause([501, 502, 503], 42,
                                                  max_chars=120)
        self.assertLessEqual(len(clause), 120)


class TestNudgeClauseWiring(unittest.TestCase):
    """The partition-audit clause is an OPTIONAL last item in the ops-wait
    nudge, under NUDGE_MAX_CHARS."""
    def test_nudge_text_appends_spec_clause(self):
        from watchdog import ops_wait_recheck as owr
        text = owr._nudge_text(2, [], spec_missing_n=3, spec_open=[501])
        self.assertIn("501", text)
        self.assertLessEqual(len(text), owr.NUDGE_MAX_CHARS)

    def test_nudge_text_no_spec_clause_when_zero(self):
        from watchdog import ops_wait_recheck as owr
        base = owr._nudge_text(2, [])
        with_zero = owr._nudge_text(2, [], spec_missing_n=0, spec_open=[])
        self.assertEqual(base, with_zero)


class TestStep5DoctrineLock(unittest.TestCase):
    """The Step-5 Spec-status doctrine is present in the autopilot SKILL, WITHIN
    the 3600-char window the #58 lock reads (never shifting its anchors)."""
    def test_spec_status_doctrine_in_step5_window(self):
        t = (ROOT / "skills/autopilot/SKILL.md").read_text(encoding="utf-8")
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        step5 = t[idx:idx + 3600]
        self.assertIn("Spec-status", step5)

    def test_58_lock_anchors_still_within_window(self):
        # The existing #58 lock tokens must all still fall inside 3600 chars.
        t = (ROOT / "skills/autopilot/SKILL.md").read_text(encoding="utf-8")
        idx = t.index("5. **Report each COMPLETED INTEGRATION CYCLE")
        step5 = t[idx:idx + 3600]
        for tok in ("branch-merge", "fork-no-merge", "completion-report.md",
                    "READY-FOR-REVIEW", "Lokálne overenie", "2129"):
            self.assertIn(tok, step5)


class TestDoctrinePresence(unittest.TestCase):
    def test_ticket_validator_mentions_spec(self):
        t = (ROOT / "agents/ticket-validator.md").read_text(encoding="utf-8")
        # a strong assertion, not the weak substring "Spec" (R1#9).
        self.assertIn("Spec anchoring", t)
        self.assertIn("spec section", t)

    def test_process_subdev_mentions_spec_check(self):
        t = (ROOT / "skills/process-subdev/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Spec-check", t)


# --------------------------------------------------------------------------- #
# Adversarial-review regression tests (#1106 review, both fresh reviewers).
# --------------------------------------------------------------------------- #
class TestReviewRegressions(unittest.TestCase):
    # R1#1/R2#5 — edit_section must not corrupt a section containing a code
    # fence (whose `#`-comment lines are NOT headers) or a sub-header.
    def test_edit_section_preserves_code_fence_comment(self):
        import cli_spec_change as csc
        body = ("## §1 First\norig one\n\n"
                "## §2 Setup\n```bash\n# install deps\napt-get install foo\n```\n"
                "old prose\n\n"
                "## §3 Third\norig three\n")
        out = csc.edit_section(body, "2", "REPLACED two")
        self.assertIn("REPLACED two", out)
        self.assertNotIn("# install deps", out)   # old §2 body fully gone
        self.assertNotIn("old prose", out)
        self.assertIn("orig one", out)            # siblings intact
        self.assertIn("orig three", out)
        self.assertIn("## §3 Third", out)

    def test_edit_section_keeps_subheader_inside_section(self):
        import cli_spec_change as csc
        body = ("## §1 First\norig one\n\n"
                "## §2 Second\nintro\n### Sub A\ndetail\n\n"
                "## §3 Third\norig three\n")
        out = csc.edit_section(body, "1", "NEW one")
        # editing §1 must stop at §2, never swallow it or its sub-header.
        self.assertIn("NEW one", out)
        self.assertIn("## §2 Second", out)
        self.assertIn("### Sub A", out)
        self.assertIn("orig three", out)

    def test_edit_section_prefers_section_marker_header(self):
        import cli_spec_change as csc
        body = ("## 2 things to note\nnot the section\n\n"
                "## §2 Real Section\nreal body\n\n## §3 T\nt\n")
        out = csc.edit_section(body, "2", "REPLACED real")
        self.assertIn("REPLACED real", out)
        self.assertNotIn("real body", out)
        self.assertIn("not the section", out)     # the `## 2 things` shadow header untouched

    # R1#2 — parse_settled_questions must survive a code fence + a decorated header.
    def test_settled_questions_survive_code_fence(self):
        import gates.spec as spec
        body = ("## §1 X\n## Settled questions\n"
                "- Q: How do we reach Money from the shadow box? "
                "→ A: Via the prod proxy. (1.1.2026)\n"
                "```\n# not a header\n```\n"
                "- Q: Which currency does the invoice use? "
                "→ A: The customer billing currency. (2.1.2026)\n\n## §2 Y\n")
        entries = spec.parse_settled_questions(body, spec_number=5)
        self.assertEqual(len(entries), 2)

    def test_settled_questions_decorated_header(self):
        import gates.spec as spec
        body = ("## Settled questions (round 2)\n"
                "- Q: How do we reach Money from the shadow box today? "
                "→ A: Via the prod proxy on 9000. (1.1.2026)\n")
        entries = spec.parse_settled_questions(body, spec_number=7)
        self.assertEqual(len(entries), 1)

    # R1#3/R2#4 — a short (3-token) settled question must NOT block a new question.
    def test_short_settled_question_no_false_block(self):
        import gates.spec as spec
        entries = [{"q": "Which currency to render?", "a": "billing", "spec": 9}]
        hit = spec.settled_conflict(
            "How do we fix the invoice currency rounding bug?", entries)
        self.assertIsNone(hit)

    # R2#6 — a bulleted `- Spec: #N §x` must parse (filing gate is a hard block).
    def test_bulleted_spec_line_parses(self):
        import gates.spec as spec
        self.assertTrue(spec.ticket_has_spec("body\n- Spec: #501 §2\nmore"))
        self.assertTrue(spec.ticket_has_spec("* Spec: #501 §2"))

    # R1#6 — a `Spec:` line inside a code fence is NOT a real anchor.
    def test_spec_line_inside_code_fence_ignored(self):
        import gates.spec as spec
        body = "intro\n```\nSpec: #999 §9\n```\nno real spec here"
        self.assertFalse(spec.ticket_has_spec(body))
        self.assertIsNone(spec.parse_spec_line(body))

    # R1#7 — a deviation must demand a needs-decision even alongside a
    # contradictory `Spec-conform: yes`.
    def test_deviation_with_conform_still_needs_decision(self):
        import gates.spec as spec
        ok, reason = spec.classify_spec_design(
            "Spec-ref: #501 §2\nSpec-conform: yes\nSpec-deviation: but B differs",
            "Spec: #501 §2")
        self.assertFalse(ok)
        self.assertIn("needs-decision", reason)

    # R2#3 — spec_missing is scoped per-stream (a spec-less stream is not nagged).
    def test_spec_missing_scoped_per_stream(self):
        import airuleset

        def out(argv, cd):
            path = argv[2] if len(argv) > 2 else ""
            if "labels=spec" in path:
                # one open spec ticket owned by stream:alpha
                return json.dumps([{"number": 501, "body": "## §1 X",
                                    "labels": [{"name": "spec"},
                                               {"name": "stream:alpha"}]}])
            # the full open-issue list: one alpha ticket (no Spec:), one beta
            return json.dumps([
                {"number": 10, "body": "no spec",
                 "labels": [{"name": "stream:alpha"}]},
                {"number": 11, "body": "no spec",
                 "labels": [{"name": "stream:beta"}]}])
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            spec_open, spec_missing, ts = airuleset._refresh_spec_settled_cache(
                "o/odoo-erp", "odoo-erp", "/repo", out)
            self.assertEqual(spec_open, [501])
            # only the alpha ticket (whose stream has a spec) is counted, not beta
            self.assertEqual(spec_missing, 1)
            self.assertIsNotNone(ts)


# --------------------------------------------------------------------------- #
# #1106 area-review fix: REST (not GraphQL) + a 15-min TTL on the hot path.
# --------------------------------------------------------------------------- #
class TestSpecCacheRestAndTTL(unittest.TestCase):
    def test_fresh_ttl_makes_zero_gh_calls_and_returns_stored(self):
        import airuleset
        calls = []

        def counting_out(argv, cd):
            calls.append(argv)
            return "[]"
        prev = {"spec_checked_ts": 1000, "spec_open": [501], "spec_missing": 3}
        so, sm, ts = airuleset._refresh_spec_settled_cache(
            "o/r", "r", "/repo", counting_out, prev_entry=prev, now=1100)
        self.assertEqual(calls, [])            # ZERO gh calls within the TTL
        self.assertEqual(so, [501])
        self.assertEqual(sm, 3)
        self.assertEqual(ts, 1000)             # unchanged check time

    def test_stale_ttl_reads_via_rest_never_issue_list(self):
        import airuleset
        argvs = []

        def out(argv, cd):
            argvs.append(argv)
            path = argv[2] if len(argv) > 2 else ""
            if "labels=spec" in path:
                return json.dumps([{"number": 501, "body": "## §1 X",
                                    "labels": [{"name": "spec"},
                                               {"name": "stream:alpha"}]}])
            return json.dumps([{"number": 10, "body": "no spec",
                                "labels": [{"name": "stream:alpha"}]}])
        prev = {"spec_checked_ts": 1, "spec_open": [], "spec_missing": 0}
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            so, sm, ts = airuleset._refresh_spec_settled_cache(
                "o/r", "r", "/repo", out, prev_entry=prev, now=100000)
        self.assertEqual(so, [501])
        self.assertEqual(sm, 1)
        self.assertEqual(ts, 100000)           # a real re-read stamps now
        # REST shape only — never `gh issue list` (the GraphQL hot-path spend).
        for argv in argvs:
            self.assertEqual(argv[0], "gh")
            self.assertEqual(argv[1], "api")
            self.assertIn("repos/", argv[2])
            self.assertNotIn("list", argv)

    def test_pr_row_excluded_from_spec_missing(self):
        import airuleset

        def out(argv, cd):
            path = argv[2] if len(argv) > 2 else ""
            if "labels=spec" in path:
                return json.dumps([{"number": 501, "body": "## §1 X",
                                    "labels": [{"name": "spec"},
                                               {"name": "stream:alpha"}]}])
            # a PR row (pull_request key) + a real issue, both stream:alpha,
            # both without a Spec: line — only the ISSUE must be counted.
            return json.dumps([
                {"number": 20, "body": "no spec",
                 "labels": [{"name": "stream:alpha"}],
                 "pull_request": {"url": "https://x/pull/20"}},
                {"number": 21, "body": "no spec",
                 "labels": [{"name": "stream:alpha"}]}])
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            so, sm, ts = airuleset._refresh_spec_settled_cache(
                "o/r", "r", "/repo", out, now=100000)
        self.assertEqual(so, [501])
        self.assertEqual(sm, 1)                # the PR (#20) is NOT counted

    def test_no_spec_repo_removes_cache_and_holds_ttl(self):
        import airuleset
        import gates.spec as spec

        def out(argv, cd):
            return "[]"                         # no spec tickets
        with tempfile.TemporaryDirectory() as home:
            os.environ["HOME"] = home
            # a stale pre-existing cache must be removed (Check 10 fails open,
            # question-gate empty-dir short-circuit preserved).
            os.makedirs(os.path.join(home, ".claude", "spec-settled"))
            p = spec.settled_cache_path("r", home=home)
            with open(p, "w") as fh:
                fh.write("{}")
            so, sm, ts = airuleset._refresh_spec_settled_cache(
                "o/r", "r", "/repo", out, now=100000)
            self.assertEqual((so, sm, ts), ([], 0, 100000))
            self.assertFalse(os.path.exists(p))


# --------------------------------------------------------------------------- #
# #1106 live-read (montalu1 v0.1.378): Check 10 missed a Slovak re-ask that
# differs only by DIACRITICS — content_tokens casefolded but did not fold
# diacritics, so `tlač`≠`tlac`, `má`≠`ma` dropped shared tokens under 0.6.
# --------------------------------------------------------------------------- #
class TestDiacriticsFolding(unittest.TestCase):
    # The live cache entry (ASCII, as the stream typed the spec) vs a diacritic
    # re-ask (as the owner typed the question).
    ENTRY = {"q": "ma byt tlac cenoviek cez PDF alebo cez tlaciaren priamo",
             "a": "cez PDF", "spec": 501}

    def test_diacritics_reask_conflicts(self):
        import gates.spec as spec
        hit = spec.settled_conflict(
            "má byť tlač cenoviek cez PDF alebo cez tlačiareň priamo?",
            [self.ENTRY])
        self.assertIsNotNone(hit)          # was None (0.56 < 0.6) before the fix
        self.assertEqual(hit["a"], "cez PDF")

    def test_unrelated_diacritics_question_does_not_conflict(self):
        import gates.spec as spec
        hit = spec.settled_conflict(
            "Ktorý sklad je predvolený pre pekáreň?", [self.ENTRY])
        self.assertIsNone(hit)

    def test_content_tokens_folds_diacritics(self):
        import gates.spec as spec
        self.assertEqual(spec.content_tokens("tlač cenoviek"),
                         spec.content_tokens("tlac cenoviek"))
        # a fuller round-trip: the diacritic re-ask and the ASCII entry produce
        # the SAME token set.
        self.assertEqual(
            spec.content_tokens(
                "má byť tlač cenoviek cez PDF alebo cez tlačiareň priamo?"),
            spec.content_tokens(self.ENTRY["q"]))


class _Args:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


if __name__ == "__main__":
    unittest.main()
