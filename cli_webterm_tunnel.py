"""airuleset webterm — MANAGED cloudflared tunnel provisioning (#635).

The public HTTPS front for BOTH webterm lanes. Before #635 the tunnel was
UNMANAGED runtime state on both: the owner had none (a manual iptables NAT patch
fronted `:80` instead, which did not survive reboot and airuleset did not know
about) and david's was a HAND-MADE systemd unit airuleset never reconciled — the
exact "deployed ≠ managed, vanishes on re-provision" class this ticket exists to
kill. This leaf owns the shared render helpers + the OWNER provisioner; the david
lane's provisioner lives in cli_webterm_david.py (it reuses these renders).

Kept in its OWN module so cli_webterm.py stays under its size cap — the SAME
leaf-splitting the webterm code already uses (cli_webterm_david / _access /
_profiles / _gateway). The render helpers are PURE (no cli_webterm import); only
`setup_webterm_owner_tunnel` needs cli_webterm's owner-gateway ports, and it
imports cli_webterm LAZILY so there is no module-level cycle (cli_webterm's
setup_webterm_service imports THIS module lazily too).

The tunnel is created ONCE via dev2's origin cert (the only credential that can —
no API token on the box can create a Cloudflare Tunnel); ONLY the per-tunnel creds
JSON lives on the running box (cert.pem is never copied to dev1 — mirrors the
david lane's minimal footprint). The tunnel UUID is NOT a secret (it is the public
`<uuid>.cfargotunnel.com` DNS target) so it is a committed constant; the creds JSON
is the sole on-box secret, and provisioning is PREREQUISITE-GATED on its presence
(a safe no-op printing the go-live step otherwise).
"""
import shutil
import subprocess
import sys
from pathlib import Path

# #635 go-live: the OWNER's dedicated, airuleset-MANAGED cloudflared tunnel that
# fronts https://zbynek.newlevel.media/ -> the loopback owner gateway.
WEBTERM_OWNER_TUNNEL_UUID = "3f392bdc-c215-46f1-a2c7-3c1b1468969c"
WEBTERM_CLOUDFLARED_DIR = Path.home() / ".cloudflared"
WEBTERM_OWNER_TUNNEL_CREDS = WEBTERM_CLOUDFLARED_DIR / (WEBTERM_OWNER_TUNNEL_UUID + ".json")
# A DEDICATED config file (never dev1's default ~/.cloudflared/config.yml, which is
# the unrelated spinbike tunnel's) so the two tunnels never collide.
WEBTERM_OWNER_TUNNEL_CONFIG = WEBTERM_CLOUDFLARED_DIR / "webterm-owner.yml"
WEBTERM_OWNER_TUNNEL_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-owner-tunnel.service")
WEBTERM_OWNER_TUNNEL_HOSTNAME = "zbynek.newlevel.media"
# dev1 has cloudflared as a system binary on PATH; the fallback literal matches it.
WEBTERM_CLOUDFLARED_BIN = "/usr/local/bin/cloudflared"


def render_cloudflared_tunnel_config(tunnel_uuid, credentials_file, hostname,
                                     service_url):
    """A locally-managed cloudflared `config.yml` fronting ONE hostname onto a
    loopback origin. `credentials-file` (the per-tunnel secret JSON) is the only
    secret — referenced by absolute path, never inlined. The trailing
    `http_status:404` catch-all makes any unmatched host a 404 rather than leaking
    to the origin."""
    return (
        "# airuleset-managed cloudflared tunnel config (webterm, #635). Do NOT\n"
        "# hand-edit — regenerated + reconciled on every `airuleset.py install`.\n"
        "# Only the referenced per-tunnel credentials JSON is a secret (not in git).\n"
        "tunnel: %s\n"
        "credentials-file: %s\n"
        "\n"
        "ingress:\n"
        "  - hostname: %s\n"
        "    service: %s\n"
        "  - service: http_status:404\n"
        % (tunnel_uuid, credentials_file, hostname, service_url))


def render_cloudflared_tunnel_unit(description, config_path, cloudflared_bin,
                                   after="network-online.target"):
    """A systemd --user unit running `cloudflared tunnel --config <cfg> run`,
    byte-mirroring the proven webterm-david-tunnel.service: outbound-only (no open
    port, no sudo), `Restart=always`, `WantedBy=default.target` so linger makes it
    reboot-durable. `cloudflared_bin`/`config_path`/`after` are per-lane."""
    return (
        "# airuleset-managed cloudflared tunnel unit (webterm, #635). airuleset OWNS\n"
        "# + reconciles this on every install — it is NOT a hand-managed unit.\n"
        "[Unit]\n"
        "Description=%s\n"
        "Documentation=https://github.com/zbynekdrlik/airuleset\n"
        "After=%s\n"
        "Wants=network-online.target\n"
        "StartLimitIntervalSec=60\n"
        "StartLimitBurst=5\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=%s tunnel --no-autoupdate --config %s run\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
        % (description, after, cloudflared_bin, config_path))


def resolve_cloudflared_bin(fallback):
    """cloudflared path for the unit ExecStart: prefer whatever `shutil.which`
    finds (dev1: /usr/local/bin), else the given fallback literal."""
    return shutil.which("cloudflared") or fallback


def setup_webterm_owner_tunnel(run=None):
    """dev1-only (called from cli_webterm.setup_webterm_service's Access-mode
    branch): provision + enable the MANAGED cloudflared tunnel that fronts
    https://zbynek.newlevel.media/ -> the loopback owner gateway. PREREQUISITE-GATED
    on the per-tunnel creds JSON (a safe no-op printing the go-live step otherwise),
    so a fresh dev1 that lacks the creds never fails — it just does not stand the
    tunnel up. Idempotent. Returns True on success, False on any skip/failure (never
    raises — mirrors setup_webterm_service)."""
    run = run or subprocess.run
    import cli_webterm as w
    from cli_filedrop_watchdog import _run_systemctl, _whoami
    if not WEBTERM_OWNER_TUNNEL_CREDS.exists():
        print("  webterm: owner tunnel creds %s absent — tunnel NO-OP until go-live "
              "(create the `webterm-owner` tunnel via dev2's origin cert and copy its "
              "creds JSON here)." % WEBTERM_OWNER_TUNNEL_CREDS, file=sys.stderr)
        return False
    cloudflared_bin = resolve_cloudflared_bin(WEBTERM_CLOUDFLARED_BIN)
    if shutil.which("cloudflared") is None and not Path(WEBTERM_CLOUDFLARED_BIN).exists():
        print("  webterm: cloudflared binary not found — owner tunnel skipped "
              "(the gateway still serves loopback:%d for the tunnel to front)."
              % w.WEBTERM_GATEWAY_PORT, file=sys.stderr)
        return False
    try:
        WEBTERM_CLOUDFLARED_DIR.mkdir(parents=True, exist_ok=True)
        WEBTERM_OWNER_TUNNEL_CONFIG.write_text(
            render_cloudflared_tunnel_config(
                WEBTERM_OWNER_TUNNEL_UUID, str(WEBTERM_OWNER_TUNNEL_CREDS),
                WEBTERM_OWNER_TUNNEL_HOSTNAME,
                "http://%s:%d" % (w.WEBTERM_TTYD_BIND, w.WEBTERM_GATEWAY_PORT)),
            encoding="utf-8")
        WEBTERM_OWNER_TUNNEL_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
        WEBTERM_OWNER_TUNNEL_SERVICE_DEST.write_text(
            render_cloudflared_tunnel_unit(
                "owner webterm cloudflared tunnel (%s -> 127.0.0.1:%d)"
                % (WEBTERM_OWNER_TUNNEL_HOSTNAME, w.WEBTERM_GATEWAY_PORT),
                str(WEBTERM_OWNER_TUNNEL_CONFIG), cloudflared_bin,
                after="network-online.target webterm-gateway.service"),
            encoding="utf-8")
    except OSError as e:
        print("  webterm: owner tunnel write FAILED (%s) — left un-provisioned."
              % e, file=sys.stderr)
        return False
    try:
        run(["loginctl", "enable-linger", _whoami()], capture_output=True, text=True)
    except Exception as e:
        print("  webterm: owner tunnel enable-linger skipped (%s)" % e, file=sys.stderr)
    _run_systemctl(["daemon-reload"])
    rc, _o, err = _run_systemctl(["enable", "--now", "webterm-owner-tunnel.service"])
    if rc != 0:
        print("  webterm: enable owner tunnel FAILED: %s" % (err or "").strip(),
              file=sys.stderr)
        return False
    # `enable --now` no-ops an already-running unit, so a re-install that changed the
    # config/unit needs an explicit restart to take effect.
    _run_systemctl(["restart", "webterm-owner-tunnel.service"])
    print("  webterm: owner tunnel live + MANAGED (%s -> 127.0.0.1:%d)."
          % (WEBTERM_OWNER_TUNNEL_HOSTNAME, w.WEBTERM_GATEWAY_PORT))
    return True
