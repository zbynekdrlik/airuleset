# AUTO-EXTRACTED — DO NOT EDIT BY HAND.
#
# Pure body-shape validators vendored from odoo-erp
# scripts/handoff_gate/_gate.py for airuleset's hand-off composer contract
# test (#1044). Regenerate with scripts/extract_handoff_gate_validators.py.
#
# Provenance: odoo-erp scripts/handoff_gate/_gate.py blob 2c016160b2bda7f8df923d406c88996ee70f0497, develop 2c2fef52fc, extracted 2026-09-23
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


REQUIRED_FIELDS = ["branch", "head", "stack", "verified_at_utc", "harness"]


_FIELD_LABELS = {
    "branch": "Branch",
    "head": "HEAD",
    "stack": "Stack",
    "verified_at_utc": "Verified-at-UTC",
    "harness": "Harness",
    # #3829 — OPTIONAL, deliberately excluded from REQUIRED_FIELDS below.
    # Recognized by the same parsing machinery as the 5 required fields
    # (bullet/bold tolerance, multi-line continuation, stops the same
    # continuation scan as any other field label) so it behaves exactly
    # like its neighbours — see evaluate_readiness()'s no_code_reason
    # handling for what a non-empty value actually does.
    "no_code": "No-code",
    # #4023 — OPTIONAL escape hatch for the E2E-covered-surface check below,
    # same OPTIONAL contract as "no_code" (excluded from REQUIRED_FIELDS,
    # recognized by the same parsing machinery). It is DELIBERATELY its own
    # field label rather than a bullet inside Harness:, for the #3057 reason:
    # RUN_E2E_SHADOW_RE searches the parsed Harness: value with zero
    # negation-awareness, so an honest "did NOT run run-e2e-against-shadow.sh"
    # disclosure folded into that block would literally CONTAIN the script
    # name and falsely satisfy the check. Being its own label, it auto-joins
    # _ANY_FIELD_LABEL_LINE_RE below (so it STOPS, never continues, the
    # Harness: continuation scan), and it never collides with the `harness`
    # pattern because `Harness\**[ \t]*:` breaks on the `-` in
    # `Harness-e2e-exempt:`.
    "harness_e2e_exempt": "Harness-e2e-exempt",
    # #6253 — OPTIONAL in FIELD_PATTERNS (excluded from REQUIRED_FIELDS), but
    # enforced by shared_benefit_violations() with its own rollout grace
    # (SHARED_BENEFIT_REQUIRED_SINCE). Recognized here so
    # parse_readiness_fields() returns its value and _ANY_FIELD_LABEL_LINE_RE
    # stops continuation scans from neighbouring fields at the right place.
    "shared_benefit": "Shared-benefit",
    # #6377 batch 20 — OPTIONAL resync-invariant evidence HEAD: the commit at
    # which evidence (E2E, shadow, red-on-revert, click-test) was captured,
    # accepted as equivalent to HEAD when E is an ancestor and the branch's OWN
    # changed paths have no diff between E and HEAD (pure develop merges in
    # between do not invalidate evidence).
    "evidence_head": "Evidence-HEAD",
    # #6377 batch 24 — OPTIONAL, advisory. The stream records a local
    # --body-file dry-run PASS before posting. Format: "PASS @ <sha>".
    "gate_dry_run": "Gate-dry-run",
}


FIELD_PATTERNS = {
    # Tolerates both "**Label:** value" (colon inside the bold markers) and
    # "**Label**: value" (colon outside) — real-world markdown uses either.
    # #3050 defect 2: the remainder is now `(.*)$` (may be EMPTY) rather than
    # `(\S.*)$` — a label line with nothing after the colon is a legitimate
    # shape when the value continues on following bulleted/indented lines
    # (see _consume_continuation_lines() below); the old `\S` requirement
    # made such a field completely invisible to parse_readiness_fields().
    key: re.compile(
        r"(?im)^[ \t]*[-*]?[ \t]*\**"
        + re.escape(label)
        + r"\**[ \t]*:[ \t]*\**[ \t]*(.*)$"
    )
    for key, label in _FIELD_LABELS.items()
}


_ANY_FIELD_LABEL_LINE_RE = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*\**(?:"
    + "|".join(re.escape(label) for label in _FIELD_LABELS.values())
    + r")\**[ \t]*:"
)


_CONTINUATION_LINE_RE = re.compile(r"^(?:[ \t]*[-*][ \t]|[ \t]+\S)")


STACK_NOTE_LINE_RE = re.compile(r"(?im)^[ \t]*[-*]?[ \t]*\**Stack-note\**[ \t]*:.*$")


HEAD_SHA_RE = re.compile(r"[0-9a-fA-F]{8,40}")


TICKET_RE = re.compile(r"#(\d+)")


VERIFIED_AT_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$"
)


COMMIT_TRAILER_RE = re.compile(
    r"(?im)^(?:closes?|fixes?|resolves?)\s*:?\s*((?:#\d+[,\s]*)+)\s*$"
)


def _consume_continuation_lines(lines: list[str], start: int) -> list[str]:
    """From `lines[start:]`, collect immediately-following continuation
    lines (bullet or indented — see _CONTINUATION_LINE_RE) for a field
    value that started on the PRECEDING line (#3050 defect 2). One blank
    line is tolerated when the line right after it still continues the
    block; any other blank line, a recognized field-label line, or a plain
    (non-continuation) line ends the scan."""
    collected: list[str] = []
    j = start
    n = len(lines)
    while j < n:
        line = lines[j]
        if not line.strip():
            if (
                j + 1 < n
                and _CONTINUATION_LINE_RE.match(lines[j + 1])
                and not _ANY_FIELD_LABEL_LINE_RE.match(lines[j + 1])
            ):
                j += 1
                continue
            break
        if _ANY_FIELD_LABEL_LINE_RE.match(line):
            break
        if _CONTINUATION_LINE_RE.match(line):
            collected.append(line.strip())
            j += 1
            continue
        break
    return collected


def parse_readiness_fields(comment_body: str) -> dict[str, str]:
    """Extract every recognized template field present in the comment. A
    field absent from the comment is simply absent from the returned dict —
    callers decide what "missing" means (missing_fields() below).

    #3050 defect 2: a field's value is no longer required to be on the SAME
    line as its label — once a label line is found, immediately-following
    bulleted/indented continuation lines are folded into the value too (see
    _consume_continuation_lines()), so a `Harness:` block written as a
    label line followed by several `- ...` bullets is captured in full
    instead of being reported as entirely missing.

    #6377 batch 12 item (2): ``branch`` and ``head`` values are normalised
    via `_sanitize_markdown_wrapping` once here — downstream consumers get
    clean values directly.  The original (possibly wrapped) text is preserved
    in ``_branch_raw`` / ``_head_raw`` keys so the ``was_wrapped`` detection
    (batch 11 notes) stays available without a per-site re-call."""
    fields: dict[str, str] = {}
    lines = comment_body.splitlines()
    for key, pattern in FIELD_PATTERNS.items():
        for i, line in enumerate(lines):
            m = pattern.match(line)
            if not m:
                continue
            parts: list[str] = []
            same_line = m.group(1).strip()
            if same_line:
                parts.append(same_line)
            parts.extend(_consume_continuation_lines(lines, i + 1))
            if parts:
                fields[key] = " ".join(parts)
            break

    # #6377 batch 12 item (2): normalise Branch:/HEAD: once here.
    for fkey in ("branch", "head"):
        if fkey in fields:
            raw = fields[fkey]
            clean, was_wrapped = _sanitize_markdown_wrapping(raw)
            if was_wrapped:
                fields[f"_{fkey}_raw"] = raw
                fields[fkey] = clean

    return fields


def missing_fields(fields: dict[str, str]) -> list[str]:
    """Required template fields (#2978) that are absent or blank."""
    return [f for f in REQUIRED_FIELDS if not fields.get(f, "").strip()]


def _sanitize_markdown_wrapping(value: str) -> tuple[str, bool]:
    """Strip surrounding backticks and quotes from a field value.

    Returns (sanitized_value, was_wrapped). #6377 batch 11 item (a): a
    markdown-formatted ``Branch: `montalu/6376-kg-m-koeficient` `` is fetched
    literally with backticks → misleading 'couldn't find remote ref'. Same
    for HEAD: fields wrapped in backticks or quotes."""
    stripped = value.strip()
    for wrapper in ("`", "'", '"'):
        if (
            stripped.startswith(wrapper)
            and stripped.endswith(wrapper)
            and len(stripped) >= 2
        ):
            return stripped[1:-1].strip(), True
    return stripped, False


GK_REVIEW_LENSES = (
    "security",
    "correctness",
    "test-integrity",
    "evidence-integrity",
    "design-doctrine",
    "process",
)


_GK_REVIEW_LENS_SET = frozenset(GK_REVIEW_LENSES)


_ROOTCAUSE_LENS_ALT = "|".join(re.escape(x) for x in GK_REVIEW_LENSES)


SELF_REVIEW_ROOTCAUSE_LENS_RE = re.compile(
    rf"(?i)(?:`(?:{_ROOTCAUSE_LENS_ALT})`"  # a backticked lens id
    rf"|(?:{_ROOTCAUSE_LENS_ALT})[\s-]+lens\b"  # `<id> lens`
    rf"|lens[:\s-]+(?:{_ROOTCAUSE_LENS_ALT})\b)"  # `lens: <id>`
)


SELF_REVIEW_MARKER_RE = re.compile(r"(?im)^[ \t]*[-*]?[ \t]*\**Self-review\**[ \t]*:")


SELF_REVIEW_MODEL_RE = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*\**Self-review-model\**[ \t]*:[ \t]*\**[ \t]*([A-Za-z0-9._-]+)"
)


SELF_REVIEW_VERDICT_RE = re.compile(r"(\d+)\s*🔴\s*(\d+)\s*🟡\s*(\d+)\s*🔵")


SELF_REVIEW_LOCATOR_RE = re.compile(
    r"[\w./-]+\.\w+:\d+|\b(?:test|guard)_[A-Za-z0-9_]+\b"
)


SELF_REVIEW_ROOTCAUSE_RE = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*\**Root-cause-of-previous-bounce\**[ \t]*:[ \t]*\**[ \t]*(\S.*)$"
)


SELF_REVIEW_PREVENCIA_RE = re.compile(
    r"(?im)^[ \t]*[-*]?[ \t]*\**Prevencia \(stream\)\**[ \t]*:[ \t]*\**[ \t]*(\S.*)$"
)


def parse_self_review(comment_body: str) -> dict[str, tuple[str, str]]:
    """Extract the `Self-review:` table as {lens_id: (verdict_cell, evidence_cell)}.

    Fence-agnostic: scans EVERY line for a markdown pipe-row whose first cell
    (stripped of bullet/backtick/bold decoration, lowercased) is one of the 6
    GK_REVIEW_LENSES. The header row (`| lens | ... |`) and the `|---|` separator
    are skipped because their first cell is not a lens id. A row's verdict and
    evidence are its 2nd and 3rd cells (interior empties preserved, so an empty
    evidence cell is caught by the locator check rather than silently realigning
    the columns). First occurrence of each lens wins."""
    rows: dict[str, tuple[str, str]] = {}
    for line in comment_body.splitlines():
        if "|" not in line:
            continue
        parts = [c.strip() for c in line.split("|")]
        if parts and parts[0] == "":
            parts = parts[1:]
        if parts and parts[-1] == "":
            parts = parts[:-1]
        if len(parts) < 3:
            continue
        key = parts[0].strip("`* ").lower()
        if key in _GK_REVIEW_LENS_SET and key not in rows:
            rows[key] = (parts[1], parts[2])
    return rows


def self_review_violations(comment_body: str, bounce_round: int) -> list[str]:
    """#5935 — every reason the readiness comment's Self-review block is invalid.

    `bounce_round` is the round number of THIS hand-off (= prior prio:bounce
    events + 1); the CLI layer computes it, and the pure default (1) keeps every
    round-independent shape checkable offline. An EMPTY list means the block is
    present, every lens row is clean (0 🔴 0 🟡) with a real locator, the model
    tier is accepted (and `fable` when round >= 2), and — when round >= 3 — the
    root-cause + prevencia escalation lines are present."""
    reasons: list[str] = []
    # #5935 review 🔵a — REQUIRE the `Self-review:` marker (a stray lens-shaped
    # table with no marker no longer passes), and parse ONLY the table that
    # FOLLOWS the LAST marker, so a round-2 comment QUOTING round-1's stale table
    # does not get the OLD rows evaluated (first-occurrence-wins would otherwise
    # score the quoted table).
    markers = list(SELF_REVIEW_MARKER_RE.finditer(comment_body))
    if not markers:
        reasons.append(
            "MISSING Self-review block (#5935): the readiness comment must carry a "
            "`Self-review:` table with one row per gk-review lens (security / correctness / "
            "test-integrity / evidence-integrity / design-doctrine / process), each row "
            "`lens | N 🔴 N 🟡 N 🔵 | <file:line or test_* name>` — see "
            ".claude/rules/gk-review-lenses.md."
        )
    else:
        rows = parse_self_review(comment_body[markers[-1].start() :])
        for lens in GK_REVIEW_LENSES:
            if lens not in rows:
                reasons.append(
                    f"Self-review block missing lens '{lens}' (#5935) — every one of the 6 "
                    "gk-review lenses needs its own row."
                )
                continue
            verdict_cell, evidence_cell = rows[lens]
            m = SELF_REVIEW_VERDICT_RE.search(verdict_cell)
            if not m:
                reasons.append(
                    f"Self-review lens '{lens}': verdict '{verdict_cell.strip()}' is not the "
                    "required 'N 🔴 N 🟡 N 🔵' shape (#5935)."
                )
            else:
                red, yellow = int(m.group(1)), int(m.group(2))
                if red or yellow:
                    reasons.append(
                        f"Self-review lens '{lens}': {red} 🔴 {yellow} 🟡 open — fix before "
                        "hand-off (#5935); a 🔴/🟡 finding is never handed off with an open row."
                    )
            if not SELF_REVIEW_LOCATOR_RE.search(evidence_cell):
                reasons.append(
                    f"Self-review lens '{lens}': evidence '{evidence_cell.strip()}' carries no "
                    "`path:line` or test_*/guard_* locator (#5935) — an un-located row is treated "
                    "as NOT reviewed."
                )
    # #5935 coordinator verify 🔵3 — the `Self-review-model:` line is scored on
    # the FRESH self-review block only,
    # exactly as the table rows are (parse_self_review over markers[-1].start()
    # above). Otherwise a round-2 comment QUOTING round-1's stale
    # `Self-review-model: sonnet` line would be scored on the quoted line
    # (`.search()` returns the FIRST match). The fresh block spans from the EARLIER
    # of its own `Self-review-model:` line and its `Self-review:` marker — the model
    # line canonically sits ABOVE the marker (SELF_REVIEW_OK), so anchoring on the
    # marker alone would EXCLUDE the model line and mis-report it MISSING. min() of
    # the two last anchors covers the fresh block whichever order they appear in,
    # while a quoted stale block (entirely above both) is excluded. (A quoted line
    # blockquoted with `> ` is already skipped — the `>` defeats SELF_REVIEW_MODEL_RE
    # — so this only matters for a non-blockquoted verbatim paste.)
    _model_matches = list(SELF_REVIEW_MODEL_RE.finditer(comment_body))
    _anchors = [m.start() for m in (markers[-1:] if markers else [])]
    _anchors += [_model_matches[-1].start()] if _model_matches else []
    scoped_body = comment_body[min(_anchors) :] if _anchors else comment_body
    mm = SELF_REVIEW_MODEL_RE.search(scoped_body)
    if not mm:
        reasons.append(
            "MISSING Self-review-model: line (#5935/#6935) — name the exact model id from the "
            "airuleset fleet allowlist (airuleset.py model-tiers / MODEL_TIERS) that ran the "
            "adversarial self-review; the legacy alias `fable` is accepted."
        )
    else:
        # #6935: the accepted set is the LIVE airuleset fleet allowlist, never a
        # hard-coded copy. When airuleset is unreachable the loader returns None
        # and this sub-check is UNCHECKED (fail-safe — never a FAIL on an
        # import/subprocess failure); the MISSING-line check above still applies.
        tier = mm.group(1).strip().lower()
        _allowlist = fleet_model_allowlist()
        if _allowlist is not None:
            _accepted, _banned = _allowlist
            if tier not in _accepted:
                _accepted_str = ", ".join(sorted(_accepted))
                if tier in _banned:
                    reasons.append(
                        f"Self-review-model '{mm.group(1).strip()}' is a BANNED model id — "
                        f"not in the airuleset fleet model allowlist (#6935); accepted "
                        f"(exact ids from airuleset): {_accepted_str}."
                    )
                else:
                    reasons.append(
                        f"Self-review-model '{mm.group(1).strip()}' is not in the airuleset "
                        f"fleet model allowlist (#6935) — accepted (exact ids from airuleset): "
                        f"{_accepted_str}."
                    )
    if bounce_round >= 3:
        rc = SELF_REVIEW_ROOTCAUSE_RE.search(comment_body)
        if not rc:
            reasons.append(
                f"MISSING Root-cause-of-previous-bounce: (#5935) — a round-{bounce_round} "
                "re-hand-off must state why the prior self-review missed the bounced finding, "
                "naming a lens id."
            )
        elif not SELF_REVIEW_ROOTCAUSE_LENS_RE.search(rc.group(1)):
            reasons.append(
                f"Root-cause-of-previous-bounce: (#5935) must NAME a lens id — backticked "
                "(`process`) or adjacent to the word 'lens' (`correctness lens`, `lens: security`) — "
                "not as a bare English word "
                f"(security / correctness / test-integrity / evidence-integrity / design-doctrine "
                f"/ process); got '{rc.group(1).strip()[:80]}'."
            )
        if not SELF_REVIEW_PREVENCIA_RE.search(comment_body):
            reasons.append(
                f"MISSING Prevencia (stream): (#5935) — a round-{bounce_round} re-hand-off must "
                "state what the stream changed in its OWN process so the class cannot recur."
            )
    return reasons
