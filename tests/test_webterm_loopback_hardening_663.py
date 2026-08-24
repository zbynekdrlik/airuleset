"""#663 — harden the webterm gateway loopback origin on the multi-tenant subdev box.

The Access-mode lanes (owner/david/marek) used to bind the gateway AND ttyd on TCP
`127.0.0.1:<port>`, reachable by EVERY local unix account on a shared box — so a peer
account could forge `Cf-Access-Authenticated-User-Email` at another lane's gateway
port, OR reach its auth-less ttyd directly, and drive a shell as that account (both
live-reproduced, see the #663 validation comment).

The fix moves both hops onto mode-0700 UNIX-domain sockets in the account's
`/run/user/<uid>` runtime dir (cloudflared `service: unix:<path>`, ttyd `-i <sock>`,
gateway `asyncio.start_unix_server` / `open_unix_connection`) — filesystem
permissions become the account boundary, closing BOTH vectors with zero new deps.

This file locks two things:
  * TRANSPORT — the gateway can actually bind + serve + proxy over a unix socket,
    preserving the trust-header auth behaviour (functional, over real sockets).
  * NO-TCP-SURFACE — every Access-mode lane's provisioned artifacts (gateway unit,
    ttyd launch script, cloudflared config) expose NO TCP loopback origin: they use
    the runtime-dir unix sockets, never `--bind/--port`/`--ttyd-port`/`-p <port>`/
    `http://127.0.0.1:<port>`.

Nothing here binds a TCP interface (the security invariant); the functional test
binds unix sockets under the test's own tmpdir.
"""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w                    # noqa: E402
import cli_webterm_gateway as g            # noqa: E402
import cli_webterm_david as dvd            # noqa: E402
import cli_webterm_marek as mrk            # noqa: E402
import cli_webterm_tunnel as tun           # noqa: E402
import cli_webterm_access as access        # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# TRANSPORT — a real gateway bound on a UNIX socket, in front of a fake ttyd
# also on a unix socket. Proves the new transport preserves auth + relay.
# --------------------------------------------------------------------------- #

class _FakeTtydUnix:
    """A minimal ttyd stand-in bound on a UNIX socket: HTTP GET echoes the
    target; a WS upgrade completes then echoes bytes (prove bidirectional relay)."""

    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.server = None
        self.seen_targets = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self._handle, path=self.sock_path)
        return self.server

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except Exception:
            writer.close()
            return
        line0 = head.split(b"\r\n", 1)[0].decode("latin-1")
        target = line0.split(" ")[1] if " " in line0 else "?"
        self.seen_targets.append(target)
        if b"upgrade: websocket" in head.lower():
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            await writer.drain()
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


class TestGatewayUnixSocketTransport(unittest.TestCase):
    HDR = "Cf-Access-Authenticated-User-Email"

    async def _harness(self):
        tmp = tempfile.mkdtemp()
        self.tmp = tmp
        dash = Path(tmp) / "index.html"
        dash.write_text("<!DOCTYPE html>DASHBOARD-UNIX", encoding="utf-8")
        ttyd_sock = os.path.join(tmp, "ttyd.sock")
        gw_sock = os.path.join(tmp, "gw.sock")
        fake = _FakeTtydUnix(ttyd_sock)
        await fake.start()
        # trust-access-header (Access) mode + unix bind + unix ttyd upstream.
        server = await g.start_gateway(
            None, 0, str(dash), None,
            base_path="/t", origins=[],
            trust_access_header=self.HDR,
            socket_path=gw_sock, ttyd_socket=ttyd_sock)
        return tmp, fake, server, gw_sock

    async def _request(self, gw_sock, raw):
        reader, writer = await asyncio.open_unix_connection(path=gw_sock)
        writer.write(raw)
        await writer.drain()
        data = await reader.read(-1)
        writer.close()
        return data

    def test_socket_created_in_a_private_place_and_serves_dashboard(self):
        async def go():
            tmp, fake, server, gw_sock = await self._harness()
            try:
                # the bind socket file exists
                self.assertTrue(os.path.exists(gw_sock))
                # authed (trusted header present) GET / -> dashboard
                resp = await self._request(
                    gw_sock,
                    b"GET / HTTP/1.1\r\nHost: unix\r\n"
                    b"%s: forged@example.com\r\n\r\n" % self.HDR.encode())
                self.assertIn(b"200 OK", resp)
                self.assertIn(b"DASHBOARD-UNIX", resp)
                # NO trusted header -> fail closed (403), never a shell
                resp2 = await self._request(
                    gw_sock, b"GET / HTTP/1.1\r\nHost: unix\r\n\r\n")
                self.assertIn(b"403", resp2)
            finally:
                server.close()
                await server.wait_closed()
                await fake.stop()
        _run(go())

    def test_http_proxy_relays_to_ttyd_over_unix(self):
        async def go():
            tmp, fake, server, gw_sock = await self._harness()
            try:
                resp = await self._request(
                    gw_sock,
                    b"GET /t/ HTTP/1.1\r\nHost: unix\r\n"
                    b"%s: forged@example.com\r\n\r\n" % self.HDR.encode())
                self.assertIn(b"200 OK", resp)
                self.assertIn(b"UPSTREAM saw /t/", resp)
            finally:
                server.close()
                await server.wait_closed()
                await fake.stop()
        _run(go())

    def test_websocket_relay_bidirectional_over_unix(self):
        async def go():
            tmp, fake, server, gw_sock = await self._harness()
            try:
                reader, writer = await asyncio.open_unix_connection(path=gw_sock)
                writer.write(
                    b"GET /t/ws HTTP/1.1\r\nHost: unix\r\nOrigin: http://unix\r\n"
                    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    b"%s: forged@example.com\r\n\r\n" % self.HDR.encode())
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                self.assertIn(b"101", head)
                writer.write(b"ping")
                await writer.drain()
                echoed = await asyncio.wait_for(reader.read(64), timeout=5)
                self.assertEqual(echoed, b"ECHO:ping")
                writer.close()
            finally:
                server.close()
                await server.wait_closed()
                await fake.stop()
        _run(go())


# --------------------------------------------------------------------------- #
# NO-TCP-SURFACE — the provisioned artifacts of every Access lane.
# --------------------------------------------------------------------------- #

class TestNoTcpLoopbackSurface(unittest.TestCase):
    """The whole point of #663: an Access-mode lane must provision NO TCP
    loopback listener for either the gateway OR ttyd. It uses unix sockets in
    the account runtime dir instead."""

    def _assert_gateway_unit_is_unix(self, unit, gw_base, ttyd_base):
        exec_line = [ln for ln in unit.splitlines() if "ExecStart" in ln][0]
        # unix socket flags present
        self.assertIn("--socket %t/" + gw_base, exec_line)
        self.assertIn("--ttyd-socket %t/" + ttyd_base, exec_line)
        # NO TCP loopback surface in the ExecStart
        self.assertNotIn("--bind ", exec_line)
        self.assertNotIn("--port ", exec_line)
        self.assertNotIn("--ttyd-host", exec_line)
        self.assertNotIn("--ttyd-port", exec_line)
        # still Access mode (trust header), not a password
        self.assertIn("--trust-access-header " + access.WEBTERM_ACCESS_TRUST_HEADER, exec_line)
        self.assertNotIn("--cred ", exec_line)

    def test_owner_access_gateway_unit_has_no_tcp(self):
        unit = w._render_webterm_gateway_unit(w.WEBTERM_TTYD_BIND, access_mode=True)
        self._assert_gateway_unit_is_unix(
            unit, w.WEBTERM_GATEWAY_SOCK_BASENAME, w.WEBTERM_TTYD_SOCK_BASENAME)

    def test_david_gateway_unit_has_no_tcp(self):
        unit = dvd.render_david_gateway_unit()
        self._assert_gateway_unit_is_unix(
            unit, dvd.WEBTERM_DAVID_GATEWAY_SOCK_BASENAME,
            dvd.WEBTERM_DAVID_TTYD_SOCK_BASENAME)

    def test_marek_gateway_unit_has_no_tcp(self):
        unit = mrk.render_marek_gateway_unit()
        self._assert_gateway_unit_is_unix(
            unit, mrk.WEBTERM_MAREK_GATEWAY_SOCK_BASENAME,
            mrk.WEBTERM_MAREK_TTYD_SOCK_BASENAME)

    def _assert_launch_is_unix(self, script, ttyd_base):
        # binds a unix socket in the account runtime dir, never a TCP port
        self.assertIn("XDG_RUNTIME_DIR", script)
        self.assertIn(ttyd_base, script)
        self.assertIn("-i ", script)      # ttyd interface = the socket
        self.assertNotIn("-p ", script)   # no TCP port
        self.assertNotIn("127.0.0.1", script)
        self.assertIn("rm -f", script)    # unlink a stale socket before bind

    def test_owner_launch_socket_variant(self):
        script = w.render_webterm_launch_script(
            ttyd_socket_basename=w.WEBTERM_TTYD_SOCK_BASENAME)
        self._assert_launch_is_unix(script, w.WEBTERM_TTYD_SOCK_BASENAME)

    def test_david_launch_socket_variant(self):
        script = w.render_webterm_launch_script(
            inventory_path=dvd.WEBTERM_DAVID_INVENTORY_PATH,
            ttyd_socket_basename=dvd.WEBTERM_DAVID_TTYD_SOCK_BASENAME)
        self._assert_launch_is_unix(script, dvd.WEBTERM_DAVID_TTYD_SOCK_BASENAME)

    def test_marek_launch_socket_variant(self):
        script = w.render_webterm_launch_script(
            inventory_path=mrk.WEBTERM_MAREK_INVENTORY_PATH,
            ttyd_socket_basename=mrk.WEBTERM_MAREK_TTYD_SOCK_BASENAME)
        self._assert_launch_is_unix(script, mrk.WEBTERM_MAREK_TTYD_SOCK_BASENAME)

    def test_cloudflared_config_uses_unix_service_not_tcp(self):
        # every managed tunnel config points at a unix: origin, never http://127.0.0.1
        owner = tun.render_cloudflared_tunnel_config(
            "uuid", "/creds.json", "zbynek.newlevel.media",
            "unix:" + w.webterm_runtime_socket_abs(w.WEBTERM_GATEWAY_SOCK_BASENAME))
        self.assertIn("service: unix:/run/user/", owner)
        self.assertNotIn("http://127.0.0.1", owner)

    def test_runtime_socket_abs_and_unit_specifier_name_the_same_file(self):
        # Genuinely TIE the two surfaces the fix must keep in sync: the systemd unit
        # reaches the socket via `%t/<basename>` (systemd resolves %t to
        # /run/user/<uid>) and the cloudflared config via the absolute
        # /run/user/<uid>/<basename>. Extract the basename from the RENDERED unit's
        # `--socket %t/...` and assert webterm_runtime_socket_abs resolves that SAME
        # basename under /run/user/<uid> — not a re-derivation of its own formula.
        import re as _re
        unit = w._render_webterm_gateway_unit(w.WEBTERM_TTYD_BIND, access_mode=True)
        exec_line = next(ln for ln in unit.splitlines() if ln.startswith("ExecStart="))
        mo = _re.search(r"--socket %t/(\S+)", exec_line)
        self.assertIsNotNone(mo, "Access-mode unit must bind --socket %t/<basename>")
        base = mo.group(1)
        self.assertEqual(base, w.WEBTERM_GATEWAY_SOCK_BASENAME)
        # %t == $XDG_RUNTIME_DIR == /run/user/<uid>, so the unit's `%t/<base>` and the
        # config's abs path are the SAME file for the account that runs the service.
        self.assertEqual(w.webterm_runtime_socket_abs(base),
                         "/run/user/%d/%s" % (os.getuid(), base))


if __name__ == "__main__":
    unittest.main()
