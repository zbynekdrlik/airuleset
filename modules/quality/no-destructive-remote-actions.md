### No Destructive Remote Actions Without User Approval

**NEVER execute destructive commands on remote machines without explicit user approval. This is non-negotiable. Violations can cause production outages affecting real businesses.**

#### Commands that REQUIRE user approval EVERY TIME

- `shutdown`, `restart`, `reboot` — **NEVER** without asking first
- `Stop-Service`, `sc stop` on production services — **NEVER** without asking first
- `taskkill /F` on production processes — **NEVER** without asking first
- `rm -rf`, `del /S`, `Remove-Item -Recurse` on remote paths — **NEVER** without asking first
- Database `DROP`, `DELETE`, `TRUNCATE` — **NEVER** without asking first
- `systemctl stop/restart` on production services — **NEVER** without asking first
- Any command that causes downtime, data loss, or service interruption

#### How to ask

Before executing any destructive command on a remote machine, ask:

> "I need to reboot pz-snv to test service auto-restart. This will cause ~2 minutes of downtime for the print bridge. Should I proceed?"

Wait for explicit "yes", "go ahead", or "approved". Silence is NOT approval.

#### Context does not matter

"Just testing", "just a dev machine", "the user asked about it", "it'll come back in 2 minutes" — none of these justify unannounced destructive actions. Asking IF something works ≠ approval to do it. You might be wrong about which machine is production.

#### The rule

**You can READ anything. You can DEPLOY pre-approved artifacts. But you NEVER reboot, stop services, kill processes, or delete data on remote machines without asking first.**
