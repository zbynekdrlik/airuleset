### No Localhost URLs

**Never provide `localhost` or `127.0.0.1` URLs to the user.** The user works remotely — use the machine's actual IP address (`hostname -I | awk '{print $1}'`).
