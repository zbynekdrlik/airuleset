"""Pure gate/alarm logic — no I/O, fully unit-testable."""
from board import TERMINAL_PHASES, STALE_ACTIVE_S, STALE_WAIT_S, WAIT_PHASES

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


# Terminal CI failure conclusions — only these record a `ci` gate as 'fail'.
# A non-terminal / in-progress / neutral conclusion is NOT a failure (mirrors
# the careful 'pending' handling in mergeable_ok): recording it as 'fail' would
# raise a false MERGED_INCOMPLETE_GATE while CI is still running.
_CI_FAIL = {"failure", "cancelled", "timed_out", "action_required"}

def ci_gate(ci_conclusion):
    """Map a GitHub CI conclusion to the board's gate vocabulary:
      * success                                  -> ok
      * failure/cancelled/timed_out/action_required -> fail (terminal failures)
      * anything else (None/in_progress/neutral/skipped/unknown) -> pending
    Only terminal failures count as 'fail'; everything non-terminal is pending."""
    if ci_conclusion == "success":
        return "ok"
    if ci_conclusion in _CI_FAIL:
        return "fail"
    return "pending"


GRACE_S = 5 * 60  # while a gate is pending and a report arrived this recently → "verifying"

def compute_alarms(r):
    """r: dict(merged, merge_mode, is_bug_fix, has_deploy, phase, last_report_age_s, gate{check:state}).
    Returns a list of alarm codes. Claims can NEVER silence MERGED_INCOMPLETE_GATE."""
    alarms = []
    req = applicable_gates(r["is_bug_fix"], r["has_deploy"])
    gate = r.get("gate", {})

    if r["merged"]:
        # A merged PR ALREADY passed GitHub branch protection (CI + required
        # reviews enforced AT MERGE TIME), so the merge itself is the gate
        # evidence. Only a gate VERIFIED as 'fail' is a real problem; a merely
        # UNREPORTED (pending) worker gate is NOT — the worker simply stopped
        # self-reporting. Flagging pending gates turned every solved-but-
        # partially-reported run into a red MERGED_INCOMPLETE_GATE (the board
        # screamed "incomplete" at work GitHub had already merged).
        failed = [g for g in req if gate.get(g) == "fail"]
        pending = [g for g in req if gate.get(g, "pending") == "pending"]
        in_terminal = r["phase"] in TERMINAL_PHASES
        if failed:
            alarms.append("MERGED_INCOMPLETE_GATE")
        elif pending and not in_terminal and r["last_report_age_s"] < GRACE_S:
            # still settling: just merged, gates not all reported yet, fresh ping
            alarms.append("VERIFYING")
    # manual mode: green-but-unmerged is a valid done — no alarm (handled by not entering above)

    # stale / abandoned mid-gate (the other wrong-work mode)
    if r["phase"] not in TERMINAL_PHASES and r["phase"] != "asking-user":
        thresh = STALE_WAIT_S if r["phase"] in WAIT_PHASES else STALE_ACTIVE_S
        if r["last_report_age_s"] > thresh:
            alarms.append("STALE_ABANDONED")
    return alarms
