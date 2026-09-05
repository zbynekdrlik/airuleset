"""airuleset — F0 privilege inventory for the control account (#870).

`airuleset.py privileges` (+ `--json`) is the migration-completeness gate for
moving airuleset off the shared dev1 `newlevel` account onto its own hardened
box. Root cause it solves: nothing in the repo enumerates in ONE place what the
control account is DESIGNED to hold, so a migration/rotation (F1/F2) cannot be
proven complete — and on the shared dev1 account `~/.secrets` mixes airuleset's
own credentials with ~10 foreign project credentials under the same uid.

Two halves, diffed:

  * ``PRIVILEGES`` — a DECLARATIVE registry (one ``Privilege`` dataclass entry
    per credential / reach the account is designed to hold): name, kind, local
    path, reach, one-line rotation, ``must_move``, and ``used_by`` source
    citations. This is the single source of truth of what SHOULD exist. Its ssh
    reach is drift-locked against ``cli_fleet.REMOTE_HOSTS`` by
    ``tests/test_privileges.py`` (a new REMOTE_HOSTS identity that is not in the
    registry fails the test).

  * ``build_report(home=...)`` — a LIVE, read-only probe of the account. For
    each declared entry: present? file mode (flagged if not 0600/0700), owner,
    ssh-key fingerprint (``ssh-keygen -lf``) OR token ``len=`` ONLY — NEVER any
    token material (the cloudflare-api-tokens skill rule; a value-leak lock in
    the test asserts the probe output never contains the value). It also finds
    UNDECLARED extras under ``~/.secrets``/``~/.ssh`` (a credential airuleset
    never registered = a finding).

Exit 1 when any undeclared item or wrong-mode item exists (the completeness
gate — on dev1 today the foreign creds make it exit 1, which IS the honest F0
signal: the new box must hold ONLY the declared set); exit 0 clean. An
absent-DECLARED entry is reported but is NOT a finding (before migration the new
box legitimately holds none of them yet).

Pure leaf: the only coupling is a LAZY ``import cli_fleet`` inside a function
(the same L-E convention ``cli_resource_guards``/``cli_burn`` use); stdlib only,
matching the repo's stdlib-only stance.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# --- credential kinds -------------------------------------------------------
KIND_SSH_KEY = "ssh-key"
KIND_API_TOKEN = "api-token"
KIND_OAUTH = "oauth"
KIND_HOP = "hop"
KIND_SUDO = "sudo"
KIND_PASSWORD = "password"  # airuleset:secret-ok kind label
KIND_STORE = "store"  # airuleset:secret-ok kind label

#: The account's default ssh key — the identity the no-``identity`` REMOTE_HOSTS
#: entries authorize. Used as the bucket key for those hosts so the default-key
#: entry's ``fleet_hosts`` is derived live, distinct from the webterm keys whose
#: ``identity_of`` is "" (not a REMOTE_HOSTS identity at all).
DEFAULT_SSH_KEY = "~/.ssh/id_ed25519"


@dataclass(frozen=True)
class Privilege:
    """One credential / reach the airuleset control account is DESIGNED to
    hold. Declarative — the probe (below) reports its LIVE state separately."""

    name: str
    kind: str
    #: ``~``-relative on-disk location, or "" for a derived reach (a HOP that
    #: reuses another entry's key and holds no file of its own).
    local_path: str
    #: What it can do / which hosts it reaches (plain words).
    reach: str
    #: One line: how to rotate it during the migration (F1/F2).
    rotation: str
    #: Does it move to the new dedicated airuleset box?
    must_move: bool
    #: Source ``file:line`` citations proving airuleset uses it.
    used_by: Tuple[str, ...]
    #: For an api-token stored inside an env file: the env-var key to measure
    #: (``len=`` only). "" = the whole file is the token (measure its 1st line).
    value_key: str = ""
    #: The ssh identity path this entry's reach is derived from, when it is an
    #: ssh key whose reach must stay consistent with ``cli_fleet.REMOTE_HOSTS``.
    identity_of: str = ""


# --------------------------------------------------------------------------- #
# THE DECLARED REGISTRY. Every entry is cited to a NON-TEST source reference;
# discovered by grepping the repo for ~/.secrets, identity=, the token env-var
# names, and the ssh reach in cli_fleet. Keep it honest: only credentials the
# code actually relates to are declared here — anything else in ~/.secrets on a
# shared box is FOREIGN and correctly surfaces as an undeclared finding.
# --------------------------------------------------------------------------- #
PRIVILEGES: List[Privilege] = [
    Privilege(
        name="gatekeeper_access_ed25519",
        kind=KIND_SSH_KEY,
        local_path="~/.secrets/gatekeeper_access_ed25519",
        reach="fleet operator ssh key — gatekeeper@gk + the identity-pinned "
              "subdev streams (marek/miva1/david1-4/simap1) AND root@subdev "
              "(SHARED_STREAM_GUARD_HOSTS, admin_user=root)",
        rotation="generate airuleset_push_ed25519 on the new box; distribute "
                 "via #869 managed authorized_keys push using the OLD key; then "
                 "remove the old key from every target's authorized_keys",
        must_move=True,
        used_by=("cli_fleet.py:52 (REMOTE_HOSTS identity)",
                 "cli_fleet.py:619 (SHARED_STREAM_GUARD_HOSTS root@subdev)",
                 "cli_resource_guards.py:33"),
        identity_of="~/.secrets/gatekeeper_access_ed25519",
    ),
    Privilege(
        name="default_key_id_ed25519",
        kind=KIND_SSH_KEY,
        local_path="~/.ssh/id_ed25519",
        reach="account default ssh key — the no-identity REMOTE_HOSTS "
              "(dev2, montalu1-8@subdev, forestshop-dev admin/stepan) that "
              "authorize the box's own default key",
        rotation="new box's own default key; authorize its .pub on each "
                 "default-key target (managed authorized_keys push), then "
                 "de-authorize dev1's old default key on those targets",
        must_move=True,
        used_by=("cli_fleet.py:190 (default-key REMOTE_HOSTS)",),
        identity_of=DEFAULT_SSH_KEY,  # covers the no-identity fleet bucket
    ),
    Privilege(
        name="spinbike_vps",
        kind=KIND_SSH_KEY,
        local_path="~/.ssh/spinbike_vps",
        reach="ssh key for spinbike-vps (no-tailscale owner box)",
        rotation="new keypair on the new box, authorize on spinbike-vps, "
                 "remove dev1's old key",
        must_move=True,
        used_by=("cli_fleet.py:280 (REMOTE_HOSTS identity)",),
        identity_of="~/.ssh/spinbike_vps",
    ),
    Privilege(
        name="webterm_david_ed25519",
        kind=KIND_SSH_KEY,
        local_path="~/.secrets/webterm_david_ed25519",
        reach="dedicated webterm-provisioning ssh key for david1-4@subdev "
              "(never the gatekeeper key — cli_webterm_profiles doctrine)",
        rotation="new keypair on the new box, authorize for david1-4@subdev, "
                 "remove dev1's old key",
        must_move=True,
        used_by=("cli_webterm_profiles.py:96 (WEBTERM_DAVID_IDENTITY)",),
    ),
    Privilege(
        name="webterm_marek_ed25519",
        kind=KIND_SSH_KEY,
        local_path="~/.secrets/webterm_marek_ed25519",
        reach="dedicated webterm-provisioning ssh key for marek@subdev",
        rotation="new keypair on the new box, authorize for marek@subdev, "
                 "remove dev1's old key",
        must_move=True,
        used_by=("cli_webterm_profiles.py:186 (WEBTERM_MAREK_IDENTITY)",),
    ),
    Privilege(
        name="cloudflare-newlevel-access",
        kind=KIND_API_TOKEN,
        local_path="~/.secrets/cloudflare-newlevel-access",
        reach="Cloudflare token — Access apps/policies EDIT on the "
              "newlevel.media account (webterm-access --apply + drop-gateway "
              "Access lanes)",
        rotation="mint a NEW account-owned token per the cloudflare-api-tokens "
                 "skill; `secret request --persist`; revoke the old token in "
                 "the Cloudflare dashboard",
        must_move=True,
        used_by=("cli_webterm_access.py:61 (WEBTERM_ACCESS_TOKEN_FILE)",
                 "cli_drop_gateway.py:373"),
    ),
    Privilege(
        name="cloudflare-account-tokens",
        kind=KIND_API_TOKEN,
        local_path="~/.secrets/cloudflare-account-tokens",
        reach="Cloudflare newlevel.media account READ token (Access apps/idps "
              "GET; read-only — cannot edit Access)",
        rotation="mint a NEW read-scoped account token; `secret request "
                 "--persist`; revoke the old token",
        must_move=True,
        used_by=("cli_webterm_access.py:50",),
    ),
    Privilege(
        name="webterm_credential",
        kind=KIND_API_TOKEN,
        local_path="~/.secrets/webterm_credential",
        reach="owner webterm gateway shared secret (constant-time compared by "
              "the gateway — the pre-Access password floor)",
        rotation="regenerate the shared secret on the new box; re-provision the "
                 "gateway unit; delete dev1's copy",
        must_move=True,
        used_by=("cli_webterm.py:161 (WEBTERM_CRED_PATH)",
                 "cli_webterm_gateway.py:209"),
    ),
    Privilege(
        name="discord_bot_token",
        kind=KIND_API_TOKEN,
        local_path="~/.claude/channels/discord/.env",
        reach="Discord bot token — the notify device-ping path (per-owner "
              "threads: ❓ ask / ✅ done / autopilot card)",
        rotation="mint a NEW Discord bot token for the airuleset account; write "
                 "the new box's ~/.claude/channels/discord/.env; revoke the old "
                 "bot token",
        must_move=True,
        used_by=("notify/__init__.py:39 (_ENV_REL)",
                 "notify/__init__.py:2806",
                 "airuleset.py:1662"),
        value_key="DISCORD_BOT_TOKEN",
    ),
    Privilege(
        name="gh_auth",
        kind=KIND_OAUTH,
        local_path="~/.git-credentials",
        reach="GitHub auth (issues + contents across the managed repos) — the "
              "GH_TOKEN fallback airuleset extracts when the shell has none",
        rotation="new fine-grained GitHub PAT (issues+contents on managed "
                 "repos), later a GitHub App; re-auth `gh` on the new box; "
                 "revoke the old PAT / re-auth dev1 read-only",
        must_move=True,
        used_by=("airuleset.py:2331 (~/.git-credentials fallback)",),
    ),
    Privilege(
        name="root@subdev",
        kind=KIND_HOP,
        local_path="",  # no file of its own — reuses the gatekeeper key
        reach="root on the subdev VPS (installs per-user cgroup guardrails) — "
              "reached with the gatekeeper_access key via "
              "SHARED_STREAM_GUARD_HOSTS admin_user=root",
        rotation="stays with the `gatekeeper` account, NOT airuleset (account "
                 "creation = GATEKEEPER-ACTION ticket); the airuleset box does "
                 "NOT hold root@subdev after cutover",
        must_move=False,
        used_by=("cli_fleet.py:619 (SHARED_STREAM_GUARD_HOSTS)",
                 "cli_resource_guards.py:33"),
        identity_of="~/.secrets/gatekeeper_access_ed25519",
    ),
    # --- #870 review-fix: completeness entries (Fable review + supervisor) ---
    Privilege(
        name="vault_store",
        kind=KIND_STORE,
        local_path="~/.claude/secrets",
        reach="filedrop vault store dir — every `secret request` value lives "
              "here; `--persist` writes a ~/.secrets copy",
        rotation="vault entries are transient (TTL ~24h); the dir itself moves "
                 "with the account",
        must_move=True,
        used_by=("filedrop/vault.py:185 (secrets_dir())",),
    ),
    Privilege(
        name="fleet_shared_password",
        kind=KIND_PASSWORD,
        local_path="",  # HARDCODED literal in cli_remote.py source, not a file
        reach="fleet shared ssh password (sshpass -p, hardcoded in "
              "cli_remote.py source + git history) — dev2, montalu1-8, "
              "forestshop admin/stepan (the no-identity hosts)",
        rotation="pin identities per issue 659/679; phase out shared password; "
                 "the literal is in git-tracked source AND git history",
        must_move=True,
        used_by=("cli_remote.py:306 (sshpass -p, hardcoded literal)",
                 "cli_remote.py:983"),
    ),
    Privilege(
        name="cloudflared_tunnel_creds",
        kind=KIND_STORE,
        local_path="~/.cloudflared",
        reach="cloudflared dir — per-tunnel credentials JSON files (the sole "
              "on-box secret for each managed tunnel) + cert.pem if present",
        rotation="new tunnel creds on the new box; old creds deleted",
        must_move=True,
        used_by=("cli_webterm_tunnel.py:34 (WEBTERM_CLOUDFLARED_DIR)",),
    ),
    Privilege(
        name="cloudflared_config",
        kind=KIND_OAUTH,  # a config YAML, not a token — mode 0644 is normal
        local_path="~/.cloudflared/webterm-owner.yml",
        reach="airuleset's DEDICATED webterm tunnel config (never the default "
              "config.yml — that belongs to spinbike on dev1, see "
              "cli_webterm_tunnel.py:36)",
        rotation="re-render on the new box from cli_webterm_tunnel + "
                 "cli_drop_gateway",
        must_move=True,
        used_by=("cli_webterm_tunnel.py:38 (WEBTERM_OWNER_TUNNEL_CONFIG)",
                 "cli_drop_gateway.py:497 (drop-gateway ingress config)"),
    ),
    Privilege(
        name="sudo_nopasswd",
        kind=KIND_SUDO,
        local_path="",
        reach="NOPASSWD sudo on dev1 (provision_owner_sudo) — package install, "
              "systemd system units, owner-key provisioning as root",
        rotation="the new airuleset box is sudo-LESS; sudo stays on dev1 for "
                 "the local newlevel account only",
        must_move=False,
        used_by=("airuleset.py:1157 (provision_owner_sudo)",),
    ),
    Privilege(
        name="gh_cli_token",
        kind=KIND_OAUTH,
        local_path="~/.config/gh/hosts.yml",
        reach="gh CLI auth token (issues + contents across managed repos) — "
              "the primary GitHub auth every `gh` invocation uses",
        rotation="re-auth `gh` on the new box with a fine-grained PAT; "
                 "revoke the old PAT",
        must_move=True,
        used_by=("airuleset.py:5788 (~/.config/gh/hosts.yml)",),
    ),
    Privilege(
        name="gh_app_token",
        kind=KIND_OAUTH,
        local_path="~/.config/gh-app-tokens/primary",
        reach="GitHub App token (alternative auth path for gh CLI)",
        rotation="re-provision on the new box; revoke the old token",
        must_move=True,
        used_by=("airuleset.py:2372 (gh-app-tokens/primary)",),
    ),
    Privilege(
        name="soniox_source",
        kind=KIND_API_TOKEN,
        local_path="~/devel/voiceagent/.env",
        reach="Soniox API key source (the origin file soniox provisioning "
              "reads and fans out to fleet targets)",
        rotation="new Soniox key on the new box; fan out via push",
        must_move=True,
        used_by=("cli_remote.py:99 (SONIOX_KEY_SOURCE)",),
        value_key="SONIOX_API_KEY",
    ),
    Privilege(
        name="soniox_fanout",
        kind=KIND_API_TOKEN,
        local_path="~/.soniox.env",
        reach="Soniox API key local fanout copy (the delivered copy on this "
              "box, consumed by meeting-analysis)",
        rotation="re-delivered by push from the source; no separate rotation",
        must_move=True,
        used_by=("cli_remote.py:163 (soniox.env fan-out delivery)",),
        value_key="SONIOX_API_KEY",
    ),
    Privilege(
        name="hetzner_airuleset",
        kind=KIND_API_TOKEN,
        local_path="~/.secrets/hetzner-airuleset",
        reach="Hetzner API token for the airuleset project (F1 box "
              "provisioning — server create/delete/manage)",
        rotation="after migration this token stays ONLY on the airuleset box; "
                 "revoke from dev1",
        must_move=True,
        used_by=("airuleset.py (F1 provisioning, issue 870)",),
    ),
    Privilege(
        name="tailscale_api_key",
        kind=KIND_API_TOKEN,
        local_path="",
        reach="Tailscale API key (tskey-api-...) — creates pre-auth keys, "
              "adds/removes tailnet devices; found in plaintext in project "
              "memory on dev1 (the shared-blast-radius the ticket cites)",
        rotation="new key in Tailscale admin console; `secret request --persist` "
              "on the new box; revoke old; delete the memory file on dev1",
        must_move=True,
        used_by=("wireguard project memory (plaintext in md file)",),
    ),
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _home(home: Optional[Path]) -> Path:
    return Path(home) if home is not None else Path.home()


def _expand(path_str: str, home: Path) -> Path:
    """Expand a leading ``~`` against `home` (test-injectable), leaving an
    absolute or relative path otherwise untouched."""
    if path_str.startswith("~/"):
        return home / path_str[2:]
    if path_str == "~":
        return home
    return Path(path_str)


def _mode_ok(mode: int, is_dir: bool) -> bool:
    """Task rule: flag anything not 0600/0700. Dirs must be 0700; files must be
    0600 (0400 read-only is also acceptable — a stricter mode is never a
    finding)."""
    perm = stat.S_IMODE(mode)
    if is_dir:
        return perm == 0o700
    return perm in (0o600, 0o400)


def _owner(st: os.stat_result) -> str:
    try:
        return pwd.getpwuid(st.st_uid).pw_name
    except (KeyError, OSError):
        return str(st.st_uid)


def _ssh_fingerprint(path: Path) -> Optional[str]:
    """SHA256 fingerprint via ``ssh-keygen -lf`` (works on a private OR public
    key WITHOUT a passphrase prompt). Returns the fingerprint field only (a
    safe, non-secret derived value) or None if ssh-keygen rejects the file /
    is unavailable."""
    for target in (path, Path(str(path) + ".pub")):
        if not target.exists():
            continue
        try:
            out = subprocess.run(
                ["ssh-keygen", "-lf", str(target)],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode == 0 and out.stdout.strip():
            # format: "<bits> SHA256:<hash> <comment> (<type>)"
            parts = out.stdout.strip().split()
            for p in parts:
                if p.startswith("SHA256:") or p.startswith("MD5:"):
                    return p
            return parts[1] if len(parts) > 1 else None
    return None


def _looks_like_pem_key(path: Path) -> bool:
    """True iff the file starts with a PEM private-key header (RSA, EC, PKCS#8,
    OPENSSH) or has a .pem extension — covers legacy keys ssh-keygen may reject."""
    if path.suffix == ".pem":
        return True
    try:
        head = path.read_bytes()[:80]
    except OSError:
        return False
    return (b"BEGIN RSA PRIVATE KEY" in head
            or b"BEGIN OPENSSH PRIVATE KEY" in head
            or b"BEGIN EC PRIVATE KEY" in head
            or b"BEGIN PRIVATE KEY" in head)


def _looks_like_ssh_key(path: Path) -> bool:
    """True iff ``ssh-keygen -lf`` accepts the file as a key (private or
    public), OR the file is a PEM-format private key ssh-keygen may reject."""
    return _ssh_fingerprint(path) is not None or _looks_like_pem_key(path)


def _token_len(path: Path, value_key: str) -> Optional[int]:
    """Length of the token VALUE only — never the value itself. For an env
    file, the length of the ``value_key=`` line's value; otherwise the length
    of the file's first non-empty stripped line."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if value_key:
        for line in text.splitlines():
            if line.startswith(value_key + "="):
                val = line[len(value_key) + 1:].strip().strip("\"'")
                return len(val)
        return None
    for line in text.splitlines():
        s = line.strip()
        if s:
            return len(s)
    return 0


# --------------------------------------------------------------------------- #
# ssh reach, derived from cli_fleet (never re-declared by hand)
# --------------------------------------------------------------------------- #
def _fleet_hosts_by_identity() -> dict:
    """{identity_path: [host names]} for every REMOTE_HOSTS entry (a
    no-identity entry is bucketed under "" = the default key), PLUS
    SHARED_STREAM_GUARD_HOSTS (root@subdev) under its own identity. Lazy import
    keeps this module a pure leaf."""
    import cli_fleet
    buckets: dict = {}
    for h in cli_fleet.REMOTE_HOSTS:
        # no-identity hosts authorize the account DEFAULT key, so bucket them
        # under it (the default-key entry's identity_of), never under "".
        ident = (h.get("identity", "") or "") or DEFAULT_SSH_KEY
        buckets.setdefault(ident, []).append(h["name"])
    for h in getattr(cli_fleet, "SHARED_STREAM_GUARD_HOSTS", []):
        ident = h.get("identity", "") or ""
        label = "%s@%s(root)" % (h.get("admin_user", "root"), h["name"])
        buckets.setdefault(ident, []).append(label)
    return buckets


def fleet_identity_paths() -> set:
    """The set of DISTINCT ssh identity paths REMOTE_HOSTS actually uses — the
    drift-lock target: each must appear as a declared ssh-key ``local_path``.
    A no-identity host contributes the sentinel "" (covered by the default
    key)."""
    import cli_fleet
    return {(h.get("identity", "") or "") for h in cli_fleet.REMOTE_HOSTS}


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
def _probe_sudo() -> str:
    """Read-only sudo probe: `sudo -n true` with a short timeout. Never
    prompts for a password (``-n`` = non-interactive)."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True, text=True, timeout=5,
        )
        return "sudo available" if r.returncode == 0 else "sudo unavailable"
    except (OSError, subprocess.SubprocessError):
        return "sudo probe failed"


def probe_entry(priv: Privilege, home: Path,
                fleet_buckets: Optional[dict] = None) -> dict:
    """Read-only live state of ONE declared entry. Never returns a token
    value — only present/mode/owner/fingerprint or ``len=``. For an ssh-key /
    hop entry whose reach is derived from ``cli_fleet``, ``fleet_hosts`` lists
    the actual hosts that identity reaches (live from the registry, never
    re-declared)."""
    rec: dict = {
        "name": priv.name,
        "kind": priv.kind,
        "local_path": priv.local_path,
        "must_move": priv.must_move,
        "present": False,
        "mode": None,
        "mode_ok": None,
        "owner": None,
        "detail": "",
        "wrong_mode": False,
        "fleet_hosts": (sorted(fleet_buckets.get(priv.identity_of, []))
                        if (fleet_buckets is not None and priv.identity_of)
                        else []),
    }
    if priv.kind in (KIND_HOP, KIND_PASSWORD, KIND_SUDO) or not priv.local_path:
        rec["detail"] = "derived reach (no local file)"
        rec["present"] = True
        if priv.kind == KIND_SUDO:
            rec["detail"] = _probe_sudo()
        return rec

    path = _expand(priv.local_path, home)
    try:
        # Check symlink FIRST — path.exists() follows symlinks, so a broken
        # symlink reads "absent" and silently hides the finding (#870 review-2).
        if path.is_symlink():
            rec["detail"] = "SYMLINK (-> %s)" % str(path.resolve())
            rec["present"] = True
            rec["wrong_mode"] = True  # a symlink credential is a finding
            return rec
        if not path.exists():
            rec["detail"] = "absent"
            return rec
        st = path.lstat()
    except OSError as exc:
        rec["detail"] = "error: %s" % type(exc).__name__
        rec["wrong_mode"] = True  # cannot verify → finding
        return rec

    is_dir = path.is_dir()
    rec["present"] = True
    rec["mode"] = "0%o" % stat.S_IMODE(st.st_mode)
    rec["mode_ok"] = _mode_ok(st.st_mode, is_dir)
    rec["wrong_mode"] = not rec["mode_ok"]
    rec["owner"] = _owner(st)

    if priv.kind == KIND_SSH_KEY:
        fp = _ssh_fingerprint(path)
        rec["detail"] = "fp=" + fp if fp else "fingerprint unavailable"
    elif priv.kind == KIND_STORE:
        # directory entry — mode is the important signal
        rec["detail"] = "dir present (mode=%s)" % rec["mode"]
    elif priv.kind in (KIND_API_TOKEN, KIND_OAUTH):
        if priv.kind == KIND_OAUTH:
            rec["detail"] = "present (structured credential file)"
        else:
            n = _token_len(path, priv.value_key)
            rec["detail"] = ("len=%d" % n) if n is not None else "token len unknown"
    return rec


# safe, non-credential filenames that legitimately live in ~/.ssh and are NOT
# themselves secrets to flag.
_SSH_SAFE_NAMES = {
    "config", "known_hosts", "known_hosts.old", "authorized_keys",
    "authorized_keys2", "environment", "rc", "id_ed25519.pub", "id_rsa.pub",
}


def _scan_dir_recursive(base: Path, prefix: str, declared: set,
                        declared_pubs: set, where: str,
                        key_only: bool) -> List[dict]:
    """Scan a directory (recursively) for undeclared credentials. When
    ``key_only`` is True, only files that look like ssh keys are flagged."""
    findings: List[dict] = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        findings.append({
            "path": prefix,
            "where": where,
            "mode": "unreadable",
            "owner": "?",
            "error": True,
        })
        return findings

    for f in entries:
        rel = prefix + f.name
        if f.is_symlink():
            # Symlinks to credentials are a finding (#870 review)
            try:
                st = f.lstat()
            except OSError:
                st = None
            findings.append({
                "path": rel,
                "where": where,
                "mode": "0%o" % stat.S_IMODE(st.st_mode) if st else "?",
                "owner": _owner(st) if st else "?",
                "symlink": True,
            })
            continue
        if f.is_dir():
            # Recurse into subdirectories (#870 review)
            findings.extend(_scan_dir_recursive(
                f, rel + "/", declared, declared_pubs, where, key_only))
            continue
        if not f.is_file():
            continue
        if rel in declared or rel in declared_pubs:
            continue
        if f.name.endswith(".pub"):
            base_rel = prefix + f.name[:-4]
            if base_rel in declared:
                continue
        if key_only:
            if f.name in _SSH_SAFE_NAMES or f.name.endswith(".pub"):
                continue
            if not _looks_like_ssh_key(f):
                continue
        try:
            st = f.lstat()
        except OSError:
            st = None
        findings.append({
            "path": rel,
            "where": where,
            "mode": "0%o" % stat.S_IMODE(st.st_mode) if st else "?",
            "owner": _owner(st) if st else "?",
        })
    return findings


def scan_undeclared(home: Path, declared_paths: set) -> List[dict]:
    """Find credentials on disk that the registry does NOT declare. In
    ``~/.secrets`` EVERY regular file is a credential by convention (recurses
    subdirs), so any non-declared file is a finding. In ``~/.ssh`` only files
    that look like a PRIVATE key count. Also scans ``~/.claude/secrets/``
    (the vault store dir, #870 review)."""
    declared = set(declared_paths)
    declared_pubs = {p + ".pub" for p in declared}
    findings: List[dict] = []

    # The vault store (~/.claude/secrets/) is a DECLARED dir entry — its
    # CONTENTS are legitimate transient vault values, NOT undeclared foreign
    # credentials. Scanning its contents would false-positive every normal
    # `secret request`. Only its dir MODE is probed (via probe_entry on the
    # vault_store entry). (#870 review-2 🟡3)
    for dirname, prefix, where, key_only in [
        (".secrets", "~/.secrets/", ".secrets", False),
        (".ssh", "~/.ssh/", ".ssh", True),
    ]:
        d = home / dirname
        if d.is_dir():
            findings.extend(_scan_dir_recursive(
                d, prefix, declared, declared_pubs, where, key_only))
    return findings


# well-known token REGEX patterns to scan for in project memory files — narrow,
# high-confidence patterns only; NEVER emit the matched value (#870 review).
# Each pattern uses a left-boundary (?<![A-Za-z0-9]) + minimum tail length to
# avoid false-positives on ordinary prose (e.g. "sk-" matching "task-", "ask-").
_MEMORY_TOKEN_PATTERNS = [
    (re.compile(r"(?<![A-Za-z0-9])tskey-api-[A-Za-z0-9]{10,}"), "tailscale-api-key"),
    (re.compile(r"(?<![A-Za-z0-9])tskey-auth-[A-Za-z0-9-]{10,}"), "tailscale-auth-key"),
    (re.compile(r"(?<![A-Za-z0-9])tskey-[A-Za-z0-9]{10,}"), "tailscale-key"),
    (re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{20,}"), "github-pat"),
    (re.compile(r"(?<![A-Za-z0-9])gho_[A-Za-z0-9]{20,}"), "github-oauth"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9]{20,}"), "github-fine-grained-pat"),
    (re.compile(r"(?<![A-Za-z0-9])xoxb-[0-9A-Za-z-]{20,}"), "slack-bot-token"),
    (re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"), "openai-api-key"),
    (re.compile(r"(?<![A-Za-z0-9])cfat_[A-Za-z0-9_-]{20,}"), "cloudflare-account-token"),
]


def scan_memory_credentials(home: Optional[Path] = None) -> List[dict]:
    """Read-only scan of ``~/.claude/projects/*/memory/*.md`` for well-known
    token prefixes. Reports path + pattern-name ONLY — NEVER the matched
    value. Surfaces plaintext credentials in project memory files (the
    Tailscale API key found on dev1, #870)."""
    h = _home(home)
    findings: List[dict] = []
    mem_glob = h / ".claude" / "projects"
    if not mem_glob.is_dir():
        return findings
    try:
        for proj_dir in sorted(mem_glob.iterdir()):
            mem_dir = proj_dir / "memory"
            if not mem_dir.is_dir():
                continue
            try:
                for md_file in sorted(mem_dir.iterdir()):
                    if not md_file.is_file() or md_file.suffix != ".md":
                        continue
                    try:
                        text = md_file.read_text(
                            encoding="utf-8", errors="replace")
                    except OSError:
                        continue  # airuleset:script-ok unreadable memory file — skip, never crash
                    for pat_re, pattern_name in _MEMORY_TOKEN_PATTERNS:
                        if pat_re.search(text):
                            rel = "~/.claude/projects/%s/memory/%s" % (
                                proj_dir.name, md_file.name)
                            findings.append({
                                "path": rel,
                                "pattern": pattern_name,
                            })
            except OSError:
                continue  # airuleset:script-ok unreadable memory dir — skip
    except OSError:
        return findings  # airuleset:script-ok projects dir unreadable — empty result
    return findings


def build_report(home: Optional[Path] = None) -> dict:
    """The whole F0 result: probed declared entries + undeclared extras +
    a findings summary + the exit code. `home` is test-injectable."""
    h = _home(home)
    buckets = _fleet_hosts_by_identity()
    entries = [probe_entry(p, h, buckets) for p in PRIVILEGES]
    declared_paths = {p.local_path for p in PRIVILEGES if p.local_path}
    undeclared = scan_undeclared(h, declared_paths)

    memory_creds = scan_memory_credentials(h)
    wrong_mode = [e for e in entries if e.get("wrong_mode")]
    exit_code = 1 if (undeclared or wrong_mode or memory_creds) else 0
    return {
        "entries": entries,
        "undeclared": undeclared,
        "memory_credentials": memory_creds,
        "findings": {
            "undeclared_count": len(undeclared),
            "wrong_mode_count": len(wrong_mode),
            "wrong_mode_names": [e["name"] for e in wrong_mode],
            "memory_credential_count": len(memory_creds),
        },
        "exit_code": exit_code,
    }


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def render_table(report: dict) -> str:
    """Human, value-free table. Wide columns are truncated so the table never
    depends on a value being printed."""
    lines: List[str] = []
    lines.append("airuleset privilege inventory (#870 F0) — control account")
    lines.append("")
    hdr = "%-26s %-9s %-6s %-6s %-38s %s" % (
        "NAME", "KIND", "PRESENT", "MODE", "STATE", "MOVE")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for e in report["entries"]:
        present = "yes" if e["present"] else "NO"
        mode = e["mode"] or "-"
        state = e["detail"] or ""
        if e.get("wrong_mode"):
            state = "WRONG-MODE " + state
        lines.append("%-26s %-9s %-6s %-6s %-38s %s" % (
            e["name"][:26], e["kind"][:9], present, mode, state[:38],
            "yes" if e["must_move"] else "no"))
    lines.append("")
    und = report["undeclared"]
    if und:
        lines.append("UNDECLARED credentials on disk (not in registry) — %d:" % len(und))
        for u in und:
            lines.append("  %-40s mode=%s owner=%s (%s)" % (
                u["path"], u["mode"], u["owner"], u["where"]))
    else:
        lines.append("UNDECLARED credentials on disk: none")
    lines.append("")
    mem = report.get("memory_credentials", [])
    if mem:
        lines.append("MEMORY CREDENTIALS (plaintext in project memory) — %d:" % len(mem))
        for m in mem:
            lines.append("  %-50s pattern=%s" % (m["path"], m["pattern"]))
    lines.append("")
    f = report["findings"]
    lines.append("FINDINGS: undeclared=%d wrong-mode=%d memory-creds=%d%s" % (
        f["undeclared_count"], f["wrong_mode_count"],
        f.get("memory_credential_count", 0),
        (" [" + ", ".join(f["wrong_mode_names"]) + "]") if f["wrong_mode_names"] else ""))
    lines.append("exit=%d (%s)" % (
        report["exit_code"],
        "clean" if report["exit_code"] == 0 else "migration-completeness gate: findings exist"))
    return "\n".join(lines)


def cmd_privileges(args) -> None:
    """`airuleset.py privileges [--json]` — print the inventory and exit 1 when
    any undeclared or wrong-mode credential exists (else 0)."""
    report = build_report()
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_table(report))
    sys.exit(report["exit_code"])
