"""Pure gate/alarm logic — no I/O, fully unit-testable."""

# source is a property of the CHECK, never of the report payload.
_VERIFIED = {"ci", "mergeable", "merged", "issue_state"}
REQUIRED_GATES = (
    "ticket_validated", "ci", "mergeable", "plan_check", "review",
    "requesting_code_review", "regression", "deploy_verified",
)
# supervisor_verify is tracked but not in the merge-required set (it gates the
# UNVERIFIED-CLAIM warning, not the MERGED-INCOMPLETE alarm).

def source_of(check):
    return "verified" if check in _VERIFIED else "claimed"

def applicable_gates(is_bug_fix, has_deploy):
    out = []
    for g in REQUIRED_GATES:
        if g == "regression" and not is_bug_fix:
            continue
        if g == "deploy_verified" and not has_deploy:
            continue
        out.append(g)
    return out

def mergeable_ok(mergeable, mergeable_state):
    if mergeable is None:
        return "pending"
    if mergeable is True and mergeable_state == "CLEAN":
        return "ok"
    return "fail"


GRACE_S = 5 * 60  # while a gate is pending and a report arrived this recently → "verifying"

def compute_alarms(r):
    """r: dict(merged, merge_mode, is_bug_fix, has_deploy, phase, last_report_age_s, gate{check:state}).
    Returns a list of alarm codes. Claims can NEVER silence MERGED_INCOMPLETE_GATE."""
    from board import TERMINAL_PHASES, STALE_ACTIVE_S, STALE_WAIT_S, WAIT_PHASES
    alarms = []
    req = applicable_gates(r["is_bug_fix"], r["has_deploy"])
    gate = r.get("gate", {})
    not_ok = [g for g in req if gate.get(g, "pending") != "ok"]

    if r["merged"]:
        if not_ok:
            # grace: still settling (phase not terminal yet) and a fresh report arrived
            # → verifying, not alarm. Once phase reaches terminal, any pending gate = alarm.
            in_terminal = r["phase"] in TERMINAL_PHASES
            if not in_terminal \
               and all(gate.get(g, "pending") == "pending" for g in not_ok) \
               and r["last_report_age_s"] < GRACE_S:
                alarms.append("VERIFYING")
            else:
                alarms.append("MERGED_INCOMPLETE_GATE")
    # manual mode: green-but-unmerged is a valid done — no alarm (handled by not entering above)

    # stale / abandoned mid-gate (the other wrong-work mode)
    if r["phase"] not in TERMINAL_PHASES and r["phase"] != "asking-user":
        thresh = STALE_WAIT_S if r["phase"] in WAIT_PHASES else STALE_ACTIVE_S
        if r["last_report_age_s"] > thresh:
            alarms.append("STALE_ABANDONED")
    return alarms
