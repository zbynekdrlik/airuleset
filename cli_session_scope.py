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
      # never chain another account's oom file (#1256: it recursed into itself)
      case "$BASH_ENV" in */__ENV__) ;; *) export AIRULESET_PREV_BASH_ENV="$BASH_ENV";; esac
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
            "# #1256: never source an oom file (itself or another HOME's): it\n"
            "# would recurse until bash crashes.\n"
            "case \"${AIRULESET_PREV_BASH_ENV:-}\" in\n"
            "  ''|*/%s) ;;\n"
            "  *) [ -r \"$AIRULESET_PREV_BASH_ENV\" ] && . \"$AIRULESET_PREV_BASH_ENV\" ;;\n"
            "esac\n" % (TOOL_OOM_SCORE_ADJ, TOOL_OOM_ENV_NAME))


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


YOUNG_S = 2                 # a younger child (a hook, a statusline) may exit mid-call
LINGER_DIR = "/var/lib/systemd/linger"


def _procs(proc_root):
    """{pid: (comm, ppid, uid, cgroup v2 path, start ticks, VmRSS kB)}."""
    out = {}
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        d = os.path.join(proc_root, name)
        stat, status, cg = _read(d + "/stat"), _read(d + "/status"), _read(d + "/cgroup")
        if not (stat and status and cg):
            continue
        try:
            fields = stat[stat.rindex(")") + 2:].split()
            ppid = int(fields[1])
        except (ValueError, IndexError):
            continue
        start = int(fields[19]) if len(fields) > 19 and fields[19].isdigit() else None
        tag = {ln.split(":")[0]: ln.split()[1:] for ln in status.splitlines() if ":" in ln}
        uid = int(tag["Uid"][0]) if tag.get("Uid") else None
        rss = int(tag["VmRSS"][0]) if tag.get("VmRSS") else None
        path = next((ln[3:] for ln in cg.splitlines() if ln.startswith("0::")), None)
        out[int(name)] = ((_read(d + "/comm") or "").strip(), ppid, uid, path, start, rss)
    return out


def _members(procs, root, uptime):
    """``root`` + every descendant in ROOT'S cgroup with ROOT'S uid that is at
    least ``YOUNG_S`` old (unknown age counts as old). A child already in its
    own scope, a setuid one, or a short-lived one stays where it is."""
    kids, clk = {}, os.sysconf("SC_CLK_TCK")
    for pid, rec in procs.items():
        kids.setdefault(rec[1], []).append(pid)
    out, todo = [root], [root]
    while todo:
        for k in kids.get(todo.pop(), ()):
            rec, top = procs[k], procs[root]
            young = (uptime is not None and rec[4] is not None
                     and uptime - rec[4] / clk < YOUNG_S)
            if rec[2] == top[2] and rec[3] == top[3] and not young:
                out.append(k)
            todo.append(k)
    return out


def _under_claude(procs, pid):
    """True when an ancestor is a claude of the same uid in the same cgroup:
    ``pid`` then moves with that session (pids recycle, so order proves nothing)."""
    me, seen, p = procs[pid], set(), procs[pid][1]
    while p in procs and p not in seen:
        seen.add(p)
        rec = procs[p]
        if rec[0] == "claude" and rec[2] == me[2] and rec[3] == me[3]:
            return True
        p = rec[1]
    return False


def _busctl_argv(unit, pids, caps_kb):
    """StartTransientUnit with PIDs: the call ``systemd-run --scope`` makes,
    for processes that already run."""
    mx, hi, sw = (k * 1024 for k in caps_kb)
    return (["busctl", "--user", "call", "org.freedesktop.systemd1",
             "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
             "StartTransientUnit", "ssa(sv)a(sa(sv))", unit, "fail", "7",
             "Description", "s", "airuleset #1245: adopted Claude session %d" % pids[0],
             "PIDs", "au", str(len(pids))] + [str(p) for p in pids]
            + ["MemoryMax", "t", str(mx), "MemoryHigh", "t", str(hi),
               "MemorySwapMax", "t", str(sw), "OOMPolicy", "s", "continue",
               # a failed job's scope is unloaded, so the next push can retry
               "CollectMode", "s", "inactive-or-failed", "0"])


def _run(argv, env):
    import subprocess
    try:
        r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


def adopt_unscoped_sessions(*, hostname=None, uid=None, user=None, proc_root="/proc",
                            cgroup_root="/sys/fs/cgroup", linger_dir=LINGER_DIR,
                            meminfo_text=None, run=None, env=None, sleep=None):
    """Move every running claude of this uid whose cgroup is uncapped
    (``memory.max`` = max), with its descendants, into its own
    ``airuleset-session-<pid>.scope`` with the launcher's caps. The session is
    not restarted; the pid and the pane stay. Returns the install lines.

    * Off ``SCOPE_HOSTS``: nothing, no line. Under pytest the real busctl is
      refused, so a full ``cmd_install`` test never moves live sessions.
    * Charges already made stay with the old cgroup (cgroup v2 moves none), so
      nothing is OOM-killed at the move; pages claude frees and touches again
      are charged to the scope over time, as for a launcher session.
    * A claude whose RSS is already past half of MemoryHigh is skipped (WARN):
      capping it now could throttle the owner's pane. Its next launch caps it.
    * A claude in a logind ``session-N.scope`` (an ssh login) moves only when
      the account lingers. The scope lives under ``user@<uid>.service``, which
      without linger stops after the last login and would kill the session.
    * Children younger than ``YOUNG_S`` stay behind: a hook that exits between
      the scan and the call fails the whole call with ``No such process``.
      Such a call is retried with a fresh scan, up to 3 attempts.
    * The descendants get the tool ``oom_score_adj``. FUTURE tool calls of an
      adopted session keep claude's own value (its env predates ``BASH_ENV``),
      so the kernel picks the biggest process in the scope.
    * Every failure is a ``WARN … DEFECT (#1245)`` line, never an exception. A
      transport failure (busctl missing or hung: no ``Call failed:`` answer)
      stops the loop at once; a refused call costs only its own session."""
    import socket
    import time
    host = hostname if hostname is not None else socket.gethostname().split(".")[0]
    if host not in SCOPE_HOSTS:
        return []
    if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return ["session-scope: skipped, the real busctl is refused under a test run (#1245)"]
    uid = os.getuid() if uid is None else uid
    if user is None:
        import pwd
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            user = str(uid)
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
    user_mgr = "/user.slice/user-%d.slice/user@%d.service/" % (uid, uid)
    lingers = os.path.exists(os.path.join(linger_dir, user))

    def mem_max(path):
        return (_read(cgroup_root + path + "/memory.max") or "max").strip()

    def uptime():
        try:
            return float((_read(proc_root + "/uptime") or "").split()[0])
        except (IndexError, ValueError):
            return None

    lines, adopted, capped, cands = [], 0, 0, []
    procs = _procs(proc_root)
    for pid in sorted(procs):
        comm, _pp, puid, path = procs[pid][:4]
        if comm != "claude" or puid != uid or not path or _under_claude(procs, pid):
            continue
        if mem_max(path) != "max":
            capped += 1
        else:
            cands.append(pid)
    def exited(pid, label):
        lines.append("session-scope: pid %d (%s) exited before the move" % (pid, label))

    for i, pid in enumerate(cands):
        if pid not in procs:                # gone in a retry's re-scan
            exited(pid, "?")
            continue
        path, rss = procs[pid][3], procs[pid][5]
        try:
            label = os.path.basename(os.readlink("%s/%d/cwd" % (proc_root, pid)))
        except OSError:
            label = "?"
        where = path.rsplit("/", 1)[-1]
        if not path.startswith(user_mgr) and not lingers:
            lines.append("session-scope: WARN pid %d (%s) uncapped in %s: %s does not "
                         "linger, so a scope under the user manager could die with it; "
                         "capped at its next launch (#1245)" % (pid, label, where, user))
            continue
        if rss is not None and rss > caps[1] // 2:
            lines.append("session-scope: WARN pid %d (%s) uncapped: claude RSS %dM is "
                         "past half of MemoryHigh (%dM); capped at its next launch (#1245)"
                         % (pid, label, rss // 1024, caps[1] // 2048))
            continue
        unit = "airuleset-session-%d.scope" % pid
        for _attempt in range(3):
            members = _members(procs, pid, uptime())
            rc, _out, err = run(_busctl_argv(unit, members, caps), env)
            if rc == 0 or "No such process" not in err:
                break
            procs = _procs(proc_root)       # a child exited between scan and call
            if pid not in procs:
                break
        if pid not in procs or not os.path.exists("%s/%d" % (proc_root, pid)):
            exited(pid, label)
            continue
        if rc != 0 and not err.lstrip().startswith("Call failed:"):
            lines.append("session-scope: WARN pid %d (%s) still uncapped in %s: %s "
                         "— DEFECT (#1245)" % (pid, label, where, err.strip()[:160]))
            if cands[i + 1:]:
                lines.append("session-scope: WARN pid(s) %s not tried after the failure "
                             "above (#1245)" % ", ".join(map(str, cands[i + 1:])))
            break
        if rc == 0:
            for _ in range(10):
                now = (_read("%s/%d/cgroup" % (proc_root, pid)) or "").strip()[3:]
                if now.endswith("/" + unit) and mem_max(now) != "max":
                    break
                sleep(0.2)
            else:
                rc, err = 1, "the call returned but the pid is still in %s" % where
        if not os.path.exists("%s/%d" % (proc_root, pid)):
            exited(pid, label)
            continue
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
        def names(pids):
            return list(dict.fromkeys(procs[k][0] for k in pids if k in procs))[:6]
        young = [k for k in _members(procs, pid, None) if k not in members]
        lines.append("session-scope: adopted pid %d (%s, claude RSS %s) + %d child "
                     "process(es) %s into %s (MemoryMax=%dK, OOMPolicy=continue)%s"
                     % (pid, label, "%dM" % (rss // 1024) if rss is not None else "?",
                        len(members) - 1, names(members[1:]), unit, caps[0],
                        "; %d under %d s left behind %s" % (len(young), YOUNG_S, names(young))
                        if young else ""))
    lines.append("session-scope: %d adopted, %d already capped" % (adopted, capped))
    return lines
