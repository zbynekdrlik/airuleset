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
  GET  /<token>/   -> the click-to-reveal PAGE (NO value, NO latch flip). A
                      browser prefetch / hover-preload / chat link-unfurl issues
                      a GET indistinguishable from a click, so a GET must never
                      consume the one-shot (#1011) — a second GET still serves
                      the page. An ANNOUNCED prefetch (Sec-Purpose / Purpose /
                      X-Moz: prefetch) is a bare 204 that touches nothing.
  POST /<token>/   -> the value page, rendered ONCE. Only the reveal page's
                      button (a human's click) sends a POST — a prefetcher never
                      does. The value is embedded as a JS string
                      (injection-escaped) so the copy button is byte-exact. After
                      serving, the endpoint STAYS UP so a later GET/POST gets a
                      helpful 410 (the process-global latch guarantees exactly-
                      once); the TTL timer is the teardown.

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
    """`text` as a JS string literal safe to inline inside a <script>.

    `json.dumps` (default `ensure_ascii=True`) already neutralises `"`, `\\`,
    control characters, AND every non-ASCII codepoint — including the U+2028 /
    U+2029 line/paragraph separators that would otherwise terminate a JS string
    — by emitting them as `\\uXXXX`. The only breakout it does NOT cover is
    the HTML parser seeing a literal `</script>`, so `<`, `>` and `&` are escaped
    to `\\u003c` / `\\u003e` / `\\u0026` on top. The value is only ever ASSIGNED
    to a variable (and set as a textarea `.value`), never eval'd or in innerHTML."""
    s = json.dumps(text)
    for a, b in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        s = s.replace(a, b)
    return s


def _html(text):
    """`text` as HTML-safe text content — the reveal page reflects only the
    vault LABEL (NAME_RE-constrained, so nothing to escape today), but escaping
    is correct by construction rather than by luck."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


# Served RAW (VALUE_PAGE/REVEAL_PAGE.replace(...).encode(), never .format()), so
# braces stay SINGLE — a doubled `{{` renders literally and breaks the CSS/JS
# (#18). The icon is an inline data: URI so no browser requests /favicon.ico
# (which this endpoint would 404 — an unauthenticated route on a credential
# endpoint is not an option). The VALUE page fills the value into a readonly
# textarea by JS (never innerHTML), so the copy is byte-exact and there is no
# HTML-escaping pitfall; the copy button works over plain HTTP via execCommand
# where the async clipboard API is blocked (a non-secure context).
VALUE_PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
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
 <p>Táto hodnota sa zobrazuje <b>len raz</b>. Skopíruj si ju teraz —
    po zatvorení okna už nebude dostupná. <b>Do chatu ju NEPÍŠ.</b></p>
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


# The click-to-reveal page (#1011): served on GET, it embeds NO value and does
# NOT flip the one-shot latch, so a browser prefetch / hover-preload / chat
# link-unfurl — which issues a GET indistinguishable from a click — cannot burn
# the single view. The value + the latch move onto the same-origin POST the
# button sends (a prefetcher never POSTs). NO script at all (a plain form), and
# the form carries NO `action` attribute so it POSTs to the current URL: the
# token stays in the address bar and is never written into the page body.
REVEAL_PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Zobrazenie tajomstva</title>
<link rel=icon href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2016'%3E%3Crect%20width='16'%20height='16'%20rx='3'%20fill='%230f172a'/%3E%3Cpath%20d='M5%207V5.5a3%203%200%20016%200V7h1v6H4V7zm1.5%200h3V5.5a1.5%201.5%200%2000-3%200z'%20fill='%2338bdf8'/%3E%3C/svg%3E">
<style>
 body{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}
 .card{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}
 h1{font-size:18px;margin:0 0 4px} p{color:#94a3b8;margin:.2em 0 1em;font-size:14px}
 code{color:#38bdf8}
 button{margin-top:6px;padding:12px 18px;border:0;border-radius:8px;background:#38bdf8;
   color:#0f172a;font-weight:600;font-size:15px;cursor:pointer}
 .warn{color:#fbbf24;font-size:13px;margin-top:12px}
</style>
<div class=card>
 <h1>Tajomstvo <code>NAME_PLACEHOLDER</code></h1>
 <p>Hodnota sa zobrazí <b>len raz</b> — až keď klikneš na tlačidlo nižšie.
    Samotné otvorenie tohto odkazu (náhľad v chate, prednačítanie prehliadačom)
    ju <b>nespotrebuje</b>. <b>Do chatu ju NEPÍŠ.</b></p>
 <form method=post>
  <button type=submit>Zobraziť hodnotu</button>
 </form>
 <div class=warn>Po zobrazení sa hodnota už znovu nezobrazí — vygeneruj ju nanovo.</div>
</div></html>"""


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


# ONE-SHOT consume-latch, PROCESS-GLOBAL (see do_GET). `_servers` below holds one
# BoundedServer per bind IP, all in this one process and each ThreadingHTTPServer,
# so the latch that makes "shown ONCE" true under concurrency must be a module
# global — a per-instance latch would miss a race across two bind interfaces.
_serve_lock = threading.Lock()
_served = False


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
        """Constant-time compare — the endpoint's only authentication. A
        non-ASCII path segment makes hmac.compare_digest raise TypeError; such
        a segment simply is not the token, so it is REFUSED (404), never raised
        into a traceback in the endpoint log."""
        try:
            return hmac.compare_digest(segment, TOKEN)
        except TypeError:
            return False

    def _html_headers(self, body_len, form):
        """The security + anti-cache header set shared by BOTH HTML responses
        (#1011): the click-to-reveal page and the value page carry the SAME
        no-store / no-referrer / nosniff headers. The CSP differs only in what
        each page needs — the reveal page is a plain FORM (script 'none',
        form-action 'self'); the value page runs the copy-button script and has
        no form (script 'unsafe-inline', form-action 'none')."""
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(body_len))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if form:
            csp = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                   "script-src 'none'; connect-src 'none'; form-action 'self'; "
                   "base-uri 'none'; frame-ancestors 'none'")
        else:
            csp = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                   "script-src 'unsafe-inline'; connect-src 'none'; "
                   "form-action 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Security-Policy", csp)

    def _is_prefetch(self):
        """True when the request ANNOUNCES itself as a prefetch / preload /
        prerender (#1011, Approach 2). Belt-and-braces on top of the GET-serves-
        no-value primary defence: an announced prefetch never even receives the
        reveal-page HTML. Not every prefetcher/unfurler sends these headers —
        which is exactly why the GET path must ALSO be value-free and latch-free."""
        for name in ("Sec-Purpose", "Purpose", "X-Purpose", "X-Moz"):
            v = (self.headers.get(name) or "").strip().lower()
            if "prefetch" in v or "prerender" in v or "preview" in v:
                return True
        return False

    def _log_consume(self, method):
        """Attribution for the CONSUMING request (#1011): the request method +
        a sanitised, bounded, single-line User-Agent go to stderr (the endpoint
        log, `show-endpoint-<port>.log`). NEVER the value, NEVER the token — the
        UA is a request header, not the secret."""
        ua = self.headers.get("User-Agent") or "-"
        ua = "".join(c if 0x20 <= ord(c) < 0x7f else "?" for c in ua)[:200]
        sys.stderr.write("show-consume: method=%s ua=%s\n" % (method, ua))
        sys.stderr.flush()

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
        # #1011 Approach 2 (belt): an ANNOUNCED prefetch/preload gets a bare 204
        # that touches NOTHING — no value, no latch, not even the reveal HTML.
        if self._is_prefetch():
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        # Once the one-shot has been consumed (or errored) via POST, EVERY later
        # request — GET or POST — is 410: never the value, never a fresh reveal
        # page. Read WITHOUT the lock: _served only ever transitions False->True
        # (set under the lock in do_POST), so a stale False here at worst serves
        # one more harmless value-free reveal page — never the value.
        if _served:
            return self._txt(410, "value already shown — generate it again")
        # #1011 Approach 1 (primary): a plain GET only ever serves the click-to-
        # reveal page. It embeds NO value and does NOT flip the latch, so a
        # prefetcher/unfurler that issues a GET WITHOUT an announcing header (the
        # case Approach 2 misses) still cannot burn the single view — a second
        # GET serves the same page. NAME (the label) is HTML-escaped; there is no
        # value and no VALUE_PLACEHOLDER on this page at all.
        body = REVEAL_PAGE.replace("NAME_PLACEHOLDER", _html(LABEL)).encode()
        self.send_response(200)
        self._html_headers(len(body), form=True)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # Only the reveal page's button — a human's intentional click — sends a
        # POST; a prefetcher/unfurler never does (#1011). So the value read + the
        # one-shot latch live HERE, mirroring the #580 do_GET latch verbatim,
        # just moved onto the request that a preload cannot trigger.
        p = self._parts()
        if not (len(p) == 1 and self._is_token(p[0])):
            return self._txt(404, "not found")
        # ONE-SHOT consume-latch: claim the single view ATOMICALLY, BEFORE
        # reading the value, so concurrent token POSTs cannot each read+serve it.
        # Only the claimant reads / serves; every other request (racing or later)
        # gets 410. The process STAYS UP so a later view gets that 410 rather than
        # a connection error; the TTL timer is the teardown.
        global _served
        with _serve_lock:
            if _served:
                return self._txt(410, "value already shown — generate it again")
            _served = True
        # Attribution: WHO/HOW consumed the one-shot (never the value/token).
        self._log_consume("POST")
        try:
            data = _read_value()
            text = data.decode("utf-8")
        except SecretError as e:
            # The value is gone (forgotten / purged / TTL) or a --file source no
            # longer validates. `e` may name the --file PATH (never the value),
            # so it goes only to stderr (the endpoint log), never the HTTP body.
            log_event("show-error", LABEL)
            sys.stderr.write("show: %s\n" % e)
            return self._txt(410, "value no longer available — generate it again")
        except UnicodeDecodeError:
            log_event("show-error", LABEL)
            return self._txt(415, "value is not UTF-8 text — copy it another way")
        if len(data) > MAX_SECRET_BYTES:
            # Defense-in-depth, unreachable today: a NAME value is capped at
            # store_value time and a --file value inside read_show_file (which
            # raises -> the 410 branch above). Kept so a future cap change can
            # never silently serve an oversize value.
            log_event("show-error", LABEL)
            return self._txt(413, "value over the %d-byte cap" % MAX_SECRET_BYTES)
        # NAME first, VALUE last: the value insertion must be the FINAL pass so
        # nothing rescans it — otherwise a value literally containing
        # "NAME_PLACEHOLDER" would be corrupted by the second replace (LABEL is
        # NAME_RE, so it can never contain "VALUE_PLACEHOLDER").
        body = VALUE_PAGE.replace("NAME_PLACEHOLDER", repr(LABEL))
        body = body.replace("VALUE_PLACEHOLDER", _js(text)).encode()
        self.send_response(200)
        self._html_headers(len(body), form=False)
        self.end_headers()
        self.wfile.write(body)
        log_event("shown", LABEL)


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
