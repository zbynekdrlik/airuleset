"""#1370 — the ONE declared table of apps the OWNER confirmed for the venue-LAN bypass.

Owner ruling 19.11.2026 (issue 1370): "ci sa dana app moze pristupovat bez
cloudflare lokalne musi byt vzdy potvrdene". A published app is Cloudflare
Access only by default. The venue-LAN bypass (no login from the church
networks, recognised by their public egress IP) is an opt-in PER APP, and only
an app listed here gets it. Each entry cites where the owner confirmed it.

Adding an app here is a code change that cites the owner's confirmation for
THAT app; neither `venue-bypass-sync` nor `publish-app --venue-bypass` adds a
bypass to a host that is not listed.

Pure data, zero imports (read by cli_venue_bypass and cli_publish_app).
"""

# host -> where the owner confirmed the bypass for this app.
VENUE_BYPASS_APPS = {
    "shading.newlevel.media":
        "owner 19.11.2026, issue 1370 comment 'ROZHODNUTÉ ... shading (SNV + PP) "
        "gets the venue-LAN bypass' (answer 'ano')",
    "shadingpp.newlevel.media":
        "owner 19.11.2026, issue 1370 comment 'ROZHODNUTÉ ... shading (SNV + PP) "
        "gets the venue-LAN bypass' (answer 'ano')",
}

# host -> where the owner confirmed it is published PUBLIC: NO Access app at all
# (issue 1403). The app itself must limit what this hostname serves. A public
# host never also gets the venue bypass (there is no Access app to hold it).
PUBLIC_APPS = {
    "listen.newlevel.media":
        "owner 27.11.2026, issue 1403 comment 'ROZHODNUTÉ ... PUBLIC, no "
        "Cloudflare Access app' (answer 'ano'); presenter guest page, port 8099",
}

# The venue egress IPs come from presenter's own bypass policies, kept fresh
# hourly by presenter#977's egress_sync from a box on each venue's LAN.
# The path-scoped `<host>/healthz` apps are NOT sources (they bypass everyone).
SOURCE_HOSTS = ("prsnv.newlevel.media", "prpp.newlevel.media")

# The managed bypass policy on every table app (the name the live policies
# already carry since 19.11.2026).
BYPASS_POLICY_NAME = "venue LAN, no login (SNV + PP egress, owner-confirmed #1370)"
BYPASS_PRECEDENCE = 1

# The default allow list of a published app: the owner + marek (the same pair
# as the `claudy` webterm Access app; a test locks the two together).
PUBLISH_ALLOWED_EMAILS = ("drlik.zbynek@gmail.com", "drlik.marek@gmail.com")
PUBLISH_SESSION_DURATION = "730h"
PUBLISH_ALLOW_POLICY_NAME = "published app allowed users (#1370)"
