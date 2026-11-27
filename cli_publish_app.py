"""#1370 — `airuleset.py publish-app`: the ONE path to publish an app.

A published app = its box's Cloudflare tunnel + a Cloudflare Access app (owner
18.11.2026: "ak to pozerám na zborovej sieti tak bez hesla, ak z internetu tak
cez cloudflare auth"). Never a router change.

    airuleset.py publish-app <host> --tunnel <uuid> --origin <url>
        [--origin-server-name X] [--venue-bypass] [--apply]

The order is fixed, and a failed step stops the chain:
  1. Access app FIRST (self_hosted, 730h, allow = owner + marek). A proxied
     CNAME before the Access app exists would serve the origin to the whole
     internet (the #983/#1115 exposure class). An existing app is left as is.
  2. `--venue-bypass` only: the host must be in the owner-confirmed table
     `cli_venue_bypass_apps.VENUE_BYPASS_APPS`, else REFUSED before any call
     (owner 19.11.2026: the LAN bypass is an opt-in per app that the owner
     confirms). The bypass gets the venue IPs exactly as the hourly
     `venue-bypass-sync` does (same code).
  3. Tunnel ingress, remotely managed (`cli_tunnel_remote_config`): the rule is
     inserted before the catch-all, every other rule and config key kept. A
     locally managed tunnel is refused (a PUT would convert it and its local
     ingress would stop counting); a host routed elsewhere is refused.
  4. Proxied CNAME `<uuid>.cfargotunnel.com`, create-only
     (`cli_drop_golive.ensure_cname_create_only`); an existing A/AAAA record or
     a CNAME pointing elsewhere is a conflict, never overwritten.

`--public` (issue 1403) replaces step 1: only a host in the owner-confirmed
`cli_venue_bypass_apps.PUBLIC_APPS` table, and the host must have NO Access app
(an existing one stops the chain, untouched). The app limits what it serves.

Dry-run by default (reads only, prints the plan). `--apply` writes, and is
refused from a git worktree checkout (#1115). Idempotent: a second run reports
every step unchanged.
"""
from __future__ import annotations

import re
import sys

import cli_venue_bypass as vb
import cli_venue_bypass_apps as table
import cli_webterm_access as wa

ZONE = "newlevel.media"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ORIGIN_RE = re.compile(r"^(?:https?://[^\s]+|unix:/[^\s]+|http_status:\d{3})$")

REFUSE_BYPASS = (
    "REFUSED: %s is not in VENUE_BYPASS_APPS (cli_venue_bypass_apps.py). The "
    "venue-LAN bypass is an opt-in PER APP that the OWNER confirms for THAT app "
    "(owner 19.11.2026, issue 1370). Default = Cloudflare Access only. Ask "
    "airuleset (gh issue create -R zbynekdrlik/airuleset) to record the owner's "
    "confirmation in the table; nothing was changed.")

REFUSE_PUBLIC = (
    "REFUSED: %s is not in PUBLIC_APPS (cli_venue_bypass_apps.py). A public app "
    "(no Cloudflare Access) is an opt-in PER APP that the OWNER confirms (issue "
    "1403). Default = Cloudflare Access only; nothing was changed.")


def validate(host, tunnel_id, origin, zone=ZONE):
    """None when the arguments are publishable, else the refusal text."""
    host = (host or "").strip().lower()
    suffix = "." + zone
    if not host.endswith(suffix):
        return "host %r is not in the zone %s" % (host, zone)
    label = host[:-len(suffix)]
    if not _LABEL_RE.match(label):
        # *.newlevel.media Universal SSL covers ONE level only (#664).
        return ("host %r must be ONE flat label under %s (the edge certificate "
                "covers one level only)" % (host, zone))
    if not _UUID_RE.match((tunnel_id or "").lower()):
        return "--tunnel %r is not a tunnel UUID" % tunnel_id
    if not _ORIGIN_RE.match(origin or ""):
        return "--origin %r is not an http(s)://, unix:/ or http_status: service" % origin
    return None


def access_payload(host, allow_precedence=1):
    """The new app with its allow policy inline. With a venue bypass to follow,
    the allow takes precedence 2 so the bypass gets 1 (like the live apps)."""
    allow = {"name": table.PUBLISH_ALLOW_POLICY_NAME, "decision": "allow",
             "precedence": allow_precedence,
             "include": [{"email": {"email": e}} for e in table.PUBLISH_ALLOWED_EMAILS]}
    return {"name": "app — %s" % host, "domain": host, "type": "self_hosted",
            "session_duration": table.PUBLISH_SESSION_DURATION,
            "auto_redirect_to_identity": False, "allowed_idps": [],
            "app_launcher_visible": False, "policies": [allow]}


def ingress_rule(host, origin, origin_server_name=None):
    rule = {"hostname": host, "service": origin}
    if origin_server_name:
        rule["originRequest"] = {"originServerName": origin_server_name}
    return rule


def plan_ingress(ingress, rule):
    """`(new_ingress | None, action, error)`: None + `unchanged` when the host
    already routes exactly there; an error when it routes elsewhere or the
    list has no catch-all last."""
    ingress = list(ingress or [])
    if not ingress or ingress[-1].get("hostname"):
        return None, "", "tunnel ingress has no catch-all last rule — refusing"
    for r in ingress:
        if (r.get("hostname") or "").lower() == rule["hostname"]:
            same = (r.get("service") == rule["service"] and not r.get("path")
                    and ((r.get("originRequest") or {}).get("originServerName")
                         == (rule.get("originRequest") or {}).get("originServerName")))
            if same:
                return None, "unchanged", ""
            return None, "", ("%s already routes to %r — left UNTOUCHED (refusing "
                              "to overwrite a route)" % (rule["hostname"], r))
    return ingress[:-1] + [rule] + ingress[-1:], "insert", ""


# --------------------------------------------------------------------------- #
# Steps (each returns True on ok and prints its line)
# --------------------------------------------------------------------------- #

def step_access(client, host, *, dry_run, out, allow_precedence=1):
    app, (st, body) = client.find_app_by_domain(host)
    if st != 200:
        print("  [ERROR] access: cannot read Access apps (HTTP %s): %s"
              % (st, wa._first_err(body)), file=out)
        return False, None
    if app is not None:
        allows = [p for p in app.get("policies") or [] if p.get("decision") == "allow"]
        if not allows:
            print("  [ERROR] access: app %s exists with NO allow policy — fix by hand"
                  % host, file=out)
            return False, app
        print("  [ok] access: app %s exists (id %s) — unchanged" % (host, app.get("id")),
              file=out)
        return True, app
    if dry_run:
        print("  [would] access: create app %s (730h, allow %s)"
              % (host, ", ".join(table.PUBLISH_ALLOWED_EMAILS)), file=out)
        return True, None
    st, body = client.create_app(access_payload(host, allow_precedence))
    if not wa._ok(st, body):
        print("  [ERROR] access: create app FAILED (HTTP %s): %s"
              % (st, wa._first_err(body)), file=out)
        return False, None
    app = body.get("result") or {}
    print("  [changed] access: created app %s (id %s)" % (host, app.get("id")), file=out)
    return True, app


def step_no_access(client, host, *, out):
    """`--public`: there must be NO Access app on the host (one would make the
    "public" app a login page). An existing app is left untouched and stops."""
    app, (st, body) = client.find_app_by_domain(host)
    if st != 200:
        print("  [ERROR] access: cannot read Access apps (HTTP %s): %s"
              % (st, wa._first_err(body)), file=out)
        return False
    if app is not None:
        print("  [ERROR] access: %s has an Access app (id %s) but is declared "
              "PUBLIC — left UNTOUCHED; resolve by hand" % (host, app.get("id")),
              file=out)
        return False
    print("  [ok] access: none — PUBLIC by owner confirmation: %s"
          % table.PUBLIC_APPS[host], file=out)
    return True


def step_bypass(client, host, app, *, dry_run, out):
    try:
        ips = vb.source_ips(vb._apps_by_domain(vb.list_apps(client)))
    except vb.SourceError as e:
        print("  [FAIL] bypass: presenter source unusable: %s — no bypass written" % e,
              file=out)
        return False
    if app is None:     # dry run of a new app
        print("  [would] bypass: create %r on %s with %s"
              % (table.BYPASS_POLICY_NAME, host, ", ".join(sorted(ips))), file=out)
        return True
    return vb.reconcile_app(client, app, ips, dry_run=dry_run, out=out)


def step_ingress(client, tunnel_id, rule, *, dry_run, out):
    st, t = client.get_tunnel(tunnel_id)
    if not wa._ok(st, t):
        print("  [ERROR] ingress: cannot read tunnel %s (HTTP %s): %s"
              % (tunnel_id, st, wa._first_err(t)), file=out)
        return False
    if not (t.get("result") or {}).get("remote_config"):
        print("  [ERROR] ingress: tunnel %s is LOCALLY managed — refusing (a PUT "
              "would convert it and drop its local ingress)" % tunnel_id, file=out)
        return False
    st, c = client.get_config(tunnel_id)
    if not wa._ok(st, c):
        print("  [ERROR] ingress: cannot read config (HTTP %s): %s"
              % (st, wa._first_err(c)), file=out)
        return False
    config = dict(((c.get("result") or {}).get("config")) or {})
    new, action, err = plan_ingress(config.get("ingress"), rule)
    if err:
        print("  [ERROR] ingress: " + err, file=out)
        return False
    if action == "unchanged":
        print("  [ok] ingress: %s -> %s on %s — unchanged"
              % (rule["hostname"], rule["service"], tunnel_id), file=out)
        return True
    line = "insert %s -> %s before the catch-all of %s" % (
        rule["hostname"], rule["service"], tunnel_id)
    if dry_run:
        print("  [would] ingress: " + line, file=out)
        return True
    config["ingress"] = new         # every other key (warp-routing, ...) kept
    st, p = client._call("PUT", client._tunnel(tunnel_id, "/configurations"),
                         {"config": config})
    if not wa._ok(st, p):
        print("  [ERROR] ingress: PUT FAILED (HTTP %s): %s" % (st, wa._first_err(p)),
              file=out)
        return False
    print("  [changed] ingress: " + line, file=out)
    return True


def step_dns(client, host, tunnel_id, *, dry_run, out, zone=ZONE):
    import cli_cloudflare_dns as dns
    import cli_drop_golive as golive
    zone_id, (st, body) = client.get_zone_id(zone)
    if zone_id is None:
        print("  [ERROR] dns: zone %s (HTTP %s): %s" % (zone, st, dns._first_err(body)),
              file=out)
        return False
    for rtype in ("A", "AAAA"):
        rec, (st, body) = client.find_record(zone_id, host, rtype)
        if st != 200:
            print("  [ERROR] dns: cannot list %s records (HTTP %s)" % (rtype, st), file=out)
            return False
        if rec is not None:
            print("  [ERROR] dns: %s already has an %s record %r — left UNTOUCHED; "
                  "remove it by hand, then re-run" % (host, rtype, rec.get("content")),
                  file=out)
            return False
    content = golive._cname_content(tunnel_id)
    r = golive.ensure_cname_create_only(
        client, zone, host, content, "published app, issue 1370 publish-app",
        dry_run=dry_run)
    if not r["ok"]:
        print("  [ERROR] dns: %s" % r["error"], file=out)
        return False
    tag = {"unchanged": "ok", "would_create": "would"}.get(r["action"], "changed")
    print("  [%s] dns: CNAME %s -> %s (proxied) %s" % (tag, host, content, r["action"]),
          file=out)
    return True


def publish(host, tunnel_id, origin, *, origin_server_name=None, venue_bypass=False,
            dry_run=True, access_client, tunnel_client, dns_client, out=None,
            apps_table=None, public=False, public_table=None):
    """Run the four steps in order; returns the exit code."""
    out = out if out is not None else sys.stdout
    apps_table = table.VENUE_BYPASS_APPS if apps_table is None else apps_table
    public_table = table.PUBLIC_APPS if public_table is None else public_table
    host = (host or "").strip().lower()
    tunnel_id = (tunnel_id or "").strip().lower()
    err = validate(host, tunnel_id, origin)
    if err:
        print("publish-app: REFUSED: " + err, file=out)
        return 2
    if venue_bypass and host not in apps_table:
        print("publish-app: " + REFUSE_BYPASS % host, file=out)
        return 2
    if public and (venue_bypass or host not in public_table):
        print("publish-app: " + (REFUSE_PUBLIC % host if host not in public_table
                                 else "REFUSED: --public and --venue-bypass "
                                      "exclude each other"), file=out)
        return 2
    print("publish-app %s [%s]" % (host, "DRY-RUN (no writes)" if dry_run else "APPLY"),
          file=out)
    if public:
        ok, app = step_no_access(access_client, host, out=out), None
    else:
        ok, app = step_access(access_client, host, dry_run=dry_run, out=out,
                              allow_precedence=2 if venue_bypass else 1)
    if ok and venue_bypass:
        ok = step_bypass(access_client, host, app, dry_run=dry_run, out=out)
    if ok:
        ok = step_ingress(tunnel_client, tunnel_id,
                          ingress_rule(host, origin, origin_server_name),
                          dry_run=dry_run, out=out)
    if ok:
        ok = step_dns(dns_client, host, tunnel_id, dry_run=dry_run, out=out)
    if not ok:
        print("  stopped: a later step never runs after a failed one", file=out)
        return 1
    if dry_run:
        print("  (dry-run — nothing changed; re-run with --apply)", file=out)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _clients():
    import cli_cloudflare_dns as dns
    import cli_tunnel_remote_config as trc
    access = vb.load_client()
    tunnel = trc.load_client()
    if tunnel is None:
        print("publish-app: no tunnel token at %s" % trc.TOKEN_FILE, file=sys.stderr)
    try:
        dns_token = dns._load_token()
    except OSError as e:
        print("publish-app: cannot read DNS token %s: %s" % (dns.DNS_TOKEN_FILE, e),
              file=sys.stderr)
        dns_token = None
    if access is None or tunnel is None or not dns_token:
        return None
    return access, tunnel, dns.DnsClient(token=dns_token)


def cmd_publish_app(args):
    dry_run = not getattr(args, "apply", False)
    host = (args.host or "").strip().lower()
    if getattr(args, "venue_bypass", False) and host not in table.VENUE_BYPASS_APPS:
        print("publish-app: " + REFUSE_BYPASS % host)
        return 2
    if not dry_run and vb.refused_from_worktree(
            "publish-app --apply", getattr(args, "_is_worktree_fn", None)):
        return 1
    clients = getattr(args, "_clients", None) or _clients()
    if clients is None:
        return 1
    access, tunnel, dns_client = clients
    return publish(host, args.tunnel, args.origin,
                   origin_server_name=getattr(args, "origin_server_name", None),
                   venue_bypass=getattr(args, "venue_bypass", False),
                   public=getattr(args, "public", False),
                   dry_run=dry_run, access_client=access, tunnel_client=tunnel,
                   dns_client=dns_client)


def register_parser(sub):
    p = sub.add_parser(
        "publish-app",
        help="#1370: publish an app = Access app (owner+marek, 730h) FIRST, then "
             "tunnel ingress, then a proxied CNAME; dry-run by default")
    p.add_argument("host", help="flat <name>.newlevel.media hostname")
    p.add_argument("--tunnel", required=True, help="the box's tunnel UUID")
    p.add_argument("--origin", required=True,
                   help="ingress service, e.g. https://127.0.0.1:443")
    p.add_argument("--origin-server-name", dest="origin_server_name", default=None,
                   help="originRequest.originServerName (TLS SNI to the origin)")
    p.add_argument("--venue-bypass", dest="venue_bypass", action="store_true",
                   help="add the venue-LAN bypass (only for a host in "
                        "VENUE_BYPASS_APPS — the owner confirms each app)")
    p.add_argument("--public", action="store_true",
                   help="NO Access app (only for a host in PUBLIC_APPS — the "
                        "owner confirms each app, issue 1403)")
    p.add_argument("--apply", action="store_true",
                   help="write (default: read and print the plan)")
    return p
