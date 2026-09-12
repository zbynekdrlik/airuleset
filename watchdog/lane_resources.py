"""Per-resource lane cap (#970 fix-forward).

Extracted from ``watchdog/goal.py`` to keep that module under its size ratchet
(LOW finding #3 of an adversarial review: 5604 lines, ceiling raised
instead of split).  The module owns:

- ``_LANE_RESOURCE_FILE`` / ``_LANE_NEEDS_FILE`` — file-path constants
- ``lane_resource_caps(cwd)`` — reads ``.claude/lane-resources.json`` and
  returns ``(caps_dict, reason)`` where caps_dict carries ``total`` and
  optionally per-resource counts (``box``, ...)
- ``count_resource_usage(cwd, evidence)`` — reads ``.lane-needs`` from live
  worktree directories and returns ``{"box": K}``
- ``_lane_nudge_text(...)`` — resource-aware nudge text

Backward compat: ``goal.py`` re-imports the public names so existing
``from watchdog.goal import lane_resource_cap`` keeps working.
"""

import json
import os

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default lane ceiling -- up to 5 parallel lanes (#442/#481/#848).
GOAL_LANE_SATURATION_WORKERS = 5

_LANE_RESOURCE_FILE = os.path.join(".claude", "lane-resources.json")

#: Marker file the supervisor writes into the worktree's PRIVATE gitdir
#: at dispatch — NOT the working tree root (Y-2: a file at the root dirties
#: the tree and blocks disk-guard reclamation).  Path:
#:   <main>/.git/worktrees/<agent-id>/lane-needs
#: The supervisor resolves it via ``git -C "$WT" rev-parse --git-dir``.
#: Plain text, one resource name per line (e.g. ``box\n``).
#: A multi-resource lane (e.g. ``box\ngpu\n``) counts once per resource;
#: the double-count of ``live_workers - resource_occupied`` is an accepted
#: approximation for the single-resource fleet today (L-4).
_LANE_NEEDS_FILE = "lane-needs"

# ---------------------------------------------------------------------------
# lane_resource_caps -- the per-project resource declaration
# ---------------------------------------------------------------------------


def lane_resource_caps(cwd):
    """Read ``.claude/lane-resources.json`` and return ``(caps, reason)``.

    ``caps`` is a dict: ``{"total": N}`` (flat cap, backward compat) or
    ``{"total": N, "box": M}`` (per-resource).  ``reason`` is ``None`` on
    success or a short diagnostic string when the default is returned.

    File format::

        {"max_lanes": 5, "resources": {"box": 1}}

    ``max_lanes`` is the TOTAL ceiling (1..GOAL_LANE_SATURATION_WORKERS).
    ``resources`` maps resource names to their concurrency caps (optional;
    absent = flat cap, today's behavior).
    """
    default_caps = {"total": GOAL_LANE_SATURATION_WORKERS}
    p = os.path.join(cwd, _LANE_RESOURCE_FILE) if cwd else None
    if not p:
        return default_caps, None
    # airuleset:script-ok FileNotFoundError is the normal absent-file path
    try:
        with open(p) as f:
            raw = f.read()
    except FileNotFoundError:
        return default_caps, None
    except OSError as exc:
        return default_caps, "unreadable: %s" % exc
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return default_caps, "malformed JSON: %s" % exc
    if not isinstance(data, dict):
        return default_caps, "not a JSON object"

    # -- max_lanes (the total ceiling) ----------------------------------------
    cap = data.get("max_lanes")
    if isinstance(cap, bool):
        return default_caps, "max_lanes is bool, not int"
    if not isinstance(cap, int):
        return default_caps, "max_lanes is %s, not int" % type(cap).__name__
    if cap < 1 or cap > GOAL_LANE_SATURATION_WORKERS:
        return default_caps, ("max_lanes=%d out of range 1..%d"
                              % (cap, GOAL_LANE_SATURATION_WORKERS))
    caps = {"total": cap}

    # -- resources (optional per-resource sub-caps) ---------------------------
    resources = data.get("resources")
    if resources is not None:
        if not isinstance(resources, dict):
            return default_caps, ("resources is %s, not dict"
                                  % type(resources).__name__)
        for rname, rval in resources.items():
            if rname == "total":
                return default_caps, "resources.total is reserved"
            if isinstance(rval, bool):
                return default_caps, "resources.%s is bool, not int" % rname
            if not isinstance(rval, int):
                return default_caps, ("resources.%s is %s, not int"
                                      % (rname, type(rval).__name__))
            if rval < 1 or rval > cap:
                return default_caps, ("resources.%s=%d out of range 1..%d"
                                      % (rname, rval, cap))
            caps[rname] = rval
    return caps, None


# ---------------------------------------------------------------------------
# Backward compat shim -- lane_resource_cap returns (int, reason)
# ---------------------------------------------------------------------------

def lane_resource_cap(cwd):
    """Backward-compat wrapper: returns ``(total_cap, reason)`` as an int
    pair, exactly like the original #970 implementation."""
    caps, reason = lane_resource_caps(cwd)
    return caps["total"], reason


# ---------------------------------------------------------------------------
# count_resource_usage -- reads .lane-needs from live worktrees
# ---------------------------------------------------------------------------

def count_resource_usage(cwd, evidence):
    """Count lanes per resource class by reading ``.lane-needs`` files in
    worktree directories that correspond to LIVE workers.

    ``evidence`` is the ``[WorkerLane, ...]`` list from
    ``count_live_workers`` -- only lanes with ``state == "live"`` are
    counted.  Returns ``{"box": K}`` (a key per resource that has at
    least one occupant, or ``{}`` when none).
    """
    if not cwd or not evidence:
        return {}
    # Y-2 fix: read from the worktree's PRIVATE gitdir, not the working tree.
    # The gitdir for a worktree is <main-repo>/.git/worktrees/<agent-id>/.
    git_dir = os.path.join(cwd, ".git", "worktrees")
    # Fallback: also check the old working-tree path for transition compat.
    worktrees_dir = os.path.join(cwd, ".claude", "worktrees")
    counts = {}
    for lane in evidence:
        if lane.state != "live":
            continue
        # Primary: gitdir path (invisible to git status, dies with worktree)
        needs_path = os.path.join(git_dir, lane.agent_id, _LANE_NEEDS_FILE)
        if not os.path.isfile(needs_path):
            # Fallback: old working-tree-root path (transition compat)
            needs_path = os.path.join(
                worktrees_dir, lane.agent_id, _LANE_NEEDS_FILE)
        try:
            with open(needs_path) as f:
                needs = [line.strip() for line in f if line.strip()]
        except (OSError, IOError):
            # No lane-needs or unreadable -> box-free lane, no resource counted
            continue
        for rname in needs:
            counts[rname] = counts.get(rname, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Nudge text -- resource-aware
# ---------------------------------------------------------------------------

def _lane_nudge_text(backlog_n, waiters, caps, usage=None, live_workers=0,
                     candidate_n=None):
    """Build the lane-check nudge text with resource-aware info (#970).

    ``caps`` is the dict from ``lane_resource_caps`` (``{"total": N, "box": M}``
    or ``{"total": N}``).  ``usage`` is from ``count_resource_usage`` or
    ``None``.  ``live_workers`` is the total live lane count for the
    resource snippet.

    ``candidate_n`` (#993 item 3): the DISPATCHABLE-candidate count (workable ∧
    deps satisfied). When given, the action
    sentence names it — "dispatchni N DISPATCHOVATEĽNÝCH jednotiek" — instead of
    the old unconditional "sú voľné sloty — workable tikety dispatchni" pressure
    (the DECISION already refused to fire when candidate_n was 0). None keeps the
    legacy wording (a caller that has no dispatchable count, e.g. the backward-
    compat GOAL_LANE_NUDGE_TEXT_FN).
    """
    if isinstance(candidate_n, int) and not isinstance(candidate_n, bool):
        _action = ("Je %d DISPATCHOVATEĽNÝCH jednotiek (so zavretými "
                   "závislosťami) — dispatchni ich" % candidate_n)
    else:
        _action = "Sú VOĽNÉ sloty — workable tikety dispatchni"
    total = caps.get("total", GOAL_LANE_SATURATION_WORKERS)
    resource_keys = sorted(k for k in caps if k != "total")
    resource_snippet = ""
    if resource_keys and usage is not None:
        parts = []
        resource_occupied = 0
        for rk in resource_keys:
            rcap = caps[rk]
            rused = usage.get(rk, 0)
            resource_occupied += rused
            parts.append("%s %d/%d occupied" % (rk, rused, rcap))
        box_free_cap = total - sum(caps[rk] for rk in resource_keys)
        box_free_used = max(0, live_workers - resource_occupied)
        box_free_avail = max(0, box_free_cap - box_free_used)
        parts.append("box-free %d/%d free" % (box_free_avail, max(0, box_free_cap)))
        resource_snippet = " (%s)" % " · ".join(parts)

    # #994 — FACTS + the #848 CONTINUOUS REFILL mechanism, but NEVER a
    # count/priority PRESCRIPTION. The nudge reports backlog, live lanes,
    # waiters + resource caps and keeps the refill/worktree/serial-integration
    # mechanism, but it no longer says "hold up to N parallel lanes" / "saturate"
    # / "as many as possible" (the exact priority override the owner reported):
    # HOW MANY lanes and WHICH tickets first are the session's call, bounded by
    # real resource limits, and PRIORITY is the one the owner agreed this session
    # (#993), not this nudge.
    return (
        "lane-check: backlog=%d OTVORENÝCH tiketov (nie všetky musia byť hneď "
        "rozpracovateľné — zadržané zelené vetvy, časť v cudzom repe či zastrešujúce "
        "NErátaj; dispatchni len naozaj workable), BEŽÍ %d živých lán "
        "(waiterov beží: %d)%s. " + _action + " "
        "PARALELNÝMI isolation:\"worktree\" autopilot-worker lánmi "
        "(run_in_background), refill (doplň) vrátený slot po jeho návrate, "
        "integruj SÉRIOVO pod integračným mutexom a po každom integračnom cykle "
        "sprav compact-request --self; ustúp len na REÁLNY resource signál "
        "(server-side rate-limit, memory pressure boxu, CC max-subagents strop). "
        "PRIORITU (ČO riešiť a v akom poradí) ani POČET lán NEURČUJE tento nudge "
        "— platí priorita dohodnutá v tejto session: architektúra > "
        "architecture-rework > prio:bounce > backlog (#993). "
        "NErefillni dep-wait jednotku (otvorené Depends-on); infra prácu smeruj "
        "cez --role do infra roly (#993 r2b)."
    ) % (backlog_n, live_workers, waiters, resource_snippet)
