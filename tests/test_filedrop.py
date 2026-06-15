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
        self.assertEqual(filedrop.PORT, int(os.getenv("FILEDROP_PORT", "8788")))
        self.assertEqual(filedrop.TOKEN_BYTES, 16)
        self.assertGreater(filedrop.MAX_SHARE_BYTES, 0)
        self.assertGreater(filedrop.PRUNE_AGE_S, 0)


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

    def test_prefers_dev_lan(self):
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
                a = root / "atoken"; a.mkdir(parents=True)
                (a / "f").write_bytes(b"x" * 8)
                os.utime(a, (time.time() - 50, time.time() - 50))  # older
                b = root / "btoken"; b.mkdir(parents=True)
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
        self.assertIn("filedrop --serve", tmpl)


if __name__ == "__main__":
    unittest.main()
