"""#1256 (f) — kill tmux servers that test runs left behind.

A tmux server daemonizes, so a test run that dies (or whose cleanup never
reaches it) leaves it running. The controller carried ~120 of them on
2026-10-19, every one with `TMPDIR=<tmp>/airuleset-pytest-run-<uuid>` (the #548
per-run dir, inherited from the test that started it) and every one of those
dirs already removed.

Only own-uid processes whose comm is `tmux: server` AND whose TMPDIR is a
per-run dir are ever candidates: a real server (no such TMPDIR) is never
touched, and a concurrent live run keeps its dir, so its servers are kept.
A server bound to the REAL default socket, or with a client connected to its
own socket path, is never reaped either (a stray test may have started the
real default server, which a human then joined).
"""
import os
import signal
import sys

RUN_PREFIX = "airuleset-pytest-run-"


def _test_servers(proc_root="/proc"):
    """[(pid, tmpdir)] for own-uid tmux servers started inside a test run."""
    uid = os.getuid()
    out = []
    try:
        entries = [d for d in os.listdir(proc_root) if d.isdigit()]
    except OSError as e:
        sys.stderr.write("tmux reaper: cannot list %s: %s\n" % (proc_root, e))
        return out
    for d in entries:
        base = os.path.join(proc_root, d)
        try:
            if os.stat(base).st_uid != uid:
                continue
            with open(os.path.join(base, "comm")) as f:
                if f.read().strip() != "tmux: server":
                    continue
            with open(os.path.join(base, "environ"), "rb") as f:
                env = f.read().split(b"\0")
        except OSError:
            continue   # exited between listdir and read, or not readable
        for item in env:
            if item.startswith(b"TMPDIR="):
                tmpdir = item[7:].decode("utf-8", "replace")
                if os.path.basename(tmpdir.rstrip("/")).startswith(RUN_PREFIX):
                    out.append((int(d), tmpdir))
                break
    return sorted(out)


def real_default_socket():
    """The account's REAL default socket (never the AIRULESET_TMUX_SOCK seam)."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp",
                        "tmux-%d" % os.getuid(), "default")


def _unix_table(proc_root):
    """{inode: (state, path)} from <proc_root>/net/unix."""
    table = {}
    try:
        with open(os.path.join(proc_root, "net", "unix")) as f:
            for line in f:
                p = line.split()
                if len(p) >= 8:
                    table[p[6]] = (p[5], p[7])
    except OSError as e:
        sys.stderr.write("tmux reaper: cannot read unix sockets: %s\n" % e)
    return table


def _protected(pid, proc_root, table, default_path):
    """True when `pid` listens on the real default socket, or a client is
    connected to its own socket path (state 03 on a path it listens on)."""
    fd_dir = os.path.join(proc_root, str(pid), "fd")
    entries = []
    try:
        for fd in os.listdir(fd_dir):
            try:
                link = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if link.startswith("socket:[") and link[8:-1] in table:
                entries.append(table[link[8:-1]])
    except FileNotFoundError:
        return False  # the process is gone: nothing left to protect
    except OSError:
        return True   # cannot prove it is unattended: keep it
    listening = {path for state, path in entries if state == "01"}
    if default_path in listening:
        return True
    return any(state == "03" and path in listening for state, path in entries)


def stale_servers(proc_root="/proc", isdir=os.path.isdir, default_path=None):
    """Servers whose test run is over (their per-run dir no longer exists),
    minus any on the real default socket or with a connected client."""
    default_path = default_path or real_default_socket()
    table = _unix_table(proc_root)
    return [pid for pid, tmpdir in _test_servers(proc_root)
            if not isdir(tmpdir) and not _protected(pid, proc_root, table, default_path)]


def run_servers(run_dir, proc_root="/proc", default_path=None):
    """Servers started by THIS run (TMPDIR == run_dir), same protections."""
    want = str(run_dir).rstrip("/")
    default_path = default_path or real_default_socket()
    table = _unix_table(proc_root)
    return [pid for pid, tmpdir in _test_servers(proc_root)
            if tmpdir.rstrip("/") == want
            and not _protected(pid, proc_root, table, default_path)]


def reap(pids, kill=os.kill):
    """SIGTERM each pid; a pid already gone is fine, anything else is reported."""
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as e:
            sys.stderr.write("tmux reaper: kill %d: %s\n" % (pid, e))
    return list(pids)
