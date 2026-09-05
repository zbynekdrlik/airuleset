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

Both target boxes ALREADY run a cloudflared tunnel fronting `newlevel.media`
subdomains (spinbike UUID 4093c494…, subdev/david UUID 1564fe31…), so the fix
adds ONE drop-host ingress → 127.0.0.1:<drop-port> to each EXISTING tunnel — no new
tunnel is created (that would need dev2's origin cert, #635).
"""
import os
import re
import sys
from pathlib import Path

# #433 self-contained leaf: same directory, identical value as airuleset.REPO_DIR.
REPO_DIR = Path(__file__).resolve().parent

# Per-account loopback port range for the public drop lane (#889). Each account
# on a shared box gets its own port, so concurrent ephemeral servers (upload,
# secret show, secret request) never collide. Range 8870-8889 sits above show's
# 8850-8869 and below no other airuleset range. Distinct from filedrop 8788,
# upload 8799-8819, secret 8830-8849, show 8850-8869. Grandfathered:
# spinbike's 8828 predates the per-account range and sits in the gap.
DROP_PORT_BASE = 8870
DROP_PORT_MAX = 8889

# Flat single-level drop hostnames. Single-level is LOAD-BEARING: Cloudflare
# Universal SSL for newlevel.media is `*.newlevel.media` (ONE level), so a
# 2-level `drop.david.newlevel.media` would have NO valid edge cert and would
# silently break the mandatory TLS. Every existing host (zbynek/david/spinbike
# .newlevel.media) is single-level, confirming the cert shape.
# Naming: `drop-<box>.newlevel.media` on single-account boxes,
# `drop-<box>-<account>.newlevel.media` on shared boxes (#889).
DROP_HOST_SPINBIKE = "drop-spinbike.newlevel.media"
DROP_HOST_DAVID = "drop-david.newlevel.media"  # grandfathered for david1

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
                 tunnel_system_unit, access, gateway_account=None):
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


# Per-account drop lanes (#889), keyed by (nodename, username) — each account
# gets its OWN hostname + loopback port, eliminating the shared-port contention
# that blocked sibling accounts when one held the port (the david1/david3 live
# incident). On single-account boxes the tuple key is the ONLY representation
# (no bare-nodename fallback — the registry is an EXPLICIT allowlist).
#
# TUNNEL TOPOLOGY on subdev: david1-4 ride the david tunnel (1564fe31), marek
# rides the marek tunnel (1e9555d1), dominika rides the dominika tunnel
# (7792f710). montalu1-8 and miva1 ride the david tunnel (provisioned at
# go-live). The gateway_account of each shared tunnel is the tunnel OWNER.
_SUBDEV_DAVID_TUNNEL = "1564fe31-a95f-4053-93d4-baff2b8a6e97"
_SUBDEV_DAVID_SERVICE = "webterm-david-tunnel.service"

DROP_LANES = {
    # --- spinbike (single-account, SYSTEM unit, no Access) ---
    ("spinbike", "newlevel"): DropLane(
        host=DROP_HOST_SPINBIKE, port=8828,
        tunnel_uuid="4093c494-b31d-4eb7-8fcb-6c5948f5d4b2",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="spinbike-tunnel.service",
        tunnel_system_unit=True, access=False),

    # --- subdev / david tunnel (david1-4) ---
    ("subdev", "david1"): DropLane(
        host=DROP_HOST_DAVID, port=8870,
        tunnel_uuid=_SUBDEV_DAVID_TUNNEL,
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service=_SUBDEV_DAVID_SERVICE,
        tunnel_system_unit=False, access=True,
        gateway_account="david1"),
    ("subdev", "david2"): DropLane(
        host="drop-subdev-david2.newlevel.media", port=8871,
        tunnel_uuid=_SUBDEV_DAVID_TUNNEL,
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service=_SUBDEV_DAVID_SERVICE,
        tunnel_system_unit=False, access=True,
        gateway_account="david1"),
    ("subdev", "david3"): DropLane(
        host="drop-subdev-david3.newlevel.media", port=8872,
        tunnel_uuid=_SUBDEV_DAVID_TUNNEL,
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service=_SUBDEV_DAVID_SERVICE,
        tunnel_system_unit=False, access=True,
        gateway_account="david1"),
    ("subdev", "david4"): DropLane(
        host="drop-subdev-david4.newlevel.media", port=8873,
        tunnel_uuid=_SUBDEV_DAVID_TUNNEL,
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service=_SUBDEV_DAVID_SERVICE,
        tunnel_system_unit=False, access=True,
        gateway_account="david1"),

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
        gateway_account="dominika"),
    # NOTE: simap1 is PAUSED (#851) — no entry. montalu1-8 and miva1 ride the
    # david tunnel once provisioned (go-live step, same gateway_account shape).
}

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


_CATCHALL_RE = re.compile(r"^(\s*)-\s*service:\s*http_status:404\s*$")


def _drop_ingress_already_present(config_text, drop_host, port=None):
    """True when `drop_host` is already an ingress hostname. When `port` is given,
    also verifies the service line points at the right port (#889 migration: a
    host present at the OLD port 8828 must be rewritten to its per-account port)."""
    m = re.search(r"(?m)^\s*-\s*hostname:\s*" + re.escape(drop_host) + r"\s*$",
                  config_text)
    if not m:
        return False
    if port is None:
        return True
    # Check the service line immediately following the hostname line.
    rest = config_text[m.end():]
    svc_m = re.match(r"\n\s*service:\s*http://127\.0\.0\.1:(\d+)\s*$", rest, re.M)
    if svc_m and int(svc_m.group(1)) == port:
        return True
    return False  # hostname present but wrong port — needs rewrite


def _remove_ingress_for_host(config_text, drop_host):
    """Remove a hostname+service ingress entry for `drop_host` from the config.
    Returns the config unchanged if the host is not present."""
    # Match the hostname line + the immediately following service line.
    pat = (r"(?m)^(\s*)-\s*hostname:\s*" + re.escape(drop_host) +
           r"\s*\n\s*service:\s*\S+\s*\n")
    return re.sub(pat, "", config_text)


def render_drop_ingress_augmentation(config_text, drop_host, port=DROP_PORT_BASE):
    """`config_text` with a drop-host ingress inserted BEFORE the catch-all 404,
    preserving EVERY existing ingress entry.

    Port-aware idempotent (#889): if `drop_host` is already present with the
    CORRECT port, returns unchanged. If present with a WRONG port (the 8828->8870
    migration), removes the stale entry and re-adds with the correct port. Works
    on the well-known cloudflared config shape (an `ingress:` list whose last
    entry is `- service: http_status:404`). REFUSES (raises ValueError) when it
    cannot find that catch-all.
    """
    if _drop_ingress_already_present(config_text, drop_host, port=port):
        return config_text
    # If the hostname exists but at the wrong port, remove it first (migration).
    if _drop_ingress_already_present(config_text, drop_host, port=None):
        config_text = _remove_ingress_for_host(config_text, drop_host)
    lines = config_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _CATCHALL_RE.match(line.rstrip("\n"))
        if m:
            indent = m.group(1)                       # the `-` column
            entry = ("%s- hostname: %s\n%s  service: http://127.0.0.1:%d\n"
                     % (indent, drop_host, indent, port))
            lines.insert(i, entry)
            return "".join(lines)
    raise ValueError(
        "no `- service: http_status:404` catch-all found in the cloudflared "
        "config — refusing to guess where the drop ingress belongs (a live prod "
        "config must not be corrupted)")


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
    augmented = config_text
    added_hosts = []
    for (_n, _u), lane in tunnel_lanes:
        try:
            new = render_drop_ingress_augmentation(augmented, lane.host, lane.port)
        except ValueError as e:
            print("drop-gateway: %s" % e, file=sys.stderr)
            return 1
        if new != augmented:
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
        if lane.gateway_account is not None:
            # #838: a sibling account of a shared drop tunnel owns no config —
            # nothing to re-assert here. Fail-safe: an unresolvable account does
            # NOT divert (proceed → the tunnel-owner path keeps #826's loud fail).
            try:
                me = username if username is not None else _current_username()
            except Exception:
                me = None
            if me is not None and me != lane.gateway_account:
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
