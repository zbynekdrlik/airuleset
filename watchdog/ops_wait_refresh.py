"""#1067 slices 1c + 1d — the DETACHED quals refresher (one snapshot per repo).

The watchdog sweep used to run the quals derivation as BLOCKING subprocesses
inside the 90 s sweep. First the ops-wait fetch (`--ops-wait`, 58 s live on
montalu1, slice 1c). Then the backlog (`--count`, 17.7 s) and dispatchable
(`--count-dispatchable`, 19.7 s) fetches inside `goal_lane_sweep`, which cost
30 s of a 39 s sweep (slice 1d). The structural problem is that a slow,
TTL-cached derivation sat on the SYNCHRONOUS sweep path at all.

This leaf moves the derivation OFF the sweep path, and slice 1d makes it ONE
derivation. The sweep READS a per-repo atomic snapshot file through the
non-blocking readers `backlog_count` / `dispatchable` / `fetch_or_refresh`
(ops-wait members). All three go through `read_snapshot`, which spawns ONE
refresher child when the snapshot is stale AND no child is alive. The spawn
is single-flight through the pidfile, the transient unit name and an
in-process guard across the readers of one sweep. The child runs
`airuleset.py <cmd> --snapshot-json` (its own `CHILD_TIMEOUT_S` timeout) and
gets `{open_count, i_members, dispatchable_count, dispatchable_reason,
ops_wait_members}` from ONE `_partition_workable` pass (#367). It then
atomically writes ONE versioned snapshot (tmp + `os.replace`).

The child is launched as a transient `systemd-run --user` unit so it runs in its
OWN cgroup and survives the sweep process's exit. The watchdog runs as
`api-watchdog.service` (`Type=oneshot` + `KillMode=control-group`), which would
otherwise kill a same-cgroup child the moment the oneshot exits
(`setsid`/`start_new_session` does NOT escape a cgroup). A logged Popen
fallback covers a non-systemd (test/CI) box.

The sweep NEVER waits for the derivation. Each reader returns its field from
the last good snapshot. With no snapshot it returns None (undetermined —
backlog is then "unmeasurable", NEVER 0), and the ops-wait reader may return
its timeout sentinel instead (a cold refresh timeout, so the outer
`_cached_member_fetch` backs its fail-TTL off geometrically, slice 1). A child
failure or timeout PRESERVES the prior good fields in the file, so a
transient hiccup never drops a served value. A last-good value older than
`MAX_SERVE_AGE_S` stops being served (it goes honest). Consecutive failures
back the re-spawn off geometrically (`_fail_ttl`), and the child skips the
derivation while the gh-rate budget is in backoff (`rate_hold`). The only
sweep-side cost is the systemd-run CLIENT call on a spawn (≤10 s timeout).

Naming: the module, the `~/.claude/ops-wait-refresh/` dir and the
`airuleset-opswait-*` unit keep their slice-1c names (the design kept the path;
a rename would orphan live caches/units). Since 1d they hold EVERY quals fact.

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

# #1067 slice 1d: ONE snapshot TTL for every consumer — the SHORTEST any of them
# needs (the lane nudge's dispatchable count was cached 5 min; the backlog 10;
# ops-wait 30). The watchdog's outer per-consumer caches keep their own TTLs on
# top; this only bounds how stale the shared snapshot may get before a refresh.
REFRESH_TTL_S = 5 * 60            # a good snapshot is fresh for the full TTL
REFRESH_FAIL_TTL_S = 60           # an error/timeout entry re-checks soon
# how long last-good values are SERVED while the derivation keeps failing. A
# transient hiccup (minutes) still serves the prior snapshot, but a PERMANENTLY
# broken derivation must go honest (None) rather than re-surface stale counts
# and W members forever (the pre-1c fail-safe: a gh error read as None). Six
# refresh windows.
MAX_SERVE_AGE_S = 6 * REFRESH_TTL_S
# the ceiling of the geometric failure backoff (`_fail_ttl`) — slice 1's cap
# (the old 30-min ops-wait TTL), so a permanently slow/broken derivation costs
# at most one run per half hour instead of one per minute.
FAIL_BACKOFF_CAP_S = 30 * 60
# the detached child's own hard timeout (generous vs the ~30-60 s derivation).
CHILD_TIMEOUT_S = 180
# reclaim a pidfile whose recorded child is older than this even if /proc still
# shows the pid (pid reuse / a crash without cleanup) — a belt beyond the child's
# own CHILD_TIMEOUT_S so a wedged/reused pid can never block refresh forever.
CHILD_MAX_AGE_S = CHILD_TIMEOUT_S + 120
# the snapshot schema. A success entry without it (a slice-1c members-only
# cache) is due at once so the backlog/dispatchable readers are not starved.
SNAPSHOT_VERSION = 2
# every field a success writes and a failure carries forward (never partially)
_SNAPSHOT_FIELDS = ("v", "members", "members_ts", "open_count", "i_members",
                    "dispatchable_count", "dispatchable_reason")
# in-process single-flight across the readers of ONE sweep: {cache path: ts}.
# A just-launched child has not written its pidfile yet, so without this the
# 2nd and 3rd reader of the same sweep would each spawn again.
_SPAWNED = {}


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
    # 0o600: the snapshot carries issue numbers + W titles; owner-only even if
    # a box's ~/.claude were ever wider than 0700 (shared-stream subdev).
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def parse_members(stdout):
    """Parse `--ops-wait` TSV stdout into the job-20 member-dict list (None on a
    malformed member line, [] on a clean empty result). The body lives with its
    producer, `cli_quals_snapshot.parse_ops_wait_members` (#1067 slice 1d), where
    `--snapshot-json` parses its own listing. This delegator keeps the ONE
    parser reachable under its historical name."""
    from cli_quals_snapshot import parse_ops_wait_members
    return parse_ops_wait_members(stdout)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def parse_snapshot(stdout):
    """Validate `--snapshot-json` stdout into the snapshot dict, or None when the
    CORE (`open_count`) is malformed (the child then records an `error` and keeps
    the prior good snapshot). The other parts degrade PER FIELD so a failure in
    one never costs the backlog count (#1067 1d review F3/F4):
    `ops_wait_members` may be null (that part failed → the ops-wait reader reads
    undetermined), `dispatchable_count` may be None only WITH a reason (#1021),
    and a malformed `i_members` (no watchdog reader today) is dropped to None."""
    try:
        snap = json.loads(stdout or "")
    except ValueError:
        return None
    if not isinstance(snap, dict):
        return None
    members = snap.get("ops_wait_members")
    dc, dr = snap.get("dispatchable_count"), snap.get("dispatchable_reason")
    if not (_is_int(snap.get("open_count")) and snap["open_count"] >= 0
            and (members is None or (isinstance(members, list) and all(
                isinstance(m, dict) and _is_int(m.get("number"))
                for m in members)))
            and (dr is None or isinstance(dr, str))
            and (_is_int(dc) or (dc is None and dr))):
        return None
    i_members = snap.get("i_members")
    if not (isinstance(i_members, list) and all(map(_is_int, i_members))):
        snap["i_members"] = None
    return snap


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


def _failed(entry):
    """The entry's LATEST attempt did not produce a snapshot."""
    return any(entry.get(k) for k in ("error", "timeout", "rate_hold"))


def _fail_ttl(entry):
    """The re-spawn window after a failed attempt, GEOMETRIC in the consecutive
    failure count (`fail_streak`): 60 s, 120 s, 240 s … capped at
    FAIL_BACKOFF_CAP_S. The slice-1 backoff lived only in the ops-wait outer
    cache; since 1d the backlog/dispatchable readers (60 s outer fail-TTLs) also
    reach `read_snapshot`, so the backoff must live HERE or a derivation that
    keeps timing out re-runs every minute (#1067 1d review F1/F2)."""
    streak = entry.get("fail_streak")
    streak = streak if _is_int(streak) and streak > 0 else 1
    return min(REFRESH_FAIL_TTL_S * 2 ** min(streak - 1, 10), FAIL_BACKOFF_CAP_S)


def _spawn_due(entry, now):
    """Should a refresh be spawned? A missing/typeless entry is always due; a
    SUCCESS entry is fresh for the full TTL, unless it predates the snapshot
    schema (a slice-1c members-only cache → due at once); an entry whose LATEST
    attempt failed (error/timeout/rate_hold) waits its geometric `_fail_ttl`."""
    if not isinstance(entry, dict):
        return True
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)):
        return True
    failed = _failed(entry)
    if not failed and entry.get("v") != SNAPSHOT_VERSION:
        return True
    ttl = _fail_ttl(entry) if failed else REFRESH_TTL_S
    return (now - ts) >= ttl


def _serve(entry, timeout_sentinel, now):
    """What the ops-wait reader returns from a snapshot entry: the last good
    member list when present AND not too stale, else the timeout sentinel for a
    cold timeout, else None (no usable result yet).

    `members_ts` is the last SUCCESSFUL derivation time (carried forward across
    preserved failures). When it is older than MAX_SERVE_AGE_S the last-good set
    is NO LONGER served (a permanently-broken derivation goes honest → None /
    sentinel instead of re-surfacing stale W). A legacy entry with no
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


def _good_snapshot(entry, now):
    """The entry when it is a v2 snapshot whose last SUCCESSFUL derivation is
    within MAX_SERVE_AGE_S, else None — the backlog/dispatchable readers serve
    nothing from a legacy, never-succeeded or too-stale entry."""
    if not isinstance(entry, dict) or entry.get("v") != SNAPSHOT_VERSION:
        return None
    mts = entry.get("members_ts")
    if not isinstance(mts, (int, float)) or (now - mts) > MAX_SERVE_AGE_S:
        return None
    return entry


def read_snapshot(cwd, cmd_name, argv0=None, now=None, spawn_fn=None,
                  alive_fn=None):
    """NON-BLOCKING: read the per-repo snapshot entry (or None) and, when it is
    due AND no refresher is alive AND this process did not already spawn one
    for it, spawn ONE detached refresher. Never runs the derivation itself —
    the ONE entry point every reader shares (single-flight, #1067 1d)."""
    now = time.time() if now is None else now
    path = cache_path(cwd)
    entry = _read_json(path)
    alive = alive_fn or refresher_alive
    spawned = _SPAWNED.get(path)
    recently = (isinstance(spawned, (int, float))
                and 0 <= now - spawned < CHILD_MAX_AGE_S)
    if _spawn_due(entry, now) and not recently and not alive(cwd):
        _SPAWNED[path] = now
        (spawn_fn or spawn_refresher)(cwd, cmd_name, argv0)
    return entry


def fetch_or_refresh(cwd, cmd_name, timeout_sentinel, argv0=None, now=None,
                     spawn_fn=None, alive_fn=None):
    """NON-BLOCKING ops-wait member reader: the last good members, None, or
    ``timeout_sentinel`` (see `_serve`), after `read_snapshot`."""
    now = time.time() if now is None else now
    entry = read_snapshot(cwd, cmd_name, argv0, now, spawn_fn, alive_fn)
    return _serve(entry, timeout_sentinel, now)


def backlog_count(cwd, cmd_name, argv0=None, now=None, spawn_fn=None,
                  alive_fn=None):
    """NON-BLOCKING backlog reader: the snapshot's `open_count` (the same number
    `--count` prints — a real 0 passed the #181 refusal inside the command), or
    None when there is no good snapshot — unmeasurable, NEVER a guessed 0.

    Staleness (documented trade-off): a later REFUSED/failed derivation keeps
    serving the last good count until it is MAX_SERVE_AGE_S old (the pre-1d
    blocking fetch read a refusal as None at once). The risk direction is a
    stale POSITIVE count, never a false 0."""
    now = time.time() if now is None else now
    good = _good_snapshot(
        read_snapshot(cwd, cmd_name, argv0, now, spawn_fn, alive_fn), now)
    count = good.get("open_count") if good else None
    return count if _is_int(count) else None


def dispatchable(cwd, cmd_name, argv0=None, now=None, spawn_fn=None,
                 alive_fn=None):
    """NON-BLOCKING dispatchable reader, in the lane nudge's fetch shape:
    `[{"count": N, "reason": r}]`; `[{"count": None, "reason": r}]` for an
    unmeasurable count WITH a reason (#1021); None when there is no good
    snapshot or an unmeasurable count carries no reason."""
    now = time.time() if now is None else now
    good = _good_snapshot(
        read_snapshot(cwd, cmd_name, argv0, now, spawn_fn, alive_fn), now)
    if not good:
        return None
    count, reason = good.get("dispatchable_count"), good.get("dispatchable_reason")
    if not _is_int(count):
        return [{"count": None, "reason": reason}] if reason else None
    return [{"count": count, "reason": reason}]


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
    """Spawn ONE refresher child that runs ``<cmd_name> --snapshot-json`` for
    ``cwd`` and writes the validated snapshot into the cache file.

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
    """Run ``<cmd_name> --snapshot-json`` for ``target`` with the child's own
    hard timeout; return ``(returncode, stdout)``. Raises ``_Timeout`` on
    overrun."""
    try:
        r = subprocess.run(
            [sys.executable, argv0, cmd_name, "--snapshot-json"],
            cwd=target, capture_output=True, text=True, timeout=CHILD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _Timeout()
    return r.returncode, r.stdout


def _default_gh_backoff():
    """The watchdog registry's own gh-rate hold signal (#1040/#1041): > 0 while
    either gh resource is under its backoff threshold. Reads the CACHED status
    only (the sweep's gh_rate_fetch keeps it fresh) — the probe never spends a
    gh call itself. Fail-open → 0."""
    try:
        import cli_gh_rate
        return cli_gh_rate.current_gh_backoff(status=cli_gh_rate._load_cache())
    except Exception as e:  # noqa: BLE001 — never block a refresh on the probe
        sys.stderr.write("refresh-child: gh-rate probe failed (%s)\n"
                         % type(e).__name__)
        return 0


def _derive(target, cmd_name, argv0, run_fn=None, backoff_fn=None):
    """Run the ONE derivation; classify the outcome as
    ok/timeout/error/rate_hold. The detached child escapes the watchdog
    registry's gh-rate hold (it runs outside the sweep), so it consults the
    same signal itself and SKIPS the gh-heavy derivation while the shared
    budget is low (#1067 1d review F3) — the prior good snapshot stays served."""
    backoff = (backoff_fn or _default_gh_backoff)()
    if _is_int(backoff) and backoff > 0:
        return {"kind": "rate_hold"}
    run = run_fn or _default_run
    try:
        rc, out = run(target, cmd_name, argv0)
    except _Timeout:
        return {"kind": "timeout"}
    if rc != 0:
        return {"kind": "error"}
    snap = parse_snapshot(out)
    if snap is None:
        return {"kind": "error"}   # malformed output -> undetermined
    return {"kind": "ok", "snap": snap}


def run_refresh_child(target, cmd_name, cache, pid_file, argv0, run_fn=None,
                      backoff_fn=None):
    """The detached child body: record the pidfile, run the derivation (its own
    timeout), and atomically write ONE snapshot to ``cache`` — PRESERVING every
    prior good field (with its `members_ts` age) on a failure/timeout so a
    transient hiccup never drops a served value. Removes the pidfile at exit no
    matter what."""
    try:
        _write_atomic(pid_file, {"pid": os.getpid(), "ts": time.time()})
        prior = _read_json(cache)
        prior = prior if isinstance(prior, dict) else {}
        result = _derive(target, cmd_name, argv0, run_fn, backoff_fn)
        entry = {"ts": time.time()}
        if result["kind"] == "ok":
            snap = result["snap"]
            entry.update({
                "v": SNAPSHOT_VERSION,
                "members": snap["ops_wait_members"],   # None = that part failed
                "members_ts": entry["ts"],        # last SUCCESSFUL derivation
                "open_count": snap["open_count"],
                "i_members": snap["i_members"],
                "dispatchable_count": snap["dispatchable_count"],
                "dispatchable_reason": snap["dispatchable_reason"]})
        else:                              # "timeout" | "error" | "rate_hold"
            entry[result["kind"]] = True
            streak = prior.get("fail_streak") if _failed(prior) else 0
            entry["fail_streak"] = (streak if _is_int(streak) else 0) + 1
            entry.update({k: prior[k] for k in _SNAPSHOT_FIELDS
                          if k in prior})         # last good, age carried fwd
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
