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

WHAT THIS MODULE DOES **NOT** GIVE YOU — read this before trusting the list
below (#153 finding 1). The properties are about what THIS module's own code
does. They say nothing about a DIFFERENT process, and the store is 0600 owned
by the very uid the agent's Bash runs as, so for the life of #144 a single
`cat ~/.claude/secrets/<NAME>.secret` put the value in the transcript. The read
path is now closed by an artifact — `hooks/block-vault-store-read.sh`, a
PreToolUse(Bash) hook that refuses any command reading or writing the store and
points at `secret exec`. That hook is a GUARDRAIL, not a security boundary:
the agent's uid holds `NOPASSWD: ALL` sudo on these boxes, so no store location
is beyond its reach and uid separation (a root-owned store, a service account,
a setuid helper) buys nothing here — each is one `sudo cat` away. What is
guaranteed is that the unsafe path is refused by default, so the leak cannot
happen by reflex, and that circumventing it leaves an artifact.

THE STORE DIR IS SHARED AND PRE-EXISTING. `~/.claude/secrets/` already held
per-user `*.env` files for the sub-dev boxes before this channel existed. Two
consequences a reader should know: `ensure_dir()` re-`chmod 0700`s that shared
dir on EVERY call, so it tightens whatever else lives there; and
`assert_safe_store_dir()` refuses a SYMLINK, which hard-fails the whole channel
(not just one value) on any box where that path is a link. Reads stay benign —
`list_entries` filters on `.secret`/`.meta` with a name check — but the dir is
not this channel's private property.

TTL IS SWEPT HOURLY, NOT TO THE SECOND. `purge()` runs opportunistically from
the CLI and from watchdog job 29, whose gate is an hour bucket. A value stored
with the 60s minimum `keep` therefore survives up to ~1h past its expiry. The
guarantee is "not indefinitely", never "to the second".

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

A FOURTH property, OPT-IN per name (#154): a name LOCKED to a user-written
command TEMPLATE (`<name>.template`, next to `<name>.secret`/`<name>.meta` in
this same dir) closes the one residual `_secret_redact`'s own docstring
states plainly — a child that deliberately TRANSFORMS the value (encrypts it,
reverses it, prints it a character per line) defeats any output filter,
because nothing at that layer can tell a legitimate render from an
exfiltration. `secret exec` for a templated name refuses agent-supplied
`-- CMD` argv outright and runs only `read_template()`'s result — the CHILD
is fixed by whoever wrote the template, never chosen by the agent. A name
with no template keeps behaving exactly as before this ticket.

THE LOOPHOLE, addressed rather than hidden: the agent runs as the SAME unix
user a template file would live under, so nothing here is a hard boundary
against a sufficiently deliberate agent — the same "GUARDRAIL, not boundary"
honesty this module already states for NOPASSWD sudo above applies here too.
Two things narrow it as far as this repo's own code reasonably can, and one
thing is DELIBERATELY absent:

  1. `hooks/block-vault-store-read.sh` is now ALSO wired for the Write and
     Edit TOOLS (settings/hooks.json only — no new hook file). A
     `<name>.template` file lives inside the SAME guarded `.claude/secrets/`
     dir a value file does, so an agent's reflexive `cat`/`echo >>`/Read/
     Grep/Glob/Write/Edit against it is refused exactly like a value file
     already was.
  2. This module ships NO `write_template()`/`set_template()` function —
     not even a private one, and `cmd_secret` has no CLI action for it
     either. This is a structural choice, not an oversight: a text-matching
     hook matches the RAW TEXT of a Bash command, but a computed path
     defeats it regardless of whether a dedicated helper exists —
     `python3 -c "from filedrop import vault; vault.write_template(...)"`
     is the obvious route THIS module would open if it shipped one, but
     even a plain stdlib one-liner with no vault import at all —
     `python3 -c "open(__import__('pathlib').Path.home()/'.claude'/
     'secrets'/'X.template','w').write('cmd')"` — names no literal store
     path either, so it is exactly as invisible to a text-matching hook.
     Removing this module's own write function therefore does not, by
     itself, close the whole class — it closes the one route THIS module
     could have made easier. Any write-capable function in an importable
     module is one Python one-liner away from the very agent the whole
     feature exists to constrain, and `secret exec` itself already requires
     the agent to be able to run Python. The only way this repo's own code
     closes that specific route is to not contain it, which is why this
     module goes no further than "read-only, and say so honestly" — the
     residual stdlib route is real regardless and is left to the same
     "guardrail, not boundary" limit stated below. A template is
     therefore authored by placing the file at `template_path(name)`
     directly, by a means outside anything this repo ships — a human
     editing it via a channel outside the Claude Code session entirely, or
     a trusted provisioning step run outside any agent.
  3. Neither (1) nor (2) is a boundary. NOPASSWD sudo past the hook, or
     editing `settings.json` to unregister it, remain open exactly as they
     already are for the value store — stated above, unchanged by this.
     What is delivered is the SAME thing the read-guard hook already
     delivers for values: refused by default, not by reflex — never more.
"""
import json
import os
import re
import secrets as _secrets
import shlex
import signal
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
    # fullmatch, never match: `$` also matches BEFORE a final newline, so
    # "DB_PASS\n" passed — carrying the newline into the log as a blank line,
    # into the filename, and into the env key handed to the child.
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
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


def template_path(name):
    """Where a per-name LOCKED COMMAND TEMPLATE would live (#154).

    Read-only helper — there is deliberately no `write_template()` sibling
    to `value_path`/`meta_path`. See the module docstring's "THE LOOPHOLE"
    section for why: every function this module exposes is importable and
    callable by anyone who can run `python3 -c "..."`, which the SANCTIONED
    `secret exec` flow itself requires — so a write primitive here would be
    reachable by exactly the agent the whole feature exists to constrain,
    via a route no text-matching hook can see (a computed module.function()
    call names no literal path at all). The file is authored by placing it
    at this path directly, by a means outside anything this repo ships.
    """
    return ensure_dir() / (check_name(name) + ".template")


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
    # 0700: the FILES are 0600, but on a multi-uid box a world-readable
    # directory listing still tells everyone WHICH credentials exist.
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(p.parent), 0o700)
    except OSError as e:
        sys.stderr.write("vault: could not tighten %s: %s\n" % (p.parent, e))
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
                     keep_s=DEFAULT_KEEP_S, now=None, durable_path=None):
    """Record that a value has been REQUESTED (state `pending`); return its NONCE.

    A pending request expires with its endpoint: once the one-shot server is
    gone the URL is dead, so a still-pending record is just litter.

    The nonce is what ties a running endpoint to THIS request. Without it,
    `forget` printed "forgotten" while a live endpoint could still repopulate
    the name — the O_EXCL that stops a double-store is freed by the very
    deletion that was supposed to revoke the credential.

    `durable_path` (#529, OPT-IN): when set (`secret request --persist PATH`),
    the value is ALSO written to that mode-600 durable file at the moment it is
    received (`store_value`'s paste-time persist), so the credential survives
    the vault's <=24h retention. Recorded in the metadata so the periodic
    backstop can gate on the resulting file. A name requested WITHOUT it is a
    one-shot secret and is never persisted.
    """
    check_name(name)
    ts = _now(now)
    nonce = _secrets.token_urlsafe(12)
    meta = {
        "requested": ts,
        "keep_s": int(keep_s),
        "expires_at": ts + int(endpoint_ttl_s),
        "nonce": nonce,
    }
    if durable_path:
        meta["durable_path"] = str(durable_path)
    _write_json_0600(meta_path(name), meta)
    log_event("request", name, ttl=int(endpoint_ttl_s))
    return nonce


ENDPOINT_MARKER = "vault_server.py"


def record_endpoint(name, pid, marker=ENDPOINT_MARKER):
    """Remember which process is serving `name`, so revocation can stop it."""
    meta = read_meta(name)
    if not meta:
        return False
    meta.update({"endpoint_pid": int(pid), "endpoint_marker": str(marker)})
    _write_json_0600(meta_path(name), meta)
    return True


def stop_endpoint(name):
    """SIGTERM the endpoint recorded for `name`. Returns what happened.

    Gated on the process still BEING that endpoint: a recorded pid can belong
    to something else entirely by the time revocation runs, and killing an
    unrelated process because a number was reused is a far worse bug than
    leaving a doomed endpoint to its TTL. Reports a sentinel string rather
    than raising — revocation must not fail because a process already exited.
    """
    meta = read_meta(name)
    pid = meta.get("endpoint_pid")
    if not isinstance(pid, int):
        return "no endpoint recorded"
    marker = str(meta.get("endpoint_marker") or ENDPOINT_MARKER)
    try:
        cmdline = Path("/proc", str(pid), "cmdline").read_bytes()
    except OSError:
        return "endpoint already gone"
    if marker.encode() not in cmdline:
        return "pid %d is not this endpoint (reused)" % pid
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        return "could not stop endpoint: %r" % (e,)
    return "endpoint stopped"


def store_value(name, data, keep_s=DEFAULT_KEEP_S, now=None, nonce=None):
    """Write `data` (bytes, byte-exact) as the value for `name`.

    O_EXCL, so a second submission can never silently overwrite the first —
    a caller that means to replace a value must `forget` it explicitly.

    `nonce` is the endpoint's proof that it belongs to the CURRENT request:
    the server always passes one, and a mismatch (or a metadata record that is
    gone entirely, i.e. after `forget` or `purge`) is refused. That is what
    stops a still-running endpoint from repopulating a name the user has just
    revoked. A direct caller may omit it; only a request-backed store has a
    nonce to check against.
    """
    check_name(name)
    if nonce is not None:
        current = read_meta(name).get("nonce")
        if current != nonce:
            raise SecretError(
                "%s: this endpoint no longer matches the current request "
                "(it was revoked or replaced) — refusing to store" % name)
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
    # #529 — persist AT RECEIPT to the durable ~/.secrets/<name> target the
    # request registered (`secret request --persist`), so the credential
    # survives the vault's <=24h retention. A fresh paste is authoritative
    # (overwrite), so a rotation replaces a prior generation.
    dpath = meta.get("durable_path")
    if isinstance(dpath, str) and dpath:
        _persist_at_receipt(name, dpath, bytes(data))


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

    One of only TWO value-returning functions in this package (the other is
    `read_show_file`, the `--file` source). Its callers are
    `airuleset.cmd_secret`'s `exec` action (passes it to a child process through
    the environment or stdin) and `filedrop/show_server.py`'s GET handler
    (writes it into the one-shot render response, at GET time only) — neither
    prints it to the session, and there is deliberately no formatting/rendering
    helper that could put a value on stdout by accident.
    """
    check_name(name)
    try:
        return value_path(name).read_bytes()
    except OSError as e:
        raise SecretError("%s is not stored (state=%s)" % (name, state(name))) from e


# --------------------------------------------------------------------------- #
# DURABLE PERSISTENCE (#529) — the vault is a DELIVERY channel with a <=24h
# retention (DEFAULT_KEEP_S / the CLI's SECRET_MAX_KEEP_S). A credential meant
# for REPEATED future use must ALSO be written to durable storage at receipt,
# or it is lost when the vault ages out. The fleet-standard durable home is a
# mode-600 file under `~/.secrets/<name>` (raw value + one trailing newline;
# READ it, never `source` it) — a SEPARATE tree from the vault store
# `~/.claude/secrets/`, so `hooks/block-vault-store-read.sh` (which guards the
# vault store) does not touch it, and — deliberately — the vault lifecycle
# (`forget`/`purge`) NEVER deletes it: the durable copy is the whole point, it
# outlives the delivery buffer. Opt-in per credential (`secret request/exec
# --persist PATH`); a one-shot secret (a rotation bootstrap used once) is never
# persisted. See modules/core/receive-files-via-upload-url.md.
# --------------------------------------------------------------------------- #


def validate_durable_path(path):
    """The resolved absolute Path a durable credential may live at, or raise.

    Two refusals, the same reasoning `assert_safe_store_dir` applies to the
    vault store: a SYMLINK at the final component (a redirectable target is not
    a store), and any location INSIDE a git repository (a value there is one
    `git add` from being committed — exactly what the `~/.secrets/` convention
    exists to avoid). The file itself need not exist yet; only the location is
    validated. The PATH is chosen by the operator (`--persist`), because the
    on-disk filename keeps its natural hyphens while the vault NAME is
    underscore-only — the two are never mechanically derived from one another.
    """
    p = Path(path).expanduser()
    if p.is_symlink():
        raise SecretError(
            "refusing a durable credential path at %s — it is a symlink, and a "
            "target that can be redirected is not a store" % p)
    try:
        real = p.resolve()
    except (OSError, RuntimeError) as e:
        raise SecretError("cannot resolve durable path %s: %s" % (p, e)) from e
    for parent in real.parents:
        if (parent / ".git").exists():
            raise SecretError(
                "refusing a durable credential path inside a git repository "
                "(%s) — a value there is one `git add` from being committed"
                % parent)
    return real


def _durable_bytes(value):
    """The bytes a durable file holds: the raw value plus exactly ONE trailing
    newline (the `~/.secrets/<name>` convention). A value that already ends in a
    newline is NOT doubled, so an SSH key (which ends in one) round-trips
    without a spurious blank line."""
    b = bytes(value)
    return b if b.endswith(b"\n") else b + b"\n"


def persist_durable(path, value, overwrite=False):
    """Write `value` (+ one trailing newline) to `path`, mode 0600.

    Same on-disk discipline as the vault store (#271): the location is
    validated (no symlink, no git repo), the parent dir is created 0700
    (best-effort chmod, since `~/.secrets/` is a shared pre-existing dir), and
    the write itself is O_NOFOLLOW so a symlink planted at the path after
    validation is still refused rather than followed.

    `overwrite=False` (the self-heal default, `secret exec --persist`): O_EXCL,
    so an existing durable file is LEFT UNTOUCHED and this returns False — the
    durable copy is the source of truth, exec only ever FILLS a gap.
    `overwrite=True` (the paste-time default, `secret request --persist`):
    O_TRUNC, so a fresh paste is authoritative and replaces any prior
    generation. Returns True when it wrote, False when it skipped an existing
    file. Raises SecretError on a real failure.
    """
    real = validate_durable_path(path)
    if not isinstance(value, (bytes, bytearray)):
        raise SecretError("durable value must be bytes")
    if not value:
        raise SecretError("empty durable value")
    try:
        real.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(real.parent), 0o700)
    except OSError:
        # airuleset:script-ok best-effort tightening of a shared dir; the
        # per-FILE 0600 below is the mode that actually protects the value.
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    flags |= os.O_TRUNC if overwrite else os.O_EXCL
    try:
        fd = os.open(str(real), flags, 0o600)
    except FileExistsError:
        return False                     # durable-first: leave the existing copy
    except OSError as e:
        raise SecretError(
            "could not write durable credential %s: %s (a symlink at a durable "
            "path is never followed)" % (real, e)) from e
    with os.fdopen(fd, "wb") as f:
        f.write(_durable_bytes(value))
    try:
        # O_TRUNC keeps a pre-existing file's mode; re-tighten to 0600.
        os.chmod(str(real), 0o600)
    except OSError:
        # airuleset:script-ok created 0600 via os.open; this only re-tightens.
        pass
    return True


def durable_target(name):
    """The durable path recorded for `name` (via `secret request --persist` or
    `set_durable_target`), or None. A plain metadata read — never the value."""
    v = read_meta(name).get("durable_path")
    return v if isinstance(v, str) and v else None


def set_durable_target(name, path):
    """Record `path` as `name`'s durable target in its metadata (idempotent),
    so the backstop and any later paste can find it. Used by `secret exec
    --persist` for a name not requested with --persist. Returns False (no-op)
    when the name has no metadata yet."""
    check_name(name)
    meta = read_meta(name)
    if not meta:
        return False
    if meta.get("durable_path") == str(path):
        return True
    meta["durable_path"] = str(path)
    _write_json_0600(meta_path(name), meta)
    return True


def _persist_at_receipt(name, dpath, value):
    """Persist a just-received value to its registered durable path.

    LOUD but NEVER fatal: the vault copy already succeeded, so a durable-write
    failure must not report the credential as unstored. It warns (the periodic
    backstop and `secret status` surface the missing file too) and returns."""
    try:
        persist_durable(dpath, value, overwrite=True)
    except SecretError as e:
        sys.stderr.write("vault: %s: durable persist to %s failed: %s\n"
                         % (name, dpath, e))


def durable_backstop():
    """Names whose durable copy was PROMISED but is MISSING — the #134
    artifact-gate for "delivery without persistence".

    For every `ready` name (a value is on disk) that recorded a durable target,
    return `(name, path)` when the file at that path does NOT exist. Pure READ:
    it never reads the credential value (so `read_value`'s single-caller
    invariant is untouched) and mutates nothing (dry-run safe by construction).
    A `pending` name has no value yet, so no durable file is expected; a name
    with no durable target opted out (a one-shot secret) and is skipped. It
    re-fires every sweep while the file is missing — not a one-shot latch — so
    a diagnostic dry run never silences it."""
    out = []
    for name in _entry_names():
        if state(name) != "ready":
            continue
        dpath = durable_target(name)
        if not dpath:
            continue
        try:
            missing = not Path(dpath).expanduser().exists()
        except OSError:
            missing = True
        if missing:
            out.append((name, dpath))
    return sorted(out)


def has_template(name):
    """True iff `name` is LOCKED to a user-written command template (#154).

    A name with a template ignores agent-supplied `-- CMD` argv entirely:
    `secret exec` runs only `read_template(name)`'s result instead, closing
    the deliberate-transformation residual `_secret_redact`'s own docstring
    states plainly (encrypt/reverse/print-a-character-per-line — nothing an
    OUTPUT filter can catch, because the session must be able to USE the
    value). A name with no template behaves exactly as before this ticket:
    agent argv, unchanged.
    """
    return template_path(name).exists()


def read_template(name):
    """The templated child's argv, or raise.

    NEVER returns None. Once `template_path(name)` exists the name is
    LOCKED — an unreadable or malformed template must fail LOUD, never fall
    back to agent-supplied argv, or a corrupted/emptied file (by anything
    that can still touch it) would silently re-open a name the operator
    believed was locked. Call this ONLY when `has_template(name)` is True; a
    name with no template file never reaches this function at all.

    The content is ONE shell-word-quoted command line, split with
    `shlex.split(..., comments=True)` and later run via
    `subprocess.run(argv, shell=False)` — literal-only, by construction:
    there is no shell to interpret `&&`/`|`/`;`/redirection, so those
    characters (if typed) become ordinary literal arguments to the templated
    program rather than shell operators. The credential VALUE never appears
    in this file at all — it still reaches the templated child through the
    exact same env-var/`--stdin` mechanism `exec` already used for agent
    argv, never spliced into the command text (which would put it in
    /proc/<pid>/cmdline, world-readable on these boxes — the same reasoning
    this module already applies to the endpoint token in vault_server.py).
    """
    check_name(name)
    p = template_path(name)
    try:
        raw = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # UnicodeDecodeError is a ValueError subclass, NOT an OSError —
        # read_text's own decode step can raise it on non-UTF-8 bytes, and
        # it must fail exactly as loud as an unreadable file (#154 review).
        raise SecretError(
            "%s: locked to a template that could not be read: %s"
            % (name, e)) from e
    try:
        argv = shlex.split(raw, comments=True)
    except ValueError as e:
        raise SecretError(
            "%s: template is not valid shell-word syntax: %s" % (name, e)) from e
    if not argv:
        raise SecretError(
            "%s: template is empty — refusing rather than falling back to "
            "agent-supplied argv" % name)
    return argv


def template_names():
    """Every name with a template file, independent of value state (#154).

    Deliberately NOT folded into `_entry_names()`/`list_entries()`: those
    drive `purge()`'s sweep, and a name with only a `.template` (no
    `.secret`/`.meta`) has neither a value nor an expiry to purge — adding
    it there would make `purge()` report a name as "expired" while its
    template file (never touched by `_unlink_entry`) silently survives, a
    false removal claim this module's `forget()`/`purge()` explicitly
    refuse to make elsewhere.
    """
    d = secrets_dir()
    if not d.exists():
        return []
    names = set()
    for p in d.iterdir():
        if p.suffix == ".template" and NAME_RE.fullmatch(p.stem):
            names.add(p.stem)
    return sorted(names)


def forget(name):
    """Delete the value and its metadata. True when something was removed."""
    check_name(name)
    stop_endpoint(name)          # revoke the URL too, not only the value
    removed = _unlink_entry(name)
    if value_path(name).exists():
        # NEVER report a revocation that did not happen: "forgotten" is the
        # agent's proof the credential is gone, and a false one is worse than
        # a loud failure.
        raise SecretError(
            "%s: the value could not be removed (%s still exists)"
            % (name, value_path(name)))
    if removed:
        log_event("forget", name)
    return removed


def _unlink_entry(name):
    """Remove a name's files. True when anything was actually removed.

    Reports per-path reality rather than "either one worked" — the caller has
    to be able to tell a real deletion from a partial one.
    """
    removed = False
    for p in (value_path(name), meta_path(name)):
        try:
            p.unlink()
            removed = True
        except FileNotFoundError:
            continue                     # already gone is the desired end state
        except OSError as e:
            sys.stderr.write("vault: could not remove %s: %s\n" % (p, e))
    return removed


def _entry_names():
    d = secrets_dir()
    if not d.exists():
        return []
    names = set()
    for p in d.iterdir():
        if p.suffix in (".secret", ".meta") and NAME_RE.fullmatch(p.stem):
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
            stop_endpoint(name)
            _unlink_entry(name)
            if value_path(name).exists():
                # Report only what really went. A sweep that claims a deletion
                # it could not perform is how a credential outlives its TTL
                # with nobody the wiser; one bad entry still must not stop the
                # rest of the sweep.
                sys.stderr.write(
                    "vault: %s is past its TTL but could not be removed\n" % name)
                continue
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


# --------------------------------------------------------------------------- #
# SHOW CHANNEL (#580) — the OUTPUT direction. `secret show` stands up a one-shot
# endpoint (filedrop/show_server.py) that RENDERS a value to the OWNER's browser
# ONCE, then tears down — the reverse of `secret request`, which RECEIVES one.
# The source is a vault NAME (read_value) or a `--file` durable path
# (read_show_file). The SESSION never sees the value: the server child reads it
# only at GET time; the CLI parent only ever passes the NAME or the validated
# PATH (neither is the value) to the child, and the token through the env.
# --------------------------------------------------------------------------- #

SHOW_SOURCE_KINDS = ("name", "file")


def validate_show_file(path):
    """The resolved absolute Path a `secret show --file` source may read, or raise.

    The same two refusals `validate_durable_path` applies to a durable WRITE
    target (no symlink at the final component, no git-repo ancestor), PLUS two
    the write-validator does not need because it validates a path that need not
    exist yet: the file must EXIST as a regular file, and it must be OWNER-ONLY
    (no group/other permission bits — a `~/.secrets/<name>` durable file is
    0600). A world- or group-readable "secret" file is REFUSED rather than
    served: showing an already-leaky file over a one-shot URL does not make it
    safe, and the `--file` source is meant for exactly the 0600 files the
    durable-persist convention writes.
    """
    import stat as _stat
    p = Path(path).expanduser()
    if p.is_symlink():
        raise SecretError(
            "refusing to show %s — it is a symlink, and a source that can be "
            "redirected is not a source" % p)
    try:
        real = p.resolve()
    except (OSError, RuntimeError) as e:
        raise SecretError("cannot resolve --file %s: %s" % (p, e)) from e
    for parent in real.parents:
        if (parent / ".git").exists():
            raise SecretError(
                "refusing to show a file inside a git repository (%s) — a "
                "credential there is one `git add` from being committed" % parent)
    try:
        stt = os.stat(str(real))
    except OSError as e:
        raise SecretError("--file %s is not readable: %s" % (real, e)) from e
    if not _stat.S_ISREG(stt.st_mode):
        raise SecretError("--file %s is not a regular file" % real)
    if stt.st_mode & 0o077:
        raise SecretError(
            "refusing to show %s — it is not owner-only (mode 0%o has group/"
            "other permission bits); a credential file must be 0600"
            % (real, _stat.S_IMODE(stt.st_mode)))
    # Belt-and-suspenders on the shared subdev VPS (foreign uids by design): a
    # cross-user 0600 file would pass the mode check but is not OURS to show.
    # DAC would already refuse the read (read_show_file's os.open -> EACCES),
    # but refusing HERE fails fast with an honest message instead of a late 410.
    if stt.st_uid != os.getuid():
        raise SecretError(
            "refusing to show %s — it is owned by uid %d, not you (uid %d); a "
            "credential file must be your own" % (real, stt.st_uid, os.getuid()))
    return real


def read_show_file(path):
    """The raw bytes of a `--file` source, or raise.

    Re-validates with `validate_show_file` and opens O_NOFOLLOW, so a symlink
    swapped in at the path AFTER the CLI's validate check (TOCTOU) is refused
    rather than followed — the same discipline `store_value` applies to a
    write. Called ONLY by filedrop/show_server.py at GET time (never by the
    session/CLI parent), so the value is read only when it is actually being
    shown.
    """
    real = validate_show_file(path)
    try:
        fd = os.open(str(real), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        raise SecretError(
            "refusing to read %s: %s (a symlink at a --file path is never "
            "followed)" % (real, e)) from e
    try:
        with os.fdopen(fd, "rb") as f:
            data = f.read(MAX_SECRET_BYTES + 1)
    except OSError as e:
        raise SecretError("could not read --file %s: %s" % (real, e)) from e
    if not data:
        raise SecretError("--file %s is empty" % real)
    if len(data) > MAX_SECRET_BYTES:
        raise SecretError("--file %s is over the %d-byte cap" % (real, MAX_SECRET_BYTES))
    return data


def show_log_label(kind, locator):
    """A value-free, path-free label identifying a `secret show` in the delivery
    log — the NAME for a vault source, or a `--file` basename sanitised to the
    NAME grammar for a file source.

    The basename names the credential's PURPOSE (like a vault NAME already
    does), never its value and never the directory path. It is held to
    `NAME_RE` so `log_event()` records it as-is rather than as `<invalid>`; a
    digit-leading or otherwise-odd basename is repaired to a valid label rather
    than passed through. This is a readability convenience, never a correctness
    dependency — `log_event()` degrades any bad label to `<invalid>` anyway.
    """
    if kind == "name":
        return locator
    base = os.path.basename(str(locator))
    lab = re.sub(r"[^A-Za-z0-9_]", "_", base)
    if not lab or lab[0].isdigit():
        lab = "f_" + lab
    return lab[:64]
