"""airuleset webterm — live-argv reconcile for running webterm units (#974).

After the install step renders launcher scripts and unit files, and
``_webterm_apply_restarts`` restarts units whose FILES changed, this module
checks whether the RUNNING process's argv still references a stale path
(e.g. ``.claude/worktrees/agent-*/…``) or a path different from the freshly
rendered one. A stale-argv unit is restarted immediately; a matching one is
left alone.

Stdlib-only; the ``proc_root`` parameter (default ``/proc``) is the seam
that makes tests hermetic (a fake ``/proc`` under ``$TMPDIR``) without
touching real systemd.

Incident: 2026-09-10 webterm outage on controller — all 8 units carried a
worktree path in their argv after the worktree was removed; two installs
regenerated the files correctly but never restarted the units because
``_webterm_apply_restarts`` only checks file-change, not live-argv.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WORKTREE_MARKER = "/.claude/worktrees/"


def _read_cmdline(pid: int | str, proc_root: Path = Path("/proc")) -> list[str]:
    """Read ``/proc/<pid>/cmdline`` and return argv as a list of strings.

    Returns an empty list on any error (pid vanished, permission denied,
    zombie with empty cmdline)."""
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
        if not raw:
            return []
        # /proc/<pid>/cmdline is NUL-separated, often trailing NUL
        parts = raw.split(b"\x00")
        return [p.decode("utf-8", errors="replace") for p in parts if p]
    except (OSError, ValueError):
        return []


def _get_main_pid(run_systemctl, unit: str) -> int:
    """Read MainPID of ``unit`` via ``systemctl --user show``."""
    rc, stdout, _err = run_systemctl(
        ["show", "-p", "MainPID", "--value", unit])
    if rc != 0:
        return 0
    try:
        return int(stdout.strip())
    except (ValueError, TypeError):
        return 0


def _argv_is_stale(argv: list[str], rendered_path: str) -> tuple[bool, str]:
    """Check whether ``argv`` references a stale/wrong script path.

    Returns ``(is_stale, reason)``.

    Two checks:
    1. ANY argv element containing the worktree marker is stale (the incident
       class — a .claude/worktrees/ path that will dangle after cleanup).
    2. An argv element with the SAME basename as the rendered path but a
       DIFFERENT directory is stale (a relocated script). Only same-basename
       elements are compared, so ttyd's ``cli_webterm.py`` connect arg is
       never compared against the ``.sh`` launcher (CRITICAL-1 fix)."""
    for part in argv:
        if WORKTREE_MARKER in part:
            return True, "worktree path in argv: %s" % part
    # Secondary check: same-basename, different directory.
    import os
    rendered_str = str(rendered_path)
    rendered_basename = os.path.basename(rendered_str)
    for part in argv:
        if "/" not in part:
            continue
        if os.path.basename(part) == rendered_basename and part != rendered_str:
            return True, "argv script %s differs from rendered %s" % (
                part, rendered_str)
    return False, ""


def reconcile_live_argv(
    run_systemctl,
    units: list[str],
    rendered_paths: dict[str, str],
    log_prefix: str = "webterm",
    proc_root: Path = Path("/proc"),
) -> list[str]:
    """Compare each unit's LIVE process argv against the rendered path and
    restart units with stale argv.

    ``rendered_paths`` maps unit name -> the expected script/module path
    (e.g. the launcher .sh for ttyd, the gateway .py for the gateway unit).

    Returns the list of units that were restarted."""
    restarted = []
    for unit in units:
        rendered = rendered_paths.get(unit)
        if rendered is None:
            continue
        # MEDIUM-3: if the rendered path itself is a worktree, restarting would
        # loop into the same stale argv — warn and skip, never restart.
        if WORKTREE_MARKER in str(rendered):
            print("  %s: WARNING — rendered path %s is itself a worktree; "
                  "skipping reconcile for %s (run install from the main checkout)"
                  % (log_prefix, rendered, unit), file=sys.stderr)
            continue
        pid = _get_main_pid(run_systemctl, unit)
        if pid <= 0:
            continue  # not running — nothing to reconcile
        argv = _read_cmdline(pid, proc_root)
        if not argv:
            continue  # can't read cmdline — skip
        stale, reason = _argv_is_stale(argv, rendered)
        if not stale:
            continue
        print("  %s: restarting %s — live argv stale (%s)"
              % (log_prefix, unit, reason))
        rc, _o, err = run_systemctl(["restart", unit])
        if rc != 0:
            print("  %s: restart %s FAILED: %s"
                  % (log_prefix, unit, (err or "").strip()),
                  file=sys.stderr)
        else:
            restarted.append(unit)
    return restarted


def check_webterm_argv_health(
    run_systemctl,
    rendered_paths: dict[str, str],
    proc_root: Path = Path("/proc"),
) -> list[str]:
    """Return per-unit status lines for ``cmd_status``.

    Shape mirrors ``_check_symlink_health`` (#972): OK / STALE / NOT RUNNING."""
    lines = []
    for unit, rendered in rendered_paths.items():
        pid = _get_main_pid(run_systemctl, unit)
        if pid <= 0:
            lines.append("  %s: NOT RUNNING (MainPID=0)" % unit)
            continue
        argv = _read_cmdline(pid, proc_root)
        if not argv:
            lines.append("  %s: UNKNOWN (cannot read /proc/%d/cmdline)" % (unit, pid))
            continue
        stale, reason = _argv_is_stale(argv, rendered)
        if stale:
            lines.append("  %s: STALE (live argv: %s)" % (unit, reason))
        else:
            lines.append("  %s: OK (live argv matches rendered)" % unit)
    return lines


# ---------------------------------------------------------------------------
# Status enumeration — which webterm units exist on THIS box (#974 MEDIUM-4)
# ---------------------------------------------------------------------------

def _box_class() -> str:
    """Return this box's class: 'controller' or the nodename.

    Reuses ``watchdog.reaper.default_box_class`` when available; falls
    back to 'workstation' (the same fail-open as ``maybe_setup_webterm``)."""
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except ImportError:
        return "workstation"


def enumerate_status_units() -> dict[str, str]:
    """Return ``{unit_name: rendered_path}`` for every webterm unit on this box.

    Reuses the SAME ``profiles.LANE_HOST`` + ``_HUMAN_TO_MODULE`` +
    ``mod._spec()`` derivation as ``_setup_controller_webterm`` (cli_webterm.py
    L1735-1751), so the enumeration never diverges from the install step.

    Returns an empty dict when the box has no webterm units."""
    import importlib
    from cli_webterm import (
        is_webterm_gateway, WEBTERM_LAUNCH_PATH, WEBTERM_GATEWAY_MODULE,
        _HUMAN_TO_MODULE,
    )
    import cli_webterm_profiles as profiles

    rendered: dict[str, str] = {}

    # Owner-box pair (dev1 — is_webterm_gateway checks nodename == "dev1")
    if is_webterm_gateway():
        rendered["webterm-ttyd.service"] = str(WEBTERM_LAUNCH_PATH)
        rendered["webterm-gateway.service"] = str(WEBTERM_GATEWAY_MODULE)

    # Lane-profile units: iterate LANE_HOST for lanes hosted on this box,
    # exactly as _setup_controller_webterm does for "controller".
    box = _box_class()
    if box == "controller":
        hosted_humans = [h for h, b in profiles.LANE_HOST.items()
                         if b == "controller"]
    else:
        # Non-controller: lanes hosted on this nodename (subdev shape)
        nodename = os.uname().nodename
        hosted_humans = [h for h, b in profiles.LANE_HOST.items()
                         if b == nodename]

    for human in hosted_humans:
        mod_name = _HUMAN_TO_MODULE.get(human)
        if mod_name is None:
            continue
        try:
            mod = importlib.import_module(mod_name)
            spec = mod._spec()
            rendered[spec.ttyd_service_name] = str(spec.launch_path)
            rendered[spec.gateway_service_name] = str(WEBTERM_GATEWAY_MODULE)
        except Exception:
            continue

    return rendered
