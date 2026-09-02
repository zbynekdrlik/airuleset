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
# LIVE (ingress reconciled + tunnel restarted). The CLI's public channel is
# available IFF this file exists — so `--public` and the no-tailscale
# auto-fallback never advertise a URL that would 404 before go-live.
#
# PER-UNIX-ACCOUNT (#664 review B-M2): the loopback origin 127.0.0.1:DROP_PORT is
# box-wide, but this marker lives in the invoking account's own home. On subdev
# the tunnel + config + `--user` unit belong to david1, so `drop-gateway --apply`
# runs there; a SIBLING account (david2, …) that should also use the lane needs
# its OWN marker seeded (the runbook documents this) — otherwise that account's
# `secret request`/`upload` silently stays on the unreachable private URLs.
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
        "allowed_emails": ["david@grena.sk", "drlik.zbynek@gmail.com"],
        "session_duration": "24h",
    },
}


def drop_lane_for_box(nodename=None):
    """The DropLane for THIS box (os.uname().nodename), or None."""
    node = nodename or os.uname().nodename
    return DROP_LANES.get(node)


# Unix accounts whose CONSUMER (the human who opens the one-shot URL) has NO
# tailscale, so the public-TLS drop lane is the DEFAULT delivery channel — even
# without an explicit --public (#786). Keyed on (nodename, unix-account): subdev
# hosts david1/david2 (consumer = David's tailscale-less laptop) ALONGSIDE
# marek/montalu whose consumers DO have tailscale, so the force is per-ACCOUNT,
# never per-box. This ONLY flips the default from private → public; the live
# go-live marker + registered-lane gates in `resolve_public_lane` are unchanged,
# so it can never invent a lane, route to a foreign marker, or fire where there
# is no live drop lane. Root cause it closes: the channel decision used to key
# purely on the BOX's own tailscale interface, so a session had to remember
# --public by hand for every david* delivery, and kept not remembering it.
NO_TAILSCALE_CONSUMER_ACCOUNTS = {
    ("subdev", "david1"),
    ("subdev", "david2"),
}


def _current_username():
    """The invoking unix account name. Prefers the real (effective-uid) passwd
    entry over $USER/$LOGNAME so a stale/spoofed env var cannot mis-route the
    channel decision. Overridable in tests (patched on the module)."""
    import pwd
    return pwd.getpwuid(os.geteuid()).pw_name


def consumer_forces_public(nodename=None, username=None):
    """True when THIS (box, unix-account) has a no-tailscale CONSUMER, so the
    public drop lane is the default even without an explicit --public (#786).

    Keys on the invoking unix account, NOT the box: subdev hosts david1/david2
    (consumer = David's tailscale-less laptop) alongside marek/montalu (consumers
    who DO have tailscale). Fail-safe: any error resolving the username → False
    (today's box-driven behaviour), never a spurious public force."""
    node = nodename or os.uname().nodename
    if username is None:
        try:
            username = _current_username()
        except Exception:
            return False
    return (node, username) in NO_TAILSCALE_CONSUMER_ACCOUNTS


def public_url_line(host, token):
    """The advertised public HTTPS URL + its transport, spelled out — mirrors
    `_secret_url_line`'s labelled shape so the user sees WHAT the channel is."""
    return ("https://%s/%s/   [verejné cez Cloudflare tunnel — šifrované (TLS), "
            "jednorazový token]" % (host, token))


def write_drop_marker(host, port=DROP_PORT, path=None):
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


def resolve_public_lane(want_public, have_encrypted_private, marker_path=None,
                        nodename=None, username=None):
    """(host, port) for the public drop lane, or None.

    The host+port are the AUTHORITATIVE values from the git-controlled
    `DROP_LANES` registry (`drop_lane_for_box`), NEVER the marker's own strings
    (#664 review A-M2): the marker is a per-box go-live FLAG, so a credential
    URL is never routed to a mutable/foreign marker host. A marker whose `host`
    disagrees with this box's registered lane (a stale copy from a home-dir
    migration, or a planted file) is refused outright.

    The lane is used when this box HAS a registered drop lane, a matching live
    marker exists, AND any of: the caller asked for it (`--public`); THIS
    (box, invoking unix-account) has a no-tailscale CONSUMER so the public lane
    is the default (`consumer_forces_public` — the #786 fix for david* accounts);
    OR there is no encrypted private lane available (the no-tailscale auto-fallback
    — the ticket's channel order: tailscale → public). A box WITH an encrypted
    private lane, NO `--public`, and a NON-consumer account keeps today's behaviour
    untouched (None). No marker / no registered lane / a mismatched marker → None,
    so the consumer force (like `--public`) degrades to today's private path rather
    than a 404 or a wrong-host URL — it flips only the DEFAULT, never invents a lane.
    """
    lane = drop_lane_for_box(nodename)
    if lane is None:
        return None
    marker = read_drop_marker(marker_path)
    if marker is None:
        return None
    marker_host, _marker_port = marker
    if marker_host != lane.host:                # stale / foreign marker — refuse
        return None
    if (want_public or consumer_forces_public(nodename, username)
            or not have_encrypted_private):
        return lane.host, DROP_PORT             # authoritative, from the registry
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

    # Refuse to edit a config that is NOT this lane's tunnel (m1): the augmenter
    # and the restart both trust `lane.tunnel_config`, so a mismatched `tunnel:`
    # would graft the drop ingress onto the wrong tunnel.
    cfg_uuid = _config_tunnel_uuid(config_text)
    if cfg_uuid is not None and cfg_uuid != lane.tunnel_uuid:
        print("drop-gateway: %s declares tunnel %s, not this lane's %s — refusing "
              "to edit the wrong tunnel's config"
              % (lane.tunnel_config, cfg_uuid, lane.tunnel_uuid), file=sys.stderr)
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

    if dry_run:
        print("  access:  %s" % _reconcile_access(lane, dry_run=True)[1])
        print("  DNS (manual runbook): CNAME %s -> %s.cfargotunnel.com (proxied)"
              % (lane.host, lane.tunnel_uuid))
        print("  (dry-run — nothing changed; re-run with --apply)")
        return 0

    if changed:
        Path(lane.tunnel_config).write_text(augmented, encoding="utf-8")
        print("  wrote %s (drop ingress added, existing entries preserved)"
              % lane.tunnel_config)

    # Restart whenever the config changed OR the lane is not yet LIVE (marker
    # absent) — so a re-run AFTER a failed restart (config already carries the
    # ingress from the prior run, `changed=False`) still restarts, instead of
    # writing the LIVE marker over a tunnel that never reloaded the ingress
    # (#664 review C1). A fully-live idempotent re-run skips the (prod) restart.
    if changed or read_drop_marker(marker_path) is None:
        argv = _restart_argv(lane)
        try:
            r = run(argv, capture_output=True, text=True, env=_restart_env(lane))
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

    access_ok, access_msg = _reconcile_access(lane, dry_run=False)
    print("  access:  %s" % access_msg)
    if not access_ok:
        # An access-gated lane's Access IS the promised double protection —
        # refuse to mark it LIVE without it (#664 review B-M3).
        print("  NOT marking LIVE — Access reconcile did not succeed on an "
              "access-gated lane; fix the token/app and re-run --apply",
              file=sys.stderr)
        return 1

    write_drop_marker(lane.host, DROP_PORT, path=marker_path)
    print("  marker written (%s) — public drop lane is now LIVE on this box"
          % (marker_path or DROP_MARKER))
    print("  DNS: ensure CNAME %s -> %s.cfargotunnel.com (proxied) exists"
          % (lane.host, lane.tunnel_uuid))
    return 0


def reconcile_drop_ingress_on_install(run=None, nodename=None, marker_path=None):
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
    call, OR this box has no live drop lane at all — a benign no-op), and False
    ONLY on a GENUINE failure (config unreadable / wrong tunnel / no catch-all /
    restart failed / unexpected exception). This un-overloads the earlier return
    (#826): `cmd_install` latches `install_failed` on a False, so a False MUST
    mean a real failure — else every one of the fleet's no-drop-lane boxes (the
    majority) would fail its install.
    """
    run = run or __import__("subprocess").run
    try:
        lane = drop_lane_for_box(nodename)
        if lane is None:
            return True                         # no drop lane on this box — benign no-op (ok)
        if read_drop_marker(marker_path) is None:
            return True                         # lane never went live — nothing to preserve (ok)
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
        try:
            augmented = render_drop_ingress_augmentation(config_text, lane.host)
        except ValueError:
            print("  drop-gateway: %s has no catch-all — cannot heal a live drop "
                  "lane, FAILING the install (#826)"
                  % lane.tunnel_config, file=sys.stderr)
            return False
        if augmented == config_text:
            return True                         # ingress already present — no restart
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
