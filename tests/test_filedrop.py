import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestConstants(unittest.TestCase):
    def test_constants_present(self):
        import filedrop
        env = os.getenv("FILEDROP_PORT")
        # #493: the no-env/no-persist fallback is now the DETERMINISTIC per-uid
        # port, not a blind shared DEFAULT_PORT.
        expected = int(env) if env else (filedrop.persisted_port()
                                         or filedrop.default_port_for_uid())
        self.assertEqual(filedrop.PORT, expected)
        self.assertEqual(filedrop.DEFAULT_PORT, 8788)
        self.assertEqual(filedrop.TOKEN_BYTES, 16)
        self.assertGreater(filedrop.MAX_SHARE_BYTES, 0)
        self.assertGreater(filedrop.PRUNE_AGE_S, 0)

    def test_persisted_port_reads_int_or_none(self):
        import unittest.mock as m
        import filedrop
        with tempfile.TemporaryDirectory() as d:
            pf = Path(d) / "filedrop.port"
            with m.patch.object(filedrop, "PORT_FILE", pf):
                self.assertIsNone(filedrop.persisted_port())   # missing
                pf.write_text("8791\n")
                self.assertEqual(filedrop.persisted_port(), 8791)
                pf.write_text("garbage")
                self.assertIsNone(filedrop.persisted_port())   # unreadable


class TestHostIp(unittest.TestCase):
    def setUp(self):
        import filedrop
        self._fd = filedrop
        self._orig = filedrop._ordered_ips
        self._orig_if = filedrop._iface_ips
        self._env = os.environ.pop("FILEDROP_HOST", None)
        # #438: host_ip() now delegates to bind_ips(), which calls the real
        # `ip -o -4 addr show` subprocess (_iface_ips) FIRST — on a real dev box
        # that returns genuine live interfaces regardless of the _ordered_ips
        # mock below, so these CIDR-priority scenarios would never take effect.
        # Force _iface_ips empty so bind_ips() falls back to the CIDR-only path
        # over the mocked _ordered_ips — the exact pattern TestBindIps.setUp
        # already uses, keeping these tests hermetic and driving the real
        # delegation end-to-end (not mocking bind_ips itself).
        filedrop._iface_ips = lambda: []

    def tearDown(self):
        self._fd._ordered_ips = self._orig
        self._fd._iface_ips = self._orig_if
        if self._env is not None:
            os.environ["FILEDROP_HOST"] = self._env

    def test_env_override_wins(self):
        os.environ["FILEDROP_HOST"] = "10.0.0.5"
        try:
            self.assertEqual(self._fd.host_ip(), "10.0.0.5")
        finally:
            os.environ.pop("FILEDROP_HOST", None)

    def test_prefers_tailscale(self):
        # Tailscale (100.64.0.0/10) is preferred over the dev LAN — stable across
        # network switches (#1). 100.5.x is NOT tailscale (outside 100.64/10).
        self._fd._ordered_ips = lambda: [
            "172.17.0.1", "10.77.10.175", "100.104.8.125", "100.5.0.1"]
        self.assertEqual(self._fd.host_ip(), "100.104.8.125")

    def test_prefers_dev_lan_when_no_tailscale(self):
        self._fd._ordered_ips = lambda: ["172.17.0.1", "10.77.9.21", "192.168.1.5"]
        self.assertEqual(self._fd.host_ip(), "10.77.9.21")

    def test_falls_back_to_first_non_loopback(self):
        self._fd._ordered_ips = lambda: ["127.0.0.1", "192.168.1.5"]
        self.assertEqual(self._fd.host_ip(), "192.168.1.5")

    def test_loopback_when_nothing(self):
        self._fd._ordered_ips = lambda: ["127.0.0.1"]
        self.assertEqual(self._fd.host_ip(), "127.0.0.1")

    def test_falls_back_to_loopback_on_a_public_ip_only_box(self):
        # #434: spinbike-vps has ONLY a public IPv4 (no tailscale, no
        # 10.77.* dev-LAN, no other private interface at all) -- the third
        # fallback used to accept ANY non-loopback IPv4 unconditionally,
        # including this public one, which the server itself (bind_ips())
        # never actually binds -- the health-check probe then always fails.
        # bind_ips() already degrades to ["127.0.0.1"] in this exact shape
        # (its own final `out or ["127.0.0.1"]` fallback); host_ip() must
        # agree, never returning a public/docker-bridge address.
        self._fd._ordered_ips = lambda: ["167.233.245.147", "fe80::1"]
        self.assertEqual(self._fd.host_ip(), "127.0.0.1")


class TestHostIpDelegatesToBindIps(unittest.TestCase):
    """#438: host_ip() must never diverge from bind_ips() -- the address the
    server actually binds. A container/bridge RFC1918 address (10.88.* podman,
    172.17.* docker) passes host_ip()'s old CIDR-only _is_private filter but is
    dropped BY INTERFACE NAME by bind_ips(), so host_ip() used to hand back a
    URL / health-probe target the server never listens on. Locked here against
    the exact live-proven divergence from the ticket body, mocking the
    _iface_ips / _ordered_ips enumeration PRIMITIVES (never bind_ips itself) so
    the test drives the REAL end-to-end delegation and stays hermetic on any
    box -- the box's own live interfaces never leak in."""

    def setUp(self):
        import filedrop
        self._fd = filedrop
        self._orig = filedrop._ordered_ips
        self._orig_if = filedrop._iface_ips
        self._env = os.environ.pop("FILEDROP_HOST", None)

    def tearDown(self):
        self._fd._ordered_ips = self._orig
        self._fd._iface_ips = self._orig_if
        if self._env is not None:
            os.environ["FILEDROP_HOST"] = self._env

    def test_podman_bridge_no_longer_diverges_from_bind_ips(self):
        # The exact live-proven divergence from the issue: a cni-podman0 bridge
        # RFC1918 address host_ip() used to return, though bind_ips() drops it
        # by interface name and the server therefore never binds it.
        self._fd._ordered_ips = lambda: ["10.88.0.1", "192.168.1.5"]
        self._fd._iface_ips = lambda: [
            ("10.88.0.1", "cni-podman0"),
            ("192.168.1.5", "eth0"),
        ]
        self.assertEqual(self._fd.bind_ips(), ["192.168.1.5"])
        # host_ip() MUST agree with the single source of truth, not the bridge.
        self.assertEqual(self._fd.host_ip(), "192.168.1.5")
        self.assertEqual(self._fd.host_ip(), self._fd.bind_ips()[0])

    def test_docker_bridge_dropped_tailscale_still_wins(self):
        # Companion (NOT an independent revert-kill): tailscale + docker0 bridge
        # + LAN. host_ip() returns the tailscale IP (bind_ips()[0]), never the
        # docker bridge. The OLD three-loop host_ip() also passed this (its
        # tailscale loop finds 100.90.94.41 first), so the revert-to-old-loops
        # kill lives in the podman test above and the no-tailscale test below --
        # this one just pins that a bridge never wins when tailscale is present.
        self._fd._ordered_ips = lambda: [
            "100.90.94.41", "172.17.0.1", "10.77.9.21"]
        self._fd._iface_ips = lambda: [
            ("172.17.0.1", "docker0"),
            ("100.90.94.41", "tailscale0"),
            ("10.77.9.21", "eth0"),
        ]
        self.assertEqual(self._fd.host_ip(), "100.90.94.41")
        self.assertEqual(self._fd.host_ip(), self._fd.bind_ips()[0])

    def test_no_tailscale_bind_priority_beats_input_order(self):
        # Second INDEPENDENT revert-kill (no bridge, no tailscale, no 10.77):
        # an other-10 (e.g. a wg/VPN 10.x) and a 192.168 LAN, both on REAL
        # interfaces. The OLD host_ip()'s third loop returned the first
        # _is_private in _ordered_ips INPUT order (192.168.1.5); the new
        # delegation returns bind_ips()[0], which _bind_priority-sorts the
        # other-10 (priority 2) ahead of 192.168 (priority 3) -> 10.0.5.9. This
        # locks the priority ALIGNMENT (not just the interface-drop) so the
        # class carries a second kill the tailscale companion above cannot.
        self._fd._ordered_ips = lambda: ["192.168.1.5", "10.0.5.9"]
        self._fd._iface_ips = lambda: [
            ("192.168.1.5", "eth0"),
            ("10.0.5.9", "wg0"),
        ]
        self.assertEqual(self._fd.bind_ips(), ["10.0.5.9", "192.168.1.5"])
        self.assertEqual(self._fd.host_ip(), "10.0.5.9")
        self.assertEqual(self._fd.host_ip(), self._fd.bind_ips()[0])

    def test_env_override_short_circuits_before_bind_ips(self):
        # FILEDROP_HOST wins before bind_ips() is ever consulted -- even when
        # the only interface is a bridge bind_ips() would otherwise reject.
        os.environ["FILEDROP_HOST"] = "10.0.0.9"
        try:
            self._fd._iface_ips = lambda: [("10.88.0.1", "cni-podman0")]
            self._fd._ordered_ips = lambda: ["10.88.0.1"]
            self.assertEqual(self._fd.host_ip(), "10.0.0.9")
        finally:
            os.environ.pop("FILEDROP_HOST", None)

    def test_degrades_to_loopback_when_no_enumeration_available(self):
        # Sandbox / no-`ip`-binary path (the systemd-served fallback the ticket
        # flags in point 3): both enumeration primitives yield nothing, so
        # host_ip() must still return loopback via bind_ips()'s own final
        # fallback -- never raise on bind_ips()[0], never a public/bridge IP.
        self._fd._iface_ips = lambda: []
        self._fd._ordered_ips = lambda: []
        self.assertEqual(self._fd.host_ip(), "127.0.0.1")
        self.assertEqual(self._fd.host_ip(), self._fd.bind_ips()[0])


class TestSafeName(unittest.TestCase):
    def test_strips_directory(self):
        from filedrop.share import safe_name
        self.assertEqual(safe_name("/tmp/centrum/rec.wav"), "rec.wav")

    def test_strips_leading_dots(self):
        from filedrop.share import safe_name
        self.assertNotIn("..", safe_name("..secret"))
        self.assertNotEqual(safe_name("..."), "")

    def test_replaces_unsafe_chars(self):
        from filedrop.share import safe_name
        out = safe_name("núdzový pud!ng.wav")
        self.assertRegex(out, r"\A[A-Za-z0-9._-]+\Z")

    def test_never_empty(self):
        from filedrop.share import safe_name
        self.assertEqual(safe_name(""), "file")
        self.assertEqual(safe_name("/"), "file")

    def test_length_cap(self):
        from filedrop.share import safe_name
        self.assertLessEqual(len(safe_name("a" * 500 + ".wav")), 128)


class TestShare(unittest.TestCase):
    def test_share_creates_token_dir_and_copies(self):
        from filedrop.share import share
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            src = Path(td) / "rec.wav"
            src.write_bytes(b"AUDIODATA")
            url, dest = share(str(src), base_dir=root)
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), b"AUDIODATA")
            self.assertEqual(dest.name, "rec.wav")
            # url shape: http://<ip>:<port>/<token>/rec.wav
            self.assertIn("/rec.wav", url)
            token = dest.parent.name
            self.assertIn(f"/{token}/", url)
            self.assertGreaterEqual(len(token), 16)

    def test_share_missing_file_raises(self):
        from filedrop.share import ShareError, share
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ShareError):
                share(str(Path(td) / "nope.bin"), base_dir=Path(td) / "drop")

    def test_share_directory_raises(self):
        from filedrop.share import ShareError, share
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ShareError):
                share(td, base_dir=Path(td) / "drop")

    def test_share_oversize_raises(self):
        import filedrop.share as sh
        orig = sh.MAX_SHARE_BYTES
        sh.MAX_SHARE_BYTES = 4
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "big.bin"
                src.write_bytes(b"123456789")
                with self.assertRaises(sh.ShareError):
                    sh.share(str(src), base_dir=Path(td) / "drop")
        finally:
            sh.MAX_SHARE_BYTES = orig

    def test_each_share_unique_token(self):
        from filedrop.share import share
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            src = Path(td) / "a.txt"
            src.write_bytes(b"x")
            u1, d1 = share(str(src), base_dir=root)
            u2, d2 = share(str(src), base_dir=root)
            self.assertNotEqual(d1.parent.name, d2.parent.name)


class TestPrune(unittest.TestCase):
    def test_age_prune(self):
        import filedrop.share as sh
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            old = root / "oldtoken"
            old.mkdir(parents=True)
            (old / "f.bin").write_bytes(b"data")
            old_time = time.time() - sh.PRUNE_AGE_S - 100
            os.utime(old, (old_time, old_time))
            sh.prune(base_dir=root)
            self.assertFalse(old.exists())

    def test_recent_kept(self):
        import filedrop.share as sh
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            fresh = root / "freshtoken"
            fresh.mkdir(parents=True)
            (fresh / "f.bin").write_bytes(b"data")
            sh.prune(base_dir=root)
            self.assertTrue(fresh.exists())

    def test_size_cap_evicts_oldest(self):
        import filedrop.share as sh
        orig = sh.PRUNE_MAX_TOTAL_BYTES
        sh.PRUNE_MAX_TOTAL_BYTES = 10
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td) / "drop"
                a = root / "atoken"
                a.mkdir(parents=True)
                (a / "f").write_bytes(b"x" * 8)
                os.utime(a, (time.time() - 50, time.time() - 50))  # older
                b = root / "btoken"
                b.mkdir(parents=True)
                (b / "f").write_bytes(b"x" * 8)
                sh.prune(base_dir=root)
                # combined 16 > cap 10 -> oldest (a) evicted, newest (b) kept
                self.assertFalse(a.exists())
                self.assertTrue(b.exists())
        finally:
            sh.PRUNE_MAX_TOTAL_BYTES = orig


class TestSafeResolve(unittest.TestCase):
    def _base(self, td):
        base = Path(td) / "drop"
        token = "A" * 22
        (base / token).mkdir(parents=True)
        (base / token / "rec.wav").write_bytes(b"data")
        return base, token

    def test_valid_path(self):
        from filedrop.server import safe_resolve
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            got = safe_resolve(f"/{token}/rec.wav", base)
            self.assertIsNotNone(got)
            self.assertEqual(got.name, "rec.wav")

    def test_rejects_traversal(self):
        from filedrop.server import safe_resolve
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            for bad in (f"/{token}/../{token}/rec.wav", "/../etc/passwd",
                        f"/{token}/..%2frec.wav", "/%2e%2e/%2e%2e/etc/passwd"):
                self.assertIsNone(safe_resolve(bad, base), bad)

    def test_rejects_wrong_segment_count(self):
        from filedrop.server import safe_resolve
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            self.assertIsNone(safe_resolve("/", base))
            self.assertIsNone(safe_resolve(f"/{token}", base))
            self.assertIsNone(safe_resolve(f"/{token}/sub/rec.wav", base))

    def test_rejects_bad_token_or_name(self):
        from filedrop.server import safe_resolve
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            self.assertIsNone(safe_resolve("/short/rec.wav", base))        # token too short
            self.assertIsNone(safe_resolve(f"/{token}/re c.wav", base))    # space in name

    def test_nonexistent_returns_none(self):
        from filedrop.server import safe_resolve
        with tempfile.TemporaryDirectory() as td:
            base, token = self._base(td)
            self.assertIsNone(safe_resolve(f"/{token}/missing.bin", base))

    def test_accepts_all_symbol_sanitized_name(self):
        # A file named "!!!" sanitizes (safe_name) to "_". The server MUST still
        # serve it — _NAME_RE deliberately permits a no-alphanumeric name. Guards
        # against a future regex tightening that would 404 legit shared files.
        from filedrop.server import safe_resolve
        from filedrop.share import safe_name
        self.assertEqual(safe_name("!!!"), "_")
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "drop"
            token = "B" * 22
            (base / token).mkdir(parents=True)
            (base / token / "_").write_bytes(b"data")
            self.assertIsNotNone(safe_resolve(f"/{token}/_", base))


class TestServerEndToEnd(unittest.TestCase):
    def test_get_served_file(self):
        from filedrop.server import make_server
        from filedrop.share import share
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            src = Path(td) / "rec.wav"
            src.write_bytes(b"HELLOAUDIO")
            url, dest = share(str(src), base_dir=root)
            token = dest.parent.name

            httpd = make_server(host="127.0.0.1", port=0, base_dir=root)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/{token}/rec.wav", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    self.assertEqual(r.read(), b"HELLOAUDIO")
                    self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
                # traversal / unknown -> 404
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
                self.assertEqual(cm.exception.code, 404)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_text_content_type_gets_utf8_charset(self):
        # #825: a served text/* file must carry an explicit charset, or a
        # browser with no BOM defaults to Windows-1252 and Slovak diacritics
        # (žčšťýáíéúäô) render as mojibake even though the file IS UTF-8.
        from filedrop.server import make_server
        from filedrop.share import share
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            src = Path(td) / "sumar.md"
            src.write_text("# Kolko a komu\n\nŽčšťýáíéúäô skoly.\n",
                            encoding="utf-8")
            url, dest = share(str(src), base_dir=root)
            token = dest.parent.name

            httpd = make_server(host="127.0.0.1", port=0, base_dir=root)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/{token}/sumar.md", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    ctype = r.headers.get("Content-Type")
                    self.assertIn("charset=utf-8", ctype)
                    self.assertTrue(ctype.startswith("text/markdown"))
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_binary_content_type_has_no_charset(self):
        # A binary type must NOT gain a nonsensical text charset — only
        # text/* (and the JSON/JS special-cased non-text/ MIME types) do.
        from filedrop.server import make_server
        from filedrop.share import share
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "drop"
            src = Path(td) / "rec.wav"
            src.write_bytes(b"HELLOAUDIO")
            url, dest = share(str(src), base_dir=root)
            token = dest.parent.name

            httpd = make_server(host="127.0.0.1", port=0, base_dir=root)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/{token}/rec.wav", timeout=5) as r:
                    self.assertEqual(r.status, 200)
                    ctype = r.headers.get("Content-Type")
                    self.assertNotIn("charset", ctype)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_favicon_no_content(self):
        from filedrop.server import make_server
        with tempfile.TemporaryDirectory() as td:
            httpd = make_server(host="127.0.0.1", port=0, base_dir=Path(td))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/favicon.ico", timeout=5) as r:
                    self.assertEqual(r.status, 204)
            finally:
                httpd.shutdown()
                httpd.server_close()

    def test_post_rejected(self):
        from filedrop.server import make_server
        with tempfile.TemporaryDirectory() as td:
            httpd = make_server(host="127.0.0.1", port=0, base_dir=Path(td))
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/x", data=b"y", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(cm.exception.code, 405)
            finally:
                httpd.shutdown()
                httpd.server_close()


class TestAirulesetWiring(unittest.TestCase):
    def test_subcommands_registered(self):
        import airuleset
        self.assertIn("share", airuleset.SUBCOMMANDS)
        self.assertIn("filedrop", airuleset.SUBCOMMANDS)

    def test_validate_filedrop_clean(self):
        import airuleset
        self.assertEqual(airuleset._validate_filedrop(), [])

    def test_module_in_profile(self):
        import airuleset
        entries = airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE)
        self.assertIn("modules/core/deliver-files-as-urls.md", entries)

    def test_module_in_generated_claude_md(self):
        import airuleset
        modules, _ = airuleset.categorize_entries(
            airuleset.parse_profile(airuleset.UNIVERSAL_PROFILE))
        md = airuleset.generate_claude_md(modules)
        self.assertIn("modules/core/deliver-files-as-urls.md", md)

    def test_service_template_has_placeholder(self):
        import airuleset
        tmpl = airuleset.FILEDROP_SERVICE_TEMPLATE.read_text()
        self.assertIn("{{REPO_DIR}}", tmpl)
        self.assertIn("{{HOST_IP}}", tmpl)
        self.assertIn("{{HOST_IPS}}", tmpl)     # multi-interface bind list
        self.assertIn("{{PORT}}", tmpl)
        self.assertIn("filedrop --serve", tmpl)

    def test_render_unit_substitutes_placeholders(self):
        import airuleset
        unit = airuleset._render_filedrop_unit()
        self.assertNotIn("{{", unit)            # all placeholders substituted
        self.assertIn("FILEDROP_HOST=", unit)
        self.assertIn("FILEDROP_HOSTS=", unit)  # comma list of private bind IPs
        self.assertIn("airuleset.py filedrop --serve", unit)

    def test_render_unit_bakes_the_bind_list(self):
        import unittest.mock as m2

        import airuleset
        import cli_filedrop_watchdog  # #433 L-B: _render_filedrop_unit moved here; patch the LEAF
        with m2.patch.object(cli_filedrop_watchdog, "filedrop_bind_ips",
                             return_value=["100.90.94.41", "10.77.9.21"]):
            unit = airuleset._render_filedrop_unit()
        self.assertIn("Environment=FILEDROP_HOSTS=100.90.94.41,10.77.9.21", unit)
        # {{HOST_IP}} must not be corrupted by the {{HOST_IPS}} substitution
        self.assertNotIn("S}}", unit)

    def test_render_unit_bakes_chosen_port(self):
        import airuleset
        unit = airuleset._render_filedrop_unit(8791)
        self.assertIn("Environment=FILEDROP_PORT=8791", unit)


class TestChooseFiledropPort(unittest.TestCase):
    """A second airuleset user on ONE host (montalu@dev1, marek@gatekeeper) must
    not restart-loop on the first user's :8788 (Errno 98, observed on
    montalu@dev1 2026-07-04) — install picks + persists a free per-user port."""

    def setUp(self):
        import unittest.mock as m
        import airuleset
        import cli_filedrop_watchdog
        self.m = m
        self.ar = airuleset
        self.fw = cli_filedrop_watchdog  # #433 L-B: _choose_filedrop_port moved here; patch the LEAF, not the facade
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.port_file = Path(tmp.name) / "filedrop.port"
        prev = os.environ.pop("FILEDROP_PORT", None)
        if prev is not None:
            self.addCleanup(os.environ.__setitem__, "FILEDROP_PORT", prev)
        for target, val in [
            ("FILEDROP_PORT_FILE", self.port_file),
            ("filedrop_persisted_port", lambda: None),
            ("_run_systemctl", lambda a: (3, "inactive", "")),   # our svc NOT active
        ]:
            p = m.patch.object(cli_filedrop_watchdog, target, val)
            p.start()
            self.addCleanup(p.stop)

    def test_env_override_wins(self):
        os.environ["FILEDROP_PORT"] = "9999"
        try:
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), 9999)
        finally:
            os.environ.pop("FILEDROP_PORT", None)

    def test_persisted_choice_is_stable(self):
        # a previously persisted port is reused verbatim — the URL never moves
        with self.m.patch.object(self.fw, "filedrop_persisted_port", lambda: 8791):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), 8791)

    def test_own_active_service_keeps_its_deterministic_port(self):
        # #493: our own live instance holds its DETERMINISTIC per-uid port →
        # that is OURS, no migration, no persist override written.
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: 8794), \
                self.m.patch.object(self.fw, "_run_systemctl",
                                    lambda a: (0, "active\n", "")):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), 8794)
        self.assertFalse(self.port_file.exists())

    def test_deterministic_port_free_is_used_without_persisting(self):
        # #493: the per-uid port, when free, is used verbatim — the share CLI
        # re-derives the SAME value from our uid, so no persist file is needed.
        import socket
        # find a base whose port is genuinely free right now
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        base = probe.getsockname()[1]
        probe.close()
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: base):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), base)
        self.assertFalse(self.port_file.exists(),
                         "the deterministic per-uid port needs no persist override")

    def test_deterministic_port_busy_picks_next_free_and_persists(self):
        # #493: only when the per-uid port is genuinely held by another instance
        # (a rare uid-mod collision / unrelated service) do we probe upward and
        # PERSIST the fallback so the unit + share CLI agree.
        import socket
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))          # OS-assigned free port
        base = blocker.getsockname()[1]         # keep it BOUND = foreign instance
        self.addCleanup(blocker.close)
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: base):
            chosen = self.ar._choose_filedrop_port("127.0.0.1")
        self.assertNotEqual(chosen, base)
        self.assertGreater(chosen, base)
        self.assertTrue(self.port_file.exists(),
                        "a collision-fallback port must be persisted for the share CLI")
        self.assertEqual(int(self.port_file.read_text().strip()), chosen)

    def test_migrated_persisted_port_held_by_foreign_user_repicks(self):
        # a ~/.claude migrated from another box carries THAT box's port — here
        # it can be a DIFFERENT user's live file-drop (montalu@subdev inherited
        # dev1's 8789 == marek's subdev port; #33 migration, 2026-07-24).
        # Foreign holder + our service inactive → drop the stale file and fall
        # back to our OWN deterministic per-uid port (#493).
        import socket
        hog = socket.socket()
        hog.bind(("127.0.0.1", 0))
        taken = hog.getsockname()[1]
        self.addCleanup(hog.close)
        # a free deterministic port distinct from the foreign-held one
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        want = probe.getsockname()[1]
        probe.close()
        self.port_file.write_text("%d\n" % taken)
        with self.m.patch.object(self.fw, "filedrop_persisted_port",
                                 lambda: taken), \
                self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                    lambda: want):
            got = self.ar._choose_filedrop_port("127.0.0.1")
        self.assertEqual(got, want,
                         "a foreign-held persisted port must be dropped for our "
                         "own deterministic port")
        self.assertNotEqual(got, taken)
        # the stale foreign value must NOT survive in the persist file
        self.assertFalse(
            self.port_file.exists() and self.port_file.read_text().strip() == str(taken),
            "the stale foreign persisted value must be dropped")

    def test_persisted_port_kept_when_our_service_is_active(self):
        # our own live instance actively serves the persisted port — a bind
        # test there would always fail (the port is legitimately in use by
        # US), so it must NOT be treated as foreign and re-picked.
        with self.m.patch.object(self.fw, "filedrop_persisted_port",
                                 lambda: 8791), \
                self.m.patch.object(self.fw, "_run_systemctl",
                                    lambda a: (0, "active\n", "")):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), 8791)

    def test_distinct_uids_get_distinct_ports(self):
        # #493: the racy shared-:8788 default piled all ~12 subdev stream
        # accounts onto ONE port — 10 of 12 servers dead unable to bind it,
        # every loser's share URL 404ing off a stranger's server. Two DIFFERENT
        # users (distinct uid) must get DIFFERENT filedrop ports so their
        # servers never contend and their URLs never cross into another user's
        # store. Drive _choose_filedrop_port as two users via os.getuid; the
        # setUp already pins the service inactive + no persisted port.
        ports = []
        for uid in (1002, 1004):        # real subdev uids (montalu, montalu2)
            with self.m.patch("os.getuid", lambda u=uid: u):
                ports.append(self.ar._choose_filedrop_port("127.0.0.1"))
        self.assertNotEqual(
            ports[0], ports[1],
            "two users must not share one filedrop port (#493 pileup)")

    def _active_on(self, port):
        """A _run_systemctl double: our filedrop service is active and its unit
        bakes FILEDROP_PORT=<port> (what _filedrop_current_served_port reads)."""
        env_line = f"Environment=FILEDROP_HOST=127.0.0.1 FILEDROP_PORT={port}"

        def _fake(a):
            if a[:1] == ["is-active"]:
                return (0, "active\n", "")
            if a[:1] == ["show"]:
                return (0, env_line + "\n", "")
            return (0, "", "")
        return _fake

    def test_active_service_keeps_its_deterministic_port_even_when_bind_fails(self):
        # our live service already holds `want`; a bind-test of `want` fails (WE
        # hold it), but served==want so we keep it — no needless migration.
        import socket
        hold = socket.socket()
        hold.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        hold.bind(("127.0.0.1", 0))
        hold.listen(1)
        want = hold.getsockname()[1]
        self.addCleanup(hold.close)
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: want), \
                self.m.patch.object(self.fw, "_run_systemctl", self._active_on(want)):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), want)
        self.assertFalse(self.port_file.exists())

    def test_active_on_legacy_port_migrates_to_free_deterministic_port(self):
        # our live service sits on a legacy port; our deterministic `want` is
        # free → migrate onto it (the restart rebinds), no stale persist.
        import socket
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        want = probe.getsockname()[1]           # genuinely free
        probe.close()
        legacy = 8788                           # our current (different) port
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: want), \
                self.m.patch.object(self.fw, "_run_systemctl",
                                    self._active_on(legacy)):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), want)

    def test_active_service_never_bakes_a_foreign_held_deterministic_port(self):
        # #493 review MAJOR: mid-migration our live service sits on a legacy port
        # while a FOREIGN account holds `want`. We must NOT bake `want` (that
        # kills our service + points share at the stranger → the 404 itself);
        # fall through to a genuinely serveable port instead.
        import socket
        foreign = socket.socket()
        foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        foreign.bind(("127.0.0.1", 0))
        foreign.listen(1)
        want = foreign.getsockname()[1]         # a FOREIGN live server holds want
        self.addCleanup(foreign.close)
        with self.m.patch.object(self.fw, "filedrop_default_port_for_uid",
                                 lambda: want), \
                self.m.patch.object(self.fw, "_run_systemctl",
                                    self._active_on(8788)):
            got = self.ar._choose_filedrop_port("127.0.0.1")
        self.assertNotEqual(
            got, want,
            "must never bake a foreign-held want (dead service + 404)")
        self.assertTrue(
            got == 8788 or self.fw._filedrop_port_bindable("127.0.0.1", got),
            "the chosen port must be genuinely serveable by us")

    def test_filedrop_port_bindable_reads_foreign_listen_as_taken(self):
        # a live FOREIGN LISTEN must read as NOT bindable — else the probe would
        # treat a stranger's server as a free port and reintroduce #493. A
        # genuinely free port reads as bindable. (SO_REUSEADDR must not misjudge
        # a live LISTEN as reclaimable.)
        import socket
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        taken = srv.getsockname()[1]
        self.addCleanup(srv.close)
        self.assertFalse(self.fw._filedrop_port_bindable("127.0.0.1", taken),
                         "a live foreign LISTEN must read as taken")
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
        probe.close()
        self.assertTrue(self.fw._filedrop_port_bindable("127.0.0.1", free),
                        "a free port must read as bindable")


class TestDefaultPortForUid(unittest.TestCase):
    """#493: a DETERMINISTIC per-uid file-drop port so N accounts on ONE host
    (the ~12 subdev streams) never race for a single shared :8788."""

    def test_deterministic_and_default_for_uid_base(self):
        import filedrop
        # a single-user box (uid ≡ 0 mod 1000) keeps the historical default
        self.assertEqual(filedrop.default_port_for_uid(1000), filedrop.DEFAULT_PORT)
        self.assertEqual(filedrop.default_port_for_uid(1002), filedrop.DEFAULT_PORT + 2)
        # deterministic: same uid → same port, every call
        self.assertEqual(filedrop.default_port_for_uid(1007),
                         filedrop.default_port_for_uid(1007))

    def test_all_managed_subdev_uids_map_to_distinct_ports(self):
        import filedrop
        uids = [1000, 1001, 1002, 1003, 1004, 1005,
                1006, 1007, 1011, 1012, 1013, 1014]   # live subdev uid set
        ports = {filedrop.default_port_for_uid(u) for u in uids}
        self.assertEqual(len(ports), len(uids),
                         "each managed uid must map to its OWN filedrop port")

    def test_module_port_falls_back_to_the_per_uid_port(self):
        # with no FILEDROP_PORT env and no persisted file, filedrop.PORT must
        # derive from the per-uid port, not a blind shared 8788 — so the share
        # CLI advertises the SAME port the per-uid server binds.
        import filedrop
        env = os.getenv("FILEDROP_PORT")
        if env is None and filedrop.persisted_port() is None:
            self.assertEqual(filedrop.PORT, filedrop.default_port_for_uid())

    def test_module_port_fallback_wires_to_per_uid_default(self):
        # #493 review MINOR: the no-env/no-persist fallback for filedrop.PORT
        # must derive from default_port_for_uid, never a blind shared
        # DEFAULT_PORT. A STRUCTURAL lock because the push gate runs as uid 1000,
        # where the two values are numerically EQUAL, so a behavioral assertion
        # is a tautology there and a mutation reverting the wiring SURVIVES.
        import inspect

        import filedrop
        port_lines = [ln for ln in inspect.getsource(filedrop).splitlines()
                      if ln.lstrip().startswith("PORT =")]
        self.assertTrue(port_lines, "filedrop must define a module-level PORT")
        self.assertIn(
            "default_port_for_uid", port_lines[0],
            "PORT fallback must derive from the per-uid port, not a blind "
            "shared DEFAULT_PORT (#493)")


class TestBindIps(unittest.TestCase):
    """bind_ips() / advertise_urls() — the multi-interface URL fix (2026-07-10).

    The user is remote and switches between tailscale and the LAN; a single-IP URL
    kept being unreachable on the network he was NOT on. bind_ips() is the one
    source of truth for which PRIVATE addresses both servers bind and both CLIs
    advertise — tailscale first, LAN next, never the public/loopback/docker IPs."""

    def setUp(self):
        import filedrop
        self._fd = filedrop
        self._orig = filedrop._ordered_ips
        self._orig_if = filedrop._iface_ips
        # These tests exercise the CIDR-only fallback path — force _iface_ips empty
        # so bind_ips() uses the mocked _ordered_ips (the iface path is tested below).
        filedrop._iface_ips = lambda: []

    def tearDown(self):
        self._fd._ordered_ips = self._orig
        self._fd._iface_ips = self._orig_if

    def test_is_private_classification(self):
        p = self._fd._is_private
        self.assertTrue(p("100.90.94.41"))     # tailscale
        self.assertTrue(p("10.77.9.21"))       # dev LAN
        self.assertTrue(p("192.168.1.5"))      # RFC1918 /16
        self.assertFalse(p("88.99.170.148"))   # gatekeeper PUBLIC — never bind
        self.assertFalse(p("127.0.0.1"))       # loopback
        self.assertFalse(p("172.17.0.1"))      # docker bridge — noise
        self.assertFalse(p("fe80::1"))         # IPv6

    def test_bind_ips_tailscale_first_then_lan_excludes_public_and_docker(self):
        self._fd._ordered_ips = lambda: [
            "88.99.170.148", "172.17.0.1", "10.77.9.21",
            "100.90.94.41", "192.168.1.5", "127.0.0.1"]
        self.assertEqual(
            self._fd.bind_ips(),
            ["100.90.94.41", "10.77.9.21", "192.168.1.5"])

    def test_bind_ips_dedups(self):
        self._fd._ordered_ips = lambda: ["10.77.9.21", "10.77.9.21", "100.90.94.41"]
        self.assertEqual(self._fd.bind_ips(), ["100.90.94.41", "10.77.9.21"])

    def test_bind_ips_falls_back_to_loopback_when_nothing_private(self):
        self._fd._ordered_ips = lambda: ["88.99.170.148", "172.17.0.1"]
        self.assertEqual(self._fd.bind_ips(), ["127.0.0.1"])

    def test_advertise_urls_one_per_interface(self):
        self._fd._ordered_ips = lambda: ["100.90.94.41", "10.77.9.21"]
        self.assertEqual(
            self._fd.advertise_urls(port=8788, path="tok/f.bin"),
            ["http://100.90.94.41:8788/tok/f.bin",
             "http://10.77.9.21:8788/tok/f.bin"])

    def test_advertise_urls_adds_leading_slash(self):
        self._fd._ordered_ips = lambda: ["10.77.9.21"]
        self.assertEqual(self._fd.advertise_urls(port=9, path="tok/"),
                         ["http://10.77.9.21:9/tok/"])

    def test_iface_aware_drops_container_bridges_keeps_tailscale_and_lan(self):
        # `ip -o -4 addr` view: tailscale + a real LAN iface + docker/podman bridges.
        # The bridge RFC1918 IPs (10.88.* podman, 172.17.* docker) must be dropped by
        # interface name even though 10.88.* passes the RFC1918 CIDR test.
        self._fd._iface_ips = lambda: [
            ("100.90.94.41", "tailscale0"),
            ("10.77.9.21", "eth0"),
            ("10.88.1.112", "cni-podman0"),
            ("172.17.0.1", "docker0"),
            ("192.168.10.20", "wlan0"),
        ]
        self.assertEqual(self._fd.bind_ips(),
                         ["100.90.94.41", "10.77.9.21", "192.168.10.20"])

    def test_iface_aware_keeps_tailscale_even_on_odd_iface_name(self):
        # tailscale is kept by CIDR regardless of interface name.
        self._fd._iface_ips = lambda: [("100.90.94.41", "cni0")]
        self.assertEqual(self._fd.bind_ips(), ["100.90.94.41"])


class TestMultiBindServer(unittest.TestCase):
    """The persistent file-drop server binds every private interface, and a host
    that fails to bind is SKIPPED (a stale LAN IP must not crash-loop the unit)."""

    def test_make_servers_binds_each_host(self):
        from filedrop.server import make_servers
        base = tempfile.mkdtemp()
        servers = make_servers(["127.0.0.1", "127.0.0.2"], port=0, base_dir=base)
        self.addCleanup(lambda: [s.server_close() for s in servers])
        self.assertEqual(len(servers), 2)

    def test_make_servers_skips_unbindable_but_keeps_the_rest(self):
        from filedrop.server import make_servers
        base = tempfile.mkdtemp()
        # 203.0.113.9 (TEST-NET-3) is not a local address → bind fails → skipped;
        # 127.0.0.1 binds fine → server stays up on it.
        servers = make_servers(["203.0.113.9", "127.0.0.1"], port=0, base_dir=base)
        self.addCleanup(lambda: [s.server_close() for s in servers])
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0].server_address[0], "127.0.0.1")

    def test_make_servers_raises_when_none_bind(self):
        from filedrop.server import make_servers
        with self.assertRaises(OSError):
            make_servers(["203.0.113.9"], port=0)

    def test_run_server_serves_on_bound_host(self):
        import socket as _s

        from filedrop.server import run_server
        base = Path(tempfile.mkdtemp())
        tok = "abcdef0123456789tok"       # >=16 chars — _TOKEN_RE requires it
        (base / tok).mkdir()
        (base / tok / "f.txt").write_bytes(b"hi")
        sk = _s.socket()
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
        sk.close()
        t = threading.Thread(
            target=run_server,
            kwargs={"hosts": ["127.0.0.1"], "port": port, "base_dir": str(base)},
            daemon=True)
        t.start()
        time.sleep(0.4)
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/{tok}/f.txt", timeout=3)
        self.assertEqual(r.read(), b"hi")


if __name__ == "__main__":
    unittest.main()
