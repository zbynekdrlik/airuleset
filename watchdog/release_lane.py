"""Release-lane classifier (#846) — a PURE state machine over the release-train
lane state, consumed by the release-gap rider's nudge text AND the footer cache
writer. Candidate (ii) from the design: structured state in, typed verdict out,
one decision-log line per sweep (#486).

The classifier takes the WIDENED release state dict (from
`_watchdog_release_state_fetch`) and returns `(stage, action, evidence)` where:

  stage    ∈ STAGES — the current release-lane state
  action   — a short Slovak action string for the nudge text
  evidence — names the PR number / run id (or "" when not applicable)

Precedence: promote-PR-open → cut-PR-CI-red → shadow-failed →
cut-in-progress → deploying → no-cut → unknown. Missing fields degrade one
rung toward generic (unknown), never raise.
"""

STAGES = (
    "no-cut",
    "cut-ci-red",
    "shadow-failed",
    "cut-in-progress",
    "promote-ready",
    "deploying",
    "unknown",
)

STALLED_STAGES = ("cut-ci-red", "shadow-failed")


class LaneResult:
    __slots__ = ("stage", "action", "evidence")

    def __init__(self, stage, action, evidence=""):
        self.stage = stage
        self.action = action
        self.evidence = evidence

    def __repr__(self):
        return "LaneResult(%r, %r, %r)" % (self.stage, self.action, self.evidence)


def _pr_ci_red(pr):
    """True when a PR's statusCheckRollup contains at least one FAILURE/ERROR.
    Handles both CheckRun items (status/conclusion) and StatusContext items
    (state, no conclusion field) — a state=="FAILURE"/"ERROR" context is red."""
    if not isinstance(pr, dict):
        return False
    checks = pr.get("statusCheckRollup")
    if not isinstance(checks, list):
        return False
    for c in checks:
        if not isinstance(c, dict):
            continue
        conc = c.get("conclusion", "")
        if conc in ("FAILURE", "ERROR", "TIMED_OUT"):
            return True
        st = c.get("status", "")
        if st == "COMPLETED" and conc not in ("SUCCESS", "NEUTRAL", "SKIPPED", ""):
            return True
        # StatusContext items have `state` instead of status/conclusion.
        state = c.get("state", "")
        if state in ("FAILURE", "ERROR"):
            return True
    return False


def _pr_number(pr):
    if isinstance(pr, dict):
        n = pr.get("number")
        if isinstance(n, int) and not isinstance(n, bool):
            return n
    return None


def _run_id(run):
    if isinstance(run, dict):
        did = run.get("databaseId")
        if isinstance(did, int) and not isinstance(did, bool):
            return did
    return None


def classify_release_lane(lstate):
    """Classify the release lane from the widened release state dict.

    `lstate` may be None or a dict with optional keys: `cut_pr`, `promote_pr`,
    `shadow_run`, `last_deploy_ts`, `oldest_ahead_ts`, `ahead`, `in_flight`,
    `train`. Missing/malformed fields degrade toward `unknown`, never raise.

    Returns a `LaneResult(stage, action, evidence)`.
    """
    if not isinstance(lstate, dict):
        return LaneResult("unknown", "", "")

    cut_pr = lstate.get("cut_pr")
    promote_pr = lstate.get("promote_pr")
    shadow_run = lstate.get("shadow_run")

    # (1) promote PR open (staging→main) = ready to merge to prod
    if isinstance(promote_pr, dict) and _pr_number(promote_pr) is not None:
        n = _pr_number(promote_pr)
        return LaneResult(
            "promote-ready",
            "zmerguj staging→main PR #%d a spusti deploy do produkcie" % n,
            "PR #%d" % n,
        )

    # (2) cut PR CI red
    if isinstance(cut_pr, dict) and _pr_number(cut_pr) is not None:
        n = _pr_number(cut_pr)
        if _pr_ci_red(cut_pr):
            return LaneResult(
                "cut-ci-red",
                "oprav NA release vetve (release-fix cherry-pick na staging, "
                "NIKDY re-cut — každý restart stojí celý chvost). "
                "Cut PR #%d má RED CI" % n,
                "PR #%d" % n,
            )

        # (3) shadow run failed (only when cut PR is open)
        if isinstance(shadow_run, dict):
            conc = shadow_run.get("conclusion")
            rid = _run_id(shadow_run)
            if conc in ("failure", "timed_out"):
                ev = "run #%d" % rid if rid else "shadow run"
                return LaneResult(
                    "shadow-failed",
                    "shadow gate FAILED (%s): oprav spec chybu → "
                    "release-fix cherry-pick na staging, NIKDY re-cut" % ev,
                    ev,
                )

        # (4) cut PR open, CI not red, shadow not failed = in progress
        return LaneResult(
            "cut-in-progress",
            "cut PR #%d beží — CI/shadow prebieha" % n,
            "PR #%d" % n,
        )

    # (5) no cut PR, no promote PR — check if deploying
    in_flight = lstate.get("in_flight")
    if isinstance(in_flight, bool) and in_flight:
        return LaneResult("deploying", "deploy prebieha", "")

    # (6) no cut = nobody started the release
    ahead = lstate.get("ahead")
    if isinstance(ahead, int) and not isinstance(ahead, bool) and ahead > 0:
        return LaneResult(
            "no-cut",
            "spusti release pipeline: otvor develop→staging PR (cut)",
            "",
        )

    return LaneResult("unknown", "", "")
