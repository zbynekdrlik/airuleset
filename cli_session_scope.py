"""#1224: every Claude session runs in its own memory-capped scope, and its
tools are the first thing the kernel OOM-kills.

dev1 (4 cores, 7.5 GB) froze three times in three weeks. Each time ONE
session's Bash tool child grew to 2.6-2.9 GB: shellcheck, a `python3 -` over a
598 MB transcript, local mutmut. The box went into global swap thrash. Nothing
capped a session: its claude process and every tool child sat in the tmux
server's uncapped unit.

Both launchers (`cli_claude_scripts`) are the ONE path every launch takes.
`wrap()` swaps each of their `exec claude` sites for `_airuleset_exec_claude`:

* On a box in ``SCOPE_HOSTS`` it execs claude through
  ``systemd-run --user --scope``.
  - ``MemoryMax`` is ``MAX_PERCENT`` of MemTotal, never below ``FLOOR_KB``;
    ``MemorySwapMax`` is ``SWAP_PERCENT``.
  - ``--scope`` execs in place: the pane pid stays the same claude process
    for tmux and the watchdog.
  - A runaway meets its own session's ceiling and the kernel memcg OOM; the
    rest of the box never reclaims for it.
  - ``OOMPolicy=continue``: systemd's default ``stop`` ends the WHOLE scope,
    claude included, when the kernel kills one tool (live on dev1).
* A probe (``systemd-run … -- true``) runs first. When the user manager is
  unreachable, or on any other box, the launch is a plain ``exec claude``,
  so a launch is never blocked (fail-open, one stderr line).
* Every launch exports ``BASH_ENV`` = ``TOOL_OOM_ENV_NAME`` in ``~/.claude``.
  Claude Code runs each Bash tool call as a non-interactive
  ``/bin/bash -c "source <snapshot> …"``, which sources ``BASH_ENV`` first.
  The file raises that shell's ``oom_score_adj`` to ``TOOL_OOM_SCORE_ADJ``,
  and the tool's children inherit it. A process may lower its value only down
  to ``oom_score_adj_min``, which is 0 for a session, so claude cannot shield
  itself; raising the tools is the lever. Inside the scope, then, the kernel
  kills the tool before claude itself. An earlier ``BASH_ENV`` is chained, not
  dropped. MCP servers started through a bash wrapper get 800 too and die
  before claude, which restarts them.

Known consequences (review, 2026-10-13):

* Everything a session launches inherits its scope, ``setsid nohup`` jobs
  included. A long build or test run gets the same ceiling, minus claude's own
  RSS, and is SIGKILLed (rc 137) instead of freezing the box. A job that
  truly needs more starts its own ``systemd-run --user --scope -p MemoryMax=…``.
* ``systemd-run`` execs claude with a RESOLVED argv[0]
  (``/home/<u>/.local/bin/claude``). A cmdline matcher must accept a path
  prefix (``(^|/)claude``); ``comm`` and tmux's ``pane_current_command`` stay
  ``claude``.
* A fallback to uncapped prints one stderr line and appends one line to
  ``~/.claude/airuleset-session-scope.log``.

The framework is systemd's cgroup v2 resource control and the kernel memcg OOM
killer, the same mechanism ``cli_resource_guards`` applies per user on
shared-stream boxes. Nothing here polls or kills.

#1245: a cap placed at launch never reached a session that was already running
(dev1's 5th freeze: a 5.6 GB tool in a session from 11.10.). Install therefore
ADOPTS every running uncapped claude of this account, with its descendants, into
its own scope with the same caps (``adopt_unscoped_sessions``).
"""
import os
import re

# The workstations a runaway froze (#1224); data, so another box is one entry.
SCOPE_HOSTS = ("dev1", "dev2")
MAX_PERCENT = 40
SWAP_PERCENT = 20
HIGH_PERCENT = 85      # MemoryHigh, of MemoryMax: reclaim throttles before a kill
FLOOR_KB = 2 * 1024 * 1024
TOOL_OOM_SCORE_ADJ = 800
TOOL_OOM_ENV_NAME = "airuleset-tool-oom.sh"

_HELPER = r"""# #1224 (cli_session_scope): every Claude session runs in its OWN
# memory-capped systemd scope on a declared workstation, and every Bash tool it
# runs is OOM-killed before Claude itself. Fails open to a plain exec.
_airuleset_exec_claude() {
  local _oom="$HOME/.claude/__ENV__" _log="$HOME/.claude/airuleset-session-scope.log"
  local _meminfo="${AIRULESET_MEMINFO:-/proc/meminfo}"
  unset AIRULESET_MEMINFO
  if [ -r "$_oom" ]; then
    if [ -n "${BASH_ENV:-}" ] && [ "$BASH_ENV" != "$_oom" ]; then
      export AIRULESET_PREV_BASH_ENV="$BASH_ENV"
    fi
    export BASH_ENV="$_oom"
  fi
  case "$(hostname -s 2>/dev/null || true)" in
    __HOSTS__)
      local _kb _max _why
      _kb=$(awk '/^MemTotal:/ {print $2; exit}' "$_meminfo" 2>/dev/null) || _kb=""
      if [ -z "$_kb" ]; then
        _why="could not read MemTotal from $_meminfo"
      elif ! command -v systemd-run >/dev/null 2>&1; then
        _why="no systemd-run"
      else
        _max=$(( _kb * __MAX__ / 100 )); [ "$_max" -ge __FLOOR__ ] || _max=__FLOOR__
        local _props=(-p "MemoryMax=${_max}K" -p "MemoryHigh=$(( _max * __HIGH__ / 100 ))K"
                      -p "MemorySwapMax=$(( _kb * __SWAP__ / 100 ))K" -p OOMPolicy=continue)
        # the probe carries the SAME properties: a refused one never kills the pane
        if systemd-run --user --scope --quiet "${_props[@]}" -- true >/dev/null 2>&1; then
          exec systemd-run --user --scope --quiet "${_props[@]}" -- claude "$@"
        fi
        _why="systemd-run --user --scope refused (no user manager or bus?)"
      fi
      echo "airuleset: $_why, starting Claude uncapped (#1224)" >&2
      { printf '%s pid=%s uncapped: %s\n' "$(date -u +%FT%TZ)" "$$" "$_why" >> "$_log"; } 2>/dev/null || :
      ;;
  esac
  exec claude "$@"
}
"""

_EXEC_CLAUDE_RE = re.compile(r"\bexec claude\b")


def render_helper():
    """The bash function both launchers call instead of ``exec claude``."""
    return (_HELPER.replace("__ENV__", TOOL_OOM_ENV_NAME)
            .replace("__HOSTS__", "|".join(SCOPE_HOSTS))
            .replace("__MAX__", str(MAX_PERCENT))
            .replace("__SWAP__", str(SWAP_PERCENT))
            .replace("__HIGH__", str(HIGH_PERCENT))
            .replace("__FLOOR__", str(FLOOR_KB)))


def wrap(script):
    """``script`` with every ``exec claude`` routed through the helper, which
    is defined right after the shebang line (before any of them can run)."""
    body = _EXEC_CLAUDE_RE.sub("_airuleset_exec_claude", script)
    head, nl, rest = body.partition("\n")
    if not head.startswith("#!"):
        return render_helper() + body
    return head + nl + render_helper() + rest


def render_tool_oom_env():
    """The ``BASH_ENV`` file every Bash tool call of a session sources."""
    return ("# airuleset #1224 (managed by `airuleset.py install`; do not edit).\n"
            "# Sourced through BASH_ENV by every non-interactive bash a Claude\n"
            "# session starts (its Bash tool calls): the kernel OOM-kills a\n"
            "# runaway tool before Claude itself. Raising is unprivileged.\n"
            "# The group keeps a refused write silent (hooks print on stderr).\n"
            "{ echo %d > /proc/self/oom_score_adj; } 2>/dev/null || :\n"
            "if [ -n \"${AIRULESET_PREV_BASH_ENV:-}\" ] && "
            "[ -r \"$AIRULESET_PREV_BASH_ENV\" ]; then\n"
            "  . \"$AIRULESET_PREV_BASH_ENV\"\n"
            "fi\n" % TOOL_OOM_SCORE_ADJ)


def write_tool_oom_env(directory):
    """Write the ``BASH_ENV`` file into ``directory`` (``~/.claude``)."""
    import os
    path = directory / TOOL_OOM_ENV_NAME
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(render_tool_oom_env())
    os.replace(tmp, path)       # a live tool call never sources a half-written file
    return path


def _caps_kb(mem_kb):
    """(MemoryMax, MemoryHigh, MemorySwapMax) in KiB: the launcher's bash math."""
    mx = max(mem_kb * MAX_PERCENT // 100, FLOOR_KB)
    return mx, mx * HIGH_PERCENT // 100, mem_kb * SWAP_PERCENT // 100


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _procs(proc_root):
    """{pid: (comm, ppid, uid, cgroup v2 path)} for every readable process."""
    out = {}
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        d = os.path.join(proc_root, name)
        stat, status, cg = _read(d + "/stat"), _read(d + "/status"), _read(d + "/cgroup")
        if not (stat and status and cg):
            continue
        try:
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (ValueError, IndexError):
            continue
        uid = next((int(ln.split()[1]) for ln in status.splitlines()
                    if ln.startswith("Uid:")), None)
        path = next((ln[3:] for ln in cg.splitlines() if ln.startswith("0::")), None)
        out[int(name)] = ((_read(d + "/comm") or "").strip(), ppid, uid, path)
    return out


def _members(procs, root):
    """``root`` + every descendant in ROOT'S cgroup with ROOT'S uid. A child
    already in its own scope (or a setuid one) stays where it is."""
    kids = {}
    for pid, (_c, ppid, _u, _p) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [root], [root]
    while todo:
        for k in kids.get(todo.pop(), ()):
            if procs[k][2] == procs[root][2] and procs[k][3] == procs[root][3]:
                out.append(k)
            todo.append(k)
    return out


def _under_claude(procs, pid):
    """True when an ancestor is a claude of the same uid in the same cgroup:
    ``pid`` then moves with that session (pids recycle, so order proves nothing)."""
    me, seen, p = procs[pid], set(), procs[pid][1]
    while p in procs and p not in seen:
        seen.add(p)
        comm, ppid, uid, path = procs[p]
        if comm == "claude" and uid == me[2] and path == me[3]:
            return True
        p = ppid
    return False


def _busctl_argv(unit, pids, caps_kb):
    """StartTransientUnit with PIDs: the call ``systemd-run --scope`` makes,
    for processes that already run."""
    mx, hi, sw = (k * 1024 for k in caps_kb)
    return (["busctl", "--user", "call", "org.freedesktop.systemd1",
             "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
             "StartTransientUnit", "ssa(sv)a(sa(sv))", unit, "fail", "6",
             "Description", "s", "airuleset #1245: adopted Claude session %d" % pids[0],
             "PIDs", "au", str(len(pids))] + [str(p) for p in pids]
            + ["MemoryMax", "t", str(mx), "MemoryHigh", "t", str(hi),
               "MemorySwapMax", "t", str(sw), "OOMPolicy", "s", "continue", "0"])


def _run(argv, env):
    import subprocess
    try:
        r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


def adopt_unscoped_sessions(*, hostname=None, uid=None, proc_root="/proc",
                            cgroup_root="/sys/fs/cgroup", meminfo_text=None,
                            run=None, env=None, sleep=None):
    """Move every running claude of this uid whose cgroup is uncapped
    (``memory.max`` = max), with its descendants, into its own
    ``airuleset-session-<pid>.scope`` with the launcher's caps. The session is
    not restarted; the pid and the pane stay. Returns the install lines.

    * Off ``SCOPE_HOSTS``: nothing, no line.
    * Charges already made stay with the old cgroup (cgroup v2 moves none), so
      the cap applies to what the session allocates from now on, its tools'
      included; no immediate OOM.
    * A claude in a logind ``session-N.scope`` (an ssh login) moves too: the
      user manager moved one out on dev1 (systemd 255, live probe).
    * The descendants get the tool ``oom_score_adj``. FUTURE tool calls of an
      adopted session keep claude's own value (its env predates ``BASH_ENV``),
      so the kernel picks the biggest process in the scope.
    * Every failure is a ``WARN … DEFECT (#1245)`` line, never an exception."""
    import socket
    import time
    host = hostname if hostname is not None else socket.gethostname().split(".")[0]
    if host not in SCOPE_HOSTS:
        return []
    if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return ["session-scope: skipped, the real busctl is refused under a test run (#1245)"]
    uid = os.getuid() if uid is None else uid
    m = re.search(r"^MemTotal:\s+(\d+)", meminfo_text if meminfo_text is not None
                  else (_read("/proc/meminfo") or ""), re.M)
    if not m:
        return ["session-scope: WARN cannot read MemTotal, running sessions "
                "not adopted — DEFECT (#1245)"]
    caps = _caps_kb(int(m.group(1)))
    run, sleep = run or _run, sleep or time.sleep
    env = dict(os.environ if env is None else env)
    if not env.get("XDG_RUNTIME_DIR"):          # an ssh push may carry none
        env["XDG_RUNTIME_DIR"] = "/run/user/%d" % uid
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=%s/bus" % env["XDG_RUNTIME_DIR"]

    def mem_max(path):
        return (_read(cgroup_root + path + "/memory.max") or "max").strip()

    lines, adopted, capped, moved = [], 0, 0, set()
    procs = _procs(proc_root)
    for pid in sorted(procs):
        if pid not in procs or pid in moved:   # gone, or moved with its parent
            continue
        comm, _pp, puid, path = procs[pid]
        if comm != "claude" or puid != uid or not path or _under_claude(procs, pid):
            continue
        if mem_max(path) != "max":
            capped += 1
            continue
        try:
            label = os.path.basename(os.readlink("%s/%d/cwd" % (proc_root, pid)))
        except OSError:
            label = "?"
        where = path.rsplit("/", 1)[-1]
        unit = "airuleset-session-%d.scope" % pid
        err = ""
        for attempt in (1, 2):
            members = _members(procs, pid)
            rc, _out, err = run(_busctl_argv(unit, members, caps), env)
            if rc == 0 or attempt == 2 or "No such process" not in err:
                break
            procs = _procs(proc_root)       # a child exited between list and call
            if pid not in procs:
                break
        if rc == 0:
            for _ in range(10):
                now = (_read("%s/%d/cgroup" % (proc_root, pid)) or "").strip()[3:]
                if now.endswith("/" + unit) and mem_max(now) != "max":
                    break
                sleep(0.2)
            else:
                rc, err = 1, "the call returned but the pid is still in %s" % where
        if rc != 0:
            lines.append("session-scope: WARN pid %d (%s) still uncapped in %s: %s "
                         "— DEFECT (#1245)" % (pid, label, where, err.strip()[:160]))
            continue
        for k in members[1:]:
            try:
                with open("%s/%d/oom_score_adj" % (proc_root, k), "w") as fh:
                    fh.write("%d\n" % TOOL_OOM_SCORE_ADJ)
            except OSError:
                pass                        # it exited; nothing left to rank
        adopted += 1
        moved.update(members)
        lines.append("session-scope: adopted pid %d (%s) + %d child process(es) into "
                     "%s (MemoryMax=%dK, OOMPolicy=continue)"
                     % (pid, label, len(members) - 1, unit, caps[0]))
    lines.append("session-scope: %d adopted, %d already capped" % (adopted, capped))
    return lines
