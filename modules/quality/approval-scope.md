### Approval Scope — The Full Merge→Deploy Flow Is Standing-Approved; Destructive NON-Deploy Ops Stand Alone

**DEFAULT: every project's full flow — merge → deploy → verify — runs end-to-end WITHOUT asking, including PRODUCTION deploys (`pr-merge-policy.md`). The deploy INCLUDES the brief restart of the app/service being deployed, to load the new version — even on prod, even a manual `scp`/`rsync`/MCP deploy. Milestone-ping it (`milestone-notifications.md`); do NOT gate it on approval.** The user guards production-event timing themselves and will say "hold" in the moment if a live event is on; a project whose deploy interrupts a live/event-sensitive service uses the per-project marker below.

#### Automatic by default (no approval) — the WHOLE flow

- Merging a fully green dev→main PR (`pr-merge-policy.md`).
- Deploy pipelines triggered by the merge — monitor to terminal, then verify (`post-deploy-verification.md`).
- Manual deploy steps the pipeline doesn't perform (deploy-ssh / `scp` / `rsync` / MCP) AFTER the merge gates pass — under `deploy-from-clean-tree.md` (clean committed tree, diff-verify) + full post-deploy verification. "No CI/deploy pipeline" does NOT mean "needs approval" — a manual deploy is still just a deploy, and it is approved.
- **The restart of the app/service being deployed, to load the new version — including prod** (e.g. redeploying a production binary/config and restarting that app). This IS the deploy, NOT a gated "service restart".

#### Per-project restriction (the user's opt-out)

- `<!-- airuleset:merge=manual -->` in a project's `CLAUDE.md` restores the manual gate for merge AND deploy — use it for a project whose deploy interrupts a live / event-sensitive production service. Only the USER adds or removes it.

#### Still requires its OWN approval, EVERY time — destructive NON-deploy ops

- Rebooting the HOST machine.
- Stopping / killing a service or process **outside a deploy** (not the deploy's own restart-to-load-the-new-version).
- `rm -rf` / deleting data; DB `DROP` / `DELETE` / `TRUNCATE` (`database-migrations.md`).
- Rollbacks that overwrite newer production state with older bytes.
- Anything in a foreign / third-party repo or outside the two-branch flow.

#### One approval ≈ one action (for the gated set)

For the gated destructive set, approval for one is NOT approval for the chain: approving a reboot of machine A doesn't approve rebooting machine B; approving one `rm` doesn't approve the next. When genuinely in doubt: **a deploy of the new version (incl. its restart, incl. prod, incl. manual) is approved** — a host reboot, data deletion, or an out-of-deploy service stop is gated, so ask only for those.
