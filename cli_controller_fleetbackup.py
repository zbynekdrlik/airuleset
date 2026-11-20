"""The controller's ``fleetbackup`` login for the dev1 fleet-backup manager (#1351).

The owner granted it directly (ROZHODNUTÉ 17.11.2026, option 2): the
fleet-backup manager on dev1 (account ``fleet-backup``) logs in to the
controller key-only and has passwordless sudo, like on every other fleet host.
The key is restricted to dev1's tailscale address with ``from=``. The login was
made by hand on 17.11.; this leaf is its declaration, so ``push`` keeps it.

It follows the controller-only provisioners that ``cmd_install`` calls (#982
``provision_owner_ignoreip``, #985 ``provision_sshd_password``, #1355
``provision_controller_journal_cap``): gated on box class and ``sudo -n``,
idempotent, non-fatal at the call site, one status line.

Why not ``cli_account_bootstrap.SERVICE_ACCOUNTS``: that declares PROJECT
accounts (repo, tmux session, webterm keys) through a run-once root script, and
``cli_account_policy.validate_sudo`` refuses ``ALL`` by design. This grant is
``NOPASSWD:ALL`` by owner decision, so it lives here, not as a weakened policy.

authorized_keys is reconciled on the key BLOB: foreign lines, comments and
blank lines are kept byte for byte; our line counts as present when its options
and type match, whatever its comment says; a copy of our blob with other
options (no ``from=``) is rewritten, and a second copy is dropped, because an
unrestricted duplicate would defeat the restriction. The file is written AS the
user (``sudo -u fleetbackup``), so root never writes into a user-owned dir.
The sudoers file goes through the #659 visudo-validated installer.
"""

import os
import re
import subprocess
import sys

from cli_owner_vps import _sudoers_install_script

# The manager key from dev1:/home/fleet-backup/.ssh/id_ed25519.pub (PUBLIC
# material) and dev1's tailscale address it must come from (hardening 17.11.,
# #1290). ONE copy: the subdev rrsync login (#1387) builds its line from these.
MANAGER_FROM = "100.104.8.125"
MANAGER_KEY = ("ssh-ed25519 "
               "AAAAC3NzaC1lZDI1NTE5AAAAIMzhgslnCx56OLBPtqRStknHDCCDlpWvLoNQkXVB5B3l")

FLEETBACKUP = {
    "user": "fleetbackup",
    "gecos": "fleet-backup manager (airuleset#1351)",
    "authorized_key": 'from="%s" %s fleet-backup-manager@dev1 (airuleset#1351)' % (
        MANAGER_FROM, MANAGER_KEY),
    # No '.' in the name: sudo's #includedir skips dotted files.
    "sudoers_path": "/etc/sudoers.d/60-airuleset-fleetbackup",
    "sudoers": (
        "# airuleset#1351 (owner 17.11.2026): fleet-backup manager, permanent\n"
        "fleetbackup ALL=(ALL) NOPASSWD:ALL\n"),
}

_KEY_TYPES = ("ssh-ed25519", "ssh-rsa", "ssh-dss", "ecdsa-sha2-",
              "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-")


def _is_key_type(token):
    return any(token == t or (t.endswith("-") and token.startswith(t))
               for t in _KEY_TYPES)


def parse_key_line(line):
    """``(options, keytype, blob)`` of an authorized_keys line, or None for a
    blank/comment/malformed line. Options may hold quoted spaces."""
    s = (line or "").strip()
    if not s or s.startswith("#"):
        return None
    options = ""
    if not _is_key_type(s.split(None, 1)[0]):
        in_quote = escaped = False
        for i, ch in enumerate(s):
            if escaped:
                escaped = False
            elif ch == "\\" and in_quote:
                escaped = True
            elif ch == '"':
                in_quote = not in_quote
            elif ch in " \t" and not in_quote:
                options, s = s[:i], s[i:].lstrip()
                break
        else:
            return None
    parts = s.split()
    if len(parts) < 2 or not _is_key_type(parts[0]):
        return None
    return options, parts[0], parts[1]


def reconcile_authorized_keys(existing, desired):
    """``(new_content, action)``; action is ``present``, ``added`` or
    ``replaced``. Only lines carrying the desired blob are ever touched."""
    want = parse_key_line(desired)
    if want is None:
        raise ValueError("declared authorized_keys line does not parse")
    lines = existing.splitlines(keepends=True)
    out, seen, changed = [], False, False
    for ln in lines:
        got = parse_key_line(ln)
        if got is None or got[2] != want[2]:
            out.append(ln)
            continue
        if seen:
            changed = True          # a second copy of our blob: drop it
            continue
        seen = True
        if got[:2] == want[:2]:
            out.append(ln)
        else:
            out.append(desired + "\n")
            changed = True
    if not seen:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(desired + "\n")
        return "".join(out), "added"
    return "".join(out), ("replaced" if changed else "present")


def _box_class():
    try:
        from watchdog.reaper import default_box_class
        return default_box_class()
    except Exception as e:  # noqa: BLE001 -- unknown box = not the controller
        return "unknown (%r)" % (e,)


# The home path is interpolated into a single-quoted shell string.
_HOME_RE = re.compile(r"/[A-Za-z0-9_./-]+")


class _StepFailed(Exception):
    pass


def _write_ak_script(ssh_dir):
    # Runs AS the user; mktemp in the same dir, so the mv is atomic.
    return ("set -e; umask 077; d='%s'; mkdir -p \"$d\"; chmod 700 \"$d\"; "
            "t=$(mktemp \"$d/.authorized_keys.XXXXXX\"); trap 'rm -f \"$t\"' EXIT; "
            "cat > \"$t\"; chmod 600 \"$t\"; mv -f \"$t\" \"$d/authorized_keys\""
            % ssh_dir)


def provision_fleetbackup_login(run=None, box_class=None, spec=None):
    """Ensure the declared ``fleetbackup`` login. Returns ``skipped (...)``,
    ``unchanged (...)``, ``applied (...)`` or ``FAILED (...)``."""
    if run is None and os.environ.get("PYTEST_CURRENT_TEST"):
        return "skipped (pytest)"   # #1196: never real sudo from a test install
    run = run or subprocess.run
    spec = spec or FLEETBACKUP
    bc = box_class if box_class is not None else _box_class()
    if bc != "controller":
        return "skipped (box-class=%s, not controller)" % bc
    user = spec["user"]

    def call(args, stdin=None, what=None, timeout=20):
        kw = {"input": stdin} if stdin is not None else {}
        r = run(args, capture_output=True, text=True, timeout=timeout, cwd="/", **kw)
        if what and getattr(r, "returncode", 1) != 0:
            raise _StepFailed("%s rc=%s %s" % (what, r.returncode,
                                               (r.stderr or "").strip()[:160]))
        return r

    try:
        probe = call(["sudo", "-n", "true"], timeout=10)
    except Exception as e:  # noqa: BLE001
        return "skipped (sudo probe error: %r)" % (e,)
    if getattr(probe, "returncode", 1) != 0:
        return "skipped (no passwordless sudo)"

    done = []
    try:
        pw = call(["getent", "passwd", user])
        if pw.returncode != 0:
            call(["sudo", "-n", "useradd", "-m", "-c", spec["gecos"], "-s",
                  "/bin/bash", user], what="useradd")
            done.append("user created")
            pw = call(["getent", "passwd", user], what="getent")
        fields = (pw.stdout or "").strip().split(":")
        if len(fields) < 7 or not _HOME_RE.fullmatch(fields[5]):
            raise _StepFailed("getent passwd %s: unexpected home %r" % (
                user, fields[5] if len(fields) > 5 else None))
        home = fields[5]

        st = call(["sudo", "-n", "passwd", "-S", user], what="passwd -S")
        if ((st.stdout or "").split() + ["", ""])[1] != "L":
            call(["sudo", "-n", "passwd", "-l", user], what="passwd -l")
            done.append("password locked")

        ssh_dir = home + "/.ssh"
        ak = ssh_dir + "/authorized_keys"
        as_user = ["sudo", "-n", "-u", user]
        # `test -e` instead of parsing cat's (localised) error text
        exists = call(as_user + ["test", "-e", ak]).returncode == 0
        existing = call(as_user + ["cat", ak], what="read authorized_keys").stdout \
            if exists else ""
        new, action = reconcile_authorized_keys(existing or "", spec["authorized_key"])
        if action != "present":
            call(as_user + ["sh", "-c", _write_ak_script(ssh_dir)], stdin=new,
                 what="write authorized_keys")
            done.append("key %s" % action)
        # sshd StrictModes: no group/world write on the home, no group/world
        # access to .ssh and the key file (read back after a write too).
        paths = [home, ssh_dir, ak]
        modes = (call(as_user + ["stat", "-c", "%a"] + paths, what="stat").stdout
                 or "").split()
        if len(modes) != len(paths):
            raise _StepFailed("stat returned %r for %r" % (modes, paths))
        if int(modes[0], 8) & 0o022:
            call(as_user + ["chmod", "go-w", home], what="chmod home")
            done.append("home mode fixed")
        if any(int(x, 8) & 0o077 for x in modes[1:]):
            call(as_user + ["chmod", "go-rwx", ssh_dir, ak], what="chmod")
            done.append("ssh modes fixed")

        r = call(["sudo", "-n", "sh", "-c",
                  _sudoers_install_script(spec["sudoers_path"])],
                 stdin=spec["sudoers"], what="sudoers install")
        if "AIRULESET_SUDOERS_UNCHANGED" not in (r.stdout or ""):
            done.append("sudoers installed")
    except _StepFailed as e:
        return "FAILED (%s; done: %s)" % (e, ", ".join(done) or "nothing")
    except Exception as e:  # noqa: BLE001
        return "FAILED (error: %r; done: %s)" % (e, ", ".join(done) or "nothing")
    if not done:
        return "unchanged (%s: key with from=, %s)" % (user, spec["sudoers_path"])
    return "applied (%s: %s)" % (user, ", ".join(done))


def install_step():
    """The ``cmd_install`` call site: one status line, never raises (the
    #1116 slice 2 shape, so the install goes on whatever happens here)."""
    try:
        print("  fleetbackup login: %s" % provision_fleetbackup_login())
    except Exception as e:  # noqa: BLE001
        print("  fleetbackup login error (non-fatal): %r" % (e,), file=sys.stderr)
