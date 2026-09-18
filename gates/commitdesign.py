"""gates.commitdesign -- the design-before-code commit gate (entry for
block-commit-without-design.sh, #136 half 2/3).

Active ONLY inside an autopilot-worker subagent. Blocks a `git commit` that
references an issue with no DELIVERED design marker yet (the marker is written
solely by hooks/post-record-design-comment.sh). All the classification/marker/
merge-context/msgfile logic lives in gates.design; this module is the thin
orchestration + block-message layer the bash hook used to carry inline. Bypass:
`[no-design: <reason>]` (logged to no-design-skips.log via gates.audit); a bare
`[no-design]` is rejected. #187/#206/#310/#414 behaviour is preserved verbatim.
"""
import os
import re
import sys
import time

from gates import command_of, field_of, read_payload
from gates import audit
from gates import design as dg
from gates import ghread

_AUDIT_LOG = "no-design-skips.log"

_GIT_COMMIT_RE = re.compile(
    r'(^|[;&|]|&&)[ \t]*(sudo[ \t]+|env[ \t]+)?git[ \t]+commit\b', re.MULTILINE)
_BARE_NODESIGN_RE = re.compile(r'\[no-design\](\s|$)', re.MULTILINE)
_NODESIGN_REASON_RE = re.compile(r'\[no-design:\s*[^\]]+\]')


def _stderr(msg):
    sys.stderr.write(msg)


def _bare_nodesign_block(audit_log_path):
    _stderr(
        "\n"
        "🚫 BLOCKED: Bare [no-design] is not accepted.\n"
        "\n"
        "  Use [no-design: <reason>] explaining WHY the design comment cannot\n"
        "  be posted right now (the reason is logged to\n"
        "  %s).\n"
        "\n" % audit_log_path)
    sys.exit(2)


_BLOCK_TEMPLATE = """
🚫 BLOCKED: no design comment posted yet for {list} (repo {repo}).
{stale}{reject}
Per autonomous-batch-issue-development.md's design-before-code step, post
root cause + chosen approach + rejected alternative to the ticket BEFORE
this commit. #414 (SOTA architecture) additionally requires a `Triage:`
line (trivial / non-trivial — non-trivial needs 2-3 considered approaches
with trade-offs, not one) and an `Architektúra:` section (structure/
topology + the framework used, or an evidenced why-none-fits):

  gh issue comment <N> --body "<root cause> ... <chosen approach> ... <rejected alternative> ... Triage: trivial ... Architektúra: <structure> + <framework or why-none-fits> ..."

(or -F a body file). One honest paragraph is enough for a TRIVIAL scoped
fix — it just has to exist, and it has to come before this commit. Once it
posts, hooks/post-record-design-comment.sh records it automatically and
this commit will go through.

Bypass (rare, logged): add [no-design: <reason>] to this commit's message
— never for a real feature/fix with a genuine design decision behind it.
"""


# #1070 item 1 -- a READ failure (BOTH the REST and the GraphQL live read of
# the ticket's comments failed, typically the owner identity's hourly GraphQL
# exhaustion) is NOT a missing design. Fail-closed (still exit 2) but with an
# HONEST reason, never the words "no design" / "missing design".
_GATE_UNAVAILABLE_TEMPLATE = """
🚫 BLOCKED (gate-unavailable): could not read the design comment(s) for {list} (repo {repo}).
  {reason}

This is a READ FAILURE, not an ABSENT design — BOTH the REST and the GraphQL
read of the ticket's comments failed (typically the owner identity's hourly
GraphQL 5000/h exhaustion, or a transport error). The design may well be
present on the ticket. Retry once GitHub reads recover; the design-before-code
gate stays fail-closed rather than trust an unreadable thread.

Bypass (rare, logged): add [no-design: <reason>] to this commit's message.
"""


def main():
    payload = read_payload()
    cmd = command_of(payload)
    if not cmd:
        sys.exit(0)

    # Scope: autopilot-worker only.
    if field_of(payload, "agent_type", "") != "autopilot-worker":
        sys.exit(0)

    # Must be a real `git commit` invocation at a statement boundary.
    if not _GIT_COMMIT_RE.search(cmd):
        sys.exit(0)

    cwd = field_of(payload, "cwd", "")
    sid = field_of(payload, "session_id", "unknown") or "unknown"
    audit_log_path = audit.audit_log_path(_AUDIT_LOG)

    # Reject a bare bypass with no reason.
    if _BARE_NODESIGN_RE.search(cmd):
        _bare_nodesign_block(audit_log_path)

    # Honor a reasoned bypass, but log it (newline-flattened first).
    cmd_flat = cmd.replace("\n", " ")
    m = _NODESIGN_REASON_RE.search(cmd_flat)
    if m:
        audit.append_line(_AUDIT_LOG, "%s  session=%s  %s" % (
            audit.iso_now(), sid, m.group(0)))
        sys.exit(0)

    # notify (resolve_work_cwd / repo_name_for) is imported lazily so the gates
    # package itself stays free of a notify import at load time.
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        import notify
    except Exception:
        sys.exit(0)

    if not cwd:
        sys.exit(0)

    refs = dg.issue_refs(cmd)
    if not refs:
        sys.exit(0)

    # #187 -- an inline `cd /other/repo && git commit` moves the shell to a
    # DIFFERENT repo than the payload's static cwd.
    work_cwd = notify.resolve_work_cwd(cmd, cwd)

    # #1003 -- a resync merge commit introduces NO new design; exempt it.
    merge_ok, merge_reason = dg.is_merge_commit_context(cmd, work_cwd)
    if merge_ok:
        audit.append_line(_AUDIT_LOG, "%s  merge-commit exempt from design gate: %s (#1003)" % (
            time.strftime("%Y-%m-%dT%H:%M:%S%z"), merge_reason))
        sys.exit(0)

    repo_key = notify.repo_name_for(work_cwd)
    if not repo_key:
        sys.exit(0)          # unmeasurable (no origin) -> never guess, never block

    missing = [n for n in refs if not dg.marker_exists(repo_key, n)]
    # #206 -- drop any still-unmarked ref already CLOSED on GitHub.
    missing = dg.required_refs(missing, work_cwd)
    if not missing:
        sys.exit(0)

    # #1070 item 1 -- a ref with no LOCAL marker may still have a design LIVE on
    # the ticket: the marker is normally written by post-record-design-comment.sh
    # from a `gh issue view` (GraphQL) re-read, which comes back EMPTY under the
    # owner identity's hourly GraphQL 5000/h exhaustion, leaving a genuinely-
    # present main design markerless and hard-blocking the worker's FIRST commit
    # (odoo-erp #7120/#7293). Do a REST-first live design-presence read
    # (gates.ghread) for each still-missing ref: design found live -> drop it
    # (and write the marker so later commits skip the read); read
    # gate-unavailable (BOTH REST and GraphQL failed) -> the ticket is
    # UNVERIFIABLE, block with an HONEST reason, never "no design". Only fires
    # when a marker is MISSING; the common design-record path already wrote it,
    # so the happy path pays no gh call.
    # #1070 review 🟡 — the live-read presence bar is the FULL design bar
    # (cli_design_record.validate_body: design shape + Triage + Architektúra +
    # Shared-benefit), NOT the looser classify_design_comment alone. A real
    # main design (posted via design-record) passes validate_body, so it is
    # still found; a worker's minimal self-posted cause/approach stub does NOT,
    # so the commit-boundary fallback can't be used to self-unblock (the #871
    # main-authors-design invariant, whose primary enforcement is the dispatch
    # gate's `Design-by: main` check). validate_body reuses the SAME dg
    # classifiers, so no new gate logic.
    try:
        import cli_design_record as _cdr
        _is_full_design = lambda b: _cdr.validate_body(b)[0]  # noqa: E731
    except Exception:
        _is_full_design = lambda b: dg.classify_design_comment(b)[0]  # noqa: E731
    slug = ghread.resolve_slug(work_cwd)
    unavailable = []
    if slug:
        still = []
        for n in missing:
            bodies, err = ghread.read_comment_bodies(n, slug, cwd=work_cwd)
            if err:
                unavailable.append((n, err))
                continue
            if bodies and any(_is_full_design(b) for b in bodies):
                dg.write_marker(repo_key, n, "-", "live-ghread", kind="design")
                continue
            still.append(n)
        missing = still
    if unavailable:
        lst = " ".join("#%d" % n for n, _ in unavailable)
        _stderr(_GATE_UNAVAILABLE_TEMPLATE.format(
            list=lst, reason=unavailable[0][1], repo=repo_key))
        sys.exit(2)
    if not missing:
        sys.exit(0)

    # #414 -- surface WHAT the last posted comment was missing.
    reject_notes = []
    for n in missing:
        reason = dg.read_reject_reason(repo_key, n, kind="design")
        if reason:
            reject_notes.append("\n  #%d: %s" % (n, reason))

    # #310 -- quarantine every write-then-consume target THIS command touched,
    # now that the block prevents the whole compound from executing.
    stale_notes = []
    quarantined = []
    for p in dg.stale_msgfile_candidates(cmd):
        abs_p = p if os.path.isabs(p) else os.path.join(work_cwd, p)
        if dg.is_git_tracked(abs_p, work_cwd):
            continue
        dest = dg.quarantine_stale_msgfile(abs_p)
        if dest:
            quarantined.append((p, dest))
    if quarantined:
        dg.ensure_stale_pattern_excluded(work_cwd)
        for p, dest in quarantined:
            stale_notes.append("\n  %s -> %s" % (p, dest))

    list_str = " ".join("#%d" % n for n in missing)
    stale_section = ""
    if stale_notes:
        stale_section = (
            "\nLeftover scratch file(s) quarantined — stale content this SAME blocked\n"
            "command never got the chance to (re)write. A later bare `git commit -F`\n"
            "retry against the ORIGINAL path below will now fail loud (file not found)\n"
            "instead of silently committing the stale content:%s\n" % "".join(stale_notes))
    reject_section = ""
    if reject_notes:
        reject_section = (
            "\nYour last posted comment was rejected — here's specifically what's still\n"
            "missing:%s\n" % "".join(reject_notes))

    _stderr(_BLOCK_TEMPLATE.format(
        list=list_str, repo=repo_key, stale=stale_section, reject=reject_section))
    sys.exit(2)


if __name__ == "__main__":
    sys.exit(main())
