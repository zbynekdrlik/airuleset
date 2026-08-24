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
adds ONE drop-host ingress → 127.0.0.1:DROP_PORT to each EXISTING tunnel — no new
tunnel is created (that would need dev2's origin cert, #635).
"""
import os
import re
import sys
from pathlib import Path

# #433 self-contained leaf: same directory, identical value as airuleset.REPO_DIR.
REPO_DIR = Path(__file__).resolve().parent

# ONE fixed loopback port the public drop lane binds. A managed cloudflared
# tunnel ingress fronts a public hostname onto 127.0.0.1:DROP_PORT, so the
# ephemeral vault/upload/show server binds THIS port (never a random one) on the
# public lane. Distinct from filedrop 8788, upload 8799-8819, secret 8830-8849,
# show 8850-8869 — it sits in the gap at 8828. One human does one action at a
# time, so request/show/upload share this port sequentially (a busy port is a
# clean refuse, never a silent wrong bind).
DROP_PORT = 8828

# Flat single-level drop hostnames. Single-level is LOAD-BEARING: Cloudflare
# Universal SSL for newlevel.media is `*.newlevel.media` (ONE level), so a
# 2-level `drop.david.newlevel.media` would have NO valid edge cert and would
# silently break the mandatory TLS. Every existing host (zbynek/david/spinbike
# .newlevel.media) is single-level, confirming the cert shape.
DROP_HOST_SPINBIKE = "drop-spinbike.newlevel.media"
DROP_HOST_DAVID = "drop-david.newlevel.media"

# The go-live marker a `drop-gateway --apply` writes once a box's drop lane is
# LIVE (ingress reconciled + local origin verified). The CLI's public channel is
# available IFF this file exists — so `--public` and the no-tailscale
# auto-fallback never advertise a URL that would 404 before go-live.
DROP_MARKER = Path.home() / ".cloudflared" / "airuleset-drop.conf"

_CFDIR = Path.home() / ".cloudflared"


class DropLane:
    """One box's public-TLS drop lane: the flat public hostname, the EXISTING
    cloudflared tunnel it rides (uuid + config path + restart unit), and whether
    Cloudflare Access fronts it. `access=True` = double protection Access+token
    (the no-tailscale CLIENT case, David → subdev); `access=False` = token-only
    TLS (the no-tailscale owner BOX, spinbike)."""

    def __init__(self, host, tunnel_uuid, tunnel_config, tunnel_service,
                 tunnel_system_unit, access):
        self.host = host
        self.tunnel_uuid = tunnel_uuid
        self.tunnel_config = tunnel_config
        self.tunnel_service = tunnel_service
        # True → a SYSTEM unit (restart via `sudo -n systemctl restart`);
        # False → a `--user` unit (`systemctl --user restart`).
        self.tunnel_system_unit = tunnel_system_unit
        self.access = access


# Per-box drop lanes, keyed by os.uname().nodename — the SAME box-identity
# mechanism cli_webterm_profiles.profile_for_host uses (username is useless here:
# spinbike/dev1/dev2 all share the unix user `newlevel`).
DROP_LANES = {
    # no-tailscale BOX — rides the existing spinbike SITE tunnel (a SYSTEM unit
    # that also serves the live spinbike.sk website; the augmentation preserves
    # every existing ingress). Owner-only ops → token-only TLS, no Access.
    "spinbike": DropLane(
        host=DROP_HOST_SPINBIKE,
        tunnel_uuid="4093c494-b31d-4eb7-8fcb-6c5948f5d4b2",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="spinbike-tunnel.service",
        tunnel_system_unit=True,
        access=False),
    # no-tailscale CLIENT (David's laptop) → subdev; rides the existing david
    # tunnel, behind Cloudflare Access (email OTP) = double protection.
    "subdev": DropLane(
        host=DROP_HOST_DAVID,
        tunnel_uuid="1564fe31-a95f-4053-93d4-baff2b8a6e97",
        tunnel_config=_CFDIR / "config.yml",
        tunnel_service="webterm-david-tunnel.service",
        tunnel_system_unit=False,
        access=True),
}

# Access spec for the drop-david host — reconciled via cli_webterm_access.
# apply_profile (same shape as WEBTERM_ACCESS_APPS). Owner + David: either may
# deliver a secret/file to david1/2. `allowed_emails` IS the whole authorization
# (deny-by-default).
DROP_ACCESS_APPS = {
    DROP_HOST_DAVID: {
        "hostname": DROP_HOST_DAVID,
        "name": "drop — david",
        "allowed_emails": ["david@grena.biz", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
}


def drop_lane_for_box(nodename=None):
    """The DropLane for THIS box (os.uname().nodename), or None."""
    node = nodename or os.uname().nodename
    return DROP_LANES.get(node)


def public_url_line(host, token):
    """The advertised public HTTPS URL + its transport, spelled out — mirrors
    `_secret_url_line`'s labelled shape so the user sees WHAT the channel is."""
    return ("https://%s/%s/   [verejné cez Cloudflare tunnel — šifrované (TLS), "
            "jednorazový token]" % (host, token))


def write_drop_marker(host, port=DROP_PORT, path=None):
    """Record that a live drop lane exists on this box (host + loopback port)."""
    p = Path(path if path is not None else DROP_MARKER)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("host=%s\nport=%d\n" % (host, port), encoding="utf-8")


def read_drop_marker(path=None):
    """(host, port) from the go-live marker, or None when absent / unreadable /
    malformed. A malformed or empty marker reads as NO lane (fail-safe: the CLI
    then keeps today's private-only behaviour, never a broken public URL)."""
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
    if not host or not port:
        return None
    return host, port


def resolve_public_lane(want_public, have_encrypted_private, marker_path=None):
    """(host, port) for the public drop lane, or None.

    The public-TLS lane is used when the box HAS a live drop lane (marker
    present) AND either the caller asked for it (`--public`) OR there is no
    encrypted private lane available (the no-tailscale auto-fallback — the
    ticket's channel order: tailscale → public). A box WITH an encrypted private
    lane and NO `--public` keeps today's behaviour untouched (returns None).
    A box with no marker never offers a public URL (returns None), so `--public`
    before go-live degrades to today's private path rather than a 404 URL.
    """
    marker = read_drop_marker(marker_path)
    if marker is None:
        return None
    if want_public or not have_encrypted_private:
        return marker
    return None


_CATCHALL_RE = re.compile(r"^(\s*)-\s*service:\s*http_status:404\s*$")


def _drop_ingress_already_present(config_text, drop_host):
    return bool(re.search(r"(?m)^\s*-\s*hostname:\s*" + re.escape(drop_host) + r"\s*$",
                          config_text))


def render_drop_ingress_augmentation(config_text, drop_host, port=DROP_PORT):
    """`config_text` with a drop-host ingress inserted BEFORE the catch-all 404,
    preserving EVERY existing ingress entry.

    Idempotent: if `drop_host` is already an ingress hostname, returns
    `config_text` unchanged. Works on the well-known cloudflared config shape (an
    `ingress:` list whose last entry is `- service: http_status:404`). REFUSES
    (raises ValueError) when it cannot find that catch-all, rather than guessing
    where the drop ingress belongs and corrupting a live prod config that also
    serves a real website (spinbike.sk).
    """
    if _drop_ingress_already_present(config_text, drop_host):
        return config_text
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


def _reconcile_access(lane, dry_run):
    """Reconcile the Cloudflare Access app for an access-gated drop lane, reusing
    cli_webterm_access.apply_profile (imported LAZILY to keep this a leaf). No-op
    for a token-only lane. Returns a short status string (never the token)."""
    if not lane.access:
        return "no Access (token-only TLS lane)"
    spec = DROP_ACCESS_APPS.get(lane.host)
    if spec is None:
        return "Access lane but no DROP_ACCESS_APPS spec for %s — skipped" % lane.host
    try:
        import cli_webterm_access as acc
    except Exception as e:                             # pragma: no cover - defensive
        return "Access reconcile skipped (cannot import cli_webterm_access: %s)" % e
    try:
        token = acc._load_token()
    except OSError as e:
        return ("Access reconcile skipped — token %s unreadable (%s); run "
                "`airuleset.py webterm-access` prerequisites first"
                % (acc.WEBTERM_ACCESS_TOKEN_FILE, e))
    if not token:
        return "Access reconcile skipped — token file empty"
    client = acc.AccessClient(acc.WEBTERM_ACCESS_ACCOUNT_ID, token=token)
    res = acc.apply_profile(client, spec, dry_run=dry_run)
    if res.get("error"):
        return "Access ERROR: %s" % res["error"]
    return "Access %s: %s" % ("(dry-run)" if dry_run else "applied",
                              "; ".join(res.get("actions") or []) or "-")


def cmd_drop_gateway(args):
    """Reconcile THIS box's public-TLS drop lane (#664). DEFAULT is DRY-RUN (no
    writes, prints the plan) — the `cmd_webterm_access` pattern.

    `--apply`: idempotently augment the box's EXISTING cloudflared tunnel config
    with the drop ingress (preserving every existing entry), reconcile the
    Cloudflare Access app for an access-gated lane, restart the tunnel, then
    write the go-live marker. DNS (a flat CNAME → `<uuid>.cfargotunnel.com`) is a
    documented manual runbook step — no DNS helper exists in this repo, and #635
    already treats the DNS cutover as manual.

    Injectable for offline tests: `run` (systemctl), `marker_path`, `nodename`.
    """
    run = getattr(args, "_run", None) or __import__("subprocess").run
    nodename = getattr(args, "_nodename", None)
    marker_path = getattr(args, "_marker_path", None)
    dry_run = getattr(args, "dry_run", False) or not getattr(args, "apply", False)

    lane = drop_lane_for_box(nodename)
    if lane is None:
        node = nodename or os.uname().nodename
        print("drop-gateway: this box (%s) has no declared drop lane — nothing "
              "to do (drop lanes exist for: %s)."
              % (node, ", ".join(sorted(DROP_LANES))))
        return 0

    mode = "DRY-RUN (no writes)" if dry_run else "APPLY"
    print("drop-gateway [%s] host=%s tunnel=%s config=%s"
          % (mode, lane.host, lane.tunnel_uuid, lane.tunnel_config))

    try:
        config_text = Path(lane.tunnel_config).read_text(encoding="utf-8")
    except OSError as e:
        print("drop-gateway: cannot read tunnel config %s: %s"
              % (lane.tunnel_config, e), file=sys.stderr)
        return 1

    try:
        augmented = render_drop_ingress_augmentation(config_text, lane.host)
    except ValueError as e:
        print("drop-gateway: %s" % e, file=sys.stderr)
        return 1

    changed = augmented != config_text
    print("  ingress: %s"
          % ("ADD %s -> http://127.0.0.1:%d" % (lane.host, DROP_PORT) if changed
             else "already present (idempotent no-op)"))
    print("  access:  %s" % _reconcile_access(lane, dry_run=True))

    if dry_run:
        print("  DNS (manual runbook): CNAME %s -> %s.cfargotunnel.com (proxied)"
              % (lane.host, lane.tunnel_uuid))
        print("  (dry-run — nothing changed; re-run with --apply)")
        return 0

    if changed:
        Path(lane.tunnel_config).write_text(augmented, encoding="utf-8")
        print("  wrote %s (drop ingress added, existing entries preserved)"
              % lane.tunnel_config)
        argv = _restart_argv(lane)
        try:
            r = run(argv, capture_output=True, text=True)
            rc = getattr(r, "returncode", 1)
        except Exception as e:                         # pragma: no cover - defensive
            print("  tunnel restart errored (%s) — config written; restart %s "
                  "by hand" % (e, lane.tunnel_service), file=sys.stderr)
            return 1
        if rc != 0:
            print("  tunnel restart FAILED (%s): %s"
                  % (" ".join(argv), (getattr(r, "stderr", "") or "").strip()),
                  file=sys.stderr)
            return 1
        print("  restarted %s" % lane.tunnel_service)
    print("  access:  %s" % _reconcile_access(lane, dry_run=False))

    write_drop_marker(lane.host, DROP_PORT, path=marker_path)
    print("  marker written (%s) — public drop lane is now LIVE on this box"
          % (marker_path or DROP_MARKER))
    print("  DNS: ensure CNAME %s -> %s.cfargotunnel.com (proxied) exists"
          % (lane.host, lane.tunnel_uuid))
    return 0
