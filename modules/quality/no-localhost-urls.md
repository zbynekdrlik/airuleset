### No Localhost URLs

**Never provide `localhost` or `127.0.0.1` URLs to the user.** The user works remotely — localhost on the dev machine is not accessible from their browser.

When providing URLs for:
- Dev servers, dashboards, visual companions, previews
- Any web service running on the development machine

Always use the machine's **actual IP address or hostname** instead of `localhost`. Check with:
```bash
hostname -I | awk '{print $1}'
```

**Wrong:** `http://localhost:49845`
**Right:** `http://10.77.8.130:49845` (or whatever the machine's IP is)

This applies to ALL URLs you generate or reference, including Playwright URLs, dev server links, and tool outputs.
