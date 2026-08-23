"""#644 — installable-PWA assets for the webterm.

Covers the generation leaf (`cli_webterm_pwa`): per-domain manifest, the
network-only service worker, and pure-stdlib PNG icons; the dashboard `<head>`
wiring (manifest link + theme-color + SW registration in
`cli_webterm.render_dashboard_html`); the provisioning write; and the decoupled
route contract with the gateway.
"""
import json
import math
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_webterm as w  # noqa: E402
import cli_webterm_profiles as profiles  # noqa: E402
import cli_webterm_pwa as pwa  # noqa: E402


def _png_dims(b):
    assert b[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert b[12:16] == b"IHDR", "no IHDR"
    return struct.unpack(">II", b[16:24])


def _decode_rgba(b):
    """Decode a filter-0, 8-bit RGBA PNG (what render_icon_png emits) to
    (width, height, pixels-bytes). Concatenates IDAT chunks, inflates, and
    strips the per-scanline filter byte (always 0/None here)."""
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", b[16:24])
    i, idat = 8, bytearray()
    while i < len(b):
        ln = struct.unpack(">I", b[i:i + 4])[0]
        typ = b[i + 4:i + 8]
        if typ == b"IDAT":
            idat += b[i + 8:i + 8 + ln]
        i += 12 + ln
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    pixels = bytearray()
    for y in range(height):
        off = y * (stride + 1)
        assert raw[off] == 0, "unexpected filter type"
        pixels += raw[off + 1:off + 1 + stride]
    return width, height, bytes(pixels)


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

    def test_icon_actually_draws_a_glyph_on_the_background(self):
        # Decode the raster and prove BOTH background AND foreground (glyph)
        # pixels exist — a blank/solid icon (feature broken) would fail here.
        fg = pwa._hex_rgba(w.CAMPBELL_THEME["brightGreen"])[:3]
        bg = pwa._hex_rgba(w.CAMPBELL_THEME["background"])[:3]
        width, height, px = _decode_rgba(pwa.render_icon_png(192))
        n_fg = n_bg = 0
        for j in range(0, len(px), 4):
            rgb = (px[j], px[j + 1], px[j + 2])
            if rgb == fg:
                n_fg += 1
            elif rgb == bg:
                n_bg += 1
        self.assertGreater(n_fg, 0, "no glyph pixels — icon is blank")
        self.assertGreater(n_bg, 0, "no background pixels")

    def test_maskable_glyph_stays_within_the_safe_zone(self):
        # A maskable icon may be cropped to a circle (~center 80%); every glyph
        # pixel must sit within radius 0.40 of the centre or it clips.
        fg = pwa._hex_rgba(w.CAMPBELL_THEME["brightGreen"])[:3]
        width, height, px = _decode_rgba(pwa.render_icon_png(512, maskable=True))
        max_r = 0.0
        for j in range(0, len(px), 4):
            if (px[j], px[j + 1], px[j + 2]) == fg:
                p = j // 4
                x, y = p % width, p // width
                r = math.hypot((x + 0.5) / width - 0.5, (y + 0.5) / height - 0.5)
                max_r = max(max_r, r)
        self.assertGreater(max_r, 0.0)              # a glyph is actually present
        self.assertLess(max_r, 0.40, "maskable glyph escapes the safe zone")


class TestDashboardHeadWiring(unittest.TestCase):
    def _dash(self):
        inv = [{"id": "dev1", "label": "dev1", "kind": "owner",
                "local": True, "host": None, "user": None}]
        return w.render_dashboard_html(inv, ttyd_base="/t")

    def test_head_links_the_manifest_with_credentials(self):
        html = self._dash()
        self.assertIn('rel="manifest"', html)
        self.assertIn(pwa.MANIFEST_FILE, html)
        # The manifest is auth-gated, so the link MUST fetch with credentials or
        # the browser's anonymous fetch fails and the app never becomes
        # installable (MDN: use-credentials required for a gated manifest).
        self.assertIn('crossorigin="use-credentials"', html)

    def test_head_references_the_icon(self):
        html = self._dash()
        self.assertIn(pwa.ICON_192, html)          # drift-locked to the constant

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
