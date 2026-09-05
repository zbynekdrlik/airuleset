"""airuleset webterm — Cloudflare Access (Zero Trust) e-mail OTP gate (#612).

Owner directive 2026-08-22 (comment 5380361224): REPLACE the per-developer
webterm PASSWORD with a Cloudflare Access application in FRONT of the public
hostname — email one-time-PIN verification at the Cloudflare edge. Authorization
becomes a DECLARED LIST OF ALLOWED E-MAILS, not a secret anybody stores or
relays. Adding a person is a ONE-LINE change to
`allowed_emails` + `airuleset.py webterm-access --apply`.

This leaf OWNS three things:
  * the declared config `WEBTERM_ACCESS_APPS` (profile -> hostname + allowed_emails),
  * an idempotent apply over the Cloudflare Access API (upsert the self-hosted
    app + its single Allow policy — GET-then-decide create-vs-update),
  * the CLI `airuleset.py webterm-access [--dry-run|--apply] [--profile P]`.

The GATEWAY behind the tunnel trusts the Cloudflare-injected identity header
(`Cf-Access-Authenticated-User-Email`) — see cli_webterm_gateway.py's
`--trust-access-header` mode, which retires the password entirely.

stdlib only (urllib/json/os), ZERO `import airuleset` — a leaf like
cli_webterm_profiles.py, so the connect path stays import-light.

SECURITY NOTE (honest, not hidden): pure stdlib has no RSA, so we do NOT
cryptographically validate the Access JWT (`Cf-Access-Jwt-Assertion`) at the
origin. The boundary is: Access-at-edge (email OTP) + Cloudflare STRIPPING
client-supplied `Cf-*` headers before setting authentic ones + the gateway/ttyd
origins being reachable ONLY by the account. #663 CLOSED the former loopback
floor: on the SHARED subdev box a TCP `127.0.0.1:<port>` origin was reachable by
EVERY local account — a peer account could forge this trust header at another
lane's gateway OR reach its auth-less ttyd directly (both live-reproduced, #663).
The gateway and ttyd now bind mode-0700 UNIX-domain sockets in the account's
`/run/user/<uid>` runtime dir (cloudflared `service: unix:<path>`, ttyd
`-i <sock>`, gateway `asyncio.start_unix_server`/`open_unix_connection`) instead
of TCP loopback, so FILESYSTEM PERMISSIONS on the 0700 dir are the account
boundary: only the account (its own cloudflared) can traverse to either socket,
and the trust-header residual becomes a same-account no-escalation (an account
"forging" a header into its OWN socket already owns that shell). The remaining
stdlib residual — no RS256 JWT verification — is unrelated to local reachability
and is superseded on that front by the socket boundary (JWT would protect only
the gateway hop, never the direct-ttyd vector #663 also closed).
"""
import json
import os
import sys
import urllib.error
import urllib.request

# NEWLEVELMEDIA account — owns the newlevel.media zone + the david tunnel.
# STEP-0 #612 (CORRECTED after a live probe): the existing account token
# `~/.secrets/cloudflare-account-tokens` can READ Access apps/idps (GET 200) but
# CANNOT create/edit them — a live `POST /access/apps` returns 403
# `auth.forbidden` (the earlier PUT-to-a-bogus-UUID → 404 was a FALSE POSITIVE;
# Cloudflare returns 404 for a PUT to a non-existent app before asserting create
# authz). So `--apply` needs a DEDICATED token with the permission group
# `Account > Access: Apps and Policies > Edit` on this account (owner-provided
# via the vault channel `airuleset.py secret request`, persisted to the file
# below). Until that file exists, `webterm-access` cannot apply — that is the one
# blocked go-live step, surfaced honestly (never a password/read-token fallback).
CF_API = "https://api.cloudflare.com/client/v4"
WEBTERM_ACCESS_ACCOUNT_ID = "8f3efbc0edbe05bd6fdcab10cd63876a"
WEBTERM_ACCESS_TOKEN_FILE = "~/.secrets/cloudflare-newlevel-access"

# The identity header Cloudflare injects downstream of a PASSED Access check and
# which the gateway trusts in `--trust-access-header` mode. Client-supplied
# `Cf-*` headers are stripped by Cloudflare before it sets the authentic one.
WEBTERM_ACCESS_TRUST_HEADER = "Cf-Access-Authenticated-User-Email"

# Declared per-profile Access apps. `allowed_emails` IS the whole authorization
# (deny-by-default: anything not listed is refused). Adding a person is ONE line
# here + `airuleset.py webterm-access --apply`.
#
# david's list is OWNER-PROVIDED (#612 needs-answer). Go-live needs TWO owner
# inputs: (1) David's email here (one line), and (2) a Cloudflare token with
# `Access: Apps and Policies: Edit` at WEBTERM_ACCESS_TOKEN_FILE (see above — the
# existing account token is read-only for Access). A new person is added the
# SAME one-line way (marek was decommissioned #882, 2026-09-05).
#
# The OWNER side (`zbynek.newlevel.media`) is NOW a declared Access app (#635,
# owner ROZHODNUTÉ 2026-08-22, REVERSING the pre-#635 "grey/DNS-only, Access
# inapplicable, tailnet-only" state): the owner chose to move his terminal behind
# Cloudflare Access like David's, trading direct tailnet-only exposure for
# any-network access with email-OTP instead of a password. `allowed_emails` is the
# whole authorization — the owner today (the #612 one-line-extensibility
# property; marek was decommissioned #882). The gateway on
# this hostname switches to `--trust-access-header` mode (password retired) via the
# `OWNER_GATEWAY_ACCESS_MODE` go-live gate in cli_webterm.py, and the grey DNS
# A-record is cut over to a proxied CNAME onto a dedicated cloudflared tunnel.
WEBTERM_ACCESS_APPS = {
    "david": {
        "hostname": "david.newlevel.media",
        "name": "webterm — david",
        "allowed_emails": ["david@grena.sk"],   # owner-corrected 2026-08-25 (.biz was a mis-given address), #612
        "session_duration": "24h",
    },
    "owner": {
        "hostname": "zbynek.newlevel.media",
        "name": "webterm — zbynek",
        "allowed_emails": ["drlik.zbynek@gmail.com"],  # owner, #635
        "session_duration": "24h",
    },
    # marek realm REMOVED (#882, 2026-09-05: stream decommissioned, odoo-erp#6257).
    # Ops: the Access app + tunnel + DNS for marek.newlevel.media should be torn
    # down by the gatekeeper (not this repo's code).
    # dominika.newlevel.media — the FOURTH webterm gateway (#867 scope-add
    # 2026-09-04, owner request: "pridat noveho webterm uzivatela dominika, email
    # nika.sarikova@gmail.com"). The e-mail is owner-PROVIDED verbatim in the
    # ticket; deny-by-default: this list IS the whole authorization, so a wrong
    # address only locks dominika out (the safe direction). Adding another person
    # is one more e-mail + `webterm-access --apply`.
    "dominika": {
        "hostname": "dominika.newlevel.media",
        "name": "webterm — dominika",
        "allowed_emails": ["nika.sarikova@gmail.com"],   # owner-provided, #867
        "session_duration": "24h",
    },
}

POLICY_NAME = "webterm allowed developers"


# --------------------------------------------------------------------------- #
# Pure payload builders (unit-tested directly — no network).
# --------------------------------------------------------------------------- #

def build_app_payload(spec):
    """Cloudflare Access self-hosted application payload for one profile spec,
    with its Allow policy INLINE in `policies` (the unified application-definition
    shape). One POST/PUT creates/updates the app AND its policy atomically — no
    separate app-scoped policy endpoint (legacy) and no deny-all window between
    the app create and a follow-up policy create. Valid on both the legacy and
    the unified Access API (#612 R2 review).

    `auto_redirect_to_identity=False` so the Access login PAGE (with the e-mail
    One-Time PIN input) is shown rather than jumping to an IdP; `allowed_idps=[]`
    means all configured login methods are offered — and with no IdP configured
    on the account, that is the built-in One-Time PIN (email OTP)."""
    return {
        "name": spec["name"],
        "domain": spec["hostname"],
        "type": "self_hosted",
        "session_duration": spec.get("session_duration", "24h"),
        "auto_redirect_to_identity": False,
        "allowed_idps": [],
        "app_launcher_visible": False,
        "policies": [build_policy_payload(spec)],
    }


def build_policy_payload(spec, precedence=1):
    """A single Allow policy INCLUDING exactly the declared e-mails. Deny-by-
    default: an e-mail not in the list is refused by Cloudflare Access. Adding
    marek is one more include entry (the one-line property). An EMPTY include is
    an open door and is REFUSED by apply_profile() upstream — never shipped."""
    emails = list(spec.get("allowed_emails") or [])
    return {
        "name": POLICY_NAME,
        "decision": "allow",
        "precedence": precedence,
        "include": [{"email": {"email": e}} for e in emails],
    }


# --------------------------------------------------------------------------- #
# Cloudflare Access API client — injectable transport for offline tests.
# --------------------------------------------------------------------------- #

class AccessClient:
    """Thin Cloudflare Access API client. `transport(method, path, body)` returns
    `(http_status, parsed_json_dict)`; the default hits the real API with the
    bearer token. Tests inject a fake transport that records calls and returns
    canned responses — so all apply logic is exercised with NO network."""

    def __init__(self, account_id, token=None, transport=None):
        self.account_id = account_id
        self._token = token
        self._transport = transport or self._http
        self.calls = []                       # (method, path) audit for tests

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

    def _base(self, suffix):
        return "/accounts/%s/access%s" % (self.account_id, suffix)

    # -- reads ------------------------------------------------------------- #

    def find_app_by_domain(self, domain):
        """Return (app_dict_or_None, (status, body)). Idempotency hinges on this:
        an app already present for `domain` is UPDATED, never duplicated. The
        compare is NORMALIZED (case-fold + strip a trailing slash) so a Cloudflare
        response that echoes the domain in a slightly different form does not slip
        past and cause a duplicate POST."""
        want = _norm_domain(domain)
        # per_page=1000 so the target app is never MISSED on a later page (a miss
        # would POST a DUPLICATE app, defeating idempotency) — #612 R2 review.
        st, d = self._call("GET", self._base("/apps?per_page=1000"))
        if st != 200 or not d.get("success"):
            return None, (st, d)
        for app in (d.get("result") or []):
            if _norm_domain(app.get("domain")) == want:
                return app, (st, d)
        return None, (st, d)

    # -- writes ------------------------------------------------------------ #

    def create_app(self, payload):
        return self._call("POST", self._base("/apps"), payload)

    def update_app(self, app_id, payload):
        return self._call("PUT", self._base("/apps/%s" % app_id), payload)


# --------------------------------------------------------------------------- #
# apply — idempotent upsert of app + policy for one profile.
# --------------------------------------------------------------------------- #

def apply_profile(client, spec, dry_run=True):
    """Idempotently reconcile the Access app + Allow policy for `spec`.

    Returns a structured dict: {ok, actions[], app_id, error}. `dry_run=True`
    (default) performs ONLY the read (find app) and reports what it WOULD do —
    no create/update. An empty `allowed_emails` is REFUSED outright (an app with
    no include is an open door — never shipped)."""
    result = {"ok": False, "hostname": spec["hostname"], "actions": [],
              "app_id": None, "would_apply": bool(not dry_run), "error": None}

    emails = list(spec.get("allowed_emails") or [])
    if not emails:
        result["error"] = ("allowed_emails is empty for %s — refusing (an Access "
                            "app with no allow-list is an open door). Fill it in "
                            "WEBTERM_ACCESS_APPS (one line) then re-apply."
                            % spec["hostname"])
        return result

    app, (st, body) = client.find_app_by_domain(spec["hostname"])
    if st != 200:
        result["error"] = ("cannot read Access apps (HTTP %s): %s"
                            % (st, _first_err(body)))
        return result

    # The app payload carries its Allow policy INLINE, so a single POST (create)
    # or PUT (update) reconciles both — the app always ends with exactly the
    # declared allow-list, never a deny-all window (#612 R2 review).
    app_payload = build_app_payload(spec)
    result["actions"].append("policy include: %d e-mail(s)" % len(emails))

    if app is None:
        result["actions"].append("create app %s (+ inline policy)" % spec["hostname"])
        if not dry_run:
            st, body = client.create_app(app_payload)
            if not _ok(st, body):
                result["error"] = "create app failed (HTTP %s): %s" % (st, _first_err(body))
                return result
            app = (body.get("result") or {})
    else:
        result["actions"].append("update app %s (id %s, + inline policy)"
                                 % (spec["hostname"], app.get("id")))
        if not dry_run:
            st, body = client.update_app(app["id"], app_payload)
            if not _ok(st, body):
                result["error"] = "update app failed (HTTP %s): %s" % (st, _first_err(body))
                return result
            app = (body.get("result") or app)

    result["app_id"] = app.get("id") if app else None
    result["ok"] = True
    return result


def _norm_domain(domain):
    """Normalize a hostname for idempotent create-vs-update matching: case-fold
    and drop a trailing slash. A None domain normalizes to "" (never matches a
    real hostname)."""
    return (domain or "").strip().rstrip("/").casefold()


def _ok(status, body):
    return status in (200, 201) and bool(body.get("success"))


def _first_err(body):
    errs = (body or {}).get("errors") or []
    if errs and isinstance(errs, list):
        return errs[0].get("message", str(errs[0]))
    return str(body)[:200]


def _load_token(path=WEBTERM_ACCESS_TOKEN_FILE):
    """Read the Cloudflare token from disk (value NEVER printed — only handed to
    the Authorization header). Trailing whitespace stripped (a secret-channel
    paste carries a newline that breaks the header — cloudflare-api-tokens skill)."""
    with open(os.path.expanduser(path)) as f:
        return f.read().strip()


# --------------------------------------------------------------------------- #
# CLI — airuleset.py webterm-access
# --------------------------------------------------------------------------- #

def cmd_webterm_access(args):
    """Reconcile the declared Cloudflare Access apps. DEFAULT is DRY-RUN (reads
    only, prints the plan); `--apply` performs the create/update. `--profile P`
    limits to one profile. The token value is never printed."""
    profiles_to_do = ([args.profile] if getattr(args, "profile", None)
                      else list(WEBTERM_ACCESS_APPS))
    # An explicit --dry-run ALWAYS wins (a safety flag is never silently ignored),
    # even if --apply is also passed; otherwise dry-run is the default (no --apply).
    dry_run = getattr(args, "dry_run", False) or not getattr(args, "apply", False)

    # The token is loaded in BOTH modes — a dry-run still READS (GET /apps) to
    # report create-vs-update. Dry-run safety is that apply_profile(dry_run=True)
    # issues only GETs, never a POST/PUT.
    try:
        token = _load_token()
    except OSError as e:
        print("webterm-access: cannot read token %s: %s"
              % (WEBTERM_ACCESS_TOKEN_FILE, e), file=sys.stderr)
        return 1
    if not token:
        print("webterm-access: token file %s is empty"
              % WEBTERM_ACCESS_TOKEN_FILE, file=sys.stderr)
        return 1

    client = AccessClient(WEBTERM_ACCESS_ACCOUNT_ID, token=token)
    mode = "DRY-RUN (no writes)" if dry_run else "APPLY"
    print("webterm-access [%s] account %s" % (mode, WEBTERM_ACCESS_ACCOUNT_ID))
    rc = 0
    for name in profiles_to_do:
        spec = WEBTERM_ACCESS_APPS.get(name)
        if spec is None:
            print("  %s: no such profile" % name, file=sys.stderr)
            rc = 1
            continue
        res = apply_profile(client, spec, dry_run=dry_run)
        status = "ok" if res["ok"] else "ERROR"
        print("  [%s] %s (%s): %s"
              % (status, name, res["hostname"], "; ".join(res["actions"]) or "-"))
        if res["error"]:
            print("      %s" % res["error"], file=sys.stderr)
            rc = 1
    if dry_run:
        print("  (dry-run — nothing changed; re-run with --apply to create/update)")
    return rc
