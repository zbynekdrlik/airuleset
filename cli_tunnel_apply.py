"""airuleset — APPLY a managed cloudflared tunnel without a dark window (#1189).

Every managed tunnel (the controller's ONE shared multi-ingress tunnel — every
webterm hostname + every `drop-<box>` lane — plus the dev1 owner and subdev lane
tunnels) is provisioned by `cli_webterm_tunnel._provision_managed_tunnel`, which
renders the config + unit and hands the SYSTEMD half to `apply_managed_tunnel`.

Incident (#1189, 29.9.2026 18:25:51Z): the provisioner restarted the controller
tunnel on EVERY install/push. A systemd restart is stop-then-start, and a
SIGTERMed cloudflared unregisters its connections, then waits up to its
`--grace-period` (default 30 s) for in-flight requests (`waitToShutdown` in
cloudflared `cmd/cloudflared/tunnel/cmd.go`: a `time.NewTicker(gracePeriod)`
select after `graceShutdownC`). Webterm's long-lived websockets never finish, so
it waits the whole 30 s (journal: SIGTERM 18:25:51Z, new pid at 18:26:21Z) —
ZERO registered connections, Cloudflare answered 1033 for every hostname on it
(a `secret show` URL the owner opened at 18:26:22Z included).

Two fixes, both here:

1. **Change-gated.** Restart ONLY when the rendered config, the rendered unit or
   the credentials JSON differ from what was last APPLIED (a sha256 stamp next to
   the config, written only after a successful apply), or the ON-DISK config/unit
   differ from the render (another writer drifted them — the file is rewritten,
   so the running process must be too). A byte-identical render is a no-op — the
   steady-state push never touches the connector.
2. **Blue/green overlap when a restart IS needed** (chosen over a short
   `--grace-period`). Cloudflare's own tunnel-availability doc: every replica of
   a tunnel "establishes four new connections to Cloudflare ... All replicas
   point to the same tunnel", and replicas exist "to update the configuration of
   a tunnel without downtime". So: start a second connector for the SAME tunnel
   (a rendered `<unit>-overlap.service`, same creds + the NEW config), wait until
   it has registered, restart the main unit (the old process drains while the
   overlap serves), wait until the NEW main has registered, then stop the
   overlap. Registration is read from cloudflared's `--pidfile` (env
   `TUNNEL_PIDFILE`), which `writePidFile` writes only after `connectedSignal`,
   i.e. after the FIRST registered connection (cloudflared cmd.go) — a
   port-free readiness signal (the metrics `/ready` endpoint would need a fixed
   port per unit, which collides across the subdev lane accounts). A short grace
   period alone was REJECTED: it only shrinks the window (grace + the new
   process's registration time, seconds) — the stop still precedes the start, so
   the 1033 window never reaches zero.

Stdlib-only leaf. Every systemd call goes through `cli_filedrop_watchdog.
_run_systemctl` (resolved at CALL time, so the existing test seam patches it);
`sleep`/`clock` are injectable so a test never waits on a real timer.
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

# The overlap connector must register within this bound; a fresh cloudflared
# registers in ~1-3 s, so 45 s means "it will not" (bad creds / no network), and
# the apply falls back to a plain restart rather than never applying the config.
OVERLAP_READY_TIMEOUT_S = 45
# The main unit's restart first DRAINS the old process for its full grace period
# (30 s default, see the module docstring), then the new one registers — 120 s
# bounds drain + start + registration with margin.
MAIN_READY_TIMEOUT_S = 120
POLL_S = 0.5
# The overlap connector only ever carries traffic while the main unit restarts;
# when it is stopped the main is already registered, so a long drain would only
# stall the install (and hold the unit name for the next one).
OVERLAP_GRACE = "5s"


def tunnel_pidfile(config_path) -> Path:
    """The main connector's `TUNNEL_PIDFILE` — beside its config, one per tunnel."""
    return Path(config_path).with_suffix(".pid")


def overlap_pidfile(config_path) -> Path:
    return Path(config_path).with_suffix(".overlap.pid")


def applied_stamp_path(config_path) -> Path:
    return Path(config_path).with_suffix(".applied")


def overlap_service_name(service_name: str) -> str:
    stem = service_name[:-len(".service")] if service_name.endswith(".service") \
        else service_name
    return stem + "-overlap.service"


def apply_digest(config_text: str, unit_text: str, creds_bytes: bytes) -> str:
    """sha256 over everything the RUNNING connector depends on (length-prefixed,
    so no concatenation of two parts can collide with another split)."""
    h = hashlib.sha256()
    for part in (config_text.encode("utf-8"), unit_text.encode("utf-8"), creds_bytes):
        h.update(b"%d:" % len(part))
        h.update(part)
    return h.hexdigest()


def render_overlap_unit(description, config_path, cloudflared_bin, user=None) -> str:
    """The on-demand overlap connector: the SAME tunnel + config as the main unit,
    its own pidfile, a short grace. No `[Install]` — it is never enabled, only
    started for the restart window, and `Restart=no` so a crash never lingers.
    `user` (#1191): a SYSTEM main unit's `User=`, mirrored so the overlap never
    runs with more privilege than the connector it stands in for."""
    return (
        "# airuleset-managed cloudflared OVERLAP connector (#1189) — started only\n"
        "# while the main tunnel unit restarts, so the tunnel never goes dark.\n"
        "[Unit]\n"
        "Description=%s — overlap connector (#1189)\n"
        "\n"
        "[Service]\n"
        "%s"
        "Type=simple\n"
        "Environment=TUNNEL_PIDFILE=%s\n"
        "Environment=TUNNEL_GRACE_PERIOD=%s\n"
        "ExecStart=%s tunnel --no-autoupdate --config %s run\n"
        "Restart=no\n"
        % (description, "User=%s\n" % user if user else "",
           overlap_pidfile(config_path), OVERLAP_GRACE, cloudflared_bin, config_path))


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _read_pid(path):
    """The integer pid in `path`, or None (absent / partial / garbage)."""
    txt = _read_text(path)
    try:
        return int(txt.strip()) if txt else None
    except ValueError:
        return None


def _wait(predicate, timeout_s, sleep, clock):
    deadline = clock() + timeout_s
    while True:
        if predicate():
            return True
        if clock() >= deadline:
            return False
        sleep(POLL_S)


def _unit_active(systemctl, unit) -> bool:
    rc, out, _err = systemctl(["show", "-p", "ActiveState", "--value", unit])
    return rc == 0 and (out or "").strip() == "active"


def _main_pid(systemctl, unit) -> int:
    rc, out, _err = systemctl(["show", "-p", "MainPID", "--value", unit])
    try:
        return int((out or "").strip()) if rc == 0 else 0
    except ValueError:
        return 0


def overlap_restart(systemctl, service_name, config_path, *, sleep=time.sleep,
                    clock=time.monotonic, lane=""):
    """Restart `service_name` behind an overlap connector. Returns
    `(ok, shape)` — shape `"overlap"` (no dark window), `"plain"` (the overlap
    never registered, so a plain restart applied the config WITH a dark window —
    printed LOUDLY), or `"failed"` (the new main never registered; the overlap is
    LEFT SERVING so the tunnel stays up, and the next install retries)."""
    overlap = overlap_service_name(service_name)
    ov_pid, main_pid = overlap_pidfile(config_path), tunnel_pidfile(config_path)
    rc, err = 0, ""
    if _unit_active(systemctl, overlap) and _read_pid(ov_pid) is not None:
        # A registered leftover (a previous install's "failed" path left it
        # serving): reuse it as the bridge — stopping it first would go dark.
        print("  webterm%s: reusing the registered overlap %s as the bridge."
              % (lane, overlap), file=sys.stderr)
    else:
        systemctl(["stop", overlap])                 # a dead/unregistered leftover
        systemctl(["reset-failed", overlap])
        ov_pid.unlink(missing_ok=True)
        rc, _o, err = systemctl(["start", "--no-block", overlap])
    ready = rc == 0 and _wait(lambda: _read_pid(ov_pid) is not None,
                              OVERLAP_READY_TIMEOUT_S, sleep, clock)
    if not ready:
        systemctl(["stop", "--no-block", overlap])
        print("  webterm%s: LOUD — overlap connector %s did not register (%s); "
              "PLAIN restart of %s — the tunnel is DARK for its grace period (#1189)."
              % (lane, overlap, (err or "").strip() or "timeout", service_name),
              file=sys.stderr)
        rc, _o, err = systemctl(["restart", "--no-block", service_name])
        return rc == 0, "plain"
    main_pid.unlink(missing_ok=True)
    rc, _o, err = systemctl(["restart", "--no-block", service_name])
    if rc != 0:
        print("  webterm%s: LOUD — restart of %s REFUSED (%s); overlap %s LEFT "
              "SERVING (#1189)." % (lane, service_name, (err or "").strip(),
                                    overlap), file=sys.stderr)
        return False, "failed"

    def _main_registered():
        pid = _main_pid(systemctl, service_name)
        return pid > 0 and _read_pid(main_pid) == pid
    if not _wait(_main_registered, MAIN_READY_TIMEOUT_S, sleep, clock):
        print("  webterm%s: LOUD — %s did not re-register within %ds; overlap "
              "connector %s LEFT SERVING so the tunnel stays up (#1189)."
              % (lane, service_name, MAIN_READY_TIMEOUT_S, overlap), file=sys.stderr)
        return False, "failed"
    systemctl(["stop", "--no-block", overlap])
    return True, "overlap"


def apply_managed_tunnel(creds_path, cloudflared_bin, config_path, config_text,
                         unit_path, service_name, unit_text, *, run, whoami,
                         systemctl, lane="", sleep=time.sleep, clock=time.monotonic):
    """Write the rendered files, enable the unit, and restart it ONLY on a real
    change — through the overlap. Returns True when the unit is enabled and the
    CURRENT render is live (or, on a failed re-registration, still served by the
    overlap — False then, and no stamp is written so the next install retries)."""
    config_path, unit_path = Path(config_path), Path(unit_path)
    stamp_path = applied_stamp_path(config_path)
    digest = apply_digest(config_text, unit_text, Path(creds_path).read_bytes())
    drifted = (_read_text(config_path) != config_text
               or _read_text(unit_path) != unit_text)
    changed = drifted or _read_text(stamp_path) != digest
    was_active = _unit_active(systemctl, service_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    for path, text in ((config_path, config_text), (unit_path, unit_text),
                       (unit_path.with_name(overlap_service_name(service_name)),
                        render_overlap_unit(service_name, config_path,
                                            cloudflared_bin))):
        if _read_text(path) != text:
            path.write_text(text, encoding="utf-8")
    # linger makes the --user unit reboot-durable even when this is reached
    # standalone, not only via a caller that already lingered.
    try:
        run(["loginctl", "enable-linger", whoami()], capture_output=True, text=True)
    except Exception as e:
        print("  webterm%s: tunnel enable-linger skipped (%s)" % (lane, e),
              file=sys.stderr)
    systemctl(["daemon-reload"])
    rc, _o, err = systemctl(["enable", "--now", service_name])
    if rc != 0:
        print("  webterm%s: enable %s FAILED: %s"
              % (lane, service_name, (err or "").strip()), file=sys.stderr)
        return False
    if not was_active:
        shape, ok = "started", True     # `enable --now` just started the NEW render
    elif not changed:
        print("  webterm%s: %s unchanged (config + unit + creds byte-identical) — "
              "no restart (#1189)." % (lane, service_name), file=sys.stderr)
        return True
    else:
        print("  webterm%s: %s %s — restarting via the overlap (#1189)."
              % (lane, service_name, "on-disk config/unit drifted from the render"
                 if drifted else "render or credentials changed"), file=sys.stderr)
        ok, shape = overlap_restart(systemctl, service_name, config_path,
                                    sleep=sleep, clock=clock, lane=lane)
    if ok:
        stamp_path.write_text(digest, encoding="utf-8")
        print("  webterm%s: %s applied (%s)." % (lane, service_name, shape),
              file=sys.stderr)
    return ok
