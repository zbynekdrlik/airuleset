#!/usr/bin/env python3
"""Tiny LAN upload endpoint. Browser drag-drop -> streams a file to disk.

The user is remote (VPN+SSH) with NO local filesystem access to the dev boxes,
and airuleset's filedrop is download-only — so to RECEIVE a big recording from
the user's laptop we stand up this push endpoint. Streams the request body in
chunks (never buffers the whole multi-GB file in RAM). LAN/VPN-internal; the
unguessable token in the path is the auth.

Usage:
    python3 upload_server.py <token> <port> <advertise_ip> <dest_dir>

  GET  /<token>/         -> serves the drag-drop upload page
  PUT  /<token>/<name>   -> streams the bytes to <dest_dir>/<name>

Pick an unguessable token (e.g. `openssl rand -hex 8`) and a free port (8799).
Print the URL to the user; they open it and drop the file. Verify the saved
size matches before proceeding.
"""
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if len(sys.argv) < 5:
    sys.exit("usage: upload_server.py <token> <port> <advertise_ip> <dest_dir>")
TOKEN = sys.argv[1]
PORT = int(sys.argv[2])
ADVERTISE_IP = sys.argv[3]
DEST = sys.argv[4]
os.makedirs(DEST, exist_ok=True)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")

PAGE = """<!doctype html><html lang=sk><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Upload — meeting recording</title>
<style>
 body{{font:16px system-ui;margin:0;background:#0f172a;color:#e2e8f0;display:grid;place-items:center;min-height:100vh}}
 .card{{background:#1e293b;padding:32px;border-radius:14px;width:min(560px,92vw);box-shadow:0 10px 40px #0006}}
 h1{{font-size:18px;margin:0 0 4px}} p{{color:#94a3b8;margin:.2em 0 1em}}
 #drop{{border:2px dashed #475569;border-radius:12px;padding:36px;text-align:center;cursor:pointer;transition:.15s}}
 #drop.hot{{border-color:#38bdf8;background:#0c4a6e33}}
 input[type=file]{{display:none}}
 .bar{{height:10px;background:#334155;border-radius:6px;overflow:hidden;margin-top:16px;display:none}}
 .bar>i{{display:block;height:100%;width:0;background:#38bdf8;transition:width .2s}}
 #status{{margin-top:12px;font-size:14px;color:#cbd5e1;white-space:pre-line}}
 .ok{{color:#4ade80}} .err{{color:#f87171}}
</style>
<div class=card>
 <h1>Upload videa / nahrávky</h1>
 <p>Potiahni súbor sem alebo klikni. Veľké súbory OK — streamuje sa priamo na server.</p>
 <div id=drop>📁 <b>Vyber alebo potiahni súbor</b></div>
 <input id=f type=file>
 <div class=bar><i id=fill></i></div>
 <div id=status></div>
</div>
<script>
const drop=document.getElementById('drop'),f=document.getElementById('f'),
 bar=document.querySelector('.bar'),fill=document.getElementById('fill'),st=document.getElementById('status');
drop.onclick=()=>f.click();
['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{{ev.preventDefault();drop.classList.add('hot')}}));
['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{{ev.preventDefault();drop.classList.remove('hot')}}));
drop.addEventListener('drop',ev=>{{if(ev.dataTransfer.files.length)send(ev.dataTransfer.files[0])}});
f.onchange=()=>{{if(f.files.length)send(f.files[0])}};
function fmt(b){{return (b/1073741824).toFixed(2)+' GB'}}
function send(file){{
 st.className='';st.textContent='Nahrávam '+file.name+' ('+fmt(file.size)+')…';bar.style.display='block';
 const xhr=new XMLHttpRequest();
 xhr.open('PUT',location.pathname.replace(/\\/$/,'')+'/'+encodeURIComponent(file.name));
 xhr.upload.onprogress=e=>{{if(e.lengthComputable){{const p=e.loaded/e.total*100;fill.style.width=p+'%';
   st.textContent='Nahrávam '+file.name+'  '+p.toFixed(1)+'%  ('+fmt(e.loaded)+' / '+fmt(file.size)+')'}}}};
 xhr.onload=()=>{{if(xhr.status===200){{fill.style.width='100%';st.className='ok';
   st.textContent='✅ Hotovo — '+file.name+' je na serveri. Môžeš zavrieť okno.'}}
   else{{st.className='err';st.textContent='❌ Chyba '+xhr.status+': '+xhr.responseText}}}};
 xhr.onerror=()=>{{st.className='err';st.textContent='❌ Sieťová chyba pri nahrávaní.'}};
 xhr.send(file);
}}
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
        name = _SAFE.sub("_", os.path.basename(p[1])) or "upload.bin"
        dest = os.path.join(DEST, name)
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


httpd = ThreadingHTTPServer(("0.0.0.0", PORT), H)
httpd.daemon_threads = True
sys.stderr.write("upload-server: http://%s:%d/%s/\n" % (ADVERTISE_IP, PORT, TOKEN))
sys.stderr.flush()
httpd.serve_forever()
