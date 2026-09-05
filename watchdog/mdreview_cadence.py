"""watchdog/mdreview_cadence.py — Job 43, mdreview recurring cadence (#858).

Dev1-gated (hostname gate: ``socket.gethostname() == "dev1"``).
Own durable state in ``~/.claude/mdreview-cadence.json`` (env seam
``AIRULESET_MDREVIEW_STATE_PATH`` for tests + ``cmd_push`` test_env).
Injected ``gh_runner`` seam.

DUE when:
  (a) pinned ticket CLOSED and closedAt >= 30 days ago, OR
  (b) sha1(sorted(MODEL_TIERS.items())) != stored snapshot (model-generation).

On due: run mdreview-audit --fleet --json -> artifact -> REOPEN ticket + comment.
Failure -> journal line, hash NOT advanced (daily TTL IS advanced per finding 7).

Imports NO notify — the lock-test in test_mdreview_audit.py verifies this.
"""

import hashlib
import json
import os
import socket
from pathlib import Path

CADENCE_DAYS = 30
CADENCE_SECONDS = CADENCE_DAYS * 86400
DAILY_TTL_S = 86400

_REPO_SLUG = "zbynekdrlik/airuleset"

# Module default — overridden by AIRULESET_MDREVIEW_STATE_PATH env seam
# in tests/conftest.py AND cli_remote.cmd_push's test_env (#804-(4) pattern).
_DEFAULT_STATE_PATH = Path.home() / ".claude" / "mdreview-cadence.json"

BOOTSTRAP_TITLE = "mdreview: recurring fleet-wide audit (pinned)"
BOOTSTRAP_BODY_LINES = [
    "Scope-gate: planned-work",
    "Dedup-checked: pinned recurring mdreview ticket — reopened, never re-filed",
    "",
    "This ticket is REOPENED by the watchdog mdreview-cadence job (Job 43) when:",
    "- 30 days have passed since the last close, OR",
    "- the MODEL_TIERS lineup changed (model-generation trigger).",
    "",
    "Close it after completing the /mdreview session.",
]


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


def _model_tiers_hash():
    import airuleset
    items = sorted(airuleset.MODEL_TIERS.items())
    return hashlib.sha1(str(items).encode("utf-8")).hexdigest()


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


def evaluate_cadence(state, now, gh_runner=None):
    """Evaluate whether the mdreview audit is due.

    state: dict with {schema, ticket, model_tiers_hash, last_eval_ts}.
    gh_runner: callable(argv_list) -> (stdout_str, returncode).
    Returns {"due": bool, "reason": str}.
    """
    ticket = state.get("ticket")
    if not ticket:
        return {"due": False, "reason": "no-ticket"}

    if gh_runner:
        stdout, rc = gh_runner(
            ["gh", "issue", "view", str(ticket),
             "-R", _REPO_SLUG,
             "--json", "state,closedAt"])
        if rc != 0:
            return {"due": False, "reason": "gh-error"}
        try:
            ticket_data = json.loads(stdout)
        except json.JSONDecodeError:
            return {"due": False, "reason": "gh-parse-error"}
    else:
        import subprocess
        result = subprocess.run(
            ["gh", "issue", "view", str(ticket),
             "-R", _REPO_SLUG,
             "--json", "state,closedAt"],
            capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"due": False, "reason": "gh-error"}
        try:
            ticket_data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"due": False, "reason": "gh-parse-error"}

    state_val = (ticket_data.get("state") or "").upper()

    if state_val == "OPEN":
        return {"due": False, "reason": "open"}

    closed_at = ticket_data.get("closedAt", "")
    closed_epoch = 0
    if closed_at:
        import datetime
        try:
            if closed_at.endswith("Z"):
                closed_at = closed_at[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(closed_at)
            closed_epoch = dt.timestamp()
        except (ValueError, OSError):
            closed_epoch = 0  # airuleset:script-ok unparseable close timestamp

    if closed_epoch > 0 and (now - closed_epoch) >= CADENCE_SECONDS:
        return {"due": True, "reason": "30d"}

    current_hash = _model_tiers_hash()
    stored_hash = state.get("model_tiers_hash", "")
    if stored_hash and current_hash != stored_hash:
        return {"due": True, "reason": "model-generation"}

    return {"due": False, "reason": "not-due"}


def act_on_due(ticket, reason, audit_data, gh_runner=None):
    """REOPEN the ticket + post a summary comment.

    Returns True on success, False on gh failure.
    """
    summary_lines = [f"mdreview due ({reason})", ""]
    for box in audit_data.get("boxes", []):
        mem = box.get("memory", {})
        host = box.get("host", "?")
        summary_lines.append(
            f"- {host}: R={len(mem.get('R', []))} "
            f"P={len(mem.get('P', []))} "
            f"S={mem.get('S_flag_count', 0)} "
            f"dedup={len(box.get('dedup_pairs', []))}")
    for f in audit_data.get("failed", []):
        summary_lines.append(f"- FAILED: {f['host']} -- {f['error']}")
    comment_body = "\n".join(summary_lines)

    if gh_runner:
        _out, rc1 = gh_runner(
            ["gh", "issue", "reopen", str(ticket), "-R", _REPO_SLUG])
        if rc1 != 0:
            return False
        _out, rc2 = gh_runner(
            ["gh", "issue", "comment", str(ticket),
             "-R", _REPO_SLUG,
             "--body", comment_body])
        if rc2 != 0:
            return False
        return True
    else:
        import subprocess
        r1 = subprocess.run(
            ["gh", "issue", "reopen", str(ticket), "-R", _REPO_SLUG],
            capture_output=True, text=True, timeout=30)
        if r1.returncode != 0:
            return False
        r2 = subprocess.run(
            ["gh", "issue", "comment", str(ticket),
             "-R", _REPO_SLUG,
             "--body", comment_body],
            capture_output=True, text=True, timeout=30)
        if r2.returncode != 0:
            return False
        return True


def bootstrap_ticket(gh_runner=None):
    """One-time bootstrap: create the pinned recurring ticket.

    Before creating, searches for an existing ticket with the exact title
    to prevent duplicates on state loss. Returns the ticket number (int)
    or None.
    """
    # 🟡 RE-REVIEW: search for existing ticket before creating
    search_argv = ["gh", "issue", "list",
                   "-R", _REPO_SLUG,
                   "--search", f"{BOOTSTRAP_TITLE} in:title",
                   "--state", "all",
                   "--json", "number,title"]

    if gh_runner:
        stdout, rc = gh_runner(search_argv)
    else:
        import subprocess
        result = subprocess.run(search_argv, capture_output=True,
                                text=True, timeout=30)
        stdout, rc = result.stdout, result.returncode

    if rc == 0 and stdout.strip():
        try:
            existing = json.loads(stdout)
            for item in existing:
                if item.get("title") == BOOTSTRAP_TITLE:
                    return int(item["number"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass  # airuleset:script-ok fall through to create

    # No existing ticket found — create one
    body = "\n".join(BOOTSTRAP_BODY_LINES)
    create_argv = ["gh", "issue", "create",
                   "-R", _REPO_SLUG,
                   "-t", BOOTSTRAP_TITLE, "--body", body]

    if gh_runner:
        stdout, _rc = gh_runner(create_argv)
    else:
        import subprocess
        result = subprocess.run(create_argv, capture_output=True,
                                text=True, timeout=30)
        stdout = result.stdout

    import re as _re
    m = _re.search(r"/(\d+)\s*$", stdout.strip())
    if m:
        return int(m.group(1))
    return None


def mdreview_cadence_job(now, _state=None, dry_run=False,
                         state_path=None, gh_runner=None,
                         fleet_runner=None):
    """Job 43 entry point. Called from run_once.

    Uses its OWN durable state file (resolved via ``state_path`` override >
    env ``AIRULESET_MDREVIEW_STATE_PATH`` > module default), never run_once's
    state_path. The ``_state`` param is UNUSED (kept for backward compat with
    the run_once _add lambda shape which passes positional ``state``).
    The ``state_path`` param is an explicit override for tests that pre-seed
    their own state file; production uses the env seam (set in conftest.py +
    cmd_push test_env).
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
    if not ticket:
        if dry_run:
            logs.append("mdreview-cadence: would bootstrap (dry-run)")
            return logs
        new_num = bootstrap_ticket(gh_runner=gh_runner)
        if new_num:
            cad_state["ticket"] = new_num
            cad_state["model_tiers_hash"] = _model_tiers_hash()
            cad_state["last_eval_ts"] = now
            cad_state["schema"] = 1
            _save_state(cad_state, state_path)
            logs.append(f"mdreview-cadence: bootstrapped #{new_num}")
        else:
            logs.append("mdreview-cadence: bootstrap failed")
            cad_state["last_eval_ts"] = now
            _save_state(cad_state, state_path)
        return logs

    if not cad_state.get("model_tiers_hash"):
        cad_state["model_tiers_hash"] = _model_tiers_hash()
        _save_state(cad_state, state_path)

    result = evaluate_cadence(cad_state, now, gh_runner=gh_runner)

    if not result["due"]:
        logs.append(f"mdreview-cadence: not-due ({result['reason']})")
        cad_state["last_eval_ts"] = now
        _save_state(cad_state, state_path)
        return logs

    reason = result["reason"]
    logs.append(f"mdreview-cadence: due({reason})")

    if dry_run:
        logs.append(f"mdreview-cadence: would reopen #{ticket} (dry-run)")
        return logs

    try:
        import cli_mdreview_audit
        data = cli_mdreview_audit.run_fleet(fleet_runner=fleet_runner)
        artifact_path = cli_mdreview_audit.save_artifact(data)
        logs.append(f"mdreview-cadence: artifact {artifact_path}")
    except Exception as e:
        logs.append(f"mdreview-cadence: audit failed: {e!r}")
        cad_state["last_eval_ts"] = now
        _save_state(cad_state, state_path)
        return logs

    ok = act_on_due(ticket, reason, data, gh_runner=gh_runner)
    if ok:
        logs.append(f"mdreview-cadence: reopened #{ticket}")
        cad_state["model_tiers_hash"] = _model_tiers_hash()
        cad_state["last_eval_ts"] = now
        _save_state(cad_state, state_path)
        logs.append("mdreview-cadence: state advanced")
    else:
        logs.append("mdreview-cadence: reopen failed")
        cad_state["last_eval_ts"] = now
        _save_state(cad_state, state_path)

    return logs
