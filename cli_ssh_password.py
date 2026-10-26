"""The fleet-shared `newlevel` ssh password: sshpass reads it from a 0600 file (#1236).

The identity-less `cli_fleet.REMOTE_HOSTS` entries (dev2's `newlevel`, the
montalu family on subdev, forestshop-dev's admin/stepan) log in through
`sshpass ... ssh` whenever the account's default key does not. The password
used to be a literal in this PUBLIC repo. It now lives only in the account's
secrets dir as `~/.secrets/newlevel-ssh-password` (mode 0600) and reaches
sshpass as `sshpass -f <file>`: never in this source, and never in argv either
(`/proc/<pid>/cmdline` is world-readable, a 0600 file is not). Same value, same
hosts, same access: the owner's ROZHODNUTÉ on #1236 (14.10.2026) changes no
password, no sshd setting and no host.

The file is resolved against the CALLING account's home at call time, under
the same `~/.secrets` root rule the vault key-file loader uses
(`cli_vault_keyfile.key_root`: a symlinked root is no root). Only its first
line's shape is checked here (never printed); sshpass reads it. (A test points
ENV_OVERRIDE at a fake file instead.)

A missing file, a non-regular one (a symlink included) or one that group/other
can read raises `SshPasswordFileError`, whose message names the file and how to
create it: never a silent fallback, never a built-in value. It is an `OSError`,
so a fleet collector's existing per-host `except OSError` / `except Exception`
guard counts ONE host as failed instead of losing the whole run, and the push
deploy loop catches it per host and fails the push.
"""

import os
import stat
from pathlib import Path

FILE_NAME = "newlevel-ssh-password"
#: How the file is created on a box: the credential channel (a one-shot URL the
#: owner pastes the value into; written 0600 with one trailing newline).
CREATE_CMD = ("python3 ~/devel/airuleset/airuleset.py secret request "
              "NEWLEVEL_SSH_PASSWORD --persist ~/.secrets/" + FILE_NAME)
#: Test seam (the AIRULESET_* override convention): an absolute path that
#: replaces the default file. tests/conftest.py and the push gate's Pass B
#: point it at a FAKE file so no test ever builds argv from the real one.
#: Never set in production; the same checks apply to it.
ENV_OVERRIDE = "AIRULESET_SSH_PASSWORD_FILE"


class SshPasswordFileError(OSError):
    """The newlevel ssh password file cannot be used (see the module doc)."""


def password_file():
    """The password file's absolute path under this account's `~/.secrets`
    (or the ENV_OVERRIDE path).

    Raises SshPasswordFileError when the file is missing (or `~/.secrets` is
    not a plain directory), is not a regular file, or grants any group/other
    permission. Never opens the file."""
    import cli_vault_keyfile  # the ~/.secrets root rule (#1153)

    override = os.environ.get(ENV_OVERRIDE)
    if override:
        path, root_ok = Path(override), True
    else:
        root = cli_vault_keyfile.key_root()  # None: absent, or a symlink
        path = (root or Path.home() / cli_vault_keyfile.KEY_ROOT_NAME) / FILE_NAME
        root_ok = root is not None
    try:
        if not root_ok:
            raise FileNotFoundError(str(path))
        st = os.lstat(path)
    except FileNotFoundError:
        raise SshPasswordFileError(
            "newlevel ssh password file %s is MISSING (or ~/.secrets is not a "
            "plain directory): the identity-less fleet hosts (sshpass) cannot "
            "log in from this account. Create it, the newlevel password fed on "
            "stdin by the owner/supervisor: %s (#1236)"
            % (path, CREATE_CMD)) from None
    if not stat.S_ISREG(st.st_mode):
        raise SshPasswordFileError(
            "newlevel ssh password file %s is not a regular file (a symlink?): "
            "replace it with a plain file: %s (#1236)" % (path, CREATE_CMD))
    if st.st_mode & 0o077:
        raise SshPasswordFileError(
            "newlevel ssh password file %s grants group/other access (mode "
            "0%o): restrict it with `chmod 600 %s` (#1236)"
            % (path, stat.S_IMODE(st.st_mode), path))
    _check_content(path, st)
    return str(path)


def _check_content(path, st):
    """A file sshpass would misuse fails here, loudly and without the value:
    unreadable (sshpass then hangs until killed), empty, or a first line with a
    CR or trailing whitespace (sent verbatim: a wrong password, one fail2ban
    strike per host per run)."""
    if st.st_uid != os.getuid() or not os.access(path, os.R_OK):
        raise SshPasswordFileError(
            "newlevel ssh password file %s is not readable by this account "
            "(owner uid %d): recreate it: %s (#1236)" % (path, st.st_uid, CREATE_CMD))
    with open(path, "rb") as fh:
        first = fh.readline()
    line = first.rstrip(b"\n")
    if not line:
        raise SshPasswordFileError(
            "newlevel ssh password file %s is empty: recreate it: %s (#1236)"
            % (path, CREATE_CMD))
    if b"\r" in line or line != line.rstrip():
        raise SshPasswordFileError(
            "newlevel ssh password file %s has a CR or trailing whitespace on its "
            "first line (sshpass would send it as-is): recreate it: %s (#1236)"
            % (path, CREATE_CMD))


def sshpass_argv():
    """`["sshpass", "-f", <file>]`: the prefix of every identity-less ssh."""
    return ["sshpass", "-f", password_file()]


def fake_file_for_suite(tmp_dir):
    """Write a FAKE 0600 password file into `tmp_dir` and return its path: the
    ENV_OVERRIDE value cmd_push gives its Pass B `unittest discover` run (which
    never reads tests/conftest.py's own fake), so a test's identity-less argv
    names a fake file, never the real one."""
    path = Path(tmp_dir) / FILE_NAME
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("fake-pass-b-value\n")
    return str(path)
