"""#1125: the hand-off composer matches the odoo-erp gate's line shapes.

Four aligned mismatches between ``cli_handoff_template`` (+ the ``--sign-only``
path of ``cmd_handoff``) and odoo-erp's ``scripts/handoff_gate``:

1. heading prefix — the label regexes share ONE line prefix that also tolerates
   a ``#{1,6}`` markdown heading (odoo-erp PR 8095's exact
   ``(?:#{1,6}[ \\t]+)?`` shape), so ``### Self-review-model:`` passes the
   ``--body-file`` / ``--sign-only`` fail-fast while look-alikes stay rejected;
2. Prevencia label — both compose paths emit the gate's canonical
   ``Prevencia (stream):`` (the develop gate's round-3 check accepts ONLY that
   label); the checks keep accepting both labels;
3. ``Gate-dry-run:`` — both compose paths emit it, carrying the stream-supplied
   ``PASS @ <sha>`` (validated against the gate's own ``dry_run.py`` regex) or
   an explicit ``not run``; a PASS is never fabricated;
4. fixture freshness — the vendored gate fixture's provenance blob equals the
   extractor's pinned ``PINNED_GATE_BLOB``, and the extractor's offline
   ``--check`` mode reports drift of a gate source against that pin.

Offline by construction: no test fetches GitHub.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import sys
import tempfile
import unittest
import unittest.mock as m

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tests", "fixtures"))

import airuleset  # noqa: E402
import cli_handoff_template as ht  # noqa: E402
import handoff_gate_pure as gate  # noqa: E402  (vendored odoo-erp validators)

_R, _Y, _B = "\U0001f534", "\U0001f7e1", "\U0001f535"

# The gate's own Gate-dry-run value check (odoo-erp
# scripts/handoff_gate/dry_run.py::check_gate_dry_run_field), verbatim.
_GATE_DRY_RUN_VALUE_RE = re.compile(r"(?i)PASS\s+@\s+[0-9a-f]{7,40}")


def _row(lens, ev):
    return "| %s | 0 %s 0 %s 0 %s | %s |" % (lens, _R, _Y, _B, ev)


_TABLE = (
    "| lens | verdict | evidence |\n|---|---|---|\n"
    + "\n".join(_row(lens, "cli_handoff_template.py:%d" % (i + 30))
                for i, lens in enumerate(
                    ("security", "correctness", "test-integrity",
                     "evidence-integrity", "design-doctrine", "process")))
    + "\n"
)

_BODY_R1 = (
    "READY-FOR-REVIEW: branch montalu/9999-thing\n"
    "\n"
    "Self-review-model: claude-opus-4-8\n"
    "**Self-review:**\n"
    "\n" + _TABLE + "\n"
    "Branch: montalu/9999-thing\n"
    "HEAD: 65ea5ae2b858\n"
    "Stack: #9999\n"
    "Verified-at-UTC: 2026-09-16T10:00:00Z\n"
    "Harness: run-shadow-unit-tests.sh mymod = success\n"
)
_BODY_R3 = (
    _BODY_R1
    + "Root-cause-of-previous-bounce: the `process` lens missed the bump\n"
    "Prevencia (stream): re-check version strictly-greater before hand-off\n"
)


def _compose(extended=True, **over):
    kw = dict(
        repo="zbynekdrlik/odoo-erp", branch="montalu/9999-thing",
        head_sha="65ea5ae2b858", verified_at_utc="2026-09-16T10:00:00Z",
        self_review_table=_TABLE, bounce_round=1,
        self_review_model="claude-opus-4-8",
    )
    if extended:
        kw.update(stack="#9999",
                  harness="run-shadow-unit-tests.sh mymod = success",
                  shared_benefit="single-client -- montalu")
    kw.update(over)
    with m.patch.object(ht, "has_extended_template", return_value=extended), \
            m.patch.object(ht, "template_requires_frontline_impact",
                           return_value=False), \
            m.patch.object(ht, "derive_branch_field",
                           side_effect=lambda b, r: b):
        return ht.compose_body(**kw)


class TestSharedHeadingPrefix(unittest.TestCase):
    """(1) one shared prefix; the `#{1,6}` heading ONLY where the gate takes
    it (odoo-erp PR 8095 widens Self-review-model, never the bounce lines);
    look-alikes rejected."""

    def test_heading_self_review_model_passes_body_file_check(self):
        body = _BODY_R1.replace("Self-review-model:",
                                "### Self-review-model:")
        self.assertIsNone(ht.validate_passthrough_body(body, bounce_round=1))

    def test_heading_bounce_lines_rejected_like_the_gate(self):
        # Neither develop's gate nor PR 8095 accepts a heading on these, so
        # the fail-fast must not pass a body the gate then bounces (#957).
        for old, new in (
                ("Root-cause-of-previous-bounce:",
                 "## Root-cause-of-previous-bounce:"),
                ("Prevencia (stream):", "#### Prevencia (stream):"),
                ("Prevencia (stream):", "### **Prevencia-read:**")):
            body = _BODY_R3.replace(old, new)
            self.assertIsNotNone(
                ht.validate_passthrough_body(body, bounce_round=3), new)
            self.assertNotEqual(gate.self_review_violations(body, 3), [])

    def test_bounce_label_shapes_match_the_gate(self):
        # Behavioural parity with the vendored gate regexes, line by line.
        lines = ("{l}: x", "- {l}: x", "* **{l}:** x", "  **{l}**: x",
                 "## {l}: x", "text {l}: x", "{l}x: x")
        for label, ours, theirs in (
                ("Root-cause-of-previous-bounce", ht._ROOTCAUSE_LINE_RE,
                 gate.SELF_REVIEW_ROOTCAUSE_RE),
                ("Prevencia (stream)", ht._PREVENCIA_STREAM_RE,
                 gate.SELF_REVIEW_PREVENCIA_RE)):
            for shape in lines:
                line = shape.format(l=label)
                self.assertEqual(bool(ours.search(line)),
                                 bool(theirs.search(line)), line)

    def test_lookalikes_still_rejected(self):
        for bad in ("####### Self-review-model:",   # 7 hashes: not a heading
                    "###Self-review-model:",        # heading needs a space
                    "### Self-review-modelx:",      # different label
                    "text ### Self-review-model:"):  # not line-anchored
            body = _BODY_R1.replace("Self-review-model:", bad)
            self.assertIsNotNone(
                ht.validate_passthrough_body(body, bounce_round=1),
                "look-alike %r must stay rejected" % bad)

    def test_label_regexes_share_the_one_prefix_behaviourally(self):
        # A bullet+bold line matches every label regex; a mid-line label none.
        for label, rx in (("Self-review-model", ht._SELF_REVIEW_MODEL_LINE_RE),
                          ("Root-cause-of-previous-bounce",
                           ht._ROOTCAUSE_LINE_RE),
                          ("Prevencia-read", ht._PREVENCIA_READ_RE),
                          ("Prevencia (stream)", ht._PREVENCIA_STREAM_RE),
                          ("Branch", ht._FULL_BODY_FIELD_LABEL_RES[0])):
            self.assertTrue(rx.search("  - **%s:** v" % label), label)
            self.assertFalse(rx.search("see %s: v" % label), label)


class TestComposeEmitsCanonicalPrevencia(unittest.TestCase):
    """(2) both compose paths emit `Prevencia (stream):`."""

    def test_extended_round3_body_passes_real_gate(self):
        body, err = _compose(
            bounce_round=3,
            root_cause="the `process` lens missed the version bump",
            prevencia_read=".claude/rules/version.md")
        self.assertIsNone(err, err)
        self.assertIn("Prevencia (stream): .claude/rules/version.md", body)
        self.assertNotIn("Prevencia-read:", body)
        # The REAL (vendored) gate's round-3 check accepts the composed body.
        self.assertEqual(gate.self_review_violations(body, 3), [])

    def test_generic_round2_emits_canonical_label(self):
        body, err = _compose(extended=False, bounce_round=2,
                             root_cause="rc", prevencia_read="/p.md")
        self.assertIsNone(err, err)
        self.assertIn("Prevencia (stream): /p.md", body)
        self.assertNotIn("Prevencia-read:", body)


class TestComposeEmitsGateDryRun(unittest.TestCase):
    """(3) a Gate-dry-run: line in the gate's value shape, never invented."""

    def _gate_dry_run(self, body):
        return gate.parse_readiness_fields(body).get("gate_dry_run")

    def test_extended_carries_supplied_pass(self):
        body, err = _compose(gate_dry_run="PASS @ 2c2fef52fce8")
        self.assertIsNone(err, err)
        val = self._gate_dry_run(body)
        self.assertEqual(val, "PASS @ 2c2fef52fce8")
        self.assertTrue(_GATE_DRY_RUN_VALUE_RE.match(val))
        # Still gate-clean on the required fields.
        self.assertEqual(
            gate.missing_fields(gate.parse_readiness_fields(body)), [])

    def test_extended_defaults_to_explicit_not_run(self):
        body, err = _compose()
        self.assertIsNone(err, err)
        self.assertEqual(self._gate_dry_run(body), "not run")

    def test_generic_carries_line_only_when_supplied(self):
        # A repo with no hand-off gate gets no `not run` noise about one.
        body, err = _compose(extended=False)
        self.assertIsNone(err, err)
        self.assertNotIn("Gate-dry-run:", body)
        body, err = _compose(extended=False, gate_dry_run="PASS @ abcdef1")
        self.assertIsNone(err, err)
        self.assertIn("Gate-dry-run: PASS @ abcdef1", body)

    def test_explicit_not_run_accepted(self):
        body, err = _compose(gate_dry_run="not run")
        self.assertIsNone(err, err)
        self.assertEqual(self._gate_dry_run(body), "not run")

    def test_malformed_value_refused(self):
        for bad in ("PASS", "yes", "PASS @ nothex!", "FAIL @ abcdef1",
                    "PASS @ abcdef1 (but 3 FAILs)",
                    "PASS @ abcdef1\nHEAD: deadbeef00",
                    "PASS @ abcdef1\rHEAD: deadbeef00",
                    "PASS\n@ abcdef1",
                    "PASS @ abcdef1 Harness-e2e-exempt: forged"):
            body, err = _compose(gate_dry_run=bad)
            self.assertEqual(body, "")
            self.assertIsNotNone(err, "malformed %r must be refused" % bad)
            self.assertIn("Gate-dry-run", err)
            self.assertEqual(len(err.splitlines()), 1, err)

    def test_flag_refused_on_verbatim_paths(self):
        # --body-file / --sign-only post the stream's own body verbatim, so a
        # --gate-dry-run there would be silently dropped: refuse it, before
        # any gh call (the seams below would raise if reached).
        for mode in ("sign_only", "body_file"):
            args = argparse.Namespace(
                repo="zbynekdrlik/odoo-erp", issue=42, branch="b",
                self_review_file=None, root_cause=None, closes_finding=None,
                prevencia_read=None, self_review_model=None, sign_only=None,
                body_file=None, gate_dry_run="PASS @ abcdef1")
            setattr(args, mode, "/nonexistent/body.md")
            boom = m.Mock(side_effect=AssertionError("gh reached"))
            with m.patch.object(airuleset, "_stream_self_login", boom), \
                    m.patch.object(airuleset, "_bounce_round", boom), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = airuleset.cmd_handoff(args)
            self.assertEqual(rc, 1, mode)
            self.assertIn("--gate-dry-run", out.getvalue())

    def test_malformed_flag_refused_before_any_gh_call(self):
        args = argparse.Namespace(
            repo="zbynekdrlik/odoo-erp", issue=42, branch="b",
            self_review_file="/nonexistent/t.md", root_cause=None,
            closes_finding=None, prevencia_read=None,
            self_review_model="claude-opus-4-8", sign_only=None,
            body_file=None, gate_dry_run="PASS")
        boom = m.Mock(side_effect=AssertionError("gh reached"))
        with m.patch.object(airuleset, "_stream_self_login", boom), \
                m.patch.object(airuleset, "_bounce_round", boom), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            rc = airuleset.cmd_handoff(args)
        self.assertEqual(rc, 1)
        self.assertIn("Gate-dry-run", out.getvalue())

    def test_cli_flag_registered(self):
        import subprocess
        r = subprocess.run([sys.executable, "airuleset.py", "handoff",
                            "--help"], capture_output=True, text=True,
                           timeout=30, cwd=_REPO)
        self.assertIn("--gate-dry-run", r.stdout)


class TestSignOnlyUsesSharedShapes(unittest.TestCase):
    """Adjacent same-class fix: --sign-only reuses the shared regexes, so a
    gate-correct `Prevencia (stream):` / heading-prefixed body is not
    blocked."""

    def _run(self, body, rnd):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "body.md")
            with open(path, "w") as f:
                f.write(body)
            home = os.path.expanduser("~")
            args = argparse.Namespace(
                repo="zbynekdrlik/odoo-erp", issue=42, branch=None,
                self_review_file=None, root_cause=None, closes_finding=None,
                prevencia_read=None, self_review_model=None, sign_only=path)
            with m.patch.object(airuleset, "HANDOFF_GATE_DIR",
                                os.path.relpath(os.path.join(td, "g"), home)), \
                    m.patch.object(airuleset, "HANDOFF_GATE_LOG",
                                   os.path.relpath(os.path.join(td, "l"),
                                                   home)), \
                    m.patch.object(airuleset, "_bounce_round",
                                   return_value=rnd), \
                    m.patch.object(airuleset, "_stream_self_login",
                                   return_value="x"), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                rc = airuleset.cmd_handoff(args)
            return rc, out.getvalue()

    def test_sign_only_round3_canonical_prevencia_accepted(self):
        rc, out = self._run(_BODY_R3, 3)
        self.assertEqual(rc, 0, out)

    def test_sign_only_heading_self_review_model_accepted(self):
        rc, out = self._run(
            _BODY_R1.replace("Self-review-model:", "### Self-review-model:"),
            1)
        self.assertEqual(rc, 0, out)

    def test_sign_only_round2_without_prevencia_still_blocked(self):
        body = _BODY_R3.replace(
            "Prevencia (stream): re-check version strictly-greater "
            "before hand-off\n", "")
        rc, out = self._run(body, 2)
        self.assertEqual(rc, 1)
        self.assertIn("Prevencia", out)


def _load_extractor():
    path = os.path.join(_REPO, "scripts", "extract_handoff_gate_validators.py")
    spec = importlib.util.spec_from_file_location("_extract_1125", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFixtureFreshness(unittest.TestCase):
    """(4) the vendored fixture is pinned; drift is reported, offline."""

    def test_fixture_provenance_matches_pin(self):
        ex = _load_extractor()
        with open(os.path.join(_REPO, "tests", "fixtures",
                               "handoff_gate_pure.py"),
                  encoding="utf-8") as f:
            head = f.read(2000)
        mo = re.search(r"_gate\.py blob ([0-9a-f]{40})", head)
        self.assertIsNotNone(mo, "fixture header has no provenance blob")
        self.assertEqual(
            mo.group(1), ex.PINNED_GATE_BLOB,
            "fixture regenerated without updating PINNED_GATE_BLOB (or the "
            "reverse) — regenerate and re-pin together")

    def test_fixture_body_matches_recorded_hash(self):
        # The pin alone only ties two hand-editable strings together; the
        # extractor also records a sha256 of the extracted symbols it wrote,
        # so a hand edit of the vendored body (or a header-only "refresh")
        # is caught here.
        ex = _load_extractor()
        with open(os.path.join(_REPO, "tests", "fixtures",
                               "handoff_gate_pure.py"),
                  encoding="utf-8") as f:
            text = f.read()
        mo = re.search(r"(?m)^# Extracted-sha256: ([0-9a-f]{64})$", text)
        self.assertIsNotNone(mo, "fixture header has no Extracted-sha256")
        self.assertIn(ex.BODY_MARKER, text)
        payload = text.split(ex.BODY_MARKER, 1)[1]
        self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(),
                         mo.group(1))

    def test_regenerate_writes_verifiable_fixture_from_crlf_source(self):
        ex = _load_extractor()
        src_text = "\n".join(
            "%s = %d" % (n, i) for i, n in enumerate(sorted(ex.WANT_ASSIGN)))
        src_text += "\n" + "\n".join(
            "def %s():\n    return 1\n" % n for n in sorted(ex.WANT_FUNC))
        with tempfile.TemporaryDirectory() as td:
            src, out = os.path.join(td, "_gate.py"), os.path.join(td, "f.py")
            data = src_text.replace("\n", "\r\n").encode()
            with open(src, "wb") as f:
                f.write(data)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = ex.main(["--src", src, "--out", out,
                              "--provenance", "develop x"])
            with open(out, "rb") as f:
                written = f.read()
        self.assertEqual(rc, 0)
        self.assertNotIn(b"\r", written)
        text = written.decode()
        self.assertIn("blob %s, develop x" % ex.git_blob_sha(data), text)
        payload = text.split(ex.BODY_MARKER, 1)[1]
        self.assertIn("# Extracted-sha256: %s" % hashlib.sha256(
            payload.encode()).hexdigest(), text)

    def test_git_blob_sha_matches_git(self):
        ex = _load_extractor()
        # `printf 'hello\n' | git hash-object --stdin`
        self.assertEqual(ex.git_blob_sha(b"hello\n"),
                         "ce013625030ba8dba906f756967f9e9ca394464a")

    def _check(self, content):
        ex = _load_extractor()
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "_gate.py")
            with open(src, "wb") as f:
                f.write(content)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                rc = ex.main(["--src", src, "--check"])
        return ex, rc, out.getvalue()

    def test_check_reports_drift(self):
        ex, rc, out = self._check(b"# a different gate\n")
        self.assertEqual(rc, 1)
        self.assertIn("DRIFT", out)
        self.assertIn(ex.PINNED_GATE_BLOB[:12], out)
        self.assertIn(hashlib.sha1(b"blob 19\0# a different gate\n")
                      .hexdigest()[:12], out)

    def test_check_passes_on_pinned_blob(self):
        ex = _load_extractor()
        with m.patch.object(ex, "PINNED_GATE_BLOB",
                            ex.git_blob_sha(b"# pinned\n")):
            with tempfile.TemporaryDirectory() as td:
                src = os.path.join(td, "_gate.py")
                with open(src, "wb") as f:
                    f.write(b"# pinned\n")
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = ex.main(["--src", src, "--check"])
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
