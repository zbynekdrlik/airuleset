"""cli_dispatch_gate — the #998 sequential gate's verdict for ANY worktree lane
(#1229), read by ``hooks/block-dispatch-over-wdrain.sh``.

``cli_concurrency`` resolves the pane's mode; this leaf turns it into the
hook's report (counted lanes, the session's own worktree excluded) and owns the
short pending-lane claim that closes the same-message race: several lanes
dispatched in ONE assistant message run their PreToolUse hooks before any
worktree exists, so each would count 0 lanes.

Dependency-light like ``cli_concurrency``: stdlib + ``cli_concurrency``;
``cli_lane_liveness`` is imported lazily inside the report (the gate hot path
of a non-sequential pane never loads it).
"""
import hashlib
import os
import time

import cli_concurrency


# #1229 — how long a just-allowed worktree dispatch holds the repo's pending
# slot: long enough for Claude Code to create its worktree, after which the
# live-lane count sees it.
PENDING_LANE_TTL_S = 90


def _inside(child, parent):
    return bool(parent) and (child == parent
                             or child.startswith(parent.rstrip(os.sep) + os.sep))


def dispatch_gate_report(cwd, run=None):
    """#1229 — the hook's sequential verdict, for ANY worktree lane (an
    autopilot-worker or any subagent dispatched with ``isolation: worktree``).

    Line 1 is ``<verdict>|<mode>|<n>|<why>|<repo>``:

    * ``why=open`` — not sequential, not a git work tree, or the count failed:
      allow, nothing claimed (fails OPEN, like ``dispatch_gate_line``);
    * ``why=lanes`` — block: ``n`` counted lanes follow, one
      ``<state>\t<branch>\t<path>`` line each;
    * ``why=claim`` — allow: the hook claims ``<repo>``'s pending slot
      (``claim_pending_lane``) once every gate of the hook has allowed.

    The session's OWN worktree (a main working inside ``EnterWorktree`` /
    ``claude -w``) is never one of its lanes. ``<repo>`` is the shared git
    common dir, so every worktree of one repository claims the same slot."""
    try:
        mode = cli_concurrency.resolve_mode(cwd)
    except Exception:  # noqa: BLE001 — cannot resolve => fail open
        return "allow|unknown|0|open|"
    if mode != "sequential":
        return "allow|%s|0|open|" % mode
    try:
        import cli_lane_liveness
        runner = run or cli_lane_liveness._run_default
        r = runner(["git", "-C", cwd, "rev-parse", "--path-format=absolute",
                    "--git-common-dir"])
        repo = (r.stdout or "").strip() if getattr(r, "returncode", 1) == 0 else ""
        if not repo:
            return "allow|sequential|0|open|"
        here = os.path.realpath(cwd)
        lanes = [lane for lane in cli_lane_liveness.gather_live_lanes(cwd, run=run)
                 if not _inside(here, os.path.realpath(lane.get("path") or ""))]
    except Exception:  # noqa: BLE001 — cannot count => fail open
        return "allow|sequential|0|open|"
    if lanes:
        rows = ["%s\t%s\t%s" % (lane.get("state", ""), lane.get("ref", ""),
                                 lane.get("path", "")) for lane in lanes]
        return "\n".join(["block|sequential|%d|lanes|%s" % (len(lanes), repo)]
                         + rows)
    return "allow|sequential|0|claim|%s" % repo


def claim_pending_lane(repo, home=None, now=None):
    """#1229 — claim ``repo``'s pending-lane slot for ``PENDING_LANE_TTL_S``.

    Two worktree dispatches in ONE assistant message run their PreToolUse hooks
    before either worktree exists, so the live count is 0 for both. The first
    allowed dispatch's claim (an atomic ``mkdir``) makes the second fail.
    Returns True when claimed (the dispatch may go) and False when a fresh claim
    already exists (refuse). A filesystem error returns True: fail OPEN."""
    base = os.path.join(home or os.path.expanduser("~"), ".claude", "lane-pending")
    slot = os.path.join(base, hashlib.sha1(
        os.path.realpath(repo).encode()).hexdigest()[:12])
    now = time.time() if now is None else now
    try:
        os.makedirs(base, exist_ok=True)
        os.mkdir(slot)
        return True
    except FileExistsError:
        pass
    except OSError:
        return True
    try:
        if now - os.stat(slot).st_mtime < PENDING_LANE_TTL_S:
            return False
        os.rmdir(slot)
    except FileNotFoundError:
        pass
    except OSError:
        return True
    try:
        os.mkdir(slot)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True
