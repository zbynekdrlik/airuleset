"""Storage + metadata for the CREDENTIAL channel (`airuleset.py secret`, #144).

The user has no filesystem access to any managed box, so a credential a session
needs (a DB password, a deploy key, a GitHub PAT, a connection string) can only
arrive by being typed somewhere. Today the only "somewhere" is the chat, and a
value typed there is written permanently into `~/.claude/projects/**/*.jsonl`,
survives compaction, is trivially copied into a git-tracked file by the next
step, and cannot be revoked. This module is the durable half of the replacement:
the user posts the value to a one-shot URL (filedrop/vault_server.py) and the
session only ever learns the NAME.

Deliberately a SEPARATE path from the file upload (`filedrop/upload_server.py`),
never another mode of it — that endpoint saves under `~/uploads/` with the
ambient umask and logs `SAVED <full path>`, and a credential that lands there
with ordinary permissions is not recoverable after the fact.

Named `vault` rather than `secret_store`: hooks/block-sensitive-staging.sh
refuses to `git add` any basename containing "secret"/"credential". Renaming is
that guard's prescribed remedy; its bypass marker is for content that genuinely
cannot be renamed, which a new module is not.

THE THREE NO-LEAK PROPERTIES THIS MODULE IS RESPONSIBLE FOR:

  1. Nothing here returns a value except `read_value()`, whose ONE caller is
     `airuleset.cmd_secret`'s `exec` action, which hands it to a child process
     and never prints it. There is no formatting/rendering helper that could
     put a value on stdout by accident.
  2. `log_event()` takes an event, a NAME and an optional integer ttl — there is
     no parameter through which a value, a prefix of it, or its length could
     reach the log, and the log is this channel's OWN file with that policy
     (`~/.claude/upload-logs/` has the opposite one and is not reused).
  3. Values live 0600 inside a 0700 dir under `~/.claude/`, outside every repo,
     and are deleted by `--forget` or by TTL — never written to a skill or a
     CLAUDE.md, both of which are git-committed.
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

SECRETS_DIR_ENV = "AIRULESET_SECRETS_DIR"
SECRET_LOG_DIR_ENV = "AIRULESET_SECRET_LOG_DIR"

# Also a valid POSIX environment-variable name, because `secret exec` hands the
# value to a child THROUGH the environment. Traversal, separators, NUL, control
# characters and leading dashes are excluded by construction (a keep-list on a
# tiny alphabet), never sanitised away after the fact.
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# Endpoint lifetime: minutes, not hours (the ticket's own words) — long enough
# for the user to open the URL on a phone, short enough that a printed URL is
# dead by the time the transcript is read back.
DEFAULT_ENDPOINT_TTL_S = 600
# Storage lifetime: one working session. A credential must not lie around
# forever, and a session that still needs it after this can ask again.
DEFAULT_KEEP_S = 8 * 3600
# An SSH private key is ~3 KB and a certificate chain a few times that; nothing
# legitimate on this channel is a megabyte, and the cap is what stops a request
# body from being used as a file-drop with 0600 storage.
MAX_SECRET_BYTES = 256 * 1024


class SecretError(Exception):
    """A named credential could not be stored, read or removed."""


def _override_dir(var, default):
    """An env-var relocation of a credential path, honoured ONLY under the
    system temp dir.

    `AIRULESET_SECRETS_DIR` was documented "tests only" and enforced nowhere, so
    any env var that reached the CLI — a settings.json env block, a hook, an
    exported variable — silently moved every credential somewhere else,
    including into a git worktree. Tests all run under a `tempfile` directory,
    so restricting the override to that keeps them working while making the
    relocation useless as an attack. A rejected override is REPORTED, never
    silently ignored: a store that is not where the caller thinks it is must
    not be a quiet condition.
    """
    raw = os.environ.get(var)
    if not raw:
        return default
    path = Path(raw)
    try:
        allowed = path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    except (OSError, ValueError):
        allowed = False
    if allowed:
        return path
    sys.stderr.write(
        "vault: ignoring %s=%s — a credential path may only be relocated "
        "under %s\n" % (var, raw, tempfile.gettempdir()))
    return default


def secrets_dir():
    """Where values live. `AIRULESET_SECRETS_DIR` relocates it for tests only,
    and only under the system temp dir (see _override_dir)."""
    return _override_dir(SECRETS_DIR_ENV, Path.home() / ".claude" / "secrets")


def log_path():
    """This channel's OWN log. Not `~/.claude/upload-logs/`: that one records
    `SAVED <full path> (<n> bytes)` by design, which is exactly the policy a
    credential channel must not have."""
    base = _override_dir(SECRET_LOG_DIR_ENV, Path.home() / ".claude" / "secret-logs")
    return Path(base) / "secret.log"


def check_name(name):
    """The name, or raise. Never returns a repaired/sanitised variant — a name
    that is not exactly right is a caller bug, and silently rewriting it is how
    two callers end up disagreeing about which file a value is in."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SecretError(
            "invalid name %r — must match %s (letters, digits, underscore; "
            "also a valid env-var name)" % (name, NAME_RE.pattern))
    return name


def assert_safe_store_dir(d):
    """Raise unless `d` is a place a credential may live.

    Two conditions, both of which were previously only claimed. It must not be
    a SYMLINK — `mkdir(exist_ok=True)` and `chmod` both follow one, so a
    planted link would put every value wherever it points while the 0700 proof
    tightened the target instead. And it must not sit inside a git repository:
    the whole point of storing outside every repo is that a credential cannot
    be committed, and a path that resolves under a `.git` ancestor defeats that
    however correct its permissions are.
    """
    if d.is_symlink():
        raise SecretError(
            "refusing a credential store at %s — it is a symlink, and a store "
            "that can be redirected is not a store" % d)
    try:
        real = d.resolve()
    except (OSError, RuntimeError) as e:
        raise SecretError("cannot resolve credential store %s: %s" % (d, e)) from e
    for parent in [real] + list(real.parents):
        if (parent / ".git").exists():
            raise SecretError(
                "refusing a credential store inside a git repository (%s) — "
                "a value there is one `git add` from being committed" % parent)
    return real


def ensure_dir():
    """The store dir, created 0700 and re-tightened on every call."""
    d = secrets_dir()
    assert_safe_store_dir(d)
    d.mkdir(parents=True, exist_ok=True)
    # AGAIN after mkdir: `exist_ok=True` succeeds against a symlink pointing at
    # an existing directory, so the pre-check alone can be raced.
    assert_safe_store_dir(d)
    try:
        os.chmod(str(d), 0o700)
    except OSError:
        # airuleset:script-ok best-effort tightening — a dir we cannot chmod
        # (an odd mount, a foreign owner) must still be usable for reads; the
        # per-FILE 0600 below is the mode that actually protects a value.
        pass
    return d


def value_path(name):
    return ensure_dir() / (check_name(name) + ".secret")


def meta_path(name):
    return ensure_dir() / (check_name(name) + ".meta")


def _now(now=None):
    return time.time() if now is None else float(now)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts))


def log_event(event, name, ttl=None):
    """Append ONE metadata line: `<iso-ts> <event> name=<name> [ttl=<n>s]`.

    The SIGNATURE is the guarantee, not a convention a caller has to honour:
    there is no parameter a value (or a prefix of it, or its length) could
    travel through, so no future call site can leak one into this file even by
    mistake. `event` is reduced to a bare lowercase word and an unparseable
    name is recorded as `<invalid>` rather than verbatim, so neither field can
    smuggle arbitrary text either.
    """
    ev = re.sub(r"[^a-z-]", "", str(event).lower()) or "unknown"
    try:
        nm = check_name(name)
    except SecretError:
        nm = "<invalid>"
    line = "%s %s name=%s" % (_iso(_now()), ev, nm)
    if ttl is not None:
        line += " ttl=%ds" % int(ttl)
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = _open_no_follow(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    except SecretError as e:
        # LOUD, but never fatal: this runs AFTER a value is safely on disk, so
        # raising here would report "not stored" for a credential that was.
        sys.stderr.write("vault: %s\n" % e)
        return
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    try:
        os.chmod(str(p), 0o600)
    except OSError:
        # airuleset:script-ok the mode is set at creation via os.open; this only
        # re-tightens a file that predates a mode change, and failing to do so
        # must not lose the log line that was already written.
        pass


def _open_no_follow(path, flags, mode=0o600):
    """`os.open` that refuses a symlink at the final component.

    O_NOFOLLOW turns a planted link into ELOOP instead of a write THROUGH it.
    Without it the metadata write (O_TRUNC) truncated whatever the link pointed
    at — an arbitrary same-uid file-destruction primitive.
    """
    try:
        return os.open(str(path), flags | os.O_NOFOLLOW, mode)
    except OSError as e:
        raise SecretError(
            "refusing to write %s: %s (a symlink at a credential path is "
            "never followed)" % (path, e)) from e


def _write_json_0600(path, data):
    fd = _open_no_follow(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read_meta(name):
    """The metadata dict, or {} when absent/unreadable. Never contains a value."""
    try:
        data = json.loads(meta_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def register_request(name, endpoint_ttl_s=DEFAULT_ENDPOINT_TTL_S,
                     keep_s=DEFAULT_KEEP_S, now=None):
    """Record that a value has been REQUESTED (state `pending`).

    A pending request expires with its endpoint: once the one-shot server is
    gone the URL is dead, so a still-pending record is just litter.
    """
    check_name(name)
    ts = _now(now)
    _write_json_0600(meta_path(name), {
        "requested": ts,
        "keep_s": int(keep_s),
        "expires_at": ts + int(endpoint_ttl_s),
    })
    log_event("request", name, ttl=int(endpoint_ttl_s))


def store_value(name, data, keep_s=DEFAULT_KEEP_S, now=None):
    """Write `data` (bytes, byte-exact) as the value for `name`.

    O_EXCL, so a second submission can never silently overwrite the first —
    a caller that means to replace a value must `forget` it explicitly.
    """
    check_name(name)
    if not isinstance(data, (bytes, bytearray)):
        raise SecretError("value must be bytes")
    if not data:
        raise SecretError("empty value")
    if len(data) > MAX_SECRET_BYTES:
        raise SecretError("value over the %d-byte cap" % MAX_SECRET_BYTES)
    p = value_path(name)
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600)
    except FileExistsError as e:
        # O_EXCL also refuses a PLANTED SYMLINK at this path (EEXIST), which is
        # why the value write was never redirectable the way the meta one was.
        raise SecretError("%s is already present — forget it first" % name) from e
    with os.fdopen(fd, "wb") as f:
        f.write(bytes(data))
    ts = _now(now)
    meta = read_meta(name)
    meta.update({"received": ts, "keep_s": int(keep_s),
                 "expires_at": ts + int(keep_s)})
    _write_json_0600(meta_path(name), meta)
    log_event("received", name)


def state(name):
    """`ready` (a value is stored), `pending` (requested, not yet posted) or
    `absent`. Deliberately the ONLY read a session ever needs."""
    check_name(name)
    if value_path(name).exists():
        return "ready"
    if meta_path(name).exists():
        return "pending"
    return "absent"


def read_value(name):
    """The raw value bytes.

    The ONE function in this package that returns a value. Its only caller is
    `airuleset.cmd_secret`'s `exec` action, which passes it to a child process
    through the environment or stdin and never prints it.
    """
    check_name(name)
    try:
        return value_path(name).read_bytes()
    except OSError as e:
        raise SecretError("%s is not stored (state=%s)" % (name, state(name))) from e


def forget(name):
    """Delete the value and its metadata. True when something was removed."""
    check_name(name)
    removed = False
    for p in (value_path(name), meta_path(name)):
        try:
            p.unlink()
            removed = True
        except OSError:
            # airuleset:script-ok forget must remove whatever IS there; a file
            # that is already gone is the desired end state, not an error.
            pass
    if removed:
        log_event("forget", name)
    return removed


def _entry_names():
    d = secrets_dir()
    if not d.exists():
        return []
    names = set()
    for p in d.iterdir():
        if p.suffix in (".secret", ".meta") and NAME_RE.match(p.stem):
            names.add(p.stem)
    return sorted(names)


def purge(now=None):
    """Delete every entry past its TTL. Returns the names removed, sorted.

    A value file with NO metadata is also removed: without an expiry there is
    nothing to bound how long it lies on disk, and "a credential with no
    deletion date" is precisely what this channel exists to prevent.
    """
    ts = _now(now)
    gone = []
    for name in _entry_names():
        meta = read_meta(name)
        exp = meta.get("expires_at")
        if not isinstance(exp, (int, float)) or ts >= exp:
            for p in (value_path(name), meta_path(name)):
                try:
                    p.unlink()
                except OSError:
                    # airuleset:script-ok already gone / unreadable — the entry
                    # still counts as purged, and one bad entry must not stop
                    # the sweep from removing the rest.
                    pass
            log_event("expired", name)
            gone.append(name)
    return gone


def list_entries(now=None):
    """[(name, state, requested_iso, received_iso, expires_iso)] — metadata only."""
    out = []
    for name in _entry_names():
        meta = read_meta(name)
        out.append((
            name,
            state(name),
            _iso(meta["requested"]) if isinstance(meta.get("requested"), (int, float)) else "-",
            _iso(meta["received"]) if isinstance(meta.get("received"), (int, float)) else "-",
            _iso(meta["expires_at"]) if isinstance(meta.get("expires_at"), (int, float)) else "-",
        ))
    return out
