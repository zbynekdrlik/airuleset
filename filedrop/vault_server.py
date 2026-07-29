#!/usr/bin/env python3
"""One-shot endpoint that RECEIVES a credential from the user's browser (#144).

The user opens the printed URL, types the value, submits — and this process
writes it 0600 under `~/.claude/secrets/` and ends. The session that printed the
URL never sees the value: it learns only that the NAME is ready.

Usage:
    AIRULESET_VAULT_TOKEN=<token> \
        python3 vault_server.py <port> <bind_ips_csv> <name> <ttl_s> <keep_s>

The token arrives through the ENVIRONMENT, never argv: `/proc/<pid>/cmdline` is
mode 0444 and readable by every uid on the box, while `/proc/<pid>/environ` is
0400, owner only. These boxes host foreign uids on purpose, and the token is
this endpoint's only auth — in argv it would let any local account POST its own
value and substitute the credential the agent is about to use.

  GET  /healthz    -> 204, no body. The CLI's liveness probe, so the probe
                      never has to send the token to whatever is listening.
  GET  /<token>/   -> the form (a password field; a textarea for a key)
  POST /<token>/   -> the raw request BODY is the value, stored byte-exact,
                      after which this process exits. One value, one endpoint.

Deliberately a SEPARATE server from `upload_server.py`, never a mode of it: that
one saves with the ambient umask under `~/uploads/` and writes
`SAVED <full path>` to its log, and a credential landing there is not
recoverable after the fact. Sharing a `do_PUT` between the two would leave the
safe and unsafe paths one branch apart.

THREE THINGS THIS PROCESS MUST NEVER DO, and how each is prevented:

  * echo the value — the only responses are fixed literals; no error path
    interpolates the body, and there is no GET route that reads a stored value;
  * log the value or the capability token — `log_message` is a no-op (the
    request LINE carries the token), and the only file written is the store's
    own metadata log via `vault.log_event`, which has no value parameter;
  * listen anywhere a stranger can reach — every bind address is re-checked
    here by `is_private()`, an implementation deliberately INDEPENDENT of
    `filedrop._is_private`, so a future regression in that one still cannot
    put a credential endpoint on a box's public IP.

AND THE ONE THIS PROCESS CANNOT PREVENT (#153 finding 3). The reasoning above
is about a local uid reading argv — but the capability URL, token and all, is
PRINTED INTO THE SESSION TRANSCRIPT by design, because printing it is how the
user receives it. So for the endpoint's whole TTL (default 600s, capped 3600s)
anyone who can read the transcript AND reach one of the private bind addresses
can POST their own value first and SUBSTITUTE the credential the agent is about
to use. The nonce binds this endpoint to the REQUEST that created it — it does
not authenticate the poster, and nothing here can: the endpoint's entire threat
model is "whoever holds the URL is the user". Keep TTLs short, and treat a
credential whose URL sat in a transcript as one that may have been chosen by
someone else.
"""
import hmac
import ipaddress
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Launched BY PATH, so sys.path[0] is filedrop/ itself and `import filedrop`
# would fail. The store is imported rather than re-implemented on purpose:
# exactly ONE piece of code writes a credential to disk.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from filedrop.vault import (MAX_SECRET_BYTES, SecretError,  # noqa: E402
                            check_name, store_value)

if len(sys.argv) < 6:
    sys.exit("usage: AIRULESET_VAULT_TOKEN=<token> vault_server.py <port> "
             "<bind_ips_csv> <name> <ttl_s> <keep_s>")
TOKEN = os.environ.get("AIRULESET_VAULT_TOKEN") or ""
# Ties this process to ONE request. If the name is forgotten (or the request
# replaced) while this endpoint is still alive, the nonce no longer matches and
# the store refuses us — so a revoked URL cannot repopulate the name.
NONCE = os.environ.get("AIRULESET_VAULT_NONCE") or None
if not TOKEN:
    sys.exit("vault: AIRULESET_VAULT_TOKEN is required — the token is passed "
             "through the environment (0400) and never in argv (0444)")
PORT = int(sys.argv[1])
BIND_IPS = [x for x in sys.argv[2].split(",") if x]
NAME = sys.argv[3]
TTL = int(sys.argv[4])
KEEP = int(sys.argv[5])


def is_private(ip):
    """True only for an address a stranger cannot reach.

    A second, independent implementation of the policy `filedrop._is_private`
    states for the file endpoints — the point of duplicating it is that this
    process must refuse a public bind even if that function is one day widened.
    Loopback IS accepted here (unlike there, where the endpoint must be
    reachable BY the user): it is strictly more private than tailscale, since
    it cannot leave the box at all.
    """
    # `ipaddress` rather than int()-per-octet: that accepted " 10" and "+10",
    # and read "010" as decimal 10 where inet_aton reads it as OCTAL 8 — a
    # spelling that could pass the check and bind something else entirely.
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ipaddress.AddressValueError, ValueError):
        return False
    a, b = (int(x) for x in str(addr).split(".")[:2])
    if a == 127:                        # loopback — never leaves this box
        return True
    if a == 100 and 64 <= b <= 127:     # tailscale CGNAT (WireGuard-encrypted)
        return True
    if a == 10:                         # RFC1918 /8 — the dev LAN
        return True
    if a == 192 and b == 168:           # RFC1918 /16
        return True
    return False                        # public / 172.16-31 docker / link-local


try:
    check_name(NAME)
except SecretError as e:
    sys.exit("vault: %s" % e)
if not BIND_IPS:
    sys.exit("vault: no bind address given")
for _ip in BIND_IPS:
    if not is_private(_ip):
        sys.exit("vault: refusing to bind non-private address %s — a credential "
                 "endpoint may only listen on tailscale/LAN/loopback" % _ip)

if TTL <= 0:
    # FATAL, not "no timer wanted": an endpoint that receives credentials and
    # never shuts itself down is the one shape this design must not have.
    sys.exit("vault: ttl must be positive (got %d) — an endpoint with no "
             "self-shutdown timer would live until reboot" % TTL)

# DAEMON, always (#114): a non-daemon Timer is joined at interpreter exit
# and would park every other exit path for the rest of the TTL, then end
# the process with its own status instead.
_ttl_timer = threading.Timer(TTL, lambda: os._exit(0))
_ttl_timer.daemon = True
_ttl_timer.start()

# Served RAW (PAGE.encode(), never .format()), so braces stay SINGLE — a doubled
# `{{` renders literally and silently breaks the CSS/JS (#18, live). The icon is
# an inline data: URI so no browser ever requests /favicon.ico at the origin
# root, which this endpoint would (correctly) 404 — an unauthenticated route on
# a credential endpoint is not an option, and a console error on the one page
# the user personally opens is a bug (#117). Percent-encoded and ASCII-only: a
# data: URI defaults to US-ASCII and a raw `#` would truncate it at a fragment.
PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Bezpecne odoslanie hesla / kluca</title>
<link rel=icon href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2016'%3E%3Crect%20width='16'%20height='16'%20rx='3'%20fill='%230f172a'/%3E%3Cpath%20d='M5%207V5.5a3%203%200%20016%200V7h1v6H4V7zm1.5%200h3V5.5a1.5%201.5%200%2000-3%200z'%20fill='%2338bdf8'/%3E%3C/svg%3E">
<style>
 body{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}
 .card{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}
 h1{font-size:18px;margin:0 0 4px} p{color:#94a3b8;margin:.2em 0 1em;font-size:14px}
 code{color:#38bdf8}
 input[type=password],textarea{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;
   border:1px solid #475569;background:#0f172a;color:#e2e8f0;font:15px ui-monospace,monospace}
 textarea{min-height:140px;display:none}
 label.multi{display:block;margin:10px 0;color:#94a3b8;font-size:13px}
 button{margin-top:14px;padding:12px 18px;border:0;border-radius:8px;background:#38bdf8;
   color:#0f172a;font-weight:600;font-size:15px;cursor:pointer}
 button[disabled]{opacity:.5;cursor:default}
 #status{margin-top:14px;font-size:14px;white-space:pre-line}
 .ok{color:#4ade80} .err{color:#f87171}
</style>
<div class=card>
 <h1>Odoslanie hodnoty pre <code id=nm></code></h1>
 <p>Vloz heslo / kluc / token. Hodnota sa ulozi len na server (prava 0600) —
    <b>nikdy sa nezobrazi v chate</b> a Claude ju nevidi. Odoslat sa da raz;
    potom sa tato adresa zavrie.</p>
 <input id=v type=password autocomplete=off spellcheck=false placeholder="hodnota">
 <textarea id=t autocomplete=off spellcheck=false placeholder="viacriadkova hodnota"></textarea>
 <label class=multi><input id=m type=checkbox> viacriadkove (napr. SSH kluc)</label>
 <button id=b>Odoslat</button>
 <div id=status></div>
</div>
<script>
const v=document.getElementById('v'),t=document.getElementById('t'),m=document.getElementById('m'),
 b=document.getElementById('b'),st=document.getElementById('status');
document.getElementById('nm').textContent=NAME_PLACEHOLDER;
m.onchange=()=>{const multi=m.checked;
 t.style.display=multi?'block':'none';v.style.display=multi?'none':'block';
 (multi?t:v).focus()};
b.onclick=()=>{
 const val=m.checked?t.value:v.value;
 if(!val){st.className='err';st.textContent='Prazdna hodnota.';return}
 b.disabled=true;st.className='';st.textContent='Odosielam...';
 fetch(location.pathname,{method:'POST',cache:'no-store',
   headers:{'Content-Type':'text/plain; charset=utf-8'},body:val})
  .then(r=>{if(r.status===200){v.value='';t.value='';
     st.className='ok';st.textContent='Prijate. Okno mozes zavriet.';
     b.style.display='none';m.parentNode.style.display='none';
     v.style.display='none';t.style.display='none'}
    else{b.disabled=false;st.className='err';st.textContent='Chyba '+r.status}})
  .catch(()=>{b.disabled=false;st.className='err';
     st.textContent='Sietova chyba - skus znova.'});
};
v.focus();
</script></html>"""


# A credential endpoint serves exactly one browser for a few minutes. Unbounded
# threads are pure abuse surface, so refuse past a small cap rather than let a
# single host spawn one thread per connection for the whole TTL.
MAX_CONNECTIONS = 16


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._live = 0
        self._lock = threading.Lock()

    def process_request(self, request, client_address):
        with self._lock:
            if self._live >= MAX_CONNECTIONS:
                self.shutdown_request(request)
                return
            self._live += 1
        super().process_request(request, client_address)

    def shutdown_request(self, request):
        super().shutdown_request(request)

    def close_request(self, request):
        super().close_request(request)
        with self._lock:
            self._live = max(0, self._live - 1)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, *a):
        """Silence. The request LINE contains the capability token, and this
        endpoint's only permitted output is the store's metadata log."""

    def _parts(self):
        return [p for p in self.path.split("?")[0].split("/") if p]

    def _txt(self, code, msg):
        b = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    @staticmethod
    def _is_token(segment):
        """Constant-time compare — free, and the alternative is a timing
        oracle on the endpoint's only authentication."""
        return hmac.compare_digest(segment, TOKEN)

    def do_GET(self):
        p = self._parts()
        if p == ["healthz"]:
            # The ONE unauthenticated route: a fixed 204 with no body, so the
            # CLI can confirm the endpoint is up without sending the token to
            # whatever happens to be listening on that port.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        # The token segment is compared RAW and never percent-decoded — this is
        # the endpoint's only auth, and decoding it would let `%74ok...`
        # authenticate as `tok...` (the #116 lesson, kept on purpose).
        if len(p) == 1 and self._is_token(p[0]):
            body = PAGE.replace("NAME_PLACEHOLDER", repr(NAME)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            # The page is entirely self-contained (inline style/script, inline
            # data: icon) and posts only to its own path, so it can afford the
            # tightest policy there is.
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._txt(404, "not found")

    def do_POST(self):
        p = self._parts()
        if len(p) != 1 or not self._is_token(p[0]):
            return self._txt(404, "not found")
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._txt(501, "chunked transfer-encoding not supported")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            # A garbage length used to raise into socketserver.handle_error,
            # which prints a traceback into the endpoint's own log — noise any
            # reachable host could generate at will.
            return self._txt(400, "malformed Content-Length")
        if length <= 0:
            return self._txt(411, "Content-Length required (got none/zero)")
        if length > MAX_SECRET_BYTES:
            self._drain(length)          # read it so the reply reaches the client
            return self._txt(413, "value over the %d-byte cap" % MAX_SECRET_BYTES)
        data = b""
        while len(data) < length:
            chunk = self.rfile.read(min(1 << 16, length - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) != length:
            # NEVER report the bytes themselves, only the counts.
            return self._txt(400, "incomplete body: got %d of %d bytes"
                                  % (len(data), length))
        try:
            store_value(NAME, data, keep_s=KEEP, nonce=NONCE)
        except SecretError as e:
            # `e` is raised by the store and names the NAME and the cap only —
            # no code path there puts the value into an exception message.
            return self._txt(409, "not stored: %s" % e)
        self._txt(200, "ok")
        # One value, one endpoint. Flush first: bytes already handed to the
        # kernel are still delivered after the process goes away, so this needs
        # no grace period and there is nothing to time out.
        try:
            self.wfile.flush()
        except OSError:
            # airuleset:script-ok the value is already safely on disk; a client
            # that hung up before the ack is not a reason to stay alive and
            # accept a second submission.
            pass
        os._exit(0)

    # Draining an oversize body is a courtesy — it lets the client actually
    # READ the 413 instead of getting a reset — so it must never cost more than
    # a courtesy. A client that announces a length and then sends nothing would
    # otherwise hold the handler for the full 60s socket timeout, which is a
    # free slowloris against a single-purpose endpoint.
    DRAIN_TIMEOUT_S = 2

    def _drain(self, length):
        left = min(length, MAX_SECRET_BYTES * 4)
        try:
            self.connection.settimeout(self.DRAIN_TIMEOUT_S)
            while left > 0:
                chunk = self.rfile.read(min(1 << 16, left))
                if not chunk:
                    return
                left -= len(chunk)
        except OSError:
            return          # promised bytes that never came: stop waiting
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError as e:
                sys.stderr.write("vault: could not restore timeout: %s\n" % e)


_servers = []
for _h in BIND_IPS:
    try:
        _s = BoundedServer((_h, PORT), H)
    except OSError as _e:
        sys.stderr.write("vault: skip bind %s:%d (%s)\n" % (_h, PORT, _e))
        continue
    _s.daemon_threads = True
    _servers.append(_s)
if not _servers:
    sys.exit("vault: no address in %r could bind :%d" % (BIND_IPS, PORT))
for _s in _servers:
    # The token is NOT printed: this file is the endpoint's diagnostic log and
    # the token is its auth.
    sys.stderr.write("vault-endpoint: bound %s:%d\n" % (_s.server_address[0], PORT))
sys.stderr.flush()
for _s in _servers[:-1]:
    threading.Thread(target=_s.serve_forever, daemon=True).start()
_servers[-1].serve_forever()
