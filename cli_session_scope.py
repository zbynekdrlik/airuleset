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
"""
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
