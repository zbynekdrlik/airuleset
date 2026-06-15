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
