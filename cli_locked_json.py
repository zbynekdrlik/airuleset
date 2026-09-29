"""cli_locked_json — one flock'd read-modify-write of a small JSON object file.

Shared by the gh rate-guard's per-call accounting (``cli_gh_rate.record_call``,
#1087) and its GraphQL cost accounting (``cli_gh_rate_cost``, #1188): many
backgrounded shim processes append to the same day files concurrently, so every
update holds an exclusive ``flock`` across read + write and never loses a
count. stdlib only (repo policy), imports nothing from the repo (no cycle).
"""
import fcntl
import json
import os


def locked_json_update(path, mutate):
    """flock'd read-modify-write of a JSON object file: ``mutate(data)`` edits
    the dict in place and its return value is returned. A missing / corrupt /
    non-object file starts as ``{}``. Raises on I/O errors (callers are
    fail-open). Reads the WHOLE file (#1087 review: a fixed-size read would
    truncate an oversized file and reset it)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", "replace")
        try:
            data = json.loads(raw) if raw.strip() else {}
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        result = mutate(data)
        payload = json.dumps(data).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
