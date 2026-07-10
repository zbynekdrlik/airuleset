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
import re
import socket
import subprocess
from pathlib import Path

DEFAULT_PORT = 8788

# Virtual / container bridge interfaces whose RFC1918 addresses are NOT a real
# path to the user (docker0 172.17.*, cni-podman0 10.88.*, libvirt virbr0, etc.).
# Advertising / binding them is unreachable noise — drop them by interface name.
# The tailscale (100.64/10) and WireGuard interfaces are KEPT (they ARE the user's
# path); tailscale is additionally kept by CIDR regardless of its interface name.
_VIRT_IFACE = re.compile(
    r"^(docker|br-|cni|podman|veth|virbr|flannel|cali|kube|vnet|ovs|lxcbr|tap)",
    re.IGNORECASE)

# A SECOND airuleset user on the same host (montalu@dev1, marek@gatekeeper)
# collides with the first user's :8788 (Errno 98 restart-loop). When install
# detects that, it picks a free port and PERSISTS the choice here so the serve
# unit, the share CLI, and `filedrop status` always agree on the same URL.
PORT_FILE = Path.home() / ".claude" / "filedrop.port"


def persisted_port():
    """The port a previous install persisted when the default was taken by a
    FOREIGN file-drop instance on this host. None when unset/unreadable."""
    try:
        return int(PORT_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


_env_port = os.environ.get("FILEDROP_PORT")
PORT = int(_env_port) if _env_port else (persisted_port() or DEFAULT_PORT)

# Shared files live under ~/.claude/filedrop/<token>/<name> — gitignored, outside
# the repo, never committed. Overridable for tests via FILEDROP_DIR.
FILEDROP_DIR = Path(os.environ.get(
    "FILEDROP_DIR", str(Path.home() / ".claude" / "filedrop")))

TOKEN_BYTES = 16                                   # secrets.token_urlsafe(16) -> 128-bit, ~22 chars
MAX_SHARE_BYTES = int(os.environ.get("FILEDROP_MAX_BYTES", str(512 * 1024 * 1024)))   # 512 MB / file
PRUNE_AGE_S = int(os.environ.get("FILEDROP_TTL_S", str(14 * 24 * 3600)))              # drop after 14 days
PRUNE_MAX_TOTAL_BYTES = int(os.environ.get("FILEDROP_MAX_TOTAL", str(5 * 1024 * 1024 * 1024)))  # 5 GB cap


def _ordered_ips():
    """This machine's IPs in a stable order — the tailscale CLI address first
    (definitely present even if `hostname -I` omits the tailscale0 interface),
    then `hostname -I` (real interface addresses), then the resolver. Best-effort;
    failures are ignored. NOTE: only ever called UNSANDBOXED (the install CLI +
    `share`/`upload`) — the systemd server reads its bind IPs from a baked env, so
    the AF_NETLINK/subprocess calls here never run under its address-family sandbox."""
    ips = []
    try:
        r = subprocess.run(["tailscale", "ip", "-4"], capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0:
            for tok in r.stdout.split():
                if tok and tok not in ips:
                    ips.append(tok)
    except Exception:
        pass
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


def _iface_ips():
    """[(ipv4, ifname)] from `ip -o -4 addr show`, so bind_ips() can drop
    container/bridge interfaces by NAME (a 10.88.* podman bridge is RFC1918 but
    unreachable to the user). Empty on any failure — the caller then falls back to
    the coarser CIDR-only list from _ordered_ips(). UNSANDBOXED-only, like
    _ordered_ips()."""
    pairs = []
    try:
        r = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                f = line.split()
                # "3: eth0    inet 10.77.9.165/24 brd ... scope global eth0"
                if len(f) >= 4 and f[2] == "inet":
                    pairs.append((f[3].split("/")[0], f[1]))
    except Exception:
        # airuleset:script-ok best-effort IP enumeration — a missing `ip` command
        # or a parse failure MUST fall back to _ordered_ips(), never abort the CLI
        # (same silent-fallback contract as _ordered_ips right above).
        pass
    return pairs


def _is_private(ip):
    """True for an IPv4 the user can actually reach us on PRIVATELY — a tailscale
    address (100.64.0.0/10) or an RFC1918 dev-LAN address (10.0.0.0/8 → 10.77.*,
    192.168.0.0/16). Deliberately EXCLUDES, so they are never bound or advertised:
      • public / internet-facing addresses — the gatekeeper Hetzner box's
        88.99.170.148 must NEVER be a listen address; the unguessable token is the
        only auth and the upload endpoint is a WRITE surface, so a public bind would
        put an open write endpoint on the internet;
      • loopback (127/8) and IPv6;
      • 172.16.0.0/12 — on the managed boxes that range is docker/bridge-internal,
        so a URL there is unreachable noise, not a real interface for the user."""
    if ":" in ip:
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if a == 100 and 64 <= b <= 127:      # tailscale CGNAT (100.64.x – 100.127.x)
        return True
    if a == 10:                          # RFC1918 /8 — the dev LAN (10.77.*)
        return True
    if a == 192 and b == 168:            # RFC1918 /16
        return True
    return False                         # public / 172.16-31 docker / 169.254 link-local / …


def _bind_priority(ip):
    """Sort key for bind_ips(): tailscale first (stable across the user's LAN
    switches — machine-identities), then the dev LAN, then other private ranges."""
    parts = ip.split(".")
    try:
        a, b = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 9
    if a == 100 and 64 <= b <= 127:
        return 0                         # tailscale — reachable on the fallback network too
    if ip.startswith("10.77."):
        return 1                         # the dev LAN
    if a == 10:
        return 2
    if a == 192 and b == 168:
        return 3
    return 4


def bind_ips():
    """This machine's PRIVATE reachable IPv4s, tailscale-first then LAN, deduped.

    The SINGLE source of truth for which addresses BOTH file-drop servers bind AND
    which URLs the `share` / `upload` CLIs advertise — so the user always gets a
    working URL whether they are currently on tailscale OR on the LAN (the reason
    one single-IP URL kept being unreachable: it showed only the interface the box
    happened to prefer). Never includes the public / loopback / docker addresses
    (_is_private), nor the RFC1918 addresses of container/bridge interfaces
    (docker0, cni-podman0 10.88.*, …) which are private but unreachable to the user —
    those are dropped by interface NAME (_VIRT_IFACE) when `ip` is available. Falls
    back to loopback ONLY when nothing private is found, so a URL at least resolves
    locally instead of the CLI printing nothing."""
    pairs = _iface_ips()
    if pairs:
        # Interface-aware: keep tailscale (by CIDR, any iface) + private IPs on a
        # REAL interface; drop container/bridge interfaces by name.
        private = [ip for ip, ifn in pairs
                   if _is_private(ip) and (_is_tailscale(ip) or not _VIRT_IFACE.match(ifn))]
    else:
        private = []
    if not private:
        # `ip` unavailable / yielded nothing usable — coarser CIDR-only fallback.
        private = [ip for ip in _ordered_ips() if _is_private(ip)]
    seen, out = set(), []
    for ip in sorted(private, key=_bind_priority):   # sorted() is stable → ties keep input order
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out or ["127.0.0.1"]


def advertise_urls(port=None, path=""):
    """One clickable URL per PRIVATE interface (bind_ips() order — tailscale first).

    `path` is the token/name suffix (`token/name` for share, `token/` for upload);
    a leading slash is added if absent. The caller filters to the ones that answer
    (all are this machine's own addresses, so a local liveness check confirms the
    server actually bound that interface)."""
    port = PORT if port is None else port
    suffix = path if path.startswith("/") else "/" + path
    return [f"http://{ip}:{port}{suffix}" for ip in bind_ips()]


def _is_tailscale(ip):
    """True for a tailscale CGNAT address (100.64.0.0/10 → 100.64.x – 100.127.x).
    Tailscale IPs are stable across LAN switches, so they are preferred for the
    URL we hand the user — they reach the dev box even on a fallback network."""
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        a, b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return a == 100 and 64 <= b <= 127


def host_ip():
    """The IP to bind to / put in URLs.

    FILEDROP_HOST env wins. Otherwise prefer the TAILSCALE IP (stable across
    network switches — the user reaches the box on the fallback network too), then
    a 10.77.* dev-LAN address, then the first non-loopback IPv4, else loopback."""
    override = os.environ.get("FILEDROP_HOST")
    if override:
        return override
    ips = _ordered_ips()
    for ip in ips:
        if _is_tailscale(ip):
            return ip
    for ip in ips:
        if ip.startswith("10.77."):
            return ip
    for ip in ips:
        if ":" not in ip and not ip.startswith("127."):
            return ip
    return "127.0.0.1"


def filedrop_url():
    return f"http://{host_ip()}:{PORT}/"
