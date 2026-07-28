#!/usr/bin/env python3
"""Tiny LAN upload endpoint. Browser drag-drop -> streams one or more files
to disk, one at a time.

The user is remote (VPN+SSH) with NO local filesystem access to the dev boxes,
and airuleset's filedrop is download-only — so to RECEIVE a big recording from
the user's laptop we stand up this push endpoint. Streams the request body in
chunks (never buffers the whole multi-GB file in RAM). LAN/VPN-internal; the
unguessable token in the path is the auth.

Usage:
    python3 upload_server.py <token> <port> <bind_ips_csv> <dest_dir> [ttl_seconds]

  GET  /<token>/         -> serves the drag-drop upload page (accepts a
                            multi-file selection/drop; the page's own JS
                            sends one PUT per file, sequentially)
  PUT  /<token>/<name>   -> streams the bytes to <dest_dir>/<name> (each PUT
                            is its own independent request/save/log line —
                            one file failing never affects another). <name> is
                            percent-ENCODED by the page and decoded here by
                            safe_name(), which keeps the real name (diacritics,
                            spaces, parentheses) and replaces only what cannot
                            safely be a filename. The TOKEN segment is never
                            decoded — it is the only auth this endpoint has.

<bind_ips_csv> is a comma-separated list of the PRIVATE addresses to listen on
(tailscale + LAN — filedrop.bind_ips()), so the user reaches the endpoint whether
they are on tailscale or the LAN. It deliberately does NOT bind 0.0.0.0: this is a
WRITE endpoint and the box may have a public IP (gatekeeper), which must never
carry an open upload port. A single IP (e.g. `127.0.0.1`) is a valid one-item list.

Pick an unguessable token (e.g. `openssl rand -hex 8`) and a free port (8799).
Print the URL(s) to the user; they open one and drop the file. Verify the saved
size matches before proceeding.
"""
import os
import sys
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

if len(sys.argv) < 5:
    sys.exit("usage: upload_server.py <token> <port> <bind_ips_csv> <dest_dir> [ttl_seconds]")
TOKEN = sys.argv[1]
PORT = int(sys.argv[2])
BIND_IPS = [x for x in sys.argv[3].split(",") if x] or ["127.0.0.1"]
DEST = sys.argv[4]
TTL = int(sys.argv[5]) if len(sys.argv) > 5 else 0
os.makedirs(DEST, exist_ok=True)

if TTL > 0:
    # self-shutdown so detached upload endpoints never accumulate as orphans.
    # DAEMON, always (#114): threading.Timer inherits Thread.daemon = False, and
    # the interpreter joins every non-daemon thread before it can shut down — so
    # a non-daemon timer parks any exit the main thread takes (the bind-loop's
    # `sys.exit` below, a KeyboardInterrupt on a live server) for the rest of the
    # TTL, and its own os._exit(0) then ends the process INSTEAD, reporting
    # success. Measured before the fix: rc=0 after 6.06s of a 6s TTL for a server
    # that bound nothing, and rc=0 after 5.93s for a SIGINT on one that had.
    # A daemon thread is only killed at interpreter shutdown, which on the happy
    # path never comes (serve_forever holds the main thread) — so the TTL still
    # fires and still ends the process exactly as before.
    _ttl_timer = threading.Timer(TTL, lambda: os._exit(0))
    _ttl_timer.daemon = True
    _ttl_timer.start()

# The served PAGE sends `encodeURIComponent(file.name)` — it has to, because a
# raw filename cannot go into an HTTP request line at all (a space terminates
# the target, `#` truncates the path, `?` starts a query). So the SERVER is what
# must decode, and #116 is what happened while it did not: `%` fell outside the
# old `[^A-Za-z0-9._-]` sanitizer's class, so every escape survived with its `%`
# turned into `_` and a dropped `nahrávka test (1).bin` landed as
# `nahr_C3_A1vka_20test_20_1_.bin`.
#
# Decoding is also what makes this the SECURITY boundary for the first time:
# `/`, `..`, NUL, control characters, a leading `-` and a 4000-character name
# only become reachable once the escapes are resolved. Hence a KEEP-list rather
# than a blacklist — anything that is not a letter, combining mark, digit or one
# of a few punctuation characters becomes `_`, so path separators, C0/C1
# controls and the entire Cf class (U+202E RIGHT-TO-LEFT OVERRIDE and friends,
# the classic extension-spoofing trick) are excluded by construction instead of
# by a list that can be incomplete. Non-ASCII letters are KEPT: the user's files
# are Slovak, and ASCII-stripping `nahrávka` to `nahr_vka` is a milder spelling
# of the same complaint #116 was filed for.
_KEEP_CATEGORIES = ("L", "M", "N")      # letters, combining marks, numbers
_KEEP_PUNCT = " ._-()"
# ext4 caps a NAME at 255 BYTES and the upload is streamed to `<name>.part`
# before the rename, so the clip counts BYTES and leaves room for that suffix
# (400 Slovak characters are 800 bytes — clipping on characters would not do).
_MAX_NAME_BYTES = 200
_MAX_EXT_BYTES = 20


def _clip(name):
    """Bound `name` to _MAX_NAME_BYTES, keeping a plausible extension."""
    if len(name.encode("utf-8")) <= _MAX_NAME_BYTES:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext.encode("utf-8")) > _MAX_EXT_BYTES:
        stem, dot, ext = name, "", ""       # no extension worth preserving
    budget = _MAX_NAME_BYTES - len((dot + ext).encode("utf-8"))
    # errors="ignore" drops a multi-byte character the byte cut split in half.
    stem = stem.encode("utf-8")[:max(budget, 1)].decode("utf-8", "ignore")
    return (stem + dot + ext) or "upload.bin"


def safe_name(segment):
    """Decode ONE url path segment into a filename that cannot leave DEST.

    Called with the FILENAME segment only — deliberately NEVER with the token
    segment, which is this endpoint's only auth (see the module docstring):
    decoding that one too would let `%74ok…` authenticate as `tok…`.
    """
    # NFC first — a macOS-origin name arrives decomposed, and without the
    # normalisation its combining accent is a separate codepoint.
    name = unicodedata.normalize("NFC", unquote(segment, errors="replace"))
    name = "".join(
        ch if (unicodedata.category(ch)[0] in _KEEP_CATEGORIES
               or ch in _KEEP_PUNCT) else "_"
        for ch in name
    ).strip(" ")
    # Dots are legal in a filename, so the character filter above leaves `..`
    # untouched — and `os.path.join(DEST, "..")` is DEST's PARENT.
    if not name.strip("."):
        return "upload.bin"
    if name.startswith("-"):
        name = "_" + name       # never hand a later CLI something read as a flag
    return _clip(name)

# The icon is declared INLINE as a data: URI, and that is the whole fix for
# #117: a document that declares none makes every browser auto-request
# /favicon.ico at the ORIGIN ROOT, which is not /<token>/ — so do_GET refuses it
# and the browser logs a console error on the one page the user personally opens
# to hand a file over (browser-console-zero-errors.md treats that as a bug).
# The refusal is correct and stays: a favicon request carries no token, and the
# token is this write endpoint's only auth — so the error is removed by never
# making the request, never by opening an unauthenticated route.
#
# Inline rather than a file on disk because PAGE is served RAW from this one
# string with no asset pipeline behind it, and upload_server.py is launched BY
# PATH (sys.path[0] is filedrop/ itself), so any runtime asset lookup would be
# install-location dependent. Percent-encoded and ASCII-only: a data: URI with
# no explicit charset defaults to US-ASCII, and raw `#`/`<`/`>` would be read as
# a URL fragment / markup inside the attribute.
PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Upload — airuleset file-drop</title>
<link rel=icon href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2016%2016'%3E%3Crect%20width='16'%20height='16'%20rx='3'%20fill='%230f172a'/%3E%3Cpath%20d='M8%203.2%2012%207.6h-2.4V11H6.4V7.6H4z'%20fill='%2338bdf8'/%3E%3C/svg%3E">
<style>
 body{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}
 .card{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}
 h1{font-size:18px;margin:0 0 4px} p{color:#94a3b8;margin:.2em 0 1em}
 #drop{border:2px dashed #475569;border-radius:12px;padding:36px;text-align:center;cursor:pointer;transition:.15s}
 #drop.hot{border-color:#38bdf8;background:#0c4a6e33}
 input[type=file]{display:none}
 .bar{height:10px;background:#334155;border-radius:6px;overflow:hidden;margin-top:16px;display:none}
 .bar>i{display:block;height:100%;width:0;background:#38bdf8;transition:width .2s}
 #status{margin-top:12px;font-size:14px;color:#cbd5e1;white-space:pre-line}
 #results{margin-top:10px;font-size:13px;color:#cbd5e1}
 #results div{padding:2px 0}
 .ok{color:#4ade80} .err{color:#f87171}
</style>
<div class=card>
 <h1>Upload súborov na server</h1>
 <p>Potiahni jeden alebo viac súborov sem alebo klikni. Veľké súbory OK — streamujú sa priamo na server, jeden po druhom.</p>
 <div id=drop>📁 <b>Vyber alebo potiahni súbory</b></div>
 <input id=f type=file multiple>
 <div class=bar><i id=fill></i></div>
 <div id=status></div>
 <div id=results></div>
</div>
<script>
const drop=document.getElementById('drop'),f=document.getElementById('f'),
 bar=document.querySelector('.bar'),fill=document.getElementById('fill'),st=document.getElementById('status'),
 results=document.getElementById('results');
drop.onclick=()=>f.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot')}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot')}));
drop.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length)sendAll(ev.dataTransfer.files)});
f.onchange=()=>{if(f.files.length)sendAll(f.files)};
function fmt(b){return (b/1073741824).toFixed(2)+' GB'}
function sendAll(fileList){
 const files=Array.from(fileList);
 results.innerHTML='';
 bar.style.display='block';fill.style.width='0%';
 files.forEach(fl=>{const d=document.createElement('div');d.id='r-'+fl.name.replace(/[^A-Za-z0-9._-]/g,'_');
   d.textContent='⏳ '+fl.name+' ('+fmt(fl.size)+') — čaká…';results.appendChild(d)});
 uploadOne(files,0);
}
function uploadOne(files,idx){
 if(idx>=files.length){st.className='ok';st.textContent='Hotovo — '+files.length+' súbor(ov) spracovaných. Môžeš zavrieť okno.';return}
 const file=files[idx],row=document.getElementById('r-'+file.name.replace(/[^A-Za-z0-9._-]/g,'_'));
 st.className='';st.textContent='Nahrávam ('+(idx+1)+'/'+files.length+') '+file.name+' ('+fmt(file.size)+')…';
 const xhr=new XMLHttpRequest();
 xhr.open('PUT',location.pathname.replace(/\\/$/,'')+'/'+encodeURIComponent(file.name));
 xhr.upload.onprogress=e=>{if(e.lengthComputable){const per=e.loaded/e.total;
   fill.style.width=((idx+per)/files.length*100)+'%';
   if(row)row.textContent='⏳ '+file.name+'  '+(per*100).toFixed(1)+'%  ('+fmt(e.loaded)+' / '+fmt(file.size)+')'}};
 // one file's failure must never stop the rest -- uploadOne(idx+1) always
 // runs from BOTH onload and onerror, regardless of this file's outcome.
 xhr.onload=()=>{if(row)row.textContent=(xhr.status===200
   ?'✅ '+file.name+' — hotovo':'❌ '+file.name+' — chyba '+xhr.status+': '+xhr.responseText);
   if(row)row.className=(xhr.status===200?'ok':'err');
   uploadOne(files,idx+1)};
 xhr.onerror=()=>{if(row){row.textContent='❌ '+file.name+' — sieťová chyba';row.className='err'}
   uploadOne(files,idx+1)};
 xhr.send(file);
}
</script></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, *a):
        sys.stderr.write("upload %s - %s\n" % (self.address_string(), a[0] % a[1:]))

    def _parts(self):
        return [p for p in self.path.split("?")[0].split("/") if p]

    def do_GET(self):
        p = self._parts()
        if len(p) == 1 and p[0] == TOKEN:
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._txt(404, "not found")

    def do_PUT(self):
        p = self._parts()
        if len(p) != 2 or p[0] != TOKEN:
            return self._txt(404, "not found")
        # reject framings we cannot verify the length of (would write 0/partial then lie 200)
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            return self._txt(501, "chunked transfer-encoding not supported")
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return self._txt(411, "Content-Length required (got none/zero)")
        name = safe_name(p[1])
        dest = os.path.join(DEST, name)
        # Defence in depth, independent of safe_name: the write must land
        # DIRECTLY inside DEST. This still holds if a later edit weakens the
        # sanitizer, and it also catches a pre-existing symlink in the upload
        # directory pointing somewhere else.
        if os.path.dirname(os.path.realpath(dest)) != os.path.realpath(DEST):
            return self._txt(400, "unsafe filename")
        part = dest + ".part"   # stream to .part, rename only when complete (atomic)
        got = 0
        try:
            with open(part, "wb") as out:
                while got < length:
                    chunk = self.rfile.read(min(1 << 20, length - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
        except OSError as e:
            self._rm(part)
            return self._txt(500, "write failed: %s" % e)
        if got != length:   # disconnect / short read -> never masquerade as success
            self._rm(part)
            return self._txt(400, "incomplete upload: got %d of %d bytes" % (got, length))
        os.replace(part, dest)
        sys.stderr.write("upload SAVED %s (%d bytes)\n" % (dest, got))
        self._txt(200, "saved %s (%d bytes)" % (dest, got))

    @staticmethod
    def _rm(path):
        try:
            os.unlink(path)
        except OSError:
            pass

    def _txt(self, code, msg):
        b = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


# Bind each private IP (never 0.0.0.0 — see the module docstring). A host that
# fails to bind is skipped, not fatal, so a stale LAN IP never takes the whole
# endpoint down; require at least one successful bind.
_servers = []
for _h in BIND_IPS:
    try:
        _s = ThreadingHTTPServer((_h, PORT), H)
    except OSError as _e:
        sys.stderr.write("upload: skip bind %s:%d (%s)\n" % (_h, PORT, _e))
        continue
    _s.daemon_threads = True
    _servers.append(_s)
if not _servers:
    sys.exit("upload: no address in %r could bind :%d" % (BIND_IPS, PORT))
for _s in _servers:
    sys.stderr.write("upload-server: http://%s:%d/%s/\n"
                     % (_s.server_address[0], PORT, TOKEN))
sys.stderr.flush()
for _s in _servers[:-1]:
    threading.Thread(target=_s.serve_forever, daemon=True).start()
_servers[-1].serve_forever()
