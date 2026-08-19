#!/usr/bin/env python3
"""One-shot endpoint that SHOWS a credential to the owner's browser (#580).

The reverse of filedrop/vault_server.py: that one RECEIVES a value from the
user; this one RENDERS a value the box already holds — a vault NAME or a
`--file` durable path — to the owner ONCE, then tears itself down. It exists so
a credential the box holds can reach the owner without being typed into the
chat (which writes it into the transcript forever) or fetched with "run cat
yourself".

Usage:
    AIRULESET_VAULT_TOKEN=<token> \
        python3 show_server.py <port> <bind_ips_csv> <kind> <locator> <ttl_s>

  kind = "name"  -> locator is a vault NAME, read via vault.read_value at GET
  kind = "file"  -> locator is a validated PATH, read via vault.read_show_file

The token arrives through the ENVIRONMENT, never argv: /proc/<pid>/cmdline is
0444 and readable by every uid on the box, while /proc/<pid>/environ is 0400,
owner only. The NAME / PATH in argv is NOT the value, so passing it there is
fine — the SESSION that spawned this process only ever handled the name/path,
and this process reads the value only at GET time.

  GET  /healthz    -> 204, no body. The CLI liveness probe — it never touches
                      the value, so probing it does NOT consume the one-shot.
  GET  /<token>/   -> the value page, rendered ONCE. The value is embedded as a
                      JS string (injection-escaped) so the copy button is
                      byte-exact; after serving it this process exits. A second
                      view finds nothing listening.

THREE THINGS THIS PROCESS MUST NEVER DO, and how each is prevented:

  * echo the value into its OWN output — the value is written only into the
    HTTP response body; `log_message` is a no-op and every stderr line is a
    fixed literal or a bind diagnostic, never the value;
  * log the value or the token — the only file written is the store's metadata
    log via `vault.log_event` (event + a value-free label, no value parameter);
  * listen anywhere a stranger can reach — every bind address is re-checked
    here by `is_private()`, deliberately INDEPENDENT of `filedrop._is_private`.

AND THE ONE IT CANNOT PREVENT (same as vault_server.py): the capability URL,
token and all, is PRINTED INTO THE SESSION TRANSCRIPT by design, because that
is how the owner receives it. So for the endpoint's whole TTL anyone who can
read the transcript AND reach a private bind address can open the URL and see
the value FIRST — the token is the endpoint's entire auth, and nothing here can
authenticate the viewer. Keep TTLs short; the endpoint is one-shot, so the
FIRST viewer is the only one, whoever it is.
"""
import hmac
import ipaddress
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Launched BY PATH, so sys.path[0] is filedrop/ itself and `import filedrop`
# would fail. The store is imported rather than re-implemented on purpose:
# exactly ONE piece of code reads a credential value.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from filedrop.vault import (MAX_SECRET_BYTES, SecretError,  # noqa: E402
                            check_name, log_event, read_show_file,
                            read_value, show_log_label, validate_show_file)

if len(sys.argv) < 6:
    sys.exit("usage: AIRULESET_VAULT_TOKEN=<token> show_server.py <port> "
             "<bind_ips_csv> <kind> <locator> <ttl_s>")
TOKEN = os.environ.get("AIRULESET_VAULT_TOKEN") or ""
if not TOKEN:
    sys.exit("show: AIRULESET_VAULT_TOKEN is required — the token is passed "
             "through the environment (0400) and never in argv (0444)")
PORT = int(sys.argv[1])
BIND_IPS = [x for x in sys.argv[2].split(",") if x]
KIND = sys.argv[3]
LOCATOR = sys.argv[4]
TTL = int(sys.argv[5])
LABEL = show_log_label(KIND, LOCATOR)


def is_private(ip):
    """True only for an address a stranger cannot reach.

    A second, independent implementation of the policy `filedrop._is_private`
    states for the file endpoints — the point of duplicating it is that this
    process must refuse a public bind even if that function is one day widened.
    Loopback IS accepted (unlike there): it is strictly more private than
    tailscale, since it cannot leave the box at all.
    """
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


if KIND not in ("name", "file"):
    sys.exit("show: kind must be 'name' or 'file' (got %r)" % KIND)
# Fail fast BEFORE binding on a bad source — the same discipline vault_server.py
# applies to an invalid name. The value itself is still read only at GET time.
try:
    if KIND == "name":
        check_name(LOCATOR)
    else:
        validate_show_file(LOCATOR)
except SecretError as e:
    sys.exit("show: %s" % e)
if not BIND_IPS:
    sys.exit("show: no bind address given")
for _ip in BIND_IPS:
    if not is_private(_ip):
        sys.exit("show: refusing to bind non-private address %s — a credential "
                 "endpoint may only listen on tailscale/LAN/loopback" % _ip)
if TTL <= 0:
    sys.exit("show: ttl must be positive (got %d) — an endpoint with no "
             "self-shutdown timer would live until reboot" % TTL)


def _expire():
    log_event("shown-expired", LABEL)
    os._exit(0)


# DAEMON, always (#114): a non-daemon Timer is joined at interpreter exit and
# would park every other exit path for the rest of the TTL.
_ttl_timer = threading.Timer(TTL, _expire)
_ttl_timer.daemon = True
_ttl_timer.start()


def _js(text):
    """`text` as a JS string literal safe to inline inside a <script> — JSON
    plus the extra escapes that stop a `</script>` (or any HTML-context
    confusion) breaking out of the script element. The value is only ever
    ASSIGNED to a variable, never eval'd."""
    s = json.dumps(text)
    for a, b in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                 (" ", "\\u2028"), (" ", "\\u2029")):
        s = s.replace(a, b)
    return s


# Served RAW (PAGE.replace(...).encode(), never .format()), so braces stay
# SINGLE — a doubled `{{` renders literally and breaks the CSS/JS (#18). The
# icon is an inline data: URI so no browser requests /favicon.ico (which this
# endpoint would 404 — an unauthenticated route on a credential endpoint is not
# an option). The value is filled into a readonly textarea by JS (never
# innerHTML), so the copy is byte-exact and there is no HTML-escaping pitfall;
# the copy button works over plain HTTP via execCommand where the async
# clipboard API is blocked (a non-secure context).
PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Zobrazenie tajomstva</title>
<link rel=icon href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2016'%3E%3Crect%20width='16'%20height='16'%20rx='3'%20fill='%230f172a'/%3E%3Cpath%20d='M5%207V5.5a3%203%200%20016%200V7h1v6H4V7zm1.5%200h3V5.5a1.5%201.5%200%2000-3%200z'%20fill='%2338bdf8'/%3E%3C/svg%3E">
<style>
 body{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}
 .card{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}
 h1{font-size:18px;margin:0 0 4px} p{color:#94a3b8;margin:.2em 0 1em;font-size:14px}
 code{color:#38bdf8}
 textarea{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;min-height:96px;
   border:1px solid #475569;background:#0f172a;color:#e2e8f0;font:15px ui-monospace,monospace}
 button{margin-top:14px;padding:12px 18px;border:0;border-radius:8px;background:#38bdf8;
   color:#0f172a;font-weight:600;font-size:15px;cursor:pointer}
 #status{margin-top:12px;font-size:14px}
 .ok{color:#4ade80}
 .warn{color:#fbbf24;font-size:13px;margin-top:10px}
</style>
<div class=card>
 <h1>Tajomstvo <code id=nm></code></h1>
 <p>Hodnota sa zobrazuje <b>len raz</b> — po zobrazení sa táto adresa zavrie.
    Skopíruj si ju teraz. <b>Do chatu ju NEPÍŠ.</b></p>
 <textarea id=v readonly spellcheck=false></textarea>
 <button id=b>Kopírovať</button>
 <div id=status></div>
 <div class=warn>Po zatvorení okna už hodnota nie je dostupná — vygeneruj ju nanovo.</div>
</div>
<script>
const V=VALUE_PLACEHOLDER,v=document.getElementById('v'),
 b=document.getElementById('b'),st=document.getElementById('status');
document.getElementById('nm').textContent=NAME_PLACEHOLDER;
v.value=V;
b.onclick=()=>{
 v.focus();v.select();
 let ok=false;
 try{ok=document.execCommand('copy')}catch(e){}
 if(navigator.clipboard){navigator.clipboard.writeText(V).then(()=>{
   st.className='ok';st.textContent='Skopírované do schránky.'}).catch(()=>{})}
 st.className='ok';st.textContent=ok?'Skopírované do schránky.':
   'Označené — skopíruj ručne (Ctrl+C).';
};
</script></html>"""


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

    def close_request(self, request):
        super().close_request(request)
        with self._lock:
            self._live = max(0, self._live - 1)


def _read_value():
    """The value bytes, read only NOW (at GET). read_value/read_show_file are
    the only value-returning paths, and this is the one place the show endpoint
    calls them."""
    if KIND == "name":
        return read_value(LOCATOR)
    return read_show_file(LOCATOR)


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
        """Constant-time compare — the endpoint's only authentication."""
        return hmac.compare_digest(segment, TOKEN)

    def _teardown(self):
        # One value, one endpoint. Flush first: bytes already handed to the
        # kernel are delivered after the process goes away.
        try:
            self.wfile.flush()
        except OSError:
            pass                            # airuleset:script-ok already served
        os._exit(0)

    def do_GET(self):
        p = self._parts()
        if p == ["healthz"]:
            # The ONE unauthenticated route: a fixed 204, so the CLI can confirm
            # the endpoint is up without the token AND without consuming the
            # one-shot (it never touches the value).
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        # The token segment is compared RAW, never percent-decoded (#116).
        if not (len(p) == 1 and self._is_token(p[0])):
            return self._txt(404, "not found")
        try:
            data = _read_value()
            text = data.decode("utf-8")
        except SecretError as e:
            # `e` names the NAME/PATH + the cap only — no value in it.
            log_event("show-error", LABEL)
            self._txt(410, "value no longer available: %s" % e)
            return self._teardown()
        except UnicodeDecodeError:
            log_event("show-error", LABEL)
            self._txt(415, "value is not UTF-8 text — copy it another way")
            return self._teardown()
        if len(data) > MAX_SECRET_BYTES:
            log_event("show-error", LABEL)
            self._txt(413, "value over the %d-byte cap" % MAX_SECRET_BYTES)
            return self._teardown()
        body = PAGE.replace("VALUE_PLACEHOLDER", _js(text))
        body = body.replace("NAME_PLACEHOLDER", repr(LABEL)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; connect-src 'none'; "
            "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)
        log_event("shown", LABEL)
        self._teardown()


_servers = []
for _h in BIND_IPS:
    try:
        _s = BoundedServer((_h, PORT), H)
    except OSError as _e:
        sys.stderr.write("show: skip bind %s:%d (%s)\n" % (_h, PORT, _e))
        continue
    _s.daemon_threads = True
    _servers.append(_s)
if not _servers:
    sys.exit("show: no address in %r could bind :%d" % (BIND_IPS, PORT))
for _s in _servers:
    # The token is NOT printed: this file is the endpoint's diagnostic log and
    # the token is its auth.
    sys.stderr.write("show-endpoint: bound %s:%d\n" % (_s.server_address[0], PORT))
sys.stderr.flush()
for _s in _servers[:-1]:
    threading.Thread(target=_s.serve_forever, daemon=True).start()
_servers[-1].serve_forever()
