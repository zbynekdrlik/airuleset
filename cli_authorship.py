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
from pathlib import Path

# The isolated-lane worktree segment -- the SAME predicate `_is_worktree_repo_dir`
# (#972) and `close_trigger.is_worker_context` (#564) key on.
WORKTREE_MARKER = "/.claude/worktrees/"

UNKNOWN_MODEL = "unknown"


def authorship_role(cwd):
    """"worker" iff `cwd` is inside an isolated lane worktree, else "main".

    Uses the raw cwd string AND its realpath (a symlinked worktree still
    resolves under `.claude/worktrees/`), matching the substring predicate the
    close-trigger / foreign-write guards already use. An empty/None cwd -- a
    degenerate payload -- defaults to "main" (the safe direction: a raw comment
    that then carries `Design-by: main` from a genuine lane is caught by
    `gates.designbypost`; the dispatch gate is the real authority)."""
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
    'Live defect after deploy'). The float reader keeps its own callers untouched."""
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


def authorship_value(cwd, projects_dir=None, home=None):
    """The `"<role> <model>"` VALUE of an authorship stamp, e.g.
    `"main claude-fable-5-1"` / `"worker claude-opus-4-8"` / `"main unknown"`.
    The half a `<kind>-by:` label is prefixed to (see `stamp_line`); a caller
    that emits its own `Reviewed-by:` field wants just this value."""
    role = authorship_role(cwd)
    model = session_model(cwd, projects_dir=projects_dir, home=home)
    return "%s %s" % (role, model)


def stamp_line(kind, cwd, projects_dir=None, home=None):
    """A truthful authorship stamp line for `kind` ("Design" / "Reviewed"):
    `"<kind>-by: <role> <model>"`, e.g. `"Design-by: main claude-fable-5-1"`,
    `"Reviewed-by: worker claude-opus-4-8"`, or `"Design-by: main unknown"`
    when the model is unreadable. Role is always labelled even when the model
    is unknown."""
    return "%s-by: %s" % (kind, authorship_value(cwd, projects_dir=projects_dir,
                                                  home=home))
