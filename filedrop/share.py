"""Copy a file into the file-drop dir and return its clickable LAN URL.

Run as the invoking user (NOT inside the server's systemd sandbox), so it can
read a source file from anywhere and create the drop dir under ~/.claude.
"""
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from . import (FILEDROP_DIR, MAX_SHARE_BYTES, PORT, PRUNE_AGE_S,
               PRUNE_MAX_TOTAL_BYTES, TOKEN_BYTES, host_ip)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class ShareError(Exception):
    """Raised when a file cannot be shared (missing, not a file, too big)."""


def safe_name(name):
    """Sanitize to a safe URL/filesystem basename. Never returns '' or a path.

    Strips any directory component, leading dots (no hidden / traversal names),
    maps every char outside [A-Za-z0-9._-] to '_', and caps the length."""
    base = os.path.basename(str(name)).strip()
    base = base.lstrip(".")
    base = _UNSAFE.sub("_", base)
    base = base[:128]
    return base or "file"


def share(path, base_dir=None):
    """Copy `path` into <base_dir>/<token>/<name>; return (url, dest_path).

    base_dir defaults to FILEDROP_DIR (the server's served root). A fresh random
    token dir is created per share, so the URL is unguessable. Raises ShareError
    on a missing path, a non-regular file, or a file over MAX_SHARE_BYTES."""
    root = Path(base_dir) if base_dir is not None else FILEDROP_DIR
    src = Path(path)
    try:
        src_resolved = src.resolve(strict=True)
    except (OSError, RuntimeError) as e:
        raise ShareError(f"file not found: {path}") from e
    if not src_resolved.is_file():
        raise ShareError(f"not a regular file: {path}")
    size = src_resolved.stat().st_size
    if size > MAX_SHARE_BYTES:
        raise ShareError(
            f"file too large: {size} bytes > cap {MAX_SHARE_BYTES} bytes")

    prune(base_dir=root)  # opportunistic cleanup before adding

    token = secrets.token_urlsafe(TOKEN_BYTES)
    name = safe_name(src_resolved.name)
    dest_dir = root / token
    dest_dir.mkdir(parents=True, exist_ok=False)   # fresh token dir, must not pre-exist
    dest = dest_dir / name
    shutil.copy2(str(src_resolved), str(dest))
    os.chmod(str(dest), 0o644)

    url = f"http://{host_ip()}:{PORT}/{token}/{quote(name)}"
    return url, dest


def prune(now=None, base_dir=None):
    """Delete token dirs older than PRUNE_AGE_S, then enforce the total-size cap
    (oldest first). Best-effort — unreadable entries are skipped, not fatal."""
    root = Path(base_dir) if base_dir is not None else FILEDROP_DIR
    now = time.time() if now is None else now
    if not root.exists():
        return
    entries = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        try:
            mtime = d.stat().st_mtime
            sz = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        except OSError:
            continue
        entries.append((mtime, sz, d))

    # 1. age prune
    keep = []
    for mtime, sz, d in entries:
        if now - mtime > PRUNE_AGE_S:
            shutil.rmtree(d, ignore_errors=True)
        else:
            keep.append((mtime, sz, d))

    # 2. total-size cap — evict oldest until under the cap
    keep.sort(key=lambda t: t[0])   # oldest first
    total = sum(sz for _m, sz, _d in keep)
    i = 0
    while total > PRUNE_MAX_TOTAL_BYTES and i < len(keep):
        _m, sz, d = keep[i]
        shutil.rmtree(d, ignore_errors=True)
        total -= sz
        i += 1
