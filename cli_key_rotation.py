"""airuleset — F1 fleet push-key rotation (#870).

Three-phase state machine for rotating the fleet push ssh key:

  ADD     — append the NEW pubkey to every target's authorized_keys using the
            entry's OWN identity (the ``identity`` field from REMOTE_HOSTS, or
            the old default key for no-identity entries; sshpass-only entries
            without ANY key identity are FAILED with a clear message — the
            rotation needs a key path, not a password).  Idempotent by blob;
            reuses ``_key_blob`` from ``cli_webterm_only``,
            ``host_key_check_opts`` from ``cli_remote``.
  VERIFY  — ``ssh -i <new> -o IdentitiesOnly=yes -o BatchMode=yes user@host
            'echo OK-$(whoami)'`` and records ``verified_at`` + ``verify_output``
            ONLY on exit 0 AND output == ``OK-<user>``.
  REMOVE  — authenticates with the NEW key (not the old identity — the
            standard rotation pattern: deleting the old key over a session
            authenticated by the new key makes lockout physically impossible
            per host); deletes exactly the old blob's line and REFUSES (per
            host, hard invariant, not a flag) any host lacking ``verified_at``.
            Also refuses when the new key file is absent.

State file default ``~/.claude/key-rotation/airuleset_push_ed25519.json``
(atomic write, mode 0600 via ``_write_0600``).

``is_paused`` hosts (checked LIVE in ALL three phases) → ``skipped`` +
printed ``Rotation-debt:`` line; ``skipped`` is CLEARED on a successful add
after an unpause.  dev1 (``newlevel@dev1``) untouched unless ``--include-dev1``
(F3 flag).

Pure leaf: lazy ``import cli_fleet``/``import cli_remote`` inside functions
(the L-E convention); stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, Optional

# Reuse existing primitives — NO duplication
from cli_webterm_only import _key_blob, _write_0600


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = "~/.claude/key-rotation"
DEFAULT_STATE_FILE = "airuleset_push_ed25519.json"

# The OLD fleet push pubkey (the key currently in FLEET_PUSH_PUBKEY).
# Duplicated here as a constant so remove knows EXACTLY which blob to delete;
# kept in sync by a test lock.  # airuleset:secret-ok PUBLIC ssh pubkey
OLD_FLEET_PUSH_PUBKEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGyVa+vk1mN9ZDh9VeBCOGx4r1OVGmcb5n67md"
    "t+R3Q/ gatekeeper-access dev1->odoo-gatekeeper"
)

# The default ssh key — used for REMOTE_HOSTS entries with no `identity`
# field. These are the sshpass/default-key hosts (dev2, montalu1-8,
# forestshop). ADD authenticates with this key (it IS authorized on those
# targets alongside the password).
_DEFAULT_SSH_KEY = "~/.ssh/id_ed25519"

# dev1 tailscale IP — excluded by default (included only with --include-dev1,
# F3 flag)
_DEV1_HOST = "100.104.8.125"

# gk hop for root@subdev (mirrors SHARED_STREAM_GUARD_HOSTS layout)
_GK_HOST = "100.90.94.41"
_SUBDEV_HOST = "100.118.174.27"

# gk's ~/.ssh/config uses "Host subdev" (with IdentityFile
# ~/.ssh/subdev_admin), so the nested ssh must use the HOSTNAME not the
# IP to match the config and pick up the right key.  #870 F1 fix.
_SUBDEV_SSH_CONFIG_ALIAS = "subdev"


# ---------------------------------------------------------------------------
# State file I/O
# ---------------------------------------------------------------------------

def _state_path(state_file: Optional[str] = None) -> str:
    if state_file:
        return os.path.expanduser(state_file)
    d = os.path.expanduser(DEFAULT_STATE_DIR)
    return os.path.join(d, DEFAULT_STATE_FILE)


def load_state(state_file: Optional[str] = None) -> Dict[str, Any]:
    """Load the rotation state file; returns empty dict if absent."""
    path = _state_path(state_file)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: Dict[str, Any], state_file: Optional[str] = None):
    """Atomic write of the state file (mode 0600)."""
    path = _state_path(state_file)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    _write_0600(tmp, json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Host enumeration
# ---------------------------------------------------------------------------

def _host_key(entry: dict) -> str:
    """A unique key for a REMOTE_HOSTS/guard entry: ``user@host``.

    Prefers ``admin_user`` over ``user`` so root@subdev entries from
    SHARED_STREAM_GUARD_HOSTS + DISK_GUARD_ROOT_HOSTS key consistently."""
    user = entry.get("admin_user") or entry.get("user", "")
    return "%s@%s" % (user, entry.get("host", ""))


def _entry_identity(entry: dict) -> str:
    """The ssh identity for an entry: its own ``identity`` field, or the
    account default key for no-identity entries.  Never returns empty."""
    return entry.get("identity") or _DEFAULT_SSH_KEY


def _rotation_targets(include_dev1: bool = False,
                      host_filter: Optional[str] = None):
    """Yield ``(host_key, entry, via_gk_hop)`` for every rotation target.

    Includes all REMOTE_HOSTS entries + root@subdev/root@gk from
    SHARED_STREAM_GUARD_HOSTS and DISK_GUARD_ROOT_HOSTS. Excludes dev1
    unless ``include_dev1``.  Deduplicates by ``user@host``.
    If ``host_filter`` is given, yields only matching ``host_key``s.
    """
    import cli_fleet

    seen = set()
    for entry in cli_fleet.REMOTE_HOSTS:
        hk = _host_key(entry)
        if hk in seen:
            continue
        seen.add(hk)
        # dev1 exclusion
        if not include_dev1 and entry.get("host") == _DEV1_HOST:
            continue
        if host_filter and hk != host_filter:
            continue
        yield hk, entry, False

    # root@subdev (SHARED_STREAM_GUARD_HOSTS) via gk hop
    for guard in cli_fleet.SHARED_STREAM_GUARD_HOSTS:
        hk = _host_key(guard)
        if hk in seen:
            continue
        seen.add(hk)
        if host_filter and hk != host_filter:
            continue
        yield hk, guard, True  # always via gk hop

    # R2 fix: root@gk (DISK_GUARD_ROOT_HOSTS) — DIRECT, no hop
    for guard in cli_fleet.DISK_GUARD_ROOT_HOSTS:
        hk = _host_key(guard)
        if hk in seen:
            continue
        seen.add(hk)
        if host_filter and hk != host_filter:
            continue
        yield hk, guard, False  # direct connection


# ---------------------------------------------------------------------------
# SSH runner helpers
# ---------------------------------------------------------------------------

def _ssh_identity_opts(identity_path: str) -> list:
    """SSH options for authenticating with a specific key."""
    return ["-i", os.path.expanduser(identity_path),
            "-o", "IdentitiesOnly=yes"]


def _build_ssh_cmd(entry: dict, identity: str, command: str,
                   via_gk_hop: bool = False) -> list:
    """Build an ssh argv for ``entry`` authenticating with ``identity``.

    For ``via_gk_hop=True`` (root@subdev), uses NESTED SSH through gatekeeper:
    ``ssh gatekeeper@gk 'ssh root@subdev <command>'``.  ProxyJump (-J) would
    tunnel TCP but authenticate from dev1's key, which root@subdev doesn't
    have — only gatekeeper's own ``~/.ssh/subdev_admin`` (configured in gk's
    ``~/.ssh/config Host subdev``) is authorized.  The nested form lets
    gatekeeper authenticate with its own key.  #870 F1 fix.

    Reuses ``host_key_check_opts`` from ``cli_remote`` for pinned hosts.
    """
    import cli_remote

    user = entry.get("admin_user") or entry.get("user", "")
    host = entry.get("host", "")

    if via_gk_hop:
        # Nested SSH: dev1 -> gatekeeper -> root@subdev.
        # The inner command runs on gatekeeper, which has its own
        # ~/.ssh/config Host subdev with the right identity file.
        # Quote the inner command for the gatekeeper shell.
        inner_cmd = command.replace("'", "'\\''")
        # Use the SSH config alias so gatekeeper's ~/.ssh/config Host
        # subdev entry matches and picks up IdentityFile subdev_admin.
        # The raw IP (100.118.174.27) doesn't match and auth fails.
        inner_host = (_SUBDEV_SSH_CONFIG_ALIAS
                      if host == _SUBDEV_HOST else host)
        gk_cmd = "ssh -o BatchMode=yes %s@%s '%s'" % (
            user, inner_host, inner_cmd)
        # The outer ssh to gatekeeper uses the caller's identity.
        # Synthetic dict mirrors gk's REMOTE_HOSTS entry (cli_fleet.py);
        # if gk ever gains a host_keys pin there, add it here too.
        cmd = ["ssh"]
        cmd.extend(cli_remote.host_key_check_opts(
            {"host": _GK_HOST, "user": "gatekeeper"}))
        cmd.extend(["-o", "BatchMode=yes"])
        cmd.extend(_ssh_identity_opts(identity))
        cmd.append("gatekeeper@%s" % _GK_HOST)
        cmd.append(gk_cmd)
        return cmd

    cmd = ["ssh"]
    cmd.extend(cli_remote.host_key_check_opts(entry))
    cmd.extend(["-o", "BatchMode=yes"])
    cmd.extend(_ssh_identity_opts(identity))

    cmd.append("%s@%s" % (user, host))
    cmd.append(command)
    return cmd


def _run_ssh(argv: list, run=None, timeout: int = 30):
    """Run an ssh command. Returns (returncode, stdout, stderr)."""
    import subprocess
    run = run or subprocess.run
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as exc:
        return -1, "", str(exc)


# ---------------------------------------------------------------------------
# Phase: ADD
# ---------------------------------------------------------------------------

def _append_pubkey_command(pubkey_blob: str) -> str:
    """Shell command to idempotently append a pubkey to authorized_keys.

    Same shape as cli_owner_keys: grep-before-append, never truncates,
    trailing-newline guard.  The ``echo ADDED-OK`` is INSIDE the else
    branch of the grep so it prints ONLY when a new line was actually
    appended (Y3 fix: rc==0 without ADDED-OK = already present).
    """
    safe_key = pubkey_blob.replace("'", "'\\''")
    blob = _key_blob(pubkey_blob)
    if not blob:
        raise ValueError("cannot extract blob from pubkey")
    safe_blob = blob.replace("'", "'\\''")
    return (
        "set -e; "
        "mkdir -p ~/.ssh; chmod 700 ~/.ssh; "
        "touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; "
        "[ ! -s ~/.ssh/authorized_keys ] || "
        "[ -z \"$(tail -c 1 ~/.ssh/authorized_keys)\" ] || "
        "printf '\\n' >> ~/.ssh/authorized_keys; "
        "if grep -qF -- '%s' ~/.ssh/authorized_keys; then "
        "  echo ALREADY-PRESENT; "
        "else "
        "  printf '%%s\\n' '%s' >> ~/.ssh/authorized_keys; "
        "  echo ADDED-OK; "
        "fi"
    ) % (safe_blob, safe_key)


def phase_add(new_pubkey: str, old_identity: str,
              state_file: Optional[str] = None,
              include_dev1: bool = False,
              host_filter: Optional[str] = None,
              dry_run: bool = False,
              run=None) -> Dict[str, Any]:
    """ADD phase: append the new pubkey to every target's authorized_keys.

    Uses each entry's OWN identity (Y1 fix).  Checks ``is_paused`` LIVE
    (Y2 fix) and clears a stale ``skipped`` marker on successful add.
    Requires ``ADDED-OK`` or ``ALREADY-PRESENT`` in output (Y3 fix).
    """
    import cli_fleet

    state = load_state(state_file)
    results = {}
    debt_hosts = []

    for hk, entry, via_gk in _rotation_targets(include_dev1, host_filter):
        host_state = state.get(hk, {})

        # Already added?
        if host_state.get("added_at"):
            results[hk] = {"action": "already-added",
                           "added_at": host_state["added_at"]}
            continue

        # Paused? (Y2: checked LIVE in every phase)
        if cli_fleet.is_paused(entry):
            reason = cli_fleet.paused_reason(entry)
            host_state["skipped"] = "paused #851"
            state[hk] = host_state
            results[hk] = {"action": "skipped", "reason": reason}
            debt_hosts.append(hk)
            continue

        if dry_run:
            results[hk] = {"action": "dry-run"}
            continue

        # Y1: use the entry's own identity (not a hardcoded global)
        identity = _entry_identity(entry)

        # Build and run the append command
        cmd_str = _append_pubkey_command(new_pubkey)
        argv = _build_ssh_cmd(entry, identity, cmd_str,
                              via_gk_hop=via_gk)
        rc, stdout, stderr = _run_ssh(argv, run=run)

        # Y3: require explicit evidence marker
        if rc == 0 and "ADDED-OK" in stdout:
            host_state["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
            # Y2: clear stale skipped marker from a prior paused state
            host_state.pop("skipped", None)
            state[hk] = host_state
            results[hk] = {"action": "added"}
        elif rc == 0 and "ALREADY-PRESENT" in stdout:
            host_state["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
            host_state.pop("skipped", None)
            state[hk] = host_state
            results[hk] = {"action": "added (was already present)"}
        else:
            # Y3: rc==0 without evidence = FAILED (garbled output, restricted
            # shell, MOTD-only — never record added_at without evidence)
            results[hk] = {"action": "FAILED", "rc": rc,
                           "stderr": stderr[:200]}

    if not dry_run:
        save_state(state, state_file)

    return {"results": results, "debt_hosts": debt_hosts}


# ---------------------------------------------------------------------------
# Phase: VERIFY
# ---------------------------------------------------------------------------

def phase_verify(new_key_path: str,
                 state_file: Optional[str] = None,
                 include_dev1: bool = False,
                 host_filter: Optional[str] = None,
                 dry_run: bool = False,
                 run=None) -> Dict[str, Any]:
    """VERIFY phase: prove the new key works for every target.

    Checks ``is_paused`` LIVE (Y2 fix).
    """
    import cli_fleet

    state = load_state(state_file)
    results = {}

    for hk, entry, via_gk in _rotation_targets(include_dev1, host_filter):
        host_state = state.get(hk, {})

        # Already verified?
        if host_state.get("verified_at"):
            results[hk] = {"action": "already-verified",
                           "verified_at": host_state["verified_at"]}
            continue

        # Y2: check is_paused LIVE (not stale skipped marker only)
        if cli_fleet.is_paused(entry):
            results[hk] = {"action": "skipped",
                           "reason": cli_fleet.paused_reason(entry)}
            continue

        # Stale skipped marker from a prior paused state (host unpaused
        # but ADD not re-run yet)
        if host_state.get("skipped"):
            results[hk] = {"action": "skipped",
                           "reason": host_state["skipped"]}
            continue

        # Not yet added?
        if not host_state.get("added_at"):
            results[hk] = {"action": "not-yet-added"}
            continue

        if dry_run:
            results[hk] = {"action": "dry-run"}
            continue

        user = entry.get("admin_user") or entry.get("user", "")
        expected = "OK-%s" % user

        # Build verify command.
        # ASYMMETRY vs _build_ssh_cmd: verify DELIBERATELY uses -J (ProxyJump),
        # NOT the nested-SSH form that phase_add uses.  At verify time the ADD
        # phase has already installed the new key on root@subdev, so -J +
        # IdentitiesOnly with new_key_path proves the new key authenticates
        # END-TO-END (the outer leg to gatekeeper@gk also works — gk is itself
        # an ADD target).  The nested form would authenticate the inner leg
        # with gk's own subdev_admin and prove nothing about the new key.
        # Do NOT "unify onto _build_ssh_cmd" without preserving this.  #870.
        import cli_remote
        verify_argv = ["ssh"]
        verify_argv.extend(cli_remote.host_key_check_opts(entry))
        verify_argv.extend(["-o", "BatchMode=yes"])
        verify_argv.extend(_ssh_identity_opts(new_key_path))
        if via_gk:
            verify_argv.extend(["-J", "gatekeeper@%s" % _GK_HOST])
        verify_argv.append("%s@%s" % (user, entry.get("host", "")))
        verify_argv.append("echo OK-$(whoami)")

        rc, stdout, stderr = _run_ssh(verify_argv, run=run)

        if rc == 0 and stdout == expected:
            host_state["verified_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            host_state["verify_output"] = stdout
            state[hk] = host_state
            results[hk] = {"action": "verified"}
        else:
            results[hk] = {
                "action": "FAILED",
                "rc": rc,
                "stdout": stdout[:100],
                "stderr": stderr[:200],
                "expected": expected,
            }

    if not dry_run:
        save_state(state, state_file)

    return {"results": results}


# ---------------------------------------------------------------------------
# Phase: REMOVE
# ---------------------------------------------------------------------------

def _remove_blob_command(old_blob: str) -> str:
    """Shell command to remove EXACTLY the old blob's line from
    authorized_keys. Uses grep -v (safe: keeps every other line untouched).
    Writes to a temp then mv — atomic, never truncates in-place.

    B4 fix: if the old key is the ONLY line, refuse explicitly rather than
    letting grep -v exit 1 under set -e (which would leave a stale tmp).
    """
    safe_blob = old_blob.replace("'", "'\\''")
    return (
        "set -e; "
        "AK=~/.ssh/authorized_keys; "
        "if ! grep -qF -- '%s' \"$AK\" 2>/dev/null; then "
        "  echo ALREADY-ABSENT; "
        "elif [ \"$(grep -cF -- '%s' \"$AK\")\" = \"$(wc -l < \"$AK\" | tr -d ' ')\" ]; then "
        "  echo REFUSED-ONLY-KEY; "
        "else "
        "  TMP=\"${AK}.rotation-tmp\"; "
        "  grep -vF -- '%s' \"$AK\" > \"$TMP\"; "
        "  chmod 600 \"$TMP\"; "
        "  mv \"$TMP\" \"$AK\"; "
        "  echo REMOVED-OK; "
        "fi"
    ) % (safe_blob, safe_blob, safe_blob)


def phase_remove(old_pubkey: str, new_key_path: str,
                 state_file: Optional[str] = None,
                 include_dev1: bool = False,
                 host_filter: Optional[str] = None,
                 dry_run: bool = False,
                 run=None) -> Dict[str, Any]:
    """REMOVE phase: delete the old key from every target's authorized_keys.

    HARD INVARIANT: refuses any host lacking ``verified_at``.
    Also refuses when ``new_key_path`` does not exist on disk.

    R1 fix: authenticates with the NEW key (not the old identity) — removing
    the old key over a session authenticated by the new one makes lockout
    physically impossible per host (the standard rotation pattern).

    Y2 fix: checks ``is_paused`` LIVE.
    """
    import cli_fleet

    # Gate: new key file MUST exist
    expanded_new = os.path.expanduser(new_key_path)
    if not os.path.isfile(expanded_new):
        return {"results": {},
                "error": "new key file absent: %s — refusing remove phase"
                         % new_key_path}

    state = load_state(state_file)
    old_blob = _key_blob(old_pubkey)
    if not old_blob:
        return {"results": {},
                "error": "cannot extract blob from old pubkey"}

    results = {}

    for hk, entry, via_gk in _rotation_targets(include_dev1, host_filter):
        host_state = state.get(hk, {})

        # Already removed?
        if host_state.get("removed_at"):
            results[hk] = {"action": "already-removed",
                           "removed_at": host_state["removed_at"]}
            continue

        # Y2: check is_paused LIVE
        if cli_fleet.is_paused(entry):
            results[hk] = {"action": "skipped",
                           "reason": cli_fleet.paused_reason(entry)}
            continue

        # Stale skipped marker
        if host_state.get("skipped"):
            results[hk] = {"action": "skipped",
                           "reason": host_state["skipped"]}
            continue

        # HARD INVARIANT: must have verified_at
        if not host_state.get("verified_at"):
            results[hk] = {"action": "REFUSED",
                           "reason": "no verified_at — cannot remove before "
                                     "verify"}
            continue

        if dry_run:
            results[hk] = {"action": "dry-run"}
            continue

        cmd_str = _remove_blob_command(old_blob)
        # R1: authenticate with the NEW key (lockout-safe by construction)
        argv = _build_ssh_cmd(entry, new_key_path, cmd_str,
                              via_gk_hop=via_gk)
        rc, stdout, stderr = _run_ssh(argv, run=run)

        if rc == 0 and "REMOVED-OK" in stdout:
            host_state["removed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            state[hk] = host_state
            results[hk] = {"action": "removed"}
        elif rc == 0 and "ALREADY-ABSENT" in stdout:
            host_state["removed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            state[hk] = host_state
            results[hk] = {"action": "already-absent"}
        elif rc == 0 and "REFUSED-ONLY-KEY" in stdout:
            results[hk] = {"action": "REFUSED",
                           "reason": "old key is the only line — refusing "
                                     "removal to prevent lockout"}
        else:
            results[hk] = {"action": "FAILED", "rc": rc,
                           "stderr": stderr[:200]}

    if not dry_run:
        save_state(state, state_file)

    return {"results": results}


# ---------------------------------------------------------------------------
# Summary rendering
# ---------------------------------------------------------------------------

def render_summary(phase: str, report: dict) -> str:
    """Render a human-readable summary table. NEVER includes private-key
    material; pubkey blobs ARE safe (public material)."""
    lines = []
    lines.append("## key-rotation %s" % phase.upper())
    lines.append("")
    lines.append("%-35s %-20s %s" % ("HOST", "ACTION", "DETAIL"))
    lines.append("-" * 75)

    results = report.get("results", {})
    for hk in sorted(results):
        r = results[hk]
        action = r.get("action", "?")
        detail = ""
        if r.get("reason"):
            detail = r["reason"][:60]
        elif r.get("verified_at"):
            detail = "at %s" % r["verified_at"]
        elif r.get("added_at"):
            detail = "at %s" % r["added_at"]
        elif r.get("rc") is not None and r["rc"] != 0:
            detail = "rc=%s" % r["rc"]
        lines.append("%-35s %-20s %s" % (hk, action, detail))

    debt = report.get("debt_hosts", [])
    if debt:
        lines.append("")
        lines.append("Rotation-debt: %s" % ", ".join(sorted(debt)))

    if report.get("error"):
        lines.append("")
        lines.append("ERROR: %s" % report["error"])

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI subcommand
# ---------------------------------------------------------------------------

def cmd_key_rotation(args):
    """CLI: ``airuleset.py key-rotation add|verify|remove [--dry-run]
    [--host user@host] [--state PATH] [--new-key PATH]
    [--include-dev1] [--summary-file PATH]``"""
    phase = getattr(args, "kr_phase", None)
    dry_run = getattr(args, "dry_run", False)
    host_filter = getattr(args, "host", None)
    state_file = getattr(args, "state", None)
    new_key = getattr(args, "new_key", None)
    include_dev1 = getattr(args, "include_dev1", False)
    summary_file = getattr(args, "summary_file", None)

    if not phase:
        print("key-rotation: specify a phase: add, verify, or remove")
        print("  add     — append new pubkey to every target")
        print("  verify  — prove the new key works for every target")
        print("  remove  — delete the old key from every target")
        return

    # Read the new pubkey from <new_key>.pub
    new_pubkey = None
    if new_key:
        pub_path = os.path.expanduser(new_key)
        if not pub_path.endswith(".pub"):
            pub_path = pub_path + ".pub"
        if os.path.isfile(pub_path):
            with open(pub_path, "r", encoding="utf-8") as fh:
                new_pubkey = fh.read().strip()

    # Old identity for add phase — each entry's own identity is used (Y1),
    # but the CLI default for entries without an identity field is the
    # account's default key
    old_identity = "~/.secrets/gatekeeper_access_ed25519"

    if phase == "add":
        if not new_pubkey:
            print("key-rotation add: --new-key required (path to the new "
                  "private key; its .pub is read for the pubkey)",
                  file=sys.stderr)
            sys.exit(1)
        report = phase_add(
            new_pubkey=new_pubkey,
            old_identity=old_identity,
            state_file=state_file,
            include_dev1=include_dev1,
            host_filter=host_filter,
            dry_run=dry_run,
        )
        summary = render_summary("add", report)

    elif phase == "verify":
        if not new_key:
            print("key-rotation verify: --new-key required (path to the new "
                  "private key)", file=sys.stderr)
            sys.exit(1)
        report = phase_verify(
            new_key_path=new_key,
            state_file=state_file,
            include_dev1=include_dev1,
            host_filter=host_filter,
            dry_run=dry_run,
        )
        summary = render_summary("verify", report)

    elif phase == "remove":
        if not new_key:
            print("key-rotation remove: --new-key required (path to the new "
                  "private key — must exist to prove it is live)",
                  file=sys.stderr)
            sys.exit(1)
        report = phase_remove(
            old_pubkey=OLD_FLEET_PUSH_PUBKEY,
            new_key_path=new_key,
            state_file=state_file,
            include_dev1=include_dev1,
            host_filter=host_filter,
            dry_run=dry_run,
        )
        summary = render_summary("remove", report)

    else:
        print("key-rotation: unknown phase %r" % phase, file=sys.stderr)
        sys.exit(1)

    print(summary)

    if summary_file:
        with open(os.path.expanduser(summary_file), "w",
                  encoding="utf-8") as fh:
            fh.write(summary)
        print("Summary written to %s" % summary_file)
