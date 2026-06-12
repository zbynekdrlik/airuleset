### Approval Scope — Deploy Follows Merge; Destructive Actions Stand Alone

**The green-gates flow runs end-to-end without approvals: merge → pipeline deploy → post-deploy verification (`pr-merge-policy.md`). Explicit user approval is reserved for anything DESTRUCTIVE or outside that flow.**

#### Automatic by default (no approval)

- Merging a fully green dev→main PR (`pr-merge-policy.md`; the `<!-- airuleset:merge=manual -->` marker restores the manual gate for merge AND deploy)
- Deploy pipelines triggered by that merge — monitor to terminal, then verify (`post-deploy-verification.md`)
- Manual deploy steps the pipeline doesn't perform (deploy-ssh / rsync) AFTER the merge gates pass — still under `deploy-from-clean-tree.md` (clean committed tree, diff-verify) and full post-deploy verification

#### Still requires its OWN approval, EVERY time

- Destructive remote actions: reboot/restart, stop/kill services or processes, `rm -rf`, DB `DROP`/`DELETE`/`TRUNCATE` (`no-destructive-remote-actions.md`)
- Rollbacks that overwrite newer production state with older bytes
- Anything in a foreign/third-party repo or outside the two-branch flow
- Schema-destructive migrations on production data (`database-migrations.md`)

#### One approval ≈ one action (for the gated set)

For gated actions, approval for one is NOT approval for the chain: approving a reboot of machine A doesn't approve rebooting machine B; approving one `rm` doesn't approve the next. When in doubt whether something is in the automatic flow or the gated set — it's gated; ask.
