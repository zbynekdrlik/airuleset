"""watchdog.reaper — Job 37: runaway shadow-ugrep OS-process reaper (#776).

The FIRST OS-process reaper in the watchdog. Every cycle it finds processes
whose cmdline NARROWLY matches the Claude-Code shadow-ugrep signature
(`ugrep -G --ignore-files`) and whose elapsed run time exceeds 30 minutes,
SIGKILLs them, and logs the kill reason + cmdline + age.

WHY (root cause, traced in the issue #776): Claude Code injects a shadow
`grep()` function into every Bash-tool shell (`~/.claude/shell-snapshots/
snapshot-bash-*.sh`) that rewrites EVERY `grep` to
`ugrep -G --ignore-files --hidden -I --exclude-dir=.git ...`. The bundled
ugrep 7.5.0 has an OPEN upstream bug (anthropics/claude-code#81916):
`--ignore-files` against a directory busy-loops at 100% CPU forever, and its
orphaned child survives the session (a 15-day 295%-CPU orphan on subdev,
#774). `hooks/block-root-recursive-grep.sh` (Layer 1) stops NEW ones from
spawning; this reaper (Layer 2) is the backstop that cleans up anything that
is already running or was orphaned before Layer 1 landed. `#775` (resource
caps) is Layer 3.

FAIL-SAFE by construction — this is code that SIGKILLs processes on prod:
  * ONLY the exact signature `ugrep -G --ignore-files` matches — nothing else
    is ever a candidate.
  * NEVER a young process — the age gate (`etimes > 30 min`) is what tells a
    runaway apart from a legitimate transient grep (which finishes in seconds
    and, at worst, is killed when its tool call times out). No legitimate grep
    runs for 30 minutes; only the busy-loop orphan does.
  * ANY ps/parse error kills NOTHING (`ps_fetch()` raising, or returning None,
    is treated as "could not read → do nothing this cycle").
  * A malformed process row is skipped, never guessed at.
  * `dry_run` logs what it WOULD kill and kills nothing.

Log-only: a reaped runaway is a SELF-HEAL (like the api-error `continue`
auto-resume), so it goes to the watchdog journal, never a Discord ping (#546
alert-suppression direction).
"""

import os
import signal
import subprocess

# 30 minutes. No legitimate shadowed grep survives this; the busy-loop orphan
# runs for days.
REAPER_MIN_AGE_S = 1800

# The EXACT shadow-ugrep flag signature the CC shell-snapshot injects. Kept as
# a contiguous substring so a match is unambiguous — argv[0] is `ugrep`
# (exec -a ugrep / ARGV0=ugrep) and `-G --ignore-files` are the first two
# injected flags, so this appears as a prefix of every shadowed grep's
# cmdline. The age gate is what makes it a RUNAWAY, not just a grep.
SHADOW_UGREP_SIGNATURE = "ugrep -G --ignore-files"


def _is_shadow_ugrep_runaway(args, etimes, min_age_s):
    """True ONLY for a process whose cmdline carries the exact shadow-ugrep
    signature AND has been running longer than `min_age_s`. Both conditions
    are required; either alone is a legitimate transient."""
    try:
        age = int(etimes)
    except (TypeError, ValueError):
        return False
    if age <= min_age_s:
        return False
    return SHADOW_UGREP_SIGNATURE in (args or "")


def default_ps_fetch():
    """Read every process as (pid:int, etimes:int, args:str). Returns None on
    ANY error (→ the reaper kills nothing this cycle). `ps -o etimes=` gives
    elapsed time in whole SECONDS directly, so no clock arithmetic is needed."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,etimes=,args="],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "  1234   5678 ugrep -G --ignore-files ..." — pid, etimes, then argv
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etimes_s, args = parts
        try:
            pid = int(pid_s)
            etimes = int(etimes_s)
        except ValueError:
            continue
        rows.append((pid, etimes, args))
    return rows


def default_kill_fn(pid):
    """SIGKILL a pid. Raises on failure (the reaper catches + logs)."""
    os.kill(int(pid), signal.SIGKILL)


def shadow_ugrep_reaper(ps_fetch=None, kill_fn=None, dry_run=False,
                        min_age_s=REAPER_MIN_AGE_S):
    """Find + SIGKILL runaway shadow-ugrep processes older than `min_age_s`.

    `ps_fetch` returns an iterable of (pid, etimes, args); returning None (or
    raising) means "could not read → kill nothing". `kill_fn(pid)` sends the
    SIGKILL. Both default to the real implementations; tests inject fakes.
    Returns the journal log lines (possibly empty). NEVER pings Discord."""
    if ps_fetch is None:
        ps_fetch = default_ps_fetch
    if kill_fn is None:
        kill_fn = default_kill_fn

    logs = []
    try:
        procs = ps_fetch()
    except Exception as e:
        return ["shadow-ugrep-reaper: ps error, killed nothing: %r" % (e,)]
    if procs is None:
        # Could not read the process table — fail safe, kill nothing.
        return logs

    for entry in procs:
        try:
            pid, etimes, args = entry
        except Exception:
            # malformed row — skip, never guess
            continue
        if not _is_shadow_ugrep_runaway(args, etimes, min_age_s):
            continue
        if dry_run:
            logs.append(
                "shadow-ugrep-reaper: DRY-RUN would SIGKILL pid=%s age=%ss "
                "cmd=%s" % (pid, etimes, args))
            continue
        try:
            kill_fn(pid)
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s age=%ss runaway "
                "shadow-ugrep (issue 776, upstream cc#81916) cmd=%s"
                % (pid, etimes, args))
        except Exception as e:
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s FAILED: %r (age=%ss cmd=%s)"
                % (pid, e, etimes, args))
    return logs
