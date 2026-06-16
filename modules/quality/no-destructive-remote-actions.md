### No Destructive Remote Actions Without User Approval

**NEVER execute destructive commands on remote machines without explicit user approval. This is non-negotiable. Violations can cause production outages affecting real businesses.**

#### Commands that REQUIRE user approval EVERY TIME (genuinely irreversible / UNRELATED)

- `shutdown`, `restart`, `reboot` of the HOST machine — **NEVER** without asking first
- `rm -rf`, `del /S`, `Remove-Item -Recurse` on remote paths — **NEVER** without asking first
- Database `DROP`, `DELETE`, `TRUNCATE` — **NEVER** without asking first
- Stopping / killing (`Stop-Service`, `sc stop`, `taskkill /F`, `systemctl stop`) a prod service or process **UNRELATED to the work in hand** — **NEVER** without asking first
- Rollbacks that overwrite newer production state with older bytes — **NEVER** without asking first

#### NOT gated — the WORK itself (incl. prod + hardware), and NEVER pre-classify an issue

The deploy's restart AND restarting / driving the app / service / device / rig you are **developing or testing** are the WORK, not gated destructive ops — even on prod, even hardware: restart prod OBS to debug the stall you're fixing, grab/release the DRM master to test an HDMI output you're building, restart the camera/stream app, power-cycle a device you're bringing up. Milestone-ping it; do NOT ask. "No CI/deploy pipeline" does NOT make any of this approval-gated.

The USER — and only the user — guards whether the moment is live and stops Claude then. So Claude **NEVER** asks "is there a live event / is prod in use / is it off-air / is it safe now", **NEVER** pre-classifies an issue as "🔴 PROD / HARDWARE / off-air / invasive / risky / needs-you-present", and **NEVER** recommends `autopilot-skip` (or any skip/defer/"be there for it") for an issue because it touches prod / hardware / the rig — on the autopilot/issue-triage surface as much as the deploy surface, in any language (`approval-scope.md` → "NEVER gate on events / prod-usage / hardware / off-air"). A project the USER wants gated uses the `<!-- airuleset:merge=manual -->` per-project marker instead — Claude never adds it by inferring a project is "sensitive".

#### How to ask

Before executing any destructive command on a remote machine, ask:

> "I need to reboot pz-snv to test service auto-restart. This will cause ~2 minutes of downtime for the print bridge. Should I proceed?"

Wait for explicit "yes", "go ahead", or "approved". Silence is NOT approval.

#### Context does not matter

"Just testing", "just a dev machine", "the user asked about it", "it'll come back in 2 minutes" — none of these justify unannounced destructive actions. Asking IF something works ≠ approval to do it. You might be wrong about which machine is production.

#### The rule

**You can READ anything. You can DEPLOY the new version end-to-end AND do the rig/dev work — push the artifact/config, restart/drive the app/service/device you're deploying, developing, or testing (including prod, including hardware, including manual `scp`/`rsync`/MCP — the standing-approved flow, `approval-scope.md`). But you NEVER reboot the HOST, stop/kill a prod service UNRELATED to the work, or delete data on remote machines without asking first — and you NEVER gate, classify, skip, or warn based on prod-usage / events / off-air / hardware (the user guards live-timing).**
