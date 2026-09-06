"""airuleset webterm — ZBYNEK (owner) gateway provisioning on the controller
(#870 F4a-live).

The owner's webterm lane on the controller box, mirroring the david/marek/
dominika thin-module shape. Unlike the subdev lanes, the owner lane:

  * ``shared_tunnel=True`` — uses the controller's single shared cloudflared
    tunnel (no per-lane tunnel_uuid/creds/config/service). setup_service
    skips tunnel_fn.
  * ``collector_mode="--u-collect"`` — the owner is the cross-tenant fleet
    collector, not a per-tenant lane.
  * ``identity_key=None`` — not gated on key presence (same reasoning as
    dominika: gateway/tunnel/dashboard come up immediately).

Session set is ``profiles.zbynek_inventory()`` — the DECLARATIVE owner
inventory, never derived from ``_deployable_hosts()``.

Port pair: 7686/8084 (next after dominika's 7685/8083).

Imports cli_webterm for shared render/template helpers + cli_webterm_lane for
the shared provisioner; the dispatch in cli_webterm.maybe_setup_webterm imports
THIS module lazily, so there is no module-level import cycle.
"""
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles
import cli_webterm_lane as lane

# The gateway runs as the airuleset control account on the controller.
ZBYNEK_GATEWAY_USER = "airuleset"

# Port pair — next after dominika (7685/8083). #663 the gateway + ttyd bind
# UNIX sockets in the account runtime dir (NOT these TCP ports) and the
# SHARED controller tunnel fronts the gateway socket.
WEBTERM_ZBYNEK_TTYD_PORT = 7686
WEBTERM_ZBYNEK_GATEWAY_PORT = 8084
# #663: Access-mode UNIX-socket basenames (in airuleset's /run/user/<uid>, 0700).
WEBTERM_ZBYNEK_GATEWAY_SOCK_BASENAME = "webterm-zbynek-gateway.sock"
WEBTERM_ZBYNEK_TTYD_SOCK_BASENAME = "webterm-zbynek-ttyd.sock"
WEBTERM_ZBYNEK_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-zbynek-inventory.json"
WEBTERM_ZBYNEK_DASH_DIR = w.CLAUDE_DIR / "webterm-zbynek-dash"
WEBTERM_ZBYNEK_DASH_INDEX = WEBTERM_ZBYNEK_DASH_DIR / "index.html"
WEBTERM_ZBYNEK_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-zbynek-ttyd.sh"
WEBTERM_ZBYNEK_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-zbynek-ttyd.service")
WEBTERM_ZBYNEK_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-zbynek-gateway.service")

# Tunnel fields are DUMMY (shared_tunnel=True means setup_service skips
# tunnel_fn, so these are never used for provisioning).
WEBTERM_ZBYNEK_TUNNEL_UUID = "00000000-0000-0000-0000-000000000000"
WEBTERM_ZBYNEK_TUNNEL_CREDS = Path.home() / ".cloudflared" / "dummy-zbynek.json"
WEBTERM_ZBYNEK_TUNNEL_CONFIG = Path.home() / ".cloudflared" / "dummy-zbynek.yml"
WEBTERM_ZBYNEK_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "dummy-zbynek-tunnel.service")
WEBTERM_ZBYNEK_TUNNEL_HOSTNAME = "zbynek.newlevel.media"
WEBTERM_ZBYNEK_CLOUDFLARED_BIN = str(Path.home() / ".local" / "bin" / "cloudflared")

_ZBYNEK_GO_LIVE = (
    "  webterm(zbynek): the owner lane on the controller.\n"
    "    The shared controller tunnel fronts zbynek.newlevel.media.\n"
    "    Prerequisites: airuleset account + ttyd + cloudflared.\n")


def _spec():
    """Build zbynek's LaneSpec from this module's constants, read FRESH each call
    so tests that patch a ``WEBTERM_ZBYNEK_*`` constant see it.
    ``shared_tunnel=True``: the controller's shared tunnel, not a per-lane one.
    ``collector_mode='--u-collect'``: the owner is the cross-tenant fleet collector.
    ``identity_key=None``: not gated on key presence."""
    return lane.LaneSpec(
        name="zbynek",
        gateway_user=ZBYNEK_GATEWAY_USER,
        profile=profiles.OWNER,
        bind="127.0.0.1",
        ttyd_port=WEBTERM_ZBYNEK_TTYD_PORT,
        gateway_port=WEBTERM_ZBYNEK_GATEWAY_PORT,
        gateway_sock_basename=WEBTERM_ZBYNEK_GATEWAY_SOCK_BASENAME,
        ttyd_sock_basename=WEBTERM_ZBYNEK_TTYD_SOCK_BASENAME,
        inventory_path=WEBTERM_ZBYNEK_INVENTORY_PATH,
        dash_dir=WEBTERM_ZBYNEK_DASH_DIR,
        dash_index=WEBTERM_ZBYNEK_DASH_INDEX,
        launch_path=WEBTERM_ZBYNEK_LAUNCH_PATH,
        ttyd_service_dest=WEBTERM_ZBYNEK_SERVICE_DEST,
        gateway_service_dest=WEBTERM_ZBYNEK_GATEWAY_SERVICE_DEST,
        ttyd_service_name="webterm-zbynek-ttyd.service",
        gateway_service_name="webterm-zbynek-gateway.service",
        tunnel_uuid=WEBTERM_ZBYNEK_TUNNEL_UUID,
        tunnel_creds=WEBTERM_ZBYNEK_TUNNEL_CREDS,
        tunnel_config=WEBTERM_ZBYNEK_TUNNEL_CONFIG,
        tunnel_service_dest=WEBTERM_ZBYNEK_TUNNEL_SERVICE_DEST,
        tunnel_service_name="dummy-zbynek-tunnel.service",
        tunnel_hostname=WEBTERM_ZBYNEK_TUNNEL_HOSTNAME,
        cloudflared_bin=WEBTERM_ZBYNEK_CLOUDFLARED_BIN,
        creds_absent_hint="",
        unit_note=lane.render_lane_unit_note(
            name_upper="ZBYNEK", name_lower="zbynek",
            account_suffix=" (airuleset account)",
            runtime_owner="airuleset's", tunnel_adjective="the SHARED controller",
            hostname=WEBTERM_ZBYNEK_TUNNEL_HOSTNAME,
            scoped_inventory="Scoped inventory (#870 F4a, owner lane):\n"
                             "# The full owner fleet inventory — dev1, dev2, gk,\n"
                             "# subdev streams, spinbike — the cross-tenant\n"
                             "# collector set."),
        go_live=_ZBYNEK_GO_LIVE,
        label="(controller zbynek)",
        log_prefix="webterm(zbynek)",
        identity_key=None,
        retire_credential_path=None,
        dashboard_human="zbynek",
        collector_mode="--u-collect",
        shared_tunnel=True,
    )


def render_zbynek_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_zbynek_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _write_zbynek_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. Thin wrapper
    over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision (running as
    the airuleset gateway account with ttyd installed). Every False is a SAFE
    no-op reason."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_zbynek_tunnel(run=None):
    """No-op for the zbynek lane: the controller's shared tunnel is provisioned
    centrally, not per-lane."""
    return True


def setup_webterm_zbynek_service(run=None):
    """Controller-only: provision the zbynek owner gateway. Prerequisite-gated
    so a non-controller install is a safe no-op. Returns True on success."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_zbynek_artifacts,
        tunnel_fn=setup_webterm_zbynek_tunnel)
