"""airuleset webterm same-origin gateway (#584) — stdlib asyncio HTTP+WS proxy.

ONE tailnet-only port fronts BOTH the tabbed dashboard AND ttyd, so a password
manager (Bitwarden) can autofill the login (an HTML form, not the native
basic-auth dialog it structurally cannot fill) AND the terminal iframes become
same-origin (which unblocks #582's Ctrl+Alt+N-while-typing).

Routes (all on the single gateway origin):
  GET  /login        -> Bitwarden-fillable HTML login form
  POST /login        -> constant-time credential check -> mint session -> 303 /
  GET|POST /logout   -> drop the session server-side + clear cookie -> 303 /login
  GET  /             -> authed: the tabbed dashboard; else 303 /login
  /t , /t/*          -> authed: transparent proxy (HTTP + WebSocket) to
                        127.0.0.1:<ttyd>; unauthed: 401 (WS) / 303 /login (HTTP)
  everything else    -> 404

Auth model (default — PASSWORD mode, `--cred`). The session cookie value is a
SERVER-minted random token (`secrets.token_urlsafe`), NEVER the credential,
HttpOnly + SameSite=Strict + Path=/. The credential (`user:pass` in
~/.secrets/webterm_credential) is compared constant-time (`hmac.compare_digest`).
ttyd binds 127.0.0.1 only, behind `-b /t`, with NO basic-auth of its own — the
gateway is the sole entry AND the sole authenticator, so a tailnet peer cannot
reach ttyd around the gateway.

Auth model (#612 — CLOUDFLARE-ACCESS mode, `--trust-access-header`). For the
PUBLIC david gateway the password is RETIRED: Cloudflare Access at the edge does
email one-time-PIN verification BEFORE any request reaches the tunnel, and the
gateway simply trusts the identity header Cloudflare injects downstream of a
passed check (`Cf-Access-Authenticated-User-Email`) — no login form, no cookie,
no credential file. FAIL-CLOSED: a request with no such header did not pass
Access and is refused (403). Cloudflare strips client-supplied `Cf-*` headers
before setting the authentic one, and the gateway binds loopback reachable only
via the cloudflared tunnel (which serves only the Access-protected hostname).
The two modes are mutually exclusive; `main()` refuses to start with neither or
both (an unauthenticated shell is never bound). Pure stdlib has no RSA, so the
Access JWT is NOT cryptographically validated at the origin — the honest
residual is documented in cli_webterm_access.py.

Defence in depth. Failed logins are rate-limited PER SOURCE IP (a tailnet peer
has its own 100.x address), so an attacker throttles only itself and never locks
the owner out. A WebSocket upgrade additionally requires `Origin` == the
gateway's own origin (CSWSH); a SameSite=Strict cookie is not even sent on a
cross-site WS handshake, so that is a second, independent layer.

Transparent relay. After the HTTP `Upgrade: websocket` handshake completes, a
WebSocket is just a raw bidirectional byte stream — so the proxy pipes bytes
both ways with backpressure (`await drain()`) and never parses RFC6455 frames.
That is why this needs NO websocket dependency (a frame parser would be dead
weight); stdlib asyncio streams suffice and keep zero new deps (#486).

No TLS / no Secure cookie flag: the transport is plain HTTP over the tailnet,
whose WireGuard layer is the encryption boundary (the same tailnet-only model
#579 already relies on). A Secure flag would make the cookie unusable over http.
"""
import argparse
import asyncio
import hmac
import secrets
import string
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

# The cookie the browser round-trips. Its value is a random session token, never
# the credential (see module docstring).
COOKIE_NAME = "webterm_session"
# Absolute session lifetime. In-memory only (per service lifetime) — a gateway
# restart clears every session, so a restart means re-login. Deliberate: no
# persistent session store to leak/steal, and a restart is rare.
SESSION_TTL_S = 12 * 3600
# Login rate limit: more than MAX_FAILS failed attempts from ONE source IP
# within WINDOW_S blocks FURTHER attempts FROM THAT IP for the rest of the
# window (429). A correct credential is never rate-limited; a different IP (the
# owner) is never affected — so this throttles brute force without an owner DoS.
RL_MAX_FAILS = 8
RL_WINDOW_S = 60
# Caps against a header/body flood on an unauthenticated socket.
MAX_HEAD_BYTES = 64 * 1024
MAX_LOGIN_BODY = 8 * 1024
RELAY_CHUNK = 64 * 1024
# Slowloris guard (#584 review): bound how long an UNauthenticated peer may take
# to send its request head / login body. A peer that dribbles bytes forever (or
# claims a large Content-Length then stalls) is closed instead of holding a
# coroutine open indefinitely. Not applied to the post-auth WS relay, which is a
# legitimately long-lived interactive terminal connection.
READ_TIMEOUT_S = 20

# #677: the aggregate per-box U map for the tab dots. Served (auth-gated) at
# /u-status; when it is older than U_STATUS_STALE_S the gateway fire-and-forgets a
# DETACHED collector (the statusbar._spawn_refresh pattern), rate-limited to at
# most one spawn per U_STATUS_SPAWN_GUARD_S so a burst of polls kicks only one.
U_STATUS_STALE_S = 90
U_STATUS_SPAWN_GUARD_S = 60


def _default_u_status_path():
    return Path.home() / ".claude" / "webterm-u-status.json"


def _default_u_collect_spawn():
    """Fire-and-forget the detached collector (`cli_webterm.py webterm-u-collect`),
    which does the fleet ssh reads and rewrites the aggregate file. Never blocks
    the async loop; a spawn failure is non-fatal (the stale file is still served)."""
    repo = Path(__file__).resolve().parent
    subprocess.Popen(
        [sys.executable, str(repo / "cli_webterm.py"), "webterm-u-collect"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True)


def _close_quiet(writer):
    """Close a StreamWriter, ignoring an already-closed/broken transport. A
    client vanishing is normal control flow in a proxy, not an error to log."""
    try:
        writer.close()
    except Exception:
        return


def _eof_quiet(writer):
    """Half-close (write_eof) best-effort — the transport may already be gone
    when one relay direction ends; that is expected teardown, not an error."""
    try:
        writer.write_eof()
    except Exception:
        return


# --------------------------------------------------------------------------- #
# Credential — constant-time compare against ~/.secrets/webterm_credential.
# --------------------------------------------------------------------------- #

def load_credential(path):
    """`(user, password)` from a `user:pass` mode-600 file, or None if the file
    is missing/empty/malformed. Only the FIRST `:` splits (a password may
    contain `:`)."""
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw or ":" not in raw:
        return None
    user, pw = raw.split(":", 1)
    if not user or not pw:
        return None
    return user, pw


def credential_matches(cred, user, pw):
    """Constant-time check that `(user, pw)` equals the loaded `cred` tuple.
    Both fields are compared with `hmac.compare_digest` (never `==`, which
    short-circuits and leaks length/prefix timing). A None `cred` (no credential
    file) never matches. Both fields are always compared (no early return on a
    username mismatch) so the work is independent of which field differs."""
    if not cred:
        return False
    cu, cpw = cred
    ok_user = hmac.compare_digest(cu.encode("utf-8"), (user or "").encode("utf-8"))
    ok_pw = hmac.compare_digest(cpw.encode("utf-8"), (pw or "").encode("utf-8"))
    return ok_user and ok_pw


# --------------------------------------------------------------------------- #
# Session store — in-memory token -> absolute-expiry (service lifetime).
# --------------------------------------------------------------------------- #

class SessionStore:
    def __init__(self, ttl_s=SESSION_TTL_S, now=time.time):
        self._sessions = {}
        self._ttl = ttl_s
        self._now = now

    def create(self):
        """Mint a FRESH random token (server-generated only — never derived
        from client input, so a client cannot fixate a session id) and record
        its expiry."""
        token = secrets.token_urlsafe(32)
        self._sessions[token] = self._now() + self._ttl
        return token

    def valid(self, token):
        if not token:
            return False
        exp = self._sessions.get(token)
        if exp is None:
            return False
        if self._now() >= exp:
            self._sessions.pop(token, None)
            return False
        return True

    def drop(self, token):
        """Invalidate a session server-side (logout) — a stolen cookie for a
        dropped token is dead even before it expires."""
        if token:
            self._sessions.pop(token, None)

    def __len__(self):
        return len(self._sessions)


# --------------------------------------------------------------------------- #
# Rate limiter — per source IP, sliding window over FAILED attempts only.
# --------------------------------------------------------------------------- #

class RateLimiter:
    def __init__(self, max_fails=RL_MAX_FAILS, window_s=RL_WINDOW_S, now=time.time):
        self._fails = {}      # ip -> list[timestamp]
        self._max = max_fails
        self._window = window_s
        self._now = now

    def _prune(self, ip, now):
        cutoff = now - self._window
        lst = [t for t in self._fails.get(ip, ()) if t >= cutoff]
        if lst:
            self._fails[ip] = lst
        else:
            self._fails.pop(ip, None)
        return lst

    def blocked(self, ip):
        """True iff `ip` has already reached the failure cap within the window.
        A different IP is never affected — so the owner is never locked out by
        an attacker's failures."""
        now = self._now()
        return len(self._prune(ip, now)) >= self._max

    def record_failure(self, ip):
        now = self._now()
        lst = self._prune(ip, now)
        lst.append(now)
        self._fails[ip] = lst

    def clear(self, ip):
        """Drop an IP's failure history (called on a SUCCESSFUL login, so a user
        who mistyped a few times then got it right is not left throttled)."""
        self._fails.pop(ip, None)

    # Memory note (#584 review): entries are pruned lazily per-IP on every
    # `blocked`/`record_failure`, so `_fails` holds at most one (possibly stale)
    # entry per distinct source IP that has failed a login. On a WireGuard-
    # authenticated tailnet the source IP cannot be spoofed and the peer set is
    # small + bounded, so this can never grow unbounded — no periodic sweep is
    # warranted (a lingering stale entry is a few dozen bytes, cleared on the
    # IP's next attempt or a service restart).


# --------------------------------------------------------------------------- #
# HTTP head parsing + cookie + response helpers (pure — unit tested directly).
# --------------------------------------------------------------------------- #

def parse_request_head(head_bytes):
    """Parse a request head (bytes up to and including the blank line) into
    `(method, target, http_version, headers)` where `headers` is a list of
    `(name, value)` in wire order (duplicates preserved). Returns None on a
    malformed head. Never raises."""
    try:
        text = head_bytes.decode("latin-1")
    except Exception:
        return None
    lines = text.split("\r\n")
    if not lines or not lines[0]:
        return None
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    method, target, version = parts
    headers = []
    for ln in lines[1:]:
        if not ln:
            continue
        if ":" not in ln:
            return None
        k, v = ln.split(":", 1)
        headers.append((k.strip(), v.strip()))
    return method.upper(), target, version, headers


def header_get(headers, name):
    """First header value (case-insensitive) or None."""
    nl = name.lower()
    for k, v in headers:
        if k.lower() == nl:
            return v
    return None


def cookie_token(headers, cookie_name=COOKIE_NAME):
    """Extract `cookie_name`'s value from the Cookie header, or None. Manual
    split (a cookie header is `k=v; k2=v2`)."""
    raw = header_get(headers, "cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k.strip() == cookie_name:
            return v.strip()
    return None


def build_set_cookie(value, max_age, clear=False):
    """A `Set-Cookie` value: HttpOnly + SameSite=Strict + Path=/. `clear=True`
    expires it (logout). No `Secure` flag — plain HTTP over the tailnet (the
    WireGuard layer is the transport boundary; Secure would make it unusable)."""
    attrs = ["%s=%s" % (COOKIE_NAME, value), "HttpOnly", "SameSite=Strict", "Path=/"]
    if clear:
        attrs.append("Max-Age=0")
        attrs.append("Expires=Thu, 01 Jan 1970 00:00:00 GMT")
    else:
        attrs.append("Max-Age=%d" % max_age)
    return "; ".join(attrs)


def is_websocket_upgrade(headers):
    """True iff the request is a WebSocket upgrade (Upgrade: websocket +
    Connection contains 'upgrade', both case-insensitive)."""
    up = (header_get(headers, "upgrade") or "").lower()
    conn = (header_get(headers, "connection") or "").lower()
    return up == "websocket" and "upgrade" in conn


def _authority(url):
    """The `host[:port]` authority of an Origin/URL — strip the scheme and any
    path (`http://dev1:8080/x` -> `dev1:8080`)."""
    if "://" in url:
        url = url.split("://", 1)[1]
    return url.split("/", 1)[0]


def origin_allowed(headers, allowed_origins=None):
    """CSWSH guard for a WS upgrade. PRIMARY, addressing-agnostic check: the
    request's `Origin` authority (host:port) must equal its own `Host` header.
    A same-origin browser request always has Origin-authority == Host — true
    whether the dashboard was opened at the raw tailnet IP
    (`http://100.104.8.125:8080/`) OR a MagicDNS name (`http://dev1:8080/`, the
    fleet's usual addressing) OR any front — while a cross-site request never
    does, and a browser page cannot forge either header. This replaces a fixed
    IP-only allowlist that would have silently 403'd every terminal when the
    dashboard was reached by hostname (#584 review). SECONDARY: an explicit
    `allowed_origins` allowlist (kept for callers that pass the known origin,
    e.g. tests). A missing Origin is REJECTED (fail closed)."""
    origin = header_get(headers, "origin")
    if not origin:
        return False
    host = header_get(headers, "host")
    if host and _authority(origin) == host:
        return True
    if allowed_origins and origin in allowed_origins:
        return True
    return False


def http_response(status_line, body_bytes, extra_headers=None,
                  content_type="text/html; charset=utf-8"):
    """A complete HTTP/1.1 response (bytes) with Connection: close."""
    if isinstance(body_bytes, str):
        body_bytes = body_bytes.encode("utf-8")
    hdrs = ["HTTP/1.1 " + status_line,
            "Content-Type: " + content_type,
            "Content-Length: %d" % len(body_bytes),
            "Connection: close",
            "X-Content-Type-Options: nosniff"]
    for h in (extra_headers or []):
        hdrs.append(h)
    return ("\r\n".join(hdrs) + "\r\n\r\n").encode("latin-1") + body_bytes


# NOTE: `.replace()` substitution (not `%`-formatting) — the CSS body is full of
# `%` (e.g. `100%`) which `%`-formatting would misread as a format spec (ruff
# F509), the SAME reason `cli_webterm.render_dashboard_html` uses `.replace()`.
# `@@ERR@@` is a FIXED server string. `@@TITLE@@` (#655) DOES carry request-derived
# input (the Host header) — it is injection-safe because `_login_title` accepts
# ONLY the strict `_HOSTNAME_CHARS` set (no HTML-special char), falling back to a
# neutral title otherwise; see `_login_title`.
_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="sk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>@@TITLE@@</title>
<style>
:root { color-scheme: dark; }
html,body { height:100%; margin:0; }
body { display:flex; align-items:center; justify-content:center;
  background:#0d1117; color:#e6edf3;
  font:14px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
form { background:#161b22; border:1px solid #30363d; border-radius:10px;
  padding:26px 24px; width:300px; }
h1 { font-size:15px; margin:0 0 16px; color:#fff; font-weight:600; }
label { display:block; margin:12px 0 4px; color:#adbac7; font-size:12px; }
input { width:100%; box-sizing:border-box; padding:8px 10px; border-radius:6px;
  border:1px solid #30363d; background:#0d1117; color:#e6edf3; font:inherit; }
button { width:100%; margin-top:18px; padding:9px; border:0; border-radius:6px;
  background:#238636; color:#fff; font:inherit; font-weight:600; cursor:pointer; }
button:hover { background:#2ea043; }
.err { color:#f85149; font-size:12px; margin:10px 0 0; }
</style></head><body>
<form method="post" action="/login" autocomplete="on">
<h1>fleet terminal — prihlásenie</h1>
<label for="u">Meno</label>
<input id="u" name="username" type="text" autocomplete="username"
  autocapitalize="none" autocorrect="off" spellcheck="false" autofocus>
<label for="p">Heslo</label>
<input id="p" name="password" type="password" autocomplete="current-password">
<button type="submit">Prihlásiť</button>
@@ERR@@
</form></body></html>"""


# #655: hostname charset — a Host header validated to THIS set has no HTML-special
# character (`< > & " '`), so a title built from it is injection-safe by
# construction (no escaping / no `html`/`re` import needed). An out-of-set or
# oversized Host degrades to the neutral title, never the stale work.newlevel.media.
_HOSTNAME_CHARS = frozenset(string.ascii_letters + string.digits + ".-")


def _login_title(host):
    """The login page `<title>` for the ACTUAL serving host (#655). The stale
    hardcoded `work.newlevel.media` (NXDOMAIN) is gone; the real hosts are
    zbynek/david.newlevel.media. A missing/blank/invalid Host degrades to a
    neutral title. `host` is the raw Host header (may carry a `:port`)."""
    h = (host or "").split(":", 1)[0].strip()
    if h and len(h) <= 253 and all(c in _HOSTNAME_CHARS for c in h):
        return h + " — prihlásenie"
    return "fleet terminal — prihlásenie"


def login_form_html(error=False, host=None):
    """A Bitwarden-fillable login form: a real DOM `<form>` with the standard
    autocomplete tokens a password manager keys on (`username` +
    `current-password`), which the native basic-auth dialog structurally lacks.
    `host` (the request Host header) drives the `<title>` so it names the real
    serving domain (#655), never a hardcoded legacy one."""
    err = ('<p class="err">Nesprávne meno alebo heslo.</p>' if error else "")
    return (_LOGIN_TEMPLATE
            .replace("@@TITLE@@", _login_title(host))
            .replace("@@ERR@@", err))


def parse_login_form(body_bytes):
    """`(username, password)` from an `application/x-www-form-urlencoded` body,
    each defaulting to ''. Never raises."""
    try:
        parsed = urllib.parse.parse_qs(body_bytes.decode("utf-8", "replace"),
                                       keep_blank_values=True)
    except Exception:
        return "", ""
    return (parsed.get("username", [""])[0], parsed.get("password", [""])[0])


# --------------------------------------------------------------------------- #
# The gateway — asyncio connection handler + transparent relay.
# --------------------------------------------------------------------------- #

class Gateway:
    # #644: the installable-PWA assets, served (auth-gated, like `/`) from the
    # dash dir next to index.html. `route -> (filename, content_type)`. The
    # FILENAMES are written by cli_webterm_pwa.write_pwa_assets; a test locks
    # this table's filenames == cli_webterm_pwa.PWA_FILENAMES so the two stay in
    # sync WITHOUT this lean gateway importing the generator.
    _PWA_ASSETS = {
        "/manifest.webmanifest": ("manifest.webmanifest",
                                  "application/manifest+json"),
        "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
        "/icon-192.png": ("icon-192.png", "image/png"),
        "/icon-512.png": ("icon-512.png", "image/png"),
        "/icon-maskable-512.png": ("icon-maskable-512.png", "image/png"),
    }

    def __init__(self, dash_index, cred_path, ttyd_host, ttyd_port, base_path,
                 origins, sessions=None, limiter=None, trust_access_header=None,
                 u_status_path=None, u_collect_spawn=None):
        self.dash_index = dash_index
        self.dash_dir = Path(dash_index).parent   # #644: PWA assets live here
        # #677: the aggregate U map served at /u-status + a detached spawner that
        # refreshes it when stale. Both injectable (tests); defaults are the real
        # ~/.claude path and a detached `cli_webterm.py webterm-u-collect`.
        self.u_status_path = Path(u_status_path) if u_status_path else _default_u_status_path()
        self.u_collect_spawn = u_collect_spawn or _default_u_collect_spawn
        self._u_spawn_at = 0.0
        self.cred_path = cred_path
        self.ttyd_host = ttyd_host
        self.ttyd_port = ttyd_port
        self.base_path = base_path.rstrip("/") or "/t"
        self.origins = set(origins)          # allowed WS origins (own origin(s))
        self.sessions = sessions if sessions is not None else SessionStore()
        self.limiter = limiter if limiter is not None else RateLimiter()
        # #612 Cloudflare-Access mode: when set, this is the identity header
        # Cloudflare injects downstream of a PASSED email-OTP Access check
        # (`Cf-Access-Authenticated-User-Email`). In this mode there is NO
        # password / login form / credential — the request is authenticated iff
        # that header is present and non-empty (FAIL-CLOSED: a request that did
        # NOT traverse Access carries no such header and is refused). Default
        # None = the unchanged password/session model (owner profile, byte-
        # identical). Cloudflare strips client-supplied `Cf-*` headers before
        # setting the authentic one; the gateway binds loopback and is reachable
        # only via the cloudflared tunnel (see cli_webterm_access.py's honest
        # residual note on the absence of stdlib RSA JWT validation).
        self.trust_access_header = trust_access_header

    # -- helpers ---------------------------------------------------------- #

    def _dashboard_bytes(self):
        try:
            return Path(self.dash_index).read_bytes()
        except OSError:
            return b"<!DOCTYPE html><p>dashboard not generated yet</p>"

    def _is_proxy_path(self, target):
        """A request whose path belongs to ttyd (the base path or under it)."""
        path = target.split("?", 1)[0]
        return path == self.base_path or path.startswith(self.base_path + "/")

    def _access_identity(self, headers):
        """In Cloudflare-Access mode, the trusted, non-empty identity header set
        by Cloudflare after a passed email-OTP check, else None. FAIL-CLOSED: any
        absent/blank header yields None (a request that did not go through Access
        carries no such header). A DUPLICATE trust header (more than one
        occurrence) also yields None — Cloudflare sets exactly one, so a second
        occurrence is a smuggling attempt and `header_get` would otherwise return
        the FIRST (client-controlled) value (#612 R2 review)."""
        if not self.trust_access_header:
            return None
        name = self.trust_access_header.lower()
        seen = [v for (k, v) in headers if k.lower() == name]
        if len(seen) != 1:
            return None
        val = (seen[0] or "").strip()
        return val or None

    def _authed(self, headers):
        # Access mode: authenticated iff the trusted Cloudflare identity header is
        # present + non-empty. No cookie/session/credential is involved. Password
        # mode (trust_access_header is None): the unchanged session-cookie check.
        if self.trust_access_header:
            return self._access_identity(headers) is not None
        return self.sessions.valid(cookie_token(headers))

    # -- read a request head with a hard size cap ------------------------- #

    async def _read_head(self, reader):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),
                                          timeout=READ_TIMEOUT_S)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                asyncio.TimeoutError):
            return None
        # Belt-and-suspenders: readuntil already raises LimitOverrunError at the
        # 64 KB stream limit (== MAX_HEAD_BYTES), so this rarely fires.
        if len(head) > MAX_HEAD_BYTES:
            return None
        return head

    async def _read_body(self, reader, headers, cap):
        n = header_get(headers, "content-length")
        if not n:
            return b""
        try:
            length = int(n)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        length = min(length, cap)
        try:
            return await asyncio.wait_for(reader.readexactly(length),
                                          timeout=READ_TIMEOUT_S)
        except asyncio.IncompleteReadError as e:
            return e.partial
        except asyncio.TimeoutError:
            return b""

    # -- the connection handler ------------------------------------------ #

    async def handle(self, reader, writer):
        try:
            await self._handle(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            # The client vanished mid-request — normal for a browser/terminal.
            return
        except Exception as e:                      # never let one client kill the loop
            sys.stderr.write("webterm-gateway: handler error: %r\n" % e)
        finally:
            _close_quiet(writer)

    async def _handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "?"
        head = await self._read_head(reader)
        if head is None:
            await self._send(writer, http_response("400 Bad Request", "bad request"))
            return
        parsed = parse_request_head(head)
        if parsed is None:
            await self._send(writer, http_response("400 Bad Request", "bad request"))
            return
        method, target, _version, headers = parsed
        path = target.split("?", 1)[0]

        if path == "/login":
            await self._route_login(reader, writer, method, headers, peer_ip)
            return
        if path == "/logout":
            await self._route_logout(writer, headers)
            return
        if path in self._PWA_ASSETS:
            await self._route_pwa_asset(writer, path, headers)
            return
        if path == "/u-status":
            await self._route_u_status(writer, headers)
            return
        if self._is_proxy_path(target):
            await self._route_proxy(reader, writer, head, headers)
            return
        if path == "/":
            if self._authed(headers):
                await self._send(writer, http_response("200 OK", self._dashboard_bytes()))
            elif self.trust_access_header:
                # Access mode, no trusted identity header => the request did not
                # pass Cloudflare Access. Fail closed (no login form to send to).
                await self._send(writer, self._access_denied())
            else:
                await self._send(writer, self._redirect("/login"))
            return
        if path == "/favicon.ico":
            await self._send(writer, http_response("204 No Content", b"",
                                                   content_type="text/plain"))
            return
        await self._send(writer, http_response("404 Not Found", "not found"))

    # -- routes ----------------------------------------------------------- #

    async def _route_login(self, reader, writer, method, headers, peer_ip):
        if self.trust_access_header:
            # Access mode has no password/login form — Cloudflare Access at the
            # edge is the login. Bounce to `/` (which serves the dashboard when
            # the trusted identity header is present, else fails closed).
            await self._send(writer, self._redirect("/"))
            return
        host = header_get(headers, "host")   # #655: title reflects the real domain
        if method == "GET":
            await self._send(writer, http_response(
                "200 OK", login_form_html(host=host)))
            return
        if method != "POST":
            await self._send(writer, http_response("405 Method Not Allowed", "no"))
            return
        if self.limiter.blocked(peer_ip):
            await self._send(writer, http_response(
                "429 Too Many Requests", login_form_html(error=True, host=host),
                extra_headers=["Retry-After: %d" % RL_WINDOW_S]))
            return
        body = await self._read_body(reader, headers, MAX_LOGIN_BODY)
        user, pw = parse_login_form(body)
        cred = load_credential(self.cred_path)
        if credential_matches(cred, user, pw):
            self.limiter.clear(peer_ip)
            token = self.sessions.create()
            await self._send(writer, self._redirect(
                "/", set_cookie=build_set_cookie(token, SESSION_TTL_S)))
            return
        self.limiter.record_failure(peer_ip)
        await self._send(writer, http_response(
            "403 Forbidden", login_form_html(error=True, host=host)))

    async def _route_logout(self, writer, headers):
        self.sessions.drop(cookie_token(headers))
        await self._send(writer, self._redirect(
            "/login", set_cookie=build_set_cookie("", 0, clear=True)))

    async def _route_pwa_asset(self, writer, path, headers):
        # #644: PWA assets (manifest / network-only SW / icons) are auth-gated
        # exactly like `/` — installation is "po prihlásení". In Access mode the
        # edge already authenticated the request; in password mode a valid
        # session cookie is required. Unauthenticated: fail closed like `/`.
        if not self._authed(headers):
            if self.trust_access_header:
                await self._send(writer, self._access_denied())
            else:
                await self._send(writer, self._redirect("/login"))
            return
        fname, ctype = self._PWA_ASSETS[path]
        try:
            body = (self.dash_dir / fname).read_bytes()
        except OSError:
            await self._send(writer, http_response("404 Not Found", "not found"))
            return
        extra = None
        if path == "/sw.js":
            # Re-check the SW script on each load (no stale worker), and allow it
            # to control the whole origin. The SW is NETWORK-ONLY itself, so this
            # Cache-Control governs only the script fetch, never terminal data.
            extra = ["Cache-Control: no-cache", "Service-Worker-Allowed: /"]
        await self._send(writer, http_response("200 OK", body,
                                               extra_headers=extra,
                                               content_type=ctype))

    async def _route_u_status(self, writer, headers):
        # #677: the aggregate per-box U map for the tab dots. AUTH-GATED exactly
        # like `/` and the PWA assets (fail-closed for an unauthenticated request);
        # a stale/absent file kicks a detached refresh but the current map is
        # always served (never a 500, so the dashboard just renders no new dots).
        if not self._authed(headers):
            if self.trust_access_header:
                await self._send(writer, self._access_denied())
            else:
                await self._send(writer, self._redirect("/login"))
            return
        body = self._u_status_body()
        await self._send(writer, http_response(
            "200 OK", body, extra_headers=["Cache-Control: no-store"],
            content_type="application/json; charset=utf-8"))

    def _u_status_body(self):
        """The current aggregate U JSON (bytes). Reads the file; when it is
        absent or older than U_STATUS_STALE_S, fire-and-forgets a detached
        collector (rate-limited) so the NEXT poll is fresh. Always returns a
        valid JSON map -- never raises, never a 500."""
        now = time.time()
        raw = None
        stale = True
        try:
            raw = self.u_status_path.read_bytes()
            stale = (now - self.u_status_path.stat().st_mtime) > U_STATUS_STALE_S
        except OSError:
            stale = True                    # absent/unreadable -> refresh + empty map
        if stale:
            self._maybe_spawn_u_collect(now)
        return raw if raw is not None else b'{"u":{},"ts":0}'

    def _maybe_spawn_u_collect(self, now):
        """Fire the detached collector at most once per U_STATUS_SPAWN_GUARD_S, so
        a burst of dashboard polls kicks exactly one refresh. A spawn failure is
        logged and non-fatal (the stale file stays served)."""
        if now - self._u_spawn_at < U_STATUS_SPAWN_GUARD_S:
            return
        self._u_spawn_at = now
        try:
            self.u_collect_spawn()
        except Exception as e:
            sys.stderr.write("webterm-gateway: u-collect spawn failed: %r\n" % e)

    async def _route_proxy(self, reader, writer, head, headers):
        is_ws = is_websocket_upgrade(headers)
        if not self._authed(headers):
            # A WS handshake cannot be a redirect; deny outright. An HTTP request
            # (a stray asset fetch) gets sent to the login page — except in Access
            # mode, where there is no login form, so it fails closed with 403.
            if is_ws:
                await self._send(writer, http_response("401 Unauthorized", "login required"))
            elif self.trust_access_header:
                await self._send(writer, self._access_denied())
            else:
                await self._send(writer, self._redirect("/login"))
            return
        if is_ws and not origin_allowed(headers, self.origins):
            await self._send(writer, http_response("403 Forbidden",
                                                   "cross-origin websocket refused"))
            return
        await self._proxy(reader, writer, head, headers, is_ws)

    # -- transparent proxy + relay ---------------------------------------- #

    async def _proxy(self, c_reader, c_writer, head, headers, is_ws):
        try:
            u_reader, u_writer = await asyncio.open_connection(
                self.ttyd_host, self.ttyd_port)
        except OSError:
            await self._send(c_writer, http_response("502 Bad Gateway",
                                                     "terminal backend unreachable"))
            return
        forwarded = self._rebuild_head(head, headers, is_ws)
        u_writer.write(forwarded)
        try:
            await u_writer.drain()
        except ConnectionError:
            _close_quiet(u_writer)
            return
        await self._relay(c_reader, c_writer, u_reader, u_writer)

    def _rebuild_head(self, head, headers, is_ws):
        """Reconstruct the request head toward ttyd: keep the method + target
        (ttyd expects the `-b /t` prefix intact) and every header EXCEPT Host
        (repointed to the loopback upstream); for a non-WS request force
        `Connection: close` so ttyd closes after the single response and the
        relay terminates cleanly. WS upgrade headers pass through untouched."""
        first = head.split(b"\r\n", 1)[0].decode("latin-1")
        lines = [first]
        for k, v in headers:
            kl = k.lower()
            if kl == "host":
                continue
            if not is_ws and kl == "connection":
                continue
            lines.append("%s: %s" % (k, v))
        lines.append("Host: %s:%d" % (self.ttyd_host, self.ttyd_port))
        if not is_ws:
            lines.append("Connection: close")
        return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")

    async def _relay(self, c_reader, c_writer, u_reader, u_writer):
        t1 = asyncio.ensure_future(self._pipe(c_reader, u_writer))
        t2 = asyncio.ensure_future(self._pipe(u_reader, c_writer))
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (t1, t2):
                if not t.done():
                    t.cancel()
            _close_quiet(u_writer)
            _close_quiet(c_writer)

    async def _pipe(self, reader, writer):
        """Copy bytes one direction with backpressure. Transparent: no frame
        parsing, so WS fragmentation/close frames pass through as bytes; EOF is
        propagated so a closed direction tears the pair down."""
        try:
            while True:
                try:
                    data = await reader.read(RELAY_CHUNK)
                except (ConnectionError, asyncio.CancelledError):
                    break
                if not data:
                    break
                try:
                    writer.write(data)
                    await writer.drain()
                except (ConnectionError, asyncio.CancelledError):
                    break
        finally:
            _eof_quiet(writer)

    # -- low-level write helpers ------------------------------------------ #

    def _redirect(self, location, set_cookie=None):
        extra = ["Location: " + location]
        if set_cookie:
            extra.append("Set-Cookie: " + set_cookie)
        return http_response("303 See Other", b"", extra_headers=extra,
                             content_type="text/plain")

    def _access_denied(self):
        """Fail-closed response for Access mode when the trusted Cloudflare
        identity header is absent — the request did not pass Cloudflare Access."""
        return http_response(
            "403 Forbidden",
            b"<!DOCTYPE html><meta charset=utf-8><title>Access required</title>"
            b"<p>This terminal is protected by Cloudflare Access. Reach it via "
            b"its https:// hostname so Cloudflare can verify you by e-mail first.",
            content_type="text/html; charset=utf-8")

    async def _send(self, writer, data):
        try:
            writer.write(data)
            await writer.drain()
        except ConnectionError:
            return


async def start_gateway(host, port, dash_index, cred_path, ttyd_host="127.0.0.1",
                        ttyd_port=7682, base_path="/t", origins=None,
                        sessions=None, limiter=None, trust_access_header=None,
                        u_status_path=None, u_collect_spawn=None):
    """Bind + start the gateway on `host:port`. Returns the asyncio.Server (the
    caller reads `server.sockets[0].getsockname()` for an ephemeral port and
    `server.serve_forever()` / `server.close()`)."""
    gw = Gateway(dash_index, cred_path, ttyd_host, ttyd_port, base_path,
                 origins or [], sessions=sessions, limiter=limiter,
                 trust_access_header=trust_access_header,
                 u_status_path=u_status_path, u_collect_spawn=u_collect_spawn)
    return await asyncio.start_server(gw.handle, host, port)


def _origins_for(host, port):
    """The gateway's own origin(s) for the CSWSH Origin check — the tailnet IP
    with the port, and (belt-and-suspenders) the bare IP for a default-port
    edge. A browser sends `Origin: http://<host>:<port>`."""
    o = ["http://%s:%d" % (host, port)]
    if port == 80:
        o.append("http://%s" % host)
    return o


async def _main_async(args):
    origins = _origins_for(args.bind, args.port)
    server = await start_gateway(
        args.bind, args.port, args.dash_index, args.cred,
        ttyd_host=args.ttyd_host, ttyd_port=args.ttyd_port,
        base_path=args.base_path, origins=origins,
        trust_access_header=args.trust_access_header)
    sock = server.sockets[0].getsockname()
    sys.stderr.write("webterm-gateway: listening on http://%s:%d/ -> ttyd %s:%d%s\n"
                     % (sock[0], sock[1], args.ttyd_host, args.ttyd_port,
                        args.base_path))
    async with server:
        await server.serve_forever()


def main(argv):
    p = argparse.ArgumentParser(description="airuleset webterm same-origin gateway")
    p.add_argument("--bind", required=True, help="tailscale IP to bind (never 0.0.0.0)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--dash-index", required=True, help="path to the generated dashboard index.html")
    p.add_argument("--cred", default=None,
                   help="path to the user:pass credential file (password mode)")
    p.add_argument("--trust-access-header", dest="trust_access_header", default=None,
                   help="#612 Cloudflare-Access mode: trust this Cloudflare-injected "
                        "identity header (e.g. Cf-Access-Authenticated-User-Email) "
                        "instead of a password/login form. Mutually exclusive with "
                        "--cred.")
    p.add_argument("--ttyd-host", default="127.0.0.1")
    p.add_argument("--ttyd-port", type=int, default=7682)
    p.add_argument("--base-path", default="/t")
    args = p.parse_args(argv)
    # Fail-CLOSED: exactly one auth mode. Neither would serve the terminal with
    # NO gate at all; both is contradictory. This refuses to start rather than
    # bind an unauthenticated shell.
    if bool(args.cred) == bool(args.trust_access_header):
        p.error("exactly one of --cred (password mode) or --trust-access-header "
                "(Cloudflare Access mode) is required")
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
