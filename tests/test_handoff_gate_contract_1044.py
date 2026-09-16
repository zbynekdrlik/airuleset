"""Contract test: the hand-off composer's output must satisfy odoo-erp's
handoff-gate body-shape validators (#1044).

Runs the REAL gate parser — the pure body-shape validators AST-extracted from
odoo-erp's ``scripts/handoff_gate/_gate.py`` into
``tests/fixtures/handoff_gate_pure.py`` (regenerate via
``scripts/extract_handoff_gate_validators.py``) — against the composer's
output.

Two composer paths are covered:
  * FIELD-generated (``compose_body`` with a clean ``Self-review:`` table) —
    already gate-clean; locked as a regression.
  * PASS-THROUGH (``validate_passthrough_body`` + a verbatim body) — the fix
    for the mangling defect where a stream, having no evidence channel, shoves
    its full gate-compliant body into ``--self-review-file`` and the composer
    wraps it, emitting a SECOND ``HEAD:`` line so the gate parses the stale one.

NOT exercised here (needs the live gate on the gk box): every git/gh/execution
check — merge-tree conflict, stale-base, addon-test execution evidence, E2E,
mutation probe, CI-red-at-HEAD, Source-verified. This is a BODY-SHAPE contract,
same documented boundary as the vendored fixture's own header.
"""
from __future__ import annotations

import os
import sys
import unittest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tests", "fixtures"))

import cli_handoff_template as ht  # noqa: E402
import handoff_gate_pure as gate  # noqa: E402  (vendored odoo-erp validators)

# Emoji verdict cells — spelled via escapes so the file stays ASCII-clean.
_R, _Y, _B = "\U0001f534", "\U0001f7e1", "\U0001f535"


def _row(lens: str, ev: str) -> str:
    return "| %s | 0 %s 0 %s 0 %s | %s |" % (lens, _R, _Y, _B, ev)


_TABLE_6 = (
    "| lens | verdict | evidence |\n|---|---|---|\n"
    + "\n".join([
        _row("security", "cli_handoff_template.py:120"),
        _row("correctness", "tests/test_handoff_gate_contract_1044.py:40"),
        _row("test-integrity", "test_field_generated_6lens_passes_gate"),
        _row("evidence-integrity", "airuleset.py:4318"),
        _row("design-doctrine", "cli_handoff_template.py:90"),
        _row("process", "pyproject.toml:3"),
    ])
    + "\n"
)

# The composer's own default lens list (HANDOFF_DEFAULT_LENSES) has a 7th
# 'shared-benefit' row the gate ignores (gate iterates only its 6 lenses).
_TABLE_7 = _TABLE_6.rstrip("\n") + "\n" + _row("shared-benefit", "f.py:6") + "\n"

# A COMPLETE, gate-compliant body a stream authors itself — the pass-through
# input. One HEAD line, a fenced Red-on-revert block, all fields.
_STREAM_BODY = (
    "READY-FOR-REVIEW: branch montalu/9999-thing\n"
    "\n"
    "Self-review-model: claude-opus-4-8\n"
    "**Self-review:**\n"
    "\n"
    + _TABLE_6
    + "\n"
    "Branch: montalu/9999-thing\n"
    "HEAD: 65ea5ae2b858\n"
    "Stack: #9999\n"
    "Verified-at-UTC: 2026-09-16T10:00:00Z\n"
    "Harness: run-shadow-unit-tests.sh mymod = success\n"
    "Shared-benefit: single-client -- montalu\n"
    "\n"
    "Red-on-revert (test_new_guard):\n"
    "```\n"
    "FAIL test_new_guard - AssertionError\n"
    "```\n"
)

# What a stream WRONGLY passes as --self-review-file today (no evidence channel):
# a full body, with its OWN evidence HEAD, that the composer then wraps.
_FULL_BODY_AS_TABLE = (
    "READY-FOR-REVIEW: branch montalu/9999-thing\n"
    "\n"
    "Branch: montalu/9999-thing\n"
    "HEAD: b1b1b1b1b1b1\n"
    "Stack: #9999\n"
    "Verified-at-UTC: 2026-09-16T09:00:00Z\n"
    "Harness: run-shadow-unit-tests.sh mymod = success\n"
    "\n"
    "Self-review-model: claude-opus-4-8\n"
    "Self-review:\n"
    + _TABLE_6
    + "\nRed-on-revert (test_new_guard):\n```\nFAIL test_new_guard\n```\n"
)


def _compose_extended(table, **over):
    kw = dict(
        repo="zbynekdrlik/odoo-erp", branch="montalu/9999-thing",
        head_sha="65ea5ae2b858",
        verified_at_utc="2026-09-16T10:00:00Z",
        self_review_table=table, bounce_round=1, stack="#9999",
        harness="run-shadow-unit-tests.sh mymod = success",
        shared_benefit="single-client -- montalu",
        self_review_model="claude-opus-4-8",
    )
    kw.update(over)
    return ht.compose_body(**kw)


class TestFieldGeneratedPassesGate(unittest.TestCase):
    """Regression lock: the clean field-generated shape stays gate-clean."""

    def _assert_gate_clean(self, body):
        fields = gate.parse_readiness_fields(body)
        self.assertEqual(gate.missing_fields(fields), [],
                         "gate reports missing template fields")
        va = (fields.get("verified_at_utc") or "").strip()
        self.assertTrue(gate.VERIFIED_AT_UTC_TIMESTAMP_RE.match(va),
                        "Verified-at-UTC not gate-shaped: %r" % va)
        self.assertEqual(gate.self_review_violations(body, 1), [],
                         "gate reports Self-review violations")

    def test_field_generated_6lens_passes_gate_pure_validators(self):
        body, err = _compose_extended(_TABLE_6)
        self.assertIsNone(err, err)
        self._assert_gate_clean(body)

    def test_field_generated_7lens_passes_gate_pure_validators(self):
        body, err = _compose_extended(_TABLE_7)
        self.assertIsNone(err, err)
        self._assert_gate_clean(body)


class TestComposerRefusesFullBodyMangling(unittest.TestCase):
    """RED->GREEN: the composer must REFUSE a full body passed as the
    Self-review table instead of wrapping it (which buries a second, stale
    HEAD line the gate then parses)."""

    def test_compose_body_refuses_full_body_as_self_review_file(self):
        body, err = _compose_extended(_FULL_BODY_AS_TABLE)
        # RED today: compose_body wraps the full body and returns it with no
        # error, producing a second HEAD line.
        self.assertIsNotNone(
            err, "compose_body must refuse a full body passed as the "
                 "Self-review table (it silently mangled it into a duplicate "
                 "HEAD instead)")
        self.assertIn("--body-file", err,
                      "the refusal must point the stream at --body-file")
        self.assertEqual(body, "", "no body may be emitted on refusal")

    def test_refusal_prevents_buried_second_head(self):
        # Proof of the defect the refusal prevents: WITHOUT refusal the wrapped
        # body carries TWO HEAD lines (stream evidence head + composer head) and
        # the gate parses the FIRST (stale) one.
        body, err = _compose_extended(
            _FULL_BODY_AS_TABLE,
            head_sha="c1c1c1c1c1c1")
        if err is None:  # pre-fix path — demonstrate the mangling is real
            import re
            heads = re.findall(r"(?m)^HEAD:\s*(\S+)", body)
            parsed = gate.parse_readiness_fields(body).get("head")
            self.fail(
                "compose_body did not refuse; it produced %d HEAD lines %r and "
                "the gate parses %r (the stale stream head, not the composer "
                "head cccc...) — this is the mangling defect" % (
                    len(heads), heads, parsed))
        # GREEN: refused, so no mangled body exists.
        self.assertEqual(body, "")


class TestPassthroughBody(unittest.TestCase):
    """RED->GREEN: a stream-authored, gate-compliant body must be validatable
    for the minimal cross-repo invariants and postable VERBATIM (fences and
    the single HEAD line intact)."""

    def test_passthrough_api_exists(self):
        self.assertTrue(
            hasattr(ht, "validate_passthrough_body"),
            "cli_handoff_template.validate_passthrough_body is missing — the "
            "pass-through post path (#1044) is not implemented")

    def test_passthrough_body_passes_gate_pure_validators(self):
        vpb = getattr(ht, "validate_passthrough_body", None)
        self.assertIsNotNone(vpb, "validate_passthrough_body missing")
        err = vpb(_STREAM_BODY, bounce_round=1)
        self.assertIsNone(err, "gate-compliant body wrongly rejected: %s" % err)
        # Verbatim: exactly ONE HEAD line, the fence intact.
        import re
        self.assertEqual(len(re.findall(r"(?m)^HEAD:", _STREAM_BODY)), 1)
        self.assertIn("```\nFAIL test_new_guard", _STREAM_BODY)
        # The gate's pure validators pass on the verbatim body.
        fields = gate.parse_readiness_fields(_STREAM_BODY)
        self.assertEqual(gate.missing_fields(fields), [])
        self.assertEqual(gate.self_review_violations(_STREAM_BODY, 1), [])
        self.assertEqual(gate.parse_readiness_fields(_STREAM_BODY)["head"],
                         "65ea5ae2b858")

    def test_passthrough_rejects_missing_ready_for_review(self):
        vpb = getattr(ht, "validate_passthrough_body", None)
        self.assertIsNotNone(vpb, "validate_passthrough_body missing")
        err = vpb("Branch: x\nHEAD: aaaaaaaa\n", bounce_round=1)
        self.assertIsNotNone(err, "must reject a body with no RFR marker")

    def test_passthrough_rejects_missing_self_review_model(self):
        vpb = getattr(ht, "validate_passthrough_body", None)
        self.assertIsNotNone(vpb, "validate_passthrough_body missing")
        no_model = _STREAM_BODY.replace(
            "Self-review-model: claude-opus-4-8\n", "")
        err = vpb(no_model, bounce_round=1)
        self.assertIsNotNone(
            err, "must reject a body missing the Self-review-model line")

    def test_passthrough_round2_requires_bounce_fields(self):
        vpb = getattr(ht, "validate_passthrough_body", None)
        self.assertIsNotNone(vpb, "validate_passthrough_body missing")
        err = vpb(_STREAM_BODY, bounce_round=2)
        self.assertIsNotNone(
            err, "round>=2 must require Root-cause-of-previous-bounce/"
                 "Prevencia-read")


if __name__ == "__main__":
    unittest.main()
