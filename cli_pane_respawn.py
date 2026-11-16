"""#1310 — install respawns a project account's tmux pane that started before
the ``claude()`` launcher existed.

The root bootstrap (``cli_account_bootstrap._render_project_step``, step 10)
starts the account's tmux session with ``exec bash -l`` BEFORE the account's
first ``airuleset.py install`` writes the ``claude()`` launcher into
``~/.bashrc`` (``cli_bashrc_appliers.apply_ultracode_launcher``). Bash parses
``~/.bashrc`` once at start, so such a pane's ``claude`` stays the bare binary:
no bypass mode, no ``-c``, no peer channel (mikrotik-pz, 12.11.2026: the owner
had to have the pane respawned by hand).

``maybe_respawn_stale_project_panes`` runs as the last tmux-related step of
``cmd_install`` and respawns a pane ONLY when every one of these holds:

* this process is a live project account with a declared ``tmux_session``;
* the launcher stamp ``~/.claude/airuleset-launcher-since`` exists;
* nobody is attached to the session (a human at the prompt may hold
  half-typed text);
* the pane process is a LOGIN shell (``cli_account_verify._is_login``, the
  predicate ``accounts verify`` uses), it has NO child process (a running
  claude, an editor, a dev server is never touched), and it started before the
  stamp (the same "older than the launcher" rule ``accounts verify`` fails on).

Right before ``tmux respawn-pane -k`` it re-reads the pane pid and its children
and skips a pane that changed. Every decision is one printed line. A pane it
leaves alone keeps failing ``accounts verify`` with the existing respawn hint.

Stdlib only; ``run`` (the tmux call) and ``proc`` (the ``/proc`` root) are
injectable. The default runner is refused under pytest so a test can never
drive the real tmux server of whoever runs the suite (#1196 pattern).
"""
import os
import subprocess
import sys

LOGIN_SHELL_CMD = "exec bash -l"     # == cli_project_toolchain.TMUX_LOGIN_SHELL
_TMUX_TIMEOUT_S = 10


def _run(argv):
    """Run one tmux command; ``(rc, stdout)``."""
    r = subprocess.run(argv, capture_output=True, text=True,
                       timeout=_TMUX_TIMEOUT_S, check=False)
    return r.returncode, r.stdout


def _children(pid, proc):
    """Child pids of ``pid`` from every thread's ``children`` file."""
    out = []
    task_dir = os.path.join(proc, str(pid), "task")
    for tid in os.listdir(task_dir):
        with open(os.path.join(task_dir, tid, "children")) as f:
            out += f.read().split()
    return out


def _cmdline(pid, proc):
    with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
        return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()


def _start_epoch(pid, proc, clk_tck):
    """Wall-clock start of ``pid``: boot time + field 22 of its ``stat``."""
    with open(os.path.join(proc, str(pid), "stat")) as f:
        fields = f.read().rsplit(")", 1)[1].split()
    with open(os.path.join(proc, "stat")) as f:
        btime = next(int(ln.split()[1]) for ln in f if ln.startswith("btime "))
    return btime + int(fields[19]) / clk_tck


def _pane_pid(pane_id, run):
    rc, out = run(["tmux", "display-message", "-p", "-t", pane_id, "#{pane_pid}"])
    return out.strip() if rc == 0 else ""


def _why_kept(pid, since, proc, clk_tck):
    """None when the pane may be respawned, else the reason it is kept."""
    import cli_account_verify
    if not cli_account_verify._is_login(_cmdline(pid, proc)):
        return "not a login shell"
    if _children(pid, proc):
        return "has a child process"
    if _start_epoch(pid, proc, clk_tck) >= since:
        return "started after the launcher"
    return None


def respawn_stale_panes(session, start_dir, since_path, *, run=_run,
                        proc="/proc", clk_tck=None):
    """Respawn every pane of ``session`` that predates the launcher stamp
    ``since_path`` and is an idle login shell. Returns the printed lines."""
    if not os.path.exists(since_path):
        return []
    if run(["tmux", "has-session", "-t", "=" + session])[0] != 0:
        return []
    clk_tck = clk_tck or os.sysconf("SC_CLK_TCK")
    since = os.stat(since_path).st_mtime
    rc, attached = run(["tmux", "display-message", "-p", "-t", "=" + session,
                        "#{session_attached}"])
    if rc != 0 or attached.strip() != "0":
        return ["  pane respawn: session %s is attached — kept (#1310)" % session]
    rc, listing = run(["tmux", "list-panes", "-s", "-t", "=" + session,
                       "-F", "#{pane_id} #{pane_pid}"])
    lines = []
    for row in (listing.splitlines() if rc == 0 else []):
        pane_id, _, pid = row.partition(" ")
        why = _why_kept(pid, since, proc, clk_tck)
        if why is None and (_pane_pid(pane_id, run) != pid or _children(pid, proc)):
            why = "changed since the check"
        if why is None:
            rc, _ = run(["tmux", "respawn-pane", "-k", "-t", pane_id,
                         "-c", start_dir, LOGIN_SHELL_CMD])
            why = "respawned with the launcher" if rc == 0 else "respawn failed rc=%d" % rc
        lines.append("  pane respawn: %s %s (pid %s): %s (#1310)"
                     % (session, pane_id, pid, why))
    return lines


def maybe_respawn_stale_project_panes(*, user=None, home=None, run=None,
                                      proc="/proc", clk_tck=None,
                                      is_project=None, spec=None):
    """The ``cmd_install`` step: a no-op off a live project account; never
    raises (install must go on, loudly)."""
    try:
        if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
            return []
        import pwd
        user = user or pwd.getpwuid(os.getuid()).pw_name
        if is_project is None:
            import cli_project_toolchain
            is_project = cli_project_toolchain.is_project_account
        if not is_project(user):
            return []
        if spec is None:
            import cli_account_bootstrap
            spec = cli_account_bootstrap.account_spec(user)
        if not spec.get("tmux_session"):
            return []
        home = home or os.path.expanduser("~")
        start = os.path.join(home, spec.get("project_dir") or "")
        if not os.path.isdir(start):
            start = home
        from cli_bashrc_appliers import LAUNCHER_SINCE_NAME
        lines = respawn_stale_panes(
            spec["tmux_session"], start.rstrip("/"),
            os.path.join(home, ".claude", LAUNCHER_SINCE_NAME),
            run=run or _run, proc=proc, clk_tck=clk_tck)
    except Exception as e:  # noqa: BLE001 — install must go on, but loudly
        lines = ["  pane respawn error (non-fatal): %s (#1310)" % e]
    for ln in lines:
        print(ln, file=sys.stderr if "error" in ln else sys.stdout)
    return lines
