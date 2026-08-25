"""airuleset webterm — the ONE parameterized per-developer LANE provisioner (#665).

The rule-of-three is reached (david + marek + a future 4th developer), so the
~90%-shared render + setup skeleton the per-developer modules used to each COPY is
consolidated here into a single engine driven by a per-lane :class:`LaneSpec`.

``cli_webterm_david.py`` and ``cli_webterm_marek.py`` are now THIN: each keeps its
per-user module-level constants (the source of truth existing tests patch) + a
``_spec()`` factory that reads them fresh + public-API wrappers that delegate to the
functions here. A future 4th developer is a ``LaneSpec`` + a tiny thin module — a
config entry, never another copy.

This is post-#663 (v0.1.34+) only: every lane binds mode-0700 UNIX sockets in the
account's ``/run/user/<uid>`` runtime dir (NOT TCP loopback) and is fronted by a
cloudflared tunnel; auth is Cloudflare Access (email one-time-PIN) at the edge, so
the gateway runs ``--trust-access-header`` with no password/credential. The engine
REUSES the already-shared seams in cli_webterm (``access_execstart_transform``,
``render_webterm_launch_script``, ``webterm_inventory``,
``webterm_runtime_socket_abs``) and cli_webterm_tunnel (``_provision_managed_tunnel``)
rather than adding machinery — it extends the #663 "one shared source" direction to
the whole lane skeleton.

Deliberately stdlib-only (``dataclasses``); imports cli_webterm for the shared
render/template helpers. The dispatch in ``cli_webterm.maybe_setup_webterm`` imports
the thin lane modules lazily, so there is no module-level import cycle.
"""
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cli_webterm as w
import cli_webterm_tunnel as tun            # shared managed-tunnel render helpers (#635)
import cli_binary_installers as binstall    # #614: ttyd static-binary auto-install


# The self-contained PATH shared by every subdev lane's ttyd unit (was the
# byte-identical `_DAVID_TTYD_PATH_ENV` / `_MAREK_TTYD_PATH_ENV`). #614: the no-sudo
# ~/.local/bin user-space static ttyd is NOT on the systemd --user manager's default
# PATH, so the unit carries its own PATH and the launcher's bare `exec ttyd` resolves
# on a clean manager start WITHOUT a hand-placed .d/ drop-in. Scoped to the subdev
# lanes ONLY — the owner (dev1) unit, where ttyd is a system /usr/bin binary already
# on the manager PATH, never gets this line. `%h` is the systemd home specifier.
TTYD_PATH_ENV = (
    "# #614: self-contained PATH so the launcher's bare `exec ttyd` resolves\n"
    "# the no-sudo ~/.local/bin user-space static binary on a clean systemd\n"
    "# --user manager start (subdev has no sudo; the manager default PATH\n"
    "# excludes ~/.local/bin).\n"
    "Environment=PATH=%h/.local/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin\n")


# The honesty-bar unit NOTE prepended to each rendered lane unit so a human reading
# the installed file on subdev is never misled by the shared template's
# dev1/tailscale/password wording. Was the two ~28-line near-copies
# `_DAVID_UNIT_NOTE` / `_MAREK_UNIT_NOTE` — the ticket's named drift-start site —
# now ONE renderer parameterised by the handful of per-lane differences.
_UNIT_NOTE_TEMPLATE = (
    "# NOTE (#612/#663): this is the {NU} developer gateway on SUBDEV{acct}\n"
    "# — #663 it binds a mode-0700 UNIX-domain socket in {owner} /run/user/<uid>\n"
    "# runtime dir (NOT TCP 127.0.0.1:<port>) and is fronted by {tadj} cloudflared\n"
    "# public HTTPS tunnel (service: unix:<sock>) for {host}. The 'dev1 / tailscale\n"
    "# IP' wording, the ttyd port 7682 and the 'webterm-gateway.service' name\n"
    "# inherited from the shared template below refer to the OWNER deployment — the\n"
    "# {NU} sockets are %t/webterm-{nl}-{{gateway,ttyd}}.sock and the units are\n"
    "# webterm-{nl}-*.service. {scoped}\n"
    "#\n"
    "# AUTH (#612 owner directive 2026-08-22): NO password / credential. Cloudflare\n"
    "# Access does email one-time-PIN verification at the EDGE before any request\n"
    "# reaches the tunnel; the gateway runs in --trust-access-header mode and just\n"
    "# trusts the Cf-Access-Authenticated-User-Email header Cloudflare injects.\n"
    "# The shared template's 'form login / credential / constant-time compare'\n"
    "# wording refers to the OWNER (password) deployment — it does NOT apply here.\n"
    "#\n"
    "# EXPOSURE: this gateway is PUBLIC (Cloudflare Access at the edge is the\n"
    "# boundary) AND #663 the origin is a UNIX socket in {owner} 0700 runtime dir —\n"
    "# filesystem permissions are the LOCAL account boundary, so a peer subdev\n"
    "# account cannot reach the gateway or ttyd. The template's 'tailnet-only entry\n"
    "# point' / 'bound to dev1's tailscale IP' / 'failed logins rate-limited per\n"
    "# source IP' claims are all FALSE here (behind cloudflared the gateway sees only\n"
    "# one peer, so per-real-IP brute-force protection lives on the Cloudflare\n"
    "# EDGE).\n#\n")


def render_lane_unit_note(*, name_upper, name_lower, account_suffix, runtime_owner,
                          tunnel_adjective, hostname, scoped_inventory):
    """Render the shared honesty-bar unit note for a lane. ``scoped_inventory`` is
    the lane's own scoped-set sentence (already `# `-continued if it wraps)."""
    return _UNIT_NOTE_TEMPLATE.format(
        NU=name_upper, nl=name_lower, acct=account_suffix, owner=runtime_owner,
        tadj=tunnel_adjective, host=hostname, scoped=scoped_inventory)


@dataclass
class LaneSpec:
    """Everything the shared engine needs to provision ONE per-developer lane. Pure
    data — the thin lane modules build it from their own module-level constants (so
    tests that patch those constants keep working) and pass it to the functions
    below. ``identity_key=None`` means a local-attach lane (no ssh key required in
    the prerequisite gate); ``retire_credential_path=None`` means the lane never had
    a legacy password credential to retire."""
    name: str
    gateway_user: str
    profile: str
    bind: str
    ttyd_port: int
    gateway_port: int
    gateway_sock_basename: str
    ttyd_sock_basename: str
    inventory_path: Path
    dash_dir: Path
    dash_index: Path
    launch_path: Path
    ttyd_service_dest: Path
    gateway_service_dest: Path
    ttyd_service_name: str
    gateway_service_name: str
    tunnel_uuid: str
    tunnel_creds: Path
    tunnel_config: Path
    tunnel_service_dest: Path
    tunnel_service_name: str
    tunnel_hostname: str
    cloudflared_bin: str
    creds_absent_hint: str
    unit_note: str
    go_live: str
    label: str
    log_prefix: str
    identity_key: Optional[str] = None
    retire_credential_path: Optional[object] = None
    # #661 rework: a lane that declares its domain's human here has its dash
    # rendered through the owner-defined per-domain tab list
    # (cli_webterm.WEBTERM_DASHBOARD_TABS[human] -- order + exclusivity; marek).
    # None (default -- david) keeps the unfiltered render: david's scoped
    # inventory ids (david1..4/codex-bridge) differ from the policy dict's
    # fleet ids, so a filter there would render empty. VISIBILITY only -- the
    # inventory JSON (the connect allowlist) is never filtered.
    dashboard_human: Optional[str] = None


def render_ttyd_unit(spec):
    tmpl = w.WEBTERM_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    return spec.unit_note + (
        tmpl.replace("{{LAUNCH_SCRIPT}}", str(spec.launch_path))
        .replace("[Service]\n", "[Service]\n" + TTYD_PATH_ENV)
        .replace("(dev1-only)", spec.label))


def render_gateway_unit(spec):
    """The lane gateway unit: #663 UNIX-socket bind in the account runtime dir
    (cloudflared fronts it), the lane dash, `After=` repointed to the lane ttyd unit,
    and Cloudflare-ACCESS mode instead of a password — the shared
    `access_execstart_transform` swaps the password/TCP flags for
    `--trust-access-header` + UNIX sockets FIRST (the flag substrings still carry
    their literal `{{TOKEN}}`s at that point), before the per-lane substitutions. The
    remaining `{{CRED_PATH}}` survives only in the template's password-model COMMENT,
    neutralised to n/a here.

    #703: every lane unit also carries `--u-lane <profile>` — the PER-TENANT
    U-dot data channel (the lane gateway serves its own scoped map, refreshed
    by the scoped `webterm-u-collect --lane` collector over the lane's own
    u_tenant sessions only). NEVER `--u-collect`: that owner-only cross-tenant
    flag stays exclusive to the owner unit (#677 boundary, lock-tested)."""
    tmpl = w.WEBTERM_GATEWAY_SERVICE_TEMPLATE.read_text(encoding="utf-8")
    execstart = w.access_execstart_transform(
        tmpl, spec.gateway_sock_basename, spec.ttyd_sock_basename)
    # #703: the lane-mode sibling of the owner unit's --u-collect injection
    # (same replace shape, one ExecStart occurrence in the shared template).
    execstart = execstart.replace(
        "--base-path {{TTYD_BASE}}",
        "--base-path {{TTYD_BASE}} --u-lane " + spec.profile)
    return spec.unit_note + (
        execstart
        .replace("{{BIND_IP}}", spec.bind)
        .replace("{{GATEWAY_MODULE}}", str(w.WEBTERM_GATEWAY_MODULE))
        .replace("{{GATEWAY_PORT}}", str(spec.gateway_port))
        .replace("{{DASH_INDEX}}", str(spec.dash_index))
        .replace("{{CRED_PATH}}", "n/a (Cloudflare Access — no credential)")
        .replace("{{TTYD_PORT}}", str(spec.ttyd_port))
        .replace("{{TTYD_BASE}}", w.WEBTERM_TTYD_BASE)
        .replace("webterm-ttyd.service", spec.ttyd_service_name)
        .replace("(dev1-only)", spec.label))


def retire_credential(cred_path, log_prefix):
    """Delete a now-dead lane password credential file (Cloudflare Access replaces
    the password, #612 owner directive — the old `secret show` channel is retired).
    Best-effort + idempotent; a missing file is the normal steady state. Returns True
    iff a file was actually removed."""
    cred = Path(str(cred_path))
    try:
        if cred.exists():
            cred.unlink()
            print("  %s: retired dead password credential %s (Cloudflare Access "
                  "replaces it)." % (log_prefix, cred), file=sys.stderr)
            return True
    except OSError as e:
        print("  %s: could not remove old credential %s (%s) — harmless, Access is "
              "the gate now." % (log_prefix, cred, e), file=sys.stderr)
    return False


def write_artifacts(spec):
    """Write the scoped inventory + dashboard + PWA assets + launcher + units, and —
    for a lane that carries a legacy password (`retire_credential_path`) — retire it.
    Pure filesystem writes (no systemd), so the render/write path is unit-testable
    without the enable/restart plumbing."""
    w.CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    spec.dash_dir.mkdir(parents=True, exist_ok=True)
    inv = w.webterm_inventory(profile=spec.profile)
    spec.inventory_path.write_text(
        json.dumps(inv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # The lane gateway renders its OWN physically-scoped inventory. #661 rework:
    # a lane that declares `dashboard_human` (marek) consumes the per-domain
    # WEBTERM_DASHBOARD_TABS policy for tab ORDER + exclusivity; one without it
    # (david) renders unfiltered -- the `human` kwarg is then omitted ENTIRELY
    # (the #684 parity lock). getattr: the parity tests drive this with a
    # SimpleNamespace spec that predates the field.
    render_kwargs = {"ttyd_base": w.WEBTERM_TTYD_BASE}
    human = getattr(spec, "dashboard_human", None)
    if human is not None:
        render_kwargs["human"] = human
    # #703: every lane dashboard polls its OWN gateway's /u-status -- that
    # gateway serves the PER-TENANT scoped map (--u-lane in the unit rendered
    # below), so enabling the poll here leaks nothing cross-tenant; both lanes
    # get the U-dot POLL automatically at every deploy (#684 parity). A given
    # box shows its dot only where the scoped reader can actually run: a `local`
    # entry always (a direct cache read), an ssh entry where its dedicated key
    # runs the fixed reader snippet -- NOT under a `restrict,command="tmux ..."`
    # forced-command key (the #661 go-live shape recommended for owner-account
    # targets), which would block the BatchMode reader -> that box is omitted,
    # dot off (fail-safe direction, never a stale nonzero). Verify the live dot
    # on the real marek/david deploy where an ssh target's key shape matters.
    render_kwargs["lane_u_status"] = True
    spec.dash_index.write_text(
        w.render_dashboard_html(inv, **render_kwargs), encoding="utf-8")
    # The lane's own installable-PWA assets (#644 per-domain identity) next to index.
    import cli_webterm_pwa
    cli_webterm_pwa.write_pwa_assets(spec.dash_dir, spec.profile)
    if spec.retire_credential_path is not None:
        retire_credential(spec.retire_credential_path, spec.log_prefix)
    spec.launch_path.write_text(
        w.render_webterm_launch_script(
            inventory_path=spec.inventory_path,
            ttyd_socket_basename=spec.ttyd_sock_basename),  # #663 UNIX socket
        encoding="utf-8")
    os.chmod(spec.launch_path, 0o755)
    spec.ttyd_service_dest.parent.mkdir(parents=True, exist_ok=True)
    spec.ttyd_service_dest.write_text(render_ttyd_unit(spec), encoding="utf-8")
    spec.gateway_service_dest.write_text(render_gateway_unit(spec), encoding="utf-8")


def _ttyd_available():
    """ttyd on subdev is a no-sudo ~/.local/bin static binary the push-driven
    non-login ssh install PATH does NOT include — so `shutil.which` alone would
    no-op the gate and the box would never re-provision (#614). Accept the explicit
    ~/.local/bin/ttyd location too (an executable file)."""
    local_ttyd = Path.home() / ".local" / "bin" / "ttyd"
    return (shutil.which("ttyd") is not None
            or (local_ttyd.is_file() and os.access(local_ttyd, os.X_OK)))


def prerequisites_ready(spec):
    """(ok, reason) — True only when this box may actually provision: running as the
    lane's gateway account with ttyd installed (and, for an ssh-based lane, the
    dedicated key present). Every False is a SAFE no-op reason, never a failure."""
    from cli_filedrop_watchdog import _whoami
    who = _whoami()
    if who != spec.gateway_user:
        return False, ("install runs as %r, not the gateway account %r"
                       % (who, spec.gateway_user))
    have_ttyd = _ttyd_available()
    if spec.identity_key is not None:
        key = Path(os.path.expanduser(spec.identity_key))
        if not key.exists() or not have_ttyd:
            return False, ("prerequisites missing (dedicated key present: %s; "
                           "ttyd: %s)" % (key.exists(), have_ttyd))
    elif not have_ttyd:
        return False, "prerequisites missing (ttyd: %s)" % have_ttyd
    return True, "ready"


def setup_tunnel(spec, run=None):
    """subdev-only: provision + enable the MANAGED cloudflared tunnel fronting
    https://<hostname>/ -> the lane gateway's #663 UNIX socket. Renders the
    lane-specific config/unit, then hands off to the SHARED
    `tun._provision_managed_tunnel` (the same never-raises, prereq-gated,
    linger+enable+restart orchestration every lane uses — so the lanes cannot
    drift). Returns its result."""
    # subdev's no-sudo user-space binary is NOT on the install-process PATH, so try
    # ~/.local/bin/cloudflared explicitly first (cli_binary_installers pattern, #614).
    local_bin = Path.home() / ".local" / "bin" / "cloudflared"
    cloudflared_bin = (str(local_bin) if local_bin.is_file()
                       else (shutil.which("cloudflared") or spec.cloudflared_bin))
    # #663: front the gateway's mode-0700 UNIX socket, not a TCP loopback port.
    gw_sock = w.webterm_runtime_socket_abs(spec.gateway_sock_basename)
    config_text = tun.render_cloudflared_tunnel_config(
        spec.tunnel_uuid, str(spec.tunnel_creds), spec.tunnel_hostname,
        "unix:" + gw_sock)
    unit_text = tun.render_cloudflared_tunnel_unit(
        "%s webterm cloudflared tunnel (%s -> unix:%s)"
        % (spec.name, spec.tunnel_hostname, gw_sock),
        str(spec.tunnel_config), cloudflared_bin,
        after="network-online.target " + spec.gateway_service_name)
    return tun._provision_managed_tunnel(
        spec.tunnel_creds, cloudflared_bin, spec.tunnel_config, config_text,
        spec.tunnel_service_dest, spec.tunnel_service_name, unit_text, run=run,
        lane="(%s)" % spec.name, creds_absent_hint=spec.creds_absent_hint)


def setup_service(spec, run=None, *, prereq_fn, write_artifacts_fn, tunnel_fn):
    """subdev-only: provision the lane developer gateway. Prerequisite-gated (via
    `prereq_fn`, the lane module's own `prerequisites_ready` so a test can patch it)
    so an ordinary subdev install can never break — a not-ready box prints the
    go-live steps and returns False, touching no systemd. Idempotent. Returns True on
    success, False on any skip/failure (never raises). `write_artifacts_fn` /
    `tunnel_fn` are the lane module's own wrappers (stable test/patch seams)."""
    run = run or subprocess.run
    # #614 (owner decision 2026-08-23, Approach 2): auto-install the ttyd BINARY into
    # ~/.local/bin BEFORE the prerequisite gate checks for it — the gate REQUIRES
    # ttyd, so a fresh subdev re-provision (ttyd absent) would otherwise no-op
    # provisioning forever. Best-effort/non-fatal, exactly how cmd_install calls the
    # ffmpeg/claude installers; a harmless no-op wherever ttyd already resolves.
    try:
        binstall.ensure_ttyd_static_binary()
    except Exception as e:
        print("  %s: ttyd static install error (non-fatal): %r" % (spec.log_prefix, e),
              file=sys.stderr)
    try:
        ok, reason = prereq_fn()
    except Exception as e:  # a gate that itself errors is a SAFE no-op, never a raise
        print("  %s: prerequisite check errored (%r) — no-op." % (spec.log_prefix, e),
              file=sys.stderr)
        return False
    if not ok:
        print("  %s: %s — no-op until go-live setup.\n%s"
              % (spec.log_prefix, reason, spec.go_live), file=sys.stderr)
        return False

    # Post-gate body wrapped so a write/systemd failure is a logged False, never a
    # raise into cmd_install (the "never raises" contract).
    try:
        from cli_filedrop_watchdog import _run_systemctl, _whoami
        write_artifacts_fn()

        try:
            run(["loginctl", "enable-linger", _whoami()], capture_output=True,
                text=True)
        except Exception as e:
            print("  %s: loginctl enable-linger skipped (%s)" % (spec.log_prefix, e),
                  file=sys.stderr)

        rc, _o, err = _run_systemctl(["daemon-reload"])
        if rc != 0:
            print("  %s: daemon-reload FAILED: %s"
                  % (spec.log_prefix, (err or "").strip()), file=sys.stderr)
        ok_all = True
        for svc in (spec.ttyd_service_name, spec.gateway_service_name):
            rc, _o, err = _run_systemctl(["enable", "--now", svc])
            if rc != 0:
                print("  %s: enable --now %s FAILED: %s"
                      % (spec.log_prefix, svc, (err or "").strip()), file=sys.stderr)
                ok_all = False
                continue
            _run_systemctl(["restart", svc])
        # Bring the public HTTPS front up too — but ONLY once the loopback gateway/
        # ttyd came up (ok_all), so the tunnel never fronts a dead origin. Prereq-
        # gated no-op if the creds JSON is not present.
        if ok_all:
            tunnel_fn(run=run)
    except Exception as e:
        print("  %s: provisioning errored (%r) — left un-provisioned."
              % (spec.log_prefix, e), file=sys.stderr)
        return False
    if ok_all:
        print("  %s: gateway live on UNIX socket %s (#663 account-scoped 0700 runtime "
              "dir — front with cloudflared service: unix:). ttyd UNIX socket %s behind "
              "/t.\n%s"
              % (spec.log_prefix,
                 w.webterm_runtime_socket_abs(spec.gateway_sock_basename),
                 w.webterm_runtime_socket_abs(spec.ttyd_sock_basename), spec.go_live),
              file=sys.stderr)
    return ok_all
