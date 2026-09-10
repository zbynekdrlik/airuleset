"""airuleset — Cloudflare DNS record management (#983).

Idempotent ``ensure_record()`` for the ``newlevel.media`` zone: creates,
updates, or no-ops a DNS record based on name+type. Used by ``cmd_install``
on the controller to assert managed DNS records (the proxied CNAME for
``claudy.newlevel.media`` and the unproxied A record for ``ar.newlevel.media``
from #982) — replacing the manual-dashboard runbook step.

stdlib only (``urllib``/``json``/``os``), ZERO ``import airuleset``.
Injectable transport for offline tests (same seam as
``cli_webterm_access.AccessClient``).

Token: ``~/.secrets/cloudflare-newlevel`` (zone-scoped DNS token — the same
token that created every webterm/drop-gateway CNAME so far, archive lesson
#635). Read from the FILE at call time, NEVER printed, never logged.
"""
import json
import os
import urllib.error
import urllib.request

CF_API = "https://api.cloudflare.com/client/v4"
DNS_TOKEN_FILE = "~/.secrets/cloudflare-newlevel"

# Managed DNS records: each ``cmd_install`` run on the controller calls
# ``ensure_managed_records()`` which reconciles ALL entries below.
MANAGED_RECORDS = [
    {
        "zone": "newlevel.media",
        "name": "claudy.newlevel.media",
        "type": "CNAME",
        "content": "f85ea304-920b-4ba4-96bc-a68001ce6fb4.cfargotunnel.com",
        "proxied": True,
        "comment": "airuleset-managed (#983) — claudy dashboard tunnel",
        # Gate: the proxied CNAME must NOT be created until the Cloudflare
        # Access app exists — otherwise the dashboard is world-readable
        # until `webterm-access --apply` runs (RED-1 review finding).
        "requires_access_hostname": "claudy.newlevel.media",
    },
    {
        "zone": "newlevel.media",
        "name": "ar.newlevel.media",
        "type": "A",
        "content": "159.69.209.249",
        "proxied": False,
        "comment": "airuleset-managed (#982/#985) — owner break-glass SSH (public IP)",
    },
]


class DnsClient:
    """Thin Cloudflare DNS API client with injectable transport for tests."""

    def __init__(self, token=None, transport=None):
        self._token = token
        self._transport = transport or self._http
        self.calls = []

    def _http(self, method, path, body):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            CF_API + path, data=data, method=method,
            headers={"Authorization": "Bearer " + (self._token or ""),
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except Exception:
                return e.code, {"success": False,
                                "errors": [{"message":
                                            e.read().decode("utf-8", "replace")[:300]}]}

    def _call(self, method, path, body=None):
        self.calls.append((method, path))
        return self._transport(method, path, body)

    def get_zone_id(self, zone_name):
        """Return ``(zone_id_or_None, (status, body))``."""
        st, d = self._call("GET", "/zones?name=%s" % zone_name)
        if st != 200 or not d.get("success"):
            return None, (st, d)
        results = d.get("result") or []
        if not results:
            return None, (st, d)
        return results[0].get("id"), (st, d)

    def find_record(self, zone_id, name, rtype):
        """Return ``(record_dict_or_None, (status, body))``."""
        path = "/zones/%s/dns_records?name=%s&type=%s" % (zone_id, name, rtype)
        st, d = self._call("GET", path)
        if st != 200 or not d.get("success"):
            return None, (st, d)
        results = d.get("result") or []
        if not results:
            return None, (st, d)
        return results[0], (st, d)

    def create_record(self, zone_id, payload):
        return self._call("POST", "/zones/%s/dns_records" % zone_id, payload)

    def update_record(self, zone_id, record_id, payload):
        return self._call("PUT",
                          "/zones/%s/dns_records/%s" % (zone_id, record_id),
                          payload)


def _ok(status, body):
    return status in (200, 201) and bool(body.get("success"))


def _first_err(body):
    errs = (body or {}).get("errors") or []
    if errs and isinstance(errs, list):
        return errs[0].get("message", str(errs[0]))
    return str(body)[:200]


def _load_token(path=DNS_TOKEN_FILE):
    """Read the Cloudflare DNS token from disk. Value NEVER printed."""
    with open(os.path.expanduser(path)) as f:
        return f.read().strip()


def _records_match(existing, wanted):
    """True when the existing record already matches the wanted state
    (content + proxied). A match = no-op (idempotent)."""
    if existing.get("content") != wanted.get("content"):
        return False
    # Cloudflare returns proxied as a bool; the wanted dict also uses bool.
    if existing.get("proxied") != wanted.get("proxied"):
        return False
    return True


def ensure_record(client, zone_name, name, rtype, content, proxied,
                  comment="", dry_run=True):
    """Idempotent DNS record upsert.

    Returns ``{ok, action, record_id, error}``.
    ``action`` is one of: ``"created"``, ``"updated"``, ``"unchanged"``,
    ``"would_create"``, ``"would_update"`` (dry-run variants).
    """
    result = {"ok": False, "action": None, "record_id": None, "error": None}

    zone_id, (st, body) = client.get_zone_id(zone_name)
    if zone_id is None:
        result["error"] = ("cannot find zone %s (HTTP %s): %s"
                           % (zone_name, st, _first_err(body)))
        return result

    existing, (st, body) = client.find_record(zone_id, name, rtype)
    if st != 200 or not body.get("success"):
        result["error"] = ("cannot list DNS records for %s (HTTP %s): %s"
                           % (name, st, _first_err(body)))
        return result

    payload = {
        "type": rtype,
        "name": name,
        "content": content,
        "proxied": proxied,
        "comment": comment,
    }
    # CNAME records must NOT carry a ttl when proxied (Cloudflare auto-sets it).
    if not proxied:
        payload["ttl"] = 1  # 1 = auto

    if existing is None:
        # Record absent -> create.
        if dry_run:
            result["ok"] = True
            result["action"] = "would_create"
            return result
        st, body = client.create_record(zone_id, payload)
        if not _ok(st, body):
            result["error"] = ("create record failed (HTTP %s): %s"
                               % (st, _first_err(body)))
            return result
        result["ok"] = True
        result["action"] = "created"
        result["record_id"] = (body.get("result") or {}).get("id")
        return result

    # Record exists -- check if it matches.
    if _records_match(existing, {"content": content, "proxied": proxied}):
        result["ok"] = True
        result["action"] = "unchanged"
        result["record_id"] = existing.get("id")
        return result

    # Record exists but differs -> update.
    if dry_run:
        result["ok"] = True
        result["action"] = "would_update"
        result["record_id"] = existing.get("id")
        return result
    st, body = client.update_record(zone_id, existing["id"], payload)
    if not _ok(st, body):
        result["error"] = ("update record failed (HTTP %s): %s"
                           % (st, _first_err(body)))
        return result
    result["ok"] = True
    result["action"] = "updated"
    result["record_id"] = existing.get("id")
    return result


def _access_app_exists(hostname, check_fn=None):
    """Check whether a Cloudflare Access app exists for ``hostname``.
    ``check_fn`` is injectable for tests; the default loads the Access
    token and probes the real API."""
    if check_fn is not None:
        return check_fn(hostname)
    try:
        import cli_webterm_access as acc
        token = acc._load_token()
        cl = acc.AccessClient(acc.WEBTERM_ACCESS_ACCOUNT_ID, token=token)
        app, _ = cl.find_app_by_domain(hostname)
        return app is not None
    except Exception:
        return False


def ensure_managed_records(dry_run=True, token_path=DNS_TOKEN_FILE,
                           client=None, check_access_fn=None):
    """Reconcile ALL ``MANAGED_RECORDS``. Returns ``(all_ok, results_list)``.
    Called from ``cmd_install`` on the controller.
    ``client`` is injectable for offline tests (skip token loading).
    ``check_access_fn(hostname) -> bool`` is injectable to test the
    Access-app gate (RED-1) without hitting the real API."""
    if client is None:
        try:
            token = _load_token(token_path)
        except OSError as e:
            return False, [{"ok": False, "error":
                            "cannot read DNS token %s: %s" % (token_path, e)}]
        if not token:
            return False, [{"ok": False, "error":
                            "DNS token file %s is empty" % token_path}]
        client = DnsClient(token=token)

    results = []
    all_ok = True
    for rec in MANAGED_RECORDS:
        # RED-1 gate: a record that requires an Access app must NOT be
        # created until the app exists — otherwise the origin is public
        # and unauthenticated until `webterm-access --apply` runs.
        access_host = rec.get("requires_access_hostname")
        if access_host and not dry_run:
            if not _access_app_exists(access_host, check_access_fn):
                r = {"ok": False, "name": rec["name"],
                     "action": None, "record_id": None,
                     "error": ("Access app for %s not found — run "
                               "`airuleset.py webterm-access --apply` "
                               "FIRST, then re-run install" % access_host)}
                results.append(r)
                all_ok = False
                continue
        r = ensure_record(
            client, rec["zone"], rec["name"], rec["type"],
            rec["content"], rec["proxied"], rec.get("comment", ""),
            dry_run=dry_run)
        r["name"] = rec["name"]
        results.append(r)
        if not r["ok"]:
            all_ok = False
    return all_ok, results
