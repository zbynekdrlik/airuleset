"""The PUBLIC identity of this box and account — strings no credential can hide (#1170).

The #1153 redactor (hooks/vault_output_redact.py) and `secret exec --file`
use every 8+-byte credential value as a needle anywhere in a tool's output.
On dev1 four plain key files held the account word, which is also the brand
word of the fleet's public zone, so every drop/share/secret URL and every
`/home/<account>/…` path reached the session masked and the one-shot
credential URL could not be delivered (the iemmixer incident, 2026-09-28).

A value that occurs inside a string this box prints BY DESIGN cannot be
protected by redaction, so it must not be a needle. `PublicIdentity.contains`
decides that, for both callers, through `cli_vault_keyfile.needles_for`:
a needle is public when, lower-cased, it occurs inside one of

- this account's name (`getpass.getuser()`, `$USER`, `$LOGNAME`, the passwd
  entry of the effective uid) and home (`$HOME`, the passwd home),
- this box's host name (`socket.gethostname()`, `os.uname().nodename`),
- every `name`/`host`/`user` of `cli_fleet.REMOTE_HOSTS`,
- the fleet's drop hosts: built with the SAME builder the drop gateway uses
  (`cli_drop_lanes._generated_drop_host`, which alone carries the public zone)
  for every fleet account and for this box, plus the controller's local drop
  host; the hand-authored seed hosts (`cli_drop_gateway.DROP_LANES`) are read
  lazily — that import costs ~45 ms, so only for a needle the cheap set did
  not already settle and that could be part of a host name at all.

What can never exempt a needle (the adversarial cases): a needle under 8
bytes (a short value inside a long host name is a coincidence, not a public
word — it keeps its old behaviour), a needle spanning a line break (identity
strings have none), a value that merely SHARES a prefix with a host name (the
test is needle-inside-identity, never the reverse), an empty or 1-byte
identity string such as `$HOME=/` (nothing 8+ bytes fits inside it), and an
identity string over MAX_IDENTITY_BYTES (an oversize env var is ignored). A
failing lane registry keeps the needle — every error path fails toward
redaction.

Each dropped needle is recorded ONCE per (name, kind) in
`~/.claude/secret-logs/public-identity.log` — the key file or store NAME and
the reason, never the value — so a weak credential is visible, not silent.
"""

import getpass
import os
import pwd
import re
import socket
import sys
from datetime import datetime, timezone

MIN_PUBLIC_NEEDLE = 8        # = cli_vault_keyfile.MIN_PLAIN_VALUE
MAX_IDENTITY_BYTES = 1024    # a longer "identity" (a crafted env var) is ignored
LOG_NAME = "public-identity.log"
_HOSTNAME_BYTES = re.compile(rb"[a-z0-9.-]+")
_LABEL_RE = re.compile(r"[^A-Za-z0-9_.:-]")


def _norm(strings):
    """Lower-cased UTF-8 bytes of every usable identity string."""
    out = []
    for s in strings:
        if not isinstance(s, str):
            continue
        b = s.strip().encode("utf-8", "surrogateescape").lower()
        if b and len(b) <= MAX_IDENTITY_BYTES and b"\n" not in b and b"\r" not in b:
            out.append(b)
    return out


def _passwd_entry():
    """(name, home) of the effective uid, or () when it has no passwd entry
    (a container uid) — then the env and getpass sources still apply."""
    try:
        pw = pwd.getpwuid(os.geteuid())
    except KeyError:
        return ()
    return (pw.pw_name, pw.pw_dir)


def local_identity():
    """This account's names and homes and this box's host names."""
    out = []
    for fn in (getpass.getuser, socket.gethostname, lambda: os.uname().nodename):
        try:
            out.append(fn())
        except (OSError, KeyError, ImportError):
            continue
    for var in ("USER", "LOGNAME", "HOME"):
        out.append(os.environ.get(var, ""))
    return out + list(_passwd_entry())


def fleet_identity(remote_hosts, nodename=None, username=None):
    """Every fleet name/host/user and every generated drop host (both the
    single-account and the shared form, so the zone and each box stem are in)."""
    import cli_drop_lanes as lanes

    out = [lanes.CONTROLLER_LOCAL_DROP_HOST]
    pairs = [(nodename, username)] if nodename and username else []
    for e in remote_hosts or ():
        if not isinstance(e, dict):
            continue
        out += [e.get(k) for k in ("name", "host", "user")]
        user = e.get("user")
        if isinstance(user, str) and isinstance(e.get("name"), str):
            pairs.append((lanes._nodename_for_entry(e), user))
    for node, user in pairs:
        if node and isinstance(user, str) and user:
            out.append(lanes._generated_drop_host(node, user, False))
            out.append(lanes._generated_drop_host(node, user, True))
    return out


def _lane_hosts():
    """The concrete drop hosts of the lane registry (the hand-authored seeds)."""
    import cli_drop_gateway

    return [lane.host for lane in cli_drop_gateway.DROP_LANES.values()]


def default_identity():
    import cli_fleet

    entry = _passwd_entry()
    node = os.uname().nodename
    user = entry[0] if entry else None
    return local_identity() + fleet_identity(cli_fleet.REMOTE_HOSTS, node, user)


class PublicIdentity:
    """`contains(needle)` — is this needle a substring of a public identity?

    `strings` defaults to `default_identity()`; `lazy_hosts` (a no-arg
    callable) defaults to the lane registry and is called at most once."""

    def __init__(self, strings=None, lazy_hosts=None):
        base = default_identity() if strings is None else strings
        self._blob = b"\n".join(_norm(base))
        self._lazy = _lane_hosts if lazy_hosts is None else lazy_hosts
        self._lazy_blob = None

    def _extra(self):
        if self._lazy_blob is None:
            try:
                self._lazy_blob = b"\n".join(_norm(self._lazy()))
            except Exception as exc:  # noqa: BLE001 — fail toward redaction
                sys.stderr.write("vault-public: lane registry unavailable (%s) — "
                                 "seed drop hosts not exempted\n"
                                 % exc.__class__.__name__)
                self._lazy_blob = b""
        return self._lazy_blob

    def contains(self, needle):
        n = bytes(needle).strip().lower()
        if len(n) < MIN_PUBLIC_NEEDLE or len(n) > MAX_IDENTITY_BYTES:
            return False
        if b"\n" in n or b"\r" in n:
            return False
        if n in self._blob:
            return True
        return bool(_HOSTNAME_BYTES.fullmatch(n)) and n in self._extra()


def log_path():
    from filedrop import vault

    return vault.log_path().parent / LOG_NAME


def note_dropped(label, kind):
    """Record ONCE that the `kind` needle of `label` was dropped as public.

    Value-free by signature: only the NAME (sanitised) and the needle kind
    travel here. Never fatal — a failed note must not break the redactor."""
    label = _LABEL_RE.sub("_", str(label))[:96] or "<unnamed>"
    kind = re.sub(r"[^a-z]", "", str(kind))[:16] or "whole"
    key = "reason=public-identity name=%s kind=%s\n" % (label, kind)
    p = log_path()
    try:
        from filedrop import vault

        p.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(p.parent), 0o700)
        if p.exists():
            with os.fdopen(vault._open_no_follow(p, os.O_RDONLY), "r",
                           encoding="utf-8", errors="replace") as fh:
                if key in fh.read(256 * 1024):
                    return
        fd = vault._open_no_follow(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            fh.write("%s dropped-needle %s" % (ts, key))
    except Exception as exc:  # noqa: BLE001 — a note never breaks redaction
        sys.stderr.write("vault-public: could not note %s (%s)\n"
                         % (label, exc.__class__.__name__))
