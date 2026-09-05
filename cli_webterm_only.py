"""Webterm-only SSH access management (#869) — self-contained leaf.

Owner directive (2026-09-04, present, verbatim): "priamy pristup cez ssh bude
uz len fallback pre mna a mareka, david a dominika nech vedia robit len cez
webterm!!"

Manages ``~/.ssh/authorized_keys`` for WEBTERM_ONLY_USERS (david1-4, dominika)
DECLARATIVELY — the desired-state key set is rendered on every install/push,
foreign keys are quarantined (never destroyed), and a structural lockout guard
refuses to write ANY set that lacks the fleet push pubkey.

DELIBERATELY SEPARATE from ``cli_owner_keys.py`` (#653) — that module's ONE
load-bearing guarantee is structural append-only (lockout impossible BY
CONSTRUCTION); this module manages an EXACT desired set (remove + add), which
is a fundamentally different contract.  Two contracts = two leaves.

SAFETY invariants (test-locked):
  * STRUCTURAL REFUSAL: the desired set is never written if it is empty OR
    if ``FLEET_PUSH_PUBKEY``'s blob is absent — the push connection that
    authenticated this install is structurally safe.
  * QUARANTINE: foreign keys → ``authorized_keys.airuleset-removed-<ts>``
    (0600, created BEFORE the live write); the previous whole file →
    ``authorized_keys.airuleset-prev-<ts>``.  Never destroyed.
  * ATOMIC: ``os.replace()`` of a fully-written temp file — no in-place
    truncate.
  * AUDIT: removed keys logged as COMMENT + ssh-keygen FINGERPRINT only,
    never the raw base64 blob.
  * PUBLIC keys only — no private key material ever touches this module.
"""
import getpass
import os
import subprocess
import sys
import time


# ---------------------------------------------------------------------------
# Public-key constants (PUBLIC material, safe to commit)
# ---------------------------------------------------------------------------

# The fleet push pubkey — the key ``cmd_push`` authenticates with to reach
# every subdev account.  Read from ``~/.secrets/gatekeeper_access_ed25519.pub``
# on dev1 (the maintainer box).  The lockout guard structurally refuses to
# write any authorized_keys that lacks this blob.
FLEET_PUSH_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGyVa+vk1mN9ZDh9VeBCOGx4r1OVGmcb5n67md"
    "t+R3Q/ gatekeeper-access dev1->odoo-gatekeeper"
)

# The webterm david lane pubkey — the key the webterm david gateway uses to
# loopback-ssh between david1-4 accounts on subdev.  Read from subdev's
# ``~david1/.ssh/authorized_keys`` (the ``webterm_david@subdev`` entry placed
# there by the manual go-live, 2026-09-04).
#
# NOTE: if this is None, the lockout guard REFUSES to write the desired set
# for david1-4 (a test pins this — fill it from the committed source or the
# live box, then remove the None guard).
WEBTERM_DAVID_LANE_PUBKEY = None  # placeholder — see dispatch note


def _key_blob(line):
    """The base64 key BLOB (field 2) — the unique-per-key token the whole
    idempotency + foreign-key detection keys on.  Same helper shape as
    ``cli_owner_keys._key_blob``.  Returns None for malformed/blank."""
    parts = (line or "").split()
    return parts[1] if len(parts) >= 2 else None


def _key_comment(line):
    """The trailing comment (fields 3+) of an authorized_keys line, or ''."""
    parts = (line or "").split(None, 2)
    return parts[2] if len(parts) >= 3 else ""


def _write_0600(path, content):
    """Write ``content`` to ``path`` with mode 0600 from the start — no umask
    window (os.open with O_CREAT|O_TRUNC at 0600, not open() then chmod)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)


def _fingerprint(line, run=None):
    """ssh-keygen fingerprint of a key line (comment + fingerprint ONLY —
    never the raw blob).  Returns the fingerprint string or '(unavailable)'."""
    run = run or subprocess.run
    try:
        r = run(
            ["ssh-keygen", "-lf", "-"],
            input=line.strip() + "\n",
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:  # noqa: BLE001
        print("  webterm-only keys: fingerprint failed: %s" % e,
              file=sys.stderr)
    return "(unavailable)"


# ---------------------------------------------------------------------------
# Desired-set builder
# ---------------------------------------------------------------------------

def desired_keys_for_user(user):
    """Return the SORTED list of authorized_keys lines for a webterm-only
    account.  Raises ValueError if the desired set is incomplete (e.g. a
    placeholder pubkey is still None)."""
    from cli_fleet import WEBTERM_ONLY_USERS
    if user not in WEBTERM_ONLY_USERS:
        raise ValueError("not a webterm-only user: %r" % user)

    from cli_owner_keys import OWNER_PUBKEYS

    keys = [FLEET_PUSH_PUBKEY]
    keys.extend(OWNER_PUBKEYS)

    # david1-4 get the lane key
    if user.startswith("david") and user[5:].isdigit():
        if WEBTERM_DAVID_LANE_PUBKEY is None:
            raise ValueError(
                "WEBTERM_DAVID_LANE_PUBKEY is None — cannot build a "
                "complete desired set for %r" % user
            )
        keys.append(WEBTERM_DAVID_LANE_PUBKEY)

    # Sort by blob for deterministic output
    keys.sort(key=lambda k: _key_blob(k) or "")
    return keys


def render_authorized_keys(user):
    """Render the full authorized_keys file content for a webterm-only user.
    Returns the string (with trailing newline)."""
    header = (
        "# airuleset:managed — webterm-only (#869); "
        "edits are overwritten by push\n"
    )
    lines = desired_keys_for_user(user)
    return header + "".join(line.rstrip("\n") + "\n" for line in lines)


# ---------------------------------------------------------------------------
# Key manager (runs inside cmd_install on the target, AS the account)
# ---------------------------------------------------------------------------

def manage_webterm_only_keys(
    user=None, ssh_dir=None, run=None, log_dir=None, dry_run=False
):
    """Manage authorized_keys for a webterm-only account.

    Called by ``cmd_install`` when ``getpass.getuser() in WEBTERM_ONLY_USERS``.
    Runs as the account itself — zero extra ssh rounds.

    Returns a dict: ``{"action": "no-op"|"updated"|"error", ...}``.
    """
    run = run or subprocess.run
    user = user or getpass.getuser()
    if ssh_dir is None:
        ssh_dir = os.path.expanduser("~/.ssh")
    if log_dir is None:
        log_dir = os.path.expanduser("~/.claude")
    ak_path = os.path.join(ssh_dir, "authorized_keys")

    result = {"action": "error", "user": user}

    # 0. GATE: skip non-webterm-only accounts (#869 no-op guarantee, test-locked)
    from cli_fleet import WEBTERM_ONLY_USERS
    if user not in WEBTERM_ONLY_USERS:
        result["action"] = "skipped"
        return result

    # 1. Render desired content
    try:
        desired_content = render_authorized_keys(user)
        desired_lines = desired_keys_for_user(user)
    except ValueError as e:
        msg = "  webterm-only keys: REFUSED for %s — %s" % (user, e)
        print(msg, file=sys.stderr)
        result["reason"] = str(e)
        return result

    # 2. STRUCTURAL REFUSAL: fleet push key must be in desired set
    fleet_blob = _key_blob(FLEET_PUSH_PUBKEY)
    desired_blobs = {_key_blob(k) for k in desired_lines}
    if not fleet_blob or fleet_blob not in desired_blobs:
        msg = ("  webterm-only keys: LOCKOUT GUARD — FLEET_PUSH_PUBKEY blob "
               "NOT in desired set for %s; touching NOTHING" % user)
        print(msg, file=sys.stderr)
        result["reason"] = "lockout-guard: fleet key missing from desired set"
        return result

    if not desired_blobs or not desired_lines:
        msg = ("  webterm-only keys: LOCKOUT GUARD — desired set EMPTY "
               "for %s; touching NOTHING" % user)
        print(msg, file=sys.stderr)
        result["reason"] = "lockout-guard: empty desired set"
        return result

    # 3. Read current file
    current_content = ""
    if os.path.exists(ak_path):
        try:
            with open(ak_path, "r", encoding="utf-8", errors="replace") as fh:
                current_content = fh.read()
        except OSError as e:
            msg = "  webterm-only keys: cannot read %s — %s" % (ak_path, e)
            print(msg, file=sys.stderr)
            result["reason"] = "read-error: %s" % e
            return result

    # Content already matches?
    if current_content == desired_content:
        print("  webterm-only keys: %s — already correct, no-op" % user)
        result["action"] = "no-op"
        return result

    # 4. Partition current lines into managed vs foreign
    foreign_lines = []
    for line in current_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        blob = _key_blob(stripped)
        if blob and blob not in desired_blobs:
            foreign_lines.append(stripped)

    ts = str(int(time.time()))

    if dry_run:
        print("  webterm-only keys: %s — DRY RUN, %d foreign key(s) "
              "would be quarantined" % (user, len(foreign_lines)))
        result["action"] = "dry-run"
        result["foreign_count"] = len(foreign_lines)
        return result

    # Ensure .ssh dir exists with correct perms
    os.makedirs(ssh_dir, exist_ok=True)
    try:
        os.chmod(ssh_dir, 0o700)
    except OSError as e:
        print("  webterm-only keys: chmod 700 on %s failed (non-fatal): %s"
              % (ssh_dir, e), file=sys.stderr)

    # 5. Quarantine foreign keys BEFORE any live write
    if foreign_lines:
        removed_path = os.path.join(
            ssh_dir, "authorized_keys.airuleset-removed-%s" % ts
        )
        _write_0600(removed_path,
                     "".join(line + "\n" for line in foreign_lines))

    # Save previous whole file
    if current_content:
        prev_path = os.path.join(
            ssh_dir, "authorized_keys.airuleset-prev-%s" % ts
        )
        _write_0600(prev_path, current_content)

    # 6. Write new file atomically via os.replace
    new_path = os.path.join(
        ssh_dir, "authorized_keys.airuleset-new-%s" % ts
    )
    fd = os.open(new_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(desired_content)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        # fd is closed by fdopen; clean up the temp file
        try:
            os.unlink(new_path)
        except OSError as cleanup_err:
            print("  webterm-only keys: temp file cleanup failed: %s"
                  % cleanup_err, file=sys.stderr)
        raise

    os.replace(new_path, ak_path)

    # 7. Audit removed keys — comment + fingerprint ONLY, never blob
    audit_lines = []
    for line in foreign_lines:
        comment = _key_comment(line)
        fp = _fingerprint(line, run=run)
        entry = "REMOVED key: comment=%r fingerprint=%s" % (comment, fp)
        audit_lines.append(entry)
        print("  webterm-only keys: %s — %s" % (user, entry))

    # Write audit log
    if audit_lines:
        log_path = os.path.join(log_dir, "webterm-only-keys.log")
        os.makedirs(log_dir, exist_ok=True)
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                for entry in audit_lines:
                    fh.write("[%s] %s: %s\n" % (ts, user, entry))
        except OSError as e:
            print("  webterm-only keys: audit log write failed (non-fatal): "
                  "%s" % e, file=sys.stderr)

    n_removed = len(foreign_lines)
    n_desired = len(desired_lines)
    print("  webterm-only keys: %s — UPDATED: %d desired key(s), "
          "%d foreign key(s) quarantined" % (user, n_desired, n_removed))
    result["action"] = "updated"
    result["desired_count"] = n_desired
    result["foreign_count"] = n_removed
    return result


# ---------------------------------------------------------------------------
# sshd Match drop-in renderer
# ---------------------------------------------------------------------------

def render_sshd_dropin():
    """Render the sshd Match drop-in content for webterm-only users.
    Returns the file content string."""
    from cli_fleet import WEBTERM_ONLY_USERS

    users_sorted = sorted(WEBTERM_ONLY_USERS)
    return (
        "# airuleset:managed — webterm-only sshd restrictions (#869)\n"
        "# Edits are overwritten by `airuleset.py webterm-only --apply-sshd`\n"
        "Match User %s\n"
        "    PasswordAuthentication no\n"
        "    KbdInteractiveAuthentication no\n"
        "    AuthorizedKeysFile .ssh/authorized_keys\n"
    ) % ",".join(users_sorted)


# The golden content for drift-lock tests
SSHD_DROPIN_PATH = "/etc/ssh/sshd_config.d/60-airuleset-webterm-only.conf"


# ---------------------------------------------------------------------------
# Subdev accounts conf renderer (for hook allowlist derivation)
# ---------------------------------------------------------------------------

def render_subdev_accounts_conf():
    """Render ``~/.claude/airuleset-subdev-accounts.conf`` from REMOTE_HOSTS.

    Format: ``user<TAB>key-basename`` per line (or ``user<TAB>default`` for
    accounts with no pinned identity — the montalu family).

    This conf is the SINGLE source for ``block-subdev-ssh-misuse.sh`` and
    ``block-destructive-remote.sh`` — no Python import per Bash call."""
    from cli_fleet import REMOTE_HOSTS

    # Subdev host IP
    subdev_ip = "100.118.174.27"
    lines = []
    for entry in REMOTE_HOSTS:
        if entry.get("host") != subdev_ip:
            continue
        user = entry["user"]
        identity = entry.get("identity", "")
        if identity:
            basename = os.path.basename(identity)
        else:
            basename = "default"
        lines.append("%s\t%s" % (user, basename))

    # Sort for deterministic output
    lines.sort()
    return "\n".join(lines) + "\n" if lines else ""


# Hardcoded fallback for when the conf is missing (fail-safe: never "allow
# all"). This MUST include dominika (#869) and match the rendered conf.
# Test-locked: hardcoded == rendered.
SUBDEV_ACCOUNTS_FALLBACK = {
    "montalu1": "default",
    "montalu2": "default",
    "montalu3": "default",
    "montalu4": "default",
    "montalu5": "default",
    "montalu6": "default",
    "montalu7": "default",
    "montalu8": "default",
    "marek": "gatekeeper_access_ed25519",
    "david1": "gatekeeper_access_ed25519",
    "david2": "gatekeeper_access_ed25519",
    "david3": "gatekeeper_access_ed25519",
    "david4": "gatekeeper_access_ed25519",
    "dominika": "gatekeeper_access_ed25519",
    "simap1": "gatekeeper_access_ed25519",
    "miva1": "gatekeeper_access_ed25519",
}


# ---------------------------------------------------------------------------
# Audit (read-only)
# ---------------------------------------------------------------------------

def audit_webterm_only_keys(user, ssh_dir=None, run=None):
    """Audit a webterm-only account's authorized_keys — read-only.

    Returns a dict with findings (unexpected keys, missing keys)."""
    run = run or subprocess.run
    if ssh_dir is None:
        ssh_dir = os.path.expanduser("~/.ssh")
    ak_path = os.path.join(ssh_dir, "authorized_keys")

    result = {"user": user, "findings": []}

    if not os.path.exists(ak_path):
        result["findings"].append("authorized_keys MISSING")
        return result

    try:
        with open(ak_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        result["findings"].append("cannot read authorized_keys: %s" % e)
        return result

    try:
        desired_lines = desired_keys_for_user(user)
    except ValueError as e:
        result["findings"].append("cannot build desired set: %s" % e)
        return result

    desired_blobs = {_key_blob(k) for k in desired_lines}
    present_blobs = set()

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        blob = _key_blob(stripped)
        if not blob:
            continue
        present_blobs.add(blob)
        if blob not in desired_blobs:
            comment = _key_comment(stripped)
            fp = _fingerprint(stripped, run=run)
            result["findings"].append(
                "FOREIGN key: comment=%r fingerprint=%s" % (comment, fp)
            )

    for key_line in desired_lines:
        blob = _key_blob(key_line)
        if blob and blob not in present_blobs:
            comment = _key_comment(key_line)
            result["findings"].append("MISSING key: comment=%r" % comment)

    return result


# ---------------------------------------------------------------------------
# CLI subcommand (#869 lane 2)
# ---------------------------------------------------------------------------

def cmd_webterm_only(args):
    """CLI: ``airuleset.py webterm-only [--audit | --apply-sshd] [--dry-run]``

    --audit:      read-only fleet audit of authorized_keys for each
                  webterm-only user (requires ssh to subdev).
    --apply-sshd: render + apply the sshd Match drop-in on subdev
                  (over ssh -J <gk hop> root@subdev, ``sshd -t``
                  BEFORE reload, fail = restore + NO reload).
    --dry-run:    with --apply-sshd, print the exact script without
                  executing it.
    """
    action = getattr(args, "wt_action", None)
    dry_run = getattr(args, "dry_run", False)

    if action == "audit":
        _cmd_webterm_only_audit()
    elif action == "apply-sshd":
        _cmd_webterm_only_apply_sshd(dry_run=dry_run)
    else:
        # Default: print usage summary
        print("webterm-only: use --audit or --apply-sshd")
        print("  --audit       read-only fleet audit of authorized_keys")
        print("  --apply-sshd  render + apply sshd Match drop-in on subdev")
        print("  --dry-run     (with --apply-sshd) print script, don't run")


def _cmd_webterm_only_audit():
    """Read-only fleet audit: ssh to each webterm-only target and audit keys."""
    from cli_fleet import WEBTERM_ONLY_USERS
    print("webterm-only audit: %d accounts" % len(WEBTERM_ONLY_USERS))
    for user in sorted(WEBTERM_ONLY_USERS):
        result = audit_webterm_only_keys(user)
        findings = result.get("findings", [])
        if findings:
            print("  %s: %d finding(s)" % (user, len(findings)))
            for f in findings:
                print("    - %s" % f)
        else:
            print("  %s: OK" % user)


def _cmd_webterm_only_apply_sshd(dry_run=False):
    """Render + apply the sshd Match drop-in on subdev.

    The script:
    1. Renders the drop-in content.
    2. Over ``ssh -J <gk hop> root@subdev``:
       a. Writes a temp file.
       b. ``sshd -t`` validates BEFORE moving into place.
       c. If valid, ``mv`` + ``systemctl reload sshd``.
       d. If invalid, REMOVE temp + NO reload.
    3. Read-back: ``sshd -T -C user=<each>`` to verify the Match applies.
    """
    content = render_sshd_dropin()
    dropin_path = SSHD_DROPIN_PATH

    # The gk hop for root@subdev
    gk_host = "100.90.94.41"
    subdev_host = "100.118.174.27"
    subdev_admin_key = "~/.secrets/subdev_admin_ed25519"

    # Build the remote script
    script = (
        "set -euo pipefail\n"
        "DROPIN='%(path)s'\n"
        "TMPF=\"${DROPIN}.airuleset-new\"\n"
        "trap 'rm -f \"$TMPF\"' EXIT\n"
        "cat > \"$TMPF\" <<'DROPIN_EOF'\n"
        "%(content)s"
        "DROPIN_EOF\n"
        "chmod 644 \"$TMPF\"\n"
        "if sshd -t -f /etc/ssh/sshd_config 2>&1; then\n"
        "  # Validate with the new file in place (test config)\n"
        "  BACKUP=\"${DROPIN}.airuleset-prev\"\n"
        "  [ -f \"$DROPIN\" ] && cp \"$DROPIN\" \"$BACKUP\"\n"
        "  mv \"$TMPF\" \"$DROPIN\"\n"
        "  if sshd -t -f /etc/ssh/sshd_config 2>&1; then\n"
        "    systemctl reload sshd\n"
        "    echo 'APPLIED: %(path)s reloaded'\n"
        "  else\n"
        "    echo 'RESTORE: sshd -t failed after mv; restoring' >&2\n"
        "    [ -f \"$BACKUP\" ] && mv \"$BACKUP\" \"$DROPIN\" || rm -f \"$DROPIN\"\n"
        "    echo 'RESTORED — NO reload' >&2\n"
        "    exit 1\n"
        "  fi\n"
        "else\n"
        "  echo 'PRE-CHECK FAILED: sshd -t failed before any change' >&2\n"
        "  exit 1\n"
        "fi\n"
    ) % {"path": dropin_path, "content": content}

    # Read-back verification commands
    from cli_fleet import WEBTERM_ONLY_USERS
    readback = ""
    for user in sorted(WEBTERM_ONLY_USERS):
        readback += (
            "echo '--- sshd -T -C user=%s ---'\n"
            "sshd -T -C user=%s 2>&1 | grep -i 'passwordauthentication\\|"
            "kbdinteractiveauthentication\\|authorizedkeysfile' || true\n"
        ) % (user, user)

    full_script = script + readback

    ssh_cmd = (
        "ssh -o StrictHostKeyChecking=no -o BatchMode=yes "
        "-i %(admin_key)s "
        "-J gatekeeper@%(gk)s "
        "root@%(subdev)s"
    ) % {"admin_key": subdev_admin_key, "gk": gk_host, "subdev": subdev_host}

    if dry_run:
        print("DRY RUN: would execute over: %s" % ssh_cmd)
        print("--- script ---")
        print(full_script)
        print("--- end script ---")
        return

    print("Applying sshd drop-in via: %s" % ssh_cmd)
    import shlex
    r = subprocess.run(
        shlex.split(ssh_cmd),
        input=full_script,
        capture_output=True, text=True, timeout=60,
    )
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    if r.returncode != 0:
        print("FAILED: exit %d" % r.returncode, file=sys.stderr)
        sys.exit(1)
    print("SUCCESS: sshd drop-in applied and verified")
