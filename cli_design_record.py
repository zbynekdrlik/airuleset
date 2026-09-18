"""cli_design_record -- `airuleset.py design-record`: the Fable MAIN session
posts a ticket's design comment with this command (#1061, owner escalation
2026-09-17).

Flow: the main authors the design (root cause traced in code + chosen approach +
rejected alternative + `Triage:` + `Architektúra:` + `Shared-benefit:`) into a
body file; this command
  1. VALIDATES the design shape (the SAME classifiers post-record-design-comment
     applies, so a comment that would not gate is refused BEFORE it is posted),
  2. APPENDS a truthful `Design-by: <role> <model>` line read from the session's
     OWN transcript via cli_authorship (never a self-declared string -- from the
     main checkout it reads the Fable id, from a lane worktree the worker's id),
  3. POSTS it with `gh issue comment`, and
  4. WRITES the local design marker (`design_gate.write_marker`) so the worker's
     commit gate (`gates.commitdesign`) passes -- the marker is normally written
     by the PostToolUse `post-record-design-comment.sh` hook, but that hook keys
     on a literal `gh issue comment` Bash call and would not fire on this command's
     own `airuleset.py design-record` invocation, so design-record writes it here.

The dispatch precondition (`gates.designdispatch`) then refuses an
autopilot-worker dispatch whose newest design comment is not
`Design-by: main <Fable id>`.
"""
import os
import subprocess
import sys
import time

import cli_authorship

DESIGN_STEM = "Design"


def design_record_help_template():
    """#1070 item 5 -- the full design-body SECTION TEMPLATE, printed as the
    `design-record --help` epilog so the required sections are discoverable in
    ONE place instead of by three failed `design-record` attempts (#1079). The
    tokens here match exactly what `validate_body` (and the design_gate
    classifiers) require, so a body built from this template passes the gate on
    the first try."""
    return (
        "Design body template (the gate requires every section below):\n"
        "\n"
        "  Triage: trivial | non-trivial\n"
        "      (a TRIVIAL scoped fix needs one honest paragraph; a NON-TRIVIAL\n"
        "       ticket needs the numbered Approaches + Trade-off comparison\n"
        "       + Architektúra section below.)\n"
        "\n"
        "  ## Root cause\n"
        "      <the cause traced in the CODE, not the symptom restated>\n"
        "\n"
        "  ## Approaches   (non-trivial only)\n"
        "      Approach 1 (chosen) — <what + why>\n"
        "      Approach 2 — <the rejected alternative> — Rejected: <why>\n"
        "      Approach 3 — <optional third> — Rejected: <why>\n"
        "      Trade-off comparison: <cost/benefit of 1 vs 2 (vs 3)>\n"
        "\n"
        "  Architektúra: <structure/topology> + <framework used, OR an\n"
        "      evidenced why-none-fits from an actually-read source>\n"
        "\n"
        "  Shared-benefit: <who beyond the requester this helps, or an\n"
        "      explicit single-client disposition>\n"
        "\n"
        "  Design-by: main <model>   (appended AUTOMATICALLY from the session's\n"
        "      own transcript — do NOT hand-type it; a worktree stamps worker)\n")


def _log_stamp(stamp, cwd, issue, url):
    """Append every design-record post to ~/.claude/design-by-gate.log (the same
    log the gates use) — cwd + stamp + url. Auditability for the cwd-spoof
    residual (#1061 review): design-record derives role/model from os.getcwd(),
    so a worker that `cd`s into the main checkout could stamp `Design-by: main`;
    the dispatch gate stays the AUTHORITY, but this log lets a reconcile see
    WHERE each Design-by: main was actually posted from. Never raises."""
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude",
                            "design-by-gate.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\tDESIGN-RECORD\t%s\t#%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                cwd or "", issue, stamp, url or "-"))
    except OSError:
        return


def _log_marker_error(repo_key, issue, exc):
    """Best-effort diagnostic for a marker-write failure (the post already
    landed; the local marker is only a convenience for the worker's commit
    gate). Never raises -- an unwritable ~/.claude just leaves no line."""
    try:
        path = os.path.join(os.path.expanduser("~"), ".claude",
                            "design-gate-errors.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s\tdesign-record-marker\t%s#%s\t%r\n"
                     % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        repo_key, issue, exc))
    except OSError:
        return


def compose_body(raw_body, cwd, projects_dir=None, home=None):
    """`raw_body` with a truthful `Design-by: <role> <model>` footer appended.
    Idempotent: any pre-existing `Design-by:` line is stripped first, so a
    re-post never carries two stamps (and a hand-typed `Design-by: main` from a
    worker is replaced by the truthful worker stamp)."""
    import re
    stamp = cli_authorship.stamp_line(DESIGN_STEM, cwd, projects_dir=projects_dir,
                                      home=home)
    kept = [ln for ln in (raw_body or "").splitlines()
            if not re.match(r"^\s*(?:[-*>]\s*)?\**\s*Design-?by\s*:", ln,
                            re.IGNORECASE)]
    body = "\n".join(kept).rstrip()
    return "%s\n\n%s\n" % (body, stamp)


def validate_body(raw_body):
    """(ok, reasons) -- does `raw_body` carry a gate-valid design? Applies the
    SAME acceptance post-record-design-comment.sh uses for the "design" kind:
    the design shape (root cause + approach + rejected alternative), a `Triage:`
    line, an `Architektúra:` section (only when non-trivial, #428), and a
    `Shared-benefit:` line (#877, unconditional). Returns every failing reason
    so the caller surfaces them all at once, never a multi-round discovery."""
    from gates import design as dg
    reasons = []
    ok_design, r_design = dg.classify_design_comment(raw_body)
    if not ok_design:
        reasons.append("design shape: " + r_design)
    triage_ok, r_triage = dg.classify_triage_and_approaches(raw_body)
    if not triage_ok:
        reasons.append("triage/approaches: " + r_triage)
    is_trivial = triage_ok and dg.triage_class(raw_body) == "trivial"
    if not is_trivial:
        arch_ok, r_arch = dg.classify_architecture_section(raw_body)
        if not arch_ok:
            reasons.append("architecture: " + r_arch)
    sb_ok, r_sb = dg.classify_shared_benefit(raw_body)
    if not sb_ok:
        reasons.append("shared-benefit: " + r_sb)
    return (not reasons), reasons


def _default_runner(argv, body):
    """(returncode, stdout, stderr) for a `gh` invocation whose body is fed on
    STDIN. Uses airuleset._gh_env() for the fleet's per-command token
    resolution (a stream box has no GH_TOKEN in its shell env)."""
    env = None
    try:
        import airuleset
        env = airuleset._gh_env()
    except Exception as exc:  # airuleset import / env-resolution failure
        _log_marker_error("_gh_env", "-", exc)
        env = None
    try:
        r = subprocess.run(argv, input=body, capture_output=True, text=True,
                           timeout=30, env=env)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return 1, "", str(e)


def _repo_key(repo, cwd):
    if repo:
        return repo.rstrip("/").split("/")[-1]
    try:
        import notify
        return notify.repo_name_for(cwd or os.getcwd())
    except Exception as exc:
        _log_marker_error("repo_name_for", "-", exc)
        return ""


def post_and_record(issue, repo, raw_body, cwd, runner=None, projects_dir=None,
                    home=None):
    """Validate -> compose (stamp) -> post -> write marker.

    Returns (True, comment_url, stamp_line) on success, or (False, reason, None)
    when the design is invalid or the post fails. `runner(argv, body)` ->
    (rc, stdout, stderr) is injected in tests; production uses `_default_runner`.
    The design marker is written ONLY after a confirmed post (never
    speculatively), the same #135 lesson post-record-design-comment.sh encodes."""
    ok, reasons = validate_body(raw_body)
    if not ok:
        return False, "invalid design: " + "; ".join(reasons), None
    # #1061 fix-forward: never post a `Design-by: <role> unknown` stamp -- the
    # dispatch gate (gates.designdispatch) would then REFUSE a legitimately
    # main-authored design (the deploy defect). An unreadable model is a
    # transient read (a busy parallel-tool turn / an api-error tail), so refuse
    # and let the caller retry once the session has emitted a real assistant turn.
    model = cli_authorship.session_model(cwd, projects_dir=projects_dir,
                                         home=home)
    if model == cli_authorship.UNKNOWN_MODEL:
        return (False,
                "authorship model unknown: the session transcript has no "
                "readable assistant model right now (a busy parallel-tool turn "
                "or an api-error tail) -- refusing to post 'Design-by: <role> "
                "unknown', which the dispatch gate would reject; retry once the "
                "session has emitted a real assistant turn", None)
    body = compose_body(raw_body, cwd, projects_dir=projects_dir, home=home)
    stamp = cli_authorship.stamp_line(DESIGN_STEM, cwd, projects_dir=projects_dir,
                                      home=home)
    argv = ["gh", "issue", "comment", str(issue)]
    if repo:
        argv += ["-R", repo]
    argv += ["-F", "-"]
    run = runner or _default_runner
    rc, out, err = run(argv, body)
    if rc != 0:
        return False, "gh issue comment failed: %s" % (err or out or "rc=%d" % rc), None
    url = out.strip()
    _log_stamp(stamp, cwd, issue, url)
    repo_key = _repo_key(repo, cwd)
    if repo_key:
        try:
            from gates import design as dg
            dg.write_marker(repo_key, issue, url, "design-record", kind="design")
        except Exception as exc:
            _log_marker_error(repo_key, issue, exc)
    return True, url, stamp


def cmd_design_record(args):
    issue = getattr(args, "issue", None)
    repo = getattr(args, "repo", None)
    body_file = getattr(args, "body_file", None)
    dry_run = getattr(args, "dry_run", False)
    if not issue:
        print("design-record: --issue is required")
        return 1
    if not body_file:
        print("design-record: --body-file is required")
        return 1
    try:
        with open(body_file, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as e:
        print("design-record BLOCK: cannot read --body-file: %s" % e)
        return 1
    if not raw.strip():
        print("design-record BLOCK: --body-file is empty")
        return 1
    cwd = os.getcwd()
    ok, reasons = validate_body(raw)
    if not ok:
        print("design-record BLOCK: the design comment would not pass the gate:")
        for r in reasons:
            print("  - %s" % r)
        print("Fix the design body and re-run; nothing was posted.")
        return 1
    # #1061 fix-forward: refuse the unknown-model stamp on BOTH the real post
    # AND the --dry-run preview (post_and_record also guards the real path),
    # so a dry-run never previews a `Design-by: <role> unknown` the dispatch
    # gate would reject.
    if cli_authorship.session_model(cwd) == cli_authorship.UNKNOWN_MODEL:
        print("design-record BLOCK: the session model read as 'unknown' "
              "(a busy parallel-tool turn or an api-error tail) -- refusing to "
              "stamp 'Design-by: <role> unknown', which the dispatch gate would "
              "reject. Retry once the session has emitted a real assistant "
              "turn; nothing was posted.")
        return 1
    if dry_run:
        body = compose_body(raw, cwd)
        stamp = cli_authorship.stamp_line(DESIGN_STEM, cwd)
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
        print("[dry-run] would post to #%s with stamp: %s" % (issue, stamp))
        return 0
    ok2, url_or_reason, stamp = post_and_record(issue, repo, raw, cwd)
    if not ok2:
        print("design-record FAILED: %s" % url_or_reason)
        return 1
    print("design-record: posted %s on #%s (%s)" % (url_or_reason, issue, stamp))
    return 0
