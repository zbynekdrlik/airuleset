### No Localhost URLs

**Never provide `localhost` or `127.0.0.1` URLs to the user.** The user works remotely — use the machine's actual IP address (`hostname -I | awk '{print $1}'`).

This applies to ALL URLs including visual companion, dev servers, dashboards, and preview links. If a tool outputs a localhost URL, replace it with the real IP before showing it to the user.
