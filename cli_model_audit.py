"""airuleset.py model-audit — READ-ONLY allowlist check for model FLOAT (#871).

The managed launch pin (`MANAGED_MODEL`) fixes a session's model at LAUNCH, but
a running session can still emit a `model_changed` record and FLOAT mid-lifetime
onto a model outside the exact-id allowlist (`airuleset.MODEL_TIERS`) — e.g. the
banned Fable 5.1 (`claude-fable-5-1`) or a superseded Opus. No code surface can
prevent an in-session float; this command SURFACES it (read-only, no keystrokes
— feedback_never_keystroke_human_active_pane / no_manual_pane_nudges).

For every live managed tmux pane it reads the newest-assistant `model` of the
pane's MAIN transcript AND of every subagent transcript under it, and flags any
that is not on the allowlist. A watchdog job (machine-channel only, never an
owner ping — the #850 repo-health class) journals violations; the owner's remedy
per floated session is `/model → Fable 5` (or a relaunch, which re-lands the
launch pin automatically).
"""
import glob
import os
import time

# airuleset is imported LAZILY inside the functions below — a top-level
# `import airuleset` would be a circular import (airuleset imports this leaf
# mid-module to bind cmd_model_audit into SUBCOMMANDS).

# #871 — a live pane's `subagents/` dir accumulates EVERY subagent this
# session has EVER dispatched, over its whole multi-week lifetime. Walking
# the WHOLE history (the pre-fix behaviour) reported ~1200 long-dead
# subagents on a single audit run -- weeks-old, already-terminated
# `claude-opus-5` transcripts from before the ban even existed are not a
# live FLOAT risk. The audit is about sessions that CAN STILL FLOAT, i.e.
# LIVE state only, so a subagent transcript is considered ONLY when its
# mtime is within this window of "now". Reused by watchdog/model_audit_job.py
# (Job 41) so both the manual `airuleset.py model-audit` CLI and the hourly
# watchdog sweep share ONE definition of "recent enough to matter".
MODEL_AUDIT_SUBAGENT_RECENCY_S = 7200  # 2h


def _subagent_transcripts(main_path, now=None,
                          recency=MODEL_AUDIT_SUBAGENT_RECENCY_S):
    """Every subagent transcript under a main transcript's session dir --
    `<main.parent>/<main.stem>/subagents/**/*.jsonl` (the #6 layout) -- whose
    mtime is within `recency` seconds of `now` (default: time.time() at call
    time). A transcript older than the window is a dead historical subagent,
    not a live float risk, and is excluded (#871). [] on any error / no
    subagents dir / an unreadable file. Read-only."""
    try:
        base = os.path.join(os.path.dirname(main_path),
                            os.path.splitext(os.path.basename(main_path))[0],
                            "subagents")
        if not os.path.isdir(base):
            return []
        when = time.time() if now is None else now
        out = []
        for p in sorted(glob.glob(os.path.join(base, "**", "*.jsonl"),
                                  recursive=True)):
            try:
                if (when - os.path.getmtime(p)) <= recency:
                    out.append(p)
            except OSError:
                continue
        return out
    except OSError:
        return []


def audit_model_floats(panes, projects_dir, find_transcript, read_model,
                       subagent_iter=_subagent_transcripts):
    """Pure core (dependency-injected for tests). Returns a list of records:
    {pane, cwd, kind ("main"/"sub"), transcript, model, banned}.

    `panes` = [(pane_id, cwd)] (watchdog.tmux_io.list_claude_panes shape);
    `find_transcript(projects_dir, cwd)` = watchdog.transcripts.find_active_transcript
    ((path, mtime) or None); `read_model(path)` =
    watchdog.transcripts.transcript_last_assistant_model (str or '').

    `subagent_iter` (default `_subagent_transcripts`, recency-windowed —
    #871) is called ONCE per pane's main transcript; a subagent path
    already reported for an earlier pane in THIS call (e.g. two panes
    resolving to the same main transcript) is reported at most once."""
    import airuleset
    records = []
    seen_sub = set()
    for pane_id, cwd in panes:
        tr = find_transcript(projects_dir, cwd)
        if not tr:
            continue
        main_path = tr[0] if isinstance(tr, tuple) else tr
        if not main_path:
            continue
        main_model = read_model(main_path)
        if main_model:
            records.append({
                "pane": pane_id, "cwd": cwd, "kind": "main",
                "transcript": str(main_path), "model": main_model,
                # #871 review 🔴3a: the AUDIT-tolerant predicate — tolerates a
                # served dated snapshot id for an allowlisted tier, never a
                # dispatch-surface check (those stay exact elsewhere).
                "banned": airuleset.is_banned_model_for_audit(main_model),
            })
        for sub in subagent_iter(main_path):
            if str(sub) in seen_sub:
                continue
            seen_sub.add(str(sub))
            m = read_model(sub)
            if not m:
                continue
            records.append({
                "pane": pane_id, "cwd": cwd, "kind": "sub",
                "transcript": str(sub), "model": m,
                "banned": airuleset.is_banned_model_for_audit(m),
            })
    return records


def cmd_model_audit(args):
    """READ-ONLY: list every live pane's (and its subagents') newest served
    model, flag any outside the exact-id allowlist. Never keystrokes, never
    writes. `--json` for machine output; `--violations-only` to print only
    flagged rows; exit 1 if any banned model is live (so a watchdog job can act
    on the exit code), else 0."""
    import json as _json

    import airuleset
    from watchdog.transcripts import (find_active_transcript,
                                       transcript_last_assistant_model)
    from watchdog.tmux_io import list_claude_panes

    projects_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    try:
        panes = list_claude_panes()
    except Exception:
        panes = []

    records = audit_model_floats(panes, projects_dir, find_active_transcript,
                                 transcript_last_assistant_model)
    flagged = [r for r in records if r["banned"]]
    shown = flagged if getattr(args, "violations_only", False) else records

    if getattr(args, "json", False):
        print(_json.dumps({"records": shown,
                           "violations": len(flagged),
                           "allowlist": sorted(airuleset.MODEL_TIERS.values())}))
    else:
        if not shown:
            print("model-audit: %d live pane(s), 0 violations (allowlist: %s)"
                  % (len(panes), ", ".join(sorted(airuleset.MODEL_TIERS.values()))))
        for r in shown:
            print("%s  %-6s %-20s %s  [%s]" % (
                r["pane"], r["kind"], r["model"], r["cwd"],
                "BANNED" if r["banned"] else "ok"))
        if flagged:
            print("model-audit: %d BANNED (floated off the allowlist) — owner "
                  "remedy per session: /model -> Fable 5, or relaunch" % len(flagged))
    return 1 if flagged else 0
