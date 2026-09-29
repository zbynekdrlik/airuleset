"""airuleset — restart a LOCAL drop lane's tunnel without a dark window (#1191).

`drop-gateway` edits a local-topology lane's cloudflared ingress (spinbike's
system unit, the marek/dominika `--user` units on subdev) and must restart the
tunnel to load it. A plain `systemctl restart` is stop-then-start and
cloudflared drains its 30 s grace period first, so every hostname on that
tunnel answers 1033 for the window — the defect #1189 removed for the managed
tunnels. This leaf routes the drop lanes through the SAME
`cli_tunnel_apply.overlap_restart`, with a lane-specific `systemctl` adapter:

- a `--user` lane runs `systemctl --user` with the #826 XDG env
  (`cli_drop_gateway._restart_env`);
- the system-unit lane (spinbike) runs `sudo -n systemctl`, and ONLY when
  `sudo -n -l` confirms the existing grant covers every command the overlap
  runs — otherwise it keeps the plain restart, LOUD.

Before the overlap it checks, from the live unit (`systemctl show`), that the
unit really runs the config drop-gateway edits (else restarting it could never
serve the new ingress — the #1191 dominika/marek config-path class), renders
the overlap unit next to the lane's unit, and makes sure the main unit carries
`TUNNEL_PIDFILE` (the registration signal the overlap waits on). A unit the
repo renders today sets it (`cli_webterm_tunnel.render_cloudflared_tunnel_unit`);
one that lacks it — spinbike's hand-managed unit, or the marek/dominika units
rendered before #1189 and no longer rendered on subdev — gets it as a systemd
drop-in. Any unmet precondition = a LOUD plain restart, never a guess.

Stdlib-only leaf: every command goes through the injected `run` (the
`subprocess.run` seam drop-gateway's tests already fake).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import cli_tunnel_apply

SYSTEM_UNIT_DIR = Path("/etc/systemd/system")
PIDFILE_DROPIN = "airuleset-tunnel-pidfile.conf"
_RUN_TIMEOUT_S = 30


def lane_unit_dir(lane) -> Path:
    """Where the lane's tunnel unit lives — the overlap unit is rendered beside it."""
    if lane.tunnel_system_unit:
        return SYSTEM_UNIT_DIR
    return Path.home() / ".config" / "systemd" / "user"


def _prefix(lane):
    return ["sudo", "-n", "systemctl"] if lane.tunnel_system_unit \
        else ["systemctl", "--user"]


def _call(run, argv, env, stdin=None, timeout=_RUN_TIMEOUT_S):
    """`run(argv)` → (rc, stdout, stderr). Never raises (an exception is rc 1
    with its text as stderr, which every caller prints)."""
    kw = dict(capture_output=True, text=True, env=env, timeout=timeout)
    if stdin is not None:
        kw["input"] = stdin
    try:
        r = run(argv, **kw)
    except Exception as e:
        return 1, "", "%s: %s" % (" ".join(argv), e)
    return (getattr(r, "returncode", 1), getattr(r, "stdout", "") or "",
            getattr(r, "stderr", "") or "")


def lane_systemctl(lane, run, env):
    """The `systemctl(args) -> (rc, out, err)` adapter `overlap_restart` drives."""
    prefix = _prefix(lane)
    return lambda args: _call(run, prefix + list(args), env)


def render_pidfile_dropin(config_path) -> str:
    return ("# airuleset-managed (#1191): the registration signal the no-dark-window\n"
            "# overlap restart waits on (cloudflared writes it once connected).\n"
            "[Service]\n"
            "Environment=TUNNEL_PIDFILE=%s\n" % cli_tunnel_apply.tunnel_pidfile(config_path))


def _unit_props(lane, run, env):
    """`{ExecStart, Environment, User}` of the live unit, or None when unreadable.
    Read WITHOUT sudo (`systemctl show` of a system unit needs no privilege), so a
    narrow grant is reported as a grant gap, never as an unreadable unit."""
    prefix = ["systemctl"] if lane.tunnel_system_unit else ["systemctl", "--user"]
    rc, out, _err = _call(run, prefix + ["show", "-p", "ExecStart", "-p",
                                         "Environment", "-p", "EnvironmentFiles",
                                         "-p", "User", lane.tunnel_service], env)
    if rc != 0:
        return None
    props = {}
    for line in (out or "").splitlines():
        key, sep, val = line.partition("=")
        if sep:
            props[key.strip()] = val.strip()
    return props


def _exec_binary(exec_start):
    """The unit's cloudflared binary, or None unless it runs `cloudflared … tunnel`
    directly (a wrapper such as `/bin/sh -c` is never copied into the overlap)."""
    m = re.search(r"\bpath=(\S+)", exec_start or "")
    if m is None or Path(m.group(1)).name != "cloudflared":
        return None
    return m.group(1) if re.search(r"\btunnel\b", exec_start) else None


def _pidfile_gap(lane, props, want):
    """Why the main unit's `TUNNEL_PIDFILE` cannot be relied on (a reason), "" when
    it is set to `want`, or None when a drop-in must add it — for a `--user` lane
    too: the live marek/dominika units predate the #1189 render and are no longer
    rendered on their box (LANE_HOST = controller), so nothing else ever will."""
    if props.get("EnvironmentFiles"):     # overrides Environment= (systemd.exec)
        return ("%s reads an EnvironmentFile, which can override TUNNEL_PIDFILE"
                % lane.tunnel_service)
    have = [e for e in props.get("Environment", "").split()
            if e.startswith("TUNNEL_PIDFILE=")]
    if have:
        return "" if have[-1] == want else (
            "%s sets %s, not %s" % (lane.tunnel_service, have[-1], want))
    return None


def _runs_config(exec_start, config) -> bool:
    """True when the unit's argv passes exactly `--config <config>`."""
    pat = r"--config[= ]%s(?=\s|;|$)" % re.escape(str(config))
    return re.search(pat, exec_start or "") is not None


def _grant_probes(lane, overlap, write_paths):
    """Every privileged argv the system-lane overlap runs (`sudo -n -l` checks
    each): the unit-file writes (a `mkdir` only for a missing parent — exactly
    what `_write_unit_file` runs), then every systemctl call of `overlap_restart`."""
    unit = lane.tunnel_service
    cmds = []
    for path in write_paths:
        if not path.parent.is_dir():
            cmds.append(["mkdir", "-p", str(path.parent)])
        cmds.append(["tee", str(path)])
    return cmds + [["systemctl", "daemon-reload"],
                   ["systemctl", "show", "-p", "ActiveState", "--value", overlap],
                   ["systemctl", "show", "-p", "MainPID", "--value", unit],
                   ["systemctl", "stop", overlap],
                   ["systemctl", "reset-failed", overlap],
                   ["systemctl", "start", "--no-block", overlap],
                   ["systemctl", "stop", "--no-block", overlap],
                   ["systemctl", "restart", "--no-block", unit]]


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None                     # absent / unreadable = must be (re)written


def _write_unit_file(lane, run, env, path, text):
    """Write `path` unless byte-identical. Returns (changed, error-or-None)."""
    if _read(path) == text:
        return False, None
    if lane.tunnel_system_unit:
        rc, err = 0, ""
        if not path.parent.is_dir():
            rc, _o, err = _call(run, ["sudo", "-n", "mkdir", "-p", str(path.parent)], env)
        if rc == 0:
            rc, _o, err = _call(run, ["sudo", "-n", "tee", str(path)], env, stdin=text)
        return True, (None if rc == 0 else "sudo write of %s failed: %s"
                      % (path, err.strip() or "rc=%d" % rc))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        return True, "write of %s failed: %s" % (path, e)
    return True, None


def prepare_overlap(lane, run, env, systemctl, unit_dir=None):
    """Make the overlap restart possible. Returns None when ready, else the reason
    the lane must fall back to a plain restart. Writes nothing until every
    read-only precondition (live unit shape, sudo grant) has passed."""
    unit, config = lane.tunnel_service, Path(lane.tunnel_config)
    props = _unit_props(lane, run, env)
    if props is None:
        return "cannot read %s from systemd" % unit
    if not _runs_config(props.get("ExecStart"), config):
        return ("%s does not run %s — its ingress edit cannot reach the running "
                "tunnel" % (unit, config))
    binary = _exec_binary(props.get("ExecStart"))
    if binary is None:
        return "%s does not run `cloudflared … tunnel` directly" % unit
    gap = _pidfile_gap(lane, props, "TUNNEL_PIDFILE=%s"
                       % cli_tunnel_apply.tunnel_pidfile(config))
    if gap:
        return gap
    unit_dir = Path(unit_dir) if unit_dir is not None else lane_unit_dir(lane)
    overlap = cli_tunnel_apply.overlap_service_name(unit)
    user = props.get("User") if lane.tunnel_system_unit else None
    writes = [(unit_dir / overlap, cli_tunnel_apply.render_overlap_unit(
        unit, config, binary, user=user or None))]
    if gap is None:
        writes.append((unit_dir / (unit + ".d") / PIDFILE_DROPIN,
                       render_pidfile_dropin(config)))
    if lane.tunnel_system_unit:
        for cmd in _grant_probes(lane, overlap, [p for p, _t in writes]):
            rc, _o, _e = _call(run, ["sudo", "-n", "-l"] + cmd, env)
            if rc != 0:
                return ("the sudo grant does not cover `%s` — the overlap needs it "
                        "(gap: ticket #1191)" % " ".join(cmd))
    changed = False
    for path, text in writes:
        wrote, err = _write_unit_file(lane, run, env, path, text)
        if err:
            return err
        changed = changed or wrote
    if changed:
        rc, _o, err = systemctl(["daemon-reload"])
        if rc != 0:
            return "daemon-reload failed: %s" % (err.strip() or "rc=%d" % rc)
    return None


def _pending_path(lane) -> Path:
    return Path(lane.tunnel_config).with_suffix(".restart-pending")


def _mark_pending(lane):
    try:
        _pending_path(lane).touch()
    except OSError as e:
        print("  drop-gateway: LOUD — cannot write %s (%s); a failed restart will "
              "not be retried automatically." % (_pending_path(lane), e),
              file=sys.stderr)


def write_config(lane, text):
    """Write `lane`'s edited tunnel config, marking its restart PENDING first, so
    a death between this write and the restart is retried by the next install
    instead of leaving an ingress that is on disk but never loaded."""
    _mark_pending(lane)
    Path(lane.tunnel_config).write_text(text, encoding="utf-8")


def retry_pending(lane) -> bool:
    """True when a previous restart of `lane`'s tunnel did not succeed (its
    pending marker, written before every restart and removed only on success, is
    still there), so an UNCHANGED ingress must still be re-applied: the config
    already carries it, the running connector may not."""
    return _pending_path(lane).exists()


def _restart(lane, run, plain_argv, env, unit_dir, sleep, clock):
    systemctl = lane_systemctl(lane, run, env)
    try:
        reason = prepare_overlap(lane, run, env, systemctl, unit_dir=unit_dir)
        if reason is None:
            ok, shape = cli_tunnel_apply.overlap_restart(
                systemctl, lane.tunnel_service, lane.tunnel_config, sleep=sleep,
                clock=clock, lane="(drop %s)" % lane.host)
            if ok:      # the overlap is stopped — no leftover for a later run
                cli_tunnel_apply.overlap_pidfile(lane.tunnel_config).unlink(
                    missing_ok=True)
            return ok, shape, "" if ok else "see the LOUD line above"
    except Exception as e:
        print("  drop-gateway: LOUD — overlap restart of %s errored (%r); the "
              "next install retries (#1191)." % (lane.tunnel_service, e),
              file=sys.stderr)
        return False, "failed", repr(e)
    print("  drop-gateway: LOUD — no overlap for %s (%s); PLAIN restart — the "
          "tunnel is DARK for its grace period (#1191)."
          % (lane.tunnel_service, reason), file=sys.stderr)
    # Blocking, and it drains the full 30 s grace: no subprocess timeout.
    rc, _o, err = _call(run, list(plain_argv), env, timeout=None)
    leftover = cli_tunnel_apply.overlap_pidfile(lane.tunnel_config)
    if rc == 0 and leftover.exists():
        # A failed earlier run left its overlap serving; it bridged this restart.
        systemctl(["stop", "--no-block",
                   cli_tunnel_apply.overlap_service_name(lane.tunnel_service)])
        leftover.unlink(missing_ok=True)
    return rc == 0, "plain", err.strip()


def restart_lane_tunnel(lane, run, *, plain_argv, env, unit_dir=None,
                        sleep=time.sleep, clock=time.monotonic):
    """Restart `lane`'s local tunnel to load an ingress edit. Returns
    `(ok, shape, detail)`: shape `"overlap"` (no dark window), `"plain"` (a LOUD
    stop-then-start) or `"failed"` (the new main never re-registered — the
    overlap is LEFT SERVING, `cli_tunnel_apply.overlap_restart` — or the restart
    errored). Anything but success leaves the `retry_pending` marker, so the next
    install retries even though the config is unchanged. Never raises."""
    _mark_pending(lane)
    ok, shape, detail = _restart(lane, run, plain_argv, env, unit_dir, sleep, clock)
    if ok:
        _pending_path(lane).unlink(missing_ok=True)
    return ok, shape, detail
