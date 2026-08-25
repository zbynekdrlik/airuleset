"""airuleset webterm — DAVID developer gateway provisioning (#612).

The public per-developer gateway for `david.newlevel.media`, provisioned on the
subdev VPS as the `david1` account. #665: this module is now THIN — the render +
setup skeleton lives in the shared parameterized provisioner `cli_webterm_lane`
(one engine for david + marek + any future developer); here only david's per-user
constants (the source of truth tests patch) + a `_spec()` factory + public-API
wrappers delegating to that engine.

#663: the gateway + ttyd bind mode-0700 UNIX-domain sockets in david1's
/run/user/<uid> runtime dir (NOT TCP loopback) — a cloudflared tunnel (service:
unix:<sock>) is the public HTTPS front (no public port, no sudo), and filesystem
permissions on the 0700 dir are the local account boundary on the shared subdev
box. Its scoped inventory — handed to the ttyd child via the `WEBTERM_INVENTORY`
env var the launcher exports (NOT a client-injectable argv flag: ttyd's `-a`
appends client `?arg=` values as argv, so an argv flag would be injectable, #612
review) — is david1..4 + codex-bridge ONLY, so the connect allowlist can never
resolve an owner-fleet id.

AUTH (#612 owner directive 2026-08-22): NO password/credential — Cloudflare Access
(email OTP) at the edge is the whole gate; the gateway runs `--trust-access-header`.

Imports cli_webterm for shared render/credential helpers + cli_webterm_lane for the
shared provisioner; the dispatch in cli_webterm.maybe_setup_webterm imports THIS
module lazily, so there is no module-level import cycle.
"""
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles
import cli_webterm_tunnel as tun            # #635: shared managed-tunnel render helpers
import cli_webterm_lane as lane             # #665: the shared parameterized provisioner

# The david deployment's own artifact paths + distinct port constants (kept legacy
# for the go-live text; the owner's are 8080/7682). #663 the gateway + ttyd bind
# UNIX sockets in the account runtime dir (NOT these TCP ports) and cloudflared
# fronts the gateway socket, so no public port and no tailscale-IP is involved.
WEBTERM_DAVID_BIND = "127.0.0.1"
WEBTERM_DAVID_TTYD_PORT = 7683
WEBTERM_DAVID_GATEWAY_PORT = 8081
# #663: Access-mode UNIX-socket basenames (in david1's /run/user/<uid>, 0700) that
# REPLACE the TCP loopback ports — on the shared subdev box any local account could
# reach 127.0.0.1:<port>, so a peer account forged the trust header at :8081 / hit
# the auth-less ttyd :7683 directly. Filesystem permissions on the 0700 runtime dir
# are the account boundary now (see cli_webterm.webterm_runtime_socket_abs).
WEBTERM_DAVID_GATEWAY_SOCK_BASENAME = "webterm-david-gateway.sock"
WEBTERM_DAVID_TTYD_SOCK_BASENAME = "webterm-david-ttyd.sock"
WEBTERM_DAVID_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-david-inventory.json"
WEBTERM_DAVID_DASH_DIR = w.CLAUDE_DIR / "webterm-david-dash"
WEBTERM_DAVID_DASH_INDEX = WEBTERM_DAVID_DASH_DIR / "index.html"
WEBTERM_DAVID_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-david-ttyd.sh"
WEBTERM_DAVID_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-david-ttyd.service")
WEBTERM_DAVID_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-david-gateway.service")

# #635: david's cloudflared tunnel under airuleset management. The tunnel already
# exists (created via dev2's origin cert in #612); airuleset WRITES + reconciles its
# config + systemd --user unit. The UUID is public (the DNS CNAME target); the
# per-tunnel creds JSON on subdev is the only secret, and setup_webterm_david_tunnel
# is prerequisite-gated on it.
WEBTERM_DAVID_TUNNEL_UUID = "1564fe31-a95f-4053-93d4-baff2b8a6e97"
WEBTERM_DAVID_TUNNEL_CREDS = tun.WEBTERM_CLOUDFLARED_DIR / (
    WEBTERM_DAVID_TUNNEL_UUID + ".json")
# david's box has no other tunnel, so the default ~/.cloudflared/config.yml is its
# own — kept as-is (matches the current working unit's --config path).
WEBTERM_DAVID_TUNNEL_CONFIG = tun.WEBTERM_CLOUDFLARED_DIR / "config.yml"
WEBTERM_DAVID_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-david-tunnel.service")
WEBTERM_DAVID_TUNNEL_HOSTNAME = "david.newlevel.media"
# subdev cloudflared is the no-sudo user-space static binary in ~/.local/bin.
WEBTERM_DAVID_CLOUDFLARED_BIN = str(Path.home() / ".local" / "bin" / "cloudflared")

_DAVID_GO_LIVE = (
    "  webterm(david): needs setup to go live —\n"
    "    1. DNS (Cloudflare): david.newlevel.media -> cloudflared tunnel (proxied\n"
    "       CNAME). (zbynek.newlevel.media is a SEPARATE owner lane now moving to\n"
    "       its OWN Cloudflare Access app + dedicated dev1 tunnel — #635.)\n"
    "    2. Install ttyd on subdev + run a cloudflared tunnel (as david1, no\n"
    "       sudo) fronting HTTPS david.newlevel.media -> the gateway's #663 UNIX\n"
    "       socket (service: unix:%s).\n"
    "    3. Deploy the dedicated key %s (authorized ONLY on david1-4).\n"
    "    4. AUTH (#612 owner directive): NO password. Put a Cloudflare Access\n"
    "       email-OTP app in front — set WEBTERM_ACCESS_APPS['david'] allow-list\n"
    "       and run `airuleset.py webterm-access --apply`. Adding a person\n"
    "       (marek) is one more e-mail in that list. No credential is delivered.\n"
    % (w.webterm_runtime_socket_abs(WEBTERM_DAVID_GATEWAY_SOCK_BASENAME),
       profiles.WEBTERM_DAVID_IDENTITY))

# #614/#638 INVARIANT + FOOTGUN (do NOT drop — content-locked by
# tests/test_webterm_david.py::TestDavidDropInInvariant638). The DAVID ttyd unit
# carries the shared self-contained PATH (cli_webterm_lane.TTYD_PATH_ENV) so the
# launcher's bare `exec ttyd` resolves the no-sudo ~/.local/bin static binary on a
# clean systemd --user manager start WITHOUT a hand-placed .d/ drop-in (#614).
# airuleset renders this MAIN unit and never writes or deletes .service.d/ drop-ins.
# The #612 go-live hand-placed webterm-david-ttyd.service.d/10-path.conf with this
# same PATH; #638 confirmed it REDUNDANT — removed by a ONE-TIME MANUAL step
# (owner-action, recorded on the ticket), never by code (a tool that deletes files a
# human hand-placed is a more dangerous tool). FOOTGUN: if you ever CHANGE
# cli_webterm_lane.TTYD_PATH_ENV, a stale hand-placed drop-in would silently
# OVERRIDE it (drop-ins load last) and airuleset will NOT clean it up — verify none
# remains with `systemctl --user show webterm-david-ttyd.service -p DropInPaths`.


def _spec():
    """Build david's LaneSpec from this module's constants, read FRESH each call so
    tests that patch a `WEBTERM_DAVID_*` constant (or a `profiles.*` value) see it."""
    return lane.LaneSpec(
        name="david",
        gateway_user=profiles.DAVID_GATEWAY_USER,
        profile=profiles.DAVID,
        bind=WEBTERM_DAVID_BIND,
        ttyd_port=WEBTERM_DAVID_TTYD_PORT,
        gateway_port=WEBTERM_DAVID_GATEWAY_PORT,
        gateway_sock_basename=WEBTERM_DAVID_GATEWAY_SOCK_BASENAME,
        ttyd_sock_basename=WEBTERM_DAVID_TTYD_SOCK_BASENAME,
        inventory_path=WEBTERM_DAVID_INVENTORY_PATH,
        dash_dir=WEBTERM_DAVID_DASH_DIR,
        dash_index=WEBTERM_DAVID_DASH_INDEX,
        launch_path=WEBTERM_DAVID_LAUNCH_PATH,
        ttyd_service_dest=WEBTERM_DAVID_SERVICE_DEST,
        gateway_service_dest=WEBTERM_DAVID_GATEWAY_SERVICE_DEST,
        ttyd_service_name="webterm-david-ttyd.service",
        gateway_service_name="webterm-david-gateway.service",
        tunnel_uuid=WEBTERM_DAVID_TUNNEL_UUID,
        tunnel_creds=WEBTERM_DAVID_TUNNEL_CREDS,
        tunnel_config=WEBTERM_DAVID_TUNNEL_CONFIG,
        tunnel_service_dest=WEBTERM_DAVID_TUNNEL_SERVICE_DEST,
        tunnel_service_name="webterm-david-tunnel.service",
        tunnel_hostname=WEBTERM_DAVID_TUNNEL_HOSTNAME,
        cloudflared_bin=WEBTERM_DAVID_CLOUDFLARED_BIN,
        creds_absent_hint=" (the webterm-david-612 creds JSON must be present "
                          "on subdev).",
        unit_note=lane.render_lane_unit_note(
            name_upper="DAVID", name_lower="david", account_suffix="",
            runtime_owner="david1's", tunnel_adjective="a",
            hostname=WEBTERM_DAVID_TUNNEL_HOSTNAME,
            scoped_inventory="Scoped inventory: david1-4 + codex-bridge only\n"
                             "# (never the owner fleet)."),
        go_live=_DAVID_GO_LIVE,
        label="(subdev david)",
        log_prefix="webterm(david)",
        identity_key=profiles.WEBTERM_DAVID_IDENTITY,
        retire_credential_path=w.WEBTERM_DAVID_CRED_PATH,
    )


def render_david_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_david_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _retire_david_credential():
    """Delete the now-dead david password credential file (#612 owner directive:
    Cloudflare Access replaces the password). Best-effort + idempotent; returns True
    iff a file was actually removed. Only the mode-600 credential FILE is removed;
    the dedicated ssh key (webterm_david_ed25519) is untouched."""
    return lane.retire_credential(w.WEBTERM_DAVID_CRED_PATH, "webterm(david)")


def _write_david_artifacts():
    """Write the scoped inventory + dashboard + launcher + units, and RETIRE any dead
    password credential. Thin wrapper over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision (running as
    david1 with the dedicated key present and ttyd installed). Every False is a SAFE
    no-op reason."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_david_tunnel(run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://david.newlevel.media/ -> the david gateway's #663 UNIX socket. Config
    path stays the box-default ~/.cloudflared/config.yml (matches the current working
    unit exactly — lowest-risk for a live external-dev terminal; subdev has no other
    tunnel, so no collision). Thin wrapper over the shared engine."""
    return lane.setup_tunnel(_spec(), run=run)


def setup_webterm_david_service(run=None):
    """subdev-only: provision the david developer gateway (#612). Prerequisite-gated
    so an ordinary subdev install can never break. Idempotent; returns True on
    success, False on any skip/failure (never raises). Thin wrapper over the shared
    engine, passing david's own patchable seams."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_david_artifacts,
        tunnel_fn=setup_webterm_david_tunnel)
