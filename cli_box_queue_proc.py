"""Process + lock control for `box-queue refresh` (#1171) — split out of
cli_box_queue.py (size ratchet), dependency-free: stdlib only, no import back.

- lock ownership: which processes OWN the refresh flock, read from
  `/proc/<pid>/fdinfo` `lock:` lines (only the locking open file description
  and the copies a child inherited carry one — a process that merely opened
  the file never does);
- kills: through a pidfd with an ownership re-check after it is open, so a
  pid recycled between scan and kill is never signalled; a command's process
  group only while its leader is an unreaped zombie (its pid — the group id —
  cannot be recycled then);
- a signal guard that turns SIGTERM/SIGHUP/SIGINT into an exception while a
  refresh runs (an ignored signal stays ignored).
"""

import contextlib
import fcntl
import os
import signal
import sys
import time


def lock_held(path):
    """True while some process (the refresh CLI or a command that inherited
    its fd) holds the flock on `path`. flock, not a pid: immune to pid reuse
    and to the CLI dying before its command."""
    try:
        fd = os.open(str(path), os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _owns_lock(pid, fd, want):
    """True when /proc/<pid>/fd/<fd> is the lock file AND its open file
    description OWNS the flock: fdinfo carries a FLOCK `lock:` line only for
    the locking fd and the copies a child inherited — never for a process
    that merely opened (or is probing) the file."""
    try:
        got = os.stat(f"/proc/{pid}/fd/{fd}")
        if (got.st_dev, got.st_ino) != (want.st_dev, want.st_ino):
            return False
        with open(f"/proc/{pid}/fdinfo/{fd}") as f:
            return any(ln.startswith("lock:") and "FLOCK" in ln for ln in f)
    except OSError:
        return False   # the fd closed / the process exited mid-scan


def _pid_owns(pid, want):
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False   # another uid's process, or it just exited
    return any(_owns_lock(pid, fd, want) for fd in fds)


def _lock_owners(path):
    """Pids of this uid owning the flock on `path` (see _owns_lock)."""
    try:
        want = os.stat(path)
    except FileNotFoundError:
        return []
    return [int(n) for n in os.listdir("/proc")
            if n.isdigit() and _pid_owns(n, want)]


def _kill_owner(pid, path):
    """SIGKILL `pid` through a pidfd, re-checking AFTER the pidfd is open
    that it still owns the lock — a pid recycled between scan and kill is
    never signalled. Without pidfd support: re-check, then os.kill."""
    want = os.stat(path)
    try:
        pidfd = os.pidfd_open(pid)
    except ProcessLookupError:
        return   # already gone — the goal anyway
    except (AttributeError, OSError) as e:
        print(f"box-queue: no pidfd for {pid} ({e}); plain kill", file=sys.stderr)
        if _pid_owns(pid, want):
            os.kill(pid, signal.SIGKILL)
        return
    try:
        if _pid_owns(pid, want):
            signal.pidfd_send_signal(pidfd, signal.SIGKILL)
    except ProcessLookupError:
        return   # exited between the check and the signal
    finally:
        os.close(pidfd)


def _kill_lock_owners(path, wait_s=2.0):
    """SIGKILL every owner of the lock except this process, then wait
    (bounded) until none is left. Returns (killed, still_owning)."""
    me = os.getpid()
    victims = [p for p in _lock_owners(path) if p != me]
    for pid in victims:
        try:
            _kill_owner(pid, path)
        except PermissionError as e:
            print(f"box-queue: cannot kill lock owner {pid}: {e}", file=sys.stderr)
    deadline = time.monotonic() + wait_s
    while True:
        left = [p for p in _lock_owners(path) if p != me]
        if not left or time.monotonic() >= deadline:
            return victims, left
        time.sleep(0.05)


class _Interrupted(Exception):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


def _raise_interrupted(signum, _frame):
    raise _Interrupted(signum)


@contextlib.contextmanager
def _signals_interrupt():
    """While a refresh runs, SIGTERM/SIGHUP/SIGINT raise _Interrupted so the
    command group is killed and the box recorded dirty — never left running
    unattended. A signal the caller IGNORES (nohup) stays ignored."""
    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            if signal.getsignal(sig) is not signal.SIG_IGN:
                old[sig] = signal.signal(sig, _raise_interrupted)
        except ValueError:
            break   # not the main thread: signals stay as they are
    try:
        yield
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def _exited_unreaped(proc, timeout):
    """Wait up to `timeout` for the leader to exit WITHOUT reaping it: while
    it is an unreaped zombie its pid — the group id — cannot be recycled, so
    the group kill that follows can never hit an unrelated process group."""
    deadline = time.monotonic() + timeout
    while True:
        if os.waitid(os.P_PID, proc.pid,
                     os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _kill_group(proc):
    """SIGKILL the command's whole process group (leader still unreaped, so
    the group id is its own), then reap the leader. Kills any background
    leftover of the refresh too, so none keeps the inherited lock."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"box-queue: refresh process group {proc.pid} already gone",
              file=sys.stderr)
    return proc.wait()


def _argv(cmd):
    return ["/bin/sh", "-c", cmd] if isinstance(cmd, str) else list(cmd)


def _tail_line(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 400))
            lines = [ln for ln in f.read().decode("utf-8", "replace").splitlines()
                     if ln.strip()]
        return lines[-1].strip()[:160] if lines else ""
    except OSError as e:
        return f"(refresh log unreadable: {e})"


def _cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:120]
    except OSError as e:
        return f"cmdline unreadable: {e}"
