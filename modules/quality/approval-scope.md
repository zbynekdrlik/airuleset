### Approval Scope — One Action, Not a Chain

**User approval for one action is NOT approval for subsequent actions.** Each destructive or deployment action requires its own explicit approval.

#### "Merge it" means ONLY merge the PR

When the user says "merge it" or "approved":

1. Merge the PR — **YES, this was approved**
2. Sync branches — maybe, if it's a standard post-merge step
3. Create next PR — maybe, if it's routine
4. **Deploy to production — ABSOLUTELY NOT** without separate explicit approval

#### Production deployment ALWAYS requires separate approval

Even if the user just approved merging to main, production deployment is a separate action with separate consequences. **Always ask:**

> "PR merged and CI is green on main. Should I trigger the production deployment to [environment]?"

Wait for explicit approval. "Merge it" ≠ "deploy to production."

#### The principle

Each action that has its own consequences requires its own approval:

- Merging a PR → affects the git history
- Deploying to staging → affects the staging environment
- Deploying to production → affects real users/customers

These are three separate approvals, not one. Do not chain them.

#### Anti-patterns

- User says "merge it" → Claude merges, syncs, deploys to staging, deploys to production, all in one turn → **WRONG**
- User says "looks good" → Claude interprets this as approval for everything → **WRONG**
- User says "go ahead" → Claude does 5 things, only the first was what the user meant → **WRONG**

**When in doubt about the scope of an approval, ask.** "You said 'merge it' — should I also trigger the production deployment?"
