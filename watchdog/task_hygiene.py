"""#1036 — Job 49: the global Odoo task-hygiene overseer.

Every ~2 h, for a CONFIGURED box (`~/.claude/odoo-task-tracking.json`), reads the
A/B/C client-board violations via the read-only JSON-2 client (`cli_odoo_ro`),
persists them to `~/.claude/task-hygiene/status.json` (the footer `I`, the quals
`--task-hygiene` flag, and the Stop gate all read THIS file — never a live Odoo
call), and — while A ∪ B is non-empty — delivers ONE gated `task-hygiene` nudge
into each eligible idle Claude pane on the box via the existing verified
keystroke primitive.

WHY GLOBAL (the 15.9. owner escalation): every stream enforced this from a
per-box memory rule + a local script; nothing fleet-wide checked the invariant
"a task in Verifikácia MUST carry a stream message", so the owner re-taught it to
every subdev Claude by hand. This is the one fleet overseer.

DESIGN (the parked_wake / model_float_audit Job-48/41 template): MACHINE-CHANNEL
only (never pings the owner — no `notify` import), dependency-injected for a
tmux/network-free unit test, and the keystroke path reuses
`send_verified(nudge="task-hygiene")` (a member of `tmux_io.MACHINE_NUDGE_KINDS`,
OFF by default — the supervisor stages it with `nudges on --kind task-hygiene`)
so the per-kind staging switch governs it and `nudge_gate` bounds it to the
owner's 1×/hour per-kind floor + cross-kind total cap. It is NOT in
`GATED_CATEGORIES` (like `bounce`/`card`/`goal-sweep`) — its cadence is decided
by `gate_ok("task-hygiene")` directly, so goal.py's shared batch never has to
compose a section it has no text for.
"""
import os

# The nudge identity for this job's keystrokes (a MACHINE_NUDGE_KINDS member,
# per-kind staged, OFF by default).
NUDGE_KIND = "task-hygiene"

# The job cadence: ~2 h (the owner's stated interval). run_once's gate uses it.
CADENCE_S = 2 * 3600


def cadence_due(now, state, cadence_s=CADENCE_S):
    """True iff at least `cadence_s` has elapsed since the last run (or it never
    ran). Read from `state["task_hygiene_last_ts"]` — a corrupt/absent value
    reads as 'never run' (due)."""
    last = state.get("task_hygiene_last_ts") if isinstance(state, dict) else None
    if not isinstance(last, (int, float)) or isinstance(last, bool):
        return True
    return (now - last) >= cadence_s


def mark_run(state, now):
    """Record this run's timestamp so the next `cadence_due` defers for the
    window. No-op on a non-dict state."""
    if isinstance(state, dict):
        state["task_hygiene_last_ts"] = now


def _resolve_sids(panes, projects_dir, find_transcript):
    """panes → {sid: [(pid, cwd, tpath), ...]} (the parked_wake resolver): a sid
    owned by >1 pane is AMBIGUOUS (shared cwd) and skipped by the caller."""
    per_sid = {}
    for pid, cwd in panes:
        tinfo = find_transcript(projects_dir, cwd)
        if not tinfo:
            continue
        tpath = tinfo[0] if isinstance(tinfo, (tuple, list)) else tinfo
        if not tpath:
            continue
        sid = os.path.basename(str(tpath))
        if sid.endswith(".jsonl"):
            sid = sid[: -len(".jsonl")]
        per_sid.setdefault(sid, []).append((pid, cwd, tpath))
    return per_sid


def task_hygiene_job(now, state, panes, projects_dir, *, cfg, compute, persist,
                     deliver, gate_ok, mark_sent, find_transcript, capture,
                     in_mode, at_idle, recent_human, nudges_enabled=None,
                     dry_run=False):
    """Return journal log lines for one task-hygiene sweep. NEVER pings the owner.

    Steps: compute A/B/C (via the injected `compute(cfg)` — the read-only Odoo
    client in prod, a fake in tests), persist the status, and — only while A ∪ B
    is non-empty — deliver ONE nudge per eligible pane (gated by `gate_ok` /
    recent-human / idle / in-mode, marked via `mark_sent` on a verified send).

    Injected deps (prod wiring passes the real primitives):
      compute(cfg)                     -> {A,B,C,summary}   (raises OdooError on read failure)
      persist(result)                  -> None              writes status.json
      deliver(pid, tpath, text)        -> bool              verified keystroke submit
      gate_ok(state, sid, kind, now)   -> bool              nudge_gate per-kind floor + total cap
      mark_sent(state, sid, kind, now) -> None              record a delivered nudge
      find_transcript(projects_dir, cwd) -> (tpath,) | tpath | None
      capture(pid)                     -> str               fresh pane capture
      in_mode(pid)                     -> bool              copy-mode/scroll → skip
      at_idle(captured)                -> bool              bare `❯` idle prompt
      recent_human(sid, cwd, tpath, pid) -> bool            VETO: a human is active
    """
    out = []
    try:
        result = compute(cfg)
    except Exception as e:                    # OdooError or any client fault
        out.append("task-hygiene: Odoo read failed: %r" % e)
        return out

    persist(result)
    a = result.get("A", [])
    b = result.get("B", [])
    c = result.get("C", [])
    out.append("task-hygiene: A=%d B=%d C=%d" % (len(a), len(b), len(c)))

    # Nudge only while A ∪ B is non-empty (C alone is a soft reminder, not a
    # stop-the-session obligation).
    if not (a or b):
        return out

    # #1036 review 🔵 — an HONEST OFF-box skip: when the kind is not staged on
    # (the default), skip the whole per-pane delivery loop rather than call
    # send_verified into each pane (which suppresses silently at the keys layer
    # with logs=None and misleadingly logs "submit-unverified"). The footer I +
    # the Stop hook carry the obligation while the nudge is OFF.
    if nudges_enabled is not None and not nudges_enabled(NUDGE_KIND):
        out.append("task-hygiene: A=%d B=%d — nudge kind OFF (stage via "
                   "`nudges on --kind task-hygiene`); footer/Stop carry it"
                   % (len(a), len(b)))
        return out

    from cli_task_hygiene import compose_nudge
    text = compose_nudge(result, cfg)
    if not text:
        return out

    per_sid = _resolve_sids(panes, projects_dir, find_transcript)
    for sid, owners in per_sid.items():
        if len(owners) > 1:
            out.append("task-hygiene: skip ambiguous (%d panes -> %s)"
                       % (len(owners), sid))
            continue
        pid, cwd, tpath = owners[0]
        captured = capture(pid)
        if in_mode(pid):
            out.append("task-hygiene: %s skip in-mode" % pid)
            continue
        if not at_idle(captured):
            out.append("task-hygiene: %s skip busy-pane" % pid)
            continue
        if recent_human(sid, cwd, tpath, pid):
            out.append("task-hygiene: %s skip recent-human" % pid)
            continue
        if not gate_ok(state, sid, NUDGE_KIND, now):
            out.append("task-hygiene: %s skip cadence" % pid)
            continue
        if dry_run:
            out.append("task-hygiene: %s would nudge (dry-run)" % pid)
            continue
        if deliver(pid, tpath, text):
            mark_sent(state, sid, NUDGE_KIND, now)
            out.append("task-hygiene: nudged %s (A=%d B=%d)" % (pid, len(a), len(b)))
        else:
            out.append("task-hygiene: %s submit-unverified, retry next sweep" % pid)
    return out
