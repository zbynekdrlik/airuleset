"""#1256 (f) — kill tmux servers that test runs left behind.

A tmux server daemonizes, so a test run that dies (or whose cleanup never
reaches it) leaves it running. The controller carried ~120 of them on
2026-10-19, every one with `TMPDIR=<tmp>/airuleset-pytest-run-<uuid>` (the #548
per-run dir, inherited from the test that started it) and every one of those
dirs already removed.

Only own-uid processes whose comm is `tmux: server` AND whose TMPDIR is a
per-run dir are ever candidates: a real server (no such TMPDIR) is never
touched, and a concurrent live run keeps its dir, so its servers are kept.
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


def stale_servers(proc_root="/proc", isdir=os.path.isdir):
    """Servers whose test run is over: their per-run dir no longer exists."""
    return [pid for pid, tmpdir in _test_servers(proc_root) if not isdir(tmpdir)]


def run_servers(run_dir, proc_root="/proc"):
    """Servers started by THIS run (TMPDIR == run_dir)."""
    want = str(run_dir).rstrip("/")
    return [pid for pid, tmpdir in _test_servers(proc_root)
            if tmpdir.rstrip("/") == want]


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
