"""#1370 — keep every OWNER-CONFIRMED venue-LAN bypass fresh (controller, hourly).

The venue egress IPs (SNV, PP) are already kept fresh by presenter#977: its
`egress_sync` asks what-is-my-IP from a box ON each venue's LAN every hour and
rewrites presenter's own bypass policies on the Access apps
`prsnv.newlevel.media` / `prpp.newlevel.media`. This leaf copies that verified
IP set into the managed bypass policy of every app in the ONE owner-confirmed
table (`cli_venue_bypass_apps.VENUE_BYPASS_APPS`), so a venue IP change never
puts a login in front of a church-LAN user.

Contract:
  * reads presenter's apps, unions the `{"ip": {"ip": ...}}` includes of their
    `decision == "bypass"` policies (the `/healthz` apps are not sources);
  * for every table app: the policy named `BYPASS_POLICY_NAME` includes EXACTLY
    that IP set (app-scoped `PUT .../apps/<id>/policies/<pid>`, `POST` when
    absent). Allow policies are never touched;
  * FAIL-LOUD: an unreadable API, a source app missing, a source with no bypass,
    a source bypass with a non-IP include, or an empty set → rc 1 and NO write,
    so an unreadable source can never remove a bypass;
  * an app NOT in the table that carries a policy with the managed name is
    REPORTED (rc 1), never kept silently and never deleted;
  * every change is printed old -> new (journald via the unit's identifier).

CLI `airuleset.py venue-bypass-sync [--apply]` (dry-run default). The timer
(`venue-bypass-sync.timer`, controller `airuleset` account only) runs
`--apply` hourly; `--apply` from a git worktree is refused (#1115).

Stdlib leaf; the HTTP layer is `cli_webterm_access.AccessClient` (injectable
transport, so tests run with no network).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import cli_venue_bypass_apps as table
import cli_webterm_access as wa

REPO_DIR = Path(__file__).resolve().parent
CONTROLLER_USER = "airuleset"
SERVICE_UNIT = "venue-bypass-sync.service"
TIMER_UNIT = "venue-bypass-sync.timer"


class SourceError(Exception):
    """The presenter source cannot be trusted this run: nothing is written."""


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def _apps_by_domain(apps):
    out = {}
    for app in apps or []:
        dom = wa._norm_domain(app.get("domain"))
        if dom:
            out[dom] = app
    return out


def _ip_set(policy):
    """The IPs of a policy's include list, or raise ValueError on any non-IP
    include (a bypass for `everyone` must never be mistaken for a venue)."""
    ips = set()
    for inc in policy.get("include") or []:
        ip = (inc.get("ip") or {}).get("ip") if isinstance(inc, dict) else None
        if not ip or len(inc) != 1:
            raise ValueError("non-IP include %r" % (inc,))
        ips.add(ip)
    return ips


def source_ips(apps_by_domain, source_hosts=table.SOURCE_HOSTS):
    """The union of the venue IPs in presenter's bypass policies. Raises
    SourceError when any source app or its bypass is missing or malformed."""
    ips = set()
    for host in source_hosts:
        app = apps_by_domain.get(wa._norm_domain(host))
        if app is None:
            raise SourceError("source Access app %s not found" % host)
        bypass = [p for p in app.get("policies") or [] if p.get("decision") == "bypass"]
        if not bypass:
            raise SourceError("source %s carries no bypass policy" % host)
        for pol in bypass:
            try:
                got = _ip_set(pol)
            except ValueError as e:
                raise SourceError("source %s bypass %r: %s"
                                  % (host, pol.get("name"), e)) from None
            if not got:
                raise SourceError("source %s bypass %r has no IP"
                                  % (host, pol.get("name")))
            ips |= got
    return ips


def bypass_payload(ips, precedence=table.BYPASS_PRECEDENCE):
    return {"name": table.BYPASS_POLICY_NAME, "decision": "bypass",
            "precedence": precedence,
            "include": [{"ip": {"ip": ip}} for ip in sorted(ips)],
            "exclude": [], "require": []}


def _free_precedence(app, wanted=table.BYPASS_PRECEDENCE):
    """`wanted` when no policy of the app holds it, else one past the highest
    (Cloudflare evaluates bypass before allow whatever its precedence)."""
    used = [p.get("precedence") for p in app.get("policies") or []
            if isinstance(p.get("precedence"), int)]
    return wanted if wanted not in used else max(used) + 1


# --------------------------------------------------------------------------- #
# Client calls (app-scoped policy endpoint: only the bypass is rewritten)
# --------------------------------------------------------------------------- #

def list_apps(client):
    st, body = client._call("GET", client._base("/apps?per_page=1000"))
    if not wa._ok(st, body):
        raise SourceError("cannot read Access apps (HTTP %s): %s"
                          % (st, wa._first_err(body)))
    apps = body.get("result") or []
    info = body.get("result_info") or {}
    total = info.get("total_count")
    if isinstance(total, int) and total > len(apps):
        raise SourceError("Access app listing is paged (%d of %d) — refusing "
                          "to judge a partial listing" % (len(apps), total))
    return apps


def _write_policy(client, app_id, policy_id, payload):
    if policy_id:
        return client._call("PUT", client._base("/apps/%s/policies/%s"
                                                % (app_id, policy_id)), payload)
    return client._call("POST", client._base("/apps/%s/policies" % app_id), payload)


def reconcile_app(client, app, ips, *, dry_run, out):
    """Make one table app's managed bypass equal `ips`. Returns True on ok."""
    host = app.get("domain")
    managed = [p for p in app.get("policies") or []
               if p.get("name") == table.BYPASS_POLICY_NAME]
    if len(managed) > 1:
        print("  [ERROR] %s: %d policies named %r — fix by hand, nothing written"
              % (host, len(managed), table.BYPASS_POLICY_NAME), file=out)
        return False
    if managed:
        pol = managed[0]
        if pol.get("decision") != "bypass":
            print("  [ERROR] %s: policy %r is decision %r, not bypass — untouched"
                  % (host, pol.get("name"), pol.get("decision")), file=out)
            return False
        try:
            cur = _ip_set(pol)
        except ValueError as e:
            print("  [ERROR] %s: managed bypass %s — untouched" % (host, e), file=out)
            return False
        if cur == ips:
            print("  [ok] bypass %s: unchanged (%s)" % (host, ", ".join(sorted(ips))), file=out)
            return True
        verb, pid, prec = "update", pol.get("id"), pol.get("precedence")
        old = ", ".join(sorted(cur))
    else:
        verb, pid, prec, old = "create", None, _free_precedence(app), "(none)"
    line = "%s %s: bypass %s -> %s" % (verb, host, old, ", ".join(sorted(ips)))
    if dry_run:
        print("  [would] " + line, file=out)
        return True
    st, body = _write_policy(client, app.get("id"), pid,
                             bypass_payload(ips, precedence=prec or table.BYPASS_PRECEDENCE))
    if not wa._ok(st, body):
        print("  [ERROR] %s FAILED (HTTP %s): %s" % (line, st, wa._first_err(body)),
              file=out)
        return False
    print("  [changed] " + line, file=out)
    return True


def sync(client, *, dry_run=True, apps_table=None, out=None):
    """Reconcile every owner-confirmed app. Returns the exit code (0 ok)."""
    out = out if out is not None else sys.stdout
    apps_table = table.VENUE_BYPASS_APPS if apps_table is None else apps_table
    print("venue-bypass-sync [%s]" % ("DRY-RUN (no writes)" if dry_run else "APPLY"),
          file=out)
    try:
        by_dom = _apps_by_domain(list_apps(client))
        ips = source_ips(by_dom)
    except SourceError as e:
        print("  [FAIL] presenter source unusable: %s — NOTHING written (an "
              "unreadable source never removes a bypass)" % e, file=out)
        return 1
    print("  source %s: %s" % (" + ".join(table.SOURCE_HOSTS),
                               ", ".join(sorted(ips))), file=out)
    rc = 0
    wanted = {wa._norm_domain(h) for h in apps_table}
    for dom, app in sorted(by_dom.items()):
        if dom in wanted:
            continue
        if any(p.get("name") == table.BYPASS_POLICY_NAME
               for p in app.get("policies") or []):
            print("  [REPORT] %s carries %r but is NOT in VENUE_BYPASS_APPS (no "
                  "owner confirmation on record) — left as is; the owner decides"
                  % (app.get("domain"), table.BYPASS_POLICY_NAME), file=out)
            rc = 1
    for host in sorted(apps_table):
        app = by_dom.get(wa._norm_domain(host))
        if app is None:
            print("  [ERROR] %s: no Access app — publish it first "
                  "(airuleset.py publish-app)" % host, file=out)
            rc = 1
            continue
        if not reconcile_app(client, app, ips, dry_run=dry_run, out=out):
            rc = 1
    return rc


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def load_client():
    """The Access client, token never printed. None + a stderr line on failure."""
    try:
        token = wa._load_token()
    except OSError as e:
        print("venue-bypass-sync: cannot read token %s: %s"
              % (wa.WEBTERM_ACCESS_TOKEN_FILE, e), file=sys.stderr)
        return None
    if not token:
        print("venue-bypass-sync: token file %s is empty"
              % wa.WEBTERM_ACCESS_TOKEN_FILE, file=sys.stderr)
        return None
    return wa.AccessClient(wa.WEBTERM_ACCESS_ACCOUNT_ID, token=token)


def refused_from_worktree(what, is_worktree_fn=None):
    """True (after the shared LOUD line) when a live write runs from a git
    worktree checkout (#1115/#972)."""
    import cli_drop_lanes as dl
    if (is_worktree_fn or dl._repo_is_worktree_checkout)():
        dl._refuse_worktree_live_write(what)
        return True
    return False


def cmd_venue_bypass_sync(args):
    dry_run = not getattr(args, "apply", False)
    if not dry_run and refused_from_worktree(
            "venue-bypass-sync --apply", getattr(args, "_is_worktree_fn", None)):
        return 1
    client = getattr(args, "_client", None) or load_client()
    if client is None:
        return 1
    return sync(client, dry_run=dry_run)


def register_parser(sub):
    p = sub.add_parser(
        "venue-bypass-sync",
        help="#1370: copy the venue egress IPs from presenter's bypass into every "
             "owner-confirmed app (VENUE_BYPASS_APPS); dry-run by default")
    p.add_argument("--apply", action="store_true",
                   help="write the changes (default: read and print the plan)")
    return p


# --------------------------------------------------------------------------- #
# Controller timer (the project-gh-token.timer pattern, #1190)
# --------------------------------------------------------------------------- #

_SERVICE = """\
# airuleset venue-bypass-sync — systemd --user service (managed by airuleset,
# #1370). Do not edit by hand; re-run `airuleset.py install`.

[Unit]
Description=airuleset venue-bypass-sync — keep owner-confirmed venue-LAN bypasses fresh
Documentation=https://github.com/zbynekdrlik/airuleset/issues/1370

[Service]
Type=oneshot
SyslogIdentifier=venue-bypass-sync
ExecStart=/usr/bin/env python3 {repo_dir}/airuleset.py venue-bypass-sync --apply
TimeoutStartSec=120
"""

_TIMER = """\
# airuleset venue-bypass-sync — systemd --user timer (managed by airuleset,
# #1370). presenter#977 refreshes the venue IPs hourly; follow it hourly.

[Unit]
Description=Copy venue egress IPs into owner-confirmed Access bypasses hourly

[Timer]
OnBootSec=5min
OnCalendar=*:20
AccuracySec=1min
Persistent=true
Unit={service}

[Install]
WantedBy=timers.target
"""


def setup_timer(*, box_class_fn=None, user=None, systemctl=None, unit_dir=None):
    """Install + enable the timer on the controller's `airuleset` account only
    (the Access token's home). Returns None (not this box), True, or False."""
    if box_class_fn is None:
        from watchdog.reaper import default_box_class as box_class_fn
    if user is None:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    try:
        if box_class_fn() != "controller" or user != CONTROLLER_USER:
            return None
    except Exception:
        return None
    if (systemctl is None or unit_dir is None) and os.environ.get("PYTEST_CURRENT_TEST"):
        return None     # a test driving cmd_install never writes the real units
    if systemctl is None:
        from cli_filedrop_watchdog import _run_systemctl as systemctl
    unit_dir = Path(unit_dir or Path.home() / ".config" / "systemd" / "user")
    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, text in ((SERVICE_UNIT, _SERVICE.format(repo_dir=REPO_DIR)),
                       (TIMER_UNIT, _TIMER.format(service=SERVICE_UNIT))):
        tmp = unit_dir / (".%s.%d" % (name, os.getpid()))
        tmp.write_text(text)
        os.replace(tmp, unit_dir / name)
    for argv in (["daemon-reload"], ["enable", "--now", TIMER_UNIT]):
        rc, _out, err = systemctl(argv)
        if rc != 0:
            print("  venue-bypass-sync timer: systemctl %s FAILED (rc=%d): %s"
                  % (" ".join(argv), rc, (err or "").strip()), file=sys.stderr)
            return False
    print("  venue-bypass-sync timer active (hourly, #1370)")
    return True


def maybe_setup_timer():
    """`setup_timer` for `airuleset.py install`: never raises."""
    try:
        return setup_timer()
    except Exception as e:  # noqa: BLE001 — install must go on, but loudly
        print("  venue-bypass-sync timer error (non-fatal): %s" % e, file=sys.stderr)
        return False
