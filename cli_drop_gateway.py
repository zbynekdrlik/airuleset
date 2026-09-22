"""airuleset — public-TLS drop lane for secret/upload (#664).

The `secret request` / `secret show` / `upload` endpoints bind ONLY private
interfaces (tailscale/LAN/loopback) — correct, because a token-only credential
or write endpoint on a public IP is unrecoverable. But that leaves a
NO-TAILSCALE context with no reachable URL, so a session used to improvise
`ssh -L` gymnastics. Two such contexts: the no-tailscale BOX (spinbike-vps) and
the no-tailscale CLIENT (David's laptop → david1/2@subdev).

The seam: a cloudflared tunnel fronts a public hostname → a LOOPBACK origin, and
loopback (127.*) is ALREADY an accepted bind (`is_private`→True — strictly more
private than tailscale, it just isn't reachable BY the user). So the public-TLS
channel = bind loopback on a FIXED port that a managed tunnel fronts, and print
`https://<host>/<token>/`. The endpoint stays exactly as private as loopback
(never directly reachable from the internet); cloudflared is the sole bridge and
terminates TLS (public plaintext is impossible by construction); the one-shot
≥128-bit token stays (in the path); `no-store` stays (server-side, untouched).

This leaf is SELF-CONTAINED (#433 script-topology rule): NO module-level
`import airuleset`. It reuses the EXISTING cloudflared-tunnel framework this repo
already ships (`cli_webterm_tunnel.render_cloudflared_tunnel_config` shape) and
the Cloudflare Access client library (`cli_webterm_access.apply_profile`), both
imported LAZILY inside the reconcile command so there is no import-order coupling.

Two tunnel topologies exist (#931):
- LOCAL (spinbike): the tunnel runs ON the box, ingress → 127.0.0.1:<drop-port>.
- CONTROLLER (subdev/david): the per-box tunnel was retired in #870; the
  controller's multi-ingress tunnel fronts the drop hostname, ingress →
  <tailscale-ip>:<drop-port> (the drop server binds all private interfaces).
  The controller's ``install`` renders the ingress declaratively.
"""
import os
import re
import sys
from pathlib import Path

# #1115: the drop-lane registry is GENERATED from the fleet registry. cli_fleet
# is a pure DATA leaf with ZERO top-level imports (it never imports this module),
# so importing it here creates no cycle — the #433 rule explicitly permits a new
# leaf to import cli_fleet directly.
import cli_fleet

# #1115 (#993 area-review split): the GENERATED-registry engine + cache + harvest
# helpers live in the cli_drop_lanes leaf (imports ONLY cli_fleet — no cross-import
# cycle; the wrappers below inject the lane-shape primitives; names re-exported).
import cli_drop_lanes
from cli_drop_lanes import (  # noqa: F401 — re-exported for cli_drop_gateway.X callers
    DROP_LANES_CACHE, _is_tailscale_host, _nodename_for_entry, drop_lanes_cache_key,
    filedrop_port_probe_snippet, parse_filedrop_port, persist_measured_filedrop_ports,
    read_drop_lanes_cache, write_drop_lanes_cache)

# #433 self-contained leaf: same directory, identical value as airuleset.REPO_DIR.
REPO_DIR = Path(__file__).resolve().parent

# Per-account loopback/drop port range for the public drop lane (#889). Each
# account on a shared box gets its own port, so concurrent ephemeral servers
# (upload, secret show, secret request) never collide. Range 8870-8899 sits
# above show's 8850-8869 and below no other airuleset range. Distinct from
# filedrop 8788, upload 8799-8819, secret 8830-8849, show 8850-8869.
# Grandfathered: spinbike's 8828 predates the per-account range. #1115: MAX
# raised 8889 -> 8909 to fit the 14 generated fleet lanes (cli_drop_lanes.
# build_drop_lanes) above the 7 hand-authored 8870-8876 ports, with headroom.
DROP_PORT_BASE = 8870
DROP_PORT_MAX = 8909

# Flat single-level drop hostnames. Single-level is LOAD-BEARING: Cloudflare
# Universal SSL for newlevel.media is `*.newlevel.media` (ONE level), so a
# 2-level `drop.david.newlevel.media` would have NO valid edge cert and would
# silently break the mandatory TLS. Every existing host (zbynek/david/spinbike
# .newlevel.media) is single-level, confirming the cert shape.
# Naming: `drop-<box>.newlevel.media` on single-account boxes,
# `drop-<box>-<account>.newlevel.media` on shared boxes (#889).
DROP_HOST_SPINBIKE = "drop-spinbike.newlevel.media"
DROP_HOST_DAVID = "drop-david.newlevel.media"  # grandfathered for david1
DROP_HOST_GK = "drop-gk.newlevel.media"  # gatekeeper box (#1111)

# The go-live marker a `drop-gateway --apply` writes once a box's drop lane is
# LIVE (ingress reconciled + tunnel restarted). The CLI's public channel is
# available IFF this file exists — so `--public` and the no-tailscale
# auto-fallback never advertise a URL that would 404 before go-live.
#
# PER-UNIX-ACCOUNT (#664 review B-M2): the loopback origin 127.0.0.1:<drop-port> is
# box-wide, but this marker lives in the invoking account's own home. On subdev
# the tunnel + config + `--user` unit belong to david1, so `drop-gateway --apply`
# runs there; a SIBLING account (david2, …) that should also use the lane needs
# its OWN marker seeded (the runbook documents this) — otherwise that account's
# `secret request`/`upload` silently stays on the unreachable private URLs.
DROP_MARKER = Path.home() / ".cloudflared" / "airuleset-drop.conf"

_CFDIR = Path.home() / ".cloudflared"


class DropLane:
    """One account's public-TLS drop lane (#889): the flat public hostname, a
    per-account loopback port, the EXISTING cloudflared tunnel it rides (uuid +
    config path + restart unit), and whether Cloudflare Access fronts it.

    `access=True` = double protection Access+token (external-dev accounts:
    david, dominika); `access=False` = token-only TLS (owner/trusted accounts).
    EXCEPTION (#1111): the owner's own gk secret-pickup box deliberately runs
    `access=True` (owner identity as the sole Access include) — defense-in-depth
    for a credential-delivery/intake box is strictly stricter than token-only,
    so this is a considered upgrade, not a violation of the owner→`access=False`
    default above.

    `gateway_account` (#838): the unix account that OWNS this lane's tunnel
    config + restart unit on the box. On a SHARED box (subdev) multiple accounts
    ride the SAME tunnel: the `gateway_account` owns the config + unit, siblings
    share it. `DROP_LANES` is `(nodename, username)`-keyed (#889), so each
    account resolves to its OWN lane with its OWN port — eliminating the shared-
    port contention that blocked david3's `secret show` when david1's upload held
    the port. A sibling account has nothing to re-assert at install time, so
    `reconcile_drop_ingress_on_install` diverts it to a benign no-op.
    `None` = the invoking account always owns the tunnel (single-account boxes)
    → #826's loud failure stays intact on the tunnel-owning account."""

    def __init__(self, host, port, tunnel_uuid, tunnel_config, tunnel_service,
                 tunnel_system_unit, access, gateway_account=None,
                 topology="local", origin_host=None, filedrop_port=None):
        self.host = host
        self.port = port
        self.tunnel_uuid = tunnel_uuid
        self.tunnel_config = tunnel_config
        self.tunnel_service = tunnel_service
        # True → a SYSTEM unit (restart via `sudo -n systemctl restart`);
        # False → a `--user` unit (`systemctl --user restart`).
        self.tunnel_system_unit = tunnel_system_unit
        self.access = access
        # #838: the tunnel-owning unix account, or None (no sibling concept).
        self.gateway_account = gateway_account
        # #931: "local" = the tunnel runs on this box (ingress → 127.0.0.1:port);
        # "controller" = the tunnel runs on the controller (ingress → origin_host:port,
        # where origin_host is this box's tailscale IP). No local tunnel config/service
        # exists; the controller's install manages the ingress.
        self.topology = topology
        # The tailscale IP the controller's tunnel proxies to (controller topology only).
        self.origin_host = origin_host
        # #1114: the account's PERSISTENT filedrop service port (FILEDROP_DEFAULT_PORT
        # + uid%1000, #493 — but the value can DRIFT off the uid-default if that port
        # was taken, so it is the port actually persisted on the box). The `/s/` share
        # ingress rule targets origin_host:filedrop_port (controller topology) or
        # 127.0.0.1:filedrop_port (local). It CANNOT be derived on the controller
        # (no per-account unix uid there), so controller lanes carry a MEASURED value;
        # a local lane can leave it None and derive at `--apply` time (uid available).
        # None on a controller lane -> NO `/s/` rule (share falls back to the private
        # URLs). measured 22.9.2026; #1115 makes install read it from the box.
        self.filedrop_port = filedrop_port


# Per-account drop lanes (#889), keyed by (nodename, username) — each account
# gets its OWN hostname + loopback port, eliminating the shared-port contention
# that blocked sibling accounts when one held the port (the david1/david3 live
# incident). On single-account boxes the tuple key is the ONLY representation
# (no bare-nodename fallback — the registry is an EXPLICIT allowlist).
#
# TUNNEL TOPOLOGY on subdev: david1-4 ride the CONTROLLER tunnel (f85ea304,
# #931 — the old per-box david tunnel 1564fe31 was RETIRED in #870's
# controller consolidation), marek rides the marek tunnel (1e9555d1),
# dominika rides the dominika tunnel (7792f710). montalu1-8 and miva1 ride
# the controller tunnel (provisioned at go-live). The controller tunnel is
# managed on the controller box (airuleset@100.101.214.103).
#
# The controller tunnel UUID is duplicated here (vs cli_webterm.py's
# CONTROLLER_TUNNEL_UUID) to keep this leaf self-contained (#433 rule).
_CONTROLLER_TUNNEL_UUID = "f85ea304-920b-4ba4-96bc-a68001ce6fb4"
_SUBDEV_TAILSCALE = "100.118.174.27"
# gk box (odoo-gatekeeper, Hetzner cx23) tailscale IP — the controller tunnel's
# origin for the gk drop lane (#1111).
_GK_TAILSCALE = "100.90.94.41"



# #1115: the HAND-AUTHORED seed lanes. These carry the irregular, grandfathered
# facts a pure derivation cannot reproduce (`drop-david`/`drop-spinbike`/`drop-gk`
# hostnames, the marek/dominika/spinbike local tunnels, the measured filedrop
# ports), so `build_drop_lanes()` preserves every one of them BYTE-FOR-BYTE and
# only GENERATES the fleet accounts that have no seed. The public `DROP_LANES`
# below is `build_drop_lanes(cli_fleet.REMOTE_HOSTS)`.
_SEED_DROP_LANES = {
    # --- spinbike (single-account, SYSTEM unit, no Access) ---
    ("spinbike", "newlevel"): DropLane(
        host=DROP_HOST_SPINBIKE, port=8828,
        tunnel_uuid="4093c494-b31d-4eb7-8fcb-6c5948f5d4b2",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="spinbike-tunnel.service",
        tunnel_system_unit=True, access=False),

    # --- subdev / david accounts (controller-ingress topology, #931) ---
    # The per-box david tunnel (1564fe31) was RETIRED in #870's controller
    # consolidation. These lanes ride the controller's multi-ingress tunnel;
    # tunnel_config/tunnel_service are None (no local tunnel to edit/restart).
    ("subdev", "david1"): DropLane(
        host=DROP_HOST_DAVID, port=8870,
        tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account="david1",
        topology="controller", origin_host=_SUBDEV_TAILSCALE,
        filedrop_port=8790),  # measured 22.9.2026; #1115 install-reads it
    ("subdev", "david2"): DropLane(
        host="drop-subdev-david2.newlevel.media", port=8871,
        tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account="david1",
        topology="controller", origin_host=_SUBDEV_TAILSCALE,
        filedrop_port=8796),  # measured 22.9.2026
    ("subdev", "david3"): DropLane(
        host="drop-subdev-david3.newlevel.media", port=8872,
        tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account="david1",
        topology="controller", origin_host=_SUBDEV_TAILSCALE,
        filedrop_port=8797),  # measured 22.9.2026
    ("subdev", "david4"): DropLane(
        host="drop-subdev-david4.newlevel.media", port=8873,
        tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account="david1",
        topology="controller", origin_host=_SUBDEV_TAILSCALE,
        filedrop_port=8798),  # measured 22.9.2026

    # --- subdev / marek tunnel ---
    ("subdev", "marek"): DropLane(
        host="drop-subdev-marek.newlevel.media", port=8874,
        tunnel_uuid="1e9555d1-4d19-4e86-8064-361506fbc2cd",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="webterm-marek-tunnel.service",
        tunnel_system_unit=False, access=False,
        gateway_account="marek"),

    # --- subdev / dominika tunnel ---
    ("subdev", "dominika"): DropLane(
        host="drop-subdev-dominika.newlevel.media", port=8875,
        tunnel_uuid="7792f710-16fb-41da-b46d-1d7b1cd0f8a6",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="webterm-dominika-tunnel.service",
        tunnel_system_unit=False, access=True,
        gateway_account="dominika",
        filedrop_port=8804),  # measured 22.9.2026 (uid-derived, no drift)

    # --- gatekeeper box (controller-ingress topology, #1111) ---
    # The owner's daily secret-pickup box joins the controller multi-ingress
    # tunnel as one more drop lane. No local tunnel (the controller renders the
    # ingress); the persistent filedrop service listens on 100.90.94.41:8788
    # (uid 1000, #493 — no persisted port file, measured 22.9.2026). Access is
    # the owner identity only (this is the owner's own box).
    ("odoo-gatekeeper", "gatekeeper"): DropLane(
        host=DROP_HOST_GK, port=8876,
        tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        tunnel_config=None, tunnel_service=None,
        tunnel_system_unit=False, access=True,
        gateway_account=None,
        topology="controller", origin_host=_GK_TAILSCALE,
        filedrop_port=8788),  # measured 22.9.2026
    # NOTE: simap1 is PAUSED (#851) — no entry. montalu1-8 and miva1 ride the
    # controller tunnel once provisioned (go-live step, same topology shape).
}


def build_drop_lanes(remote_hosts):
    """Bound wrapper (#1115): cli_drop_lanes engine with injected primitives."""
    return cli_drop_lanes.build_drop_lanes(
        remote_hosts, seed=_SEED_DROP_LANES, drop_lane_cls=DropLane,
        controller_tunnel_uuid=_CONTROLLER_TUNNEL_UUID,
        port_base=DROP_PORT_BASE, port_max=DROP_PORT_MAX)


DROP_LANES = build_drop_lanes(cli_fleet.REMOTE_HOSTS)  # generated at import (#1115)


# Access specs for Access-gated drop hostnames — reconciled via
# cli_webterm_access.apply_profile (same shape as WEBTERM_ACCESS_APPS).
# `allowed_emails` IS the whole authorization (deny-by-default).
DROP_ACCESS_APPS = {
    DROP_HOST_DAVID: {
        "hostname": DROP_HOST_DAVID,
        "name": "drop — david1",
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
    "drop-subdev-david2.newlevel.media": {
        "hostname": "drop-subdev-david2.newlevel.media",
        "name": "drop — david2",
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
    "drop-subdev-david3.newlevel.media": {
        "hostname": "drop-subdev-david3.newlevel.media",
        "name": "drop — david3",
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
    "drop-subdev-david4.newlevel.media": {
        "hostname": "drop-subdev-david4.newlevel.media",
        "name": "drop — david4",
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
    "drop-subdev-dominika.newlevel.media": {
        "hostname": "drop-subdev-dominika.newlevel.media",
        "name": "drop — dominika",
        "allowed_emails": ["dominika@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
    # #1111: gk is the owner's own box — the Access include is the owner
    # identity ALONE (the same owner email the owner-facing lanes carry).
    DROP_HOST_GK: {
        "hostname": DROP_HOST_GK,
        "name": "drop — gatekeeper",
        "allowed_emails": ["drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
}


def _current_username():
    """The invoking unix account name. Prefers the real (effective-uid) passwd
    entry over $USER/$LOGNAME so a stale/spoofed env var cannot mis-route the
    channel decision. Overridable in tests (patched on the module)."""
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def drop_lane_for_account(nodename=None, username=None):
    """The DropLane for THIS (box, account), or None (#889).

    Resolves by `(nodename, username)` — each account has its own lane with its
    own hostname and port. Falls back to `_current_username()` when username is
    None; fail-safe: any error resolving the username → None (no lane, never a
    wrong lane).
    """
    node = nodename or os.uname().nodename
    if username is None:
        try:
            username = _current_username()
        except Exception:
            return None
    return DROP_LANES.get((node, username))


def drop_lane_for_box(nodename=None):
    """DEPRECATED: backward-compatible wrapper for callers that resolve by
    nodename only. Returns the first matching lane for this box, or None.
    New callers should use `drop_lane_for_account()` instead."""
    node = nodename or os.uname().nodename
    for (n, _u), lane in DROP_LANES.items():
        if n == node:
            return lane
    return None


def public_url_line(host, token):
    """The advertised public HTTPS URL + its transport, spelled out — mirrors
    `_secret_url_line`'s labelled shape so the user sees WHAT the channel is."""
    return ("https://%s/%s/   [verejné cez Cloudflare tunnel — šifrované (TLS), "
            "jednorazový token]" % (host, token))


def public_share_url_line(host, token_name, access=True):
    """The advertised public HTTPS SHARE URL + its transport (#1114). `token_name`
    is the `<token>/<name>` suffix; the `/s/` prefix routes it (at the tunnel) to
    the persistent filedrop service. Mirrors `public_url_line`'s labelled shape;
    the label reflects whether Cloudflare Access fronts this lane (#1114 review —
    a token-only lane must not claim Access)."""
    transport = ("šifrované (TLS), Access" if access
                 else "šifrované (TLS), jednorazový token")
    return "https://%s/s/%s   [verejné cez Cloudflare tunnel — %s]" % (
        host, token_name, transport)


def write_drop_marker(host, port=DROP_PORT_BASE, path=None):
    """Record that a live drop lane exists on this box (host + loopback port).

    Written with `O_NOFOLLOW` + mode 0600 (the repo's #271 sensitive-write
    discipline) — the marker gates a credential-intake URL, so a pre-planted
    symlink at the path must not redirect the write and the file must not be
    world-readable. Contents are non-secret (host+port), but a gratuitous
    deviation from the vault-store bar is not warranted for a credential-routing
    input."""
    p = Path(path if path is not None else DROP_MARKER)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = ("host=%s\nport=%d\n" % (host, port)).encode("utf-8")
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def read_drop_marker(path=None):
    """(host, port) from the go-live marker, or None when absent / unreadable /
    malformed. A malformed / empty / out-of-range marker reads as NO lane
    (fail-safe: the CLI then keeps today's private-only behaviour, never a broken
    public URL or an uncaught bind error)."""
    p = Path(path if path is not None else DROP_MARKER)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    host, port = None, None
    for line in text.splitlines():
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key == "host":
            host = val
        elif key == "port":
            try:
                port = int(val)
            except ValueError:
                return None
    if not host or not port or not (1 <= port <= 65535):
        return None
    return host, port


def resolve_public_lane(want_public=True, have_encrypted_private=None,
                        marker_path=None, nodename=None, username=None):
    """(host, port) for the public drop lane, or None (#889: public is the
    DEFAULT for every account — `want_public` and `have_encrypted_private` are
    accepted but IGNORED for backward compatibility).

    The host+port are the AUTHORITATIVE values from the git-controlled
    `DROP_LANES` registry (`drop_lane_for_account`), NEVER the marker's own
    strings (#664 review A-M2): the marker is a per-account go-live FLAG, so a
    credential URL is never routed to a mutable/foreign marker host. A marker
    whose `host` disagrees with this account's registered lane (a stale copy
    from a home-dir migration, or a planted file) is refused outright.

    The lane is used when this account HAS a registered drop lane AND a matching
    live marker exists. No marker / no registered lane / a mismatched marker →
    None, and the caller prints a loud refuse pointing at the go-live step.
    """
    lane = drop_lane_for_account(nodename, username)
    if lane is None:
        return None
    marker = read_drop_marker(marker_path)
    if marker is None:
        return None
    marker_host, _marker_port = marker
    if marker_host != lane.host:                # stale / foreign marker — refuse
        return None
    return lane.host, lane.port                 # authoritative, from the registry


def resolve_public_lane_full(marker_path=None, nodename=None, username=None):
    """(host, port, bind_ip) for the public drop lane, or None.

    Like `resolve_public_lane` but also returns the IP the drop server must
    BIND on so the tunnel origin can reach it (#931):
    - LOCAL topology: `"127.0.0.1"` (cloudflared on the same box → loopback).
    - CONTROLLER topology: `lane.origin_host` (cloudflared on the controller →
      this box's tailscale IP).

    Fail-closed: a controller lane with no `origin_host` → None (never fall
    back to loopback when the tunnel origin is remote).
    """
    lane = drop_lane_for_account(nodename, username)
    if lane is None:
        return None
    marker = read_drop_marker(marker_path)
    if marker is None:
        return None
    marker_host, _marker_port = marker
    if marker_host != lane.host:
        return None
    if lane.topology == "controller":
        if not lane.origin_host:
            return None                          # fail-closed: no origin → no lane
        return lane.host, lane.port, lane.origin_host
    return lane.host, lane.port, "127.0.0.1"


_CATCHALL_RE = re.compile(r"^(\s*)-\s*service:\s*http_status:404\s*$")


def _drop_ingress_already_present(config_text, drop_host, port=None, filedrop_port=None):
    """True when `drop_host`'s ingress is already present with the expected port(s).

    #1114: with `filedrop_port`, recognises the `/s/` PATH rule (a `hostname` line
    followed by `path: ^/s/` then `service: http://127.0.0.1:<filedrop_port>`).
    Otherwise checks the DROP rule: with `port`, a `hostname` line IMMEDIATELY
    followed by `service: http://127.0.0.1:<port>` — so a preceding `/s/` rule for
    the SAME host (whose next line is `path:`, not `service:`) is never mistaken for
    the drop rule (#889 migration: a host present at the WRONG drop port reads as
    absent and gets rewritten). With neither, just hostname presence.
    """
    esc = re.escape(drop_host)
    if filedrop_port is not None:
        s_pat = (r"(?m)^[ \t]*-[ \t]*hostname:[ \t]*" + esc +
                 r"[ \t]*\n[ \t]*path:[ \t]*\^/s/[ \t]*\n[ \t]*"
                 r"service:[ \t]*http://127\.0\.0\.1:" + str(int(filedrop_port)) + r"[ \t]*$")
        return re.search(s_pat, config_text) is not None
    if port is None:
        return re.search(r"(?m)^[ \t]*-[ \t]*hostname:[ \t]*" + esc + r"[ \t]*$",
                         config_text) is not None
    d_pat = (r"(?m)^[ \t]*-[ \t]*hostname:[ \t]*" + esc +
             r"[ \t]*\n[ \t]*service:[ \t]*http://127\.0\.0\.1:" + str(int(port)) + r"[ \t]*$")
    return re.search(d_pat, config_text) is not None


def _remove_ingress_for_host(config_text, drop_host):
    """Remove the DROP ingress entry (`hostname` + immediately-following `service`)
    for `drop_host`. A `/s/` PATH rule (hostname + path + service) is NOT matched
    (the `path:` line sits between), so an existing share rule is preserved."""
    # Match the hostname line + the immediately following service line.
    pat = (r"(?m)^(\s*)-\s*hostname:\s*" + re.escape(drop_host) +
           r"\s*\n\s*service:\s*\S+\s*\n")
    return re.sub(pat, "", config_text)


def _remove_all_ingress_for_host(config_text, drop_host):
    """#1114: remove ALL ingress entries for `drop_host` — both the `/s/` PATH rule
    (hostname + path + service) and the drop rule (hostname + service) — so the pair
    can be re-inserted in the correct order (`/s/` first)."""
    pat = (r"(?m)^[ \t]*-[ \t]*hostname:[ \t]*" + re.escape(drop_host) +
           r"[ \t]*\n(?:[ \t]*path:[ \t]*\S+[ \t]*\n)?[ \t]*service:[ \t]*\S+[ \t]*\n")
    return re.sub(pat, "", config_text)


def _insert_before_catchall(config_text, build_entry):
    """Insert ingress lines immediately BEFORE the `- service: http_status:404`
    catch-all. `build_entry(indent)` returns the lines, given the catch-all's own
    `-` column so the new entry matches existing indentation. REFUSES (ValueError)
    when the catch-all is absent — a live prod config must not be corrupted."""
    lines = config_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _CATCHALL_RE.match(line.rstrip("\n"))
        if m:
            lines.insert(i, build_entry(m.group(1)))   # m.group(1) = the `-` column
            return "".join(lines)
    raise ValueError(
        "no `- service: http_status:404` catch-all found in the cloudflared "
        "config — refusing to guess where the drop ingress belongs (a live prod "
        "config must not be corrupted)")


def render_drop_ingress_augmentation(config_text, drop_host, port=DROP_PORT_BASE,
                                     filedrop_port=None):
    """`config_text` with a drop-host ingress inserted BEFORE the catch-all 404,
    preserving EVERY existing ingress entry.

    Port-aware idempotent (#889): if `drop_host` is already present with the
    CORRECT port, returns unchanged. If present with a WRONG port (the 8828->8870
    migration), removes the stale entry and re-adds with the correct port. Works
    on the well-known cloudflared config shape (an `ingress:` list whose last
    entry is `- service: http_status:404`). REFUSES (raises ValueError) when it
    cannot find that catch-all.

    #1114: when `filedrop_port` is given (a LOCAL-topology drop lane, so the origin
    is loopback), ALSO manages a `/s/` PATH rule to `http://127.0.0.1:<filedrop_port>`
    rendered BEFORE the drop rule (so `share` deliveries reach the persistent
    filedrop service). Both rules are (re)written together and idempotent. When
    `filedrop_port` is None the pre-#1114 behaviour is byte-identical AND an
    existing `/s/` rule is never stripped (the reconcile heal path keeps it).
    """
    if filedrop_port is None:
        if _drop_ingress_already_present(config_text, drop_host, port=port):
            return config_text
        # If the drop rule exists at the wrong port, remove it first (migration).
        if _drop_ingress_already_present(config_text, drop_host, port=None):
            config_text = _remove_ingress_for_host(config_text, drop_host)
        return _insert_before_catchall(
            config_text,
            lambda ind: ("%s- hostname: %s\n%s  service: http://127.0.0.1:%d\n"
                         % (ind, drop_host, ind, port)))

    # #1114 share lane: manage the /s/ rule AND the drop rule as a pair.
    drop_ok = _drop_ingress_already_present(config_text, drop_host, port=port)
    share_ok = _drop_ingress_already_present(config_text, drop_host,
                                             filedrop_port=filedrop_port)
    if drop_ok and share_ok:
        return config_text
    config_text = _remove_all_ingress_for_host(config_text, drop_host)
    return _insert_before_catchall(
        config_text,
        lambda ind: (
            "%s- hostname: %s\n%s  path: ^/s/\n%s  service: http://127.0.0.1:%d\n"
            "%s- hostname: %s\n%s  service: http://127.0.0.1:%d\n"
            % (ind, drop_host, ind, ind, filedrop_port,
               ind, drop_host, ind, port)))


def _local_filedrop_port(lane):
    """The filedrop service port for a LOCAL-topology lane's `/s/` rule (#1114):
    the lane's MEASURED value if set, else derived from THIS account's uid (#493) —
    valid at `--apply` time because the invoking process IS the lane's account for a
    local tunnel. Returns None if neither is available (no `/s/` rule is rendered)."""
    if lane.filedrop_port is not None:
        return lane.filedrop_port
    try:
        # Mirror the filedrop SERVER's own port resolution exactly
        # (filedrop.PORT = env -> persisted_port() -> default_port_for_uid()), so a
        # box that hit the #33/#493 collision fallback and PERSISTED a non-uid port
        # gets the SAME port in its /s/ rule as the server serves (a share/server
        # port DISAGREEMENT is itself a 404 — the exact class #493 forbids).
        from filedrop import default_port_for_uid, persisted_port
        return persisted_port() or default_port_for_uid()
    except Exception:
        return None


def _restart_argv(lane):
    """The systemctl restart argv for `lane`'s tunnel (SYSTEM unit → sudo -n)."""
    if lane.tunnel_system_unit:
        return ["sudo", "-n", "systemctl", "restart", lane.tunnel_service]
    return ["systemctl", "--user", "restart", lane.tunnel_service]


def _restart_env(lane):
    """The subprocess env for the tunnel restart, or None to inherit (#826).

    A `--user` unit runs over a NON-LOGIN ssh install session, where
    XDG_RUNTIME_DIR / DBUS_SESSION_BUS_ADDRESS are unset, so a bare
    `systemctl --user restart` fails 'Failed to connect to bus: No medium found'
    (the exact david1@subdev incident). Route it through the ONE shared
    systemd-user env helper (`cli_filedrop_watchdog._xdg_runtime_env` — the same
    helper every other remote `--user` call site uses; a LAZY import matching
    this leaf's existing lazy-import-of-siblings pattern, #433). A SYSTEM unit
    runs via `sudo -n systemctl`, which resets env itself, so it needs none →
    inherit (None)."""
    if lane.tunnel_system_unit:
        return None
    from cli_filedrop_watchdog import _xdg_runtime_env
    return _xdg_runtime_env()


def _config_tunnel_uuid(config_text):
    """The `tunnel:` UUID declared in a cloudflared config, or None."""
    m = re.search(r"(?m)^\s*tunnel:\s*(\S+)\s*$", config_text)
    return m.group(1) if m else None


def _reconcile_access(lane, dry_run):
    """Reconcile the Cloudflare Access app for an access-gated drop lane, reusing
    cli_webterm_access.apply_profile (imported LAZILY to keep this a leaf). No-op
    for a token-only lane. Returns `(ok, msg)` — `ok` is False on a real reconcile
    failure OR when the Access layer could not be applied for an access lane (so
    the caller can refuse to mark an access-gated lane LIVE without its promised
    Access protection). `msg` never contains the token. A DRY-RUN that only reads
    is `ok=True` (nothing to fail)."""
    if not lane.access:
        return True, "no Access (token-only TLS lane)"
    spec = DROP_ACCESS_APPS.get(lane.host)
    if spec is None:
        return False, "Access lane but no DROP_ACCESS_APPS spec for %s" % lane.host
    try:
        import cli_webterm_access as acc
    except Exception as e:                             # pragma: no cover - defensive
        return False, "Access reconcile skipped (cannot import cli_webterm_access: %s)" % e
    try:
        token = acc._load_token()
    except OSError as e:
        return False, ("Access token %s unreadable (%s); run `airuleset.py "
                       "webterm-access` prerequisites first"
                       % (acc.WEBTERM_ACCESS_TOKEN_FILE, e))
    if not token:
        return False, "Access token file empty"
    client = acc.AccessClient(acc.WEBTERM_ACCESS_ACCOUNT_ID, token=token)
    res = acc.apply_profile(client, spec, dry_run=dry_run)
    if res.get("error"):
        return False, "Access ERROR: %s" % res["error"]
    return True, "Access %s: %s" % ("(dry-run)" if dry_run else "applied",
                                    "; ".join(res.get("actions") or []) or "-")


def _lanes_for_box(nodename):
    """All DropLane entries for this box, as a list of ((node, user), lane)."""
    return [((n, u), lane) for (n, u), lane in DROP_LANES.items() if n == nodename]


def _lanes_for_tunnel(nodename, tunnel_uuid):
    """DropLane entries on this box sharing a specific tunnel UUID."""
    return [((n, u), lane) for (n, u), lane in DROP_LANES.items()
            if n == nodename and lane.tunnel_uuid == tunnel_uuid]


def drop_ingress_rules_for_controller(cache=None):
    """Controller-tunnel drop ingress rules — thin wrapper over the cli_drop_lanes
    engine with THIS module's DROP_LANES injected (#931/#1114/#1115)."""
    return cli_drop_lanes.drop_ingress_rules_for_controller(DROP_LANES, cache=cache)


def cmd_drop_gateway(args):
    """Reconcile THIS account's drop lane on THIS box (#889). DEFAULT is DRY-RUN
    (no writes, prints the plan) — the `cmd_webterm_access` pattern.

    `--apply`: idempotently augment the invoking account's tunnel config with
    the drop ingress lines for ALL accounts sharing THAT tunnel (preserving
    every existing entry), reconcile Access apps, restart the tunnel, then write
    the go-live marker. DNS (flat CNAMEs) is a manual runbook step.

    On subdev, three different tunnels exist (david/marek/dominika), so each
    gateway account runs `--apply` for its OWN tunnel only.

    Injectable for offline tests: `run` (systemctl), `marker_path`, `nodename`.
    """
    run = getattr(args, "_run", None) or __import__("subprocess").run
    nodename = getattr(args, "_nodename", None)
    marker_path = getattr(args, "_marker_path", None)
    dry_run = getattr(args, "dry_run", False) or not getattr(args, "apply", False)
    username = getattr(args, "_username", None)

    node = nodename or os.uname().nodename

    # Resolve the invoking account's lane first.
    if username is None:
        try:
            username = _current_username()
        except Exception:
            username = None
    my_lane = drop_lane_for_account(node, username) if username else None

    if my_lane is None:
        all_boxes = sorted({n for (n, _u) in DROP_LANES})
        print("drop-gateway: no registered drop lane for %s@%s — nothing to do "
              "(drop lanes exist for boxes: %s)."
              % (username or "?", node, ", ".join(all_boxes)))
        return 0

    # #931: controller-topology lanes have no local tunnel config/service to
    # edit or restart — the controller's install manages the ingress. On the
    # lane account: validate + reconcile Access + write the marker.
    if my_lane.topology == "controller":
        mode = "DRY-RUN (no writes)" if dry_run else "APPLY"
        print("drop-gateway [%s] account=%s@%s topology=controller "
              "tunnel=%s origin=%s:%d"
              % (mode, username, node, my_lane.tunnel_uuid,
                 my_lane.origin_host, my_lane.port))
        if dry_run:
            print("  ingress: controller-managed -> http://%s:%d"
                  % (my_lane.origin_host, my_lane.port))
            if my_lane.access:
                print("  access:  [%s] %s"
                      % (username, _reconcile_access(my_lane, dry_run=True)[1]))
            print("  DNS (manual runbook): CNAME %s -> %s.cfargotunnel.com "
                  "(proxied)" % (my_lane.host, my_lane.tunnel_uuid))
            print("  (dry-run -- nothing changed; re-run with --apply)")
            return 0
        # Reconcile Access for this lane.
        if my_lane.access:
            access_ok, access_msg = _reconcile_access(my_lane, dry_run=False)
            print("  access:  [%s] %s" % (username, access_msg))
            if not access_ok:
                print("  NOT marking LIVE -- Access reconcile did not succeed "
                      "on an access-gated lane; fix the token/app and re-run "
                      "--apply", file=sys.stderr)
                return 1
        write_drop_marker(my_lane.host, my_lane.port, path=marker_path)
        print("  marker written (%s) -- public drop lane is now ARMED for %s"
              % (marker_path or DROP_MARKER, username or "this account"))
        print("  DNS: ensure CNAME %s -> %s.cfargotunnel.com (proxied) exists"
              % (my_lane.host, my_lane.tunnel_uuid))
        print("  NOTE: the ingress is managed on the controller -- run "
              "'airuleset.py install' there to provision it")
        return 0

    # Process only lanes sharing THIS account's tunnel (#889 review C1: subdev
    # has 3 different tunnels, never graft one tunnel's lanes onto another's).
    tunnel_lanes = _lanes_for_tunnel(node, my_lane.tunnel_uuid)

    mode = "DRY-RUN (no writes)" if dry_run else "APPLY"
    print("drop-gateway [%s] account=%s@%s lanes=%d tunnel=%s config=%s"
          % (mode, username, node, len(tunnel_lanes),
             my_lane.tunnel_uuid, my_lane.tunnel_config))

    try:
        config_text = Path(my_lane.tunnel_config).read_text(encoding="utf-8")
    except OSError as e:
        print("drop-gateway: cannot read tunnel config %s: %s"
              % (my_lane.tunnel_config, e), file=sys.stderr)
        return 1

    # Refuse to edit a config that is NOT this lane's tunnel (m1).
    cfg_uuid = _config_tunnel_uuid(config_text)
    if cfg_uuid is not None and cfg_uuid != my_lane.tunnel_uuid:
        print("drop-gateway: %s declares tunnel %s, not this lane's %s — refusing "
              "to edit the wrong tunnel's config"
              % (my_lane.tunnel_config, cfg_uuid, my_lane.tunnel_uuid), file=sys.stderr)
        return 1

    # Add ingress lines for all lanes sharing THIS tunnel (C1 fix: never graft
    # one tunnel's lanes onto another's — subdev has 3 different tunnels).
    # #1114: local topology → also emit the /s/ rule to loopback:<filedrop_port>
    # (the lane's measured value, else derived from this account's uid at --apply
    # time, #493 — the invoking process IS the lane's account for a local tunnel).
    augmented = config_text
    added_hosts = []
    for (_n, _u), lane in tunnel_lanes:
        fp = _local_filedrop_port(lane)
        try:
            new = render_drop_ingress_augmentation(augmented, lane.host, lane.port,
                                                   filedrop_port=fp)
        except ValueError as e:
            print("drop-gateway: %s" % e, file=sys.stderr)
            return 1
        if new != augmented:
            if fp is not None:
                added_hosts.append("%s -> /s/ http://127.0.0.1:%d + http://127.0.0.1:%d"
                                   % (lane.host, fp, lane.port))
            else:
                added_hosts.append("%s -> http://127.0.0.1:%d" % (lane.host, lane.port))
        augmented = new

    changed = augmented != config_text
    if added_hosts:
        for h in added_hosts:
            print("  ingress: ADD %s" % h)
    else:
        print("  ingress: all %d hosts already present (idempotent no-op)"
              % len(tunnel_lanes))

    if dry_run:
        for (_n, _u), lane in tunnel_lanes:
            print("  access:  [%s] %s" % (_u, _reconcile_access(lane, dry_run=True)[1]))
            print("  DNS (manual runbook): CNAME %s -> %s.cfargotunnel.com (proxied)"
                  % (lane.host, lane.tunnel_uuid))
        print("  (dry-run — nothing changed; re-run with --apply)")
        return 0

    if changed:
        Path(my_lane.tunnel_config).write_text(augmented, encoding="utf-8")
        print("  wrote %s (drop ingress added, existing entries preserved)"
              % my_lane.tunnel_config)

    # Restart whenever the config changed OR the invoking account's lane is not
    # yet LIVE (marker absent) — so a re-run AFTER a failed restart still
    # restarts, instead of writing the LIVE marker over a tunnel that never
    # reloaded (#664 review C1).
    if changed or read_drop_marker(marker_path) is None:
        argv = _restart_argv(my_lane)
        try:
            r = run(argv, capture_output=True, text=True, env=_restart_env(my_lane))
            rc = getattr(r, "returncode", 1)
        except Exception as e:                         # pragma: no cover - defensive
            print("  tunnel restart errored (%s) — config written; restart %s "
                  "by hand" % (e, my_lane.tunnel_service), file=sys.stderr)
            return 1
        if rc != 0:
            print("  tunnel restart FAILED (%s): %s"
                  % (" ".join(argv), (getattr(r, "stderr", "") or "").strip()),
                  file=sys.stderr)
            return 1
        print("  restarted %s" % my_lane.tunnel_service)

    # Reconcile Access for access-gated lanes sharing this tunnel.
    access_failed = False
    for (_n, _u), lane in tunnel_lanes:
        access_ok, access_msg = _reconcile_access(lane, dry_run=False)
        print("  access:  [%s] %s" % (_u, access_msg))
        if not access_ok and lane.access:
            access_failed = True
    if access_failed:
        print("  NOT marking LIVE — Access reconcile did not succeed on an "
              "access-gated lane; fix the token/app and re-run --apply",
              file=sys.stderr)
        return 1

    write_drop_marker(my_lane.host, my_lane.port, path=marker_path)
    print("  marker written (%s) — public drop lane is now LIVE for %s on this box"
          % (marker_path or DROP_MARKER, username or "this account"))
    for (_n, _u), lane in tunnel_lanes:
        print("  DNS: ensure CNAME %s -> %s.cfargotunnel.com (proxied) exists"
              % (lane.host, lane.tunnel_uuid))
    return 0


def reconcile_drop_ingress_on_install(run=None, nodename=None, marker_path=None,
                                      username=None):
    """Re-assert the drop ingress into this box's tunnel config at install time —
    idempotently, and ONLY when the lane already went LIVE (marker present)
    (#664 review A-M1).

    WHY: the subdev david tunnel provisioner (`setup_webterm_david_tunnel`)
    UNCONDITIONALLY rewrites `~/.cloudflared/config.yml` with just its own
    hostname + the catch-all on every install — silently deleting a drop ingress
    a prior `drop-gateway --apply` added, while the go-live marker survives. Left
    unhealed, the CLI would then advertise a public URL that 404s. This runs
    AFTER webterm setup in `cmd_install`, re-adds the ingress if it went missing,
    and restarts the tunnel. It NEVER raises — it catches its own errors and
    returns False, which per the contract below FAILS the install (LOUD on
    failure). It is a pure no-op (returns True) on any box without a live drop
    lane (the overwhelming majority — no lane, or a lane whose marker was never
    written).

    Returns True when NOTHING is wrong (the ingress is present+live after this
    call, OR this box has no live drop lane at all, OR this account is a SIBLING
    of a shared drop tunnel it does not own — all benign no-ops), and False ONLY
    on a GENUINE failure (config unreadable / wrong tunnel / no catch-all /
    restart failed / unexpected exception) ON THE TUNNEL-OWNING account. This
    un-overloads the earlier return (#826): `cmd_install` latches `install_failed`
    on a False, so a False MUST mean a real failure — else every one of the
    fleet's no-drop-lane boxes (the majority) would fail its install.

    #838: on a SHARED box `DROP_LANES` is nodename-keyed, so a SIBLING account
    (david2@subdev: marker seeded per the #786 runbook, but no own
    `~/.cloudflared/config.yml` — the tunnel config + `--user` unit live under the
    gateway account david1) resolves to the SAME lane as the gateway account.
    Left unhandled, the sibling's absent config raised OSError → False → the
    install failed on EVERY release push. A sibling has nothing to re-assert (the
    gateway account's own install pass heals the ingress), so when the lane names
    a `gateway_account` and the invoking account (`username`, defaulting to
    `_current_username()`) differs from it, this is a benign no-op (True). The
    check sits BEFORE the config read, so a sibling never touches a config it does
    not own — and #826's loud failure stays on the tunnel-owning account (a lane
    with `gateway_account=None`, or the gateway account itself, still returns
    False on a genuine broken config).
    """
    run = run or __import__("subprocess").run
    try:
        node = nodename or os.uname().nodename
        lane = drop_lane_for_account(node, username)
        if lane is None:
            return True                         # no drop lane for this account — benign no-op
        if read_drop_marker(marker_path) is None:
            return True                         # lane never went live — nothing to preserve (ok)
        # #931: controller-topology lanes have no local tunnel config — the
        # controller's own install manages the ingress. Heal a stale marker
        # (host/port mismatch from pre-#931 registry) and return True.
        if lane.topology == "controller":
            marker = read_drop_marker(marker_path)
            if marker is not None:
                marker_host, marker_port = marker
                if marker_host != lane.host or marker_port != lane.port:
                    write_drop_marker(lane.host, lane.port, path=marker_path)
                    _me = username if username is not None else _current_username()
                    print("  drop-gateway: rewrote stale marker for %s (%s:%d "
                          "-> %s:%d) (#931)"
                          % (_me, marker_host, marker_port,
                             lane.host, lane.port), file=sys.stderr)
            return True
        if lane.gateway_account is not None:
            # #838: a sibling account of a shared drop tunnel owns no config —
            # nothing to re-assert here. Fail-safe: an unresolvable account does
            # NOT divert (proceed → the tunnel-owner path keeps #826's loud fail).
            try:
                me = username if username is not None else _current_username()
            except Exception:
                me = None
            if me is not None and me != lane.gateway_account:
                # #927: the sibling DOES own its marker (per-account, under its
                # own ~/.cloudflared/) — a stale marker (from before #889's
                # per-account lanes) must be rewritten so resolve_public_lane
                # sees the correct host+port instead of returning None forever.
                marker = read_drop_marker(marker_path)
                if marker is not None:
                    marker_host, marker_port = marker
                    if marker_host != lane.host or marker_port != lane.port:
                        write_drop_marker(lane.host, lane.port, path=marker_path)
                        print("  drop-gateway: rewrote stale marker for sibling "
                              "%s (%s:%d -> %s:%d) (#927)"
                              % (me, marker_host, marker_port,
                                 lane.host, lane.port), file=sys.stderr)
                print("  drop-gateway: this account (%s) is a SIBLING of the "
                      "shared drop tunnel owned by the gateway account %r on this "
                      "box — the gateway account re-asserts the ingress; nothing "
                      "to heal here (#838)" % (me, lane.gateway_account),
                      file=sys.stderr)
                return True
        try:
            config_text = Path(lane.tunnel_config).read_text(encoding="utf-8")
        except OSError as e:
            print("  drop-gateway: cannot read %s to re-assert the drop ingress "
                  "(%s)" % (lane.tunnel_config, e), file=sys.stderr)
            return False
        cfg_uuid = _config_tunnel_uuid(config_text)
        if cfg_uuid is not None and cfg_uuid != lane.tunnel_uuid:
            print("  drop-gateway: %s is not this lane's tunnel — cannot heal a "
                  "live drop lane, FAILING the install (#826)"
                  % lane.tunnel_config, file=sys.stderr)
            return False
        # Re-assert ALL lanes on this box that share this tunnel (the gateway
        # account's install pass heals every sibling's ingress).
        augmented = config_text
        for (_n, _u), box_lane in _lanes_for_box(node):
            if box_lane.tunnel_uuid != lane.tunnel_uuid:
                continue  # different tunnel — skip
            try:
                augmented = render_drop_ingress_augmentation(
                    augmented, box_lane.host, box_lane.port)
            except ValueError:
                print("  drop-gateway: %s has no catch-all — cannot heal a live "
                      "drop lane, FAILING the install (#826)"
                      % lane.tunnel_config, file=sys.stderr)
                return False
        # #927: also validate the tunnel owner's marker — a stale port (from
        # before #889's per-account range) must be rewritten so resolve_public_lane
        # matches. The marker is per-account; the owner's marker was written by
        # cmd_drop_gateway --apply and may carry pre-#889 values.
        marker = read_drop_marker(marker_path)
        if marker is not None:
            marker_host, marker_port = marker
            if marker_host != lane.host or marker_port != lane.port:
                write_drop_marker(lane.host, lane.port, path=marker_path)
                print("  drop-gateway: rewrote stale marker for %s (%s:%d -> "
                      "%s:%d) (#927)"
                      % (username or "this account", marker_host, marker_port,
                         lane.host, lane.port), file=sys.stderr)
        if augmented == config_text:
            return True                         # all ingresses already present — no restart
        Path(lane.tunnel_config).write_text(augmented, encoding="utf-8")
        argv = _restart_argv(lane)
        r = run(argv, capture_output=True, text=True, env=_restart_env(lane))
        if getattr(r, "returncode", 1) != 0:
            print("  drop-gateway: re-added the drop ingress to %s but restart "
                  "FAILED (%s) — restart %s by hand"
                  % (lane.tunnel_config, (getattr(r, "stderr", "") or "").strip(),
                     lane.tunnel_service), file=sys.stderr)
            return False
        print("  drop-gateway: re-asserted the drop ingress into %s + restarted %s"
              % (lane.tunnel_config, lane.tunnel_service), file=sys.stderr)
        return True
    except Exception as e:                             # pragma: no cover - defensive
        print("  drop-gateway: ingress re-assert errored (%r) — skipped" % e,
              file=sys.stderr)
        return False
