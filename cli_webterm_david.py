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
public HTTPS front (no public port, no sudo). Its scoped inventory — handed to
the ttyd child via the `WEBTERM_INVENTORY` env var the launcher exports (NOT a
client-injectable argv flag: ttyd's `-a` appends client `?arg=` values as argv,
so an argv flag would be injectable — #612 review) — is david1..4 + codex-bridge
ONLY, so the connect allowlist can never resolve an owner-fleet id.

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
import cli_webterm_access as access
import cli_webterm_profiles as profiles
import cli_webterm_tunnel as tun  # #635: shared managed-tunnel render helpers

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

# #635: bring david's cloudflared tunnel under airuleset management (it was a
# HAND-MADE unit — its own header said "Hand-managed on subdev … needs-owner-
# action" — the exact "deployed ≠ managed, vanishes on re-provision" class #635
# exists to kill). The tunnel already exists (created via dev2's origin cert in
# #612); airuleset now WRITES + reconciles its config + systemd --user unit,
# functionally identical to the working hand-made one. The UUID is public (the
# DNS CNAME target); the per-tunnel creds JSON on subdev is the only secret, and
# setup_webterm_david_tunnel is prerequisite-gated on it.
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
    "       sudo) fronting HTTPS david.newlevel.media -> 127.0.0.1:%d.\n"
    "    3. Deploy the dedicated key %s (authorized ONLY on david1-4).\n"
    "    4. AUTH (#612 owner directive): NO password. Put a Cloudflare Access\n"
    "       email-OTP app in front — set WEBTERM_ACCESS_APPS['david'] allow-list\n"
    "       and run `airuleset.py webterm-access --apply`. Adding a person\n"
    "       (marek) is one more e-mail in that list. No credential is delivered.\n"
    % (WEBTERM_DAVID_GATEWAY_PORT, profiles.WEBTERM_DAVID_IDENTITY))


# Prepended to each rendered david unit so a human reading the installed file on
# subdev is never misled by the shared template's "dev1 / tailscale" wording —
# the david gateway is a DIFFERENT deployment (loopback + cloudflared).
_DAVID_UNIT_NOTE = (
    "# NOTE (#612): this is the DAVID developer gateway on SUBDEV — it binds\n"
    "# LOOPBACK (127.0.0.1) and is fronted by a cloudflared public HTTPS tunnel\n"
    "# for david.newlevel.media. The 'dev1 / tailscale IP' wording, the ttyd port\n"
    "# 7682 and the 'webterm-gateway.service' name inherited from the shared\n"
    "# template below refer to the OWNER deployment — the DAVID ports are ttyd\n"
    "# 7683 / gateway 8081 and the units are webterm-david-*.service. Scoped\n"
    "# inventory: david1-4 + codex-bridge only (never the owner fleet).\n"
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


# The david ttyd binary is a NO-SUDO user-space static binary in ~/.local/bin
# (subdev accounts have no sudo), which is NOT on the systemd --user manager's
# default PATH — and `daemon-reexec` does not re-read ~/.config/environment.d.
# So the DAVID unit carries its OWN PATH, making the launcher's bare `exec ttyd`
# resolve on a clean manager start (reboot / fresh re-provision) WITHOUT a
# hand-placed .d/ drop-in (#614 — moving the #612 go-live drop-in into code).
# Byte-identical to that drop-in's PATH; `%h` is the systemd home specifier.
# Scoped to the DAVID render ONLY — the owner (dev1) unit, where ttyd is a
# system /usr/bin binary already on the manager PATH, never gets this line.
#
# #638 — INVARIANT + FOOTGUN. airuleset renders this MAIN unit and, by
# deliberate invariant (#614, adversarially upheld), never writes or deletes
# `.d/` drop-ins. The #612 go-live hand-placed
# `webterm-david-ttyd.service.d/10-path.conf` with this same PATH before this
# code existed; #638 confirmed it is REDUNDANT and is TO BE removed by a
# ONE-TIME MANUAL step (owner-action, recorded on the ticket; NOT auto-cleaned
# by airuleset, and not yet run from a read-only worktree), never by code — a
# tool that deletes files a human hand-placed is a more dangerous tool. FOOTGUN: if
# you ever CHANGE the PATH below, a stale hand-placed drop-in would silently
# OVERRIDE it (drop-ins load last) and airuleset will NOT clean it up — verify
# none remains on the box first with
# `systemctl --user show webterm-david-ttyd.service -p DropInPaths`.
_DAVID_TTYD_PATH_ENV = (
    "# #614: self-contained PATH so the launcher's bare `exec ttyd` resolves\n"
    "# the no-sudo ~/.local/bin user-space static binary on a clean systemd\n"
    "# --user manager start (subdev has no sudo; the manager default PATH\n"
    "# excludes ~/.local/bin).\n"
    "Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin\n")


def render_david_ttyd_unit():
    tmpl = w.WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return _DAVID_UNIT_NOTE + (
        tmpl.replace("{{LAUNCH_SCRIPT}}", str(WEBTERM_DAVID_LAUNCH_PATH))
        .replace("[Service]\n", "[Service]\n" + _DAVID_TTYD_PATH_ENV)
        .replace("(dev1-only)", "(subdev david)"))


def render_david_gateway_unit():
    """The david gateway unit: LOOPBACK bind (cloudflared fronts it), david
    dash/ports, `After=` repointed to the david ttyd unit — and, per the #612
    owner directive, Cloudflare-ACCESS mode instead of a password: the ExecStart's
    `--cred {{CRED_PATH}}` is swapped for `--trust-access-header <header>` so NO
    credential file is validated. The remaining `{{CRED_PATH}}` token lives only
    in the shared template's password-model COMMENT — neutralised to n/a here,
    with the extended _DAVID_UNIT_NOTE stating the real (Access) auth model."""
    tmpl = w.WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return _DAVID_UNIT_NOTE + (
        # ExecStart: password -> Cloudflare Access header trust (retire --cred).
        tmpl.replace("--cred {{CRED_PATH}}",
                     "--trust-access-header " + access.WEBTERM_ACCESS_TRUST_HEADER)
            .replace("{{BIND_IP}}", WEBTERM_DAVID_BIND)
            .replace("{{GATEWAY_MODULE}}", str(w.WEBTERM_GATEWAY_MODULE))
            .replace("{{GATEWAY_PORT}}", str(WEBTERM_DAVID_GATEWAY_PORT))
            .replace("{{DASH_INDEX}}", str(WEBTERM_DAVID_DASH_INDEX))
            # Only the shared template's password-model comment still references
            # this token now — the ExecStart no longer does. Mark it n/a so no
            # human reads a live credential path into a passwordless unit.
            .replace("{{CRED_PATH}}", "n/a (Cloudflare Access — no credential)")
            .replace("{{TTYD_PORT}}", str(WEBTERM_DAVID_TTYD_PORT))
            .replace("{{TTYD_BASE}}", w.WEBTERM_TTYD_BASE)
            .replace("webterm-ttyd.service", "webterm-david-ttyd.service")
            .replace("(dev1-only)", "(subdev david)"))


def _retire_david_credential():
    """Delete the now-dead david password credential file (#612 owner directive:
    Cloudflare Access replaces the password, so the old `secret show` credential
    channel is retired). Best-effort + idempotent — a missing file is the normal
    steady state, never an error. Only the mode-600 credential FILE is removed;
    the dedicated ssh key (webterm_david_ed25519) is untouched (still needed for
    the david1-4 tabs). Returns True iff a file was actually removed."""
    cred = Path(str(w.WEBTERM_DAVID_CRED_PATH))
    try:
        if cred.exists():
            cred.unlink()
            print("  webterm(david): retired dead password credential %s "
                  "(Cloudflare Access replaces it)." % cred, file=sys.stderr)
            return True
    except OSError as e:
        print("  webterm(david): could not remove old credential %s (%s) — "
              "harmless, Access is the gate now." % (cred, e), file=sys.stderr)
    return False


def _write_david_artifacts():
    """Write the scoped inventory + dashboard + launcher + units — and RETIRE any
    dead password credential (NO credential is provisioned any more; Cloudflare
    Access replaces the password, #612 owner directive 2026-08-22). Pure
    filesystem writes (no systemd), split out so the render/write path is
    unit-testable without the enable/restart plumbing."""
    w.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    WEBTERM_DAVID_DASH_DIR.mkdir(parents=True, exist_ok=True)
    inv = w.webterm_inventory(profile=profiles.DAVID)
    WEBTERM_DAVID_INVENTORY_PATH.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    WEBTERM_DAVID_DASH_INDEX.write_text(
        w.render_dashboard_html(inv, ttyd_base=w.WEBTERM_TTYD_BASE),
        encoding="utf-8")
    # #612 owner directive 2026-08-22: NO credential. Cloudflare Access (email
    # OTP) at the edge is the whole gate, so the gateway runs in
    # --trust-access-header mode and NO credential is provisioned. Retire the
    # dead password file (the old `secret show` path) so no stale credential
    # channel is left lying around.
    _retire_david_credential()
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
    # ttyd on subdev is a no-sudo user-space static binary in ~/.local/bin,
    # which the install PROCESS's PATH (a push-driven NON-login ssh shell as
    # david1) does NOT include — so `shutil.which("ttyd")` alone would no-op
    # this gate and the box would never re-provision (#614). Accept the
    # explicit ~/.local/bin/ttyd location too (an executable file), mirroring
    # cli_binary_installers._binary_reachable's dest-or-PATH check.
    local_ttyd = Path.home() / ".local" / "bin" / "ttyd"
    have_ttyd = (shutil.which("ttyd") is not None
                 or (local_ttyd.is_file() and os.access(local_ttyd, os.X_OK)))
    if not key.exists() or not have_ttyd:
        return False, ("prerequisites missing (dedicated key present: %s; "
                       "ttyd: %s)" % (key.exists(), have_ttyd))
    return True, "ready"


def setup_webterm_david_tunnel(run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://david.newlevel.media/ -> the loopback david gateway. REPLACES the
    hand-made webterm-david-tunnel.service with an airuleset-written, functionally
    identical one (same UUID + config path + ExecStart). Renders the david-specific
    config/unit, then hands off to the SHARED `tun._provision_managed_tunnel` (the
    same never-raises, prereq-gated, linger+enable+restart orchestration the owner
    lane uses — so the two lanes cannot drift). Returns its result.

    Config path stays the box-default ~/.cloudflared/config.yml (matches the current
    working unit exactly — lowest-risk for a live external-dev terminal; subdev has
    no other tunnel today, so no collision — a review-noted accepted tradeoff)."""
    # subdev's no-sudo user-space binary is NOT on the install-process PATH, so try
    # ~/.local/bin/cloudflared explicitly first (cli_binary_installers pattern, #614).
    local_bin = Path.home() / ".local" / "bin" / "cloudflared"
    cloudflared_bin = (str(local_bin) if local_bin.is_file()
                       else (shutil.which("cloudflared") or WEBTERM_DAVID_CLOUDFLARED_BIN))
    config_text = tun.render_cloudflared_tunnel_config(
        WEBTERM_DAVID_TUNNEL_UUID, str(WEBTERM_DAVID_TUNNEL_CREDS),
        WEBTERM_DAVID_TUNNEL_HOSTNAME,
        "http://%s:%d" % (WEBTERM_DAVID_BIND, WEBTERM_DAVID_GATEWAY_PORT))
    unit_text = tun.render_cloudflared_tunnel_unit(
        "david webterm cloudflared tunnel (%s -> 127.0.0.1:%d)"
        % (WEBTERM_DAVID_TUNNEL_HOSTNAME, WEBTERM_DAVID_GATEWAY_PORT),
        str(WEBTERM_DAVID_TUNNEL_CONFIG), cloudflared_bin,
        after="network-online.target webterm-david-gateway.service")
    return tun._provision_managed_tunnel(
        WEBTERM_DAVID_TUNNEL_CREDS, cloudflared_bin, WEBTERM_DAVID_TUNNEL_CONFIG,
        config_text, WEBTERM_DAVID_TUNNEL_SERVICE_DEST,
        "webterm-david-tunnel.service", unit_text, run=run, lane="(david)",
        creds_absent_hint=" (the webterm-david-612 creds JSON must be present "
        "on subdev).")


def setup_webterm_david_service(run=None):
    """subdev-only: provision the david developer gateway (#612). Prerequisite-
    gated (see `prerequisites_ready`) so an ordinary subdev install can never
    break — a not-ready box prints the go-live steps and returns False, touching
    no systemd. Idempotent. Returns True on success, False on any skip/failure
    (never raises)."""
    run = run or subprocess.run
    try:
        ok, reason = prerequisites_ready()
    except Exception as e:  # a gate that itself errors is a SAFE no-op, never a raise
        print("  webterm(david): prerequisite check errored (%r) — no-op."
              % e, file=sys.stderr)
        return False
    if not ok:
        print("  webterm(david): %s — no-op until go-live setup.\n%s"
              % (reason, _DAVID_GO_LIVE), file=sys.stderr)
        return False

    # Post-gate body wrapped so a write/systemd failure is a logged False, never
    # a raise into cmd_install (the "never raises" contract) — mirrors
    # setup_webterm_service's own non-fatal-step discipline.
    try:
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
            print("  webterm(david): daemon-reload FAILED: %s"
                  % (err or "").strip(), file=sys.stderr)
        ok_all = True
        for svc in ("webterm-david-ttyd.service", "webterm-david-gateway.service"):
            rc, _o, err = _run_systemctl(["enable", "--now", svc])
            if rc != 0:
                print("  webterm(david): enable --now %s FAILED: %s"
                      % (svc, (err or "").strip()), file=sys.stderr)
                ok_all = False
                continue
            _run_systemctl(["restart", svc])
        # #635: bring the (previously hand-made) public HTTPS front under airuleset
        # management too — but ONLY once the loopback gateway/ttyd came up (ok_all),
        # so the tunnel never fronts a dead origin. Prereq-gated no-op if the creds
        # JSON is not present.
        if ok_all:
            setup_webterm_david_tunnel(run=run)
    except Exception as e:
        print("  webterm(david): provisioning errored (%r) — left un-provisioned."
              % e, file=sys.stderr)
        return False
    if ok_all:
        print("  webterm(david): gateway live on 127.0.0.1:%d (loopback — front "
              "with cloudflared). ttyd loopback 127.0.0.1:%d behind /t.\n%s"
              % (WEBTERM_DAVID_GATEWAY_PORT, WEBTERM_DAVID_TTYD_PORT,
                 _DAVID_GO_LIVE))
    return ok_all
