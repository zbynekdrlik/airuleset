"""#1114: `share` delivers through the account's PUBLIC drop lane
(https://<host>/s/<token>/<file>); the tailscale/LAN URLs become an explicitly
labelled fallback. Owner regression 22.9.2026 (the david4 break: share printed
only tailscale URLs, unreachable from David's laptop).

Calibrated TDD (RED-first). Coverage:
  - filedrop.server: GET /s/<token>/<name> serves the file exactly like the bare
    /<token>/<name>; /s/ alone and traversal -> 404 as today.
  - cli_webterm_tunnel.render_cloudflared_multi_ingress_config: a rule is
    (host, service) OR (host, path, service); the 2-tuple output stays byte-
    identical, a 3-tuple emits a `path:` line.
  - cli_drop_gateway: DropLane.filedrop_port (measured); drop_ingress_rules_for_
    controller() emits the /s/ path rule BEFORE each lane's drop rule; the local
    render_drop_ingress_augmentation emits the /s/ rule to loopback.
  - cli_filedrop_watchdog.cmd_share: the public /s/ URL is printed FIRST when the
    lane is live (200 or 302 Access-redirect), and the labelled private fallback
    is printed on 5xx/timeout or when no lane exists.
"""
import contextlib
import io
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_drop_gateway as dg          # noqa: E402
import cli_filedrop_watchdog as fw     # noqa: E402
import cli_webterm_tunnel as tun       # noqa: E402
import filedrop                        # noqa: E402
import filedrop.share as fshare        # noqa: E402
from filedrop.server import make_server, safe_resolve  # noqa: E402


# ---------------------------------------------------------------------------
# filedrop.server — the `/s/` path prefix
# ---------------------------------------------------------------------------
class TestServerSlashSPrefix(unittest.TestCase):
    def _base(self, td):
        base = Path(td) / "drop"
        token = "T" * 22
        (base / token).mkdir(parents=True)
        (base / token / "rec.wav").write_bytes(b"HELLOAUDIO")
        return base, token

    def test_s_prefix_resolves_same_file(self):
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            bare = safe_resolve(f"/{token}/rec.wav", base)
            pref = safe_resolve(f"/s/{token}/rec.wav", base)
            self.assertIsNotNone(pref)
            self.assertEqual(pref, bare)

    def test_s_prefix_quoted_name(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "drop"
            token = "Q" * 22
            (base / token).mkdir(parents=True)
            (base / token / "a.b_c-d.wav").write_bytes(b"X")
            self.assertIsNotNone(safe_resolve(f"/s/{token}/a.b_c-d.wav", base))

    def test_s_alone_is_404(self):
        with tempfile.TemporaryDirectory() as td:
            base, _ = self._base(td)
            self.assertIsNone(safe_resolve("/s/", base))
            self.assertIsNone(safe_resolve("/s", base))

    def test_s_prefix_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            for bad in (f"/s/{token}/../{token}/rec.wav",
                        "/s/../etc/passwd",
                        "/s/%2e%2e/%2e%2e/etc/passwd",
                        f"/s/{token}/..%2frec.wav"):
                self.assertIsNone(safe_resolve(bad, base), bad)

    def test_s_prefix_short_token_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base, _ = self._base(td)
            self.assertIsNone(safe_resolve("/s/short/rec.wav", base))  # token < 16

    def test_bare_path_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            self.assertIsNotNone(safe_resolve(f"/{token}/rec.wav", base))
            self.assertIsNone(safe_resolve("/", base))
            self.assertIsNone(safe_resolve(f"/{token}", base))
            self.assertIsNone(safe_resolve(f"/{token}/sub/rec.wav", base))

    def test_get_over_http_via_s_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            httpd = make_server(host="127.0.0.1", port=0, base_dir=base)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/s/{token}/rec.wav", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertEqual(r.read(), b"HELLOAUDIO")
                    self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/s/", timeout=5)
                self.assertEqual(cm.exception.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()


# ---------------------------------------------------------------------------
# cli_webterm_tunnel — optional path field in the multi-ingress renderer
# ---------------------------------------------------------------------------
class TestMultiIngressOptionalPath(unittest.TestCase):
    def test_two_tuple_output_byte_identical(self):
        rules = [("a.example.com", "http://127.0.0.1:80")]
        cfg = tun.render_cloudflared_multi_ingress_config("u", "/c.json", rules)
        self.assertIn("  - hostname: a.example.com\n    service: http://127.0.0.1:80\n", cfg)
        self.assertNotIn("path:", cfg)
        self.assertIn("service: http_status:404", cfg)

    def test_three_tuple_emits_path_line(self):
        rules = [("a.example.com", "^/s/", "http://127.0.0.1:8790"),
                 ("a.example.com", "http://127.0.0.1:8870")]
        cfg = tun.render_cloudflared_multi_ingress_config("u", "/c.json", rules)
        self.assertIn(
            "  - hostname: a.example.com\n    path: ^/s/\n    service: http://127.0.0.1:8790\n",
            cfg)
        # The /s/ path rule must precede the same host's catch-all drop rule.
        self.assertLess(cfg.index("path: ^/s/"),
                        cfg.index("service: http://127.0.0.1:8870"))
        self.assertIn("http_status:404", cfg)

    def test_mixed_rules(self):
        rules = [("web.example.com", "unix:/run/x.sock"),
                 ("a.example.com", "^/s/", "http://127.0.0.1:8790"),
                 ("a.example.com", "http://127.0.0.1:8870")]
        cfg = tun.render_cloudflared_multi_ingress_config("u", "/c.json", rules)
        self.assertIn("hostname: web.example.com", cfg)
        self.assertIn("service: unix:/run/x.sock", cfg)
        self.assertIn("path: ^/s/", cfg)


# ---------------------------------------------------------------------------
# cli_drop_gateway — DropLane.filedrop_port + controller /s/ rules
# ---------------------------------------------------------------------------
class TestDropLaneFiledropPort(unittest.TestCase):
    def test_measured_controller_ports(self):
        want = {"david1": 8790, "david2": 8796, "david3": 8797, "david4": 8798}
        for user, port in want.items():
            lane = dg.DROP_LANES[("subdev", user)]
            self.assertEqual(lane.filedrop_port, port, user)

    def test_dominika_measured(self):
        self.assertEqual(dg.DROP_LANES[("subdev", "dominika")].filedrop_port, 8804)

    def test_marek_none(self):
        self.assertIsNone(dg.DROP_LANES[("subdev", "marek")].filedrop_port)

    def test_spinbike_none(self):
        self.assertIsNone(dg.DROP_LANES[("spinbike", "newlevel")].filedrop_port)

    def test_field_defaults_none(self):
        lane = dg.DropLane(host="h", port=1, tunnel_uuid="u", tunnel_config=None,
                           tunnel_service=None, tunnel_system_unit=False, access=False)
        self.assertIsNone(lane.filedrop_port)


class TestControllerSlashSRules(unittest.TestCase):
    def test_each_controller_lane_has_s_rule_before_drop_rule(self):
        rules = dg.drop_ingress_rules_for_controller()
        checked = 0
        for (_n, _u), lane in dg.DROP_LANES.items():
            if lane.topology != "controller" or not lane.origin_host:
                continue
            if lane.filedrop_port is None:
                continue
            s_rule = (lane.host, "^/s/",
                      "http://%s:%d" % (lane.origin_host, lane.filedrop_port))
            drop_rule = (lane.host, "http://%s:%d" % (lane.origin_host, lane.port))
            self.assertIn(s_rule, rules, "missing /s/ rule for %s" % lane.host)
            self.assertIn(drop_rule, rules, "missing drop rule for %s" % lane.host)
            self.assertLess(rules.index(s_rule), rules.index(drop_rule),
                            "/s/ rule must precede the drop rule for %s" % lane.host)
            checked += 1
        self.assertGreaterEqual(checked, 4, "expected david1-4 controller lanes")

    def test_david4_points_at_measured_filedrop_port(self):
        rules = dg.drop_ingress_rules_for_controller()
        self.assertIn(("drop-subdev-david4.newlevel.media", "^/s/",
                       "http://100.118.174.27:8798"), rules)

    def test_none_filedrop_port_emits_no_s_rule(self):
        lane = dg.DROP_LANES[("subdev", "david4")]
        orig = lane.filedrop_port
        lane.filedrop_port = None
        try:
            rules = dg.drop_ingress_rules_for_controller()
            s_rules = [r for r in rules
                       if len(r) == 3 and r[0] == "drop-subdev-david4.newlevel.media"]
            self.assertEqual(s_rules, [],
                             "filedrop_port=None must emit no /s/ rule")
            # The drop rule (2-tuple) is still present.
            self.assertIn(("drop-subdev-david4.newlevel.media",
                           "http://100.118.174.27:8873"), rules)
        finally:
            lane.filedrop_port = orig


class TestLocalSlashSAugmentation(unittest.TestCase):
    CONFIG = ("tunnel: u\ncredentials-file: /c.json\n\ningress:\n"
              "  - hostname: spinbike.newlevel.media\n"
              "    service: http://127.0.0.1:9000\n"
              "  - service: http_status:404\n")
    HOST = "drop-spinbike.newlevel.media"

    def test_s_rule_to_loopback_before_drop(self):
        out = dg.render_drop_ingress_augmentation(
            self.CONFIG, self.HOST, 8828, filedrop_port=8788)
        self.assertIn("  - hostname: %s\n    path: ^/s/\n"
                      "    service: http://127.0.0.1:8788\n" % self.HOST, out)
        self.assertIn("  - hostname: %s\n    service: http://127.0.0.1:8828\n" % self.HOST, out)
        self.assertLess(out.index("path: ^/s/"),
                        out.index("service: http://127.0.0.1:8828"))
        self.assertLess(out.index(self.HOST), out.index("http_status:404"))

    def test_idempotent_with_s_rule(self):
        once = dg.render_drop_ingress_augmentation(
            self.CONFIG, self.HOST, 8828, filedrop_port=8788)
        twice = dg.render_drop_ingress_augmentation(
            once, self.HOST, 8828, filedrop_port=8788)
        self.assertEqual(once, twice)
        self.assertEqual(once.count("- hostname: %s" % self.HOST), 2)  # /s/ + drop

    def test_drop_ingress_already_present_recognises_s_rule(self):
        once = dg.render_drop_ingress_augmentation(
            self.CONFIG, self.HOST, 8828, filedrop_port=8788)
        self.assertTrue(dg._drop_ingress_already_present(once, self.HOST, filedrop_port=8788))
        self.assertFalse(dg._drop_ingress_already_present(self.CONFIG, self.HOST, filedrop_port=8788))

    def test_no_filedrop_port_no_s_rule_backward_compatible(self):
        out = dg.render_drop_ingress_augmentation(self.CONFIG, self.HOST, 8828)
        self.assertNotIn("path: ^/s/", out)
        self.assertIn("service: http://127.0.0.1:8828", out)


# ---------------------------------------------------------------------------
# cli_filedrop_watchdog.cmd_share — public-first channel order
# ---------------------------------------------------------------------------
class TestCmdSharePublicFirst(unittest.TestCase):
    LOCAL_URL = "http://100.64.0.1:8790/TOKTOKTOKTOKTOKTOK12/rec.wav"
    LANE = ("drop-david.newlevel.media", 8870, "100.118.174.27")
    PRIVATE = ("http://100.64.0.1:8790/TOKTOKTOKTOKTOKTOK12/rec.wav",
               "http://192.168.1.5:8790/TOKTOKTOKTOKTOKTOK12/rec.wav")

    def _run(self, lane_full, status):
        def fake_share(path, base_dir=None):
            return (self.LOCAL_URL, Path("/tmp/x/TOK/rec.wav"))
        with mock.patch.object(fshare, "share", fake_share), \
             mock.patch.object(filedrop, "advertise_urls",
                               lambda port=None, path="": list(self.PRIVATE)), \
             mock.patch.object(fw, "_filedrop_is_live", lambda u, timeout=2: True), \
             mock.patch.object(dg, "resolve_public_lane_full",
                               lambda *a, **k: lane_full), \
             mock.patch.object(fw, "_public_share_status", lambda u, timeout=3: status):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                fw.cmd_share(types.SimpleNamespace(path="/tmp/rec.wav"))
            return out.getvalue(), err.getvalue()

    def test_public_url_first_on_200(self):
        out, _err = self._run(self.LANE, 200)
        first = out.strip().splitlines()[0]
        self.assertTrue(
            first.startswith("https://drop-david.newlevel.media/s/"
                             "TOKTOKTOKTOKTOKTOK12/rec.wav"),
            "first line was: %r" % first)

    def test_public_url_first_on_302_access_redirect(self):
        out, _err = self._run(self.LANE, 302)
        first = out.strip().splitlines()[0]
        self.assertTrue(first.startswith("https://drop-david.newlevel.media/s/"),
                        "first line was: %r" % first)

    def test_502_falls_back_to_labelled_private(self):
        out, err = self._run(self.LANE, 502)
        self.assertNotIn("https://drop-david", out)
        self.assertIn("192.168.1.5", out)             # the private fallback URLs
        self.assertIn("unreachable", err)
        self.assertIn("502", err)
        self.assertIn("see #1115", err)

    def test_timeout_falls_back_to_labelled_private(self):
        out, err = self._run(self.LANE, None)
        self.assertNotIn("https://drop-david", out)
        self.assertIn("timeout", err)
        self.assertIn("see #1115", err)

    def test_no_lane_prints_labelled_private_only(self):
        out, err = self._run(None, 200)
        self.assertNotIn("https://", out)
        self.assertIn("192.168.1.5", out)
        self.assertIn("no public lane on this box", err)
        self.assertIn("see #1115", err)


if __name__ == "__main__":
    unittest.main()
