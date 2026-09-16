"""Template-aware handoff comment renderer (#969).

The handoff CLI (``cmd_handoff`` in ``airuleset.py``) posts a READY-FOR-REVIEW
comment whose shape depends on the TARGET repo: repos with
``.claude/rules/subdev-handoff-comment.md`` (the "extended template" — odoo-erp)
need ``Branch: <owner>:<branch>``, ``Stack:``, ``Harness:``, ``Shared-benefit:``
and optional ``Tested-tree:``, ``Evidence-HEAD:``, ``Tenant-scope:``,
``Source-verified:``; repos WITHOUT that file get the existing generic shape.

Exposed API:
    ``has_extended_template(repo)`` — probe the target repo for the template.
    ``derive_branch_field(branch, repo, origin_url)`` — ``owner:branch`` or
        plain ``branch`` depending on whether the local checkout is a fork.
    ``render_extended_body(...)`` — compose the full comment body for a
        template-aware repo.
    ``render_generic_body(...)`` — compose the original generic comment body.
"""
from __future__ import annotations

import re
import subprocess
from typing import Optional

# --- #1044: pass-through markers -------------------------------------------
# The composer OWNS none of the odoo-erp gate's body shape (it evolves across
# 10+ checks_* modules and requires execution evidence the composer cannot
# synthesise). For a rich hand-off the STREAM authors the full gate-compliant
# body and the composer SIGNS + POSTS it verbatim (the `--body-file` path in
# cmd_handoff). These regexes validate only the minimal cross-repo invariants
# the composer is responsible for — the SAME ones cmd_handoff's --sign-only
# path already checks — never the full gate shape (the repo gate is the
# authority on that).
_RFR_MARKER_RE = re.compile(r'^\s*([#*_-]+\s*)?READY-FOR-REVIEW', re.MULTILINE)
_CFR_MARKER_RE = re.compile(
    r'Ready for gatekeeper cross-fork review[.!]?\s*$', re.MULTILINE)
_SELF_REVIEW_MODEL_LINE_RE = re.compile(
    r'(?im)^[ \t]*[-*]?[ \t]*\**Self-review-model\**[ \t]*:')
_ROOTCAUSE_LINE_RE = re.compile(
    r'(?im)^[ \t]*[-*]?[ \t]*\**Root-cause-of-previous-bounce\**[ \t]*:')
_PREVENCIA_LINE_RE = re.compile(
    r'(?im)^[ \t]*[-*]?[ \t]*\**Prevencia-read\**[ \t]*:')
# Line-anchored template field labels (bullet/bold tolerant, mirroring the
# gate's own FIELD_PATTERNS) — used to detect a FULL body wrongly passed as
# the Self-review table.
_FULL_BODY_FIELD_LABEL_RES = tuple(
    re.compile(r'(?im)^[ \t]*[-*]?[ \t]*\**' + lbl + r'\**[ \t]*:')
    for lbl in ("Branch", "HEAD", "Stack", "Verified-at-UTC", "Harness")
)


def _has_rfr_marker(text: str) -> bool:
    """True if ``text`` carries the READY-FOR-REVIEW / cross-fork trigger."""
    return bool(_RFR_MARKER_RE.search(text) or _CFR_MARKER_RE.search(text))


def is_full_body(table_text: str) -> bool:
    """True if ``table_text`` looks like a FULL readiness body rather than a
    bare Self-review table (#1044).

    A legitimate ``--self-review-file`` is only the markdown lens table (every
    row starts with ``|``); a stream with evidence to carry has no clean channel
    and wrongly pastes its whole gate-compliant body here, which the field
    renderer would then WRAP inside the Self-review block and emit a SECOND,
    duplicate ``HEAD:`` line the gate parses instead of the composer's fresh
    one. Detect that misuse so compose_body can refuse it and point at the
    verbatim ``--body-file`` path.

    Signal: a READY-FOR-REVIEW marker, OR >= 2 line-anchored template field
    labels (a real table has zero — its cells live on ``|``-delimited lines)."""
    if not table_text:
        return False
    if _has_rfr_marker(table_text):
        return True
    hits = sum(1 for rx in _FULL_BODY_FIELD_LABEL_RES if rx.search(table_text))
    return hits >= 2


def validate_passthrough_body(
    body: str, *, bounce_round: int = 1
) -> Optional[str]:
    """Validate a STREAM-authored, gate-compliant body for the minimal
    cross-repo invariants the composer is responsible for, before it is signed
    and posted VERBATIM via the ``--body-file`` path (#1044).

    Returns an error message, or None when the body may be posted. The body
    shape itself (Branch/HEAD/Stack/… + the Self-review table + evidence
    fences) is the REPO gate's authority — this only enforces what the
    composer already enforces for ``--sign-only``: the RFR marker, the
    ``Self-review-model:`` line, and (round >= 2) the bounce escalation
    fields. It does NOT rewrite the body — fences and the single ``HEAD:``
    line pass through untouched."""
    if not (body or "").strip():
        return "handoff BLOCK: --body-file body is empty"
    if not _has_rfr_marker(body):
        return ("handoff BLOCK: --body-file body has no READY-FOR-REVIEW "
                "marker")
    if not _SELF_REVIEW_MODEL_LINE_RE.search(body):
        return ("handoff BLOCK: --body-file body missing Self-review-model: "
                "line (required on every readiness comment)")
    if bounce_round >= 2:
        if not _ROOTCAUSE_LINE_RE.search(body):
            return ("handoff BLOCK: round %d --body-file body missing "
                    "Root-cause-of-previous-bounce:" % bounce_round)
        if not _PREVENCIA_LINE_RE.search(body):
            return ("handoff BLOCK: round %d --body-file body missing "
                    "Prevencia-read:" % bounce_round)
    return None


def _run(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except Exception as e:
        return subprocess.CompletedProcess(argv, 1, "", str(e))


def has_extended_template(repo: str, *, runner=None) -> bool:
    """True if ``repo`` has ``.claude/rules/subdev-handoff-comment.md``.

    Uses ``gh api`` to probe the file's existence (HTTP 200 vs 404).
    Returns False on any error (fail-safe: use generic template).

    ``runner`` is injectable for tests — ``runner(path)`` returns the
    raw ``gh api`` stdout or raises.
    """
    path = "repos/%s/contents/.claude/rules/subdev-handoff-comment.md" % repo
    if runner:
        try:
            result = runner(path)
            return bool(result)
        except Exception:
            return False
    r = _run(["gh", "api", path, "-q", ".sha"])
    return r.returncode == 0 and bool((r.stdout or "").strip())


def _parse_origin_slug(url: str) -> Optional[str]:
    """owner/name from a git remote URL, or None.

    Replicates airuleset._parse_origin_slug (pure + testable).
    """
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None
    if u.endswith(".git"):
        u = u[:-4]
    parts = [p for p in u.replace(":", "/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    if not owner or not name:
        return None
    return "%s/%s" % (owner, name)


def derive_branch_field(
    branch: str,
    target_repo: str,
    origin_url: Optional[str] = None,
) -> str:
    """Return the ``Branch:`` field value.

    If the local checkout's owner (from ``origin_url``) differs from
    ``target_repo``'s owner, return ``<local_owner>:<branch>`` (fork
    shorthand); otherwise return plain ``<branch>``.

    When ``origin_url`` is None, falls back to ``git config --get
    remote.origin.url`` from the cwd.
    """
    if origin_url is None:
        r = _run(["git", "config", "--get", "remote.origin.url"])
        origin_url = (r.stdout or "").strip() if r.returncode == 0 else ""

    local_slug = _parse_origin_slug(origin_url)
    if not local_slug:
        # Cannot determine local owner — return plain branch.
        return branch

    local_owner = local_slug.split("/")[0]
    target_owner = target_repo.split("/")[0] if "/" in target_repo else ""

    if local_owner and target_owner and local_owner != target_owner:
        return "%s:%s" % (local_owner, branch)
    return branch


def render_extended_body(
    *,
    branch_field: str,
    head_sha: str,
    verified_at_utc: str,
    stack: str,
    harness: str,
    shared_benefit: str,
    self_review_table: str,
    bounce_round: int,
    # Optional fields — passed through when given.
    tested_tree: Optional[str] = None,
    evidence_head: Optional[str] = None,
    tenant_scope: Optional[str] = None,
    source_verified: Optional[str] = None,
    # Bounce-specific fields.
    root_cause: Optional[str] = None,
    prevencia_read: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
    # Which exact model performed the fresh-context self-review (#991). A
    # FACT, not tiering doctrine — the odoo-erp gate requires it as the
    # self-review evidence line. Validated to an exact MODEL_TIERS id by the
    # CLI (cmd_handoff); required there, so it is always present in practice.
    self_review_model: Optional[str] = None,
) -> str:
    """Compose a full READY-FOR-REVIEW comment body for a repo with the
    extended template (odoo-erp shape).

    Field order follows the ``subdev-handoff-comment.md`` template:
    READY-FOR-REVIEW header, Self-review-model, Self-review block, Branch,
    HEAD, Stack, Verified-at-UTC, Harness, then optional/conditional fields.
    """
    parts: list[str] = []

    # Header.
    parts.append("READY-FOR-REVIEW: branch %s" % branch_field)
    parts.append("")

    # Self-review-model: the model that performed the fresh-context
    # self-review (#991) — goes BEFORE the Self-review block.
    if self_review_model:
        parts.append("Self-review-model: %s" % self_review_model)

    # Self-review block.
    parts.append("**Self-review:**")
    parts.append("")
    parts.append(self_review_table.strip())
    parts.append("")

    # Required template fields.
    parts.append("Branch: %s" % branch_field)
    parts.append("HEAD: %s" % head_sha)
    parts.append("Stack: %s" % stack)
    parts.append("Verified-at-UTC: %s" % verified_at_utc)
    parts.append("Harness: %s" % harness)

    # Optional template fields — emit when given.
    if tested_tree:
        parts.append("Tested-tree: %s" % tested_tree)
    if evidence_head:
        parts.append("Evidence-HEAD: %s" % evidence_head)
    if tenant_scope:
        parts.append("Tenant-scope: %s" % tenant_scope)
    # shared_benefit is unconditionally required (validated upstream).
    parts.append("Shared-benefit: %s" % shared_benefit)
    if source_verified:
        parts.append("Source-verified: %s" % source_verified)

    # Bounce-specific fields.
    if bounce_round >= 2:
        if root_cause:
            parts.append("Root-cause-of-previous-bounce: %s" % root_cause)
        if prevencia_read:
            parts.append("Prevencia-read: %s" % prevencia_read)

    for cf in (closes_finding or []):
        parts.append("Closes-finding: %s" % cf)

    # Bounce-round: emit ONLY when > 1 (first hand-off = round 1, no bounce
    # has occurred — emitting "Bounce-round: 1" trips the gate's
    # BRANCH-ALREADY-INTEGRATED heuristic; #969).
    if bounce_round > 1:
        parts.append("Bounce-round: %d" % bounce_round)

    return "\n".join(parts) + "\n"


def render_generic_body(
    *,
    branch: str,
    head_sha: str,
    verified_at_utc: str,
    self_review_table: str,
    bounce_round: int,
    root_cause: Optional[str] = None,
    prevencia_read: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
    self_review_model: Optional[str] = None,
) -> str:
    """Compose the original generic READY-FOR-REVIEW comment body.

    This is the pre-#969 shape, preserved for repos without the extended
    template.
    """
    parts: list[str] = []

    parts.append("READY-FOR-REVIEW: branch %s" % branch)
    parts.append("")
    if self_review_model:
        parts.append("Self-review-model: %s" % self_review_model)
    parts.append("**Self-review:**")
    parts.append("")
    parts.append(self_review_table.strip())
    parts.append("")
    parts.append("Verified-at-UTC: %s" % verified_at_utc)
    parts.append("HEAD: %s" % head_sha)

    if bounce_round >= 2:
        if root_cause:
            parts.append("Root-cause-of-previous-bounce: %s" % root_cause)
        if prevencia_read:
            parts.append("Prevencia-read: %s" % prevencia_read)

    for cf in (closes_finding or []):
        parts.append("Closes-finding: %s" % cf)

    # Same fix: omit Bounce-round when <= 1 (#969).
    if bounce_round > 1:
        parts.append("Bounce-round: %d" % bounce_round)

    return "\n".join(parts) + "\n"


def validate_extended_flags(
    *,
    stack: Optional[str],
    harness: Optional[str],
    shared_benefit: Optional[str],
) -> Optional[str]:
    """Return an error message if any unconditionally-required extended
    template field is missing, or None if all present."""
    missing = []
    if not (stack or "").strip():
        missing.append("--stack")
    if not (harness or "").strip():
        missing.append("--harness")
    if not (shared_benefit or "").strip():
        missing.append("--shared-benefit")
    if missing:
        return ("handoff BLOCK: repo has extended template — missing "
                "required flags: %s" % ", ".join(missing))
    return None


def compose_body(
    *,
    repo: str,
    branch: str,
    head_sha: str,
    verified_at_utc: str,
    self_review_table: str,
    bounce_round: int,
    stack: Optional[str] = None,
    harness: Optional[str] = None,
    shared_benefit: Optional[str] = None,
    tenant_scope: Optional[str] = None,
    source_verified: Optional[str] = None,
    tested_tree: Optional[str] = None,
    evidence_head: Optional[str] = None,
    root_cause: Optional[str] = None,
    prevencia_read: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
    self_review_model: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """Compose the comment body, choosing extended or generic shape.

    Returns ``(body, error)``. On error, ``body`` is empty and ``error``
    is the message to print; on success, ``error`` is None.
    """
    # #1044: refuse a FULL readiness body wrongly passed as the Self-review
    # table. Wrapping it inside the Self-review block emits a SECOND, duplicate
    # HEAD: line that the gate parses instead of the composer's fresh one (the
    # mangling defect). A rich hand-off body belongs on the verbatim
    # --body-file path, which signs + posts it 1:1 with fences and HEAD intact.
    if is_full_body(self_review_table):
        return ("", "handoff BLOCK: --self-review-file looks like a FULL "
                "readiness body (it carries a READY-FOR-REVIEW marker or "
                "template field labels), not a bare Self-review table. The "
                "composer would wrap it and emit a duplicate HEAD: line the "
                "gate then parses. Author the complete gate-compliant body and "
                "post it verbatim with `airuleset.py handoff --body-file "
                "<body.md>` instead.")

    # Y1 review finding (#969): when any extended flag is explicitly supplied,
    # treat the intent as "extended" even if the probe fails — silently
    # falling back to generic would post a body the gate rejects.
    has_extended_flags = any((stack, harness, shared_benefit))
    use_extended = has_extended_template(repo)
    if not use_extended and has_extended_flags:
        use_extended = True  # caller intent overrides a failed probe
    if use_extended:
        err = validate_extended_flags(
            stack=stack, harness=harness, shared_benefit=shared_benefit)
        if err:
            return ("", err)
        branch_field = derive_branch_field(branch, repo)
        body = render_extended_body(
            branch_field=branch_field, head_sha=head_sha,
            verified_at_utc=verified_at_utc, stack=stack, harness=harness,
            shared_benefit=shared_benefit, self_review_table=self_review_table,
            bounce_round=bounce_round, tested_tree=tested_tree,
            evidence_head=evidence_head, tenant_scope=tenant_scope,
            source_verified=source_verified, root_cause=root_cause,
            prevencia_read=prevencia_read,
            closes_finding=closes_finding,
            self_review_model=self_review_model,
        )
    else:
        body = render_generic_body(
            branch=branch, head_sha=head_sha,
            verified_at_utc=verified_at_utc,
            self_review_table=self_review_table, bounce_round=bounce_round,
            root_cause=root_cause, prevencia_read=prevencia_read,
            closes_finding=closes_finding,
            self_review_model=self_review_model,
        )
    return (body, None)
