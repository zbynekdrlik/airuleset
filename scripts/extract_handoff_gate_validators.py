#!/usr/bin/env python3
"""Extract odoo-erp's PURE handoff-gate body-shape validators into a vendored
fixture for airuleset's contract test (#1044).

WHY THIS EXISTS
----------------
airuleset's hand-off composer (`airuleset.py handoff` / `cli_handoff_template`)
must emit a READY-FOR-REVIEW comment body that odoo-erp's
`scripts/subdev_handoff_gate.py` accepts. The full gate cannot be imported on a
non-odoo-erp box: `handoff_gate/_gate.py` imports `check_changelog_placement`
and needs a live git worktree + `gh`. But its BODY-SHAPE validators
(`parse_readiness_fields`, `missing_fields`, `parse_self_review`,
`self_review_violations`, plus their regex/constant deps) are PURE — no I/O.

This tool AST-extracts exactly those pure symbols by NAME from a local checkout
of `_gate.py` into a standalone module, so the contract test runs the REAL gate
parser against the composer's output (per #1044), not a hand-mirrored copy.

USAGE (regenerate the vendored fixture from a fresh odoo-erp checkout):
    python3 scripts/extract_handoff_gate_validators.py \
        --src <path>/odoo-erp/scripts/handoff_gate/_gate.py \
        --out tests/fixtures/handoff_gate_pure.py \
        --provenance "odoo-erp _gate.py blob <sha>, develop <sha>, <date>"

What is NOT extracted (and thus NOT exercised offline): every git/gh/execution
check (merge-tree conflict, stale-base, addon-test execution evidence, E2E,
mutation probe, CI-red-at-HEAD, Source-verified). Those need the live gate on
the gk box — the contract test asserts only that the composer's FIELD/TABLE
shape passes the pure validators.
"""
from __future__ import annotations

import argparse
import ast

# Module-level assignments (regex/constant deps) the pure functions reference.
WANT_ASSIGN = {
    "REQUIRED_FIELDS", "_FIELD_LABELS", "FIELD_PATTERNS",
    "_ANY_FIELD_LABEL_LINE_RE", "_CONTINUATION_LINE_RE", "STACK_NOTE_LINE_RE",
    "HEAD_SHA_RE", "TICKET_RE", "VERIFIED_AT_UTC_TIMESTAMP_RE",
    "COMMIT_TRAILER_RE", "GK_REVIEW_LENSES", "_GK_REVIEW_LENS_SET",
    "SELF_REVIEW_MARKER_RE", "SELF_REVIEW_MODEL_RE", "SELF_REVIEW_VERDICT_RE",
    "SELF_REVIEW_LOCATOR_RE", "SELF_REVIEW_ROOTCAUSE_RE",
    "SELF_REVIEW_PREVENCIA_RE", "_ROOTCAUSE_LENS_ALT",
    "SELF_REVIEW_ROOTCAUSE_LENS_RE",
}
# Pure body-shape functions.
WANT_FUNC = {
    "_consume_continuation_lines", "parse_readiness_fields", "missing_fields",
    "_sanitize_markdown_wrapping", "parse_self_review",
    "self_review_violations",
}


def extract(src_text: str) -> list[tuple[int, str]]:
    tree = ast.parse(src_text)
    segments: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANT_FUNC:
            segments.append((node.lineno, ast.get_source_segment(src_text, node)))
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in WANT_ASSIGN for n in names):
                segments.append((node.lineno, ast.get_source_segment(src_text, node)))
    segments.sort(key=lambda x: x[0])
    return segments


HEADER = '''\
# AUTO-EXTRACTED — DO NOT EDIT BY HAND.
#
# Pure body-shape validators vendored from odoo-erp
# scripts/handoff_gate/_gate.py for airuleset's hand-off composer contract
# test (#1044). Regenerate with scripts/extract_handoff_gate_validators.py.
#
# Provenance: {provenance}
#
# The full gate is NOT importable off an odoo-erp checkout (it needs
# check_changelog_placement + git/gh); these pure functions are its
# line-anchored template-field + Self-review-table validators, verbatim.
import re


def fleet_model_allowlist():
    # In the offline contract test the airuleset fleet allowlist loader is
    # UNCHECKED (fail-safe None), exactly as the live gate behaves when
    # airuleset is unreachable — the MISSING-line/shape checks still apply.
    return None
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to odoo-erp _gate.py")
    ap.add_argument("--out", required=True, help="output vendored module path")
    ap.add_argument("--provenance", default="(unspecified)",
                    help="source sha/date recorded in the header")
    args = ap.parse_args()

    with open(args.src, encoding="utf-8") as f:
        src_text = f.read()
    segments = extract(src_text)
    if len(segments) < len(WANT_ASSIGN) + len(WANT_FUNC):
        got = len(segments)
        want = len(WANT_ASSIGN) + len(WANT_FUNC)
        raise SystemExit(
            "extracted %d/%d symbols — the gate's shape changed; update "
            "WANT_ASSIGN/WANT_FUNC before regenerating" % (got, want))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(HEADER.format(provenance=args.provenance))
        f.write("\n\n")
        f.write("\n\n\n".join(seg for _, seg in segments))
        f.write("\n")
    print("wrote %d symbols -> %s" % (len(segments), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
