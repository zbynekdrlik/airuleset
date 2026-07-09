### URL Hygiene — No Dead Links, No Localhost

**Never provide `localhost` or `127.0.0.1` URLs to the user.** The user works remotely — use the machine's actual IP address (`hostname -I | awk '{print $1}'`).

**Never present a URL to the user without verifying it is LIVE first.** Before showing any URL (preview, dashboard, visual companion, dev server):

1. `curl -s -o /dev/null -w "%{http_code}" <url>` — must return 200 (or appropriate success code)
2. If the server is dead/timed out — **restart it BEFORE presenting the URL**, not after the user reports it broken
3. If you started a preview server earlier in the session, CHECK if it's still running before referencing its URL again

**Anti-patterns:**
- Presenting a URL from 30 minutes ago without checking if the server is still alive → **WRONG**
- "Take a look at http://..." when the server already timed out → **WRONG**
- User reports URL broken → "let me restart it" → **WRONG — you should have checked first**

This applies to ALL URLs: visual companion, dev servers, dashboards, preview mockups, and deploy targets.

#### Enforcement — the Stop hook checks 🌐 lines only

`stop-check-prose-violations.sh` HARD-blocks a `localhost`/`127.0.0.1`/`0.0.0.0` URL on a `🌐`-marked line (the completion-report's "user-clickable URL" marker, per `completion-report.md`) — never the whole message, so a code block or a prose sentence discussing "the dev server runs on localhost" is untouched. This is deliberately narrow: outside a 🌐 line there is no reliable way to tell "a URL being presented to the user" from "a URL mentioned in passing", so it stays discipline-only there.
