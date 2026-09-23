"""#1120 (a) — the hand-off composer gains `--frontline-impact`, rendered as a
`Frontline-impact:` line (the `--tenant-scope` pattern), and FAILS LOUD when the
target repo's hand-off template DECLARES the field required.

Root cause (design 5791161529): a change to the shared Frontline shell (odoo-erp
#7883) had no hand-off field forcing the author to enumerate every module the
shell hosts + prove each. airuleset supplies the field plumbing; the repo (its
subdev-handoff-comment.md template) declares the requirement (Approach 2
rejected — domain knowledge stays in odoo-erp).
"""

import re
import sys
from pathlib import Path
from unittest import TestCase, main
import unittest.mock as m

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from cli_handoff_template import (  # noqa: E402
    compose_body,
    render_extended_body,
    render_generic_body,
    template_requires_frontline_impact,
)

FRONTLINE_RE = re.compile(r"(?im)^[\s*_-]*Frontline-impact\s*:\s*(.+)$")

REVIEW_TABLE = """\
| lens | verdict | evidence |
|---|---|---|
| security | 0 \U0001f534 0 \U0001f7e1 0 \U0001f535 | foo.py:10 |
"""


def _ext(**kwargs):
    defaults = dict(
        branch_field="david:7892-adopt",
        head_sha="abcdef1234567890abcdef1234567890abcdef12",
        verified_at_utc="2026-09-23T12:00:00Z",
        stack="#7892",
        harness="pytest tests/",
        shared_benefit="fleet",
        self_review_table=REVIEW_TABLE,
        bounce_round=1,
    )
    defaults.update(kwargs)
    return render_extended_body(**defaults)


class TestRenderFrontlineImpact(TestCase):
    def test_extended_emits_line_when_given(self):
        body = _ext(frontline_impact="Granč ERP: pokladňa e2e/pos.spec.ts")
        mm = FRONTLINE_RE.search(body)
        self.assertIsNotNone(mm, "Frontline-impact: line must render")
        self.assertIn("pokladňa", mm.group(1))

    def test_extended_omits_line_when_none(self):
        body = _ext()
        self.assertIsNone(FRONTLINE_RE.search(body))

    def test_generic_emits_line_when_given(self):
        body = render_generic_body(
            branch="david:7892-adopt",
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            verified_at_utc="2026-09-23T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=1,
            frontline_impact="Granč ERP: rozvoz e2e/rozvoz.spec.ts",
        )
        self.assertIsNotNone(FRONTLINE_RE.search(body))

    def test_generic_omits_line_when_none(self):
        body = render_generic_body(
            branch="b",
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            verified_at_utc="2026-09-23T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=1,
        )
        self.assertIsNone(FRONTLINE_RE.search(body))


class TestTemplateRequiresFrontlineImpact(TestCase):
    """The CONTENT probe: True iff the template declares the field."""

    def test_runner_content_declares_field(self):
        self.assertTrue(template_requires_frontline_impact(
            "zbynekdrlik/odoo-erp",
            runner=lambda path: "Branch:\nHEAD:\nFrontline-impact:\n"))

    def test_runner_content_no_field(self):
        self.assertFalse(template_requires_frontline_impact(
            "zbynekdrlik/odoo-erp",
            runner=lambda path: "Branch:\nHEAD:\nTenant-scope:\n"))

    def test_runner_raises_is_false(self):
        def boom(path):
            raise RuntimeError("404")
        self.assertFalse(template_requires_frontline_impact(
            "x/y", runner=boom))

    def test_runner_empty_is_false(self):
        self.assertFalse(template_requires_frontline_impact(
            "x/y", runner=lambda path: ""))

    def test_prose_mention_does_not_require(self):
        # A field DECLARATION is line-anchored; an inline prose mention or
        # example must NOT flip the repo into requiring it.
        prose = ("When you touch the shared shell, add a `Frontline-impact:` "
                 "line listing every module.\n")
        self.assertFalse(template_requires_frontline_impact(
            "x/y", runner=lambda path: prose))

    def test_field_declaration_with_leading_markdown(self):
        # A real declaration tolerating a leading list/quote/emphasis char.
        for tmpl in ("- Frontline-impact:\n", "> Frontline-impact: <fill>\n",
                     "**Frontline-impact:**\n"):
            self.assertTrue(template_requires_frontline_impact(
                "x/y", runner=lambda path, t=tmpl: t), tmpl)


class TestComposeFailsLoud(TestCase):
    """compose_body fails LOUD when the template requires the field and no
    value is supplied; passes when supplied or when not required."""

    def _compose(self, **kwargs):
        defaults = dict(
            repo="zbynekdrlik/odoo-erp",
            branch="david:7892-adopt",
            head_sha="abcdef1234567890abcdef1234567890abcdef12",
            verified_at_utc="2026-09-23T12:00:00Z",
            self_review_table=REVIEW_TABLE,
            bounce_round=1,
            stack="#7892",
            harness="pytest",
            shared_benefit="fleet",
        )
        defaults.update(kwargs)
        return compose_body(**defaults)

    def test_required_missing_value_fails_loud(self):
        with m.patch("cli_handoff_template.has_extended_template",
                     return_value=True), \
             m.patch(
                 "cli_handoff_template.template_requires_frontline_impact",
                 return_value=True):
            body, err = self._compose()
        self.assertEqual(body, "")
        self.assertIsNotNone(err)
        self.assertIn("Frontline-impact", err)

    def test_required_with_value_succeeds(self):
        with m.patch("cli_handoff_template.has_extended_template",
                     return_value=True), \
             m.patch(
                 "cli_handoff_template.template_requires_frontline_impact",
                 return_value=True):
            body, err = self._compose(
                frontline_impact="Granč ERP: pokladňa pos.spec.ts; "
                                 "rozvoz rozvoz.spec.ts")
        self.assertIsNone(err)
        self.assertIsNotNone(FRONTLINE_RE.search(body))

    def test_not_required_missing_value_ok(self):
        with m.patch("cli_handoff_template.has_extended_template",
                     return_value=True), \
             m.patch(
                 "cli_handoff_template.template_requires_frontline_impact",
                 return_value=False):
            body, err = self._compose()
        self.assertIsNone(err)
        self.assertIsNone(FRONTLINE_RE.search(body))


if __name__ == "__main__":
    main()
