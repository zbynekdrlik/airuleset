"""#1114 fix-forward (live read 22.9.2026): `share` on david4 printed
`share: public lane unreachable (403)` although the public URL served the file
(200 from the controller over the internet). Cloudflare's browser-integrity
check answers 403 to urllib's default `User-Agent: Python-urllib/3.x` — curl and
a named agent get 200. The live-check must send a named User-Agent."""
import http.server
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cli_filedrop_watchdog as fw     # noqa: E402


class _UABlockingHandler(http.server.BaseHTTPRequestHandler):
    """Mimics the Cloudflare edge: 403 for the default urllib agent, 200 otherwise."""

    def do_GET(self):
        ua = self.headers.get("User-Agent", "")
        code = 403 if ua.startswith("Python-urllib") else 200
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class TestShareLiveCheckUserAgent(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.HTTPServer(("127.0.0.1", 0), _UABlockingHandler)
        self.port = self.srv.server_address[1]
        t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def test_live_check_is_not_rejected_as_python_urllib(self):
        self.assertEqual(
            fw._public_share_status("http://127.0.0.1:%d/s/tok/f.txt" % self.port), 200)


if __name__ == "__main__":
    unittest.main()
