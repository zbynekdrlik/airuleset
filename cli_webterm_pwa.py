"""airuleset webterm — installable-PWA assets (#644).

The owner wants the webterm as an INSTALLABLE PWA: a standalone Windows window
with no browser chrome (URL bar / tabs / borders) and its own taskbar icon,
visually like Windows Terminal. Chromium installability needs a web manifest +
icons (192 & 512) + a registered service worker + https (Cloudflare fronts the
gateway). This leaf GENERATES those assets; the gateway (`cli_webterm_gateway`)
SERVES them, and the dashboard `<head>` (`cli_webterm.render_dashboard_html`)
links the manifest + registers the SW.

Design invariants:
  * The service worker is NETWORK-ONLY — it never touches Cache Storage. Every
    request sits behind Cloudflare Access; a cached response could bypass or
    confuse the auth flow, so the SW is a pure pass-through fetch handler whose
    only purpose is to satisfy the installability criterion.
  * Per-DOMAIN identity: the manifest `name` differs per profile ("Webterm
    dev1" for the owner / "Webterm david" for david). The gateway serves each
    profile's own manifest because provisioning writes a DIFFERENT manifest into
    each profile's dash dir — no per-request profile logic in the gateway.
  * NO external assets: icons are generated in-repo as PNGs by a tiny pure-stdlib
    (`zlib`/`struct`) encoder drawing a `>_` terminal glyph — no PIL, no font
    fetch (CSP / Cloudflare-Access safe). `background_color`/`theme_color` reuse
    the #643 Campbell background (`cli_webterm.CAMPBELL_THEME["background"]`).

Deliberately a small leaf: imports `cli_webterm` (for the Campbell background)
and `cli_webterm_profiles` (for the profile constants) at module level; the
`cli_webterm` provisioning path imports THIS module lazily (inside the setup
functions), so there is no module-level import cycle — the same shape
`cli_webterm_david` uses.
"""
import json
import math
import struct
import zlib

import cli_webterm as w
import cli_webterm_profiles as profiles

# Per-profile PWA identity: (name, short_name). Owner = the dev1 fleet gateway,
# david = the subdev developer gateway. Rendered into a per-profile manifest.
# #655 (#644 follow-up): the owner rejected the "dev1" suffix in his installed
# PWA — plain "Webterm" for zbynek.newlevel.media. "Webterm david" stays for
# david.newlevel.media (distinct installed app). short_name kept consistent with
# name. NOTE: Chrome does not reliably refresh an installed manifest's name, so
# the owner must re-add (re-install) the app for the new name to appear.
APP_NAMES = {
    profiles.OWNER: ("Webterm", "Webterm"),
    profiles.DAVID: ("Webterm david", "david"),
    profiles.MAREK: ("Webterm marek", "marek"),
}

# The canonical PWA asset FILENAMES this module writes into a dash dir. The
# gateway's own route table (cli_webterm_gateway.Gateway._PWA_ASSETS) serves
# exactly these names from `dash_index.parent`; a test locks the two lists so
# they can never drift (the modules stay decoupled — no cross-import).
MANIFEST_FILE = "manifest.webmanifest"
SW_FILE = "sw.js"
ICON_192 = "icon-192.png"
ICON_512 = "icon-512.png"
ICON_MASKABLE_512 = "icon-maskable-512.png"
PWA_FILENAMES = (MANIFEST_FILE, SW_FILE, ICON_192, ICON_512, ICON_MASKABLE_512)


def render_manifest(profile):
    """The web app manifest bytes for `profile` (per-domain `name`). `display:
    standalone` (no browser chrome), `start_url`/`scope` = `/`, colours matched
    to the #643 Campbell background, and 192/512 icons plus a maskable 512."""
    name, short_name = APP_NAMES.get(profile, ("Webterm", "webterm"))
    bg = w.CAMPBELL_THEME["background"]           # #0C0C0C — single source of truth
    manifest = {
        "name": name,
        "short_name": short_name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": bg,
        "theme_color": bg,
        "icons": [
            {"src": "/" + ICON_192, "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/" + ICON_512, "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/" + ICON_MASKABLE_512, "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    }
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# NETWORK-ONLY service worker. It NEVER calls the Cache Storage API: everything
# is behind Cloudflare Access, and a cached/stale response could bypass or
# confuse the auth flow. Its only purpose is to be a registered SW with a fetch
# handler (Chromium installability), passing every request straight to the
# network. WebSocket upgrades (the ttyd terminal) are not `fetch` events, so the
# live terminal stream is untouched.
_SERVICE_WORKER = """\
// airuleset webterm PWA service worker (#644) — NETWORK-ONLY pass-through.
// Everything sits behind Cloudflare Access; a stored or replayed response could
// bypass or confuse the auth flow, so this SW never stores anything (no Storage
// API at all). Its only job is to satisfy the installability criterion with a
// fetch handler that forwards every request straight to the network.
self.addEventListener('install', function (e) { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', function (event) {
  // Pure pass-through — every request goes straight to the network, unstored.
  event.respondWith(fetch(event.request));
});
"""


def render_service_worker():
    """The minimal network-only service worker (bytes)."""
    return _SERVICE_WORKER.encode("utf-8")


# --------------------------------------------------------------------------- #
# Icons — a pure-stdlib PNG of a `>_` terminal glyph (Campbell green on the
# Campbell-black background). No external asset, no PIL: a tiny zlib/struct PNG
# encoder + a parametric glyph, so there is no stdlib rasterizer dependency
# (why-none-fits) and nothing is fetched at runtime.
# --------------------------------------------------------------------------- #

def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))


def _png_bytes(width, height, pixels):
    """Encode an 8-bit RGBA raster (`pixels`, `width*height*4` bytes, row-major)
    to PNG bytes — stdlib only (`zlib`/`struct`)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)   # 8-bit RGBA
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)                              # filter type 0 (None) per scanline
        raw += pixels[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    return (sig + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b""))


def _hex_rgba(h):
    """`#RRGGBB` -> opaque `(r, g, b, 255)` — so the icon colours DERIVE from
    CAMPBELL_THEME (single source of truth) instead of drifting literals."""
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def _dist_to_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    if length2 == 0.0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / length2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def render_icon_png(size, maskable=False):
    """A `size`x`size` PNG of a Campbell-green `>_` prompt on Campbell black.
    `maskable=True` insets the glyph further (a larger safe-zone margin) for the
    circular crop platforms apply to maskable icons."""
    s = int(size)
    # Derived from the palette so an edit to CAMPBELL_THEME propagates here too.
    bg = bytes(_hex_rgba(w.CAMPBELL_THEME["background"]))      # Campbell black
    fg = _hex_rgba(w.CAMPBELL_THEME["brightGreen"])            # vivid green glyph
    px = bytearray(bg * (s * s))
    pad = 0.28 if maskable else 0.20               # extra margin for maskable crop
    span = (1.0 - 2.0 * pad) * s

    def _x(u):
        return (pad + u * (1.0 - 2.0 * pad)) * s

    def _y(v):
        return (pad + v * (1.0 - 2.0 * pad)) * s

    hw = 0.075 * span                              # stroke half-width
    # ">" chevron (unit coords) + "_" underscore to its lower right.
    ax, ay = _x(0.10), _y(0.15)                    # top of chevron
    bx, by = _x(0.55), _y(0.45)                    # apex (points right)
    cx, cy = _x(0.10), _y(0.75)                    # bottom of chevron
    ux0, ux1, uy = _x(0.55), _x(0.98), _y(0.86)    # underscore span + baseline
    fg_bytes = bytes(fg)
    x_lo = max(0, int(min(ax, cx, ux0) - hw - 1))
    x_hi = min(s, int(ux1 + hw + 2))
    y_lo = max(0, int(ay - hw - 1))
    y_hi = min(s, int(uy + hw + 2))
    for y in range(y_lo, y_hi):
        fy = y + 0.5
        row = y * s
        for x in range(x_lo, x_hi):
            fx = x + 0.5
            on = (_dist_to_segment(fx, fy, ax, ay, bx, by) <= hw
                  or _dist_to_segment(fx, fy, bx, by, cx, cy) <= hw
                  or (ux0 <= fx <= ux1 and abs(fy - uy) <= hw))
            if on:
                i = (row + x) * 4
                px[i:i + 4] = fg_bytes
    return _png_bytes(s, s, px)


def write_pwa_assets(dash_dir, profile):
    """Write the profile's PWA assets (manifest + network-only SW + generated
    icons) into `dash_dir` (next to index.html). Called by the owner and david
    provisioning paths AFTER the dashboard index is written. Idempotent (plain
    overwrites)."""
    from pathlib import Path
    d = Path(dash_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / MANIFEST_FILE).write_bytes(render_manifest(profile))
    (d / SW_FILE).write_bytes(render_service_worker())
    (d / ICON_192).write_bytes(render_icon_png(192))
    (d / ICON_512).write_bytes(render_icon_png(512))
    (d / ICON_MASKABLE_512).write_bytes(render_icon_png(512, maskable=True))
