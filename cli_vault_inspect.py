"""`airuleset.py secret inspect <path>` — a key file's FORMAT, never its VALUE (#1153).

odoo-erp 8236: a stream lane printed a PROD API key into its transcript
"while checking the format of the key file" — the only format check that
existed read the value. This answers every format question a lane actually
has without it: size, line count, trailing newline, CRLF, whether the file is
`NAME=value` lines and which variable NAMES it defines, a 12-hex sha256 prefix
(enough to tell whether two copies match), owner and octal mode.

Only a path under the home key-file root (`~/.secrets/`) or the credential
store (`filedrop.vault.secrets_dir()`) is inspected, resolved through every
symlink and `..` first, so the command cannot be pointed at an arbitrary file
to learn anything about it; a root that is itself a SYMLINK is not a root
(a `~/.secrets -> ~` link would otherwise open the whole home). The file is
opened O_NOFOLLOW|O_NONBLOCK and re-checked on the open descriptor, so a swap
for a symlink or a FIFO after the check neither escapes nor hangs.
hooks/block-vault-store-read.sh names this command in its refusal and allows
exactly `secret inspect <one path>` (unpiped) on both roots.

What is printed is a function of the value (a hash prefix, a length) but never
the value or any slice of it. The `NAME=` shape is decided conservatively so a
bare value cannot be reported under the "names" label: a base64 value such as
`QWxh…ZQ==` or `…Qo=` would otherwise read as a variable called `QWxh…ZQ`. So a
line counts only when a real value follows the `=` (not `=`, not end of line),
and names are printed only when EVERY meaningful line has the shape. One
residual follows from the design: a bare value that itself reads `IDENT=x…`
is indistinguishable from an env line, so its part before `=` (at most 64
identifier characters) is reported as a name.
"""

import hashlib
import os
import pwd
import re
import stat
import sys
from pathlib import Path

KEY_ROOT_NAME = ".secrets"
MAX_BYTES = 16 * 1024 * 1024
NAME_LINE_RE = re.compile(rb"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]{0,63})=(?!=)\S")


class InspectError(Exception):
    def __init__(self, msg, rc):
        super().__init__(msg)
        self.rc = rc


def _roots():
    from filedrop import vault
    roots = []
    for base in (Path.home() / KEY_ROOT_NAME, Path(vault.secrets_dir())):
        if base.is_symlink():
            continue             # review B finding 8: a linked root is no root
        try:
            roots.append(base.resolve(strict=True))
        except OSError:
            continue
    return roots


def resolve_target(raw):
    """The real path to inspect, or raise InspectError (rc 2 = refused)."""
    if not raw:
        raise InspectError("secret inspect: needs a PATH", 2)
    try:
        real = Path(raw).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise InspectError("secret inspect: %s does not exist" % raw, 1) from None
    if not any(real != r and real.is_relative_to(r) for r in _roots()):
        raise InspectError(
            "secret inspect: refusing %s — only a file under ~/%s/ or the "
            "credential store is inspected" % (raw, KEY_ROOT_NAME), 2)
    st = real.stat()
    if not stat.S_ISREG(st.st_mode):
        raise InspectError("secret inspect: %s is not a regular file" % raw, 2)
    if st.st_size > MAX_BYTES:
        raise InspectError("secret inspect: %s is larger than %d bytes"
                           % (raw, MAX_BYTES), 2)
    return real, st


def describe(data):
    """The format facts about `data`, as (key, value) pairs — no value slice."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines = lines[:-1]
    meaningful = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(b"#")]
    matches = [NAME_LINE_RE.match(ln) for ln in meaningful]
    shaped = bool(meaningful) and all(matches)
    facts = [
        ("bytes", str(len(data))),
        ("lines", str(len(lines))),
        ("trailing_newline", "yes" if data.endswith(b"\n") else "no"),
        ("crlf", "yes" if b"\r\n" in data else "no"),
    ]
    if shaped:
        names = [m.group(1).decode("ascii") for m in matches]
        facts.append(("name_shape", "yes (%d of %d lines)" % (len(names), len(meaningful))))
        facts.append(("names", ", ".join(dict.fromkeys(names))))
    else:
        facts.append(("name_shape", "no"))
    facts.append(("sha256_12", hashlib.sha256(data).hexdigest()[:12]))
    return facts


def cmd_inspect(args):
    """`secret inspect PATH` — print metadata only; exit 1/2 on error."""
    extra = [a for a in (getattr(args, "cmd", None) or []) if a != "--"]
    try:
        if extra:
            raise InspectError("secret inspect: takes exactly one PATH", 2)
        real, st = resolve_target(getattr(args, "name", None))
        fd = os.open(str(real), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise InspectError("secret inspect: %s changed under the check" % real, 2)
            data = fh.read(MAX_BYTES + 1)
    except InspectError as e:
        print(str(e), file=sys.stderr)
        sys.exit(e.rc)
    except OSError as e:
        print("secret inspect: cannot read the file (%s)" % e.strerror, file=sys.stderr)
        sys.exit(1)
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    print("path: %s" % real)
    for key, val in describe(data):
        print("%s: %s" % (key, val))
    print("owner: %s" % owner)
    print("mode: %04o" % stat.S_IMODE(st.st_mode))
