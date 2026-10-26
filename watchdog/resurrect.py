"""Bare-shell pane predicates (formerly the issue 804 mode-5 RESURRECT action).

The relaunch ACTION that typed `claude --continue` into a dead stream's bare
shell was deleted in the issue 1257 B9 fix round: a pane at a bare shell is a
session the owner left (`owner_stop` latches its cwd), so every relaunchable
pane was latched and the action (env-off fleet-wide) was unreachable. The
DEAD-SESSION census (`goal.goal_lane_sweep` + `roster.py`) still reports a dead
expected-armed stream. What remains are the two predicates other jobs read:
`_SHELL_COMMANDS` (the owner-stop exit scan) and `pane_is_bare_idle` (the
credential-dead relaunch never touches a pane at a bare shell). Leaf module:
no `import watchdog`, never raises.
"""

_SHELL_COMMANDS = frozenset(("bash", "zsh", "sh", "fish", "dash", "ash", "ksh"))
# Bare shell-prompt terminators. GLYPH terminators (`❯➜»`) end a bare prompt and
# essentially never a line of program output. ASCII terminators (`$#%`) DO end
# output ("... 45%", a `$price`), so they count only when the char before them
# is NOT a digit. `>` (bash PS2 / a `read`-style prompt) is deliberately excluded.
_PROMPT_GLYPH_TERMINATORS = "❯➜»"
_PROMPT_ASCII_TERMINATORS = "$#%"


def pane_is_bare_idle(pane, run):
    """True iff `pane`'s capture shows a shell at a BARE IDLE prompt — the last
    non-blank line ends at a shell prompt with NOTHING typed after it (a typed
    stale command, a running process's output or a `read` prompt is not bare).
    Fail-safe: run==None / no pane / a failed or empty capture / no
    recognizable prompt terminator ALL read NOT-bare. Never raises."""
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
