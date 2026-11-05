"""#1131 — controller-fronted SERVICE routes: the ONE declared table and its
pure helpers. A stdlib leaf that `cli_drop_lanes` re-exports, so callers keep
reading `cli_drop_lanes.CONTROLLER_SERVICE_ROUTES`.
"""
import re

# ---------------------------------------------------------------------------
# #1131: controller-fronted SERVICE routes — an owner-box service (not a drop
# lane) published on the controller tunnel behind Cloudflare Access.
# ---------------------------------------------------------------------------

# The ONE Access session value for drop lanes AND service routes (#1115 reopen:
# 720h like the webterm apps). cli_drop_gateway re-exports it.
DROP_ACCESS_SESSION = "720h"

# ONE declared table (#1131). Each row is ``{hostname, origin, name,
# allowed_emails, session_duration}``. It is read by TWO consumers:
#   - the controller ingress render (``controller_service_ingress_rules`` via
#     ``cli_webterm._setup_controller_webterm``): ``hostname -> origin``;
#   - the go-live reconcile (``cli_drop_golive.reconcile_service_routes``):
#     the Access app FIRST, then a create-only proxied CNAME to the controller
#     tunnel.
# An INCOMPLETE row is PENDING in both: no ingress rule, no Access, no CNAME
# (fail-closed). Adding a future owner dashboard means adding ONE row here.
# Deliberately NOT a drop lane: drop lanes are per-account filedrop with port
# caches + go-live markers, and a service has none of that (design comment on
# the ticket, Approach 2 rejected).
CONTROLLER_SERVICE_ROUTES = (
    # fleet-backup dashboard (dev1 fleetbackup-web.service, read-only, strict
    # Host allowlist; fleet-backup issue 9). Owner-only.
    {"hostname": "backup.newlevel.media",
     "origin": "http://100.104.8.125:8795",
     "name": "fleet-backup dashboard",
     "allowed_emails": ["drlik.zbynek@gmail.com"],
     "session_duration": DROP_ACCESS_SESSION},
    # Vaultwarden restore copy on this box (rootless Podman under the bw account,
    # #1290). Owner-only while it is a test copy. Native Bitwarden apps cannot
    # pass Access, so the final bw.newlevel.media needs a route without it.
    {"hostname": "bwnew.newlevel.media",
     "origin": "http://127.0.0.1:8095",
     "name": "Vaultwarden restore copy",
     "allowed_emails": ["drlik.zbynek@gmail.com"],
     "session_duration": DROP_ACCESS_SESSION},
)

_SERVICE_ROUTE_KEYS = ("hostname", "origin", "name", "allowed_emails",
                       "session_duration")
# ONE lowercase DNS label directly under the zone. The Universal SSL wildcard
# covers exactly one level, and the hostname is spliced into the tunnel YAML, so
# nothing else is publishable.
_SERVICE_ROUTE_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                                    r"\.newlevel\.media")
_SERVICE_ROUTE_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def service_route_complete(route):
    """True iff ``route`` is a complete, publishable spec (#1131). Every key must
    be present and non-empty. ``hostname`` must be one lowercase label under
    ``newlevel.media``. ``origin`` must be an ``http(s)://`` URL.
    ``allowed_emails`` must be a non-empty list of e-mail addresses. An
    incomplete row is PENDING and must never get ingress, Access or DNS. An
    Access app with an empty include list is an open door, so an empty list
    fails too."""
    if not isinstance(route, dict):
        return False
    for key in _SERVICE_ROUTE_KEYS:
        if not route.get(key):
            return False
    if not _SERVICE_ROUTE_HOST_RE.fullmatch(str(route["hostname"])):
        return False
    if not str(route["origin"]).startswith(("http://", "https://")):
        return False
    emails = route["allowed_emails"]
    if isinstance(emails, str) or not isinstance(emails, (list, tuple)):
        return False
    return all(isinstance(e, str) and _SERVICE_ROUTE_EMAIL_RE.fullmatch(e)
               for e in emails)


def service_route_access_spec(route):
    """The ``cli_webterm_access.apply_profile`` spec for a route. It has the
    same shape as a ``DROP_ACCESS_APPS`` entry (the origin is not part of
    Access)."""
    return {"hostname": route["hostname"], "name": route["name"],
            "allowed_emails": list(route["allowed_emails"]),
            "session_duration": route["session_duration"]}


def controller_service_ingress_rules(routes=None):
    """``[(hostname, origin), ...]`` for every COMPLETE service route (#1131).
    The controller tunnel's multi-ingress config appends these. An incomplete
    route gets no rule, which mirrors the slice E PENDING drop lane. A host that
    appears twice with different origins raises (the same conflict check as the
    drop rules). ``routes`` is injectable and defaults to
    ``CONTROLLER_SERVICE_ROUTES``."""
    rules = []
    seen = {}
    for route in (CONTROLLER_SERVICE_ROUTES if routes is None else routes):
        if not service_route_complete(route):
            continue
        host, origin = route["hostname"], route["origin"]
        prev = seen.get(host)
        if prev is not None:
            if prev != origin:
                raise ValueError("conflicting controller service route for %s: "
                                 "%s vs %s" % (host, prev, origin))
            continue
        seen[host] = origin
        rules.append((host, origin))
    return rules
