"""Issue 1129 — which GitHub App minted THIS stream box's `gh` token.

Two stream Apps exist (`odoo-erp-stream-tokens`, and `odoo-erp-stream-tokens-2`
per the odoo-erp `scripts/gh-app/streams.conf` `app=` column), and a comment's
`author.login` is the minting App's bare slug. A constant identity therefore
misreads every own comment of a second-App stream as foreign
(`cli_quals._ages_from_comments` → false `no-target!`/`stale!`).

The writer is odoo-erp `scripts/gh-app/push-stream-tokens.sh`: it delivers
`~/.config/gh-app-tokens/<owner>__<name>` + its `.expires` sidecar and points
the `primary` symlink (what `gh-app-token` reads) at it. The slug record is the
sibling `.app` sidecar of the RESOLVED token file. Identity is per App, shared
by every stream that App mints for (the accepted #463 residual).

Pure leaf: stdlib only, imports nothing from the repo. A local read — no
network (the #356 rule for App-token boxes). It runs on the footer and
PreToolUse-hook paths, so it never blocks and never trusts junk: `primary` must
resolve to a regular file, the sidecar is opened non-blocking and must be a
regular file of at most `SLUG_MAX` bytes holding ONE GitHub App slug. Anything
else is None, and the caller keeps its constant.
"""
import os
import re
import stat
from pathlib import Path

# A GitHub App slug: lowercase alphanumerics and hyphens, never a leading `-`.
SLUG_RX = re.compile(r"[a-z0-9][a-z0-9-]*")
SLUG_MAX = 100


def read_app_slug(token_dir):
    """The App slug recorded next to the token `token_dir/primary` resolves
    to, or None when there is no usable record."""
    try:
        token = Path(os.path.realpath(Path(token_dir) / "primary"))
        if not token.is_file():
            return None
        sidecar = str(token) + ".app"
        if not stat.S_ISREG(os.stat(sidecar).st_mode):
            return None     # never open a FIFO / device / tty at all
        fd = os.open(sidecar, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None     # swapped between stat and open
            raw = os.read(fd, SLUG_MAX + 1)
        finally:
            os.close(fd)
        content = raw.decode("ascii").strip()
    except (OSError, ValueError):
        return None
    if len(raw) > SLUG_MAX or not SLUG_RX.fullmatch(content):
        return None
    return content
