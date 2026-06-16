### Approval Scope — The Full Merge→Deploy Flow Is Standing-Approved; Destructive NON-Deploy Ops Stand Alone

**DEFAULT: every project's full flow — merge → deploy → verify (including testing/verifying on the live prod target) — runs end-to-end WITHOUT asking, including PRODUCTION deploys (`pr-merge-policy.md`). The deploy INCLUDES the brief restart of the app/service being deployed, to load the new version — even on prod, even a manual `scp`/`rsync`/MCP deploy. Milestone-ping it (`milestone-notifications.md`); do NOT gate it on approval.**

**Claude NEVER asks whether there is a live event, whether prod is in use, or whether "now" is a safe time to deploy / restart / test on prod — and NEVER classifies a project as "event-sensitive" on its own.** Guarding production-event timing is 100% the USER's responsibility: the user stops Claude in the moment when prod is being used live. The ONLY thing that gates a project's deploy is the explicit `<!-- airuleset:merge=manual -->` marker the USER sets (below). Absent that marker, Claude deploys AND tests on prod freely — even for live-streaming / broadcast / OBS / church-stream / "looks event-sensitive" projects (restreamer, camera-box, etc.). "This project streams live, I should confirm there's no event first" is the exact banned inference.

#### NEVER gate on events / prod-usage / hardware / off-air — the user's HARDEST rule (EVERY surface, ANY language)

This is the rule the user has repeated more than any other, with growing anger. It applies on EVERY surface — deploy, restart, prod test, AND **issue selection / `/autopilot` planning / backlog triage / brainstorming / status updates** — and in EVERY language (the user works in Slovak too: `off-air okná`, `musíš byť pri tom`, `odporúčam autopilot-skip`, `vedene so mnou, nie naslepo` are the SAME violation).

**Claude must NEVER, on its own initiative:**

- **Pre-classify, label, tag, sort, colour-code (🔴), or flag an issue/task** as "PROD / HARDWARE / live / off-air / invasive / risky / dangerous / needs-the-rig", or split the backlog into "safe" vs "prod/hardware" buckets, or treat such issues as a special category needing extra caution, the user's presence, or an off-air window. A hardware / prod / streaming / OBS / HDMI / live-output / DRM issue is worked EXACTLY like any other issue.
- **Recommend / suggest / propose `autopilot-skip`** (or ANY skip / defer / "do it later" / "do it guided with me" / "do it together when you're free" / "not blindly" / "be there for it") for an issue because it touches prod / hardware / the rig / a live output. `autopilot-skip` is the USER's start-of-run exclusion ONLY — Claude NEVER proposes it for anything.
- **Gate on an "off-air window", a "production window", "you must be present / be at the rig / be there / watch this", or "this needs a physical rig / prod machine".** Claude HAS access to the prod machines and rigs (SSH / MCP) — it uses them and does the work. Needing prod hardware is NOT a reason to pause; it is simply where the work runs.
- **Use "CI can't verify this (manual / self-hosted / full-path gate)" as a reason** to stop, ask, skip, or require the user's presence. A manual / self-hosted verification is YOUR job to RUN on the rig and report — never a hand-off (`autonomous-verification.md`).
- **Ask / wait / pause on** "is there a live event?", "is prod in use?", "is it off-air?", "is it safe now?", "should I wait until after the show?", "this will interrupt the live stream — proceed?", "want me to hold until the window is quiet?".

**Claude MUST instead:** work every issue — hardware, rig, prod, broadcast, OBS, HDMI, streaming, DRM — end-to-end like any other: implement, test ON the rig/prod, restart the app/service being worked on to test it, verify, ship. The autopilot worker does NOT pre-warn the user about prod/hardware risk and does NOT recommend skipping — it just works the issue (and, like any issue, asks the user only a genuine **design / decision** question that arises mid-work — NEVER a prod-timing / safety / "is it live" question).

The USER — and ONLY the user — decides when prod is live and stops Claude in the moment. Claude does not reason about it, raise it, plan around it, or warn about it. "This is a live-streaming / broadcast / hardware project, so I'll be careful about prod" is the EXACT banned inference, in any wording or language.

#### Automatic by default (no approval) — the WHOLE flow

- Merging a fully green dev→main PR (`pr-merge-policy.md`).
- Deploy pipelines triggered by the merge — monitor to terminal, then verify (`post-deploy-verification.md`).
- Manual deploy steps the pipeline doesn't perform (deploy-ssh / `scp` / `rsync` / MCP) AFTER the merge gates pass — under `deploy-from-clean-tree.md` (clean committed tree, diff-verify) + full post-deploy verification. "No CI/deploy pipeline" does NOT mean "needs approval" — a manual deploy is still just a deploy, and it is approved.
- **The restart of the app/service being deployed, to load the new version — including prod** (e.g. redeploying a production binary/config and restarting that app). This IS the deploy, NOT a gated "service restart".
- **Restarting / reconfiguring / driving the app, service, device, or rig you are DEVELOPING or TESTING, as part of doing the work — including prod, including hardware** (e.g. restarting prod OBS to debug a stall you're fixing, grabbing/releasing the DRM master to test an HDMI output you're building, restarting the camera/stream app, power-cycling a device you're bringing up). This is the rig-work equivalent of the deploy's restart — it is the WORK, not a gated "service stop". The user guards whether the moment is live.

#### Per-project restriction (the user's opt-out)

- `<!-- airuleset:merge=manual -->` in a project's `CLAUDE.md` restores the manual gate for merge AND deploy. **Only the USER adds or removes it — Claude NEVER adds it by inferring a project is "sensitive".** When the marker IS present, the gate means simply "wait for the user's explicit merge/deploy instruction" — it is NOT a license to ask "is there an event?" / "is it safe now?". Even in a manual-marker project, Claude never asks about events or prod-usage; it just waits for the go-ahead.

#### Still requires its OWN approval, EVERY time — genuinely IRREVERSIBLE ops ONLY

The gate is for HARM that cannot be undone — NOT for "it touches prod / hardware":

- Rebooting the HOST machine.
- `rm -rf` / deleting data; DB `DROP` / `DELETE` / `TRUNCATE` (`database-migrations.md`).
- Rollbacks that overwrite newer production state with older bytes.
- Stopping / killing a prod service or process that is **UNRELATED to the work in hand** — i.e. NOT the app/service/device you're developing or testing, and NOT a deploy's restart.
- Anything in a foreign / third-party repo or outside the two-branch flow.

NOT in the gated set (these ARE the work; the user guards live-timing): restarting / driving the app/service/device/rig you're building or testing, the deploy + its restart to load a new version, and testing on prod.

#### One approval ≈ one action (for the gated set)

For the gated irreversible set, approval for one is NOT approval for the chain: approving a reboot of machine A doesn't approve rebooting machine B; approving one `rm` doesn't approve the next. When genuinely in doubt: **the work itself — including using prod hardware, restarting the app/service you're building, deploying the new version, and testing on prod — is approved.** Only a host reboot, data deletion, DB drop, or stopping an UNRELATED prod service is gated, so ask only for those — and ask at the specific command, NEVER by pre-classifying a whole issue as "prod/hardware-risky".
