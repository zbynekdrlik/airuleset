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
        identity_of="",  # the default (no-identity) reach
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


def _looks_like_ssh_key(path: Path) -> bool:
    """True iff ``ssh-keygen -lf`` accepts the file as a key (private or
    public) — the reliable way to tell a key from config/known_hosts without
    reading its bytes here."""
    return _ssh_fingerprint(path) is not None


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
        ident = h.get("identity", "") or ""
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
def probe_entry(priv: Privilege, home: Path) -> dict:
    """Read-only live state of ONE declared entry. Never returns a token
    value — only present/mode/owner/fingerprint or ``len=``."""
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
    }
    if priv.kind == KIND_HOP or not priv.local_path:
        rec["detail"] = "derived reach (no local file)"
        rec["present"] = True
        return rec

    path = _expand(priv.local_path, home)
    if not path.exists():
        rec["detail"] = "absent"
        return rec

    st = path.stat()
    is_dir = path.is_dir()
    rec["present"] = True
    rec["mode"] = "0%o" % stat.S_IMODE(st.st_mode)
    rec["mode_ok"] = _mode_ok(st.st_mode, is_dir)
    rec["wrong_mode"] = not rec["mode_ok"]
    rec["owner"] = _owner(st)

    if priv.kind == KIND_SSH_KEY:
        fp = _ssh_fingerprint(path)
        rec["detail"] = "fp=" + fp if fp else "fingerprint unavailable"
    elif priv.kind in (KIND_API_TOKEN, KIND_OAUTH):
        if priv.kind == KIND_OAUTH:
            # a structured credential file (git-credentials) — present+mode is
            # the useful signal; never parse a value out of it.
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


def scan_undeclared(home: Path, declared_paths: set) -> List[dict]:
    """Find credentials on disk that the registry does NOT declare. In
    ``~/.secrets`` EVERY regular file is a credential by convention, so any
    non-declared file (and not a ``.pub`` of a declared key) is a finding. In
    ``~/.ssh`` only files ``ssh-keygen -lf`` accepts as a PRIVATE key (not a
    ``.pub``, not config/known_hosts) count."""
    findings: List[dict] = []
    declared = set(declared_paths)
    declared_pubs = {p + ".pub" for p in declared}

    secrets_dir = home / ".secrets"
    if secrets_dir.is_dir():
        for f in sorted(secrets_dir.iterdir()):
            if not f.is_file():
                continue
            rel = "~/.secrets/" + f.name
            if rel in declared or rel in declared_pubs:
                continue
            if f.name.endswith(".pub") and ("~/.secrets/" + f.name[:-4]) in declared:
                continue
            st = f.stat()
            findings.append({
                "path": rel,
                "where": ".secrets",
                "mode": "0%o" % stat.S_IMODE(st.st_mode),
                "owner": _owner(st),
            })

    ssh_dir = home / ".ssh"
    if ssh_dir.is_dir():
        for f in sorted(ssh_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name in _SSH_SAFE_NAMES or f.name.endswith(".pub"):
                continue
            rel = "~/.ssh/" + f.name
            if rel in declared:
                continue
            if not _looks_like_ssh_key(f):
                continue
            st = f.stat()
            findings.append({
                "path": rel,
                "where": ".ssh",
                "mode": "0%o" % stat.S_IMODE(st.st_mode),
                "owner": _owner(st),
            })
    return findings


def build_report(home: Optional[Path] = None) -> dict:
    """The whole F0 result: probed declared entries + undeclared extras +
    a findings summary + the exit code. `home` is test-injectable."""
    h = _home(home)
    entries = [probe_entry(p, h) for p in PRIVILEGES]
    declared_paths = {p.local_path for p in PRIVILEGES if p.local_path}
    undeclared = scan_undeclared(h, declared_paths)

    wrong_mode = [e for e in entries if e.get("wrong_mode")]
    exit_code = 1 if (undeclared or wrong_mode) else 0
    return {
        "entries": entries,
        "undeclared": undeclared,
        "findings": {
            "undeclared_count": len(undeclared),
            "wrong_mode_count": len(wrong_mode),
            "wrong_mode_names": [e["name"] for e in wrong_mode],
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
    f = report["findings"]
    lines.append("FINDINGS: undeclared=%d wrong-mode=%d%s" % (
        f["undeclared_count"], f["wrong_mode_count"],
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
