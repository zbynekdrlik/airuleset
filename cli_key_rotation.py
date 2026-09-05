"""airuleset — F1 fleet push-key rotation (#870).

Three-phase state machine for rotating the fleet push ssh key:

  ADD     — append the NEW pubkey to every target's authorized_keys using the
            OLD identity (idempotent by blob; reuses ``_key_blob``/``_fingerprint``
            from ``cli_webterm_only``, ``host_key_check_opts`` + the ssh runner
            from ``cli_remote``; never rewrites the whole file — append-one-line
            shape, same as ``cli_owner_keys``).
  VERIFY  — ``ssh -i <new> -o IdentitiesOnly=yes -o BatchMode=yes user@host
            'echo OK-$(whoami)'`` and records ``verified_at`` + ``verify_output``
            ONLY on exit 0 AND output == ``OK-<user>``.
  REMOVE  — deletes exactly the old blob's line and REFUSES (per host, hard
            invariant, not a flag) any host lacking ``verified_at``; also
            refuses when the new key file is absent.

State file default ``~/.claude/key-rotation/airuleset_push_ed25519.json``
(atomic write, mode 0600 via ``_write_0600``).

``is_paused`` hosts → ``skipped: "paused #851"`` + printed ``Rotation-debt:``
line.  dev1 (``newlevel@dev1``) untouched unless ``--include-dev1`` (F3 flag).

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

# dev1 tailscale IP — excluded by default (included only with --include-dev1,
# F3 flag)
_DEV1_HOST = "100.104.8.125"

# gk hop for root@subdev (mirrors SHARED_STREAM_GUARD_HOSTS layout)
_GK_HOST = "100.90.94.41"
_SUBDEV_HOST = "100.118.174.27"


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
    """A unique key for a REMOTE_HOSTS/guard entry: ``user@host``."""
    return "%s@%s" % (entry.get("user") or entry.get("admin_user", ""),
                      entry.get("host", ""))


def _rotation_targets(include_dev1: bool = False, host_filter: Optional[str] = None):
    """Yield ``(host_key, entry, via_gk_hop)`` for every rotation target.

    Includes all REMOTE_HOSTS entries + root@subdev from
    SHARED_STREAM_GUARD_HOSTS. Excludes dev1 unless ``include_dev1``.
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

    # root@subdev via gk hop
    for guard in cli_fleet.SHARED_STREAM_GUARD_HOSTS:
        hk = "%s@%s" % (guard.get("admin_user", "root"), guard.get("host", ""))
        if hk in seen:
            continue
        seen.add(hk)
        if host_filter and hk != host_filter:
            continue
        yield hk, guard, True


# ---------------------------------------------------------------------------
# SSH runner helpers
# ---------------------------------------------------------------------------

def _ssh_identity_opts(identity_path: str) -> list:
    """SSH options for authenticating with a specific key."""
    return ["-i", os.path.expanduser(identity_path),
            "-o", "IdentitiesOnly=yes"]


def _build_ssh_cmd(entry: dict, identity: str, command: str,
                   via_gk_hop: bool = False, run=None) -> list:
    """Build an ssh argv for ``entry`` authenticating with ``identity``.

    For ``via_gk_hop=True`` (root@subdev), routes through the gk jump host.
    Reuses ``host_key_check_opts`` from ``cli_remote`` for pinned hosts.
    """
    import cli_remote

    user = entry.get("admin_user") or entry.get("user", "")
    host = entry.get("host", "")

    cmd = ["ssh"]
    cmd.extend(cli_remote.host_key_check_opts(entry))
    cmd.extend(["-o", "BatchMode=yes"])
    cmd.extend(_ssh_identity_opts(identity))

    if via_gk_hop:
        cmd.extend(["-J", "gatekeeper@%s" % _GK_HOST])

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
    trailing-newline guard.
    """
    # The whole pubkey line is single-quoted in the script, so escape
    # any embedded single quotes (should not happen with ssh keys, but safe).
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
        "grep -qF -- '%s' ~/.ssh/authorized_keys || "
        "printf '%%s\\n' '%s' >> ~/.ssh/authorized_keys; "
        "echo ADDED-OK"
    ) % (safe_blob, safe_key)


def phase_add(new_pubkey: str, old_identity: str,
              state_file: Optional[str] = None,
              include_dev1: bool = False,
              host_filter: Optional[str] = None,
              dry_run: bool = False,
              run=None) -> Dict[str, Any]:
    """ADD phase: append the new pubkey to every target's authorized_keys."""
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

        # Paused?
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

        # Build and run the append command
        cmd_str = _append_pubkey_command(new_pubkey)
        argv = _build_ssh_cmd(entry, old_identity, cmd_str,
                              via_gk_hop=via_gk, run=run)
        rc, stdout, stderr = _run_ssh(argv, run=run)

        if rc == 0 and "ADDED-OK" in stdout:
            host_state["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
            state[hk] = host_state
            results[hk] = {"action": "added"}
        elif rc == 0 and "ADDED-OK" not in stdout:
            # grep matched = already present (no ADDED-OK printed)
            host_state["added_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                   time.gmtime())
            state[hk] = host_state
            results[hk] = {"action": "added (was already present)"}
        else:
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
    """VERIFY phase: prove the new key works for every target."""
    state = load_state(state_file)
    results = {}

    for hk, entry, via_gk in _rotation_targets(include_dev1, host_filter):
        host_state = state.get(hk, {})

        # Already verified?
        if host_state.get("verified_at"):
            results[hk] = {"action": "already-verified",
                           "verified_at": host_state["verified_at"]}
            continue

        # Paused / skipped?
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

        # Build verify command
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
    Writes to a temp then mv — atomic, never truncates in-place."""
    safe_blob = old_blob.replace("'", "'\\''")
    return (
        "set -e; "
        "AK=~/.ssh/authorized_keys; "
        "if grep -qF -- '%s' \"$AK\" 2>/dev/null; then "
        "  TMP=\"${AK}.rotation-tmp\"; "
        "  grep -vF -- '%s' \"$AK\" > \"$TMP\"; "
        "  chmod 600 \"$TMP\"; "
        "  mv \"$TMP\" \"$AK\"; "
        "  echo REMOVED-OK; "
        "else "
        "  echo ALREADY-ABSENT; "
        "fi"
    ) % (safe_blob, safe_blob)


def phase_remove(old_pubkey: str, new_key_path: str,
                 old_identity: str,
                 state_file: Optional[str] = None,
                 include_dev1: bool = False,
                 host_filter: Optional[str] = None,
                 dry_run: bool = False,
                 run=None) -> Dict[str, Any]:
    """REMOVE phase: delete the old key from every target's authorized_keys.

    HARD INVARIANT: refuses any host lacking ``verified_at``.
    Also refuses when ``new_key_path`` does not exist on disk.
    """
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

        # Paused / skipped?
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
        argv = _build_ssh_cmd(entry, old_identity, cmd_str,
                              via_gk_hop=via_gk, run=run)
        rc, stdout, stderr = _run_ssh(argv, run=run)

        if rc == 0 and ("REMOVED-OK" in stdout or "ALREADY-ABSENT" in stdout):
            host_state["removed_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            state[hk] = host_state
            results[hk] = {"action": "removed" if "REMOVED-OK" in stdout
                           else "already-absent"}
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

    # Old identity for add/remove phases — today's fleet push key
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
            old_identity=old_identity,
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
