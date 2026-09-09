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

import subprocess
from typing import Optional


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
    reviewed_by_tier: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
) -> str:
    """Compose a full READY-FOR-REVIEW comment body for a repo with the
    extended template (odoo-erp shape).

    Field order follows the ``subdev-handoff-comment.md`` template:
    READY-FOR-REVIEW header, Self-review block, Branch, HEAD, Stack,
    Verified-at-UTC, Harness, then optional/conditional fields.
    """
    parts: list[str] = []

    # Header.
    parts.append("READY-FOR-REVIEW: branch %s" % branch_field)
    parts.append("")

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
    if shared_benefit:
        parts.append("Shared-benefit: %s" % shared_benefit)
    if source_verified:
        parts.append("Source-verified: %s" % source_verified)

    # Bounce-specific fields.
    if bounce_round >= 2:
        if root_cause:
            parts.append("Root-cause-of-previous-bounce: %s" % root_cause)
        if prevencia_read:
            parts.append("Prevencia-read: %s" % prevencia_read)
        if reviewed_by_tier:
            parts.append("Reviewed-by-tier: %s" % reviewed_by_tier)

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
    reviewed_by_tier: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
) -> str:
    """Compose the original generic READY-FOR-REVIEW comment body.

    This is the pre-#969 shape, preserved for repos without the extended
    template.
    """
    parts: list[str] = []

    parts.append("READY-FOR-REVIEW: branch %s" % branch)
    parts.append("")
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
        if reviewed_by_tier:
            parts.append("Reviewed-by-tier: %s" % reviewed_by_tier)

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
    reviewed_by_tier: Optional[str] = None,
    closes_finding: Optional[list[str]] = None,
) -> tuple[str, Optional[str]]:
    """Compose the comment body, choosing extended or generic shape.

    Returns ``(body, error)``. On error, ``body`` is empty and ``error``
    is the message to print; on success, ``error`` is None.
    """
    use_extended = has_extended_template(repo)
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
            prevencia_read=prevencia_read, reviewed_by_tier=reviewed_by_tier,
            closes_finding=closes_finding,
        )
    else:
        body = render_generic_body(
            branch=branch, head_sha=head_sha,
            verified_at_utc=verified_at_utc,
            self_review_table=self_review_table, bounce_round=bounce_round,
            root_cause=root_cause, prevencia_read=prevencia_read,
            reviewed_by_tier=reviewed_by_tier,
            closes_finding=closes_finding,
        )
    return (body, None)
