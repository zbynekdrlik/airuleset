"""#1238 — the controller tunnel's ONE multi-ingress render, with two callers.

The controller's shared cloudflared tunnel fronts every hosted webterm lane,
every controller-topology drop lane, the declared service routes and claudy.
`install` renders it (`cli_webterm._setup_controller_webterm`), but on a push
that render runs BEFORE the deploy loop harvests each account's persistent
filedrop port (#1115, `cli_remote._deploy_to_all_remotes`). A NEW drop lane
therefore had no `^/s/` rule until the NEXT push, and `share` printed a URL
whose route did not exist. So the deploy loop now hands the ports the harvest
CHANGED to `reapply_after_harvest`, which re-renders the SAME config here.
The change-gated apply (#1189, `_provision_managed_tunnel`) restarts the tunnel
via the overlap only when the rendered bytes differ, so a harvest that changed
nothing costs nothing.

A stdlib leaf. Every cross-module name is read through its module at call time
(`cli_webterm.profiles`, `cli_webterm_tunnel._provision_managed_tunnel`, …),
so the existing test seams patch it exactly as before.
"""
import os
import sys
from pathlib import Path

CLAUDY_INGRESS = ("claudy.newlevel.media", "http://100.101.214.103:8791")  # #983


def is_controller_account():
    """True on the controller box when running as its `airuleset` account,
    from the pwd entry (unspoofable, #869), never `$USER`."""
    import pwd
    try:
        from watchdog.reaper import default_box_class
    except ImportError:
        return False
    return (default_box_class() == "controller"
            and pwd.getpwuid(os.getuid()).pw_name == "airuleset")


def hosted_lanes():
    """``[(human, module, spec)]`` for every webterm lane `LANE_HOST` places on
    the controller (#870 F4a), each module + spec loaded in one pass."""
    import importlib
    import cli_webterm as w
    lanes = []
    for human, box in w.profiles.LANE_HOST.items():
        mod_name = w._HUMAN_TO_MODULE.get(human)
        if box != "controller" or mod_name is None:
            continue
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "setup_webterm_%s_service" % human) and hasattr(mod, "_spec"):
            lanes.append((human, mod, mod._spec()))
    return lanes


def apply_tunnel(lanes):
    """Render the controller multi-ingress config and hand it to the change-
    gated provisioner; returns its ``tunnel_ok``. One rule per hosted lane
    (hostname -> its gateway UNIX socket), then the controller-topology drop
    lanes from the filedrop-port cache (#931/#1114/#1238), the declared service
    routes (#1131), claudy (#983) and the catch-all 404."""
    import cli_drop_gateway as dg
    import cli_drop_lanes as dl
    import cli_webterm as w
    import cli_webterm_tunnel as tun
    cloudflared = Path.home() / ".cloudflared"
    creds_path = cloudflared / w.CONTROLLER_TUNNEL_CREDS_NAME
    config_path = cloudflared / "controller-webterm.yml"
    service_name = "webterm-controller-tunnel.service"
    service_dest = Path.home() / ".config" / "systemd" / "user" / service_name
    cloudflared_bin = str(Path.home() / ".local" / "bin" / "cloudflared")
    rules, after = [], ["network-online.target"]
    for _human, _mod, spec in lanes:
        rules.append((spec.tunnel_hostname,
                      "unix:" + w.webterm_runtime_socket_abs(spec.gateway_sock_basename)))
        after.append(spec.gateway_service_name)
    rules.extend(dg.drop_ingress_rules_for_controller()
                 + dl.controller_service_ingress_rules())
    rules.append(CLAUDY_INGRESS)
    config_text = tun.render_cloudflared_multi_ingress_config(
        w.CONTROLLER_TUNNEL_UUID, str(creds_path), rules)
    unit_text = tun.render_cloudflared_tunnel_unit(
        "airuleset webterm controller tunnel (multi-ingress, #870 F4a)",
        str(config_path), cloudflared_bin, after=" ".join(after))
    return tun._provision_managed_tunnel(
        creds_path, cloudflared_bin, config_path, config_text,
        service_dest, service_name, unit_text, lane="(controller)")


def reapply_after_harvest(changed, *, apply_fn=None, is_controller_fn=None):
    """Re-render + apply the controller tunnel when the push harvest CHANGED a
    lane's filedrop port (a new lane's first port, or a drift), so its `/s/`
    rule goes live in THIS push. Returns None when nothing changed, off the
    controller account, or under a test run that left EITHER real seam in
    place (a test must never write the live controller tunnel); else the
    apply's result. Never raises: a failure is LOUD and the next push
    re-renders anyway."""
    if not changed:
        return None
    if os.environ.get("PYTEST_CURRENT_TEST") and None in (apply_fn, is_controller_fn):
        return None
    is_controller_fn = is_controller_fn or is_controller_account
    keys = ", ".join(changed)
    try:
        if not is_controller_fn():
            return None
        print("  drop-lanes: filedrop port changed for %s — re-rendering the "
              "controller ingress in this push (#1238)" % keys)
        ok = (apply_fn or (lambda: apply_tunnel(hosted_lanes())))()
    except Exception as e:  # noqa: BLE001 — loud, never break the push
        print("  ⚠ drop-lanes: controller ingress re-render for %s failed "
              "(non-fatal, the next push retries): %r" % (keys, e), file=sys.stderr)
        return False
    if not ok:
        print("  ⚠ drop-lanes: controller tunnel not live after the re-render for "
              "%s — share on it refuses until a push applies it" % keys,
              file=sys.stderr)
    return bool(ok)
