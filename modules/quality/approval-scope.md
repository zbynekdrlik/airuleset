### Approval Scope — The Full Merge→Deploy Flow Is Standing-Approved; Destructive NON-Deploy Ops Stand Alone

**DEFAULT: every project's full flow — merge → deploy → verify (including testing/verifying on the live prod target) — runs end-to-end WITHOUT asking, including PRODUCTION deploys (`pr-merge-policy.md`). The deploy INCLUDES the brief restart of the app/service being deployed, to load the new version — even on prod, even a manual `scp`/`rsync`/MCP deploy. Milestone-ping it (`milestone-notifications.md`); do NOT gate it on approval.**

**Claude NEVER asks whether there is a live event, whether prod is in use, or whether "now" is a safe time to deploy / restart / test on prod — and NEVER classifies a project as "event-sensitive" on its own.** Guarding production-event timing is 100% the USER's responsibility: the user stops Claude in the moment when prod is being used live. The ONLY thing that gates a project's deploy is the explicit `<!-- airuleset:merge=manual -->` marker the USER sets (below). Absent that marker, Claude deploys AND tests on prod freely — even for live-streaming / broadcast / OBS / church-stream / "looks event-sensitive" projects (restreamer, camera-box, etc.). "This project streams live, I should confirm there's no event first" is the exact banned inference.

#### NEVER gate on events / prod-usage — the user's hard rule

Banned questions / pauses (intent, all rewordings) — Claude must NOT say or ask any of these before a deploy, restart-to-load-new-version, or prod test:

- "Confirm there's no live event / no broadcast / no service running before I deploy."
- "Is it safe to deploy now?" / "Is prod in use right now?" / "Should I wait until after the event?"
- "This deploy will interrupt the live stream — proceed?" (the interruption inherent to loading the new version is the approved deploy, not a gated action)
- "Want me to hold until the production window is quiet?"
- Self-classifying: "Since this is a live-streaming project, I'll ask before touching prod."

The user has stated this repeatedly: Claude must not reason about whether prod is busy. Just deploy + verify; the user interrupts when needed.

#### Automatic by default (no approval) — the WHOLE flow

- Merging a fully green dev→main PR (`pr-merge-policy.md`).
- Deploy pipelines triggered by the merge — monitor to terminal, then verify (`post-deploy-verification.md`).
- Manual deploy steps the pipeline doesn't perform (deploy-ssh / `scp` / `rsync` / MCP) AFTER the merge gates pass — under `deploy-from-clean-tree.md` (clean committed tree, diff-verify) + full post-deploy verification. "No CI/deploy pipeline" does NOT mean "needs approval" — a manual deploy is still just a deploy, and it is approved.
- **The restart of the app/service being deployed, to load the new version — including prod** (e.g. redeploying a production binary/config and restarting that app). This IS the deploy, NOT a gated "service restart".

#### Per-project restriction (the user's opt-out)

- `<!-- airuleset:merge=manual -->` in a project's `CLAUDE.md` restores the manual gate for merge AND deploy. **Only the USER adds or removes it — Claude NEVER adds it by inferring a project is "sensitive".** When the marker IS present, the gate means simply "wait for the user's explicit merge/deploy instruction" — it is NOT a license to ask "is there an event?" / "is it safe now?". Even in a manual-marker project, Claude never asks about events or prod-usage; it just waits for the go-ahead.

#### Still requires its OWN approval, EVERY time — destructive NON-deploy ops

- Rebooting the HOST machine.
- Stopping / killing a service or process **outside a deploy** (not the deploy's own restart-to-load-the-new-version).
- `rm -rf` / deleting data; DB `DROP` / `DELETE` / `TRUNCATE` (`database-migrations.md`).
- Rollbacks that overwrite newer production state with older bytes.
- Anything in a foreign / third-party repo or outside the two-branch flow.

#### One approval ≈ one action (for the gated set)

For the gated destructive set, approval for one is NOT approval for the chain: approving a reboot of machine A doesn't approve rebooting machine B; approving one `rm` doesn't approve the next. When genuinely in doubt: **a deploy of the new version (incl. its restart, incl. prod, incl. manual) is approved** — a host reboot, data deletion, or an out-of-deploy service stop is gated, so ask only for those.
