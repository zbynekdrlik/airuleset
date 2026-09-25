"""Plain key files under `~/.secrets/`: loading, redaction needles, inline use (#1153).

Slice 2 of #1153 (design issuecomment-5830067896) gives the plain key-file
root the two things the credential store already had:

- `airuleset.py secret exec --file <path> (--env KEY | --stdin) -- <cmd>`
  (`cmd_exec_file`): the file's value reaches the child exactly like a stored
  value, and the child's fd 1/2 are captured and filtered before they reach
  the transcript. A path outside the root, a symlink, a non-regular file, a
  file owned by another uid, an empty or oversize file, and a PRIVATE KEY
  (its uses are `ssh -i <key>` and `ssh-keygen -y`) are refused BEFORE the
  child runs.
- the needles the PostToolUse redactor (hooks/vault_output_redact.py) uses
  for every tool's output (`plain_root_values`, `needles_for`,
  `scrub_bytes`): the values of regular files DIRECTLY under the root.

One place for both, so the loader the redactor trusts and the one `exec
--file` uses can never drift. The needle rules (moved here from the redactor;
the slice-2 changes are marked): a whole value goes through
`cli_vault._secret_redact` (raw, the stripped form, and every encoded/escaped
rendering) — slice 2: without the file's trailing newline, which would
otherwise swallow the output's own line break; each 16+-byte LINE of a value
is matched at the end or start of an output line (a Grep `path:N:` prefix, a
`cat -n`/Read gutter, an Edit `+` patch line, `Authorization: Bearer <v>`) —
slice 2: for a single-line value too, so a one-line `NAME=value` file's value
half is a needle; a PEM banner and the NAME half of `NAME=value` are never
needles; slice 2: a PEM/OpenSSH block inside a value is ALSO a whole needle,
so a printed block is replaced as one block, banners included.

`exec --file` adds a FRAGMENT filter for its own child's output only
(`scrub_fragments`): any 12+-byte run of the value's lines is replaced, so a
child that prints a slice (`cut -c1-30`, `head -c 120`, `fold`) does not
print it in the clear (review B finding 1). It is too slow for every tool
call, so the redactor does not use it.

What is deliberately NOT a needle: a `*.pub` file (public material —
`ssh-keygen -y` output is meant to be seen, and the guard allows it), a
symlink or anything that is not a regular file (never followed), a file in a
subdirectory, a file over the store's own size cap, and a whole value shorter
than 8 bytes (a plain file holding `prod` would shred every output). Residual,
stated: a short value, a line under 16 bytes, a value embedded mid-line
(outside `exec --file`), and a deliberately transformed value (hex, reversed,
re-encoded) are not recognised — the same limits `secret exec` has always had.
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
MAX_PLAIN_FILES = 128        # regular files READ per redactor call (cost bound)
MIN_LINE_BYTES = 16
MAX_LINE_LENGTHS = 64
MAX_PEM_BLOCKS = 64
FRAGMENT_BYTES = 12
PEM_BANNER_RE = re.compile(rb"^-----(BEGIN|END) ([A-Z0-9 ]{1,64})-----$")
# Split so a secret scanner reading this source does not see a key banner.
PRIVATE_KEY_RE = re.compile(rb"-----BEGIN [A-Z0-9 ]{0,32}PRIV" + rb"ATE KEY-----")
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


def _read_regular(path, cap, expect=None):
    """The bytes of a REGULAR file opened without following a symlink and
    without blocking on a FIFO, or None when it is not one, is over `cap`, or
    (with `expect`, an earlier lstat) is no longer the file that was checked."""
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > cap:
            return None
        if expect is not None and (st.st_dev, st.st_ino, st.st_uid) != (
                expect.st_dev, expect.st_ino, expect.st_uid):
            return None
        data = fh.read(cap + 1)
    return data if len(data) <= cap else None


def plain_root_values():
    """Values of the regular, non-`.pub` files DIRECTLY under the root —
    dotfiles included, at most MAX_PLAIN_FILES of them READ (review B 6: the
    cap counts files that are actually candidates, not skipped entries)."""
    root = key_root()
    if root is None:
        return []
    try:
        with os.scandir(root) as it:
            entries = sorted((e for e in it if not e.name.endswith(".pub")
                              and e.is_file(follow_symlinks=False)),
                             key=lambda e: e.name)
    except OSError:
        return []
    cap, out = _cap(), []
    for e in entries[:MAX_PLAIN_FILES]:
        value = _read_regular(e.path, cap)
        if value and len(value.strip()) >= MIN_PLAIN_VALUE:
            out.append(value)
    return out


def line_needles(value):
    """Each 16+-byte line of a value (the value half of a `NAME=value` line;
    never a PEM banner)."""
    for ln in value.splitlines():
        ln = ln.strip()
        env = ENV_LINE_RE.match(ln)
        if env:
            ln = env.group(1).strip().strip(b"\"'")
        if len(ln) >= MIN_LINE_BYTES and not PEM_BANNER_RE.match(ln):
            yield ln


def pem_blocks(value):
    """Every `-----BEGIN X-----` … `-----END X-----` block, found by ONE linear
    pass over the lines (review B 5: a lazy-dot regex from every BEGIN banner
    took 29.5 s on a crafted 256 KB file — past the hook timeout)."""
    out, start, label, pos = [], None, None, 0
    for line in value.splitlines(keepends=True):
        m = PEM_BANNER_RE.match(line.strip())
        if m and m.group(1) == b"BEGIN":
            start, label = pos + (len(line) - len(line.lstrip())), m.group(2)
        elif m and start is not None and m.group(2) == label:
            out.append(value[start:pos + len(line.rstrip())])
            start = None
            if len(out) >= MAX_PEM_BLOCKS:
                break
        pos += len(line)
    return out


def needles_for(values):
    """(whole needles longest first, line needles) for `values` (bytes)."""
    whole, lines = set(), set()
    for v in values:
        v = v.rstrip(b"\r\n") or v
        whole.add(v)
        whole.update(pem_blocks(v))
        lines.update(line_needles(v))
    return sorted(whole, key=len, reverse=True), lines


def scrub_bytes(blob, needles, redact):
    """Whole values through the full `secret exec` filter (every rendering);
    single lines of a value by an end/start-anchored set lookup per output
    line — one pass, never a replace per line needle (review C finding 2:
    19.7 s for a 64 KB value of 16-byte lines, i.e. a hook timeout)."""
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


def fragment_grams(value):
    """Every FRAGMENT_BYTES-long run of the value's secret lines."""
    grams = set()
    sources = set(line_needles(value)) | {value.strip()}
    for src in sources:
        for ln in src.splitlines():
            ln = ln.strip()
            env = ENV_LINE_RE.match(ln)
            if env:
                ln = env.group(1).strip().strip(b"\"'")
            if PEM_BANNER_RE.match(ln):
                continue
            grams.update(ln[i:i + FRAGMENT_BYTES]
                         for i in range(len(ln) - FRAGMENT_BYTES + 1))
    return grams


def scrub_fragments(blob, grams):
    """`blob` with every run covered by a known FRAGMENT_BYTES window marked."""
    if not grams:
        return blob
    n, out = FRAGMENT_BYTES, []
    for row in blob.split(b"\n"):
        spans, i = [], 0
        while i + n <= len(row):
            if row[i:i + n] in grams:
                end = i + n
                while end < len(row) and row[end - n + 1:end + 1] in grams:
                    end += 1
                spans.append((i, end))
                i = end
            else:
                i += 1
        for a, b in reversed(spans):
            row = row[:a] + MARKER + row[b:]
        out.append(row)
    return b"\n".join(out)


def resolve_key_file(raw):
    """(real path, its lstat) of a key file `exec --file` may use, or KeyFileError."""
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
    return real, st


def read_key_file(real, checked):
    cap = _cap()
    value = _read_regular(real, cap, expect=checked)
    if value is None:
        raise KeyFileError("secret exec --file: %s is not the regular file of at "
                           "most %d bytes that was checked" % (real, cap), 2)
    if not value.strip():
        raise KeyFileError("secret exec --file: %s is empty" % real, 2)
    if PRIVATE_KEY_RE.search(value):
        raise KeyFileError("secret exec --file: %s is a private key — use it as a "
                           "key argument (ssh/scp/sftp -i, rsync -e 'ssh -i'), or "
                           "`ssh-keygen -y -f` for its public half" % real, 2)
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
    if getattr(args, "persist", None) or getattr(args, "persist_map", None):
        fail("secret exec --file: --persist is for a vault NAME — the file is "
             "already the durable copy", 2)
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
        real, checked = resolve_key_file(args.file)
        value = read_key_file(real, checked)
    except KeyFileError as e:
        fail(str(e), e.rc)
    env = dict(os.environ)
    if not use_stdin:
        text = value.rstrip(b"\r\n")
        if b"\0" in text:
            fail("secret exec --file: %s holds a NUL byte — use --stdin" % real, 1)
        try:
            env[key] = text.decode("utf-8")
        except UnicodeDecodeError:
            fail("secret exec --file: %s is not UTF-8 — use --stdin" % real, 1)
    st.log_event("used", st.show_log_label("file", "file_" + real.name))
    try:
        res = subprocess.run(cmd, env=env, capture_output=True,
                             input=value if use_stdin else None)
    except OSError as e:
        fail("secret exec: cannot run %s: %s" % (cmd[0], e.strerror), 127)
    needles, grams = needles_for([value]), fragment_grams(value)
    for stream, data in ((sys.stdout, res.stdout), (sys.stderr, res.stderr)):
        if data:
            data = scrub_bytes(data, needles, _secret_redact)
            stream.buffer.write(scrub_fragments(data, grams))
            stream.flush()
    sys.exit(res.returncode)
