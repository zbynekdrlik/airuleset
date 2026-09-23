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
refresher child is alive it spawns ONE refresher child (``spawn_refresher`` →
``airuleset.py <cmd> --ops-wait``, its own ``CHILD_TIMEOUT_S`` timeout) that
parses the CLI output (``parse_members``) and atomically writes the member list
into the same file (tmp + ``os.replace``). The child is launched as a transient
``systemd-run --user`` unit so it runs in its OWN cgroup and survives the sweep
process's exit — the watchdog runs as ``api-watchdog.service`` (``Type=oneshot``
+ ``KillMode=control-group``), which would otherwise kill a same-cgroup child the
moment the oneshot exits (``setsid``/``start_new_session`` does NOT escape a
cgroup); a logged Popen fallback covers a non-systemd (test/CI) box. The
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
# stays LIGHT on the hot import path — it is imported (lazily) by
# airuleset._watchdog_ops_wait_fetch on every stale-cache sweep, so it must not
# pull ops_wait_recheck's whole dependency tree in just to read two ints.
REFRESH_TTL_S = 30 * 60           # a good result is fresh for the full TTL
REFRESH_FAIL_TTL_S = 60           # an error/timeout entry re-checks soon
# how long a last-good member set is SERVED while the derivation keeps failing.
# A transient hiccup (minutes) still serves the prior set, but a PERMANENTLY
# broken derivation must eventually go honest (return None) rather than
# re-surfacing days-old W members forever — this restores the pre-1c gh-error
# fail-safe (the old blocking fetch returned None on a gh error). 4 failed
# refresh cycles.
MAX_SERVE_AGE_S = 4 * REFRESH_TTL_S
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


def _serve(entry, timeout_sentinel, now):
    """What the sweep returns from a cache entry: the last good member list when
    present AND not too stale, else the timeout sentinel for a cold timeout, else
    None (no usable result yet).

    `members_ts` is the last SUCCESSFUL derivation time (carried forward across
    preserved failures). When it is older than MAX_SERVE_AGE_S the last-good set
    is NO LONGER served (a permanently-broken derivation goes honest → None /
    sentinel instead of re-surfacing days-old W). A legacy entry with no
    `members_ts` is served (backward compatible)."""
    if isinstance(entry, dict):
        members = entry.get("members")
        if isinstance(members, list):
            mts = entry.get("members_ts")
            if not isinstance(mts, (int, float)) or (now - mts) <= MAX_SERVE_AGE_S:
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
    return _serve(entry, timeout_sentinel, now)


def _refresh_unit_name(cwd):
    """The transient systemd unit name for this repo's refresher — the cache key
    makes it per-repo and stable, so `--collect` cleans a finished run and a
    concurrent same-name start atomically fails ('already exists') as a backstop
    to the pidfile single-flight."""
    return "airuleset-opswait-%s" % _key(cwd)


def _log_spawn(msg):
    """Surface a spawn decision in the watchdog unit's journal (this runs INSIDE
    the sweep process, whose stderr the systemd unit captures) — a detached
    refresh has no other channel. Best-effort; stderr in-process never blocks."""
    sys.stderr.write("refresh-spawn: %s\n" % msg)


def spawn_refresher(cwd, cmd_name, argv0=None, run_fn=None, popen_fn=None,
                    log_fn=None):
    """Spawn ONE refresher child that runs ``<cmd_name> --ops-wait`` for ``cwd``
    and writes the parsed members into the cache file.

    #1067 slice 1c REVIEW: the watchdog runs as ``api-watchdog.service``
    (``Type=oneshot`` + ``KillMode=control-group``), so a plain
    ``Popen(start_new_session=True)`` child stays in the SAME cgroup and systemd
    kills it the moment the oneshot main process exits — the 30-58 s derivation
    never finishes and the cache is never written (``setsid`` does NOT escape a
    cgroup). PRIMARY path: launch the child as a transient ``systemd-run --user``
    unit, which runs in its OWN cgroup and survives the sweep's exit; the user bus
    env for the systemd-run CLIENT comes from the shared ``_xdg_runtime_env``
    (#826), and the derivation's required env (PATH incl. the gh shim, HOME,
    GH_*/GITHUB_* auth) is forwarded INTO the unit via ``--setenv`` (a unit does
    NOT inherit the caller's env — review F1). FALLBACK (systemd-run
    absent or failing — a non-systemd test/CI box, where there is no killing
    oneshot cgroup anyway): the plain detached Popen, logged
    ``refresh-spawn: popen-fallback (<reason>)`` so a box where it would be killed
    is visible. Single-flight/liveness is the pidfile (``refresher_alive``), which
    ``run_refresh_child`` writes regardless of launch method — ONE mechanism.
    ``run_fn``/``popen_fn``/``log_fn`` are injectable seams for tests."""
    if argv0 is None:
        import airuleset
        argv0 = os.path.abspath(airuleset.__file__)
    # the repo root (parent of the `watchdog` package) so `-m
    # watchdog.ops_wait_refresh` resolves in the fresh child interpreter.
    repo_root = os.path.dirname(os.path.abspath(argv0))
    child_argv = [sys.executable, "-m", "watchdog.ops_wait_refresh",
                  "--target", cwd, "--cmd", cmd_name,
                  "--cache", cache_path(cwd), "--pid", pid_path(cwd),
                  "--argv0", argv0]
    log = log_fn or _log_spawn
    if _spawn_via_systemd_run(cwd, repo_root, child_argv, run_fn, log):
        return
    _spawn_via_popen(repo_root, child_argv, popen_fn, log)


# #1067 slice 1c review F1: a `systemd-run --user` transient unit inherits the
# USER MANAGER's environment, NOT the caller's — so the derivation's env is NOT
# forwarded unless we ask for it. The child shells `gh`/`git` by BARE NAME, so it
# needs, and ONLY needs, the following (each justified — least privilege):
#   PATH           resolve `gh` (incl. the ~/.local/bin app-token shim, #888) + git
#   HOME           ~/.claude cache dir + ~/.config/gh
#   XDG_CONFIG_HOME gh's config dir when relocated off ~/.config
#   LANG, LC_ALL   gh/git output encoding (avoids the #1108 UnicodeEncode class)
#   GITHUB_TOKEN   gh honours it for auth
#   GH_* prefix    gh's OWN namespace (GH_TOKEN / GH_CONFIG_DIR / GH_HOST / …) —
#                  all legitimately gh's; forwarding the whole namespace keeps the
#                  child's gh behaving identically to the watchdog's.
# The broad `GITHUB_` prefix and XDG_RUNTIME_DIR were REMOVED (review: narrow to
# need — the unit gets XDG_RUNTIME_DIR from its manager, and CI `GITHUB_*` vars
# are not the derivation's business).
_UNIT_ENV_KEYS = ("PATH", "HOME", "XDG_CONFIG_HOME", "LANG", "LC_ALL",
                  "GITHUB_TOKEN")
_UNIT_ENV_PREFIXES = ("GH_",)


def _unit_setenv_args(source):
    """`--setenv=NAME` args (NAME ONLY — no `=VALUE`) telling systemd-run to
    IMPORT each var's value from its OWN client environment into the unit
    (systemd 255: "When = and VALUE are omitted, the value of the variable with
    the same name in the program environment will be used").

    Emitting NAME-only keeps credential VALUES (GH_TOKEN / GITHUB_TOKEN / any
    GH_* secret) OUT of the systemd-run ARGV — on a shared-stream box (subdev,
    ~15 accounts) `/proc/<pid>/cmdline` / `ps aux` is readable by every other
    account, and a `--setenv=NAME=VALUE` would ALSO persist the value in the
    transient unit's properties (review: cross-account credential leak). The
    values ride the systemd-run client's `env=` dict instead (in-process memory,
    never argv). `source` is the watchdog process env; only vars PRESENT in it are
    forwarded, so every emitted NAME resolves in the client env."""
    return ["--setenv=%s" % k for k in sorted(source)
            if k in _UNIT_ENV_KEYS or k.startswith(_UNIT_ENV_PREFIXES)]


def _spawn_via_systemd_run(cwd, repo_root, child_argv, run_fn, log):
    """Launch the refresher as a transient ``--user`` unit in its own cgroup.
    Returns True when the unit is running (or already running — the unit name is
    the atomic single-flight backstop), False when the caller must fall back.
    The unit captures the child's stdout/stderr to its own journal
    (``journalctl --user -u airuleset-opswait-*``), so a child failure is visible
    there in addition to the ``error`` cache entry it writes."""
    run = run_fn or subprocess.run
    try:
        from cli_filedrop_watchdog import _xdg_runtime_env
        env = _xdg_runtime_env()
    except Exception:
        # xdg helper unavailable: the systemd-run CLIENT still inherits
        # os.environ (a live user-bus in a --user service), and _unit_setenv_args
        # forwards from os.environ below — so this is NOT a forced fall-back.
        env = None
    src = env if env is not None else os.environ
    argv = ["systemd-run", "--user", "--collect", "--quiet",
            "--unit", _refresh_unit_name(cwd),
            "--working-directory", repo_root,
            "--property", "RuntimeMaxSec=%d" % (CHILD_TIMEOUT_S + 30),
            *_unit_setenv_args(src),
            "--", *child_argv]
    try:
        # a short client-call ceiling: systemd-run returns in ms once the unit is
        # started; 10 s bounds a bus hang without re-adding real latency to the
        # sweep (the derivation itself runs detached, off the sweep path). Pass
        # `env=src` (the SAME dict the `--setenv=NAME` list was derived from) so
        # every imported NAME always resolves in the client env — the by-name
        # import can never reference a var absent from what run() receives
        # (review F1 robustness: no implicit src/env divergence).
        r = run(argv, capture_output=True, text=True, timeout=10, env=src)
    except FileNotFoundError:
        log("popen-fallback (systemd-run absent)")
        return False
    except Exception as e:  # noqa: BLE001 — any spawn error falls back, logged
        log("popen-fallback (systemd-run error: %s)" % type(e).__name__)
        return False
    if r.returncode == 0:
        return True
    if "exists" in (r.stderr or "").lower():
        return True   # single-flight: a unit of this name is already running
    log("popen-fallback (systemd-run rc=%d)" % r.returncode)
    return False


def _spawn_via_popen(repo_root, child_argv, popen_fn, log):
    """The cgroup-BOUND fallback (killed by KillMode=control-group under the real
    watchdog unit — used only where systemd-run is unavailable, e.g. CI)."""
    popen = popen_fn or subprocess.Popen
    try:
        popen(child_argv, cwd=repo_root,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        log("popen-fallback spawn failed (OSError)")


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
        prior_members_ts = (prior.get("members_ts")
                            if isinstance(prior, dict) else None)
        result = _derive(target, cmd_name, argv0, run_fn)
        entry = {"ts": time.time()}
        if result["kind"] == "ok":
            entry["members"] = result["members"]
            entry["members_ts"] = entry["ts"]      # last SUCCESSFUL derivation
        else:                                      # "timeout" | "error"
            entry[result["kind"]] = True
            if isinstance(prior_members, list):
                entry["members"] = prior_members   # preserve last good
                if isinstance(prior_members_ts, (int, float)):
                    entry["members_ts"] = prior_members_ts   # carry its age fwd
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
