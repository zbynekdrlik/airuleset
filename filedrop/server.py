"""File-drop HTTP server — serves FILEDROP_DIR/<token>/<name> over the LAN.

Read-only (GET/HEAD). No directory listing. The per-file unguessable token in
the path IS the authorization. Path-traversal safe. stdlib only.

The matching client is share.py (copies files in + returns URLs). The server only
READS, so its systemd unit runs with ProtectHome=read-only and no write paths.
"""
import mimetypes
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import FILEDROP_DIR, PORT, host_ip

# token = secrets.token_urlsafe(>=16) -> url-safe base64 alphabet only.
_TOKEN_RE = re.compile(r"\A[A-Za-z0-9_-]{16,128}\Z")
_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")


def safe_resolve(raw_path, base_dir):
    """Map a request path to a real file under base_dir, or None if unsafe.

    Pure function (no I/O beyond the final containment/exists check) so it is
    unit-testable in isolation. Accepts ONLY '/<token>/<name>' where token and
    name match the strict alphabets above; rejects anything with '..', the wrong
    segment count, bad characters, or a resolved path that escapes base_dir."""
    decoded = unquote(urlparse(raw_path).path)
    raw_segments = decoded.split("/")
    if ".." in raw_segments:
        return None
    parts = [p for p in raw_segments if p not in ("", ".")]
    if len(parts) != 2:
        return None
    token, name = parts
    if not _TOKEN_RE.match(token) or not _NAME_RE.match(name):
        return None
    base = Path(base_dir).resolve()
    candidate = (base / token / name).resolve()
    try:
        candidate.relative_to(base)          # containment: must stay under base
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "filedrop/0.1"
    protocol_version = "HTTP/1.1"
    timeout = 30                              # slow-loris guard (socket read timeout)

    def log_message(self, fmt, *args):
        sys.stderr.write("filedrop %s - %s\n" %
                         (self.address_string(), fmt % args))

    @property
    def _base_dir(self):
        return getattr(self.server, "base_dir", FILEDROP_DIR)

    def _deny(self, code, msg):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve(self, head):
        path = urlparse(self.path).path
        if path == "/favicon.ico":
            # Browsers auto-request this on every page open; answer 204 so the
            # user's console stays clean (browser-console-zero-errors) instead of
            # logging a 404 every time they open a shared file.
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/", ""):
            return self._deny(404, "file-drop: provide a file link (/<token>/<name>)")
        target = safe_resolve(self.path, self._base_dir)
        if target is None:
            return self._deny(404, "not found")
        try:
            size = target.stat().st_size
            f = target.open("rb")
        except OSError:
            return self._deny(404, "not found")
        try:
            ctype, _enc = mimetypes.guess_type(str(target))
            self.send_response(200)
            self.send_header("Content-Type", ctype or "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition",
                             f'inline; filename="{target.name}"')
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            if not head:
                shutil.copyfileobj(f, self.wfile)   # stream — never load whole file in RAM
        finally:
            f.close()

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def do_POST(self):
        self._deny(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_POST


def make_server(host=None, port=None, base_dir=None):
    """Build (but do not start) the ThreadingHTTPServer. host/port/base_dir
    default to the LAN IP, PORT, and FILEDROP_DIR respectively."""
    host = host_ip() if host is None else host
    port = PORT if port is None else port
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    httpd.base_dir = Path(base_dir) if base_dir is not None else FILEDROP_DIR
    return httpd


def run_server(host=None, port=None, base_dir=None):
    """Serve forever (systemd ExecStart target). Read-only: never creates or
    writes the drop dir (the client/setup does), so it runs cleanly under a
    read-only-home sandbox even before the first file is shared."""
    httpd = make_server(host=host, port=port, base_dir=base_dir)
    h, p = httpd.server_address[0], httpd.server_address[1]
    sys.stderr.write(
        f"filedrop: serving {httpd.base_dir} on http://{h}:{p}/\n")
    httpd.serve_forever()
