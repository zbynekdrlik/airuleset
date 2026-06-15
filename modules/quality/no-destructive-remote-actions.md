### No Destructive Remote Actions Without User Approval

**NEVER execute destructive commands on remote machines without explicit user approval. This is non-negotiable. Violations can cause production outages affecting real businesses.**

#### Commands that REQUIRE user approval EVERY TIME

- `shutdown`, `restart`, `reboot` — **NEVER** without asking first
- `Stop-Service`, `sc stop` on production services — **NEVER** without asking first
- `taskkill /F` on production processes — **NEVER** without asking first
- `rm -rf`, `del /S`, `Remove-Item -Recurse` on remote paths — **NEVER** without asking first
- Database `DROP`, `DELETE`, `TRUNCATE` — **NEVER** without asking first
- `systemctl stop/restart` on production services — **NEVER** without asking first (EXCEPT the deploy's own restart — see carve-out)
- Any command that causes downtime, data loss, or service interruption — EXCEPT the brief restart inherent to deploying the new version (carve-out below)

#### Carve-out — a DEPLOY's own restart is NOT gated

Restarting the app/service that is BEING DEPLOYED, to load the new version, is the **approved deploy flow** (`approval-scope.md`) — even on prod, even via manual `scp` / `rsync` / MCP. Milestone-ping it; do NOT ask. "No CI/deploy pipeline" does NOT make a deploy approval-gated. This rule gates only the destructive ops that are **NOT part of deploying the new version**: rebooting the HOST machine, stopping / restarting / killing a service or process **outside a deploy**, deleting data (`rm -rf`), DB `DROP`/`DELETE`/`TRUNCATE`. A project the USER wants gated uses the `<!-- airuleset:merge=manual -->` per-project marker instead of a per-deploy prompt — and Claude NEVER asks "is there a live event / is prod in use / is it safe now" before a deploy, a restart-to-load-the-new-version, or a prod TEST/verification. Guarding prod-event timing is the user's job; they stop Claude in the moment. See `approval-scope.md` → "NEVER gate on events / prod-usage".

#### How to ask

Before executing any destructive command on a remote machine, ask:

> "I need to reboot pz-snv to test service auto-restart. This will cause ~2 minutes of downtime for the print bridge. Should I proceed?"

Wait for explicit "yes", "go ahead", or "approved". Silence is NOT approval.

#### Context does not matter

"Just testing", "just a dev machine", "the user asked about it", "it'll come back in 2 minutes" — none of these justify unannounced destructive actions. Asking IF something works ≠ approval to do it. You might be wrong about which machine is production.

#### The rule

**You can READ anything. You can DEPLOY the new version end-to-end — push the artifact/config AND restart the deployed app to load it, including prod, including manual `scp`/`rsync`/MCP (the standing-approved flow, `approval-scope.md`). But you NEVER reboot the HOST, stop/kill a service or process OUTSIDE a deploy, or delete data on remote machines without asking first.**
