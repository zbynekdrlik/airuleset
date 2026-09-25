"""Plain key files under `~/.secrets/`: loading, redaction needles, inline use (#1153).

Slice 2 of #1153 (design issuecomment-5830067896) gives the plain key-file
root the two things the credential store already had:

- `airuleset.py secret exec --file <path> (--env KEY | --stdin) -- <cmd>`
  (`cmd_exec_file`): the file's value reaches the child exactly like a stored
  value, and the child's fd 1/2 are captured and filtered before they reach
  the transcript. A path outside the root, a symlink, a non-regular file, a
  file owned by another uid, an empty or oversize file are refused BEFORE the
  child runs.
- the needles the PostToolUse redactor (hooks/vault_output_redact.py) uses
  for every tool's output (`plain_root_values`, `needles_for`,
  `scrub_bytes`): the values of regular files DIRECTLY under the root.

One place for both, so the loader the redactor trusts and the one `exec
--file` uses can never drift. The needle rules (moved here from the redactor
unchanged): a whole value goes through `cli_vault._secret_redact` (raw, the
stripped form, and every encoded/escaped rendering); each 16+-byte line of a
multi-line value is matched at the end or start of an output line (a Grep
`path:N:` prefix, a `cat -n`/Read gutter, an Edit `+` patch line); a PEM
banner and the NAME half of `NAME=value` are never needles. New in slice 2: a
PEM/OpenSSH block inside a file is ALSO a whole needle, so a printed block is
replaced as one block, banners included.

What is deliberately NOT a needle: a `*.pub` file (public material —
`ssh-keygen -y` output is meant to be seen, and the guard allows it), a
symlink or anything that is not a regular file (never followed), a file in a
subdirectory, a file over the store's own size cap, and a whole value shorter
than 8 bytes (a plain file holding `prod` would shred every output). Residual,
stated: a short value, a line under 16 bytes, a value embedded mid-line of a
multi-line file, and a deliberately transformed value are not recognised —
the same limits `secret exec` has always had.
"""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

KEY_ROOT_NAME = ".secrets"
MARKER = b"<<REDACTED>>"
MIN_PLAIN_VALUE = 8          # a plain file's WHOLE value (stripped), see above
MAX_PLAIN_FILES = 128        # entries considered per redactor call (cost bound)
MIN_LINE_BYTES = 16
MAX_LINE_LENGTHS = 64
PEM_BANNER_RE = re.compile(rb"^-----(?:BEGIN|END) [A-Z0-9 ]+-----$")
PEM_BLOCK_RE = re.compile(
    rb"-----BEGIN ([A-Z0-9 ]{1,64})-----\r?\n.{0,262144}?\r?\n-----END \1-----", re.S)
ENV_LINE_RE = re.compile(rb"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*=(.*)$")


class KeyFileError(Exception):
    def __init__(self, msg, rc):
        super().__init__(msg)
        self.rc = rc


def _cap():
    from filedrop import vault
    return vault.MAX_SECRET_BYTES


def key_root():
    """The resolved `~/.secrets` dir, or None. A root that is itself a SYMLINK
    is no root (a `~/.secrets -> ~` link would open the whole home)."""
    base = Path.home() / KEY_ROOT_NAME
    if base.is_symlink() or not base.is_dir():
        return None
    try:
        return base.resolve(strict=True)
    except OSError:
        return None


def _read_regular(path, cap):
    """The bytes of a REGULAR file opened without following a symlink and
    without blocking on a FIFO, or None when it is not one or is over `cap`."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            return None
        data = fh.read(cap + 1)
    return data if len(data) <= cap else None


def plain_root_values():
    """Values of the regular, non-`.pub` files DIRECTLY under the root."""
    root = key_root()
    if root is None:
        return []
    try:
        entries = sorted(root.iterdir())[:MAX_PLAIN_FILES]
    except OSError:
        return []
    cap, out = _cap(), []
    for p in entries:
        if p.name.endswith(".pub"):
            continue
        try:
            if not stat.S_ISREG(p.lstat().st_mode):
                continue                     # symlink, dir, FIFO, socket
        except OSError:
            continue
        value = _read_regular(p, cap)
        if value and len(value.strip()) >= MIN_PLAIN_VALUE:
            out.append(value)
    return out


def line_needles(value):
    """Each 16+-byte line of a multi-line value (the value half of a
    `NAME=value` line; never a PEM banner)."""
    for ln in value.splitlines():
        ln = ln.strip()
        env = ENV_LINE_RE.match(ln)
        if env:
            ln = env.group(1).strip().strip(b"\"'")
        if len(ln) >= MIN_LINE_BYTES and not PEM_BANNER_RE.match(ln):
            yield ln


def needles_for(values):
    """(whole needles longest first, line needles) for `values` (bytes)."""
    whole, lines = set(), set()
    for v in values:
        # A file's trailing newline is the file's, not the value's: kept in the
        # needle it would swallow the output's own line break (`cat k; echo`).
        v = v.rstrip(b"\r\n") or v
        whole.add(v)
        whole.update(m.group(0) for m in PEM_BLOCK_RE.finditer(v))
        if b"\n" in v.strip():
            lines.update(line_needles(v))
    return sorted(whole, key=len, reverse=True), lines


def scrub_bytes(blob, needles, redact):
    """Whole values through the full `secret exec` filter (every rendering);
    single lines of a multi-line value by an end/start-anchored set lookup per
    output line — one pass, never a replace per line needle (review C finding
    2: 19.7 s for a 64 KB value of 16-byte lines, i.e. a hook timeout)."""
    values, lines = needles
    for v in values:
        blob = redact(blob, v, MARKER)
    if not lines:
        return blob
    lengths = sorted({len(n) for n in lines}, reverse=True)[:MAX_LINE_LENGTHS]
    out = []
    for row in blob.split(b"\n"):
        body = row.rstrip(b"\r \t")
        for n in lengths:
            if len(body) < n:
                continue
            if body[-n:] in lines:
                row = body[:-n] + MARKER + row[len(body):]
                break
            if body[:n] in lines:
                row = MARKER + row[n:]
                break
        out.append(row)
    return b"\n".join(out)


def resolve_key_file(raw):
    """The real path of a key file `exec --file` may use, or KeyFileError."""
    root = key_root()
    if root is None:
        raise KeyFileError("secret exec --file: there is no ~/%s/ root (or it is "
                           "a symlink)" % KEY_ROOT_NAME, 2)
    p = Path(raw).expanduser()
    if p.is_symlink():
        raise KeyFileError("secret exec --file: refusing %s — a symlink is never "
                           "followed" % raw, 2)
    try:
        real = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise KeyFileError("secret exec --file: %s does not exist" % raw, 1) from None
    if real == root or not real.is_relative_to(root):
        raise KeyFileError("secret exec --file: refusing %s — only a file under "
                           "~/%s/ is used" % (raw, KEY_ROOT_NAME), 2)
    st = real.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise KeyFileError("secret exec --file: %s is not a regular file" % raw, 2)
    if st.st_uid != os.getuid():
        raise KeyFileError("secret exec --file: %s is owned by uid %d, not you"
                           % (raw, st.st_uid), 2)
    return real


def read_key_file(real):
    cap = _cap()
    value = _read_regular(real, cap)
    if value is None:
        raise KeyFileError("secret exec --file: %s is not a regular file of at "
                           "most %d bytes" % (real, cap), 2)
    if not value.strip():
        raise KeyFileError("secret exec --file: %s is empty" % real, 2)
    return value


def cmd_exec_file(args):
    """`secret exec --file PATH (--env KEY | --stdin) -- CMD` — see the module doc."""
    from cli_vault import _secret_redact
    from filedrop import vault as st

    def fail(msg, rc):
        print(msg, file=sys.stderr)
        sys.exit(rc)

    if getattr(args, "name", None):
        fail("secret exec: give a NAME or --file, not both", 2)
    cmd = list(getattr(args, "cmd", None) or [])
    if not cmd:
        fail("secret exec: needs a command after `--`", 2)
    use_stdin = bool(getattr(args, "stdin", False))
    key = getattr(args, "env", None)
    if not use_stdin:
        if key is None:
            fail("secret exec --file: give --env KEY (the variable the child "
                 "reads) or --stdin — a file has no NAME to default to", 2)
        try:
            st.check_name(key)
        except st.SecretError as e:
            fail("secret exec: bad --env key: %s" % e, 2)
    try:
        real = resolve_key_file(args.file)
        value = read_key_file(real)
    except KeyFileError as e:
        fail(str(e), e.rc)
    env = dict(os.environ)
    if not use_stdin:
        try:
            env[key] = value.rstrip(b"\r\n").decode("utf-8")
        except UnicodeDecodeError:
            fail("secret exec --file: %s is not UTF-8 — use --stdin" % real, 1)
    st.log_event("used", st.show_log_label("file", str(real)))
    try:
        res = subprocess.run(cmd, env=env, capture_output=True,
                             input=value if use_stdin else None)
    except OSError as e:
        fail("secret exec: cannot run %s: %s" % (cmd[0], e.strerror), 127)
    needles = needles_for([value])
    for stream, data in ((sys.stdout, res.stdout), (sys.stderr, res.stderr)):
        if data:
            stream.buffer.write(scrub_bytes(data, needles, _secret_redact))
            stream.flush()
    sys.exit(res.returncode)
