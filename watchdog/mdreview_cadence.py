"""watchdog/mdreview_cadence.py — Job 43, the daily target-governance delta (#858, #874).

Dev1-gated (hostname gate: ``socket.gethostname() == "dev1"``).
Own durable state in ``~/.claude/mdreview-cadence.json`` (env seam
``AIRULESET_MDREVIEW_STATE_PATH`` for tests + ``cmd_push`` test_env).
Injected ``gh_runner`` seam.

DAILY target-governance delta (#874): once a day, while the tracking ticket
recorded in the state file (``ticket``) is OPEN, the job runs ONE fleet audit,
diffs each project's ``target_governance`` (what a target adds to its OWN
.claude/ — commands, skills, hooks, rules, settings hooks, CLAUDE.md @imports,
each with git provenance + a name classifier) against the previous snapshot
stored in THIS job's own durable state file, and posts a `Target-governance
delta …` comment on that ticket for the new/changed items (journal-only when
nothing changed). Blocks the built-in-shadow class BEFORE it lands via
hooks/block-builtin-command-shadow.sh (gates.commandshadow).

#1137: the job never creates or reopens a ticket. No ticket recorded, or a
ticket that is not OPEN (or unreadable), is ONE journal line and nothing else.
An audit failure advances the daily TTL (finding 7).

Imports NO notify — the lock-test in test_mdreview_audit.py verifies this.
"""

import json
import os
import socket
from pathlib import Path

DAILY_TTL_S = 86400

_REPO_SLUG = "zbynekdrlik/airuleset"

# Module default — overridden by AIRULESET_MDREVIEW_STATE_PATH env seam
# in tests/conftest.py AND cli_remote.cmd_push's test_env (#804-(4) pattern).
_DEFAULT_STATE_PATH = Path.home() / ".claude" / "mdreview-cadence.json"


def _state_path(override=None):
    """Resolve the cadence state file path.

    Priority: explicit override > env seam > module default.
    The override is kept for backward compat with existing tests that pass
    state_path directly; production uses the env seam.
    """
    if override:
        return Path(override)
    ovr = os.environ.get("AIRULESET_MDREVIEW_STATE_PATH", "")
    if ovr:
        return Path(ovr)
    return _DEFAULT_STATE_PATH


def _load_state(state_path_override=None):
    p = _state_path(state_path_override)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}  # airuleset:script-ok corrupt/missing state file — start fresh


def _save_state(data, state_path_override=None):
    p = _state_path(state_path_override)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)


def _gh(argv, gh_runner=None):
    """``(stdout, returncode)`` of one gh call (``gh_runner`` is the test seam)."""
    if gh_runner:
        return gh_runner(argv)
    import subprocess
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return r.stdout, r.returncode


def ticket_state(ticket, gh_runner=None):
    """The tracking ticket's state (``OPEN``/``CLOSED``), or None when gh
    cannot read it."""
    stdout, rc = _gh(["gh", "issue", "view", str(ticket), "-R", _REPO_SLUG,
                      "--json", "state"], gh_runner)
    if rc != 0:
        return None
    try:
        return (json.loads(stdout).get("state") or "").upper() or None
    except (ValueError, AttributeError):
        return None


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _post_comment(ticket, body, gh_runner=None):
    """Post a comment on the tracking ticket. Returns True on success."""
    return _gh(["gh", "issue", "comment", str(ticket), "-R", _REPO_SLUG,
                "--body", body], gh_runner)[1] == 0


def _target_governance_step(ticket, cad_state, audit_data, gh_runner=None):
    """Daily target-governance delta on the tracking ticket (#874).

    Diffs the audit's flattened ``target_governance`` against the previous
    snapshot stored in this job's OWN durable state, posts a comment for the
    new/changed items (journal-only when nothing changed), and returns
    ``(log_lines, new_snapshot)``. On a comment-post FAILURE the OLD snapshot
    is returned so the delta is re-attempted next run (never silently dropped).
    """
    import cli_mdreview_audit
    logs = []
    gmap = cli_mdreview_audit.collect_target_governance(audit_data)
    prev = cad_state.get("target_governance", {}) or {}
    date_str = audit_data.get("date") or _today()
    delta = cli_mdreview_audit.target_governance_delta(gmap, prev, date_str)
    new_snapshot = cli_mdreview_audit.governance_snapshot(gmap)

    if delta["comment"]:
        ok = _post_comment(ticket, delta["comment"], gh_runner=gh_runner)
        if ok:
            logs.append(
                f"mdreview-cadence: target-governance {delta['count']} new, "
                f"{delta['collisions']} collisions (posted)")
        else:
            logs.append(
                f"mdreview-cadence: target-governance {delta['count']} new "
                "(comment failed — snapshot held for retry)")
            return logs, prev
    else:
        logs.append("mdreview-cadence: target-governance 0 new, 0 collisions")
    return logs, new_snapshot


def _run_fleet_audit(fleet=True, fleet_runner=None):
    """Default ``audit_fn`` for :func:`mdreview_cadence_job` — the REAL daily
    fleet audit, run IN-PROCESS (not a subprocess): ``cli_mdreview_audit.
    run_fleet`` scans the local box's ``inventory_box`` and ssh's to every
    deployable host. Tests inject a fake ``audit_fn`` returning a small
    artifact dict instead, so the CI gate never spawns the real fleet audit
    (#874 fix-forward — the 68 s ``test_create_fires_at_most_once`` culprit).
    ``fleet`` is accepted for the seam signature; ``run_fleet`` always audits
    the whole fleet."""
    import cli_mdreview_audit
    return cli_mdreview_audit.run_fleet(fleet_runner=fleet_runner)


def mdreview_cadence_job(now, _state=None, dry_run=False,
                         state_path=None, gh_runner=None,
                         fleet_runner=None, audit_fn=None):
    """Job 43 entry point. Called from run_once.

    Uses its OWN durable state file (resolved via ``state_path`` override >
    env ``AIRULESET_MDREVIEW_STATE_PATH`` > module default), never run_once's
    state_path. The ``_state`` param is UNUSED (kept for backward compat with
    the run_once _add lambda shape which passes positional ``state``).
    The ``state_path`` param is an explicit override for tests that pre-seed
    their own state file; production uses the env seam (set in conftest.py +
    cmd_push test_env).
    The ``audit_fn`` param (#874) is the injectable daily-audit seam:
    ``audit_fn(fleet: bool) -> dict`` returns the audit artifact; the
    default runs the REAL in-process fleet audit (``_run_fleet_audit`` ->
    ``run_fleet``), and tests inject a fake so the CI gate never spawns
    ssh-to-every-host. ``fleet_runner`` remains the lower-level per-host
    seam threaded into the default audit_fn.
    Returns log lines.
    """
    logs = []

    if socket.gethostname() != "dev1":
        logs.append("mdreview-cadence: skip (not dev1)")
        return logs

    cad_state = _load_state(state_path)
    last_eval = cad_state.get("last_eval_ts", 0)
    if now - last_eval < DAILY_TTL_S:
        logs.append("mdreview-cadence: ttl-skip")
        return logs

    ticket = cad_state.get("ticket")
    state = ticket_state(ticket, gh_runner) if ticket else None
    if state != "OPEN":
        if not ticket:
            logs.append("mdreview-cadence: no tracking ticket recorded -- "
                        "nothing posted, none is filed (#1137)")
        else:
            logs.append("mdreview-cadence: tracking ticket #%s is %s -- no delta "
                        "posted, never reopened (#1137)" % (ticket, state or "unreadable"))
        if not dry_run:
            cad_state["last_eval_ts"] = now
            _save_state(cad_state, state_path)
        return logs

    if dry_run:
        logs.append("mdreview-cadence: would run daily audit + "
                    "target-governance delta (dry-run)")
        return logs

    # Daily fleet audit — ONE run per day. A failure advances the daily TTL.
    try:
        import cli_mdreview_audit
        # #874: the audit runs through the injectable ``audit_fn`` seam
        # (default = the real in-process fleet audit). Tests inject a
        # fake so the CI gate never spawns ssh-to-every-host (~68 s).
        run_audit = audit_fn
        if run_audit is None:
            def run_audit(fleet=True, _fr=fleet_runner):
                return _run_fleet_audit(fleet=fleet, fleet_runner=_fr)
        data = run_audit(fleet=True)
        artifact_path = cli_mdreview_audit.save_artifact(data)
        logs.append(f"mdreview-cadence: artifact {artifact_path}")
    except Exception as e:
        logs.append(f"mdreview-cadence: audit failed: {e!r}")
        cad_state["last_eval_ts"] = now
        _save_state(cad_state, state_path)
        return logs

    # Daily target-governance delta on the tracking ticket (#874).
    tg_logs, new_snapshot = _target_governance_step(
        ticket, cad_state, data, gh_runner=gh_runner)
    logs.extend(tg_logs)
    cad_state["target_governance"] = new_snapshot
    cad_state["last_eval_ts"] = now
    _save_state(cad_state, state_path)
    return logs
