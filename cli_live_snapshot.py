"""ONE /proc pass for a batch of live-use checks (#1216).

`cli_target_purge._target_in_live_use` walks every pid's exe, cwd and fd links
for EACH target. For thousands of transcript candidates that is thousands of
full walks: 6441 subagent transcripts on dev1 cost 462 s (13.5 M readlink
calls), so the watchdog unit was killed at its 90 s start timeout on every
poll and nothing was ever gzipped.

Here /proc is read once into two sets, and each target is answered with two
set lookups. The verdict is the same as `_target_in_live_use`:

- a link EQUAL to `realpath(target)`, or strictly INSIDE it, means live;
- an unusable /proc (missing, unlistable) or a realpath failure means live
  (fail-safe);
- a per-pid read failure (vanished or foreign-uid pid) is skipped, the
  same accepted residual.

Precedent: `cli_scratch_sweep._scan_live_tmp_tops` (#513) inverts the scan the
same way for /tmp. Stdlib only."""
import os
from pathlib import Path


def _pid_links(pdir):
    links = []
    for name in ("exe", "cwd"):
        try:
            links.append(os.readlink(pdir / name))
        except OSError:
            continue
    try:
        fds = os.listdir(pdir / "fd")
    except OSError:
        return links
    for fd in fds:
        try:
            links.append(os.readlink(pdir / "fd" / fd))
        except OSError:
            continue
    return links


def live_links(proc_dir=None):
    """Every readable exe/cwd/fd link of every pid, from ONE /proc pass, as
    `(exact, ancestors)`. `ancestors` holds every `link[:i]` with a separator
    at `i > 0`, so `t in ancestors` is exactly `link.startswith(t + os.sep)`
    for some link. None when /proc itself is unusable."""
    proc = Path(proc_dir) if proc_dir is not None else Path("/proc")
    if not proc.is_dir():
        return None
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    exact, ancestors = set(), set()
    for pid in pids:
        for link in _pid_links(proc / pid):
            if link in exact:
                continue
            exact.add(link)
            s = link
            while True:
                i = s.rfind(os.sep)
                if i <= 0:
                    break
                s = s[:i]
                if s in ancestors:
                    break       # its own prefixes were added with it
                ancestors.add(s)
    return frozenset(exact), frozenset(ancestors)


def in_live_use(snapshot, target):
    """`_target_in_live_use(target)` answered from a `live_links` snapshot."""
    if snapshot is None:
        return True
    try:
        resolved = os.path.realpath(str(target))
    except OSError:
        return True
    exact, ancestors = snapshot
    return resolved in exact or resolved in ancestors


def batch_checker(proc_dir=None):
    """A `target -> bool` live-use check for one batch. /proc is read on the
    FIRST call only, so a batch whose candidates all fail the cheaper floors
    never reads it."""
    snap = []

    def check(target):
        if not snap:
            snap.append(live_links(proc_dir))
        return in_live_use(snap[0], target)
    return check
