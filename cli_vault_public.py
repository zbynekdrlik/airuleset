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
- the fleet's drop hosts: `cli_drop_lanes.public_drop_hosts`, built with the
  SAME builder the drop gateway uses (`_generated_drop_host`, which alone
  carries the public zone) plus the grandfathered hosts that leaf owns — a
  superset of `cli_drop_gateway.DROP_LANES`, locked by a test, without that
  module's ~45 ms import on every tool call (review finding 4).

`trust_env=False` (every `secret exec` path) ignores `$USER`/`$LOGNAME`/
`$HOME` and `getpass`: there the CALLER sets the environment, so an env var
must not be able to make a genuine value "public" (review finding 5). The
PostToolUse hook runs in Claude Code's own environment and trusts it.

What can never exempt a needle (the adversarial cases): a needle under 8
bytes (a short value inside a long host name is a coincidence, not a public
word — it keeps its old behaviour), a needle spanning a line break (identity
strings have none), a value that merely SHARES a prefix with a host name (the
test is needle-inside-identity, never the reverse), an empty or 1-byte
identity string such as `$HOME=/` (nothing 8+ bytes fits inside it), and an
identity string over MAX_IDENTITY_BYTES (an oversize env var is ignored).
Every error path fails toward redaction: the callers build the identity via
`cli_vault_keyfile.public_identity`, which falls back to an EMPTY identity
(keep every needle) when anything here raises — the hook fails OPEN, so a
crash would leak every value (review finding 1).

Each dropped needle is recorded ONCE per (name, kind) in
`~/.claude/secret-logs/public-identity.log` — the key file or store NAME and
the reason, never the value — so a weak credential is visible, not silent.
"""

import getpass
import hashlib
import os
import pwd
import re
import socket
import sys
from datetime import datetime, timezone

MIN_PUBLIC_NEEDLE = 8        # = cli_vault_keyfile.MIN_PLAIN_VALUE
MAX_IDENTITY_BYTES = 1024    # a longer "identity" (a crafted env var) is ignored
LOG_NAME = "public-identity.log"
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


def local_identity(trust_env=True):
    """This account's names and homes and this box's host names; the
    environment-derived ones only with `trust_env`."""
    out = list(_passwd_entry())
    sources = [socket.gethostname, lambda: os.uname().nodename]
    if trust_env:
        sources.append(getpass.getuser)
        out += [os.environ.get(var, "") for var in ("USER", "LOGNAME", "HOME")]
    for fn in sources:
        try:
            out.append(fn())
        except (OSError, KeyError, ImportError) as exc:
            sys.stderr.write("vault-public: identity source skipped (%s)\n"
                             % exc.__class__.__name__)
    return out


def fleet_identity(remote_hosts, nodename=None, username=None):
    """Every fleet name/host/user and every drop host the fleet can print."""
    import cli_drop_lanes

    out = []
    for e in remote_hosts or ():
        if isinstance(e, dict):
            out += [e.get(k) for k in ("name", "host", "user")]
    return out + cli_drop_lanes.public_drop_hosts(remote_hosts, nodename, username)


def default_identity(trust_env=True):
    import cli_fleet

    entry = _passwd_entry()
    user = entry[0] if entry else None
    return (local_identity(trust_env)
            + fleet_identity(cli_fleet.REMOTE_HOSTS, os.uname().nodename, user))


class PublicIdentity:
    """`contains(needle)` — is this needle a substring of a public identity?
    `windows(n)` — every n-byte run of one (the `exec --file` fragment
    filter's public grams). `strings` defaults to `default_identity()`."""

    def __init__(self, strings=None, trust_env=True):
        base = default_identity(trust_env) if strings is None else strings
        self._parts = _norm(base)
        self._blob = b"\n".join(self._parts)

    def contains(self, needle):
        n = bytes(needle).strip().lower()
        if len(n) < MIN_PUBLIC_NEEDLE or len(n) > MAX_IDENTITY_BYTES:
            return False
        if b"\n" in n or b"\r" in n:
            return False
        return n in self._blob

    def windows(self, n):
        if n < MIN_PUBLIC_NEEDLE:
            return set()
        return {part[i:i + n] for part in self._parts
                for i in range(len(part) - n + 1)}


def log_path():
    from filedrop import vault

    return vault.log_path().parent / LOG_NAME


def note_dropped(label, kind):
    """Record ONCE that the `kind` needle of `label` was dropped as public.

    Value-free by signature: only the NAME (sanitised) and the needle kind
    travel here. Never fatal — a failed note must not break the redactor."""
    raw = str(label)
    label = _LABEL_RE.sub("_", raw) or "<unnamed>"
    if len(label) > 96:     # a hash of the NAME keeps two long names apart
        digest = hashlib.sha256(raw.encode("utf-8", "surrogateescape")).hexdigest()
        label = "%s~%s" % (label[:80], digest[:12])
    kind = re.sub(r"[^a-z]", "", str(kind))[:16] or "whole"
    key = "reason=public-identity name=%s kind=%s\n" % (label, kind)
    p = log_path()
    try:
        from filedrop import vault

        if p.parent.is_symlink():
            sys.stderr.write("vault-public: %s is a symlink — not noting %s\n"
                             % (p.parent, label))
            return
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
