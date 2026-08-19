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

Auth model. The session cookie value is a SERVER-minted random token
(`secrets.token_urlsafe`), NEVER the credential, HttpOnly + SameSite=Strict +
Path=/. The credential (`user:pass` in ~/.secrets/webterm_credential) is compared
constant-time (`hmac.compare_digest`). ttyd binds 127.0.0.1 only, behind
`-b /t`, with NO basic-auth of its own — the gateway is the sole entry AND the
sole authenticator, so a tailnet peer cannot reach ttyd around the gateway.

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
# `@@ERR@@` is replaced with a FIXED server string (never user input), so there
# is no injection surface.
_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="sk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>work.newlevel.media — prihlásenie</title>
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


def login_form_html(error=False):
    """A Bitwarden-fillable login form: a real DOM `<form>` with the standard
    autocomplete tokens a password manager keys on (`username` +
    `current-password`), which the native basic-auth dialog structurally lacks."""
    err = ('<p class="err">Nesprávne meno alebo heslo.</p>' if error else "")
    return _LOGIN_TEMPLATE.replace("@@ERR@@", err)


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
    def __init__(self, dash_index, cred_path, ttyd_host, ttyd_port, base_path,
                 origins, sessions=None, limiter=None):
        self.dash_index = dash_index
        self.cred_path = cred_path
        self.ttyd_host = ttyd_host
        self.ttyd_port = ttyd_port
        self.base_path = base_path.rstrip("/") or "/t"
        self.origins = set(origins)          # allowed WS origins (own origin(s))
        self.sessions = sessions if sessions is not None else SessionStore()
        self.limiter = limiter if limiter is not None else RateLimiter()

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

    def _authed(self, headers):
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
        if self._is_proxy_path(target):
            await self._route_proxy(reader, writer, head, headers)
            return
        if path == "/":
            if self._authed(headers):
                await self._send(writer, http_response("200 OK", self._dashboard_bytes()))
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
        if method == "GET":
            await self._send(writer, http_response("200 OK", login_form_html()))
            return
        if method != "POST":
            await self._send(writer, http_response("405 Method Not Allowed", "no"))
            return
        if self.limiter.blocked(peer_ip):
            await self._send(writer, http_response(
                "429 Too Many Requests", login_form_html(error=True),
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
        await self._send(writer, http_response("403 Forbidden",
                                               login_form_html(error=True)))

    async def _route_logout(self, writer, headers):
        self.sessions.drop(cookie_token(headers))
        await self._send(writer, self._redirect(
            "/login", set_cookie=build_set_cookie("", 0, clear=True)))

    async def _route_proxy(self, reader, writer, head, headers):
        is_ws = is_websocket_upgrade(headers)
        if not self._authed(headers):
            # A WS handshake cannot be a redirect; deny outright. An HTTP request
            # (a stray asset fetch) gets sent to the login page.
            if is_ws:
                await self._send(writer, http_response("401 Unauthorized", "login required"))
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

    async def _send(self, writer, data):
        try:
            writer.write(data)
            await writer.drain()
        except ConnectionError:
            return


async def start_gateway(host, port, dash_index, cred_path, ttyd_host="127.0.0.1",
                        ttyd_port=7682, base_path="/t", origins=None,
                        sessions=None, limiter=None):
    """Bind + start the gateway on `host:port`. Returns the asyncio.Server (the
    caller reads `server.sockets[0].getsockname()` for an ephemeral port and
    `server.serve_forever()` / `server.close()`)."""
    gw = Gateway(dash_index, cred_path, ttyd_host, ttyd_port, base_path,
                 origins or [], sessions=sessions, limiter=limiter)
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
        base_path=args.base_path, origins=origins)
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
    p.add_argument("--cred", required=True, help="path to the user:pass credential file")
    p.add_argument("--ttyd-host", default="127.0.0.1")
    p.add_argument("--ttyd-port", type=int, default=7682)
    p.add_argument("--base-path", default="/t")
    args = p.parse_args(argv)
    try:
        asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
