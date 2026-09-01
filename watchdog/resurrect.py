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
# The #805-interface fallback: after this many consecutive still-dead relaunch
# ATTEMPTS (an attempt that fired but did not bring the stream back), switch from
# `claude --continue` (restores context, incl. a ballooned one that may die again
# immediately) to a FRESH `claude` -- so a ballooned-context session cannot
# livelock the resurrect on --continue forever. `rfails` is WRITTEN by the census
# (`goal.goal_lane_sweep`): a due dead entry whose PREVIOUS due-cycle actually
# fired a relaunch (`ratt`) yet is STILL dead increments it; it clears when the
# stream comes back live (the visited-cwd reset). launch_cmd only READS it.
RESURRECT_MAX_FAILS = 2
_SHELL_COMMANDS = frozenset(("bash", "zsh", "sh", "fish", "dash", "ash", "ksh"))
# Bare shell-prompt terminators for the bare-idle check. GLYPH terminators
# (`❯➜»`) are prompt-only -- they end a bare prompt and essentially never end a
# line of program output. ASCII terminators (`$#%`) DO occur at the end of output
# ("... 45%", a `$price`), so they count as a bare prompt only when the char
# before them is NOT a digit (excludes percentages/numbers). Deliberately EXCLUDES
# `>` (bash PS2 continuation / a `read`-style prompt) -- typing into either would
# corrupt a multiline command or feed a running read.
_PROMPT_GLYPH_TERMINATORS = "❯➜»"
_PROMPT_ASCII_TERMINATORS = "$#%"
_LAUNCH_CONTINUE = "claude --continue"   # restores the session context incl. /goal
_LAUNCH_FRESH = "claude"                 # #805-interface fallback (fresh start)


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
    RESURRECT_MAX_FAILS consecutive still-dead relaunch attempts (`rfails`,
    written by the census), then a fresh `claude` (the #805 interface). Reads
    `rfails` off the entry, never mutates. A corrupt / non-numeric `rfails`
    reads as 0 (fail-safe -> `--continue`); NEVER raises (the module contract) --
    `launch_cmd` is `decide`'s FIRST line, so a raise here would abort the whole
    goal_lane_sweep every sweep."""
    fails = entry.get("rfails", 0) if isinstance(entry, dict) else 0
    if not isinstance(fails, (int, float)):
        fails = 0
    return _LAUNCH_FRESH if fails >= RESURRECT_MAX_FAILS else _LAUNCH_CONTINUE


def pane_is_bare_idle(pane, run):
    """True iff `pane`'s capture shows a shell at a BARE IDLE prompt — the last
    non-blank line ends at a shell prompt (`_PROMPT_TERMINATORS`) with NOTHING
    typed after it. This is the mode-4-review safety gate: `find_pane` matches a
    pane only by shell-command name + exact cwd, which does NOT prove the pane is
    idle — a human's STALE half-typed command (older than the 5-min client-input
    veto window, so signals 1/2/3 are all blind) would have `claude --continue`
    + Enter APPENDED to it and the concatenated line EXECUTED, and a foreground
    bash script / `read` prompt would receive the text on its stdin. This gate
    refuses (False) every such non-bare state so the caller logs `skip:pane-not-
    bare` and never keystrokes. Fail-safe: run==None / no pane / a failed or
    empty capture / no recognizable prompt terminator ALL read NOT-bare (never a
    keystroke into an unread pane). Never raises."""
    if run is None or not pane:
        return False
    try:
        cap = run(["tmux", "capture-pane", "-t", str(pane), "-p"]) or ""
    except Exception:
        return False
    lines = [ln.rstrip() for ln in cap.splitlines() if ln.strip()]
    if not lines:
        return False
    # rstrip so the check is robust to tmux stripping (or keeping) the prompt's
    # trailing space: a bare prompt ENDS at its terminator, typed text does not.
    last = lines[-1]
    t = last[-1]
    if t in _PROMPT_GLYPH_TERMINATORS:
        return True
    if t in _PROMPT_ASCII_TERMINATORS:
        return len(last) == 1 or not last[-2].isdigit()
    return False


def decide(entry, loc, pane, pane_bare, human_recent, human_reason, enabled,
           dry_run):
    """PURE mode-5 RESURRECT verdict for a DEAD roster entry the caller already
    found `due`. Returns `(log_line, act)` — a single explicit decision-log line
    (#486 structured state, no pane-render heuristic) plus `act`, True ONLY when
    a live relaunch keystroke should fire. `act` requires ALL of: a shell
    relaunch pane was found (`pane`), that pane is at a BARE IDLE prompt
    (`pane_bare` — the caller's `pane_is_bare_idle` read; never keystroke into a
    human's half-typed command or a running process), NO recent human on it (the
    mode-4 HARD veto — the owner's hardest rule
    `feedback_never_touch_stopped_sessions`; never keystroke a human-active
    pane), the opt-in flag ON, and not a dry-run. The CALLER owns the keystroke +
    the `rgts` cadence anchor (re-spaced on EVERY outcome so a persistent veto
    never floods); this function only decides and composes the line. `loc` is the
    human project label; the launch command is derived internally via
    `launch_cmd(entry)` (`claude --continue`, or a fresh `claude` after
    RESURRECT_MAX_FAILS failed attempts) and named in the line — the CALLER
    re-derives the SAME `launch_cmd(entry)` for the actual keystroke.

    Gate order is deliberate: no-pane first (nothing to type into), then the
    bare-idle gate (a non-bare pane holds typed text / a running process — refuse
    keystroke-free), then the recent-human HARD veto (a human-active pane is
    NEVER keystroked — logged with the vetoing signal — even with the flag ON),
    then the opt-in flag, then dry-run. `act` is conjunctive over every gate, so
    the order affects only which log the caller sees, never safety.

    The relaunch itself is NOT booked as "delivered": a shell-command launch
    into a bare bash prompt is outside `send_verified`'s CC-input-box domain (it
    verifies a CC transcript, and REFUSES a non-CC pane), so there is no
    transcript to confirm against this sweep. The line says the attempt is
    `delivered_unconfirmed` — the #594 fail-safe direction — and the STRUCTURAL
    confirmation is the NEXT census: a successful relaunch makes the cwd live
    again, so it drops out of `roster.dead_entries` and its entry is refreshed.
    Never claims a confirmed delivery from a bare send-keys (the mode-6 doctrine
    the owner rejected)."""
    cmd = launch_cmd(entry)
    if pane is None:
        return ("resurrect %s -> skip:no-relaunch-pane "
                "(no shell pane in the dead cwd)" % loc, False)
    if not pane_bare:
        return ("resurrect %s -> skip:pane-not-bare (shell pane holds typed "
                "text or a running process — never keystroke it)" % loc, False)
    if human_recent:
        return ("resurrect %s -> skip:recent-human HARD veto (%s) — never "
                "keystroke a human-active pane" % (loc, human_reason or "?"),
                False)
    if not enabled:
        return ("resurrect %s -> would relaunch (%s) -- disabled "
                "(AIRULESET_RESURRECT_ACTION off)" % (loc, cmd), False)
    if dry_run:
        return ("resurrect %s -> would relaunch (%s) -- dry-run" % (loc, cmd),
                False)
    return ("resurrect %s -> relaunching (%s; delivered_unconfirmed, the next "
            "census confirms the cwd came back live)" % (loc, cmd), True)


def find_pane(cwd, run):
    """The first pane whose foreground command is a SHELL (`_SHELL_COMMANDS`,
    not claude/node/bun) AND whose `pane_current_path` EXACTLY matches `cwd`, or
    None. A dead session leaves its tmux pane at a shell. This is a NECESSARY
    (not sufficient) relaunch target: the command name + cwd do NOT prove the
    pane is safe to type into, so the caller applies TWO further gates before any
    keystroke — `pane_is_bare_idle` (the pane is at a bare prompt with nothing
    typed / no running process) and the recent-human HARD veto. Queries the SAME
    `list-panes -a` shape as `_reconcile_candidate_panes` (3 fields, no
    `#{pane_pid}`). Returns the FIRST match in first-seen order. Never raises."""
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
