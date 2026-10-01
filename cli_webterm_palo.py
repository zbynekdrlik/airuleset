"""airuleset webterm — PALO lane on the controller (#1205, owner request
2026-09-30, verbatim: "Potrebujem vytvorit novy webterm, bude pre
palo@montalu.sk a bude tam len montalu6").

The SIXTH per-human lane, the second born on the controller. It is the timo
lane shape (#1183) through the shared ``lane.controller_lane_spec`` helper: the
controller's ONE multi-ingress cloudflared tunnel fronts palo.newlevel.media
(the ingress rule derives from this spec in
``cli_webterm._setup_controller_webterm``), DNS is the #983 managed CNAME gated
on the Access app, and ``identity_key=None`` brings the gateway/dashboard up at
once while the single ssh tab fails VISIBLY until the lane key lands.

Session set: exactly ONE tab — the ``montalu6@subdev`` STREAM account, the same
tab (session + start dir) the owner dashboard has, through the dedicated
``profiles.WEBTERM_PALO_IDENTITY`` lane key. montalu6 is a stream account, not
a #1184 project account and not a webterm-only account, so its lane line is
NOT rendered by a bootstrap or a desired-set rewrite: it is the
append-or-refresh ``cli_webterm_only.append_controller_lane_pubkey_command``
script this module renders (``forced-command-install``), keyed on the palo
blob, leaving every other line of montalu6's authorized_keys untouched.

SECURITY NOTE (honest, #612 R1 shape). The PUBLIC Access-gated path reaches
ONLY the one-member set {montalu6-subdev}; Palo's connect allowlist can never
resolve an owner-realm box, another stream or another person's account. Palo
has NO unix account, NO ssh key and NO password anywhere (webterm-only, #869):
his whole authorization is the Access allow-list. The lane key's line is the
owner's montalu6 forced command byte-for-byte (``restrict,pty,command=``: no
port/agent/X11 forwarding, no other command). What the attach itself gives is
the montalu6 tmux session — a working shell AS montalu6 — which is exactly the
access the owner asked to grant. The gateway runs under the controller's
`airuleset` account (the accepted #870 F4c B1 residue shared by every lane).
"""
import sys

import cli_webterm_lane as lane
import cli_webterm_only as wo
import cli_webterm_profiles as profiles

PALO_GATEWAY_USER = profiles.PALO_GATEWAY_USER

# Port pair — next after timo (7687/8085). #663 the gateway + ttyd bind UNIX
# sockets in the account runtime dir (NOT these TCP ports).
WEBTERM_PALO_TTYD_PORT = 7688
WEBTERM_PALO_GATEWAY_PORT = 8086

_PALO_GO_LIVE = (
    "  webterm(palo): needs setup to go live (#1205, supervisor) —\n"
    "    1. Mint the LANE key on the controller: ssh-keygen -t ed25519 -N ''\n"
    "       -C webterm-palo-controller -f %s (0600).\n"
    "       Paste its PUBLIC key into cli_webterm_only.\n"
    "       WEBTERM_CONTROLLER_LANE_PUBKEYS['palo'] (+ the #870 F4b lock test:\n"
    "       EXPECTED_HUMANS and a fingerprint pin).\n"
    "    2. Install the forced-command line on montalu6@subdev (append-or-\n"
    "       refresh on the palo blob, every other line untouched):\n"
    "       python3 cli_webterm_palo.py forced-command-install | ssh <push path\n"
    "       to montalu6@subdev> bash -s. Then `airuleset.py status` there shows\n"
    "       no `forced command: STALE` line.\n"
    "    3. AUTH: NO password, NO account.\n"
    "       `airuleset.py webterm-access --apply --profile palo` creates the\n"
    "       Access app for palo.newlevel.media (allow-list palo@montalu.sk).\n"
    "       The next controller install then\n"
    "       upserts the managed DNS CNAME (gated on that app), adds the shared\n"
    "       tunnel ingress, provisions this gateway, and adds palo@montalu.sk to\n"
    "       the drop-subdev-montalu6 Access app (#1115 reader rule).\n"
    "    4. Verify: https://palo.newlevel.media/ answers 302 to Cloudflare\n"
    "       Access; a Playwright login through the gateway shows exactly ONE\n"
    "       tab, montalu6, attached to the montalu6 session.\n"
    % profiles.WEBTERM_PALO_IDENTITY)


def _spec():
    """Build palo's LaneSpec through the shared controller-lane helper, read
    FRESH each call so a test that patches a ``WEBTERM_PALO_*`` port sees it."""
    return lane.controller_lane_spec(
        "palo", profile=profiles.PALO, gateway_user=PALO_GATEWAY_USER,
        ttyd_port=WEBTERM_PALO_TTYD_PORT, gateway_port=WEBTERM_PALO_GATEWAY_PORT,
        scoped_inventory="Scoped inventory (#1205, owner request 2026-09-30):\n"
                         "# ONE tab — the montalu6@subdev stream session, via\n"
                         "# the dedicated webterm_palo lane key (a forced-\n"
                         "# command line on montalu6 only). Palo has no\n"
                         "# account, key or password anywhere.",
        go_live=_PALO_GO_LIVE)


def render_palo_ttyd_unit():
    return lane.render_ttyd_unit(_spec())


def render_palo_gateway_unit():
    return lane.render_gateway_unit(_spec())


def _write_palo_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. NO credential
    and NO ssh key is embedded. Thin wrapper over the shared engine."""
    return lane.write_artifacts(_spec())


def prerequisites_ready():
    """(ok, reason) — True only on the controller as `airuleset` (LANE_HOST
    acceptance branch) with ttyd installed. Every False is a SAFE no-op."""
    return lane.prerequisites_ready(_spec())


def setup_webterm_palo_tunnel(run=None):
    """No-op: the controller's shared tunnel is provisioned centrally."""
    return True


def setup_webterm_palo_service(run=None):
    """Controller-only: provision the palo gateway. Prerequisite-gated so any
    other install is a safe no-op. Returns True on success."""
    return lane.setup_service(
        _spec(), run=run,
        prereq_fn=prerequisites_ready,
        write_artifacts_fn=_write_palo_artifacts,
        tunnel_fn=setup_webterm_palo_tunnel)


def forced_command_key_line():
    """The ONE authorized_keys line for the palo lane key on its single tab's
    account: ``restrict,pty,command="<attach>"`` rendered by the same
    ``_controller_lane_key_line`` the owner's montalu6 line comes from, so the
    attach (session + start dir) is identical and nothing broader. Raises
    ValueError until the key is minted (go-live step 1) — never a partial
    render."""
    pubkey = wo.WEBTERM_CONTROLLER_LANE_PUBKEYS.get("palo")
    if not pubkey:
        raise ValueError(
            "the palo lane has no controller pubkey yet — mint %s on the "
            "controller and add it to WEBTERM_CONTROLLER_LANE_PUBKEYS['palo'] "
            "first (go-live step 1)" % profiles.WEBTERM_PALO_IDENTITY)
    (entry,) = profiles.palo_inventory()
    return wo._controller_lane_key_line(
        entry["preferred"], pubkey, start_dir_chain=entry.get("start_dir_chain"),
        account=entry["user"])


def render_forced_command_install():
    """The shell script (run AS montalu6 on subdev) that appends — or refreshes
    in place — the palo lane line, keyed on the palo blob. Never a desired-set
    rewrite: montalu6 is not a webterm-only account (the F1 ruling)."""
    (entry,) = profiles.palo_inventory()
    return wo.append_controller_lane_pubkey_command(
        entry["user"], forced_command_key_line())


def main(argv):
    """``python3 cli_webterm_palo.py forced-command-install`` prints the go-live
    step-2 script on stdout (pipe it to the montalu6 account). Exit 2 on usage,
    1 when the key is not minted yet."""
    if argv != ["forced-command-install"]:
        sys.stderr.write("usage: cli_webterm_palo.py forced-command-install\n")
        return 2
    try:
        sys.stdout.write(render_forced_command_install())
    except ValueError as e:
        sys.stderr.write("cli_webterm_palo: %s\n" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
