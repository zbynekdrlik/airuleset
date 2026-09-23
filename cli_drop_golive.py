"""airuleset — controller-side drop-lane GO-LIVE reconcile (#1115 slice B).

Slice A generates the drop-lane registry from the fleet (`cli_drop_lanes`); this
leaf makes those lanes LIVE from the ONE box that holds the tokens — the
controller — at install time, replacing the manual per-account
`drop-gateway --apply` step.

Today (pre-slice-B) go-live is manual and split across boxes: the DNS CNAME is a
"manual runbook" line `cmd_drop_gateway` only PRINTS, the Access app is
reconciled only when `--apply` runs AS the lane account (with a token only the
controller holds — so david3/david4 never got theirs, #1111 close note), and the
marker is written per-account by hand. Every generated fleet lane therefore has a
`DROP_LANES` entry but no live DNS/Access/marker, so its URLs still fall back to
tailscale — the mixed state the owner banned (22.9.).

This leaf, called from the controller's `install` step
(`cli_webterm._setup_controller_webterm`, which already renders
`controller-webterm.yml`), reconciles PER CONTROLLER-TOPOLOGY LANE:

  1. **DNS** — a proxied CNAME `<host> -> <tunnel uuid>.cfargotunnel.com` via the
     zone API (`~/.secrets/cloudflare-newlevel`). CREATE-IF-MISSING ONLY: an
     existing identical record is a no-op; a record pointing ELSEWHERE is a LOUD
     line, UNTOUCHED (never deleted/overwritten — a foreign record is never our
     record to clobber).
  2. **Access** — the app + include list via the Access API
     (`~/.secrets/cloudflare-newlevel-access`), create/update through
     `cli_webterm_access.apply_profile` with the SAME `DROP_ACCESS_APPS` include
     lists `_reconcile_access` uses today. FAIL-CLOSED: an `access=True` lane with
     no `DROP_ACCESS_APPS` spec is NEVER marked live (it could otherwise serve
     unprotected).
  3. **journal** — every change is a printed line; a failure is a LOUD summary
     line `drop-lanes: <host> DNS/Access FAILED …`, never silent, and NEVER a
     whole-install abort.

A lane is **LIVE** when DNS ok AND (Access not required OR Access ok). The set of
live lanes (`{host: port}`) is written to a controller-side cache
`~/.claude/drop-golive.json` that `push`'s deploy loop
(`cli_remote._deploy_to_all_remotes`) reads to decide which target ACCOUNTS get
the go-live marker `~/.cloudflared/airuleset-drop.conf` (written over the deploy
ssh session it already opens, reusing `cli_drop_gateway.write_drop_marker`'s
O_NOFOLLOW + 0600 write). A lane that is not live gets NO marker (fail-closed).

Self-contained leaf (#433): imports `cli_drop_gateway` (the lane facts + marker
I/O), `cli_cloudflare_dns` (the zone client) and `cli_webterm_access` (the Access
client) LAZILY inside the functions that need them, so there is no import-order
coupling and a test can inject fakes. The default clients hit the real API only
when built from the on-disk tokens; every reconcile path takes an injectable
client so tests run with NO network and NO ssh, and the worktree lane makes NO
live change.
"""
import json
import os
import shlex
import sys
from pathlib import Path

# The go-live cache the deploy loop reads: {host: port} for every LIVE lane.
GOLIVE_CACHE = Path.home() / ".claude" / "drop-golive.json"

# Cloudflare proxied CNAME target suffix for a named tunnel.
_CFARGO_SUFFIX = ".cfargotunnel.com"


def _cname_content(tunnel_uuid):
    """The proxied-CNAME content for a tunnel: ``<uuid>.cfargotunnel.com``."""
    return "%s%s" % (tunnel_uuid, _CFARGO_SUFFIX)


def ensure_cname_create_only(dns_client, zone, name, content, comment,
                             dry_run=True):
    """Idempotent CREATE-ONLY CNAME upsert (#1115 slice B).

    Unlike ``cli_cloudflare_dns.ensure_record`` (which UPDATEs a differing
    record), this NEVER overwrites: a record already pointing at ``content`` is a
    no-op; a record pointing ELSEWHERE is reported as a CONFLICT and left
    UNTOUCHED (a foreign/hand-made record is never ours to clobber). The proxied
    CNAME carries no ttl (Cloudflare auto-sets it behind the proxy).

    Returns ``{ok, action, record_id, error}``; ``action`` is one of
    ``created`` / ``unchanged`` / ``would_create`` / ``conflict``. A conflict or
    an API error is ``ok=False`` (the lane is then NOT live — fail-closed).
    """
    import cli_cloudflare_dns as dns
    result = {"ok": False, "action": None, "record_id": None, "error": None}

    zone_id, (st, body) = dns_client.get_zone_id(zone)
    if zone_id is None:
        result["error"] = ("cannot find zone %s (HTTP %s): %s"
                           % (zone, st, dns._first_err(body)))
        return result

    existing, (st, body) = dns_client.find_record(zone_id, name, "CNAME")
    if st != 200 or not body.get("success"):
        result["error"] = ("cannot list DNS records for %s (HTTP %s): %s"
                           % (name, st, dns._first_err(body)))
        return result

    if existing is not None:
        cur = (existing.get("content") or "").rstrip(".").casefold()
        want = content.rstrip(".").casefold()
        if cur == want:
            result["ok"] = True
            result["action"] = "unchanged"
            result["record_id"] = existing.get("id")
            return result
        # Points elsewhere — LOUD, untouched (never overwrite an unknown record).
        result["action"] = "conflict"
        result["error"] = ("CNAME %s already points to %r, not %r — left "
                           "UNTOUCHED (refusing to overwrite an unknown record)"
                           % (name, existing.get("content"), content))
        return result

    # Absent -> create.
    if dry_run:
        result["ok"] = True
        result["action"] = "would_create"
        return result
    payload = {"type": "CNAME", "name": name, "content": content,
               "proxied": True, "comment": comment}
    st, body = dns_client.create_record(zone_id, payload)
    if not dns._ok(st, body):
        result["error"] = ("create CNAME failed (HTTP %s): %s"
                           % (st, dns._first_err(body)))
        return result
    result["ok"] = True
    result["action"] = "created"
    result["record_id"] = (body.get("result") or {}).get("id")
    return result


def reconcile_access_for_lane(lane, dry_run=True, access_client=None,
                             access_specs=None):
    """Reconcile the Cloudflare Access app + include list for one lane.

    Returns ``(ok, action, msg)``. A token-only lane (``access is False``) needs
    no Access and is ``(True, "none", …)``. An ``access=True`` lane with NO
    ``DROP_ACCESS_APPS`` spec is FAIL-CLOSED ``(False, …)`` — it must never be
    marked live without its promised Access protection. The include list is the
    SAME one ``cli_drop_gateway._reconcile_access`` uses (``DROP_ACCESS_APPS``),
    reconciled via ``cli_webterm_access.apply_profile``. ``access_client`` is
    injectable for tests (no network); when None a real client is built from the
    on-disk token. The token value is NEVER included in ``msg``.
    """
    import cli_webterm_access as acc
    if not lane.access:
        return True, "none", "no Access (token-only TLS lane)"
    specs = access_specs
    if specs is None:
        import cli_drop_gateway as dg
        specs = dg.DROP_ACCESS_APPS
    spec = specs.get(lane.host)
    if spec is None:
        return (False, "no-spec",
                "Access lane but no DROP_ACCESS_APPS spec for %s — NOT marking "
                "live (fail-closed)" % lane.host)

    client = access_client
    if client is None:
        try:
            token = acc._load_token()
        except OSError as e:
            return (False, "token-error",
                    "Access token %s unreadable (%s)"
                    % (acc.WEBTERM_ACCESS_TOKEN_FILE, e))
        if not token:
            return False, "token-error", "Access token file empty"
        client = acc.AccessClient(acc.WEBTERM_ACCESS_ACCOUNT_ID, token=token)

    res = acc.apply_profile(client, spec, dry_run=dry_run)
    if res.get("error"):
        return False, "error", "Access ERROR: %s" % res["error"]
    verb = "(dry-run)" if dry_run else "applied"
    return (True, "ok",
            "Access %s: %s" % (verb, "; ".join(res.get("actions") or []) or "-"))


def _controller_lanes(drop_lanes):
    """The controller-topology lanes (the ones the controller tunnel fronts and
    whose DNS/Access the controller reconciles), sorted by (node, user)."""
    return [((n, u), lane) for (n, u), lane in sorted(drop_lanes.items())
            if lane.topology == "controller" and lane.origin_host]


def _resolve_access_specs(access_specs):
    """The Access-spec dict to use — the injected one, else DROP_ACCESS_APPS."""
    if access_specs is not None:
        return access_specs
    import cli_drop_gateway as dg
    return dg.DROP_ACCESS_APPS


def _lane_go_live_eligible(lane, access_specs):
    """A lane is ELIGIBLE for go-live iff it is token-only (access=False) OR it is
    an access lane that ALREADY has a DROP_ACCESS_APPS spec (#1115 slice B review,
    both reviewers): an access lane with NO spec must NEVER have its DNS CNAME
    created — a proxied CNAME with no Access app in front is publicly routable and
    UNPROTECTED (the #983 RED-1 class). Such a lane is PENDING go-live (its spec
    is an owner-provided go-live data step), NOT a failure, and gets neither DNS
    nor a marker until the spec lands."""
    if not lane.access:
        return True
    return access_specs.get(lane.host) is not None


def reconcile_drop_lanes(dry_run=True, drop_lanes=None, access_specs=None,
                         dns_client=None, access_client=None, zone="newlevel.media",
                         issue="1115", out=None):
    """Reconcile DNS + Access for every controller-topology drop lane (#1115 B).

    Returns ``(all_ok, results, live)`` where ``results`` is a list of per-lane
    dicts ``{host, port, node, user, dns_action, access_action, live, error}``
    and ``live`` is ``{host: port}`` for every LIVE lane (DNS ok AND (Access not
    required OR Access ok)). Every change / failure is printed to ``out``
    (default stdout); a per-lane failure is a LOUD ``drop-lanes: <host>
    DNS/Access FAILED …`` line and sets ``all_ok=False`` but NEVER raises — the
    caller (the controller install step) must never abort the whole install.

    Dependency-injected so tests run offline: ``drop_lanes`` (default
    ``cli_drop_gateway.DROP_LANES``), ``access_specs`` (default
    ``DROP_ACCESS_APPS``), ``dns_client`` / ``access_client`` (default real
    clients built from the on-disk tokens). In ``dry_run`` NOTHING is written
    (DNS reports would_create, Access issues only GETs) and the go-live cache is
    NOT touched.

    Idempotent for the OBSERVABLE state: a 2nd run creates NO DNS record (an
    existing identical CNAME is ``unchanged``, no POST/PUT) and marks the same
    lanes live. The Access side is CONVERGENT rather than a strict no-op — a
    present app is re-PUT with the same include list every run (mirrors
    ``cli_drop_gateway._reconcile_access``), which is harmless.
    """
    out = out if out is not None else sys.stdout
    if drop_lanes is None:
        import cli_drop_gateway as dg
        drop_lanes = dg.DROP_LANES

    dns_c = dns_client
    if dns_c is None and not dry_run:
        import cli_cloudflare_dns as dns
        try:
            token = dns._load_token()
        except OSError as e:
            print("drop-lanes: cannot read DNS token %s (%s) — SKIPPING go-live"
                  % (dns.DNS_TOKEN_FILE, e), file=out)
            return False, [], {}
        if not token:
            print("drop-lanes: DNS token file empty — SKIPPING go-live", file=out)
            return False, [], {}
        dns_c = dns.DnsClient(token=token)
    elif dns_c is None:
        # dry-run with no injected client: build a token-less client that only
        # ever issues GETs (would_create never POSTs).
        import cli_cloudflare_dns as dns
        try:
            dns_c = dns.DnsClient(token=dns._load_token())
        except OSError:
            dns_c = dns.DnsClient(token="")

    specs = _resolve_access_specs(access_specs)
    results = []
    live = {}
    pending = []
    all_ok = True
    comment = "airuleset #%s — drop lane go-live (controller tunnel)" % issue

    for (node, user), lane in _controller_lanes(drop_lanes):
        row = {"host": lane.host, "port": lane.port, "node": node, "user": user,
               "dns_action": None, "access_action": None, "live": False,
               "pending": False, "error": None}

        # PENDING (not a failure): an access lane whose Access spec has not been
        # provisioned yet — never create its DNS CNAME (that would expose an
        # unprotected public hostname), never mark it live. Reported once as a
        # summary line below, not a per-lane FAILED line (which would be 12 lines
        # of false-failure on EVERY controller push until the specs land).
        if not _lane_go_live_eligible(lane, specs):
            row["pending"] = True
            row["access_action"] = "pending-no-spec"
            row["dns_action"] = "skipped"
            pending.append(lane.host)
            results.append(row)
            continue

        try:
            # Access FIRST (the go-live gate) — for an access lane the DNS CNAME
            # is created ONLY once Access is confirmed, so the hostname is never
            # publicly routable without its Access app (the #983 RED-1 ordering).
            acc_ok, acc_action, acc_msg = reconcile_access_for_lane(
                lane, dry_run=dry_run, access_client=access_client,
                access_specs=specs)
            row["access_action"] = acc_action
            if lane.access and not acc_ok:
                all_ok = False
                row["dns_action"] = "skipped (access gate)"
                row["error"] = acc_msg
                print("  drop-lanes: %s DNS/Access FAILED (Access=%s): %s — DNS "
                      "NOT created (fail-closed)"
                      % (lane.host, acc_action, acc_msg), file=out)
                results.append(row)
                continue

            dns_res = ensure_cname_create_only(
                dns_c, zone, lane.host, _cname_content(lane.tunnel_uuid),
                comment, dry_run=dry_run)
            row["dns_action"] = dns_res["action"]
            if dns_res["ok"]:
                row["live"] = True
                live[lane.host] = lane.port
                print("  drop-lanes: %s DNS=%s Access=%s -> LIVE"
                      % (lane.host, dns_res["action"], acc_msg), file=out)
            else:
                all_ok = False
                row["error"] = dns_res.get("error")
                print("  drop-lanes: %s DNS/Access FAILED (DNS=%s): %s"
                      % (lane.host, dns_res["action"], dns_res.get("error")),
                      file=out)
        except Exception as e:  # noqa: BLE001 — a transport-level error (URLError/
            # timeout: tunnel down, DNS-server unreachable) must NOT abort go-live
            # for EVERY OTHER lane, and must stay a LOUD per-lane line (the module
            # contract). Record this one lane as failed, keep going.
            all_ok = False
            row["error"] = "%s: %s" % (type(e).__name__, e)
            print("  drop-lanes: %s DNS/Access FAILED (%s): %s"
                  % (lane.host, type(e).__name__, e), file=out)
        results.append(row)

    if pending:
        print("  drop-lanes: %d lane(s) PENDING go-live (no DROP_ACCESS_APPS "
              "spec yet, no DNS/marker): %s"
              % (len(pending), ", ".join(sorted(pending))), file=out)

    if not dry_run:
        try:
            write_golive_live_hosts(live)
            print("  drop-lanes: wrote %d live lane(s) to %s"
                  % (len(live), GOLIVE_CACHE), file=out)
        except Exception as e:  # noqa: BLE001 — best-effort; never fail install
            print("  ⚠ drop-lanes: go-live cache write failed (non-fatal): %r"
                  % e, file=out)
    return all_ok, results, live


def reconcile_and_report(dry_run=False, out=None):
    """Controller install-step entry point (#1115 slice B).

    Runs ONLY on the controller as the `airuleset` account (the box that holds
    both Cloudflare tokens); a no-op everywhere else. Reconciles every
    controller-topology lane's DNS + Access, writes the go-live cache, and prints
    a LOUD summary line per failure. NEVER raises and NEVER fails the install —
    a reconcile failure is reported, the install continues (the design's
    "never a whole-install abort"). Returns True when nothing failed (or it was a
    benign no-op off the controller), False when a lane's reconcile failed.
    """
    out = out if out is not None else sys.stdout
    try:
        from watchdog.reaper import default_box_class
    except Exception:
        return True  # cannot classify the box — benign no-op
    import pwd
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return True
    if default_box_class() != "controller" or user != "airuleset":
        return True  # not the controller/airuleset — nothing to reconcile

    mode = "DRY-RUN" if dry_run else "APPLY"
    print("drop-lanes go-live [%s] (controller):" % mode, file=out)
    all_ok, _results, live = reconcile_drop_lanes(dry_run=dry_run, out=out)
    if not all_ok:
        print("  drop-lanes: one or more lanes FAILED go-live (see the lines "
              "above) — install continues, those lanes stay private-only",
              file=out)
    return all_ok


# --------------------------------------------------------------------------- #
# go-live cache (controller-side) — read by the deploy loop.
# --------------------------------------------------------------------------- #

def read_golive_live_hosts(path=None):
    """The go-live cache as ``{host: port}`` (#1115 slice B). Robust: a missing /
    unreadable / malformed file, or a non-int port, yields ``{}`` (the deploy
    loop then writes NO marker). Never raises."""
    p = Path(path) if path is not None else GOLIVE_CACHE
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            out[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def write_golive_live_hosts(live, path=None):
    """Write the go-live cache atomically (#1115 slice B). ``live`` is
    ``{host: port}``; a non-int port is skipped. Returns the path written."""
    p = Path(path) if path is not None else GOLIVE_CACHE
    clean = {}
    for k, v in (live or {}).items():
        try:
            clean[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n")
    tmp.replace(p)
    return p


# --------------------------------------------------------------------------- #
# marker distribution over the deploy ssh session (push).
# --------------------------------------------------------------------------- #

def marker_snippet_for_entry(remote, live_hosts, drop_lanes=None):
    """A pure-shell fragment that writes THIS target account's go-live marker,
    chained into the deploy ssh command RIGHT AFTER the filedrop-port probe
    (#1115 slice B). Returns ``""`` when this account has no LIVE lane (no marker
    is written — fail-closed: a lane that did not go live never gets a marker).

    The marker is written by the target's OWN
    ``cli_drop_gateway.write_drop_marker`` (the deploy shell is already cd'd to
    the repo, so ``import cli_drop_gateway`` resolves), reusing its O_NOFOLLOW +
    0600 write verbatim. host + port reach python as ``argv`` (no python-level
    injection) AND the host is ``shlex.quote``-d before it is spliced into the
    shell command string, so a hostname with a shell metachar cannot break out
    (the values are the git-controlled DROP_LANES registry, but the quoting makes
    the safety a property of the code, not of the data). The group ends with
    ``|| true`` and contains NO ``exit``, so it ALWAYS exits 0 and the ``&& …``
    chain to the later gating post-checks continues (mirrors
    ``filedrop_port_probe_snippet``'s exit-0/exit-free contract — it must precede
    the ``{ … }`` post-check groups that ``exit`` the remote shell).
    """
    if drop_lanes is None:
        import cli_drop_gateway as dg
        drop_lanes = dg.DROP_LANES
    node = _nodename_for_entry(remote)
    user = remote.get("user", "")
    lane = drop_lanes.get((node, user))
    if lane is None or lane.host not in (live_hosts or {}):
        return ""
    port = live_hosts[lane.host]
    return (
        '{ python3 -c '
        '"import cli_drop_gateway,sys; '
        'cli_drop_gateway.write_drop_marker(sys.argv[1], int(sys.argv[2]))" '
        '%s %d 2>/dev/null || true; }' % (shlex.quote(lane.host), int(port))
    )


def _nodename_for_entry(remote):
    """The box nodename for a fleet entry — the SAME derivation the ingress
    consumer + the push filedrop-port harvest use (`cli_drop_lanes`), so the
    marker snippet's lane lookup can never disagree with the DROP_LANES key."""
    import cli_drop_lanes
    return cli_drop_lanes._nodename_for_entry(remote)
