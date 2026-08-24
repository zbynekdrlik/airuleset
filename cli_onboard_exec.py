"""cli_onboard_exec.py — the injectable local/ssh EXECUTION + remote-filesystem
transport layer for `cli_onboard.py` (#583).

Split out of `cli_onboard.py` so the onboarding LOGIC (steps / registry / audit)
and the TRANSPORT (how a command runs — locally via ``subprocess.run`` or
ssh-wrapped for a ``REMOTE_HOSTS`` box — and how a remote file is read/written)
live in separate files, each well under the ~1000-line cap (``architecture-
first.md``). ``cli_onboard`` re-exports every name below as a facade, so
``cli_onboard.<name>`` keeps resolving for callers and tests.

Core invariant (#583): local and remote share ONE code path — every detection,
read and write goes through ``_exec``, which is a plain ``subprocess.run`` for a
local host and an ssh-wrapped call for a remote one. A ``--host <remote>``
onboard therefore NEVER touches the local filesystem, and the whole layer is
offline-testable with an injected ssh runner.
"""

import os
import shlex
import subprocess


# --------------------------------------------------------------------------- #
# Injectable executor — local argv, or ssh-wrapped for a REMOTE_HOSTS target.
# --------------------------------------------------------------------------- #
def _run(run):
    return run or subprocess.run


def resolve_remote(host):
    """The REMOTE_HOSTS entry for `host` (name or ip), or None for local.
    dev1 / local / None run locally (this maintainer box)."""
    if host in (None, "", "local", "dev1"):
        return None
    try:
        import cli_fleet
        for h in cli_fleet.REMOTE_HOSTS:
            if h.get("name") == host or h.get("host") == host:
                return h
    except Exception:
        return None
    return None


def _ssh_prefix(remote):
    """The `ssh …user@host` argv prefix for a REMOTE_HOSTS entry, reused by
    `_exec` and `_remote_home`. BatchMode=yes + ConnectTimeout make the
    "refuse on unreachable" fail-safe load-bearing: without them a keyless or
    down box would PROMPT/HANG instead of returning non-zero (#583 review)."""
    # #680: route the inline StrictHostKeyChecking=no through the #669 pin
    # helper (the ONE source) -- a raw-public-IP target carrying a committed
    # host_keys pin (spinbike-vps) is verified STRICTLY; every tailscale/subdev
    # host keeps the unchanged =no. Deferred import (call-time) -- cli_remote
    # is fully imported by the time an onboard --host ssh runs.
    from cli_remote import host_key_check_opts
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    ssh += host_key_check_opts(remote)
    ident = remote.get("identity")
    if ident:
        ssh += ["-i", os.path.expanduser(ident)]
    ssh += ["%s@%s" % (remote["user"], remote["host"])]
    return ssh


def _exec(argv, host=None, run=None, input=None):
    """Run `argv` locally, or ssh-wrapped when `host` names a remote box. gh
    API calls (list/create with -R) pass host=None — they talk to GitHub from
    any box; only working-tree git / repo-create needs the host. Every argv
    element is `shlex.quote`-d for the ssh command string, so a project path
    with shell metacharacters can never break out (#583)."""
    remote = resolve_remote(host)
    if remote is None:
        return _run(run)(argv, capture_output=True, text=True, input=input)
    ssh = _ssh_prefix(remote) + [" ".join(shlex.quote(a) for a in argv)]
    return _run(run)(ssh, capture_output=True, text=True, input=input)


def _git(path, args, host=None, run=None):
    return _exec(["git", "-C", str(path), *args], host=host, run=run)


def _gh(args, run=None):
    """gh API call (repo-scoped via -R) — always local, never ssh."""
    return _run(run)(["gh", *args], capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# Remote path + filesystem, ALL routed through the injected runner (#583).
# --------------------------------------------------------------------------- #
def _remote_home(host, run=None):
    """The remote `$HOME` (local `~` for a local host). `$HOME` stays UNquoted
    so the remote shell expands it — a fixed literal with ZERO user data, no
    injection surface. None if the remote could not be reached/read."""
    remote = resolve_remote(host)
    if remote is None:
        return os.path.expanduser("~")
    r = _run(run)(_ssh_prefix(remote) + ['printf %s "$HOME"'],
                  capture_output=True, text=True)
    return (r.stdout or "").strip() or None


def _resolve_remote_path(orig_path, host, run=None):
    """Expand a leading `~` against the REMOTE home (never the local one — the
    #583 tilde bug). An absolute path is used verbatim; an unresolvable `~/…`
    (unreachable remote) returns None so the caller refuses. A RELATIVE remote
    path returns None too (it would silently anchor to the ssh login dir and
    the local cwd on a re-onboard) — the skill documents `~`/absolute only."""
    s = str(orig_path)
    if s == "~":
        return _remote_home(host, run=run)
    if s.startswith("~/"):
        home = _remote_home(host, run=run)
        return (home.rstrip("/") + s[1:]) if home else None
    if s.startswith("/"):
        return s
    return None


def _fs_exists(path, host=None, run=None, kind="e"):
    """True iff `path` exists on the target (local or remote), via `test`.
    kind: 'e' any / 'f' regular file / 'd' directory."""
    flag = {"e": "-e", "f": "-f", "d": "-d"}.get(kind, "-e")
    return _exec(["test", flag, str(path)], host=host, run=run).returncode == 0


def _read_file(path, host=None, run=None):
    """Text content of `path` on the target (via `cat`), or None if absent —
    never raises for a missing file."""
    r = _exec(["cat", str(path)], host=host, run=run)
    return r.stdout if r.returncode == 0 else None


def _write_file(path, content, host=None, run=None):
    """Write `content` to `path` on the target over the runner (local or ssh),
    via `sh -c 'cat > <quoted path>'` with `content` on stdin. The path is
    `shlex.quote`-d inside the `-c` string AND the whole `-c` argument is quoted
    again for ssh transport, so a hostile project path cannot inject (#583).
    Returns True on success (the caller surfaces a False as a skipped step)."""
    cmd = ["sh", "-c", "cat > %s" % shlex.quote(str(path))]
    return _exec(cmd, host=host, run=run, input=content).returncode == 0
