"""#1067 slice 1c (a) — the DETACHED ops-wait refresher.

``airuleset._watchdog_ops_wait_fetch`` used to run the
``slice-quals|core-quals --ops-wait`` derivation as a BLOCKING subprocess
(``timeout=35``) inside the 90 s watchdog sweep, so a slow derivation (58 s live
on montalu1 after slice 1b) timed out on every stale-cache sweep and job 20
logged ``partition i:1|w:?``. The slice-1 FETCH_TIMEOUT backoff only made it fail
LESS often; the structural problem is that a slow, TTL-cached derivation sat on
the SYNCHRONOUS sweep path at all.

This leaf moves the derivation OFF the sweep path. The sweep READS a per-repo
atomic cache file (``fetch_or_refresh``); when that file is stale AND no
refresher child is alive it spawns ONE detached child (``spawn_refresher`` →
``airuleset.py <cmd> --ops-wait``, its own ``CHILD_TIMEOUT_S`` timeout,
``start_new_session=True``) that parses the CLI output (``parse_members``) and
atomically writes the member list into the same file (tmp + ``os.replace``). The
sweep NEVER waits for the derivation — it returns the last good result, ``None``
(undetermined — no result yet, exactly as before), or the caller's timeout
sentinel (a cold refresh timeout with no prior good result, so the outer
``_cached_member_fetch`` backs its fail-TTL off geometrically, slice 1).

The members still come from the ONE ``_partition_workable`` derivation (#367):
the child runs the SAME authority-aware ``--ops-wait`` CLI the blocking fetch
ran; only WHO waits for it changes. A child failure/timeout PRESERVES the prior
good member list in the file so a transient hiccup never drops a served W set.

# airuleset:script-ok the pidfile-remove in run_refresh_child's `finally` uses a
# bare `except OSError: pass` on purpose — the file may already be gone (a
# concurrent reclaim / never written) and the cleanup must NOT mask a real
# exception propagating out of the try; a detached child has no logging channel.
"""
import json
import os
import subprocess
import sys
import time

# Reuse the #547 TTL semantics. Kept as LOCAL constants (mirroring
# ops_wait_recheck.OPS_WAIT_FETCH_TTL_S / OPS_WAIT_FETCH_FAIL_TTL_S) so this leaf
# stays import-cycle-free — it is imported by airuleset._watchdog_ops_wait_fetch.
REFRESH_TTL_S = 30 * 60           # a good result is fresh for the full TTL
REFRESH_FAIL_TTL_S = 60           # an error/timeout entry re-checks soon
# the detached child's own hard timeout (generous vs the ~30-58 s derivation).
CHILD_TIMEOUT_S = 180
# reclaim a pidfile whose recorded child is older than this even if /proc still
# shows the pid (pid reuse / a crash without cleanup) — a belt beyond the child's
# own CHILD_TIMEOUT_S so a wedged/reused pid can never block refresh forever.
CHILD_MAX_AGE_S = CHILD_TIMEOUT_S + 120


class _Timeout(Exception):
    """The child derivation exceeded CHILD_TIMEOUT_S (distinct from a gh error)."""


def _refresh_dir():
    d = os.path.join(os.path.expanduser("~"), ".claude", "ops-wait-refresh")
    os.makedirs(d, exist_ok=True)
    return d


def _key(cwd):
    import statusbar
    return statusbar.cwd_key(cwd)


def cache_path(cwd):
    return os.path.join(_refresh_dir(), _key(cwd) + ".json")


def pid_path(cwd):
    return os.path.join(_refresh_dir(), _key(cwd) + ".pid")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_atomic(path, obj):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def parse_members(stdout):
    """Parse ``--ops-wait`` TSV stdout into the member-dict list the job-20 nudge
    consumes. Returns ``None`` on a malformed member line (undetermined — never a
    partial set), ``[]`` on a clean empty result. Moved VERBATIM from
    ``airuleset._watchdog_ops_wait_fetch``'s parse loop (#1067 slice 1c) so the
    child and the pre-1c fetch parse identically."""
    members = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        # #754: `--ops-wait` appends a `#`-prefixed aggregate W-summary line —
        # skip it here so it never trips the malformed→None guard below.
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        try:
            num = int(parts[0])
        except (ValueError, IndexError):
            return None   # a malformed line -> undetermined, never a partial set
        # field 3 is the reason column (>=5 fields = the full form); field 4+ the
        # (tab-joined) title. Anything shorter -> no flags, empty title.
        reason = parts[3] if len(parts) >= 5 else ""
        title = "\t".join(parts[4:]) if len(parts) >= 5 else ""
        members.append({"number": num, "stale": "stale!" in reason,
                        "gk_handoff": "gk-handoff!" in reason,
                        "release_recheck": "recheck!" in reason,
                        "acceptance": "acceptance" in reason,
                        "tacit_close": "tacit-close?" in reason,
                        "converge": "converge!" in reason,
                        "no_target": "no-target!" in reason,
                        "deploy_target": "deploy-target!" in reason,
                        "title": title})
    return members


def refresher_alive(cwd, now=None):
    """True iff a live refresher child is recorded for ``cwd``. Reclaims a stale
    pidfile (a dead /proc entry, or a record older than CHILD_MAX_AGE_S — pid
    reuse / a crash without cleanup)."""
    now = time.time() if now is None else now
    data = _read_json(pid_path(cwd))
    if not isinstance(data, dict):
        return False
    try:
        pid = int(data.get("pid"))
        started = float(data.get("ts", 0))
    except (TypeError, ValueError):
        _reclaim_pid(cwd)
        return False
    if not os.path.exists("/proc/%d" % pid):
        _reclaim_pid(cwd)
        return False
    if now - started > CHILD_MAX_AGE_S:
        return False   # too old -> allow a fresh spawn (do NOT kill the old one)
    return True


def _reclaim_pid(cwd):
    try:
        os.remove(pid_path(cwd))
    except OSError:
        return   # already gone / not ours to remove


def _spawn_due(entry, now):
    """Should a refresh be spawned? A missing/typeless entry is always due; a
    SUCCESS entry is fresh for the full TTL; an entry whose LATEST attempt failed
    (error/timeout flag) is fresh only for the short fail-window so a transient
    failure re-checks soon (mirrors the outer cache's fail-TTL)."""
    if not isinstance(entry, dict):
        return True
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)):
        return True
    failed = bool(entry.get("error") or entry.get("timeout"))
    ttl = REFRESH_FAIL_TTL_S if failed else REFRESH_TTL_S
    return (now - ts) >= ttl


def _serve(entry, timeout_sentinel):
    """What the sweep returns from a cache entry: the last good member list
    whenever present (even alongside a latest-attempt failure flag), else the
    timeout sentinel for a cold timeout, else None (no usable result yet)."""
    if isinstance(entry, dict):
        members = entry.get("members")
        if isinstance(members, list):
            return members
        if entry.get("timeout"):
            return timeout_sentinel
    return None


def fetch_or_refresh(cwd, cmd_name, timeout_sentinel, argv0=None, now=None,
                     spawn_fn=None, alive_fn=None):
    """NON-BLOCKING ops-wait member fetch (the module docstring has the full
    contract). Reads the per-repo cache file; when it is stale AND no refresher
    child is alive, spawns ONE detached refresher; returns the last good result,
    None, or ``timeout_sentinel``. Never runs the derivation itself."""
    now = time.time() if now is None else now
    entry = _read_json(cache_path(cwd))
    alive = alive_fn or refresher_alive
    if _spawn_due(entry, now) and not alive(cwd):
        (spawn_fn or spawn_refresher)(cwd, cmd_name, argv0)
    return _serve(entry, timeout_sentinel)


def spawn_refresher(cwd, cmd_name, argv0=None):
    """Spawn ONE detached refresher child (``start_new_session=True``) that runs
    ``<cmd_name> --ops-wait`` for ``cwd`` and writes the parsed members into the
    cache file. Best-effort: any OSError is swallowed (the next sweep retries)."""
    if argv0 is None:
        import airuleset
        argv0 = os.path.abspath(airuleset.__file__)
    # the repo root (parent of the `watchdog` package) so `-m
    # watchdog.ops_wait_refresh` resolves in the fresh child interpreter.
    repo_root = os.path.dirname(os.path.abspath(argv0))
    try:
        subprocess.Popen(
            [sys.executable, "-m", "watchdog.ops_wait_refresh",
             "--target", cwd, "--cmd", cmd_name,
             "--cache", cache_path(cwd), "--pid", pid_path(cwd),
             "--argv0", argv0],
            cwd=repo_root,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return   # spawn failed -> next sweep retries (fail-safe: no result yet)


def _default_run(target, cmd_name, argv0):
    """Run ``<cmd_name> --ops-wait`` for ``target`` with the child's own hard
    timeout; return ``(returncode, stdout)``. Raises ``_Timeout`` on overrun."""
    try:
        r = subprocess.run(
            [sys.executable, argv0, cmd_name, "--ops-wait"],
            cwd=target, capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _Timeout()
    return r.returncode, r.stdout


def _derive(target, cmd_name, argv0, run_fn=None):
    """Run the derivation; classify the outcome as ok/timeout/error."""
    run = run_fn or _default_run
    try:
        rc, out = run(target, cmd_name, argv0)
    except _Timeout:
        return {"kind": "timeout"}
    if rc != 0:
        return {"kind": "error"}
    members = parse_members(out)
    if members is None:
        return {"kind": "error"}   # malformed output -> undetermined
    return {"kind": "ok", "members": members}


def run_refresh_child(target, cmd_name, cache, pid_file, argv0, run_fn=None):
    """The detached child body: record the pidfile, run the derivation (its own
    timeout), and atomically write the result to ``cache`` — PRESERVING the prior
    good member list on a failure/timeout so a transient hiccup never drops a
    served W set. Removes the pidfile at exit no matter what."""
    try:
        _write_atomic(pid_file, {"pid": os.getpid(), "ts": time.time()})
        prior = _read_json(cache)
        prior_members = (prior.get("members")
                         if isinstance(prior, dict) else None)
        result = _derive(target, cmd_name, argv0, run_fn)
        entry = {"ts": time.time()}
        if result["kind"] == "ok":
            entry["members"] = result["members"]
        else:                                  # "timeout" | "error"
            entry[result["kind"]] = True
            if isinstance(prior_members, list):
                entry["members"] = prior_members   # preserve last good
        _write_atomic(cache, entry)
    finally:
        try:
            os.remove(pid_file)
        except OSError:
            pass


def _main(argv):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--cmd", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--pid", required=True)
    p.add_argument("--argv0", required=True)
    a = p.parse_args(argv)
    run_refresh_child(a.target, a.cmd, a.cache, a.pid, a.argv0)


if __name__ == "__main__":
    _main(sys.argv[1:])
