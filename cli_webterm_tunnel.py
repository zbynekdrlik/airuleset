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
    finds (dev1: /usr/local/bin), else the given fallback literal (subdev's no-sudo
    ~/.local/bin binary, which is NOT on the install process PATH)."""
    return shutil.which("cloudflared") or fallback


def _provision_managed_tunnel(creds_path, cloudflared_bin, config_path, config_text,
                              unit_path, service_name, unit_text, run=None,
                              lane="", creds_absent_hint=""):
    """SHARED orchestration for a managed cloudflared tunnel — owner (dev1) AND
    david (subdev) both call this, so the two lanes can NEVER drift (the review
    found the copy-paste twin was the source of several parity gaps). The CALLER
    renders `config_text`/`unit_text` (each lane resolves its own cloudflared_bin +
    ports + `After=`); this owns the identical write + linger + systemd enable/restart.

    PREREQUISITE-GATED on `creds_path` (the per-tunnel secret JSON) — a safe no-op
    printing the go-live step otherwise; the whole body is try-wrapped so it NEVER
    raises (returns False on any skip/failure). Idempotent (`enable --now` no-ops a
    running unit, so an explicit `restart` applies a changed config/unit). Returns
    True only when the unit is written + enabled + restarted."""
    run = run or subprocess.run
    try:
        from cli_filedrop_watchdog import _run_systemctl, _whoami
        if not creds_path.exists():
            print("  webterm%s: tunnel creds %s absent — tunnel NO-OP until go-live.%s"
                  % (lane, creds_path, creds_absent_hint), file=sys.stderr)
            return False
        if shutil.which("cloudflared") is None and not Path(cloudflared_bin).exists():
            print("  webterm%s: cloudflared binary not found — tunnel skipped (the "
                  "loopback gateway still serves for the tunnel to front)." % lane,
                  file=sys.stderr)
            return False
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_text, encoding="utf-8")
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_text, encoding="utf-8")
        # linger makes the --user unit reboot-durable (the whole point of #635) even
        # when this provisioner is reached standalone, not only via a caller that
        # already lingered.
        try:
            run(["loginctl", "enable-linger", _whoami()], capture_output=True, text=True)
        except Exception as e:
            print("  webterm%s: tunnel enable-linger skipped (%s)" % (lane, e),
                  file=sys.stderr)
        _run_systemctl(["daemon-reload"])
        rc, _o, err = _run_systemctl(["enable", "--now", service_name])
        if rc != 0:
            print("  webterm%s: enable %s FAILED: %s"
                  % (lane, service_name, (err or "").strip()), file=sys.stderr)
            return False
        _run_systemctl(["restart", service_name])
    except Exception as e:
        print("  webterm%s: tunnel provisioning errored (%r) — left un-provisioned."
              % (lane, e), file=sys.stderr)
        return False
    print("  webterm%s: tunnel live + MANAGED (%s)." % (lane, service_name),
          file=sys.stderr)
    return True


def setup_webterm_owner_tunnel(run=None):
    """dev1-only (called from cli_webterm.setup_webterm_service's Access-mode
    branch): provision + enable the MANAGED cloudflared tunnel that fronts
    https://zbynek.newlevel.media/ -> the loopback owner gateway. Renders the
    owner-specific config/unit, then hands off to `_provision_managed_tunnel` (the
    shared, never-raises, prereq-gated orchestration). Returns its result."""
    import cli_webterm as w
    cloudflared_bin = resolve_cloudflared_bin(WEBTERM_CLOUDFLARED_BIN)
    config_text = render_cloudflared_tunnel_config(
        WEBTERM_OWNER_TUNNEL_UUID, str(WEBTERM_OWNER_TUNNEL_CREDS),
        WEBTERM_OWNER_TUNNEL_HOSTNAME,
        "http://%s:%d" % (w.WEBTERM_TTYD_BIND, w.WEBTERM_GATEWAY_PORT))
    unit_text = render_cloudflared_tunnel_unit(
        "owner webterm cloudflared tunnel (%s -> 127.0.0.1:%d)"
        % (WEBTERM_OWNER_TUNNEL_HOSTNAME, w.WEBTERM_GATEWAY_PORT),
        str(WEBTERM_OWNER_TUNNEL_CONFIG), cloudflared_bin,
        after="network-online.target webterm-gateway.service")
    return _provision_managed_tunnel(
        WEBTERM_OWNER_TUNNEL_CREDS, cloudflared_bin, WEBTERM_OWNER_TUNNEL_CONFIG,
        config_text, WEBTERM_OWNER_TUNNEL_SERVICE_DEST,
        "webterm-owner-tunnel.service", unit_text, run=run, lane="",
        creds_absent_hint=" (create the `webterm-owner` tunnel via dev2's origin "
        "cert and copy its creds JSON here).")
