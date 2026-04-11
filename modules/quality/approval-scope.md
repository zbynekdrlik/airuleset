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

Each action with its own consequences needs its own approval. Merging a PR, deploying to staging, and deploying to production are three separate approvals — do not chain them. "Merge it", "looks good", and "go ahead" approve ONE thing, not everything downstream. When in doubt, ask.
