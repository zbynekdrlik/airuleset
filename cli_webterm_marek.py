"""airuleset webterm — MAREK developer gateway provisioning (#612 scope-add
2026-08-24).

The THIRD per-developer gateway, for `marek.newlevel.media`, provisioned on the
SAME subdev VPS as david — but as the `marek` unix account, with a SEPARATE
scoped inventory + Cloudflare Access realm + cloudflared tunnel. Kept in its OWN
module (mirroring cli_webterm_david.py) so cli_webterm.py stays under its size cap
and the owner (dev1) + david paths are entirely untouched — this module only adds
the marek deployment.

Stronger isolation than david by design (see the #612 marek design comment): the
marek gateway runs AS marek on subdev and attaches his LOCAL tmux group, so its
scoped inventory is a SINGLE LOCAL entry — NO ssh, NO dedicated key. marek's ttyd
child therefore has ZERO ssh capability: it can reach nothing but the local marek
session. The connect allowlist can never resolve an owner-fleet id OR a david id.

PREREQUISITE-GATED so a normal subdev install (as any other account) is a safe
NO-OP: it provisions only when running as the `marek` account with ttyd installed;
otherwise it prints the go-live steps and returns False, touching no systemd. The
gateway binds LOOPBACK — a SEPARATE cloudflared tunnel is the public HTTPS front
(no public port, no sudo). AUTH is Cloudflare Access (email one-time-PIN) at the
edge; the gateway runs in `--trust-access-header` mode (no password/credential),
exactly like the david lane.

Imports cli_webterm for the shared render/template helpers; the dispatch in
cli_webterm.maybe_setup_webterm imports THIS module lazily, so there is no
module-level import cycle.

SECURITY NOTE — the boundary this lane DOES and does NOT provide (honest, #612
R1 review, mirrors cli_webterm_access.py's own note). What it provides: the
PUBLIC Access-gated path reaches ONLY marek's scoped session — the connect
allowlist is physically `{marek-subdev}` (a single LOCAL attach, no ssh/key), so
a marek WEB LOGIN can never drive an owner-fleet or david id, and marek's Access
realm/tunnel are separate from every other developer's. What it does NOT close:
the marek gateway/ttyd bind LOOPBACK on the SHARED subdev box, where 127.0.0.1 is
reachable by every local unix account (and pure stdlib cannot verify the Access
JWT signature at the origin — the same residual cli_webterm_access.py documents).
So a marek UNIX SHELL — which the webterm legitimately gives marek to his OWN
account, and which marek already has independently of webterm — can `curl` david's
loopback ttyd (127.0.0.1:<port>, itself auth-less by design) and vice-versa. This
is the PRE-EXISTING multi-tenant-subdev floor the david lane shipped with and the
#612 R2 review accepted. Directionally (honest, #612 R2 review): this lane adds NO
new reachability INTO owner/david (marek→david already held via david's live
auth-less loopback ttyd, marek having his own independent subdev shell); it DOES
newly expose marek's OWN account — any local subdev account gains a marek shell via
marek's NEW loopback ttyd 7684 / header-forgeable gateway 8082, reachability that
did not exist before (marek's account is keyed on the operator ssh identity, not a
shared password). The party newly at risk is the lane's own tenant, and the class
is the same accepted floor. Closing that floor (a mode-0600 unix-domain-socket origin per
gateway user, or origin JWT verification) is CROSS-CUTTING — it must cover the
LIVE owner + david gateways too — so it is the separate hardening ticket #663, not
this scoped marek lane.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cli_webterm as w
import cli_webterm_access as access
import cli_webterm_profiles as profiles
import cli_webterm_tunnel as tun     # shared managed-tunnel render helpers (#635)
import cli_binary_installers as binstall  # #614: ttyd static-binary auto-install

# The gateway login account on subdev (marek's own account); re-exported from the
# profiles leaf so tests + the go-live message have one source.
MAREK_GATEWAY_USER = profiles.MAREK_GATEWAY_USER

# marek's own artifact paths + loopback ports — DISTINCT from owner (8080/7682)
# AND david (8081/7683), so the shared subdev box is self-documenting. The gateway
# binds loopback and a cloudflared tunnel fronts it (no public port, no tailscale
# IP is involved).
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

# marek's SEPARATE cloudflared tunnel (created via dev2's origin cert, #612
# go-live) — NOT david's. A separate tunnel = separate creds JSON + unit + restart
# blast-radius, so a reconcile/restart of marek's front never disturbs david's
# live terminal, and a compromise of marek's creds fronts ONLY marek's origin (the
# #612 marek design, Approach 1). The UUID is public (the DNS CNAME target); the
# per-tunnel creds JSON on subdev is the only secret, and the tunnel provisioner
# is prerequisite-gated on it.
WEBTERM_MAREK_TUNNEL_UUID = "1e9555d1-4d19-4e86-8064-361506fbc2cd"
WEBTERM_MAREK_TUNNEL_CREDS = tun.WEBTERM_CLOUDFLARED_DIR / (
    WEBTERM_MAREK_TUNNEL_UUID + ".json")
# A DEDICATED config file (never the box-default ~/.cloudflared/config.yml, which
# is DAVID's tunnel config on subdev) so the two per-developer tunnels never
# collide — the same isolation the owner tunnel uses (webterm-owner.yml).
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
    % (WEBTERM_MAREK_TUNNEL_UUID, WEBTERM_MAREK_TUNNEL_UUID))


# Prepended to each rendered marek unit so a human reading the installed file on
# subdev is never misled by the shared template's "dev1 / tailscale / password"
# wording — the marek gateway is a DIFFERENT deployment (loopback + cloudflared +
# Cloudflare Access), on the marek account.
_MAREK_UNIT_NOTE = (
    "# NOTE (#612): this is the MAREK developer gateway on SUBDEV (marek account)\n"
    "# — it binds LOOPBACK (127.0.0.1) and is fronted by a SEPARATE cloudflared\n"
    "# public HTTPS tunnel for marek.newlevel.media. The 'dev1 / tailscale IP'\n"
    "# wording, the ttyd port 7682 and the 'webterm-gateway.service' name inherited\n"
    "# from the shared template below refer to the OWNER deployment — the MAREK\n"
    "# ports are ttyd 7684 / gateway 8082 and the units are webterm-marek-*.service.\n"
    "# Scoped inventory: the LOCAL marek tmux session ONLY (never the owner fleet,\n"
    "# never david's accounts) — a local attach, no ssh, no key.\n"
    "#\n"
    "# AUTH (#612 owner directive 2026-08-22): NO password / credential. Cloudflare\n"
    "# Access does email one-time-PIN verification at the EDGE before any request\n"
    "# reaches the tunnel; the gateway runs in --trust-access-header mode and just\n"
    "# trusts the Cf-Access-Authenticated-User-Email header Cloudflare injects.\n"
    "# The shared template's 'form login / credential / constant-time compare'\n"
    "# wording refers to the OWNER (password) deployment — it does NOT apply here.\n"
    "#\n"
    "# EXPOSURE: unlike the OWNER unit, the shared template's 'tailnet-only entry\n"
    "# point' / 'security boundary is tailnet-only exposure' claims below are FALSE\n"
    "# for THIS gateway — it is PUBLIC (Cloudflare Access at the edge is the\n"
    "# boundary). And 'failed logins rate-limited per source IP' does NOT hold at\n"
    "# the origin: behind cloudflared the gateway sees only 127.0.0.1, so any\n"
    "# per-real-IP brute-force protection lives on the Cloudflare EDGE, not here.\n"
    "# (The template's 'bound to dev1's tailscale IP (127.0.0.1)' line is doubly\n"
    "# wrong here: this gateway binds LOOPBACK on subdev, not a tailscale IP.)\n#\n")


# The marek ttyd binary is a NO-SUDO user-space static binary in ~/.local/bin
# (subdev accounts have no sudo), which is NOT on the systemd --user manager's
# default PATH — and `daemon-reexec` does not re-read ~/.config/environment.d.
# So the MAREK unit carries its OWN PATH, making the launcher's bare `exec ttyd`
# resolve on a clean manager start (reboot / fresh re-provision) WITHOUT a
# hand-placed .d/ drop-in (the #614 lesson, applied to the marek lane from day 1
# so it never needs a hand-placed drop-in at all). Byte-identical to david's PATH;
# `%h` is the systemd home specifier. Scoped to the MAREK render ONLY.
#
# FOOTGUN (the #614/#638 invariant): airuleset renders this MAIN unit and NEVER
# writes/deletes/scans `.d/` drop-ins. If you ever CHANGE the PATH below, verify no
# stale hand-placed drop-in remains (it would silently OVERRIDE it — drop-ins load
# last) with `systemctl --user show webterm-marek-ttyd.service -p DropInPaths`.
_MAREK_TTYD_PATH_ENV = (
    "# #614: self-contained PATH so the launcher's bare `exec ttyd` resolves\n"
    "# the no-sudo ~/.local/bin user-space static binary on a clean systemd\n"
    "# --user manager start (subdev has no sudo; the manager default PATH\n"
    "# excludes ~/.local/bin).\n"
    "Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin\n")


def render_marek_ttyd_unit():
    tmpl = w.WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return _MAREK_UNIT_NOTE + (
        tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_MAREK_LAUNCH_PATH))
        .replace("[Service]\n", "[Service]\n" + _MAREK_TTYD_PATH_ENV)
        .replace("(dev1-only)", "(subdev marek)"))


def render_marek_gateway_unit():
    """The marek gateway unit: LOOPBACK bind (cloudflared fronts it), marek
    dash/ports, `After=` repointed to the marek ttyd unit — and Cloudflare-ACCESS
    mode instead of a password: the ExecStart's `--cred {{CRED_PATH}}` is swapped
    for `--trust-access-header <header>` so NO credential file is validated. The
    remaining `{{CRED_PATH}}` token lives only in the shared template's
    password-model COMMENT — neutralised to n/a here."""
    tmpl = w.WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    # ExecStart transforms FIRST (before the {{TOKEN}} substitutions) so the flag
    # substrings still carry their literal tokens: password --cred -> Access header
    # trust; #663 TCP bind/ttyd -> UNIX sockets in the account runtime dir.
    execstart = (
        tmpl.replace("--cred {{CRED_PATH}}",
                     "--trust-access-header " + access.WEBTERM_ACCESS_TRUST_HEADER)
            .replace("--bind {{BIND_IP}} --port {{GATEWAY_PORT}}",
                     "--socket %t/" + WEBTERM_MAREK_GATEWAY_SOCK_BASENAME)
            .replace("--ttyd-host 127.0.0.1 --ttyd-port {{TTYD_PORT}}",
                     "--ttyd-socket %t/" + WEBTERM_MAREK_TTYD_SOCK_BASENAME))
    return _MAREK_UNIT_NOTE + (
        execstart
            .replace("{{BIND_IP}}", WEBTERM_MAREK_BIND)
            .replace("{{GATEWAY_MODULE}}", str(w.WEBTERM_GATEWAY_MODULE))
            .replace("{{GATEWAY_PORT}}", str(WEBTERM_MAREK_GATEWAY_PORT))
            .replace("{{DASH_INDEX}}", str(WEBTERM_MAREK_DASH_INDEX))
            .replace("{{CRED_PATH}}", "n/a (Cloudflare Access — no credential)")
            .replace("{{TTYD_PORT}}", str(WEBTERM_MAREK_TTYD_PORT))
            .replace("{{TTYD_BASE}}", w.WEBTERM_TTYD_BASE)
            .replace("webterm-ttyd.service", "webterm-marek-ttyd.service")
            .replace("(dev1-only)", "(subdev marek)"))


def _write_marek_artifacts():
    """Write the scoped inventory + dashboard + launcher + units. NO credential is
    provisioned (Cloudflare Access replaces the password) and NO ssh key is used
    (the single session is a LOCAL attach). Pure filesystem writes (no systemd),
    split out so the render/write path is unit-testable without the enable/restart
    plumbing."""
    w.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    WEBTERM_MAREK_DASH_DIR.mkdir(parents=True, exist_ok=True)
    inv = w.webterm_inventory(profile=profiles.MAREK)
    WEBTERM_MAREK_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # The marek gateway renders its OWN physically-scoped inventory (a single
    # LOCAL entry), so it does NOT consume the #661 WEBTERM_DASHBOARD_TABS policy
    # (human=None → unfiltered), exactly like the david gateway.
    WEBTERM_MAREK_DASH_INDEX.write_text(
        w.render_dashboard_html(inv, ttyd_base=w.WEBTERM_TTYD_BASE),
        encoding="utf-8")
    # marek's own installable-PWA assets (manifest name "Webterm marek", #644
    # per-domain identity) next to his index.html.
    import cli_webterm_pwa
    cli_webterm_pwa.write_pwa_assets(WEBTERM_MAREK_DASH_DIR, profiles.MAREK)
    WEBTERM_MAREK_LAUNCH_PATH.write_text(
        w.render_webterm_launch_script(
            inventory_path=WEBTERM_MAREK_INVENTORY_PATH,
            ttyd_socket_basename=WEBTERM_MAREK_TTYD_SOCK_BASENAME),  # #663
        encoding="utf-8")
    os.chmod(WEBTERM_MAREK_LAUNCH_PATH, 0o755)
    WEBTERM_MAREK_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_MAREK_SERVICE_DEST.write_text(render_marek_ttyd_unit(),
                                          encoding="utf-8")
    WEBTERM_MAREK_GATEWAY_SERVICE_DEST.write_text(render_marek_gateway_unit(),
                                                  encoding="utf-8")


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision: running as
    the marek gateway account with ttyd installed. NO dedicated key is required
    (the single session is a LOCAL attach). Every False is a SAFE no-op reason,
    never a failure."""
    from cli_filedrop_watchdog import _whoami
    who = _whoami()
    if who != MAREK_GATEWAY_USER:
        return False, ("install runs as %r, not the gateway account %r"
                       % (who, MAREK_GATEWAY_USER))
    # ttyd on subdev is a no-sudo user-space static binary in ~/.local/bin, which
    # the install PROCESS's PATH (a push-driven NON-login ssh shell as marek) does
    # NOT include — so `shutil.which("ttyd")` alone would no-op this gate and the
    # box would never re-provision (#614). Accept the explicit ~/.local/bin/ttyd
    # location too (an executable file).
    local_ttyd = Path.home() / ".local" / "bin" / "ttyd"
    have_ttyd = (shutil.which("ttyd") is not None
                 or (local_ttyd.is_file() and os.access(local_ttyd, os.X_OK)))
    if not have_ttyd:
        return False, "prerequisites missing (ttyd: %s)" % have_ttyd
    return True, "ready"


def setup_webterm_marek_tunnel(run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://marek.newlevel.media/ -> the loopback marek gateway. Renders the
    marek-specific config/unit, then hands off to the SHARED
    `tun._provision_managed_tunnel` (the same never-raises, prereq-gated,
    linger+enable+restart orchestration the owner + david lanes use — so the lanes
    cannot drift). Uses a DEDICATED config file (webterm-marek.yml), so it never
    collides with david's box-default config.yml on the shared subdev box. Returns
    its result."""
    local_bin = Path.home() / ".local" / "bin" / "cloudflared"
    cloudflared_bin = (str(local_bin) if local_bin.is_file()
                       else (shutil.which("cloudflared")
                             or WEBTERM_MAREK_CLOUDFLARED_BIN))
    # #663: front the gateway's mode-0700 UNIX socket, not a TCP loopback port.
    gw_sock = w.webterm_runtime_socket_abs(WEBTERM_MAREK_GATEWAY_SOCK_BASENAME)
    config_text = tun.render_cloudflared_tunnel_config(
        WEBTERM_MAREK_TUNNEL_UUID, str(WEBTERM_MAREK_TUNNEL_CREDS),
        WEBTERM_MAREK_TUNNEL_HOSTNAME, "unix:" + gw_sock)
    unit_text = tun.render_cloudflared_tunnel_unit(
        "marek webterm cloudflared tunnel (%s -> unix:%s)"
        % (WEBTERM_MAREK_TUNNEL_HOSTNAME, gw_sock),
        str(WEBTERM_MAREK_TUNNEL_CONFIG), cloudflared_bin,
        after="network-online.target webterm-marek-gateway.service")
    return tun._provision_managed_tunnel(
        WEBTERM_MAREK_TUNNEL_CREDS, cloudflared_bin, WEBTERM_MAREK_TUNNEL_CONFIG,
        config_text, WEBTERM_MAREK_TUNNEL_SERVICE_DEST,
        "webterm-marek-tunnel.service", unit_text, run=run, lane="(marek)",
        creds_absent_hint=" (the webterm-marek-612 creds JSON must be present "
        "on subdev — transfer it from dev2 ~/.cloudflared/%s.json)."
        % WEBTERM_MAREK_TUNNEL_UUID)


def setup_webterm_marek_service(run=None):
    """subdev-only: provision the marek developer gateway (#612 scope-add).
    Prerequisite-gated (see `prerequisites_ready`) so an ordinary subdev install
    (any other account) can never break — a not-ready box prints the go-live steps
    and returns False, touching no systemd. Idempotent. Returns True on success,
    False on any skip/failure (never raises)."""
    run = run or subprocess.run
    # #614: auto-install the ttyd BINARY into ~/.local/bin BEFORE the prerequisite
    # gate checks for it — the gate REQUIRES ttyd present, so a fresh subdev
    # re-provision (ttyd absent) would otherwise no-op provisioning forever.
    # Best-effort/non-fatal, exactly how cmd_install calls the ffmpeg/claude
    # installers; a harmless no-op wherever ttyd already resolves.
    try:
        binstall.ensure_ttyd_static_binary()
    except Exception as e:
        print("  webterm(marek): ttyd static install error (non-fatal): %r" % e,
              file=sys.stderr)
    try:
        ok, reason = prerequisites_ready()
    except Exception as e:  # a gate that itself errors is a SAFE no-op, never a raise
        print("  webterm(marek): prerequisite check errored (%r) — no-op."
              % e, file=sys.stderr)
        return False
    if not ok:
        print("  webterm(marek): %s — no-op until go-live setup.\n%s"
              % (reason, _MAREK_GO_LIVE), file=sys.stderr)
        return False

    # Post-gate body wrapped so a write/systemd failure is a logged False, never a
    # raise into cmd_install (the "never raises" contract).
    try:
        from cli_filedrop_watchdog import _run_systemctl, _whoami
        _write_marek_artifacts()

        try:
            run(["loginctl", "enable-linger", _whoami()], capture_output=True,
                text=True)
        except Exception as e:
            print("  webterm(marek): loginctl enable-linger skipped (%s)" % e,
                  file=sys.stderr)

        rc, _o, err = _run_systemctl(["daemon-reload"])
        if rc != 0:
            print("  webterm(marek): daemon-reload FAILED: %s"
                  % (err or "").strip(), file=sys.stderr)
        ok_all = True
        for svc in ("webterm-marek-ttyd.service", "webterm-marek-gateway.service"):
            rc, _o, err = _run_systemctl(["enable", "--now", svc])
            if rc != 0:
                print("  webterm(marek): enable --now %s FAILED: %s"
                      % (svc, (err or "").strip()), file=sys.stderr)
                ok_all = False
                continue
            _run_systemctl(["restart", svc])
        # Bring the public HTTPS front up too — but ONLY once the loopback
        # gateway/ttyd came up (ok_all), so the tunnel never fronts a dead origin.
        # Prereq-gated no-op if the creds JSON is not present.
        if ok_all:
            setup_webterm_marek_tunnel(run=run)
    except Exception as e:
        print("  webterm(marek): provisioning errored (%r) — left un-provisioned."
              % e, file=sys.stderr)
        return False
    if ok_all:
        print("  webterm(marek): gateway live on 127.0.0.1:%d (loopback — front "
              "with cloudflared). ttyd loopback 127.0.0.1:%d behind /t.\n%s"
              % (WEBTERM_MAREK_GATEWAY_PORT, WEBTERM_MAREK_TTYD_PORT,
                 _MAREK_GO_LIVE))
    return ok_all
