"""airuleset webterm — MAREK developer gateway provisioning (#612 scope-add
2026-08-24).

The THIRD per-developer gateway, for `marek.newlevel.media`, provisioned on the
SAME subdev VPS as david — but as the `marek` unix account, with a SEPARATE scoped
inventory + Cloudflare Access realm + cloudflared tunnel. #665: this module is now
THIN — the render + setup skeleton lives in the shared parameterized provisioner
`cli_webterm_lane` (one engine for david + marek + any future developer); here only
marek's per-user constants (the source of truth tests patch) + a `_spec()` factory +
public-API wrappers delegating to that engine.

Session set (#661 rework, owner ruling 2026-08-25 — the original single-local-attach
set was owner-REJECTED as incomplete; #787 doplnenie 2026-08-31 added montalu2):
marek's LOCAL tmux group (the gateway runs AS marek on subdev — no ssh, no key,
unchanged) PLUS five ssh tabs — montalu2@subdev + montalu4@subdev (loopback), his
`marek` tmux sessions on dev1 + dev2 (newlevel@<tailscale IP>), and his forestshop
VPS (admin@forestshop-dev, #679 strict host-key pin) — every ssh tab via the
DEDICATED `profiles.WEBTERM_MAREK_IDENTITY` key (never the fleet gatekeeper key,
never the sshpass shared-password branch). The connect allowlist is physically this
six-member set: it can never resolve another stream's id, a david id, or another
person's account (stepan). The lane dash renders through the owner-defined #661 tab
policy (`LaneSpec.dashboard_human="marek"` → WEBTERM_DASHBOARD_TABS).

PREREQUISITE-GATED so a normal subdev install (as any other account) is a safe
NO-OP. #663: the gateway + ttyd bind mode-0700 UNIX sockets in marek's
/run/user/<uid> runtime dir (NOT TCP loopback) — a SEPARATE cloudflared tunnel
(service: unix:<sock>) is the public HTTPS front. AUTH is Cloudflare Access (email
one-time-PIN) at the edge; the gateway runs `--trust-access-header` (no
password/credential), exactly like the david lane.

SECURITY NOTE — the boundary this lane DOES and does NOT provide (honest, #612 R1
review; reachability widened by the #661 rework and #787). The PUBLIC Access-gated
path reaches ONLY marek's scoped six-member set — the connect allowlist is physically
`{marek-subdev, montalu2-subdev, montalu4-subdev, dev1, dev2, forestshop}` (his own
owner-granted targets, ssh only via the dedicated marek key), so a marek WEB LOGIN
can never drive another stream's, david's, or stepan's id, and
marek's Access realm/tunnel are separate from every other developer's. HONEST
TRANSITIVE-REACH consequence (#661 review 🟡): the dev1/dev2 entries ssh as
`newlevel` — the OWNER's maintainer account, whose home holds the fleet keys — so
once the marek pubkey is authorized there, an interactive dev1/dev2 tab is an
owner-account shell from which every stream is transitively reachable. That grant
is the owner's explicit ruling ("marek-ové tmux sessions na dev1/dev2"), NEW trust
(unlike codex-bridge's mirror-of-existing), and the reason _MAREK_GO_LIVE step 5
RECOMMENDS a forced-command `restrict` authorized_keys entry as the default shape. The
multi-tenant LOOPBACK floor is CLOSED (#663): the gateway + ttyd bind mode-0700 UNIX
sockets in marek's runtime dir, so a peer subdev account can no longer reach marek's
gateway/ttyd (or, from marek, another lane's). The only remaining stdlib residual
(no RS256 Access-JWT verification) is unrelated to local reachability — see
cli_webterm_access.py's SECURITY NOTE.

Imports cli_webterm for shared render/template helpers + cli_webterm_lane for the
shared provisioner; the dispatch in cli_webterm.maybe_setup_webterm imports THIS
module lazily, so there is no module-level import cycle.
"""
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles
import cli_webterm_tunnel as tun            # shared managed-tunnel render helpers (#635)
import cli_webterm_lane as lane             # #665: the shared parameterized provisioner

# The gateway login account on subdev (marek's own account); re-exported from the
# profiles leaf so tests + the go-live message have one source.
MAREK_GATEWAY_USER = profiles.MAREK_GATEWAY_USER

# marek's own artifact paths + distinct port constants (kept legacy for the go-live
# text) — DISTINCT from owner (8080/7682) AND david (8081/7683), so the shared subdev
# box is self-documenting. #663 the gateway + ttyd bind UNIX sockets in the account
# runtime dir (NOT these TCP ports) and a cloudflared tunnel fronts the gateway
# socket (no public port, no tailscale IP is involved).
WEBTERM_MAREK_BIND = "127.0.0.1"
WEBTERM_MAREK_TTYD_PORT = 7684
WEBTERM_MAREK_GATEWAY_PORT = 8082
# #663: Access-mode UNIX-socket basenames (in marek's /run/user/<uid>, 0700) that
# REPLACE the TCP loopback ports — this lane's NEW auth-less ttyd :7684 /
# header-forgeable gateway :8082 were the reachability #663's directional note flags
# as newly exposing marek's own account to every other local subdev account. The
# 0700 runtime dir is the account boundary now (see cli_webterm.webterm_runtime_socket_abs).
WEBTERM_MAREK_GATEWAY_SOCK_BASENAME = "webterm-marek-gateway.sock"
WEBTERM_MAREK_TTYD_SOCK_BASENAME = "webterm-marek-ttyd.sock"
WEBTERM_MAREK_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-marek-inventory.json"
WEBTERM_MAREK_DASH_DIR = w.CLAUDE_DIR / "webterm-marek-dash"
WEBTERM_MAREK_DASH_INDEX = WEBTERM_MAREK_DASH_DIR / "index.html"
WEBTERM_MAREK_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-marek-ttyd.sh"
WEBTERM_MAREK_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-marek-ttyd.service")
WEBTERM_MAREK_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-marek-gateway.service")

# marek's SEPARATE cloudflared tunnel (created via dev2's origin cert, #612 go-live)
# — NOT david's. A separate tunnel = separate creds JSON + unit + restart
# blast-radius, so a reconcile/restart of marek's front never disturbs david's live
# terminal, and a compromise of marek's creds fronts ONLY marek's origin (the #612
# marek design, Approach 1). The UUID is public (the DNS CNAME target); the
# per-tunnel creds JSON on subdev is the only secret, and the tunnel provisioner is
# prerequisite-gated on it.
WEBTERM_MAREK_TUNNEL_UUID = "1e9555d1-4d19-4e86-8064-361506fbc2cd"
WEBTERM_MAREK_TUNNEL_CREDS = tun.WEBTERM_CLOUDFLARED_DIR / (
    WEBTERM_MAREK_TUNNEL_UUID + ".json")
# A DEDICATED config file (never the box-default ~/.cloudflared/config.yml, which is
# DAVID's tunnel config on subdev) so the two per-developer tunnels never collide —
# the same isolation the owner tunnel uses (webterm-owner.yml).
WEBTERM_MAREK_TUNNEL_CONFIG = tun.WEBTERM_CLOUDFLARED_DIR / "webterm-marek.yml"
WEBTERM_MAREK_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-marek-tunnel.service")
WEBTERM_MAREK_TUNNEL_HOSTNAME = "marek.newlevel.media"
# subdev cloudflared is the no-sudo user-space static binary in ~/.local/bin.
WEBTERM_MAREK_CLOUDFLARED_BIN = str(Path.home() / ".local" / "bin" / "cloudflared")

_MAREK_GO_LIVE = (
    "  webterm(marek): needs setup to go live —\n"
    "    1. DNS (Cloudflare): marek.newlevel.media -> the marek cloudflared\n"
    "       tunnel (proxied CNAME to %s.cfargotunnel.com). (Created in the #612\n"
    "       marek lane.)\n"
    "    2. Transfer the marek per-tunnel creds JSON to subdev\n"
    "       ~/.cloudflared/%s.json (created via dev2's origin cert).\n"
    "    3. Install ttyd on subdev (auto-installed here into ~/.local/bin) and\n"
    "       run this install AS the marek account.\n"
    "    4. AUTH (#612 owner directive): NO password. A Cloudflare Access email-\n"
    "       OTP app fronts marek.newlevel.media — set WEBTERM_ACCESS_APPS['marek']\n"
    "       allow-list and run `airuleset.py webterm-access --apply`. No credential\n"
    "       is delivered.\n"
    "    5. #661/#787 ssh tabs: deploy the dedicated key %s\n"
    "       (private key on subdev as marek; pubkey in authorized_keys of\n"
    "       montalu2@subdev, montalu4@subdev, newlevel@dev1, newlevel@dev2,\n"
    "       admin@forestshop-dev). RECOMMENDED DEFAULT (#661 review): a\n"
    "       forced-command entry — restrict,command=\"tmux ...\" — especially\n"
    "       on newlevel@dev1/dev2 (the OWNER account: an unrestricted key\n"
    "       there is a full owner shell with transitive fleet reach). Until\n"
    "       the key lands the montalu2/montalu4/dev1/dev2/forestshop tabs\n"
    "       fail visibly; marek@subdev keeps working.\n"
    % (WEBTERM_MAREK_TUNNEL_UUID, WEBTERM_MAREK_TUNNEL_UUID,
       profiles.WEBTERM_MAREK_IDENTITY))

# #614/#638 FOOTGUN: the marek ttyd unit carries the shared self-contained PATH
# (cli_webterm_lane.TTYD_PATH_ENV) so the launcher's bare `exec ttyd` resolves the
# no-sudo ~/.local/bin static binary on a clean systemd --user manager start WITHOUT
# a hand-placed .d/ drop-in — the #614 lesson applied to the marek lane from day 1.
# airuleset renders the MAIN unit only; it never writes/deletes/scans drop-ins. If
# you ever CHANGE the shared PATH, verify no stale hand-placed drop-in remains (it
# would silently override it) with
# `systemctl --user show webterm-marek-ttyd.service -p DropInPaths`.


def _spec():
    """Build marek's LaneSpec from this module's constants, read FRESH each call so
    tests that patch a `WEBTERM_MAREK_*` constant see it. `identity_key=None` +
    `retire_credential_path=None`: marek is a local attach (no ssh key) that never
    had a password credential."""
    return lane.LaneSpec(
        name="marek",
        gateway_user=MAREK_GATEWAY_USER,
        profile=profiles.MAREK,
        bind=WEBTERM_MAREK_BIND,
        ttyd_port=WEBTERM_MAREK_TTYD_PORT,
        gateway_port=WEBTERM_MAREK_GATEWAY_PORT,
        gateway_sock_basename=WEBTERM_MAREK_GATEWAY_SOCK_BASENAME,
        ttyd_sock_basename=WEBTERM_MAREK_TTYD_SOCK_BASENAME,
        inventory_path=WEBTERM_MAREK_INVENTORY_PATH,
        dash_dir=WEBTERM_MAREK_DASH_DIR,
        dash_index=WEBTERM_MAREK_DASH_INDEX,
        launch_path=WEBTERM_MAREK_LAUNCH_PATH,
        ttyd_service_dest=WEBTERM_MAREK_SERVICE_DEST,
        gateway_service_dest=WEBTERM_MAREK_GATEWAY_SERVICE_DEST,
        ttyd_service_name="webterm-marek-ttyd.service",
        gateway_service_name="webterm-marek-gateway.service",
        tunnel_uuid=WEBTERM_MAREK_TUNNEL_UUID,
        tunnel_creds=WEBTERM_MAREK_TUNNEL_CREDS,
        tunnel_config=WEBTERM_MAREK_TUNNEL_CONFIG,
        tunnel_service_dest=WEBTERM_MAREK_TUNNEL_SERVICE_DEST,
        tunnel_service_name="webterm-marek-tunnel.service",
        tunnel_hostname=WEBTERM_MAREK_TUNNEL_HOSTNAME,
        cloudflared_bin=WEBTERM_MAREK_CLOUDFLARED_BIN,
        creds_absent_hint=" (the webterm-marek-612 creds JSON must be present "
                          "on subdev — transfer it from dev2 ~/.cloudflared/%s.json)."
                          % WEBTERM_MAREK_TUNNEL_UUID,
        unit_note=lane.render_lane_unit_note(
            name_upper="MAREK", name_lower="marek", account_suffix=" (marek account)",
            runtime_owner="marek's", tunnel_adjective="a SEPARATE",
            hostname=WEBTERM_MAREK_TUNNEL_HOSTNAME,
            scoped_inventory="Scoped inventory (#661 rework, #787 added montalu2): the\n"
                             "# LOCAL marek tmux session + montalu2@subdev +\n"
                             "# montalu4@subdev + marek's dev1/dev2 sessions +\n"
                             "# admin@forestshop-dev — ssh ONLY via the dedicated\n"
                             "# webterm_marek key (never the fleet gatekeeper key,\n"
                             "# never a david account, never stepan's)."),
        go_live=_MAREK_GO_LIVE,
        label="(subdev marek)",
        log_prefix="webterm(marek)",
        # #661 rework: identity_key STAYS None — the lane's core marek-subdev
        # tab is a keyless LOCAL attach, and gating provisioning on the NEW
        # WEBTERM_MAREK_IDENTITY would no-op re-renders of the LIVE lane until
        # the key is provisioned (a #684 parity regression). The ssh tabs
        # (montalu2/montalu4/dev1/dev2/forestshop) degrade to a VISIBLE ssh
        # failure until the key + authorized_keys land (owner-action,
        # _MAREK_GO_LIVE step 5).
        identity_key=None,
        retire_credential_path=None,
        # #661: the marek dash consumes the owner-defined per-domain tab list
        # (order + exclusivity) — the policy key is the domain's login user.
        dashboard_human=MAREK_GATEWAY_USER,
    )


def render_marek_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_marek_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _write_marek_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. NO credential is
    provisioned (Cloudflare Access replaces the password) and NO ssh key is used (the
    single session is a LOCAL attach). Thin wrapper over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision (running as the
    marek gateway account with ttyd installed; NO dedicated key required — local
    attach). Every False is a SAFE no-op reason."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_marek_tunnel(run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://marek.newlevel.media/ -> the marek gateway's #663 UNIX socket. Uses a
    DEDICATED config file (webterm-marek.yml) so it never collides with david's
    box-default config.yml. Thin wrapper over the shared engine."""
    return lane.setup_tunnel(_spec(), run=run)


def setup_webterm_marek_service(run=None):
    """subdev-only: provision the marek developer gateway (#612 scope-add).
    Prerequisite-gated so an ordinary subdev install (any other account) can never
    break. Idempotent; returns True on success, False on any skip/failure (never
    raises). Thin wrapper over the shared engine, passing marek's own patchable
    seams."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_marek_artifacts,
        tunnel_fn=setup_webterm_marek_tunnel)
