"""Per-resource lane cap (#970 fix-forward).

Extracted from ``watchdog/goal.py`` to keep that module under its size ratchet
(LOW finding #3 of an adversarial review: 5604 lines, ceiling raised
instead of split).  The module owns:

- ``_LANE_RESOURCE_FILE`` / ``_LANE_NEEDS_FILE`` — file-path constants
- ``lane_resource_caps(cwd)`` — reads ``.claude/lane-resources.json`` and
  returns ``(caps_dict, reason)`` where caps_dict carries ``total`` and
  optionally per-resource counts (``box``, ...)

(#1096: ``count_resource_usage`` and ``_lane_nudge_text`` were removed with the
lane-occupancy delivery-cadence machinery — #1089 retired the keystroke DELIVERY
that consumed the nudge text + its per-resource usage read.)

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
    """Read ``.claude/lane-resources.json`` + apply the #998 sequential-mode
    cap, returning ``(caps, reason)``.

    ``caps`` is a dict: ``{"total": N}`` (flat cap, backward compat) or
    ``{"total": N, "box": M}`` (per-resource).  ``reason`` is ``None`` on
    success or a short diagnostic string when the default is returned.

    #998: when the pane's EFFECTIVE mode is ``sequential`` (a declared
    sequential window, or a project ``{"mode":"sequential"}``), the total cap
    is forced to **1** regardless of ``max_lanes`` — ONE worker lane at a
    time. The mode is resolved by the SINGLE resolver
    (``cli_concurrency.resolve_mode``) so a declared window and a project file
    collapse to one lane through the same source of truth. ``parallel`` is
    byte-identical to the pre-#998 behaviour.
    """
    caps, reason = _file_caps(cwd)
    try:
        import cli_concurrency
        mode = cli_concurrency.resolve_mode(cwd)
    except Exception:  # noqa: BLE001 — resolver unavailable => today's caps
        mode = None
    if mode == "sequential":
        # ONE lane, authoritative over max_lanes/resources — the file caps (and
        # any file diagnostic) are moot once the total is forced to 1.
        return {"total": 1}, "sequential-mode"
    return caps, reason


def _file_caps(cwd):
    """The pre-#998 file-only cap read (max_lanes / resources). Split out so
    ``lane_resource_caps`` can layer the sequential-mode override on top."""
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
