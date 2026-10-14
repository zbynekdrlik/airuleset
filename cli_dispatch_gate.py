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
import fcntl
import hashlib
import os
import time

import cli_concurrency


# #1229 — how long a just-allowed worktree dispatch holds the repo's pending
# slot. Hooks of one message start together, so a short window covers the
# same-message race; it also bounds how long a dispatch another gate refused
# keeps a corrected retry waiting (re-review round 3).
PENDING_LANE_TTL_S = 20


def _inside(child, parent):
    return bool(parent) and (child == parent
                             or child.startswith(parent.rstrip(os.sep) + os.sep))


def dispatch_gate_report(cwd, run=None, project=None):
    """#1229 — the hook's sequential verdict, for ANY worktree lane (an
    autopilot-worker or any subagent dispatched with ``isolation: worktree``).

    Line 1 is ``<verdict>|<mode>|<n>|<why>|<repo>``:

    * ``why=open`` — not sequential, not a git work tree, or the count failed:
      allow, nothing claimed (fails OPEN, like ``dispatch_gate_line``);
    * ``why=lanes`` — block: ``n`` counted lanes follow, one
      ``<state>\t<branch>\t<path>`` line each;
    * ``why=claim`` — allow: the hook claims ``<repo>``'s pending slot
      (``claim_pending_lane``) once every gate of the hook has allowed.

    Lanes are classified from the MAIN worktree, whatever subdir or lane the
    session's shell sits in, so transcript liveness is read where Claude Code
    writes it. ``project`` (the hook's ``CLAUDE_PROJECT_DIR``) is the session's
    own root: a main started inside a worktree (``claude -w``) does not count
    its own checkout, while a ``cd`` into a worker's lane hides nothing.
    ``<repo>`` is the shared git common dir, so every worktree of one
    repository claims the same slot."""
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
        main = os.path.dirname(repo) if os.path.basename(repo) == ".git" else cwd
        own = os.path.realpath(project) if project else ""
        lanes = [lane for lane in cli_lane_liveness.gather_live_lanes(main, run=run)
                 if not (own and _inside(own, os.path.realpath(lane.get("path") or "")))]
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
    before either worktree exists, so the live count is 0 for both. The check
    and the write of the slot file's mtime happen under ``flock``, so of two
    concurrent dispatches exactly one claims it, even over an expired slot.
    Returns True when claimed (the dispatch may go) and False when a fresh claim
    already exists (refuse). A filesystem error returns True: fail OPEN."""
    base = os.path.join(home or os.path.expanduser("~"), ".claude", "lane-pending")
    slot = os.path.join(base, hashlib.sha1(
        os.path.realpath(repo).encode()).hexdigest()[:12] + ".claim")
    try:
        os.makedirs(base, exist_ok=True)
        fd = os.open(slot, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        now = time.time() if now is None else now   # read AFTER the lock
        st = os.fstat(fd)
        if st.st_size and abs(now - st.st_mtime) < PENDING_LANE_TTL_S:
            return False
        os.ftruncate(fd, 0)
        os.write(fd, b"%d\n" % int(now))
        os.utime(slot, (now, now))
        return True
    except OSError:
        return True
    finally:
        os.close(fd)
