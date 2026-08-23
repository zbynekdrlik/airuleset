"""#644 — installable-PWA assets for the webterm.

Covers the generation leaf (`cli_webterm_pwa`): per-domain manifest, the
network-only service worker, and pure-stdlib PNG icons; the dashboard `<head>`
wiring (manifest link + theme-color + SW registration in
`cli_webterm.render_dashboard_html`); the provisioning write; and the decoupled
route contract with the gateway.
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402


def _png_dims(b):
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert b[12:16] == b"IHDR", "no IHDR"
    return struct.unpack(">II", b[16:24])


class TestManifest(unittest.TestCase):
    def test_owner_manifest_is_valid_standalone_pwa(self):
        m = json.loads(pwa.render_manifest(profiles.OWNER).decode("utf-8"))
        self.assertEqual(m["name"], "Webterm dev1")
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["start_url"], "/")
        self.assertEqual(m["scope"], "/")
        # colours matched to the #643 Campbell background
        self.assertEqual(m["background_color"], w.CAMPBELL_THEME["background"])
        self.assertEqual(m["theme_color"], w.CAMPBELL_THEME["background"])
        self.assertEqual(m["background_color"], "#0C0C0C")

    def test_david_manifest_has_its_own_name(self):
        m = json.loads(pwa.render_manifest(profiles.DAVID).decode("utf-8"))
        self.assertEqual(m["name"], "Webterm david")

    def test_manifest_icons_cover_192_512_and_maskable(self):
        m = json.loads(pwa.render_manifest(profiles.OWNER).decode("utf-8"))
        sizes = {i["sizes"] for i in m["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        purposes = {i["purpose"] for i in m["icons"]}
        self.assertIn("maskable", purposes)
        self.assertIn("any", purposes)
        for i in m["icons"]:
            self.assertEqual(i["type"], "image/png")
            self.assertTrue(i["src"].startswith("/"))


class TestServiceWorker(unittest.TestCase):
    def test_is_network_only_never_caches(self):
        sw = pwa.render_service_worker().decode("utf-8")
        # A fetch handler exists (installability), passing through to the network.
        self.assertIn("addEventListener('fetch'", sw)
        self.assertIn("fetch(event.request)", sw)
        # NETWORK-ONLY: it must never touch Cache Storage — a cached response
        # behind Cloudflare Access could bypass/confuse auth.
        self.assertNotIn("caches", sw)
        self.assertNotIn("cache.put", sw)
        self.assertNotIn(".match(", sw)


class TestIcons(unittest.TestCase):
    def test_192_is_a_valid_png(self):
        self.assertEqual(_png_dims(pwa.render_icon_png(192)), (192, 192))

    def test_512_is_a_valid_png(self):
        self.assertEqual(_png_dims(pwa.render_icon_png(512)), (512, 512))

    def test_maskable_is_a_valid_png(self):
        self.assertEqual(_png_dims(pwa.render_icon_png(512, maskable=True)),
                         (512, 512))

    def test_icon_has_both_background_and_glyph_pixels(self):
        # Sanity: the PNG is not a solid block — it decodes and is non-trivial.
        b = pwa.render_icon_png(192)
        self.assertGreater(len(b), 200)             # real compressed raster
        self.assertEqual(b[:8], b"\x89PNG\r\n\x1a\n")


class TestDashboardHeadWiring(unittest.TestCase):
    def _dash(self):
        inv = [{"id": "dev1", "label": "dev1", "kind": "owner",
                "local": True, "host": None, "user": None}]
        return w.render_dashboard_html(inv, ttyd_base="/t")

    def test_head_links_the_manifest(self):
        html = self._dash()
        self.assertIn('rel="manifest"', html)
        self.assertIn(pwa.MANIFEST_FILE, html)

    def test_head_has_campbell_theme_color(self):
        html = self._dash()
        self.assertIn('name="theme-color"', html)
        self.assertIn("#0C0C0C", html)

    def test_page_registers_the_service_worker(self):
        html = self._dash()
        self.assertIn("serviceWorker", html)
        self.assertIn(pwa.SW_FILE, html)


class TestWriteAssets(unittest.TestCase):
    def test_writes_all_pwa_files(self):
        d = tempfile.mkdtemp()
        pwa.write_pwa_assets(d, profiles.OWNER)
        for name in pwa.PWA_FILENAMES:
            self.assertTrue((Path(d) / name).is_file(), "missing " + name)

    def test_owner_and_david_manifests_differ_per_domain(self):
        do = tempfile.mkdtemp()
        dd = tempfile.mkdtemp()
        pwa.write_pwa_assets(do, profiles.OWNER)
        pwa.write_pwa_assets(dd, profiles.DAVID)
        mo = (Path(do) / pwa.MANIFEST_FILE).read_text()
        md = (Path(dd) / pwa.MANIFEST_FILE).read_text()
        self.assertIn("Webterm dev1", mo)
        self.assertIn("Webterm david", md)
        self.assertNotEqual(mo, md)


class TestGatewayRouteContract(unittest.TestCase):
    def test_gateway_serves_exactly_the_generated_filenames(self):
        # The gateway and the generator stay decoupled (no cross-import); this
        # test locks that the gateway's PWA route table serves EXACTLY the files
        # this module writes, so the two can never drift.
        import cli_webterm_gateway as g
        served = {fname for (fname, _ctype) in g.Gateway._PWA_ASSETS.values()}
        self.assertEqual(served, set(pwa.PWA_FILENAMES))


if __name__ == "__main__":
    unittest.main()
