# Published apps — tunnel + Cloudflare Access, venue-LAN bypass only by owner confirmation (#1370)

Owner rulings 18.-19.11.2026 (airuleset issue 1370): "všade chcem mať rovnaký režim na našich appkách" and "ci sa dana app moze pristupovat bez cloudflare lokalne musi byt vzdy potvrdene".

**The standard for EVERY app we publish:**

- **A published app = its box's Cloudflare tunnel + a Cloudflare Access app.** From the internet it always opens behind the Cloudflare e-mail login (owner + marek, 730h). One hostname works everywhere.
- **Never a router change.** No port forward, no DNS rewrite, no static LAN record. A public DNS record pointing at a private IP (`10.x`) is wrong: the app then works only on that LAN.
- **The venue-LAN bypass (no login on the church networks SNV / PP) is an opt-in PER APP, and only the OWNER confirms it, for THAT app.** Default = Access only. An app gets the bypass only when it is listed in `VENUE_BYPASS_APPS` (`cli_venue_bypass_apps.py` in airuleset) with the owner's confirmation cited. Never infer it ("it's an internal tool", "the other app has it").
- **The venue IPs are never typed by hand.** presenter's egress sync (presenter issue 977) keeps them fresh hourly; airuleset's controller timer `venue-bypass-sync` copies them into every listed app and fails loud when the source is missing.

**The ONE tool (airuleset, controller only):**

```
python3 airuleset.py publish-app <host>.newlevel.media --tunnel <tunnel-uuid> \
    --origin https://127.0.0.1:443 [--origin-server-name <host>] [--venue-bypass] [--apply]
```

Order: Access app FIRST, then the tunnel ingress (remotely managed tunnel, inserted before the catch-all), then the proxied CNAME. Dry run by default. `--venue-bypass` is refused for a host not in the table. `venue-bypass-sync [--apply]` re-checks every listed app.

**A project session never builds this itself and never asks the owner how.** It asks airuleset: `gh issue create -R zbynekdrlik/airuleset` (or a peer message to airuleset) naming the host, the box's tunnel and the origin. If the app should open without login on the church LAN, airuleset asks the OWNER to confirm that one app (a `❓` question), records the answer in the table, then publishes.
