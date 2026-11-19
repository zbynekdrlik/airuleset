"""journald ``SystemMaxUse`` cap on the CONTROLLER, applied locally (#1355).

Every other managed box gets its journald cap from the #841 root leg
(`cli_disk_guard_root.build_apply_script`, fanned out over ssh to
`cli_fleet.DISK_GUARD_ROOT_HOSTS`). The controller is deliberately NOT in that
list: the fan-out runs FROM the controller, and its 50- fail2ban hardening
drop-in would collide with the controller-only 60- owner ignoreip, which is
last-writer-wins (`cli_disk_guard_root.OWNER_IGNOREIP_PATH`). So the
controller never got a cap (3.7G of journal on 19.11.2026, about 60 MB/day).

This leaf follows the controller-only local provisioners from `cmd_install`
(#982 `provision_owner_ignoreip`, #985 `provision_sshd_password`): box-class
gated, `sudo -n` gated, idempotent, non-fatal at the call site. The drop-in is
rendered by the ONE journald renderer `cli_disk_guard_root.render_journald_cap`.

1G keeps about 16 days of history at the measured rate, enough for the
week-back watchdog forensics (#1216 counts SIGTERMs in the journal), on a 38G
disk. The first apply rotates older history away; that is journald's own
rotation (owner ROTATION rule, #841), not a delete.
"""

import os
import subprocess
import sys

from cli_disk_guard_root import JOURNALD_CAP_PATH, render_journald_cap

JOURNAL_CAP_CONTROLLER = "1G"
CONTROLLER_JOURNAL_CAP_PATH = JOURNALD_CAP_PATH


def render_controller_journal_cap():
    return render_journald_cap(JOURNAL_CAP_CONTROLLER)


def _box_class():
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except Exception as e:  # noqa: BLE001 -- unknown box = not the controller
        print("  journal cap: box class undeterminable (%r)" % e, file=sys.stderr)
        return "unknown"


def _sudo(run, args, timeout=15, stdin=None):
    kw = {"input": stdin} if stdin is not None else {}
    return run(["sudo", "-n"] + list(args), capture_output=True, text=True,
               timeout=timeout, **kw)


def _already_current(dest, content):
    if not os.path.isfile(dest):
        return False
    try:
        with open(dest, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read() == content
    except OSError as e:
        print("  journal cap: cannot read %s (%s), rewriting" % (dest, e), file=sys.stderr)
        return False


def provision_controller_journal_cap(run=None, dest=None, box_class=None):
    """Install the controller's journald cap drop-in. Returns a status line:
    ``skipped (...)``, ``unchanged (...)``, ``applied (...)`` or ``FAILED (...)``.

    Order: write atomically (tee to a tmp file, chmod 0644, mv), then restart
    journald (it reads journald.conf.d only at start; a rotate alone would
    vacuum to the OLD limit, the #841 "deployed != effective" trap), then
    rotate, then read back ``journalctl --disk-usage`` into the status."""
    run = run or subprocess.run
    bc = box_class if box_class is not None else _box_class()
    if bc != "controller":
        return "skipped (box-class=%s, not controller)" % bc
    content = render_controller_journal_cap()
    dest = dest or CONTROLLER_JOURNAL_CAP_PATH
    if _already_current(dest, content):
        return "unchanged (%s)" % dest
    try:
        probe = _sudo(run, ["true"], timeout=10)
    except Exception as e:  # noqa: BLE001
        return "skipped (sudo probe error: %r)" % e
    if getattr(probe, "returncode", 1) != 0:
        return "skipped (no passwordless sudo)"
    tmp = dest + ".airuleset-tmp"
    try:
        steps = ((["mkdir", "-p", os.path.dirname(dest)], None),
                 (["tee", tmp], content),
                 (["chmod", "0644", tmp], None),
                 (["mv", "-f", tmp, dest], None))
        for args, stdin in steps:
            r = _sudo(run, args, stdin=stdin)
            if getattr(r, "returncode", 1) != 0:
                _sudo(run, ["rm", "-f", tmp], timeout=5)
                return "FAILED (%s rc=%d)" % (args[0], r.returncode)
        r = _sudo(run, ["systemctl", "try-restart", "systemd-journald.service"])
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (journald restart rc=%d — file written, cap NOT live)" % r.returncode
        r = _sudo(run, ["journalctl", "--rotate"], timeout=60)
        if getattr(r, "returncode", 1) != 0:
            return "FAILED (journalctl --rotate rc=%d)" % r.returncode
        usage = _sudo(run, ["journalctl", "--disk-usage"])
    except Exception as e:  # noqa: BLE001
        _sudo(run, ["rm", "-f", tmp], timeout=5)
        return "FAILED (error: %r)" % e
    return "applied (%s, SystemMaxUse=%s; %s)" % (
        dest, JOURNAL_CAP_CONTROLLER, (getattr(usage, "stdout", "") or "").strip())
