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
import json
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
                            check_name, store_value, store_values)

if len(sys.argv) < 6:
    sys.exit("usage: AIRULESET_VAULT_TOKEN=<token> vault_server.py <port> "
             "<bind_ips_csv> <names_csv> <ttl_s> <keep_s>")
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
# The name POSITION widened to a comma-separated LIST (#603). One name is
# byte-identical to before (single-field page, raw-body POST); several names
# share this ONE endpoint (a field per name, a JSON body, an atomic submit). A
# vault NAME is NAME_RE (letters/digits/underscore), so the comma is a safe
# delimiter that can never appear inside a name.
NAMES = [x for x in sys.argv[3].split(",") if x]
NAME = NAMES[0] if NAMES else ""            # the single-field path's one name
IS_MULTI = len(NAMES) > 1
TTL = int(sys.argv[4])
KEEP = int(sys.argv[5])
# Several names each carry their own nonce, through a {name: nonce} JSON map in
# the environment (never argv). A single name keeps the exact env var it always
# used, so its whole flow is unchanged.
NONCES = {}
if IS_MULTI:
    try:
        _parsed = json.loads(os.environ.get("AIRULESET_VAULT_NONCES") or "{}")
        if isinstance(_parsed, dict):
            NONCES = {k: v for k, v in _parsed.items() if isinstance(v, str)}
    except ValueError:
        NONCES = {}


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


if not NAMES:
    sys.exit("vault: no name given")
for _n in NAMES:
    try:
        check_name(_n)
    except SecretError as e:
        sys.exit("vault: %s" % e)
# A MULTI endpoint MUST carry a nonce for every name (revocation protection) —
# refuse to serve if AIRULESET_VAULT_NONCES was absent/unparseable/incomplete,
# rather than silently storing a member with no nonce check (#603 review). The
# single path's `AIRULESET_VAULT_NONCE` can't degrade this way; this keeps multi
# from being weaker than single.
if IS_MULTI and not all(_n in NONCES for _n in NAMES):
    sys.exit("vault: a multi-field endpoint needs a nonce per name via "
             "AIRULESET_VAULT_NONCES — refusing to serve without revocation "
             "protection for every field")
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


# The MULTI-field page (#603): one endpoint, a field per name, ONE atomic submit.
# Served RAW (single braces, #18). The field labels come from NAMES, which are
# NAME_RE-validated (letters/digits/underscore only) at startup, so
# `json.dumps(NAMES)` produces an array with NO HTML/JS metacharacters — there is
# nothing to break out of the <script> with, and the labels are set via
# textContent regardless. The values are collected client-side into a JSON body;
# the client refuses an empty field (inline) so a normal submit is always
# complete. The whole set is stored all-or-nothing by the server.
MULTI_PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer>
<title>Bezpecne odoslanie hesiel / klucov</title>
<link rel=icon href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2016'%3E%3Crect%20width='16'%20height='16'%20rx='3'%20fill='%230f172a'/%3E%3Cpath%20d='M5%207V5.5a3%203%200%20016%200V7h1v6H4V7zm1.5%200h3V5.5a1.5%201.5%200%2000-3%200z'%20fill='%2338bdf8'/%3E%3C/svg%3E">
<style>
 body{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}
 .card{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}
 h1{font-size:18px;margin:0 0 4px} p{color:#94a3b8;margin:.2em 0 1em;font-size:14px}
 code{color:#38bdf8}
 label.fld{display:block;margin:12px 0}
 label.fld .nm{display:block;color:#94a3b8;font-size:13px;margin-bottom:4px;font-family:ui-monospace,monospace}
 input[type=password]{width:100%;box-sizing:border-box;padding:12px;border-radius:8px;
   border:1px solid #475569;background:#0f172a;color:#e2e8f0;font:15px ui-monospace,monospace}
 button{margin-top:16px;padding:12px 18px;border:0;border-radius:8px;background:#38bdf8;
   color:#0f172a;font-weight:600;font-size:15px;cursor:pointer}
 button[disabled]{opacity:.5;cursor:default}
 #status{margin-top:14px;font-size:14px;white-space:pre-line}
 .ok{color:#4ade80} .err{color:#f87171}
</style>
<div class=card>
 <h1>Odoslanie <span id=cnt></span> hodnot naraz</h1>
 <p>Vyplň VŠETKY políčka a odošli RAZ. Hodnoty sa ulozia len na server (prava
    0600) — <b>nikdy sa nezobrazia v chate</b> a Claude ich nevidi. Odoslat sa da
    raz; potom sa tato adresa zavrie.</p>
 <div id=fields></div>
 <button id=b>Odoslat vsetky</button>
 <div id=status></div>
</div>
<script>
const NAMES=NAMES_PLACEHOLDER,fields=document.getElementById('fields'),
 b=document.getElementById('b'),st=document.getElementById('status'),inputs={};
document.getElementById('cnt').textContent=NAMES.length;
NAMES.forEach((nm,i)=>{
 const wrap=document.createElement('label');wrap.className='fld';
 const lab=document.createElement('span');lab.className='nm';lab.textContent=nm;
 const inp=document.createElement('input');inp.type='password';
 inp.autocomplete='off';inp.spellcheck=false;inp.placeholder='hodnota pre '+nm;
 wrap.appendChild(lab);wrap.appendChild(inp);fields.appendChild(wrap);
 inputs[nm]=inp;if(i===0)setTimeout(()=>inp.focus(),0);
});
b.onclick=()=>{
 const payload={};
 for(const nm of NAMES){
  const val=inputs[nm].value;
  if(!val){st.className='err';st.textContent='Vypln policko: '+nm;
    inputs[nm].focus();return}
  payload[nm]=val;
 }
 b.disabled=true;st.className='';st.textContent='Odosielam...';
 fetch(location.pathname,{method:'POST',cache:'no-store',
   headers:{'Content-Type':'application/json; charset=utf-8'},
   body:JSON.stringify(payload)})
  .then(r=>{if(r.status===200){
     for(const nm of NAMES)inputs[nm].value='';
     st.className='ok';st.textContent='Prijate. Okno mozes zavriet.';
     b.style.display='none';fields.style.display='none'}
    else{b.disabled=false;st.className='err';
      r.text().then(t=>{st.textContent='Chyba '+r.status+(t?': '+t:'')})
       .catch(()=>{st.textContent='Chyba '+r.status})}})
  .catch(()=>{b.disabled=false;st.className='err';
     st.textContent='Sietova chyba - skus znova.'});
};
</script></html>"""


# A credential endpoint serves exactly one browser for a few minutes. Unbounded
# threads are pure abuse surface, so refuse past a small cap rather than let a
# single host spawn one thread per connection for the whole TTL.
MAX_CONNECTIONS = 16

# The MULTI path's whole JSON body cap: one per-value cap per name plus one more
# value's worth of JSON structure overhead. Each individual value is still
# re-capped at MAX_SECRET_BYTES on its own (in _store_multi and in the store).
MULTI_BODY_CAP = (len(NAMES) + 1) * MAX_SECRET_BYTES


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


# ONE-SHOT consume-latch for the MULTI path (#603), PROCESS-GLOBAL — the SAME
# reason show_server.py (#580) needs one: `_servers` below holds one
# BoundedServer per bind IP, all in THIS one process, so the latch that makes
# "submitted ONCE" true under concurrency must be a module global (a per-instance
# one would miss a race across two bind interfaces). Claimed AFTER validation but
# BEFORE the store, so a validation error (an empty field) never consumes it and
# the endpoint stays up. The SINGLE-name path needs NO latch — store_value's
# O_EXCL already makes each name single-shot and it exits on the first success —
# so its flow is left byte-identical.
_store_lock = threading.Lock()
_stored = False


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
            if IS_MULTI:
                # NAMES are NAME_RE-validated (no HTML/JS metacharacters), so
                # json.dumps produces a safe JS array literal to build the
                # field-per-name form from.
                body = MULTI_PAGE.replace(
                    "NAMES_PLACEHOLDER", json.dumps(NAMES)).encode()
            else:
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
        # The single path caps the whole body at one value; the multi path
        # carries N values in a JSON object, so its cap scales with the count
        # (per-value caps are re-checked below and inside the store).
        cap = MULTI_BODY_CAP if IS_MULTI else MAX_SECRET_BYTES
        if length > cap:
            self._drain(length)          # read it so the reply reaches the client
            return self._txt(413, "body over the %d-byte cap" % cap)
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
        if IS_MULTI:
            return self._store_multi(data)
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

    def _store_multi(self, data):
        """The MULTI path (#603): a JSON object {name: value}, stored
        ALL-OR-NOTHING with a single-consume latch. Never echoes a value —
        every error names only the NAME (NAME_RE) and a reason.
        """
        try:
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, RecursionError):
            # NEVER interpolate the body / the parse exception (it can quote a
            # snippet of the value) — a fixed literal only. RecursionError is
            # explicit: a deeply-nested JSON body (well under the size cap)
            # makes json.loads recurse past the limit, and it is NOT a
            # ValueError, so without it the request reset the connection and
            # dumped a traceback into the endpoint log (#603 review).
            return self._txt(400, "malformed JSON body")
        if not isinstance(payload, dict):
            return self._txt(400, "expected a JSON object of {name: value}")
        # VALIDATION — before the latch, so an empty/missing field never
        # consumes the one-shot and the endpoint stays up for a fix + resubmit
        # (the client blocks an empty field, so this is only a bypass/edge).
        items = []
        for nm in NAMES:
            raw = payload.get(nm)
            if not isinstance(raw, str) or not raw:
                return self._txt(422, "missing or empty value for %s" % nm)
            enc = raw.encode("utf-8")
            if len(enc) > MAX_SECRET_BYTES:
                return self._txt(
                    413, "value for %s over the %d-byte cap"
                    % (nm, MAX_SECRET_BYTES))
            items.append((nm, enc))
        # CONSUME-LATCH — claim the single submission atomically, AFTER
        # validation, so two racing tabs cannot both store. A racer gets 410.
        global _stored
        with _store_lock:
            if _stored:
                return self._txt(410, "already submitted — this page is one-shot")
            _stored = True
        try:
            store_values(items, keep_s=KEEP, nonces=NONCES)
        except (SecretError, OSError) as e:
            # A genuine store failure after a clean validation (a revoked nonce,
            # a real disk error). store_values already rolled back its own
            # writes (reverting each committed member to `pending`), so nothing
            # partial is on disk and a corrected retry can still match. Release
            # the latch so that retry can happen and stay up. `e` names NAME +
            # reason only, never a value. OSError is caught belt-and-suspenders
            # (store_values wraps it as SecretError) so a future regression can
            # never dead-lock the latch at 410 (#603 review).
            with _store_lock:
                _stored = False
            return self._txt(409, "not stored: %s" % e)
        self._txt(200, "ok")
        try:
            self.wfile.flush()
        except OSError:
            pass                # airuleset:script-ok values already safely stored
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
