"""airuleset webterm — DOMINIKA observer gateway provisioning (#867, owner request
2026-09-04).

A per-human webterm lane (alongside owner and david; marek was decommissioned
#882, 2026-09-05), for `dominika.newlevel.media`, provisioned on the SAME subdev
VPS as david — but as the `dominika` unix account, with a SEPARATE scoped
inventory + Cloudflare Access realm + cloudflared tunnel. This module is THIN —
the render + setup skeleton lives in the shared parameterized provisioner
`cli_webterm_lane` (one engine for every lane); here only dominika's per-user
constants (the source of truth tests patch) + a `_spec()` factory + public-API
wrappers delegating to that engine.

Session set (owner request 2026-09-04 — "pridat noveho webterm uzivatela dominika
… aby mala pristup k m5, miva"): dominika is a PURE OBSERVER — she has NO local
attach. BOTH her tabs are loopback ssh, and BOTH are CROSS-TENANT OBSERVE tabs —
`montalu5@subdev` (a montalu-family stream) and `miva1@subdev` (a separate
external sub-dev stream
notify-routed to the OWNER) — so she merely WATCHES them, never a within-tenant
read (neither carries `u_tenant`, #703). Every ssh tab goes via the DEDICATED
`profiles.WEBTERM_DOMINIKA_IDENTITY` key (never the fleet gatekeeper key, never the
sshpass shared-password branch). The connect allowlist is physically this
two-member set: it can never resolve another stream's id, a david id, or
another person's account. The lane dash renders through the owner-defined per-domain
tab policy (`LaneSpec.dashboard_human="dominika"` → WEBTERM_DASHBOARD_TABS).

PREREQUISITE-GATED so a normal subdev install (as any other account) is a safe
NO-OP. #663: the gateway + ttyd bind mode-0700 UNIX sockets in dominika's
/run/user/<uid> runtime dir (NOT TCP loopback) — a SEPARATE cloudflared tunnel
(service: unix:<sock>) is the public HTTPS front. AUTH is Cloudflare Access (email
one-time-PIN) at the edge; the gateway runs `--trust-access-header` (no
password/credential), exactly like the david lane.

SECURITY NOTE — the boundary this lane DOES and does NOT provide (honest, #612 R1
review shape). The PUBLIC Access-gated path reaches ONLY dominika's scoped
two-member set — the connect allowlist is physically `{montalu5-subdev,
miva1-subdev}` (ssh only via the dedicated dominika key), so a dominika WEB LOGIN
can never drive another stream's, david's, or the owner's id, and
dominika's Access realm/tunnel are separate from every other lane's. Both targets
are OBSERVE-only (loopback ssh into their own tmux group), so there is NO
owner-realm transitive-reach consequence here —
montalu5 + miva1 are sub-dev stream accounts, not the owner's maintainer account.
Because dominika has NO local tab, until the dedicated key + authorized_keys land
(owner-action, `_DOMINIKA_GO_LIVE` step 5) BOTH tabs fail VISIBLY; there is no
keyless fallback tab.
The multi-tenant LOOPBACK floor is CLOSED (#663): the gateway + ttyd bind mode-0700
UNIX sockets in dominika's runtime dir, so a peer subdev account can no longer reach
dominika's gateway/ttyd (or, from dominika, another lane's). The only remaining
stdlib residual (no RS256 Access-JWT verification) is unrelated to local
reachability — see cli_webterm_access.py's SECURITY NOTE.

Imports cli_webterm for shared render/template helpers + cli_webterm_lane for the
shared provisioner; the dispatch in cli_webterm.maybe_setup_webterm imports THIS
module lazily, so there is no module-level import cycle.
"""
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles
import cli_webterm_tunnel as tun            # shared managed-tunnel render helpers (#635)
import cli_webterm_lane as lane             # the shared parameterized provisioner (#665)

# The gateway login account on subdev (dominika's own account); re-exported from
# the profiles leaf so tests + the go-live message have one source.
DOMINIKA_GATEWAY_USER = profiles.DOMINIKA_GATEWAY_USER

# dominika's own artifact paths + distinct port constants (kept legacy for the
# go-live text) — DISTINCT from owner (8080/7682), david (8081/7683) AND the
# former marek lane (8082/7684, decommissioned #882). #663 the gateway +
# ttyd bind UNIX sockets in the account runtime dir (NOT these TCP ports) and a
# cloudflared tunnel fronts the gateway socket (no public port, no tailscale IP is
# involved).
WEBTERM_DOMINIKA_BIND = "127.0.0.1"
WEBTERM_DOMINIKA_TTYD_PORT = 7685
WEBTERM_DOMINIKA_GATEWAY_PORT = 8083
# #663: Access-mode UNIX-socket basenames (in dominika's /run/user/<uid>, 0700)
# that REPLACE the TCP loopback ports.
WEBTERM_DOMINIKA_GATEWAY_SOCK_BASENAME = "webterm-dominika-gateway.sock"
WEBTERM_DOMINIKA_TTYD_SOCK_BASENAME = "webterm-dominika-ttyd.sock"
WEBTERM_DOMINIKA_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-dominika-inventory.json"
WEBTERM_DOMINIKA_DASH_DIR = w.CLAUDE_DIR / "webterm-dominika-dash"
WEBTERM_DOMINIKA_DASH_INDEX = WEBTERM_DOMINIKA_DASH_DIR / "index.html"
WEBTERM_DOMINIKA_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-dominika-ttyd.sh"
WEBTERM_DOMINIKA_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-dominika-ttyd.service")
WEBTERM_DOMINIKA_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-dominika-gateway.service")

# dominika's SEPARATE cloudflared tunnel (created via dev2's origin cert, #867
# go-live) — NOT david's. A separate tunnel = separate creds JSON + unit +
# restart blast-radius. The UUID is public (the DNS CNAME target); the per-tunnel
# creds JSON on subdev is the only secret, and the tunnel provisioner is
# prerequisite-gated on it.
WEBTERM_DOMINIKA_TUNNEL_UUID = "7792f710-16fb-41da-b46d-1d7b1cd0f8a6"
WEBTERM_DOMINIKA_TUNNEL_CREDS = tun.WEBTERM_CLOUDFLARED_DIR / (
    WEBTERM_DOMINIKA_TUNNEL_UUID + ".json")
# A DEDICATED config file (never the box-default ~/.cloudflared/config.yml, which
# is DAVID's tunnel config on subdev) so the per-lane tunnels never collide.
WEBTERM_DOMINIKA_TUNNEL_CONFIG = tun.WEBTERM_CLOUDFLARED_DIR / "webterm-dominika.yml"
WEBTERM_DOMINIKA_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-dominika-tunnel.service")
WEBTERM_DOMINIKA_TUNNEL_HOSTNAME = "dominika.newlevel.media"
# subdev cloudflared is the no-sudo user-space static binary in ~/.local/bin.
WEBTERM_DOMINIKA_CLOUDFLARED_BIN = str(Path.home() / ".local" / "bin" / "cloudflared")

_DOMINIKA_GO_LIVE = (
    "  webterm(dominika): needs setup to go live —\n"
    "    1. Unix account `dominika` on subdev (root bootstrap) + the fleet\n"
    "       gatekeeper_access pubkey in her authorized_keys (the push path),\n"
    "       and ~/.local/bin/{ttyd,cloudflared}.\n"
    "    2. DNS (Cloudflare): dominika.newlevel.media -> the dominika cloudflared\n"
    "       tunnel (proxied CNAME to %s.cfargotunnel.com).\n"
    "    3. Transfer the dominika per-tunnel creds JSON to subdev\n"
    "       ~/.cloudflared/%s.json (created via dev2's origin cert).\n"
    "    4. AUTH: NO password. A Cloudflare Access email-OTP app fronts\n"
    "       dominika.newlevel.media — set WEBTERM_ACCESS_APPS['dominika']\n"
    "       allow-list (nika.sarikova@gmail.com) and run\n"
    "       `airuleset.py webterm-access --apply`. No credential is delivered.\n"
    "    5. ssh tabs: deploy the dedicated key %s (private key on subdev as\n"
    "       dominika; pubkey in authorized_keys of montalu5@subdev and\n"
    "       miva1@subdev as a forced-command entry —\n"
    "       restrict,pty,command=\"tmux ...\" (keep `pty`; `restrict` alone\n"
    "       kills the PTY)). dominika has NO local tab, so BOTH tabs fail\n"
    "       VISIBLY until the key + authorized_keys land.\n"
    % (WEBTERM_DOMINIKA_TUNNEL_UUID, WEBTERM_DOMINIKA_TUNNEL_UUID,
       profiles.WEBTERM_DOMINIKA_IDENTITY))

# #614/#638 FOOTGUN: the dominika ttyd unit carries the shared self-contained PATH
# (cli_webterm_lane.TTYD_PATH_ENV) so the launcher's bare `exec ttyd` resolves the
# no-sudo ~/.local/bin static binary on a clean systemd --user manager start WITHOUT
# a hand-placed .d/ drop-in — the #614 lesson applied to the dominika lane from day
# 1. airuleset renders the MAIN unit only; it never writes/deletes/scans drop-ins.
# If you ever CHANGE the shared PATH, verify no stale hand-placed drop-in remains
# (it would silently override it) with
# `systemctl --user show webterm-dominika-ttyd.service -p DropInPaths`.


def _spec():
    """Build dominika's LaneSpec from this module's constants, read FRESH each call
    so tests that patch a `WEBTERM_DOMINIKA_*` constant see it. `identity_key=None`:
    dominika has NO local tab (both tabs are ssh), but gating the whole lane on the
    dedicated key would no-op every `write_artifacts` re-render until the key lands
    (a #684 parity regression) — the gateway/tunnel/dashboard must come up as soon
    as the account + ttyd exist, with the two ssh tabs degrading to a VISIBLE ssh
    failure until the key + authorized_keys land (owner-action, _DOMINIKA_GO_LIVE
    step 5). `retire_credential_path=None`: this lane never had a legacy password
    credential (Cloudflare Access replaces the password from day 1)."""
    return lane.LaneSpec(
        name="dominika",
        gateway_user=DOMINIKA_GATEWAY_USER,
        profile=profiles.DOMINIKA,
        bind=WEBTERM_DOMINIKA_BIND,
        ttyd_port=WEBTERM_DOMINIKA_TTYD_PORT,
        gateway_port=WEBTERM_DOMINIKA_GATEWAY_PORT,
        gateway_sock_basename=WEBTERM_DOMINIKA_GATEWAY_SOCK_BASENAME,
        ttyd_sock_basename=WEBTERM_DOMINIKA_TTYD_SOCK_BASENAME,
        inventory_path=WEBTERM_DOMINIKA_INVENTORY_PATH,
        dash_dir=WEBTERM_DOMINIKA_DASH_DIR,
        dash_index=WEBTERM_DOMINIKA_DASH_INDEX,
        launch_path=WEBTERM_DOMINIKA_LAUNCH_PATH,
        ttyd_service_dest=WEBTERM_DOMINIKA_SERVICE_DEST,
        gateway_service_dest=WEBTERM_DOMINIKA_GATEWAY_SERVICE_DEST,
        ttyd_service_name="webterm-dominika-ttyd.service",
        gateway_service_name="webterm-dominika-gateway.service",
        tunnel_uuid=WEBTERM_DOMINIKA_TUNNEL_UUID,
        tunnel_creds=WEBTERM_DOMINIKA_TUNNEL_CREDS,
        tunnel_config=WEBTERM_DOMINIKA_TUNNEL_CONFIG,
        tunnel_service_dest=WEBTERM_DOMINIKA_TUNNEL_SERVICE_DEST,
        tunnel_service_name="webterm-dominika-tunnel.service",
        tunnel_hostname=WEBTERM_DOMINIKA_TUNNEL_HOSTNAME,
        cloudflared_bin=WEBTERM_DOMINIKA_CLOUDFLARED_BIN,
        creds_absent_hint=" (the webterm-dominika-867 creds JSON must be present "
                          "on subdev — transfer it from dev2 ~/.cloudflared/%s.json)."
                          % WEBTERM_DOMINIKA_TUNNEL_UUID,
        unit_note=lane.render_lane_unit_note(
            name_upper="DOMINIKA", name_lower="dominika",
            account_suffix=" (dominika account)",
            runtime_owner="dominika's", tunnel_adjective="a SEPARATE",
            hostname=WEBTERM_DOMINIKA_TUNNEL_HOSTNAME,
            scoped_inventory="Scoped inventory (#867, owner request 2026-09-04):\n"
                             "# TWO OBSERVE tabs only — montalu5@subdev + miva1@subdev\n"
                             "# (loopback ssh via the dedicated webterm_dominika key,\n"
                             "# never the fleet gatekeeper key, never another lane's).\n"
                             "# NO local attach, NO other stream, NO owner-realm box."),
        go_live=_DOMINIKA_GO_LIVE,
        label="(subdev dominika)",
        log_prefix="webterm(dominika)",
        # identity_key STAYS None (see _spec docstring): gating on the NEW
        # WEBTERM_DOMINIKA_IDENTITY would no-op re-renders of the LIVE lane until
        # the key is provisioned (a #684 parity regression). Both ssh tabs degrade
        # to a VISIBLE ssh failure until the key + authorized_keys land
        # (owner-action, _DOMINIKA_GO_LIVE step 5).
        identity_key=None,
        retire_credential_path=None,
        # the dominika dash consumes the owner-defined per-domain tab list
        # (order + exclusivity) — the policy key is the domain's login user.
        dashboard_human=DOMINIKA_GATEWAY_USER,
    )


def render_dominika_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_dominika_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _write_dominika_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. NO credential is
    provisioned (Cloudflare Access replaces the password) and NO ssh key is embedded
    (the two sessions are loopback ssh via the dedicated key at connect time). Thin
    wrapper over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision (running as the
    dominika gateway account with ttyd installed; NO dedicated key required in the
    gate — the two ssh tabs degrade visibly until the key lands, _spec docstring).
    Every False is a SAFE no-op reason."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_dominika_tunnel(run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://dominika.newlevel.media/ -> the dominika gateway's #663 UNIX socket.
    Uses a DEDICATED config file (webterm-dominika.yml) so it never collides with
    david's box-default config.yml. Thin wrapper over the shared engine."""
    return lane.setup_tunnel(_spec(), run=run)


def setup_webterm_dominika_service(run=None):
    """subdev-only: provision the dominika observer gateway (#867). Prerequisite-
    gated so an ordinary subdev install (any other account) can never break.
    Idempotent; returns True on success, False on any skip/failure (never raises).
    Thin wrapper over the shared engine, passing dominika's own patchable seams."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_dominika_artifacts,
        tunnel_fn=setup_webterm_dominika_tunnel)
