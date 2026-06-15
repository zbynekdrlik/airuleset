"""File-drop — serve user-facing files as clickable LAN URLs (stdlib only).

The user has no direct filesystem access to the dev machines, so any file they
need to open must be handed back as a clickable LAN web URL, never a /tmp path
(see modules/core/deliver-files-as-urls.md). This package is the always-on host:

  - server.py  — read-only static HTTP server: GET /<token>/<name> only.
  - share.py   — copy a file into FILEDROP_DIR/<token>/<name>, return the URL.

Unlike the autopilot board (one central daemon on dev1), the file-drop service
runs on EVERY machine — each serves the files produced on THAT machine, bound to
THAT machine's own LAN IP. So the host IP is discovered at runtime (not hardcoded).
"""
import os
import socket
import subprocess
from pathlib import Path

PORT = int(os.environ.get("FILEDROP_PORT", "8788"))

# Shared files live under ~/.claude/filedrop/<token>/<name> — gitignored, outside
# the repo, never committed. Overridable for tests via FILEDROP_DIR.
FILEDROP_DIR = Path(os.environ.get(
    "FILEDROP_DIR", str(Path.home() / ".claude" / "filedrop")))

TOKEN_BYTES = 16                                   # secrets.token_urlsafe(16) -> 128-bit, ~22 chars
MAX_SHARE_BYTES = int(os.environ.get("FILEDROP_MAX_BYTES", str(512 * 1024 * 1024)))   # 512 MB / file
PRUNE_AGE_S = int(os.environ.get("FILEDROP_TTL_S", str(14 * 24 * 3600)))              # drop after 14 days
PRUNE_MAX_TOTAL_BYTES = int(os.environ.get("FILEDROP_MAX_TOTAL", str(5 * 1024 * 1024 * 1024)))  # 5 GB cap


def _ordered_ips():
    """This machine's IPs in a stable order — `hostname -I` first (real interface
    addresses), then the resolver. Best-effort; failures are ignored."""
    ips = []
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True,
                             text=True, timeout=5)
        if out.returncode == 0:
            for tok in out.stdout.split():
                if tok and tok not in ips:
                    ips.append(tok)
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def host_ip():
    """The LAN IP to bind to / put in URLs.

    FILEDROP_HOST env wins. Otherwise prefer a 10.77.* address (the dev LAN per
    machine-identities), else the first non-loopback IPv4, else loopback."""
    override = os.environ.get("FILEDROP_HOST")
    if override:
        return override
    ips = _ordered_ips()
    for ip in ips:
        if ip.startswith("10.77."):
            return ip
    for ip in ips:
        if ":" not in ip and not ip.startswith("127."):
            return ip
    return "127.0.0.1"


def filedrop_url():
    return f"http://{host_ip()}:{PORT}/"
