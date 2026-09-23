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

USAGE (regenerate the vendored fixture from the current odoo-erp gate):
    gh api 'repos/zbynekdrlik/odoo-erp/contents/scripts/handoff_gate/_gate.py?ref=develop' \
        --jq .content | base64 -d > <scratch>/_gate.py
    python3 scripts/extract_handoff_gate_validators.py \
        --src <scratch>/_gate.py \
        --out tests/fixtures/handoff_gate_pure.py \
        --provenance "develop <sha>, extracted <date>"
  The header records the source's blob sha (computed offline from the bytes).
  Then set PINNED_GATE_BLOB below to that sha in the SAME commit —
  tests/test_handoff_gate_shape_1125.py fails until fixture and pin agree.

DRIFT CHECK (#1125 — is odoo-erp's gate newer than the vendored fixture?):
    python3 scripts/extract_handoff_gate_validators.py --src <scratch>/_gate.py --check
  prints DRIFT + both blob shas and exits 1 when the source differs from
  PINNED_GATE_BLOB; exits 0 when they match. Offline once --src is fetched —
  the test suite never fetches GitHub.

What is NOT extracted (and thus NOT exercised offline): every git/gh/execution
check (merge-tree conflict, stale-base, addon-test execution evidence, E2E,
mutation probe, CI-red-at-HEAD, Source-verified). Those need the live gate on
the gk box — the contract test asserts only that the composer's FIELD/TABLE
shape passes the pure validators.
"""
from __future__ import annotations

import argparse
import ast
import hashlib

# #1125: the odoo-erp `scripts/handoff_gate/_gate.py` blob the vendored fixture
# was extracted from. Updated together with every regeneration; the
# fixture-freshness test asserts the fixture header records this same blob.
PINNED_GATE_BLOB = "2c016160b2bda7f8df923d406c88996ee70f0497"


def git_blob_sha(data: bytes) -> str:
    """The blob object id of ``data`` (== ``hash-object``, == the GitHub
    contents-API ``sha``) — computed offline."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

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


def check_drift(src_bytes: bytes) -> int:
    """Report whether ``src_bytes`` (a fetched odoo-erp ``_gate.py``) is the
    blob the fixture is pinned to. 0 = fresh, 1 = drift (regenerate)."""
    blob = git_blob_sha(src_bytes)
    if blob == PINNED_GATE_BLOB:
        print("OK: gate blob %s == pinned fixture blob" % blob[:12])
        return 0
    print("DRIFT: odoo-erp _gate.py is blob %s but the vendored fixture is "
          "pinned to %s — regenerate tests/fixtures/handoff_gate_pure.py and "
          "update PINNED_GATE_BLOB" % (blob[:12], PINNED_GATE_BLOB[:12]))
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="path to odoo-erp _gate.py")
    ap.add_argument("--out", help="output vendored module path")
    ap.add_argument("--provenance", default="(unspecified)",
                    help="develop sha/date recorded in the header after the "
                         "computed blob sha")
    ap.add_argument("--check", action="store_true",
                    help="drift check only: compare --src's blob with "
                         "PINNED_GATE_BLOB (exit 1 on drift), write nothing")
    args = ap.parse_args(argv)

    with open(args.src, "rb") as f:
        src_bytes = f.read()
    if args.check:
        return check_drift(src_bytes)
    if not args.out:
        ap.error("--out is required unless --check")
    segments = extract(src_bytes.decode("utf-8"))
    if len(segments) < len(WANT_ASSIGN) + len(WANT_FUNC):
        got = len(segments)
        want = len(WANT_ASSIGN) + len(WANT_FUNC)
        raise SystemExit(
            "extracted %d/%d symbols — the gate's shape changed; update "
            "WANT_ASSIGN/WANT_FUNC before regenerating" % (got, want))
    blob = git_blob_sha(src_bytes)
    provenance = "odoo-erp scripts/handoff_gate/_gate.py blob %s, %s" % (
        blob, args.provenance)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(HEADER.format(provenance=provenance))
        f.write("\n\n")
        f.write("\n\n\n".join(seg for _, seg in segments))
        f.write("\n")
    print("wrote %d symbols -> %s (gate blob %s)" % (
        len(segments), args.out, blob))
    if blob != PINNED_GATE_BLOB:
        print("NOTE: set PINNED_GATE_BLOB = %r in this script (same commit)"
              % blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
