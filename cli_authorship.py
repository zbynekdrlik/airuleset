"""cli_authorship -- truthful `Design-by:` / `Reviewed-by:` authorship stamps
(#1061, owner escalation 2026-09-17).

The owner's standing rule (#871): the DESIGN and the REVIEW of every ticket are
done by the Fable MAIN session; the weaker model only IMPLEMENTS a decided
design. This module reads the invoking session's OWN model from its live
transcript jsonl -- NEVER a self-declared string a weaker worker could type --
plus its main/worker ROLE from the cwd, and composes the stamp line the
`design-record` poster and the hand-off `Reviewed-by:` line carry. The dispatch
precondition (`gates.designdispatch`) then refuses an `autopilot-worker` dispatch
whose newest design comment is not `Design-by: main <Fable id>`.

Dependency-light on purpose (only `watchdog.transcripts` + stdlib): it is called
from a PreToolUse gate on every Agent dispatch, so it must never drag airuleset's
whole import graph in. The Fable-id MATCH lives in the gate, not here.
"""
import os
import re
from pathlib import Path

# The isolated-lane worktree segment -- the SAME predicate `_is_worktree_repo_dir`
# (#972) and `close_trigger.is_worker_context` (#564) key on.
WORKTREE_MARKER = "/.claude/worktrees/"

UNKNOWN_MODEL = "unknown"


def _norm_model(m):
    """A model id normalised for the configured/served comparison (#1064): the
    trailing `[..]` context tag stripped, lowercased -- the SAME tolerance
    `gates.designdispatch._norm_model` / `airuleset.is_allowed_model` apply, kept
    LOCAL so cli_authorship stays dependency-light."""
    m = (m or "").strip().lower()
    return re.sub(r"\[[^\]]*\]$", "", m)


def authorship_role(cwd):
    """"implementer" iff this process is the dual-agent implementer window
    (`AIRULESET_ROLE=implementer`, set by the managed `claude-impl` launcher);
    else "worker" iff `cwd` is inside an isolated lane worktree, else "main".

    #1060 L3b: the implementer runs its OWN Claude Code SESSION on the gateway
    backend (window 1 `impl`), NOT a dispatched subagent, and its work lands in
    a worktree — so the worktree test would otherwise stamp it "worker". The
    env role is checked FIRST so an implementer session stamps
    `Implemented-by: implementer <alias>` truthfully; the design-by gate keeps
    demanding the Fable id from `main`, so an implementer that mistakenly posted
    a `Design-by:` line stamps role `implementer` (!= main) and is refused.
    The env is set by the MANAGED launcher (never a self-declared string a
    weaker model types), and `Implemented-by:` is not the design-gate anti-spoof
    surface (that only trusts `Design-by: main`), so keying on it is safe.

    Uses the raw cwd string AND its realpath (a symlinked worktree still
    resolves under `.claude/worktrees/`), matching the substring predicate the
    close-trigger / foreign-write guards already use. An empty/None cwd -- a
    degenerate payload -- defaults to "main" (the safe direction: a raw comment
    that then carries `Design-by: main` from a genuine lane is caught by
    `gates.designbypost`; the dispatch gate is the real authority)."""
    if os.environ.get("AIRULESET_ROLE") == "implementer":
        return "implementer"
    if not cwd:
        return "main"
    raw = str(cwd)
    try:
        resolved = str(Path(cwd).resolve())
    except Exception:
        resolved = raw
    for candidate in (raw, resolved):
        # Append "/" before the substring test so the bare `…/.claude/worktrees`
        # directory itself also matches WORKTREE_MARKER -> classified "worker".
        # This errs SAFE: a stray `Design-by: main` posted from that dir is
        # treated as a worker's and blocked (the fail-closed direction), which
        # is what we want; a genuine main never runs from inside .claude/worktrees.
        if WORKTREE_MARKER in (candidate.rstrip("/") + "/"):
            return "worker"
    return "main"


def _default_projects_dir(home=None):
    home = home or os.environ.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".claude", "projects")


def _main_checkout_of(cwd):
    """The main checkout path a worktree `cwd` belongs to (the part before
    `/.claude/worktrees/`), or None when `cwd` is not a worktree path."""
    s = str(cwd)
    idx = s.find(WORKTREE_MARKER)
    if idx < 0:
        return None
    return s[:idx]


def _worktree_subagent_transcript(cwd, projects_dir):
    """(path, mtime) of the newest subagent transcript for a worktree `cwd`, or
    None. A worktree has no top-level project dir; a dispatched worker's
    transcript is nested under the SUPERVISOR's session dir at
    `<projects>/<enc-main-checkout>/<session>/subagents/<worktree-basename>.jsonl`
    (proven live on disk, #486/#1061). Best-effort: any error -> None."""
    main = _main_checkout_of(cwd)
    if not main:
        return None
    basename = os.path.basename(str(cwd).rstrip("/"))
    if not basename:
        return None
    try:
        from watchdog.transcripts import encode_project_dir
        base = Path(projects_dir) / encode_project_dir(main)
        if not base.is_dir():
            return None
        newest, newest_m = None, -1.0
        for p in base.glob("*/subagents/%s.jsonl" % basename):
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            if m > newest_m:
                newest, newest_m = p, m
        return (newest, newest_m) if newest else None
    except Exception:
        return None


def session_model(cwd, projects_dir=None, home=None):
    """The newest REAL assistant `message.model` id in the transcript of the
    session running in `cwd`, or `UNKNOWN_MODEL`.

    Read from the live jsonl (`watchdog.transcripts`), never a self-declared
    string (#1061). A MAIN session -> `<projects>/<enc-cwd>/*.jsonl`
    (`find_active_transcript`). A worktree lane -> its nested subagent transcript
    (`_worktree_subagent_transcript`). Any failure -> `UNKNOWN_MODEL` (honest;
    the dispatch gate then refuses, the fail-closed direction).

    Uses `transcript_newest_assistant_model` (the wide-window AUTHORSHIP reader),
    NOT `transcript_last_assistant_model` (the model-float audit's "last REAL
    served model"): the fix-forward for the deploy defect where a busy main
    turn's 60-entry tail (all tool-result entries) or an api-error tail made the
    float reader return `''` -> `unknown` -> a refused legitimate design (#1061,
    'Live defect after deploy'). The float reader keeps its own callers untouched.

    #1060 L3b: for the dual-agent IMPLEMENTER window (`AIRULESET_ROLE=
    implementer`), the model is the gateway ALIAS the managed launcher exported
    as `ANTHROPIC_MODEL` (== the marker's `main`, e.g. `impl-main`), NOT the
    transcript's resolved backend model — the alias stamps LITERALLY, mirroring
    `statusbar.account_email_segment`'s `impl:<alias>` render. The launcher sets
    the env (never a self-declared string), and the implementer stamp is not a
    design-gate anti-spoof surface, so keying on it is safe. Falls through to the
    transcript reader only when the env alias is unset (a bare `claude-impl`)."""
    if os.environ.get("AIRULESET_ROLE") == "implementer":
        alias = (os.environ.get("ANTHROPIC_MODEL") or "").strip()
        if alias:
            return alias
    try:
        from watchdog.transcripts import (find_active_transcript,
                                          transcript_newest_assistant_model)
    except Exception:
        return UNKNOWN_MODEL
    if projects_dir is None:
        projects_dir = _default_projects_dir(home)
    found = None
    try:
        found = find_active_transcript(projects_dir, cwd)
    except Exception:
        found = None
    if not found and authorship_role(cwd) == "worker":
        found = _worktree_subagent_transcript(cwd, projects_dir)
    if not found:
        return UNKNOWN_MODEL
    path = found[0]
    try:
        model = transcript_newest_assistant_model(path)
    except Exception:
        return UNKNOWN_MODEL
    model = (model or "").strip()
    return model if model else UNKNOWN_MODEL


def _managed_model():
    """The managed launch default (`airuleset.MANAGED_MODEL`) normalised, or
    None when airuleset is unimportable -- the degenerate case that still lets
    the `unknown` stamp/refusal fire (#1064). airuleset is imported LAZILY so the
    dependency-light dispatch-gate path (which never calls configured_model)
    stays untouched."""
    try:
        import airuleset
        return _norm_model(airuleset.MANAGED_MODEL)
    except Exception:
        return None


def _read_model_from_cmdline(pid):
    """The `--model <id>` value from `/proc/<pid>/cmdline` (NUL-separated argv),
    or None. Handles both `--model X` and `--model=X`. Best-effort: any read
    error / no flag -> None."""
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    args = [a for a in raw.split(b"\x00") if a]
    for i, a in enumerate(args):
        s = a.decode("utf-8", "replace")
        if s == "--model" and i + 1 < len(args):
            return args[i + 1].decode("utf-8", "replace").strip() or None
        if s.startswith("--model="):
            return s[len("--model="):].strip() or None
    return None


def _pane_configured_model(cwd):
    """The `--model <id>` the live claude pane running in `cwd` was LAUNCHED with,
    or None (#1064). READ-ONLY reuse of the watchdog tmux inventory +
    `_pane_claude_pid` primitive: match a claude pane whose (sudo-resolved) cwd
    equals `cwd`, resolve its claude pid, read `/proc/<pid>/cmdline` for a
    `--model` argv. Best-effort -- no tmux / no matching pane / no `--model` flag
    / a cross-user /proc read denial -> None (the caller falls back to the
    managed default). A dispatched subagent has no pane of its own, so a worktree
    lane cwd resolves to None here by design."""
    if not cwd:
        return None
    try:
        import watchdog
        from watchdog import tmux_io
    except Exception:
        return None
    targets = {str(cwd).rstrip("/")}
    try:
        rp = os.path.realpath(str(cwd)).rstrip("/")
    except Exception:
        rp = None
    if rp:
        targets.add(rp)
    try:
        panes = tmux_io.list_claude_panes()
    except Exception:
        return None
    for pane_id, pcwd in panes or []:
        if (pcwd or "").rstrip("/") not in targets:
            continue
        try:
            ppid = (watchdog._default_run(
                ["tmux", "display-message", "-p", "-t", pane_id,
                 "#{pane_pid}"]) or "").strip()
            cpid = watchdog._pane_claude_pid(ppid) if ppid.isdigit() else None
        except Exception:
            cpid = None
        if cpid:
            model = _read_model_from_cmdline(cpid)
            if model:
                return model
    return None


def configured_model(cwd, projects_dir=None, home=None):
    """The model the session running in `cwd` was LAUNCHED with, normalised
    (#1064) -- the stamp's PRIMARY identity, as opposed to `session_model`'s
    API-SERVED model (which floats). Resolution order:
      1. the dual-agent IMPLEMENTER alias (`AIRULESET_ROLE=implementer` +
         `ANTHROPIC_MODEL`), mirroring `session_model` so the pair matches;
      2. the pane's claude argv `--model <id>` (`_pane_configured_model`);
      3. the managed launch default (`_managed_model`) -- the main is always
         launched with it, so it is truthful for the common design author and
         covers a session launched via the settings `model` key (no `--model`
         argv) or a dispatched subagent (no pane);
      4. `UNKNOWN_MODEL` when even the managed default is unresolvable.
    `projects_dir`/`home` are accepted for signature symmetry with
    `session_model`; the pane resolution keys on `cwd`."""
    if os.environ.get("AIRULESET_ROLE") == "implementer":
        alias = (os.environ.get("ANTHROPIC_MODEL") or "").strip()
        if alias:
            return _norm_model(alias)
    raw = None
    try:
        raw = _pane_configured_model(cwd)
    except Exception:
        raw = None
    if raw and raw.strip():
        return _norm_model(raw)
    managed = _managed_model()
    return managed if managed else UNKNOWN_MODEL


def _stamp_value(role, configured, served):
    """Compose the `"<role> <model>[ (served: <served>)]"` value (#1064). The
    stamp records the CONFIGURED identity; the served model is appended ONLY when
    it is known AND differs -- so a session served the model it was launched with
    stamps byte-identically to before this change. When the configured model is
    unresolvable the served model (if known) becomes the stamp model; when
    NEITHER resolves the model is `unknown` (role still labelled)."""
    if configured == UNKNOWN_MODEL:
        return "%s %s" % (role, served)
    if served != UNKNOWN_MODEL and _norm_model(served) != configured:
        return "%s %s (served: %s)" % (role, configured, served)
    return "%s %s" % (role, configured)


def authorship_value(cwd, projects_dir=None, home=None):
    """The `"<role> <model>"` VALUE of an authorship stamp, e.g.
    `"main claude-fable-5-1"` / `"worker claude-opus-4-8"` / `"main unknown"`,
    with an optional ` (served: <model>)` audit suffix when the API-served model
    floated off the launch model (#1064). The half a `<kind>-by:` label is
    prefixed to (see `stamp_line`); a caller that emits its own `Reviewed-by:`
    field wants just this value."""
    role = authorship_role(cwd)
    configured = configured_model(cwd, projects_dir=projects_dir, home=home)
    served = session_model(cwd, projects_dir=projects_dir, home=home)
    return _stamp_value(role, configured, served)


def stamp_line(kind, cwd, projects_dir=None, home=None):
    """A truthful authorship stamp line for `kind` ("Design" / "Reviewed"):
    `"<kind>-by: <role> <model>"`, e.g. `"Design-by: main claude-fable-5-1"`,
    `"Reviewed-by: worker claude-opus-4-8"`, or `"Design-by: main unknown"`
    when the model is unreadable. Role is always labelled even when the model
    is unknown."""
    return "%s-by: %s" % (kind, authorship_value(cwd, projects_dir=projects_dir,
                                                  home=home))
