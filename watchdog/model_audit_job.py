"""#871 — model-FLOAT audit job (machine-channel only, never an owner ping).

Hourly, reads the newest-assistant model of every live pane's MAIN transcript
and its single NEWEST subagent transcript (bounded — the `airuleset.py
model-audit` CLI walks the whole subagent history, which is fine for a manual
deep audit but too much per 60s sweep), and JOURNALS any model that is NOT on
the exact-id allowlist (`airuleset.MODEL_TIERS`) — a session that FLOATED off
the launch pin mid-lifetime (`model_changed`), e.g. onto the banned Fable 5.1.

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
    if m and airuleset.is_banned_model(m):
        out.append("model-float %s pane=%s model=%s cwd=%s "
                    "(off the allowlist — /model -> Fable 5 or relaunch)"
                    % (kind, pane, m, cwd))


def model_float_audit_job(now, state, panes, projects_dir, read_model,
                          find_transcript, newest_subagent,
                          interval=MODEL_AUDIT_INTERVAL_S, dry_run=False,
                          due_fn=None):
    """Return journal log lines for every live pane/subagent on a banned model.
    Hourly-gated via `state["model_audit_last_ts"]`. Dependency-injected for
    tests. NEVER pings the owner. `dry_run` does not change behaviour (this job
    is already side-effect-free beyond journaling) but is accepted for the
    uniform job signature."""
    if due_fn is None:
        from .repo_health import _sweep_due
        due_fn = _sweep_due
    if not due_fn(state, "model_audit_last_ts", now, interval):
        return []
    out = []
    for pane, cwd in panes:
        tr = find_transcript(projects_dir, cwd)
        if not tr:
            continue
        main_path = tr[0] if isinstance(tr, tuple) else tr
        if not main_path:
            continue
        _flag(read_model, main_path, "main", pane, cwd, out)
        sub = newest_subagent(main_path)
        if sub:
            _flag(read_model, sub, "sub", pane, cwd, out)
    state["model_audit_last_ts"] = now
    return out
