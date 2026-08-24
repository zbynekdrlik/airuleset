"""Owner VPS-class sudo provisioning (#659) -- a self-contained leaf.

Owner report (2026-08-24, spinbike-vps): *"ten claude tam by mal mat aj sudo
ak nema aby mohol realne na tom boxe plnohodnotne pracovat."* An owner VPS is
the owner's OWN machine; the claude working there needs full operational
capability (systemctl, apt, journalctl of system units), so the owner's
working account gets passwordless sudo.

`provision_owner_sudo()` installs `/etc/sudoers.d/<owner-user>` =
`"<user> ALL=(ALL) NOPASSWD:ALL"`, VALIDATED with `visudo -cf` on the candidate
file BEFORE it is moved into place -- so a malformed file can never land and
lock the box out of sudo. It is folded into `cmd_install`, so the deploy loop's
existing `git pull && install` connection provisions the target with ZERO extra
ssh round (the same Pattern A as `cli_owner_keys.provision_owner_keys`, #653).

SCOPE / SECURITY -- the #659 hard invariants, enforced structurally here:
  * OWNER VPS ONLY. Gated on the `AIRULESET_OWNER_VPS=1` env the deploy loop
    sets ONLY for a `REMOTE_HOSTS` entry flagged `"owner_vps": True` -- never a
    blanket rule, and the local dev1 install (run directly, not via the deploy
    loop) never sets the env, so dev1 is never auto-sudoed. Sub-dev stream
    accounts stay sudo-less by design (their entries carry no flag).
  * NARROW. The sudoers file names ONLY the single owner user, never `ALL`
    users. The username is charset-validated (`_SAFE_USER_RE`) before it is
    interpolated into either the file NAME or its content -- no path traversal,
    no sudoers-syntax injection possible.
  * VALIDATED. `visudo -cf <candidate>` gates the install; an invalid file is
    discarded, never moved into `/etc/sudoers.d`.
  * ATOMIC + append-safe. The candidate is written to a `.`-prefixed temp in
    `/etc/sudoers.d` (sudo's `#includedir` ignores any filename containing a
    `.`, so a half-written candidate is never parsed) and `mv`'d into place
    atomically once valid.
  * BEST-EFFORT, gated on `sudo -n true`. Writing `/etc/sudoers.d` needs sudo;
    on a box without passwordless sudo yet, `sudo -n` fails and this LOUD-reports
    the one-time manual bootstrap rather than prompting. It never raises, so a
    provisioning hiccup never aborts the rest of install (same discipline as
    `cli_owner_keys._provision_root_keys`).
"""
import os
import re
import shlex
import shutil
import subprocess
import sys


# A conservative POSIX unix-username charset. `visudo` accepts far more, but the
# owner accounts on the managed fleet are all plain lowercase names (newlevel,
# ...), and a strict allowlist is what makes interpolating `user` into both the
# sudoers CONTENT and the destination FILENAME injection-proof by construction.
_SAFE_USER_RE = re.compile(r"\A[a-z_][a-z0-9_-]{0,31}\Z")


def _owner_vps_signalled(env=None) -> bool:
    """True iff this install was invoked with `AIRULESET_OWNER_VPS=1` -- the
    signal the deploy loop sets ONLY for an `owner_vps` REMOTE_HOSTS target."""
    e = env if env is not None else os.environ
    return e.get("AIRULESET_OWNER_VPS") == "1"


def _resolve_owner_user() -> str:
    """The current unix username -- the owner user on an owner VPS. Resolved via
    `getpass.getuser()` (honours $LOGNAME/$USER, then the passwd db), the same
    account whose `~` the rest of install provisions."""
    import getpass
    return getpass.getuser()


def _sudoers_install_script(dest: str) -> str:
    """A POSIX-sh script (run under `sudo -n sh -c`) that idempotently installs
    a `visudo`-validated sudoers file at `dest`, reading the desired content
    from stdin. Prints exactly one of AIRULESET_SUDOERS_{UNCHANGED,INSTALLED}
    on success, or AIRULESET_SUDOERS_INVALID to stderr + exit 3 on a validation
    failure. The candidate is a `.`-prefixed temp in `dest`'s dir (sudo ignores
    dotted filenames, so it is never parsed while half-written) and is `mv`'d
    into place atomically only after `visudo -cf` passes -- never a truncating
    write to `dest` itself."""
    d = shlex.quote(dest)
    return (
        # umask 077 -> the temp candidate is created 0600 (owner-writable, so
        # the `printf >` write works whether run as root in prod or a non-root
        # tester); the final 0440 root:root mode is set explicitly by the
        # `chmod 0440` below, right before the atomic mv, so the installed
        # sudoers file is never group/world-readable.
        "set -e; umask 077; dest=" + d + "; "
        "want=$(cat); "
        # idempotent: identical existing content -> no-op (both sides go through
        # $() which strips trailing newlines, so they compare cleanly).
        "if [ -f \"$dest\" ] && [ \"$(cat \"$dest\")\" = \"$want\" ]; then "
        "echo AIRULESET_SUDOERS_UNCHANGED; exit 0; fi; "
        "tmp=$(mktemp \"$(dirname \"$dest\")/.airuleset-owner-sudo-XXXXXX\"); "
        "printf '%s\\n' \"$want\" > \"$tmp\"; "
        "if visudo -cf \"$tmp\" >/dev/null 2>&1; then "
        "chown root:root \"$tmp\"; chmod 0440 \"$tmp\"; "
        "mv -f \"$tmp\" \"$dest\"; echo AIRULESET_SUDOERS_INSTALLED; "
        "else rm -f \"$tmp\"; echo AIRULESET_SUDOERS_INVALID >&2; exit 3; fi"
    )


def provision_owner_sudo(user=None, run=None, sudoers_dir="/etc/sudoers.d",
                          require_signal=True):
    """Install NOPASSWD sudo for the single owner user on an owner VPS-class box
    (#659). Called by `cmd_install`; gated on the `AIRULESET_OWNER_VPS=1` env
    (owner_vps targets only) and on passwordless sudo. Best-effort, non-fatal,
    LOUD on every skip/failure. Returns a short status string.

    `user` defaults to the current unix user; `run` defaults to
    `subprocess.run` (injectable for tests); `require_signal=False` lets a test
    exercise the provisioning path without the env gate."""
    run = run or subprocess.run
    if require_signal and not _owner_vps_signalled():
        return "not-owner-vps"

    user = user or _resolve_owner_user()
    if not _SAFE_USER_RE.match(user or ""):
        print("  ⚠ owner sudo: refusing to provision for unsafe username %r "
              "(expected a plain unix account name)" % user, file=sys.stderr)
        return "unsafe-username"

    if not shutil.which("sudo"):
        print("  ⚠ owner sudo: this box is flagged owner_vps but has no `sudo` "
              "binary -- cannot provision NOPASSWD for %s" % user,
              file=sys.stderr)
        return "no-sudo"

    try:
        probe = run(["sudo", "-n", "true"], capture_output=True, text=True,
                    timeout=10)
    except Exception as e:  # noqa: BLE001 -- best-effort, never break install
        print("  ⚠ owner sudo: sudo probe error (non-fatal): %r" % e,
              file=sys.stderr)
        return "sudo-probe-error"
    if getattr(probe, "returncode", 1) != 0:
        print("  owner sudo: box is owner_vps but %s has NO passwordless sudo "
              "yet -- one-time manual bootstrap needed. As a sudo-capable user "
              "run:\n    echo '%s ALL=(ALL) NOPASSWD:ALL' | sudo EDITOR='tee' "
              "visudo -f %s/%s\n  then the next push maintains it automatically."
              % (user, user, sudoers_dir, user), file=sys.stderr)
        return "no-passwordless-sudo"

    dest = os.path.join(sudoers_dir, user)
    content = "%s ALL=(ALL) NOPASSWD:ALL" % user
    script = _sudoers_install_script(dest)
    try:
        r = run(["sudo", "-n", "sh", "-c", script], input=content + "\n",
                capture_output=True, text=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        print("  ⚠ owner sudo: install error (non-fatal): %r" % e,
              file=sys.stderr)
        return "install-error"
    if getattr(r, "returncode", 1) != 0:
        print("  ⚠ owner sudo: install FAILED (rc=%s) -- %s left unchanged: %s"
              % (r.returncode, dest, (r.stderr or "").strip()[:200]),
              file=sys.stderr)
        return "install-failed"

    out = (r.stdout or "")
    if "AIRULESET_SUDOERS_UNCHANGED" in out:
        print("  owner sudo: %s already NOPASSWD (visudo-valid) -- unchanged"
              % dest)
        return "unchanged"
    print("  owner sudo: installed %s (NOPASSWD for %s, visudo-validated)"
          % (dest, user))
    return "provisioned"
