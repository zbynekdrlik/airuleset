"""airuleset webterm — TIMO observer lane on the controller (#1183, owner request
2026-09-29: "potreboval by som vytvorit novy webterm pristup pre timo. Email je
timotej.kam@gmail.com a nech ma len z dev1 pristup na fohmixer tmux projekt").

The FIFTH per-human lane and the first BORN on the controller. It follows the
#867 recipe on the #870 F4c topology, like the zbynek lane:

  * ``shared_tunnel=True`` — the controller's ONE multi-ingress cloudflared
    tunnel fronts timo.newlevel.media (an ingress rule is derived from this
    spec by ``cli_webterm._setup_controller_webterm``), so there is no
    per-lane tunnel UUID/creds/unit. DNS is the #983 managed CNAME
    (``cli_cloudflare_dns.MANAGED_RECORDS``), gated on the Access app.
  * ``identity_key=None`` — the #867 choice for a new lane: the
    gateway/dashboard come up as soon as ttyd exists, and the single ssh tab
    fails VISIBLY until the lane key and its forced-command line land.

Session set: exactly ONE tab — the ``fohmixer@dev1`` PROJECT account (#1184:
one account per project, declared in cli_account_bootstrap.SERVICE_ACCOUNTS),
attaching the shared project tmux session through the dedicated
``profiles.WEBTERM_TIMO_IDENTITY`` lane key. That key is authorized on
fohmixer@dev1 ONLY as a ``restrict,pty,command=`` line rendered by
``account-bootstrap --render fohmixer``.

SECURITY NOTE (honest, #612 R1 shape). The PUBLIC Access-gated path reaches ONLY
the one-member set {fohmixer} — timo's connect allowlist can never resolve an
owner-realm box, a stream, or another person's account. Timo has NO unix
account, NO ssh key and NO password anywhere (webterm-only, #869): his whole
authorization is the Access allow-list. The fohmixer account is a separate uid
with NO sudo and NO secrets (its declaration). Its bootstrap also closes the
routes by which that uid could become the owner's `newlevel` account through
its weak shared password: a uid-keyed nftables rule rejects every new outbound
ssh (loopback included), and pam_wheel/polkit bar su/pkexec. These are
enforced by the bootstrap and verified at go-live step 5 — not assumed. It
does NOT stop other outbound traffic (https for the repo and the Claude API).
The gateway runs under the controller's `airuleset` account (the accepted #870 F4c
B1 residue shared by every lane); its 0700 runtime-dir UNIX sockets are the
local boundary (#663).
"""
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles
import cli_webterm_lane as lane

TIMO_GATEWAY_USER = profiles.TIMO_GATEWAY_USER

# Port pair — next after zbynek (7686/8084). #663 the gateway + ttyd bind
# UNIX sockets in the account runtime dir (NOT these TCP ports).
WEBTERM_TIMO_TTYD_PORT = 7687
WEBTERM_TIMO_GATEWAY_PORT = 8085
WEBTERM_TIMO_GATEWAY_SOCK_BASENAME = "webterm-timo-gateway.sock"
WEBTERM_TIMO_TTYD_SOCK_BASENAME = "webterm-timo-ttyd.sock"
WEBTERM_TIMO_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-timo-inventory.json"
WEBTERM_TIMO_DASH_DIR = w.CLAUDE_DIR / "webterm-timo-dash"
WEBTERM_TIMO_DASH_INDEX = WEBTERM_TIMO_DASH_DIR / "index.html"
WEBTERM_TIMO_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-timo-ttyd.sh"
WEBTERM_TIMO_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-timo-ttyd.service")
WEBTERM_TIMO_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-timo-gateway.service")

# Tunnel fields are DUMMY (shared_tunnel=True: setup_service never calls the
# per-lane tunnel provisioner — the zbynek lane precedent).
WEBTERM_TIMO_TUNNEL_UUID = "00000000-0000-0000-0000-000000000000"
WEBTERM_TIMO_TUNNEL_CREDS = Path.home() / ".cloudflared" / "dummy-timo.json"
WEBTERM_TIMO_TUNNEL_CONFIG = Path.home() / ".cloudflared" / "dummy-timo.yml"
WEBTERM_TIMO_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "dummy-timo-tunnel.service")
WEBTERM_TIMO_TUNNEL_HOSTNAME = "timo.newlevel.media"
WEBTERM_TIMO_CLOUDFLARED_BIN = str(Path.home() / ".local" / "bin" / "cloudflared")

_TIMO_GO_LIVE = (
    "  webterm(timo): needs setup to go live (#1183, supervisor) —\n"
    "    1. Mint the LANE key on the controller: ssh-keygen -t ed25519 -N ''\n"
    "       -C webterm-timo-controller -f %s (0600).\n"
    "       Paste its PUBLIC key into cli_webterm_only.\n"
    "       WEBTERM_CONTROLLER_LANE_PUBKEYS['timo'] (+ the #870 F4b lock test).\n"
    "    2. Render + run as root on dev1:\n"
    "       airuleset.py account-bootstrap --render fohmixer > fohmixer.sh\n"
    "       (creates fohmixer, NO sudo, forced-command lines for zbynek/marek/\n"
    "       timo, clones zbynekdrlik/fohmixer, starts the fohmixer tmux session).\n"
    "       Push fohmixer `dev` first; retire the newlevel checkout afterwards.\n"
    "    3. Registry: flip projects-registry.json fohmixer -> account fohmixer,\n"
    "       path /home/fohmixer/devel/fohmixer; lower cli_accounts.LEGACY_CEILING\n"
    "       25 -> 24 in the same change; add the fohmixer@dev1 REMOTE_HOSTS\n"
    "       entry (claudy shape) so push manages its airuleset. The Claude\n"
    "       login goes through the claudy lease, never a hand-copied\n"
    "       .credentials.json; a push credential is a NEW declared secret.\n"
    "    4. AUTH: NO password. `airuleset.py webterm-access --apply` creates the\n"
    "       Access app for timo.newlevel.media (allow-list timotej.kam@gmail.com);\n"
    "       the next controller install then upserts the managed DNS CNAME\n"
    "       (it is gated on that Access app) onto the shared controller tunnel.\n"
    "    5. Verify: all three dashboards (zbynek/marek/timo) open the fohmixer\n"
    "       tab (Playwright), and AS fohmixer each of these FAILS: `sudo -n true`,\n"
    "       `su - newlevel`, `pkexec true`, `ssh -o BatchMode=yes\n"
    "       newlevel@127.0.0.1 true` (connection reset).\n"
    % profiles.WEBTERM_TIMO_IDENTITY)


def _spec():
    """Build timo's LaneSpec from this module's constants, read FRESH each call so
    tests that patch a ``WEBTERM_TIMO_*`` constant see it."""
    return lane.LaneSpec(
        name="timo",
        gateway_user=TIMO_GATEWAY_USER,
        profile=profiles.TIMO,
        bind="127.0.0.1",
        ttyd_port=WEBTERM_TIMO_TTYD_PORT,
        gateway_port=WEBTERM_TIMO_GATEWAY_PORT,
        gateway_sock_basename=WEBTERM_TIMO_GATEWAY_SOCK_BASENAME,
        ttyd_sock_basename=WEBTERM_TIMO_TTYD_SOCK_BASENAME,
        inventory_path=WEBTERM_TIMO_INVENTORY_PATH,
        dash_dir=WEBTERM_TIMO_DASH_DIR,
        dash_index=WEBTERM_TIMO_DASH_INDEX,
        launch_path=WEBTERM_TIMO_LAUNCH_PATH,
        ttyd_service_dest=WEBTERM_TIMO_SERVICE_DEST,
        gateway_service_dest=WEBTERM_TIMO_GATEWAY_SERVICE_DEST,
        ttyd_service_name="webterm-timo-ttyd.service",
        gateway_service_name="webterm-timo-gateway.service",
        tunnel_uuid=WEBTERM_TIMO_TUNNEL_UUID,
        tunnel_creds=WEBTERM_TIMO_TUNNEL_CREDS,
        tunnel_config=WEBTERM_TIMO_TUNNEL_CONFIG,
        tunnel_service_dest=WEBTERM_TIMO_TUNNEL_SERVICE_DEST,
        tunnel_service_name="dummy-timo-tunnel.service",
        tunnel_hostname=WEBTERM_TIMO_TUNNEL_HOSTNAME,
        cloudflared_bin=WEBTERM_TIMO_CLOUDFLARED_BIN,
        creds_absent_hint="",
        unit_note=lane.render_lane_unit_note(
            name_upper="TIMO", name_lower="timo",
            account_suffix=" (airuleset account, controller)",
            runtime_owner="airuleset's", tunnel_adjective="the SHARED controller",
            hostname=WEBTERM_TIMO_TUNNEL_HOSTNAME,
            scoped_inventory="Scoped inventory (#1183, owner request 2026-09-29):\n"
                             "# ONE tab — the fohmixer@dev1 project account, via\n"
                             "# the dedicated webterm_timo lane key (a forced-\n"
                             "# command line on fohmixer only). Timo has no\n"
                             "# account, key or password anywhere."),
        go_live=_TIMO_GO_LIVE,
        label="(controller timo)",
        log_prefix="webterm(timo)",
        identity_key=None,
        retire_credential_path=None,
        dashboard_human=TIMO_GATEWAY_USER,
        shared_tunnel=True,
    )


def render_timo_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_timo_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _write_timo_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. NO credential
    and NO ssh key is embedded. Thin wrapper over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only on the controller as `airuleset` (LANE_HOST
    acceptance branch) with ttyd installed. Every False is a SAFE no-op."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_timo_tunnel(run=None):
    """No-op: the controller's shared tunnel is provisioned centrally."""
    return True


def setup_webterm_timo_service(run=None):
    """Controller-only: provision the timo observer gateway. Prerequisite-gated
    so any other install is a safe no-op. Returns True on success."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_timo_artifacts,
        tunnel_fn=setup_webterm_timo_tunnel)
