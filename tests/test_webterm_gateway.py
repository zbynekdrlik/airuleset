"""Tests for the webterm same-origin gateway (#584, cli_webterm_gateway.py).

Two layers:
  * PURE unit tests — constant-time credential compare, session store, per-IP
    rate limiter, request-head parse, cookie parse/build, WS-upgrade detection,
    CSWSH origin check, login-form parse.
  * INTEGRATION tests — a real gateway on 127.0.0.1:<ephemeral> in front of a
    FAKE ttyd upstream (also 127.0.0.1), exercised over real sockets: login ok /
    wrong / rate-limit / cookie flags / logout, unauth redirect, dashboard
    serve, HTTP proxy relay, WebSocket relay + Origin check, session fixation.

Nothing here EVER binds a non-loopback interface (the security invariant).
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm_gateway as g  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure unit tests
# --------------------------------------------------------------------------- #

class TestCredential(unittest.TestCase):
    def _credfile(self, text):
        d = tempfile.mkdtemp()
        p = Path(d) / "cred"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_load_ok(self):
        self.assertEqual(g.load_credential(self._credfile("zbynek:s3cret\n")),
                         ("zbynek", "s3cret"))

    def test_load_password_may_contain_colon(self):
        self.assertEqual(g.load_credential(self._credfile("u:a:b:c")), ("u", "a:b:c"))

    def test_load_missing_or_malformed(self):
        self.assertIsNone(g.load_credential("/no/such/file"))
        self.assertIsNone(g.load_credential(self._credfile("")))
        self.assertIsNone(g.load_credential(self._credfile("noseparator")))
        self.assertIsNone(g.load_credential(self._credfile(":onlypass")))
        self.assertIsNone(g.load_credential(self._credfile("onlyuser:")))

    def test_matches_constant_time(self):
        cred = ("zbynek", "hunter2")
        self.assertTrue(g.credential_matches(cred, "zbynek", "hunter2"))
        self.assertFalse(g.credential_matches(cred, "zbynek", "wrong"))
        self.assertFalse(g.credential_matches(cred, "eve", "hunter2"))
        self.assertFalse(g.credential_matches(cred, "zbynek", ""))
        self.assertFalse(g.credential_matches(None, "zbynek", "hunter2"))

    def test_matches_uses_compare_digest_not_equality(self):
        # Lock the timing-safe path: BOTH fields go through hmac.compare_digest
        # (never a bare `==` on the secret, which short-circuits on length/prefix).
        import inspect
        # strip the docstring so the prose "never `==`" cannot satisfy the check
        src = inspect.getsource(g.credential_matches)
        code = src.split('"""')[-1]
        self.assertEqual(code.count("compare_digest"), 2)  # user AND password
        self.assertNotIn("==", code)


class TestSessionStore(unittest.TestCase):
    def test_create_valid_drop(self):
        clock = [1000.0]
        s = g.SessionStore(ttl_s=100, now=lambda: clock[0])
        t = s.create()
        self.assertTrue(s.valid(t))
        s.drop(t)
        self.assertFalse(s.valid(t))

    def test_expiry(self):
        clock = [1000.0]
        s = g.SessionStore(ttl_s=100, now=lambda: clock[0])
        t = s.create()
        clock[0] = 1099.0
        self.assertTrue(s.valid(t))
        clock[0] = 1100.0                      # exactly at expiry -> invalid
        self.assertFalse(s.valid(t))

    def test_unknown_and_empty_token_invalid(self):
        s = g.SessionStore()
        self.assertFalse(s.valid("nope"))
        self.assertFalse(s.valid(""))
        self.assertFalse(s.valid(None))

    def test_tokens_are_fresh_and_unique(self):
        s = g.SessionStore()
        toks = {s.create() for _ in range(50)}
        self.assertEqual(len(toks), 50)        # never reuses / never derives from input
        self.assertTrue(all(len(t) >= 20 for t in toks))


class TestRateLimiter(unittest.TestCase):
    def test_per_ip_block_after_cap_but_other_ip_free(self):
        clock = [0.0]
        rl = g.RateLimiter(max_fails=3, window_s=60, now=lambda: clock[0])
        for _ in range(3):
            rl.record_failure("100.64.0.9")
        self.assertTrue(rl.blocked("100.64.0.9"))
        # The owner's DIFFERENT IP is never affected — no owner DoS.
        self.assertFalse(rl.blocked("100.64.0.2"))

    def test_window_slides(self):
        clock = [0.0]
        rl = g.RateLimiter(max_fails=2, window_s=60, now=lambda: clock[0])
        rl.record_failure("1.1.1.1")
        rl.record_failure("1.1.1.1")
        self.assertTrue(rl.blocked("1.1.1.1"))
        clock[0] = 61.0                        # window elapsed
        self.assertFalse(rl.blocked("1.1.1.1"))

    def test_clear_on_success(self):
        rl = g.RateLimiter(max_fails=2, window_s=60)
        rl.record_failure("1.1.1.1")
        rl.record_failure("1.1.1.1")
        self.assertTrue(rl.blocked("1.1.1.1"))
        rl.clear("1.1.1.1")
        self.assertFalse(rl.blocked("1.1.1.1"))


class TestParsing(unittest.TestCase):
    def test_request_head(self):
        head = b"GET /t/?arg=dev1 HTTP/1.1\r\nHost: x\r\nCookie: a=b\r\n\r\n"
        method, target, ver, headers = g.parse_request_head(head)
        self.assertEqual(method, "GET")
        self.assertEqual(target, "/t/?arg=dev1")
        self.assertEqual(g.header_get(headers, "cookie"), "a=b")
        self.assertEqual(g.header_get(headers, "COOKIE"), "a=b")   # case-insensitive

    def test_request_head_malformed(self):
        self.assertIsNone(g.parse_request_head(b"garbage\r\n\r\n"))
        self.assertIsNone(g.parse_request_head(b""))

    def test_cookie_token(self):
        h = [("Cookie", "foo=1; webterm_session=abc123; bar=2")]
        self.assertEqual(g.cookie_token(h), "abc123")
        self.assertIsNone(g.cookie_token([("Cookie", "foo=1")]))
        self.assertIsNone(g.cookie_token([]))

    def test_build_set_cookie_flags(self):
        c = g.build_set_cookie("tok", 3600)
        self.assertIn("webterm_session=tok", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Strict", c)
        self.assertIn("Path=/", c)
        self.assertIn("Max-Age=3600", c)
        cleared = g.build_set_cookie("", 0, clear=True)
        self.assertIn("Max-Age=0", cleared)

    def test_websocket_upgrade_detection(self):
        ws = [("Upgrade", "websocket"), ("Connection", "Upgrade")]
        self.assertTrue(g.is_websocket_upgrade(ws))
        self.assertTrue(g.is_websocket_upgrade(
            [("Upgrade", "WebSocket"), ("Connection", "keep-alive, Upgrade")]))
        self.assertFalse(g.is_websocket_upgrade([("Connection", "keep-alive")]))

    def test_origin_allowed_fail_closed(self):
        allowed = {"http://100.104.8.125:8080"}
        self.assertTrue(g.origin_allowed(
            [("Origin", "http://100.104.8.125:8080")], allowed))
        self.assertFalse(g.origin_allowed(
            [("Origin", "http://evil.example")], allowed))
        self.assertFalse(g.origin_allowed([], allowed))   # missing Origin -> reject

    def test_login_form_parse(self):
        body = b"username=zbynek&password=p%40ss"
        self.assertEqual(g.parse_login_form(body), ("zbynek", "p@ss"))
        self.assertEqual(g.parse_login_form(b""), ("", ""))


class TestLoginForm(unittest.TestCase):
    def test_form_is_bitwarden_fillable(self):
        html = g.login_form_html()
        self.assertIn('autocomplete="username"', html)
        self.assertIn('autocomplete="current-password"', html)
        self.assertIn('type="password"', html)
        self.assertIn('method="post"', html)
        self.assertIn('action="/login"', html)

    def test_error_variant(self):
        self.assertIn("Nesprávne", g.login_form_html(error=True))
        self.assertNotIn("Nesprávne", g.login_form_html(error=False))


# --------------------------------------------------------------------------- #
# Integration — real gateway + fake ttyd upstream, over 127.0.0.1 sockets.
# --------------------------------------------------------------------------- #

class _FakeTtyd:
    """A minimal 127.0.0.1 upstream standing in for ttyd: replies to a plain
    HTTP GET with a body echoing the request target, and completes a WS upgrade
    then echoes every byte back (to prove bidirectional relay)."""

    def __init__(self):
        self.server = None
        self.seen_targets = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            writer.close()
            return
        line0 = head.split(b"\r\n", 1)[0].decode("latin-1")
        target = line0.split(" ")[1] if " " in line0 else "?"
        self.seen_targets.append(target)
        low = head.lower()
        if b"upgrade: websocket" in low:
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            await writer.drain()
            # echo loop (prove the byte relay is bidirectional + transparent)
            while True:
                try:
                    data = await reader.read(4096)
                except Exception:
                    break
                if not data:
                    break
                writer.write(b"ECHO:" + data)
                await writer.drain()
        else:
            body = ("UPSTREAM saw " + target).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n"
                         b"Connection: close\r\n\r\n" % len(body) + body)
            await writer.drain()
        writer.close()

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()


class _GatewayHarness:
    """Holds the gateway config + a _FakeTtyd; the running server + origin are
    filled in by TestGatewayIntegration._harness (which must learn the ephemeral
    port BEFORE binding so the gateway knows its own origin for the CSWSH check)."""

    def __init__(self, cred="zbynek:s3cret\n", dash="<!DOCTYPE html>DASHBOARD",
                 sessions=None, limiter=None):
        self.cred_text = cred
        self.dash_text = dash
        self.sessions = sessions
        self.limiter = limiter
        self.fake = _FakeTtyd()

    async def request(self, raw_bytes, read_all=True):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(raw_bytes)
        await writer.drain()
        if read_all:
            data = await reader.read(-1)
        else:
            data = await reader.read(4096)
        writer.close()
        return data


def _run(coro):
    return asyncio.run(coro)


class TestGatewayIntegration(unittest.TestCase):
    """End-to-end over real 127.0.0.1 sockets. Each test builds its own harness
    so the gateway's own origin is known before CSWSH checks."""

    async def _harness(self, **kw):
        # Build a harness whose gateway KNOWS its own origin (needed for the WS
        # Origin check). We must know the port first, so: bind, read port, then
        # start the real gateway with origins=[that origin].
        h = _GatewayHarness(**kw)
        h.tmp = tempfile.mkdtemp()
        h.cred_path = Path(h.tmp) / "cred"
        h.cred_path.write_text(h.cred_text, encoding="utf-8")
        h.dash_path = Path(h.tmp) / "index.html"
        h.dash_path.write_text(h.dash_text, encoding="utf-8")
        h.ttyd_port = await h.fake.start()
        # First bind on port 0 to learn the port, close, then rebind with origin.
        probe = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = probe.sockets[0].getsockname()[1]
        probe.close()
        await probe.wait_closed()
        h.origin = "http://127.0.0.1:%d" % port
        h.server = await g.start_gateway(
            "127.0.0.1", port, str(h.dash_path), str(h.cred_path),
            ttyd_host="127.0.0.1", ttyd_port=h.ttyd_port, base_path="/t",
            origins=[h.origin], sessions=h.sessions, limiter=h.limiter)
        h.port = port
        return h

    async def _teardown(self, h):
        h.server.close()
        await h.server.wait_closed()
        await h.fake.stop()

    # -- login / auth ----------------------------------------------------- #

    def test_login_form_served_200(self):
        async def go():
            h = await self._harness()
            try:
                resp = await h.request(b"GET /login HTTP/1.1\r\nHost: x\r\n\r\n")
                self.assertIn(b"200 OK", resp)
                self.assertIn(b'autocomplete="current-password"', resp)
            finally:
                await self._teardown(h)
        _run(go())

    def test_wrong_password_403_no_cookie(self):
        async def go():
            h = await self._harness()
            try:
                body = b"username=zbynek&password=WRONG"
                req = (b"POST /login HTTP/1.1\r\nHost: x\r\n"
                       b"Content-Type: application/x-www-form-urlencoded\r\n"
                       b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                resp = await h.request(req)
                self.assertIn(b"403", resp)
                self.assertNotIn(b"Set-Cookie", resp)
            finally:
                await self._teardown(h)
        _run(go())

    def test_correct_login_sets_httponly_samesite_cookie(self):
        async def go():
            h = await self._harness()
            try:
                body = b"username=zbynek&password=s3cret"
                req = (b"POST /login HTTP/1.1\r\nHost: x\r\n"
                       b"Content-Type: application/x-www-form-urlencoded\r\n"
                       b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                resp = await h.request(req)
                self.assertIn(b"303", resp)                 # redirect to /
                self.assertIn(b"Set-Cookie: webterm_session=", resp)
                self.assertIn(b"HttpOnly", resp)
                self.assertIn(b"SameSite=Strict", resp)
                self.assertNotIn(b"s3cret", resp)           # credential never in cookie
                self.assertNotIn(b"Location: /login", resp)  # goes to dashboard
            finally:
                await self._teardown(h)
        _run(go())

    def test_dashboard_requires_login_then_served(self):
        async def go():
            sessions = g.SessionStore()
            h = await self._harness(sessions=sessions)
            try:
                # no cookie -> redirect to /login
                r1 = await h.request(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
                self.assertIn(b"303", r1)
                self.assertIn(b"Location: /login", r1)
                # a valid session -> dashboard body
                tok = sessions.create()
                r2 = await h.request(
                    b"GET / HTTP/1.1\r\nHost: x\r\nCookie: webterm_session=%s\r\n\r\n"
                    % tok.encode())
                self.assertIn(b"200 OK", r2)
                self.assertIn(b"DASHBOARD", r2)
            finally:
                await self._teardown(h)
        _run(go())

    def test_logout_invalidates_session_and_clears_cookie(self):
        async def go():
            sessions = g.SessionStore()
            h = await self._harness(sessions=sessions)
            try:
                tok = sessions.create()
                resp = await h.request(
                    b"GET /logout HTTP/1.1\r\nHost: x\r\nCookie: webterm_session=%s\r\n\r\n"
                    % tok.encode())
                self.assertIn(b"303", resp)
                self.assertIn(b"Max-Age=0", resp)           # cookie cleared
                self.assertFalse(sessions.valid(tok))       # server-side dropped
            finally:
                await self._teardown(h)
        _run(go())

    def test_rate_limit_blocks_attacker_ip_returns_429(self):
        async def go():
            # A limiter with a tiny cap and a fixed 127.0.0.1 peer (the test's IP).
            limiter = g.RateLimiter(max_fails=2, window_s=60)
            h = await self._harness(limiter=limiter)
            try:
                body = b"username=zbynek&password=WRONG"
                req = (b"POST /login HTTP/1.1\r\nHost: x\r\n"
                       b"Content-Type: application/x-www-form-urlencoded\r\n"
                       b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
                await h.request(req)   # fail 1
                await h.request(req)   # fail 2 -> now at cap
                r3 = await h.request(req)
                self.assertIn(b"429", r3)
                self.assertIn(b"Retry-After", r3)
            finally:
                await self._teardown(h)
        _run(go())

    # -- proxy relay ------------------------------------------------------ #

    def test_http_proxy_relays_to_upstream_when_authed(self):
        async def go():
            sessions = g.SessionStore()
            h = await self._harness(sessions=sessions)
            try:
                tok = sessions.create()
                resp = await h.request(
                    b"GET /t/?arg=dev1 HTTP/1.1\r\nHost: x\r\n"
                    b"Cookie: webterm_session=%s\r\n\r\n" % tok.encode())
                self.assertIn(b"200 OK", resp)
                self.assertIn(b"UPSTREAM saw /t/?arg=dev1", resp)  # relayed + target intact
            finally:
                await self._teardown(h)
        _run(go())

    def test_unauth_proxy_http_redirects_to_login(self):
        async def go():
            h = await self._harness()
            try:
                resp = await h.request(b"GET /t/ HTTP/1.1\r\nHost: x\r\n\r\n")
                self.assertIn(b"303", resp)
                self.assertIn(b"Location: /login", resp)
            finally:
                await self._teardown(h)
        _run(go())

    def test_ws_relay_bidirectional_with_good_origin(self):
        async def go():
            sessions = g.SessionStore()
            h = await self._harness(sessions=sessions)
            try:
                tok = sessions.create()
                reader, writer = await asyncio.open_connection("127.0.0.1", h.port)
                req = (b"GET /t/ws HTTP/1.1\r\nHost: x\r\n"
                       b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Sec-WebSocket-Version: 13\r\n"
                       b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                       b"Origin: " + h.origin.encode() + b"\r\n"
                       b"Cookie: webterm_session=" + tok.encode() + b"\r\n\r\n")
                writer.write(req)
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                self.assertIn(b"101", head)                 # upgrade relayed
                writer.write(b"hello")                      # client -> upstream
                await writer.drain()
                echoed = await reader.readexactly(len(b"ECHO:hello"))
                self.assertEqual(echoed, b"ECHO:hello")     # upstream -> client
                writer.close()
            finally:
                await self._teardown(h)
        _run(go())

    def test_ws_cross_origin_refused_cswsh(self):
        async def go():
            sessions = g.SessionStore()
            h = await self._harness(sessions=sessions)
            try:
                tok = sessions.create()
                req = (b"GET /t/ws HTTP/1.1\r\nHost: x\r\n"
                       b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Origin: http://evil.example\r\n"
                       b"Cookie: webterm_session=" + tok.encode() + b"\r\n\r\n")
                resp = await h.request(req)
                self.assertIn(b"403", resp)
                self.assertNotIn(b"101", resp)
            finally:
                await self._teardown(h)
        _run(go())

    def test_ws_unauth_returns_401_never_reaches_upstream(self):
        async def go():
            h = await self._harness()
            try:
                req = (b"GET /t/ws HTTP/1.1\r\nHost: x\r\n"
                       b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Origin: " + h.origin.encode() + b"\r\n\r\n")
                resp = await h.request(req)
                self.assertIn(b"401", resp)
                self.assertEqual(h.fake.seen_targets, [])   # upstream never touched
            finally:
                await self._teardown(h)
        _run(go())


if __name__ == "__main__":
    unittest.main()
