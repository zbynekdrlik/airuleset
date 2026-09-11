"""RED->GREEN tests for cli_handoff_template (#969).

Verifies:
  1. render_extended_body emits Branch/HEAD/Stack/Harness/Shared-benefit
     fields that the odoo-erp subdev_handoff_gate.py gate requires.
  2. Bounce-round is OMITTED on round 1 (first hand-off, no bounce).
  3. Bounce-round IS emitted on round >= 2.
  4. derive_branch_field returns owner:branch for a fork, plain branch
     for same-repo.
  5. validate_extended_flags fails loud on missing required fields.
  6. render_generic_body preserves the pre-#969 shape (minus the
     Bounce-round: 1 fix).
  7. has_extended_template detects template presence via runner.
"""
from __future__ import annotations

import re
import sys
import os
import unittest

# Ensure the repo root is on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_handoff_template import (
    compose_body,
    derive_branch_field,
    has_extended_template,
    render_extended_body,
    render_generic_body,
    validate_extended_flags,
)

# --- Fixture: minimal odoo-erp gate field patterns (mirroring the real gate) ---

# From subdev_handoff_gate.py FIELD_PATTERNS / REQUIRED_FIELDS:
_FIELD_RE = {
    "branch": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Branch\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "head": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**HEAD\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "stack": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Stack\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "verified_at_utc": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Verified-at-UTC\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "harness": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Harness\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "shared_benefit": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Shared-benefit\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "tested_tree": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Tested-tree\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "evidence_head": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Evidence-HEAD\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    ),
    "tenant_scope": re.compile(
        r"(?im)^[\s*_-]*Tenant-scope\s*:"
    ),
    "source_verified": re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**Source-verified\**[ \t]*:[ \t]*\**[ \t]*(\S.*?)[ \t]*$"
    ),
}

BOUNCE_ROUND_RE = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*\**Bounce-round\**[ \t]*:[ \t]*(\d+)"
)

REVIEW_TABLE = """\
| lens | verdict | evidence |
|---|---|---|
| security | 0 \U0001f534 0 \U0001f7e1 0 \U0001f535 | foo.py:10 |
| correctness | 0 \U0001f534 0 \U0001f7e1 0 \U0001f535 | bar.py:20 |
"""


def _parse_fields(body):
    """Extract field values from a rendered body using the gate's patterns."""
    result = {}
    for key, pattern in _FIELD_RE.items():
        m = pattern.search(body)
        if m:
            result[key] = m.group(1).strip() if m.lastindex else "present"
    return result


class TestRenderExtendedBody(unittest.TestCase):
    """The extended body MUST contain Branch/HEAD/Stack/Harness/Shared-benefit."""

    def _render(self, bounce_round=1, **kwargs):
        defaults = dict(
            branch_field="kvaskodev:david4/6665-label-bridge",
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            verified_at_utc="2026-09-09T12:54:52Z",
            stack="#6657, #6665",
            harness="pytest tests/ (local venv)",
            shared_benefit="cisto klientske -- pekarenka",
            self_review_table=REVIEW_TABLE,
            bounce_round=bounce_round,
        )
        defaults.update(kwargs)
        return render_extended_body(**defaults)

    def test_required_fields_present(self):
        body = self._render()
        fields = _parse_fields(body)
        for key in ("branch", "head", "stack", "verified_at_utc",
                     "harness", "shared_benefit"):
            self.assertIn(key, fields,
                          "MISSING required field %r in rendered body" % key)
            self.assertTrue(fields[key],
                            "Field %r is empty in rendered body" % key)

    def test_branch_field_has_owner_colon_branch(self):
        body = self._render()
        fields = _parse_fields(body)
        self.assertEqual(fields["branch"],
                         "kvaskodev:david4/6665-label-bridge")

    def test_bounce_round_omitted_on_round_1(self):
        """Round 1 = first hand-off, no bounce has occurred.
        Bounce-round: 1 must NOT appear (it trips the gate's
        BRANCH-ALREADY-INTEGRATED heuristic)."""
        body = self._render(bounce_round=1)
        self.assertIsNone(BOUNCE_ROUND_RE.search(body),
                          "Bounce-round should NOT appear on round 1")

    def test_bounce_round_omitted_on_round_0(self):
        body = self._render(bounce_round=0)
        self.assertIsNone(BOUNCE_ROUND_RE.search(body))

    def test_bounce_round_emitted_on_round_2(self):
        body = self._render(
            bounce_round=2,
            root_cause="lens -- why",
            prevencia_read="/path/to/prevencia.md",
        )
        m = BOUNCE_ROUND_RE.search(body)
        self.assertIsNotNone(m, "Bounce-round must appear on round 2")
        self.assertEqual(m.group(1), "2")

    def test_bounce_round_emitted_on_round_3(self):
        body = self._render(
            bounce_round=3,
            root_cause="lens -- why",
            prevencia_read="/path/to/prevencia.md",
        )
        m = BOUNCE_ROUND_RE.search(body)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "3")

    def test_optional_fields_when_given(self):
        body = self._render(
            tested_tree="abc1234",
            evidence_head="def5678",
            tenant_scope="slovnormal only",
            source_verified="n/a -- no external fetch",
        )
        fields = _parse_fields(body)
        self.assertIn("tested_tree", fields)
        self.assertIn("evidence_head", fields)
        self.assertIn("tenant_scope", fields)
        self.assertIn("source_verified", fields)

    def test_optional_fields_omitted_when_none(self):
        body = self._render()
        fields = _parse_fields(body)
        self.assertNotIn("tested_tree", fields)
        self.assertNotIn("evidence_head", fields)
        self.assertNotIn("tenant_scope", fields)
        self.assertNotIn("source_verified", fields)

    def test_ready_for_review_header_present(self):
        body = self._render()
        self.assertRegex(body, r"^READY-FOR-REVIEW:")

    def test_self_review_table_present(self):
        body = self._render()
        self.assertIn("| security |", body)

    def test_closes_finding_lines(self):
        body = self._render(closes_finding=["F1 -- fixed", "F2 -- fixed"])
        self.assertIn("Closes-finding: F1 -- fixed", body)
        self.assertIn("Closes-finding: F2 -- fixed", body)


class TestRenderGenericBody(unittest.TestCase):
    """The generic body preserves the pre-#969 shape, minus Bounce-round: 1."""

    def test_bounce_round_omitted_on_round_1(self):
        body = render_generic_body(
            branch="dev",
            head_sha="abc123",
            verified_at_utc="2026-09-09T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=1,
        )
        self.assertIsNone(BOUNCE_ROUND_RE.search(body))

    def test_bounce_round_emitted_on_round_2(self):
        body = render_generic_body(
            branch="dev",
            head_sha="abc123",
            verified_at_utc="2026-09-09T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=2,
            root_cause="lens -- why",
            prevencia_read="/path",
        )
        m = BOUNCE_ROUND_RE.search(body)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "2")

    def test_no_branch_field_line(self):
        """Generic body should NOT have a separate Branch: field line
        (it has READY-FOR-REVIEW: branch ... instead)."""
        body = render_generic_body(
            branch="dev",
            head_sha="abc123",
            verified_at_utc="2026-09-09T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=1,
        )
        # Should NOT match a standalone "Branch: " field.
        self.assertIsNone(_FIELD_RE["branch"].search(body))


class TestDeriveBranchField(unittest.TestCase):
    def test_fork_returns_owner_colon_branch(self):
        result = derive_branch_field(
            "david4/6665-fix",
            target_repo="zbynekdrlik/odoo-erp",
            origin_url="https://github.com/kvaskodev/odoo-erp.git",
        )
        self.assertEqual(result, "kvaskodev:david4/6665-fix")

    def test_same_repo_returns_plain_branch(self):
        result = derive_branch_field(
            "dev",
            target_repo="zbynekdrlik/airuleset",
            origin_url="https://github.com/zbynekdrlik/airuleset.git",
        )
        self.assertEqual(result, "dev")

    def test_scp_form_url(self):
        result = derive_branch_field(
            "fix-thing",
            target_repo="zbynekdrlik/odoo-erp",
            origin_url="git@github.com:kvaskodev/odoo-erp.git",
        )
        self.assertEqual(result, "kvaskodev:fix-thing")

    def test_no_origin_url_returns_plain_branch(self):
        result = derive_branch_field(
            "dev",
            target_repo="zbynekdrlik/airuleset",
            origin_url="",
        )
        self.assertEqual(result, "dev")


class TestValidateExtendedFlags(unittest.TestCase):
    def test_all_present_returns_none(self):
        self.assertIsNone(validate_extended_flags(
            stack="#100",
            harness="pytest",
            shared_benefit="shared -- mechanism",
        ))

    def test_missing_stack(self):
        result = validate_extended_flags(
            stack="",
            harness="pytest",
            shared_benefit="shared",
        )
        self.assertIn("--stack", result)

    def test_missing_all(self):
        result = validate_extended_flags(
            stack=None,
            harness=None,
            shared_benefit=None,
        )
        self.assertIn("--stack", result)
        self.assertIn("--harness", result)
        self.assertIn("--shared-benefit", result)


class TestComposeBody(unittest.TestCase):
    """compose_body uses extended shape when extended flags are given."""

    def test_extended_flags_override_failed_probe(self):
        """Y1: when --stack/--harness/--shared-benefit are given, use
        extended shape even if the probe returns False."""
        import unittest.mock as m
        with m.patch("cli_handoff_template.has_extended_template",
                      return_value=False):
            body, err = compose_body(
                repo="zbynekdrlik/odoo-erp",
                branch="dev",
                head_sha="abc123def",
                verified_at_utc="2026-09-09T12:00:00Z",
                self_review_table=REVIEW_TABLE,
                bounce_round=1,
                stack="#100",
                harness="pytest",
                shared_benefit="shared",
            )
        self.assertIsNone(err)
        fields = _parse_fields(body)
        self.assertIn("branch", fields)
        self.assertIn("stack", fields)


class TestHasExtendedTemplate(unittest.TestCase):
    def test_runner_returns_content_means_true(self):
        self.assertTrue(has_extended_template(
            "zbynekdrlik/odoo-erp",
            runner=lambda path: "abc123sha",
        ))

    def test_runner_raises_means_false(self):
        def fail_runner(path):
            raise RuntimeError("404")
        self.assertFalse(has_extended_template(
            "zbynekdrlik/other",
            runner=fail_runner,
        ))

    def test_runner_returns_empty_means_false(self):
        self.assertFalse(has_extended_template(
            "zbynekdrlik/other",
            runner=lambda path: "",
        ))


if __name__ == "__main__":
    unittest.main()
