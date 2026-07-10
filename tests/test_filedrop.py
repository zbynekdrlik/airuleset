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
        expected = int(env) if env else (filedrop.persisted_port()
                                         or filedrop.DEFAULT_PORT)
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
        self._env = os.environ.pop("FILEDROP_HOST", None)

    def tearDown(self):
        self._fd._ordered_ips = self._orig
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
        with m2.patch.object(airuleset, "filedrop_bind_ips",
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
        self.m = m
        self.ar = airuleset
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
            p = m.patch.object(airuleset, target, val)
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
        with self.m.patch.object(self.ar, "filedrop_persisted_port", lambda: 8791):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), 8791)

    def test_own_active_service_keeps_default(self):
        # our own live instance holds :8788 → that is OURS, no migration
        with self.m.patch.object(self.ar, "_run_systemctl",
                                 lambda a: (0, "active\n", "")):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"),
                             self.ar.FILEDROP_DEFAULT_PORT)
        self.assertFalse(self.port_file.exists())

    def test_default_free_uses_default_without_persisting(self):
        import socket
        # find a base whose port is genuinely free right now
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        base = probe.getsockname()[1]
        probe.close()
        with self.m.patch.object(self.ar, "FILEDROP_DEFAULT_PORT", base):
            self.assertEqual(self.ar._choose_filedrop_port("127.0.0.1"), base)
        self.assertFalse(self.port_file.exists(),
                         "default port needs no persisted override")

    def test_default_busy_picks_next_free_and_persists(self):
        import socket
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))          # OS-assigned free port
        base = blocker.getsockname()[1]         # keep it BOUND = foreign instance
        self.addCleanup(blocker.close)
        with self.m.patch.object(self.ar, "FILEDROP_DEFAULT_PORT", base):
            chosen = self.ar._choose_filedrop_port("127.0.0.1")
        self.assertNotEqual(chosen, base)
        self.assertGreater(chosen, base)
        self.assertTrue(self.port_file.exists(),
                        "migrated port must be persisted for the share CLI")
        self.assertEqual(int(self.port_file.read_text().strip()), chosen)


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
