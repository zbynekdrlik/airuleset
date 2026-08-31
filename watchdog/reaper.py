"""watchdog.reaper — Job 37: runaway shadow-ugrep OS-process reaper (#776).

The FIRST OS-process reaper in the watchdog. Every cycle it finds processes
that are a genuine runaway shadow-ugrep — the exact `ugrep -G --ignore-files`
signature (anchored at argv[0]), running > 30 minutes, AND burning CPU the
whole time (a busy-loop) — SIGKILLs them, and logs the kill reason + cmdline +
age.

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
  * SIGNATURE anchored, not a substring — argv[0] basename must be `ugrep`
    and its first two flags must be `-G --ignore-files`. A process merely
    QUOTING the signature (`watch "pgrep -af 'ugrep -G --ignore-files'"`)
    never matches (its argv[0] is `watch`).
  * NEVER a young process — the age gate (`etimes > 30 min`) is necessary.
  * NEVER a low-CPU long-runner — the CPU gate (`cputimes >= 0.5 * etimes`)
    is what tells a 100%-CPU BUSY-LOOP apart from a legitimate long-running
    grep blocked on a pipe (`tail -f log | grep pat`, a sanctioned fleet
    waiter, burns ~0 CPU-seconds however long it runs, so it is NEVER
    killed). Age alone was not enough — this closes that hole.
  * TOCTOU — the pid's `/proc/<pid>/cmdline` is re-verified against the
    anchored signature immediately BEFORE the kill, so a pid reused by an
    unrelated process between the ps read and the kill is never killed.
  * ANY ps/parse error kills NOTHING (`ps_fetch()` raising, or returning
    None, is treated as "could not read → do nothing this cycle").
  * A malformed process row is skipped, never guessed at.
  * `kill_fn` UNWIRED (None) kills nothing — a mis-wired run_once seam logs
    "would kill" and moves on, never a real SIGKILL.
  * `dry_run` logs what it WOULD kill and kills nothing.
  * ps is scoped to this box-user's OWN processes (`-u <uid>`) — you can only
    SIGKILL your own anyway, so scoping avoids a permanent per-cycle
    permission-error log line for another user's runaway on a shared box.

Log-only: a reaped runaway is a SELF-HEAL (like the api-error `continue`
auto-resume), so it goes to the watchdog journal, never a Discord ping (#546
alert-suppression direction).
"""

import os
import signal
import subprocess

# 30 minutes. No legitimate shadowed grep survives this AND stays CPU-busy;
# the busy-loop orphan runs for days at 100% CPU.
REAPER_MIN_AGE_S = 1800

# A busy-loop burns ~1 CPU-second per wall-second (100% of a core), so its
# cumulative CPU time (`cputimes`) is >= its elapsed wall time; a pipe-blocked
# grep burns ~0. 0.5 is a wide, safe separator between the two.
REAPER_MIN_CPU_RATIO = 0.5

# The EXACT shadow-ugrep flag signature the CC shell-snapshot injects. argv[0]
# is `ugrep` (exec -a ugrep / ARGV0=ugrep) and the first two injected flags are
# `-G --ignore-files`. Anchored at argv[0] — NOT a substring match — so a
# process merely mentioning the signature in its arguments never matches.
SHADOW_UGREP_SIGNATURE = "ugrep -G --ignore-files"
_SIG_TOKENS = SHADOW_UGREP_SIGNATURE.split()   # ["ugrep", "-G", "--ignore-files"]


def _matches_signature(args):
    """True iff `args` (a process cmdline string) is the anchored shadow-ugrep
    signature: argv[0] basename == `ugrep` and its first two flags are
    `-G --ignore-files`."""
    toks = (args or "").split()
    if len(toks) < len(_SIG_TOKENS):
        return False
    if os.path.basename(toks[0]) != _SIG_TOKENS[0]:
        return False
    return toks[1:len(_SIG_TOKENS)] == _SIG_TOKENS[1:]


def _is_shadow_ugrep_runaway(args, etimes, cputimes, min_age_s=REAPER_MIN_AGE_S,
                             min_cpu_ratio=REAPER_MIN_CPU_RATIO):
    """True ONLY for a process carrying the anchored shadow-ugrep signature,
    running longer than `min_age_s`, AND burning CPU the whole time (a
    busy-loop). All three are required; any one alone is a legitimate
    transient / long-runner."""
    try:
        age = int(etimes)
        cpu = int(cputimes)
    except (TypeError, ValueError):
        return False
    if age <= min_age_s:
        return False
    if cpu < age * min_cpu_ratio:
        return False
    return _matches_signature(args)


def default_ps_fetch():
    """Read this user's OWN processes as (pid:int, etimes:int, cputimes:int,
    args:str). Returns None on ANY error (→ the reaper kills nothing this
    cycle). `ps -o etimes=`/`cputimes=` give whole SECONDS directly, so no
    clock arithmetic is needed. Scoped to `-u <uid>` — you can only SIGKILL
    your own processes, and scoping avoids a permanent permission-error log for
    another user's runaway on a shared box."""
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,etimes=,cputimes=,args=", "-u", str(os.getuid())],
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
        # "  1234   5678   5670 ugrep -G --ignore-files ..." — pid, etimes,
        # cputimes, then the whole argv (which itself contains spaces).
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, etimes_s, cputimes_s, args = parts
        try:
            pid = int(pid_s)
            etimes = int(etimes_s)
            cputimes = int(cputimes_s)
        except ValueError:
            continue
        rows.append((pid, etimes, cputimes, args))
    return rows


def default_proc_cmdline(pid):
    """Read /proc/<pid>/cmdline (NUL-separated argv) as a space-joined string,
    or None if it cannot be read (process gone). Used for the pre-kill TOCTOU
    re-verify."""
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return None
    return " ".join(p.decode("utf-8", "replace") for p in raw.split(b"\0") if p)


def default_kill_fn(pid):
    """SIGKILL a pid. Raises on failure (the reaper catches + logs)."""
    os.kill(int(pid), signal.SIGKILL)


def shadow_ugrep_reaper(ps_fetch=None, kill_fn=None, verify_fn=None,
                        dry_run=False, min_age_s=REAPER_MIN_AGE_S,
                        min_cpu_ratio=REAPER_MIN_CPU_RATIO):
    """Find + SIGKILL runaway shadow-ugrep processes (anchored signature, age >
    `min_age_s`, CPU-busy).

    `ps_fetch` returns an iterable of (pid, etimes, cputimes, args); returning
    None (or raising) means "could not read → kill nothing". `verify_fn(pid)`
    returns the pid's live cmdline for the pre-kill TOCTOU re-verify (default
    reads /proc). `kill_fn(pid)` sends the SIGKILL — when it is None (an
    unwired run_once seam) the reaper kills NOTHING and logs "would kill".
    `ps_fetch` defaults to the real process read; tests inject fakes. Returns
    the journal log lines (possibly empty). NEVER pings Discord."""
    if ps_fetch is None:
        ps_fetch = default_ps_fetch
    if verify_fn is None:
        verify_fn = default_proc_cmdline

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
            pid, etimes, cputimes, args = entry
        except Exception:
            # malformed row — skip, never guess
            continue
        if not _is_shadow_ugrep_runaway(args, etimes, cputimes, min_age_s,
                                        min_cpu_ratio):
            continue
        if dry_run:
            logs.append(
                "shadow-ugrep-reaper: DRY-RUN would SIGKILL pid=%s age=%ss "
                "cpu=%ss cmd=%s" % (pid, etimes, cputimes, args))
            continue
        if kill_fn is None:
            logs.append(
                "shadow-ugrep-reaper: kill_fn not wired — would SIGKILL pid=%s "
                "age=%ss cpu=%ss cmd=%s (skipped)"
                % (pid, etimes, cputimes, args))
            continue
        # TOCTOU: re-verify the pid still IS the runaway right before killing,
        # so a pid reused by an unrelated process is never SIGKILLed.
        try:
            live = verify_fn(pid)
        except Exception:
            live = None
        if live is None:
            logs.append(
                "shadow-ugrep-reaper: pid=%s vanished before kill, skipped "
                "(cmd was %s)" % (pid, args))
            continue
        if not _matches_signature(live):
            logs.append(
                "shadow-ugrep-reaper: pid=%s no longer the runaway (reused?), "
                "skipped (now %r)" % (pid, live))
            continue
        try:
            kill_fn(pid)
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s age=%ss cpu=%ss runaway "
                "shadow-ugrep (issue 776, upstream cc#81916) cmd=%s"
                % (pid, etimes, cputimes, args))
        except Exception as e:
            logs.append(
                "shadow-ugrep-reaper: SIGKILL pid=%s FAILED: %r "
                "(age=%ss cpu=%ss cmd=%s)" % (pid, e, etimes, cputimes, args))
    return logs
