"""#871 — model-FLOAT audit job (machine-channel only, never an owner ping).

Hourly, reads the newest-assistant model of every live pane's MAIN transcript
and every RECENT subagent transcript under it (recency-windowed — see
`cli_model_audit.MODEL_AUDIT_SUBAGENT_RECENCY_S`, #871 fix), and JOURNALS any
model that is NOT on the exact-id allowlist (`airuleset.MODEL_TIERS`) — a
session that FLOATED off the launch pin mid-lifetime (`model_changed`), e.g.
onto the banned Fable 5.1. The audit is about sessions that CAN STILL FLOAT —
i.e. LIVE state only — so a subagent transcript is considered ONLY when its
mtime is within the shared recency window; a subagent dispatched weeks ago
(long dead, possibly from before the ban even existed) is not a live float
risk and is never journaled. Bounded by construction (a per-pane recency
window, not a "walk the whole history" scan), so this stays cheap per 60s
sweep even though it now checks every recent subagent, not just the newest.

Machine-channel ONLY (the #850 repo-health class): it returns journal LOG lines
and NEVER pings the owner (this module deliberately imports no `notify` send
path). The owner's remedy per floated session is `/model -> Fable 5`, or a
relaunch (which re-lands the launch pin). Read-only — never keystrokes, never
writes (feedback_never_keystroke_human_active_pane / no_manual_pane_nudges).
"""

MODEL_AUDIT_INTERVAL_S = 3600


def _flag(read_model, path, kind, pane, cwd, out):
    import airuleset
    m = read_model(path)
    # #871 review 🔴3a: the AUDIT-tolerant predicate — a served dated
    # snapshot id for an allowlisted tier (e.g. claude-haiku-4-5-20251001)
    # must not journal a false model-float violation.
    if m and airuleset.is_banned_model_for_audit(m):
        out.append("model-float %s pane=%s model=%s cwd=%s "
                    "(off the allowlist — /model -> Fable 5 or relaunch)"
                    % (kind, pane, m, cwd))


def model_float_audit_job(now, state, panes, projects_dir, read_model,
                          find_transcript, subagent_transcripts,
                          interval=MODEL_AUDIT_INTERVAL_S, dry_run=False,
                          due_fn=None):
    """Return journal log lines for every live pane/subagent on a banned model.
    Hourly-gated via `state["model_audit_last_ts"]`. Dependency-injected for
    tests. NEVER pings the owner. `dry_run` does not change behaviour (this job
    is already side-effect-free beyond journaling) but is accepted for the
    uniform job signature.

    `subagent_transcripts(main_path, now)` returns EVERY subagent transcript
    path within the shared recency window (in production wiring, this is
    `cli_model_audit._subagent_transcripts` — one definition of "recent
    enough", shared with the manual CLI, #871). A transcript path already
    flagged for an earlier pane in this SAME sweep (e.g. two panes resolving
    to the same main transcript) is checked at most once."""
    if due_fn is None:
        from .repo_health import _sweep_due
        due_fn = _sweep_due
    if not due_fn(state, "model_audit_last_ts", now, interval):
        return []
    out = []
    seen_sub = set()
    for pane, cwd in panes:
        tr = find_transcript(projects_dir, cwd)
        if not tr:
            continue
        main_path = tr[0] if isinstance(tr, tuple) else tr
        if not main_path:
            continue
        _flag(read_model, main_path, "main", pane, cwd, out)
        for sub in subagent_transcripts(main_path, now):
            if str(sub) in seen_sub:
                continue
            seen_sub.add(str(sub))
            _flag(read_model, sub, "sub", pane, cwd, out)
    state["model_audit_last_ts"] = now
    return out
