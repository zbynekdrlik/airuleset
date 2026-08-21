"""airuleset webterm — DAVID developer gateway provisioning (#612).

The public per-developer gateway for `david.newlevel.media`, provisioned on the
subdev VPS. Kept in its OWN module so cli_webterm.py stays under its size cap and
the owner (dev1) provisioning path is entirely untouched — this module only adds
the david deployment, it never re-defines an owner path.

PREREQUISITE-GATED so a normal subdev install is a safe NO-OP until the owner
completes go-live setup: it provisions only when running as the designated
gateway account (david1) with the dedicated key present and ttyd installed;
otherwise it prints the exact needs-owner-action steps and returns False,
touching no systemd. The gateway binds LOOPBACK — a cloudflared tunnel is the
public HTTPS front (no public port, no sudo). Its scoped inventory (via the ttyd
launcher's `--inventory`) is david1..4 + codex-bridge ONLY, so the connect
allowlist can never resolve an owner-fleet id.

Imports cli_webterm for the shared render/credential helpers + templates; the
dispatch in cli_webterm.maybe_setup_webterm imports THIS module lazily, so there
is no module-level import cycle.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cli_webterm as w
import cli_webterm_profiles as profiles

# The david deployment's own artifact paths + loopback ports (distinct from the
# owner's, so the subdev box is self-documenting; the gateway binds loopback and
# cloudflared fronts it, so no public port and no tailscale-IP is involved).
WEBTERM_DAVID_BIND = "127.0.0.1"
WEBTERM_DAVID_TTYD_PORT = 7683
WEBTERM_DAVID_GATEWAY_PORT = 8081
WEBTERM_DAVID_INVENTORY_PATH = w.CLAUDE_DIR / "webterm-david-inventory.json"
WEBTERM_DAVID_DASH_DIR = w.CLAUDE_DIR / "webterm-david-dash"
WEBTERM_DAVID_DASH_INDEX = WEBTERM_DAVID_DASH_DIR / "index.html"
WEBTERM_DAVID_LAUNCH_PATH = w.CLAUDE_DIR / "airuleset-webterm-david-ttyd.sh"
WEBTERM_DAVID_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-david-ttyd.service")
WEBTERM_DAVID_GATEWAY_SERVICE_DEST = (
    Path.home() / ".config" / "systemd" / "user" / "webterm-david-gateway.service")

_DAVID_GO_LIVE = (
    "  webterm(david): needs-owner-action to go live —\n"
    "    1. DNS (Cloudflare): david.newlevel.media -> cloudflared tunnel (or a\n"
    "       proxied A -> subdev 116.203.108.177); zbynek.newlevel.media A ->\n"
    "       100.104.8.125 (DNS-only/grey, tailnet-only).\n"
    "    2. Install ttyd on subdev + run a cloudflared tunnel (as david1, no\n"
    "       sudo) fronting HTTPS david.newlevel.media -> 127.0.0.1:%d.\n"
    "    3. Deploy the dedicated key %s (authorized ONLY on david1-4) and\n"
    "       deliver the david credential (%s) via `secret show`.\n"
    % (WEBTERM_DAVID_GATEWAY_PORT, profiles.WEBTERM_DAVID_IDENTITY,
       w.WEBTERM_DAVID_CRED_PATH))


# Prepended to each rendered david unit so a human reading the installed file on
# subdev is never misled by the shared template's "dev1 / tailscale" wording —
# the david gateway is a DIFFERENT deployment (loopback + cloudflared).
_DAVID_UNIT_NOTE = (
    "# NOTE (#612): this is the DAVID developer gateway on SUBDEV — it binds\n"
    "# LOOPBACK (127.0.0.1) and is fronted by a cloudflared public HTTPS tunnel\n"
    "# for david.newlevel.media. The 'dev1 / tailscale IP' wording inherited from\n"
    "# the shared template below does NOT apply here. Scoped inventory: david1-4\n"
    "# + codex-bridge only (never the owner fleet). Regenerate via install.\n#\n")


def render_david_ttyd_unit():
    tmpl = w.WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return _DAVID_UNIT_NOTE + (
        tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_DAVID_LAUNCH_PATH))
        .replace("(dev1-only)", "(subdev david)"))


def render_david_gateway_unit():
    """The david gateway unit: LOOPBACK bind (cloudflared fronts it), david
    dash/cred/ports, and its `After=` repointed to the david ttyd unit (the
    shared template hardcodes the owner ttyd service name)."""
    tmpl = w.WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return _DAVID_UNIT_NOTE + (
        tmpl.replace("{{BIND_IP}}", WEBTERM_DAVID_BIND)
            .replace("{{GATEWAY_MODULE}}", str(w.WEBTERM_GATEWAY_MODULE))
            .replace("{{GATEWAY_PORT}}", str(WEBTERM_DAVID_GATEWAY_PORT))
            .replace("{{DASH_INDEX}}", str(WEBTERM_DAVID_DASH_INDEX))
            .replace("{{CRED_PATH}}", str(w.WEBTERM_DAVID_CRED_PATH))
            .replace("{{TTYD_PORT}}", str(WEBTERM_DAVID_TTYD_PORT))
            .replace("{{TTYD_BASE}}", w.WEBTERM_TTYD_BASE)
            .replace("webterm-ttyd.service", "webterm-david-ttyd.service")
            .replace("(dev1-only)", "(subdev david)"))


def _write_david_artifacts():
    """Write the scoped inventory + dashboard + launcher + credential + units.
    Pure filesystem writes (no systemd), split out so the render/write path is
    unit-testable without the enable/restart plumbing."""
    w.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    WEBTERM_DAVID_DASH_DIR.mkdir(parents=True, exist_ok=True)
    inv = w.webterm_inventory(profile=profiles.DAVID)
    WEBTERM_DAVID_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    WEBTERM_DAVID_DASH_INDEX.write_text(
        w.render_dashboard_html(inv, ttyd_base=w.WEBTERM_TTYD_BASE),
        encoding="utf-8")
    w._ensure_credential(profile=profiles.DAVID)
    WEBTERM_DAVID_LAUNCH_PATH.write_text(
        w.render_webterm_launch_script(
            inventory_path=WEBTERM_DAVID_INVENTORY_PATH,
            ttyd_port=WEBTERM_DAVID_TTYD_PORT),
        encoding="utf-8")
    os.chmod(WEBTERM_DAVID_LAUNCH_PATH, 0o755)
    WEBTERM_DAVID_SERVICE_DEST.parent.mkdir(parents=True, exist_ok=True)
    WEBTERM_DAVID_SERVICE_DEST.write_text(render_david_ttyd_unit(),
                                          encoding="utf-8")
    WEBTERM_DAVID_GATEWAY_SERVICE_DEST.write_text(render_david_gateway_unit(),
                                                  encoding="utf-8")


def prerequisites_ready():
    """(ok, reason) — True only when this box may actually provision: running as
    the designated gateway account (david1) with the dedicated key present and
    ttyd installed. Every False is a SAFE no-op reason, never a failure."""
    from cli_filedrop_watchdog import _whoami
    who = _whoami()
    if who != profiles.DAVID_GATEWAY_USER:
        return False, ("install runs as %r, not the gateway account %r"
                       % (who, profiles.DAVID_GATEWAY_USER))
    key = Path(os.path.expanduser(profiles.WEBTERM_DAVID_IDENTITY))
    have_ttyd = shutil.which("ttyd") is not None
    if not key.exists() or not have_ttyd:
        return False, ("prerequisites missing (dedicated key present: %s; "
                       "ttyd: %s)" % (key.exists(), have_ttyd))
    return True, "ready"


def setup_webterm_david_service(run=None):
    """subdev-only: provision the david developer gateway (#612). Prerequisite-
    gated (see `prerequisites_ready`) so an ordinary subdev install can never
    break — a not-ready box prints the go-live steps and returns False, touching
    no systemd. Idempotent. Returns True on success, False on any skip/failure
    (never raises)."""
    run = run or subprocess.run
    ok, reason = prerequisites_ready()
    if not ok:
        print("  webterm(david): %s — no-op until go-live setup.\n%s"
              % (reason, _DAVID_GO_LIVE), file=sys.stderr)
        return False

    from cli_filedrop_watchdog import _run_systemctl, _whoami
    _write_david_artifacts()

    try:
        run(["loginctl", "enable-linger", _whoami()], capture_output=True,
            text=True)
    except Exception as e:
        print("  webterm(david): loginctl enable-linger skipped (%s)" % e,
              file=sys.stderr)

    rc, _o, err = _run_systemctl(["daemon-reload"])
    if rc != 0:
        print("  webterm(david): daemon-reload FAILED: %s" % (err or "").strip(),
              file=sys.stderr)
    ok_all = True
    for svc in ("webterm-david-ttyd.service", "webterm-david-gateway.service"):
        rc, _o, err = _run_systemctl(["enable", "--now", svc])
        if rc != 0:
            print("  webterm(david): enable --now %s FAILED: %s"
                  % (svc, (err or "").strip()), file=sys.stderr)
            ok_all = False
            continue
        _run_systemctl(["restart", svc])
    if ok_all:
        print("  webterm(david): gateway live on 127.0.0.1:%d (loopback — front "
              "with cloudflared). ttyd loopback 127.0.0.1:%d behind /t.\n%s"
              % (WEBTERM_DAVID_GATEWAY_PORT, WEBTERM_DAVID_TTYD_PORT,
                 _DAVID_GO_LIVE))
    return ok_all
