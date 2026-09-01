"""#804 mode-5 -- RESURRECT a dead armed /goal stream (the ACTION half; the
DETECTION half is the durable roster + DEAD-SESSION census in
`goal.goal_lane_sweep`).

For a rostered EXPECTED-armed stream whose session DIED (no live claude/node/bun
candidate pane this sweep -- `roster.dead_entries`), the census can RELAUNCH the
managed Claude session by typing the launch command into the dead stream's
bare-idle-SHELL pane. Once CC is alive again its restored transcript still carries
the `Goal set:` marker with a dark footer, so the EXISTING dark-watch re-arm
pipeline (confirmed-dead -> dark-rearm -> deliver_goal, with all its #524 safety
gates) arms it -- resurrect REUSES that verified arm path (`send_verified` via
deliver_goal) rather than inventing a new keystroke-delivered arm. This is a
deliberate deviation from the settled design's `record_goal_request(origin=
"resurrect-rearm")`: relaunch-then-let-dark-watch-arm needs NO new origin and no
new 5-point wiring, so it carries a far lower blast radius than a new arm origin,
and the relaunched session's restored `Goal set:` marker feeds the existing
pipeline. If live verification shows `--continue` does NOT restore the marker, the
supervisor adds the explicit record during the live pass.

#486 structured-state: a PURE decision (`due` cadence + `launch_cmd` stage) with an
explicit decision-log line per skip/act, NO new pane-render heuristic. The single
side-effecting primitive (`relaunch`, the shell keystroke) fires ONLY behind the
`AIRULESET_RESURRECT_ACTION` opt-in flag (default OFF): a worktree worker cannot do
the design-mandated dry-run-first live kill->comeback verification (the relaunch +
arm span real CC startup across sweeps, box-specific), so the ACTION ships present
+ unit-tested + census-observable in dry-run/journal, and the SUPERVISOR enables
the flag fleet-wide after verifying the resurrect decisions in the journal + a real
kill->comeback on a sandbox box (design M5).

Safety (the owner's HARDEST rule, `feedback_never_touch_stopped_sessions` --
explicitly REVERSED by #804 "sam sa vypne a uz nezapne", but with guards):
resurrect keystrokes ONLY (1) a bare-idle-SHELL pane whose current_path EXACTLY
matches the dead cwd, (2) with NO recent human on it (the item-3 HARD recent-human
veto -- all 3 signals incl. tmux client_activity, with a LOGGED reason), (3) at
most once per RESURRECT_CADENCE_S, and NEVER when the owner deliberately retired
the stream (a user `/goal clear` DROPS the roster entry, so it is never
dead-flagged). This module never types on its own -- the caller gates every
side effect. Module-import safety mirrors `roster.py`/`nudge_gate.py`:
`watchdog/__init__.py` never imports it at module level; it needs no `import
watchdog`; it never raises.
"""
import os

# The minimum gap between relaunch ATTEMPTS for one stream. The census loop
# evaluates resurrect every sweep, but the `rgts` anchor (re-spaced on EVERY
# outcome -- act / veto / no-pane) bounds an attempt (and its journal line) to
# once per this window, so a persistent veto never floods. This is a FIXED
# cadence, NOT the settled design's escalating nudge_gate backoff -- consistent
# with mode-1/mode-2 shipping inline, lower blast radius; 30 min is a reasonable
# "a dead loop comes back within half an hour" cadence the owner asked for.
RESURRECT_CADENCE_S = 30 * 60
# #805 fallback: after this many consecutive still-dead relaunch attempts, switch
# from `claude --continue` (restores context, incl. a ballooned one that may die
# again immediately) to a FRESH `claude` -- so a ballooned-context session cannot
# livelock the resurrect on --continue forever.
RESURRECT_MAX_FAILS = 2
_SHELL_COMMANDS = frozenset(("bash", "zsh", "sh", "fish", "dash", "ash", "ksh"))
_LAUNCH_CONTINUE = "claude --continue"   # restores the session context incl. /goal
_LAUNCH_FRESH = "claude"                 # #805 fallback (fresh start)


def action_enabled():
    """The #804 mode-5 opt-in: the live relaunch keystroke fires ONLY when
    `AIRULESET_RESURRECT_ACTION` is truthy. Default OFF -- census decision-logging
    runs REGARDLESS, only the keystroke is gated (see the module docstring)."""
    return os.environ.get("AIRULESET_RESURRECT_ACTION", "").strip().lower() in (
        "1", "true", "yes", "on")


def due(entry, now):
    """PURE: is a resurrect ATTEMPT due for this dead roster `entry` at `now`
    (>= RESURRECT_CADENCE_S since the last attempt `rgts`)? Returns
    `(due: bool, wait_s: int|None)` -- a never-attempted entry (no numeric `rgts`)
    is due immediately (`wait_s=None`); otherwise `wait_s` counts down to the next
    window. A non-dict entry is never due. Never mutates, never raises."""
    if not isinstance(entry, dict):
        return False, None
    rgts = entry.get("rgts")
    if not isinstance(rgts, (int, float)):
        return True, None
    elapsed = now - rgts
    if elapsed >= RESURRECT_CADENCE_S:
        return True, None
    return False, int(RESURRECT_CADENCE_S - elapsed)


def launch_cmd(entry):
    """The launch command for this entry's stage: `claude --continue` until
    RESURRECT_MAX_FAILS consecutive still-dead attempts (`rfails`), then a fresh
    `claude` (#805). Reads `rfails` off the entry, never mutates."""
    fails = int(entry.get("rfails", 0) or 0) if isinstance(entry, dict) else 0
    return _LAUNCH_FRESH if fails >= RESURRECT_MAX_FAILS else _LAUNCH_CONTINUE


def find_pane(cwd, run):
    """The bare-idle-SHELL pane whose `pane_current_path` EXACTLY matches `cwd`,
    or None. A dead session leaves its tmux pane at a bare shell prompt (foreground
    command a login/interactive shell, not claude/node/bun). ONLY such a pane --
    never one running a foreground command (a human's process) -- is a relaunch
    target; the exact-cwd match + the recent-human veto the caller applies bound
    it further. Queries the SAME `list-panes -a` shape as
    `_reconcile_candidate_panes` (3 fields, no `#{pane_pid}`). Returns the
    FIRST match in first-seen order. Never raises."""
    if run is None or not cwd:
        return None
    try:
        out = run(["tmux", "list-panes", "-a", "-F",
                   "#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"]) or ""
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        pid, cmd, pcwd = parts
        if pid and pcwd == cwd and cmd.strip().lstrip("-") in _SHELL_COMMANDS:
            return pid
    return None


def relaunch(pane, cmd, run):
    """The ONE side-effecting primitive: type `cmd` + Enter into `pane` (a bare
    shell) to relaunch the managed Claude session. Returns True iff the send
    command returned without raising. The CALLER gates this on `action_enabled()`
    + not dry_run + the recent-human veto + a found bare-shell pane -- this
    function itself only types. Never raises."""
    if run is None or not pane or not cmd:
        return False
    try:
        run(["tmux", "send-keys", "-t", pane, cmd, "Enter"])
        return True
    except Exception:
        return False
